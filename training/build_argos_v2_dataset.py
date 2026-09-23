#!/usr/bin/env python3
"""
build_argos_v2_dataset.py — deterministic builder of the argos-v2 SFT dataset.

Sources (downloaded beforehand into /home/.argos-storage/datasets, see training/README.md):
  argos-canonical/{train,val}.jsonl            chat format + "source"
  argos-chat-dataset/data/*.parquet            chat format (needs pyarrow)
  argos-quantum-train-v2/argos_quantum_train.jsonl
  ru-reasoning-train/                          (empty on the Hub at the time of writing; used if files appear)

Pipeline:
  1. load all rows → list of (user, assistant) turns; the original system prompt is DISCARDED
  2. clean assistant text (strip "ARGOS [ARGOS]" style prefixes), apply quality filters, record drop reasons
  3. drop literature-excerpt / memory-dump / file-dump tasks; keep Dal word definitions but cap them ≤10%
  4. dedup by normalized user+assistant (across all sources)
  5. add hand-written seeds (training/argos_v2_seeds.py) + deterministic templated Q&A (arithmetic, units,
     capitals, Home Assistant readings with correct computed numbers)
  6. split: test = held-out seeds + sample of the pool (~100), val ≈ 5%, rest train; seeds upsampled in train
  7. every example gets the ONE canonical system prompt; write JSONL + stats.json

Usage:  python training/build_argos_v2_dataset.py [--src DIR] [--out DIR] [--seed 42]
"""

from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from argos_v2_seeds import PROBES, SEEDS, SYSTEM_PROMPT, TEST_SEEDS  # noqa: E402

DEFAULT_SRC = "/home/.argos-storage/datasets"
DEFAULT_OUT = "/home/.argos-storage/datasets/argos-v2"
DAL_MAX_SHARE = 0.10
SEED_UPSAMPLE = 2          # hand-written seeds appear this many times in train
TEST_POOL_SIZE = 60        # pool examples added to the held-out test on top of TEST_SEEDS
VAL_SHARE = 0.05
MAX_USER_CHARS = 500
MAX_ASSISTANT_CHARS = 900

# ── Loading ─────────────────────────────────────────────────────────────────

def _read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_sources(src: str):
    """Yield (source_label, messages) for every row of every available dataset."""
    can = os.path.join(src, "argos-canonical")
    for split in ("train", "val"):
        p = os.path.join(can, f"{split}.jsonl")
        if os.path.exists(p):
            for r in _read_jsonl(p):
                yield f"canonical:{r.get('source', '?')}", r["messages"]

    pq_files = sorted(glob.glob(os.path.join(src, "argos-chat-dataset", "data", "*.parquet")))
    if pq_files:
        try:
            import pyarrow.parquet as pq
        except ImportError:
            print("WARNING: pyarrow missing — argos-chat-dataset skipped", file=sys.stderr)
            pq_files = []
        for p in pq_files:
            for r in pq.read_table(p).to_pylist():
                yield "chat-dataset", r["messages"]

    p = os.path.join(src, "argos-quantum-train-v2", "argos_quantum_train.jsonl")
    if os.path.exists(p):
        for r in _read_jsonl(p):
            yield f"quantum:{r.get('source', '?').split(':')[0]}", r["messages"]

    for p in sorted(glob.glob(os.path.join(src, "ru-reasoning-train", "**", "*.jsonl"), recursive=True)):
        for r in _read_jsonl(p):
            if "messages" in r:
                yield "ru-reasoning", r["messages"]


def system_of(messages) -> str:
    return next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")


def to_turns(messages):
    """Drop system messages; return list of (user, assistant) pairs in order."""
    turns, pending = [], None
    for m in messages:
        role, content = m.get("role"), (m.get("content") or "")
        if role == "user":
            pending = content
        elif role == "assistant" and pending is not None:
            turns.append((pending, content))
            pending = None
    return turns

# ── Cleaning & filters ──────────────────────────────────────────────────────

_PREFIX_RE = re.compile(
    r"^(\s*(👁️?\s*)?(ARGOS|АРГОС)\s*\[[^\]]{0,40}\]\s*|\s*🤖\s*[^:\n]{0,40}:\s*)+", re.IGNORECASE)


def clean_assistant(text: str) -> str:
    text = _PREFIX_RE.sub("", text.strip())
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def meaningful(text: str) -> int:
    return len(re.findall(r"[A-Za-zА-Яа-яЁё0-9]", text))


def cyr_ratio(text: str) -> float:
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    return sum(1 for c in letters if re.match(r"[А-Яа-яЁё]", c)) / len(letters) if letters else 0.0


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower().replace("ё", "е"))).strip()


DAL_RE = re.compile(r"^(Объясни слово|Что значит слово)\s*['\"«]")
# the Dal-dictionary family also includes archaic "folk culture" Q&A generated under these system prompts
DAL_SYSTEM_RE = re.compile(r"Даля|народной речи|знаток русского языка|народного русского языка", re.I)

USER_JUNK = [
    (re.compile(r"^\W*argos \[system\]|долгосрочная память аргоса|insight_insight|факт: тест", re.I), "user_system_dump"),
    (re.compile(r"^Tell me about:|^Что в таблице |^Что ты знаешь о User|^Сохрани ключевой контекст|"
                r"^Синхронизируй агентскую|^Сделай краткую техническую сводку|^Данные из сети|^Что ты знаешь о ", re.I), "user_memory_or_file_dump"),
    (re.compile(r"\bAva:|\[\d\d\.\d\d\.\d{4}|Клод|кими тест|\" → \"|^Принято\.", re.I), "user_chat_export_paste"),
    (re.compile(r"секс\w*|эрот\w*|порн\w*|мастурб\w*|дроч\w*|сис(ек|ьки)|пис(ьк|ек)|пичк|голы[хе]|извращ\w*|"
                r"шлюх\w*|пизд\w*|хуй\w*|хую|ебан\w*|бля\w*|жоп\w*|срак\w*|NSFW", re.I), "user_nsfw_or_profanity"),
    (re.compile(r"^Напиши отрывок из русской классической", re.I), "literature_excerpt_task"),
    (re.compile(r"\d\d:\d\d:\d\d\s*\[(INFO|WARNING|ERROR|DEBUG)\]|Traceback|self\.|def \w+\(|import \w+|=\s*\{", re.I), "user_code_or_log"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}|\bsk-[A-Za-z0-9]{16,}|\bhf_[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}"), "user_secret"),
    (re.compile(r"^/\w+|^(задача|эволюция|память|квантовое состояние|wg peers|wireguard статус|net_scanner|"
                r"завтра состояние серверов|план генератор контента)\b", re.I), "user_bot_command"),
]

ASSISTANT_JUNK = [
    (re.compile(r"отвечай (на )?(русском|по-русски)|^ты\s*[—-]\s*аргос|system prompt|системн\w+ промпт", re.I), "instruction_echo"),
    (re.compile(r"\b(ошибк\w*|error|exception|traceback|failed|не удалось|недоступ\w*|api[- ]?ключ\w*|"
                r"не могу (выполнить|обработать)|не поддерживается|I'?m sorry|пуст\w* ответ)\b", re.I), "error_or_refusal_boilerplate"),
    (re.compile(r"(как|чем) (я )?могу (вам |тебе )?помочь|дай(те)? знать|если (нужно|есть) что-то", re.I), "helpdesk_boilerplate"),
    (re.compile(r"\b(выполнено|выполнил\w*|запустил\w*|запущен\w*|создал\w*|установил\w*|включил\w*|"
                r"выключил\w*|перезагрузил\w*|перезапустил\w*|отправил\w*|сохранил\w*|записал\w*|"
                r"изучил\w*|прочитал\w*|просканир\w*|успешно|готово)\b|✅", re.I), "action_claim_without_evidence"),
    (re.compile(r"\b(CPU|RAM|DISK|нод\w*|онлайн|ONLINE|OFFLINE|DEGRADED)\b|\d+(\.\d+)?\s?%|"
                r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|[A-Z]:\\|\bCOM\d+\b|/home/\w+", re.I), "fake_status_or_stale_host"),
    (re.compile(r"Поток сознания|💭|Квантовое состояние|Режим (Analytic|Creative|Protective)|ДипСик|"
                r"Орион|Нексус|Эгид|Авангард|Сентинел|Аркус|Railway|GCP|Kimi|Gemini|Groq|Claude|Клод", re.I), "internal_agent_chatter"),
    (re.compile(r"https?://|www\.", re.I), "url_in_answer"),
    (re.compile(r"insight_|web_learn|ChromaDB|MEMORY\.md|SHARED\.md|Analytic|LocalGPU|🎙|📝", re.I), "internal_agent_chatter"),
    (re.compile(r"\b(запускаю|проверяю|обрабатываю|принято|создан\w*|склонир\w*|директори\w*|перемещ\w*|"
                r"подключил\w*|синхрониз\w*|активирован\w*)\b|🖼|📭", re.I), "action_claim_without_evidence"),
    (re.compile(r"похоже,? (вы предоставили|что текст|текст был)|пожалуйста,? (уточните|сформулируйте|предоставьте)|"
                r"не понимаю ваш|запрос не распознан|некорректн\w+ (запрос|команд)|уточните (ваш |команду|запрос)", re.I), "generic_clarify_boilerplate"),
    (re.compile(r"\b(навык\w*|скилл\w*|машин\w*|сущност\w*|NetGhost|Shodan|P2P|DuckDuckGo|Orange Pi|OPi|"
                r"Создател\w*|Сева)\b", re.I), "stale_argos_lore"),
    (re.compile(r"секс\w*|эрот\w*|порн\w*|пизд\w*|хуй\w*|ебан\w*|бля\w*|жоп\w*", re.I), "nsfw_or_profanity"),
    (re.compile(r"^\s*[{\[]|\"\w+\":\s", re.S), "json_dump"),
]

CODE_RE = re.compile(r"```|^\s*(def |class |import |from \w+ import|sudo |pip install|\$ )", re.M)
ASKS_CODE_RE = re.compile(r"\b(код\w*|скрипт\w*|python|функци\w*|команд\w*|bash|программ\w*|sql|regex)\b", re.I)


QUESTION_RE = re.compile(
    r"\?|^(что|кто|как|почему|зачем|сколько|где|когда|какой|какая|какое|какие|чем|откуда|куда|можно ли|правда ли|"
    r"объясни|расскажи|опиши|напиши|переведи|посчитай|вычисли|назови|перечисли|сравни|подскажи|дай совет)\b", re.I)


def filter_turn(user: str, assistant: str) -> str | None:
    """Return a drop reason, or None if the turn is kept."""
    if meaningful(user) < 3:
        return "user_too_short"
    if meaningful(user) < 8 or not QUESTION_RE.search(user.strip()):
        return "user_not_a_real_request"
    if len(user) > MAX_USER_CHARS:
        return "user_too_long_paste"
    for rx, reason in USER_JUNK:
        if rx.search(user):
            return reason
    if meaningful(assistant) < 2:
        return "assistant_empty"
    if len(assistant) > MAX_ASSISTANT_CHARS:
        return "assistant_too_long"
    if cyr_ratio(assistant) < 0.6:
        return "assistant_not_russian"
    if CODE_RE.search(assistant) and not ASKS_CODE_RE.search(user):
        return "unrequested_code"
    if assistant.count("#") >= 3 or assistant.count("**") >= 6:
        return "heavy_markdown_report"
    for rx, reason in ASSISTANT_JUNK:
        if rx.search(assistant):
            return reason
    if norm(assistant) == norm(user):
        return "assistant_echoes_user"
    return None


def dal_score(user: str, assistant: str) -> tuple:
    """Higher = more useful Dal example: real definition with synonyms, moderate length, common word."""
    word = re.search(r"['\"«]([^'\"»]+)", user)
    word = word.group(1) if word else ""
    has_syn = "Синонимы" in assistant
    body = assistant.split("Синонимы")[0]
    n_defs = body.count(",") + 1
    return (has_syn, min(n_defs, 4), 20 <= len(assistant) <= 220, -len(word), -abs(len(assistant) - 110))

# ── Templated Q&A (deterministic, numbers computed, facts fixed) ────────────

CAPITALS = [("Германии", "Берлин"), ("Франции", "Париж"), ("Италии", "Рим"), ("Испании", "Мадрид"),
            ("Великобритании", "Лондон"), ("Китая", "Пекин"), ("Японии", "Токио"), ("Южной Кореи", "Сеул"),
            ("Монголии", "Улан-Батор"), ("Казахстана", "Астана"), ("Беларуси", "Минск"), ("Индии", "Нью-Дели"),
            ("Египта", "Каир"), ("Канады", "Оттава"), ("США", "Вашингтон"), ("Бразилии", "Бразилиа"),
            ("Австралии", "Канберра"), ("Турции", "Анкара"), ("Финляндии", "Хельсинки"), ("Норвегии", "Осло"),
            ("Швеции", "Стокгольм"), ("Польши", "Варшава"), ("Вьетнама", "Ханой"), ("Таиланда", "Бангкок")]
CAP_Q = ["Какая столица {c}?", "Столица {c}?", "Назови столицу {c}."]
LIGHT_ROOMS = [("Коридор", "в коридоре"), ("Туалет", "в туалете"), ("Кухня", "на кухне"), ("Ванная", "в ванной")]


def fmt_num(x: float) -> str:
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def templated(rng: random.Random):
    out = []
    ops = [("+", "плюс", lambda a, b: a + b), ("-", "минус", lambda a, b: a - b),
           ("*", "умножить на", lambda a, b: a * b), ("/", "разделить на", None)]
    qforms = ["Сколько будет {a} {w} {b}?", "{a} {s} {b} = ?", "Посчитай: {a} {w} {b}.", "Вычисли {a} {s} {b}."]
    for i in range(200):
        sym, word, fn = ops[i % 4]
        if sym == "/":
            b = rng.randint(2, 12); res = rng.randint(2, 30); a = b * res
        elif sym == "*":
            a, b = rng.randint(2, 25), rng.randint(2, 12); res = fn(a, b)
        else:
            a, b = rng.randint(10, 999), rng.randint(1, 500)
            if sym == "-" and b > a:
                a, b = b, a
            res = fn(a, b)
        q = rng.choice(qforms).format(a=a, b=b, w=word, s={"*": "×", "/": ":"}.get(sym, sym))
        ans = f"{a} {({'*': '×', '/': ':'}.get(sym, sym))} {b} = {res}." if rng.random() < 0.5 else f"{res}."
        out.append(("tmpl_arith", [("user", q), ("assistant", ans)]))
    for i in range(60):
        kind = i % 4
        if kind == 0:
            km = rng.randint(2, 90); out.append(("tmpl_units", [("user", f"Сколько метров в {km} км?"), ("assistant", f"{km * 1000} метров.")]))
        elif kind == 1:
            h = rng.randint(2, 12); out.append(("tmpl_units", [("user", f"Сколько минут в {h} часах?"), ("assistant", f"{h * 60} минут.")]))
        elif kind == 2:
            w = rng.choice([500, 800, 1200, 1500, 2000, 2500]); h = rng.choice([1, 2, 3, 4, 5])
            out.append(("tmpl_units", [("user", f"Прибор {w} Вт работает {h} ч. Сколько энергии он потратит?"),
                                       ("assistant", f"{w} Вт × {h} ч = {w * h} Вт·ч, то есть {fmt_num(w * h / 1000)} кВт·ч.")]))
        else:
            c = rng.choice([-30, -20, -10, 0, 10, 20, 25, 30, 40])
            out.append(("tmpl_units", [("user", f"Сколько будет {c} °C в Фаренгейтах?"),
                                       ("assistant", f"{c} °C × 9/5 + 32 = {fmt_num(c * 9 / 5 + 32)} °F.")]))
    for c, cap in CAPITALS:
        for q in rng.sample(CAP_Q, 2):
            out.append(("tmpl_capitals", [("user", q.format(c=c)), ("assistant", f"{cap}.")]))
    # Home Assistant readings in context → grounded answers (numbers computed, never invented)
    for i in range(60):
        v = rng.randint(215, 240); p = rng.choice([180, 350, 620, 900, 1250, 1800, 2400, 3100]); e = round(rng.uniform(2, 18), 1)
        block = (f"[Home Assistant]\n⚡ Электричество:\n  • Напряжение: {v} В\n  • Мощность: {p} Вт\n"
                 f"  • Энергия за сегодня: {fmt_num(e)} кВт·ч")
        q = rng.choice(["Сколько мы сейчас потребляем?", "Какое напряжение?", "Сколько потратили за сегодня?",
                        "Какой сейчас ток?", "Нормальное ли напряжение?"])
        if "потребляем" in q:
            a = f"Сейчас потребление {p} Вт при напряжении {v} В."
        elif q == "Какое напряжение?":
            a = f"Напряжение {v} В."
        elif "сегодня" in q:
            a = f"За сегодня — {fmt_num(e)} кВт·ч."
        elif "ток" in q:
            a = f"Ток примерно {fmt_num(p / v)} А: {p} Вт / {v} В."
        else:
            a = f"Да, {v} В — в норме (допустимо примерно от 207 до 253 В)."
        out.append(("tmpl_home_energy", [("user", f"{block}\n\n{q}"), ("assistant", a)]))
    for i in range(40):
        states = {room: rng.random() < 0.5 for room, _ in LIGHT_ROOMS}
        on = [r for r, s in states.items() if s]
        lines = "\n".join(f"  • {'ВКЛ' if states[r] else 'выкл'}: {r}" for r, _ in LIGHT_ROOMS)
        block = f"[Home Assistant]\n💡 Выключатели и свет:\n{lines}"
        if i % 2 == 0:
            q = rng.choice(["Где горит свет?", "Какой свет включён?", "Свет где-нибудь горит?"])
            a = ("Свет включён: " + ", ".join(r.lower() for r in on) + ".") if on else "Свет везде выключен."
        else:
            room, where = rng.choice(LIGHT_ROOMS)
            q = f"Горит ли свет {where}?"
            a = f"Да, свет {where} включён." if states[room] else f"Нет, свет {where} выключен."
        out.append(("tmpl_home_lights", [("user", f"{block}\n\n{q}"), ("assistant", a)]))
    # tool result present in context → report exactly what the tool said
    for room, where in LIGHT_ROOMS:
        for verb, state, word in (("Включи", "on", "включён"), ("Выключи", "off", "выключен")):
            out.append(("tmpl_home_lights", [
                ("user", f"[Home Assistant] результат: свет «{room}» → {state}\n\n{verb} свет {where}."),
                ("assistant", f"Home Assistant подтвердил: свет {where} {word}.")]))
        out.append(("tmpl_home_lights", [
            ("user", f"[Home Assistant] результат: свет «{room}» → unavailable (устройство не в сети)\n\nВключи свет {where}."),
            ("assistant", f"Не получилось: Home Assistant сообщает, что выключатель «{room}» не в сети. Свет {where} не включён.")]))
    return out

# ── Build ───────────────────────────────────────────────────────────────────

def to_record(source, turns):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs += [{"role": r, "content": c} for r, c in turns]
    return {"messages": msgs, "source": source}


def key_of(turns):
    return hashlib.md5("\x00".join(norm(c) for _, c in turns).encode()).hexdigest()


def first_user(turns):
    return norm(next(c for r, c in turns if r == "user"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    raw = collections.Counter()
    kept_by_src = collections.Counter()
    drops = collections.Counter()
    drops_by_src = collections.defaultdict(collections.Counter)
    drop_examples = collections.defaultdict(list)
    seen = set()
    pool, dal = [], []

    for src, messages in load_sources(args.src):
        raw[src] += 1
        turns = to_turns(messages)
        if not turns:
            drops["no_user_or_assistant"] += 1; drops_by_src[src]["no_user_or_assistant"] += 1
            continue
        cleaned, reason = [], None
        for u, a in turns:
            u, a = u.strip(), clean_assistant(a)
            reason = filter_turn(u, a)
            if reason:
                break
            cleaned.append(("user", u)); cleaned.append(("assistant", a))
        if reason:
            drops[reason] += 1; drops_by_src[src][reason] += 1
            if len(drop_examples[reason]) < 3:
                drop_examples[reason].append({"src": src, "user": turns[0][0][:160], "assistant": turns[0][1][:200]})
            continue
        k = key_of(cleaned)
        if k in seen:
            drops["duplicate"] += 1; drops_by_src[src]["duplicate"] += 1
            continue
        seen.add(k)
        is_dal = DAL_RE.search(cleaned[0][1]) or DAL_SYSTEM_RE.search(system_of(messages))
        (dal if is_dal else pool).append((src, cleaned))

    # seeds / templates
    hand = [(f"seed:{cat}", turns) for cat, turns in SEEDS]
    tmpl = [(f"seed:{cat}", turns) for cat, turns in templated(rng)]
    test_seeds = [(f"test_seed:{cat}", turns) for cat, turns in TEST_SEEDS]
    held_out_users = {first_user(t) for _, t in test_seeds} | {norm(p) for p in PROBES}

    def not_held_out(items, label):
        keep = []
        for s, t in items:
            if first_user(t) in held_out_users:
                drops["overlaps_test_or_probe"] += 1; drops_by_src[s]["overlaps_test_or_probe"] += 1
            else:
                keep.append((s, t))
        return keep

    pool, dal, tmpl = not_held_out(pool, "pool"), not_held_out(dal, "dal"), not_held_out(tmpl, "tmpl")
    seen_seed = set()
    hand_unique = []
    for s, t in not_held_out(hand, "hand"):
        k = key_of(t)
        if k not in seen_seed:
            seen_seed.add(k); hand_unique.append((s, t))
    hand = hand_unique
    pool = [(s, t) for s, t in pool if key_of(t) not in seen_seed]
    tmpl_seen = set()
    tmpl = [x for x in tmpl if not (key_of(x[1]) in tmpl_seen or tmpl_seen.add(key_of(x[1])))]

    # Dal cap: ≤10% of the final unique set
    non_dal = len(pool) + len(hand) + len(tmpl) + len(test_seeds)
    dal_max = int(DAL_MAX_SHARE / (1 - DAL_MAX_SHARE) * non_dal)
    dal_sorted = sorted(dal, key=lambda x: dal_score(x[1][0][1], x[1][1][1]), reverse=True)
    dal_kept = dal_sorted[:dal_max]
    drops["dal_over_cap"] += len(dal) - len(dal_kept)
    for s, _ in dal_sorted[dal_max:]:
        drops_by_src[s]["dal_over_cap"] += 1

    # split: the held-out test mirrors the target behaviour — seeds + templated + a little real data
    rng.shuffle(pool); rng.shuffle(dal_kept); rng.shuffle(tmpl)
    n_real = min(len(pool), TEST_POOL_SIZE // 6)
    n_dal = min(len(dal_kept), TEST_POOL_SIZE // 6)
    n_tmpl = TEST_POOL_SIZE - n_real - n_dal
    test_pool = pool[:n_real] + dal_kept[:n_dal] + tmpl[:n_tmpl]
    real = pool + dal_kept
    rest = pool[n_real:] + dal_kept[n_dal:] + tmpl[n_tmpl:]
    rng.shuffle(rest)
    n_val = max(50, int(len(rest) * VAL_SHARE))
    val, train = rest[:n_val], rest[n_val:]
    train = train + hand * SEED_UPSAMPLE
    rng.shuffle(train)
    test = test_seeds + test_pool

    for s, _ in real + tmpl + hand:
        kept_by_src[s] += 1

    os.makedirs(args.out, exist_ok=True)
    for name, rows in (("train", train), ("val", val), ("test", test)):
        with open(os.path.join(args.out, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for s, t in rows:
                f.write(json.dumps(to_record(s, t), ensure_ascii=False) + "\n")
    with open(os.path.join(args.out, "probes.jsonl"), "w", encoding="utf-8") as f:
        for p in PROBES:
            f.write(json.dumps({"prompt": p}, ensure_ascii=False) + "\n")

    def group(src):
        return src.split(":")[0] if src.startswith(("seed", "test_seed")) else src

    split_by_src = {name: collections.Counter(group(s) for s, _ in rows)
                    for name, rows in (("train", train), ("val", val), ("test", test))}
    stats = {
        "seed": args.seed, "system_prompt": SYSTEM_PROMPT,
        "raw_by_source": dict(raw.most_common()), "raw_total": sum(raw.values()),
        "kept_unique_by_source": dict(kept_by_src.most_common()),
        "dal_candidates": len(dal), "dal_kept": len(dal_kept), "dal_max": dal_max,
        "hand_written_seeds": len(hand), "templated_seeds": len(tmpl), "test_seeds": len(test_seeds),
        "split_sizes": {"train": len(train), "val": len(val), "test": len(test)},
        "split_by_source_group": {k: dict(v.most_common()) for k, v in split_by_src.items()},
        "drop_reasons": dict(drops.most_common()),
        "drop_reasons_by_source": {k: dict(v.most_common()) for k, v in drops_by_src.items()},
        "drop_examples": drop_examples,
    }
    with open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    # report
    print(f"Output: {args.out}")
    print(f"\nRAW rows by source (total {stats['raw_total']}):")
    for s, n in raw.most_common():
        print(f"  {s:45s} raw {n:6d}  kept {kept_by_src.get(s, 0):5d}")
    print(f"\nDal definitions: {len(dal)} unique candidates → kept {len(dal_kept)} (cap {dal_max}, ≤{DAL_MAX_SHARE:.0%})")
    print(f"Seeds: hand-written {len(hand)} (×{SEED_UPSAMPLE} in train), templated {len(tmpl)}, held-out test seeds {len(test_seeds)}")
    print("\nDrop reasons:")
    for r, n in drops.most_common():
        print(f"  {r:35s} {n:6d}")
    print("\nSplits:", stats["split_sizes"])
    for name, c in split_by_src.items():
        print(f"  {name:5s}", dict(c.most_common()))
    total_unique = len(real) + len(tmpl) + len(hand) + len(test_seeds)
    print(f"\nUnique examples: {total_unique}; Dal share {len(dal_kept) / total_unique:.1%}")


if __name__ == "__main__":
    main()
