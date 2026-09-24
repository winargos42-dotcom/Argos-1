"""Отбор кандидатов из Big Russian Dataset (ZeroAgency, MIT) для argos-v2.

Исходник: бакет hf://buckets/AvaSiG/ru-big-russian-dataset-bucket (зеркало ZeroAgency/ru-big-russian-dataset),
1,71 млн диалогов с оценками GPT-4.1 по 17 критериям. Скрипт читает parquet потоком (по пакетам, без pandas),
оставляет лучшие короткие русские диалоги без рассуждений и ролевых игр и делает стратифицированную выборку
по теме и источнику. Дальше build_argos_v2_dataset.py прогоняет их через те же фильтры, что и данные ARGOS,
и заменяет system prompt на канонический.

    hf buckets cp hf://buckets/AvaSiG/ru-big-russian-dataset-bucket/data/<файл> /home/.argos-storage/datasets/big-russian/data/
    python training/select_big_russian.py            # → big-russian/candidates.jsonl + candidates_stats.json
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import random
import re

DEFAULT_DIR = "/home/.argos-storage/datasets/big-russian"
TARGET = 8000          # кандидатов (после фильтров билдера и дедупликации остаётся заметно меньше)
PER_TOPIC_SHARE = 0.08  # одна тема — не больше 8 % выборки
PER_SOURCE_SHARE = 0.20

# Источники, которые не подходят ассистенту ARGOS
EXCLUDE_SOURCE = re.compile(
    r"ru-alpaca-summ"          # пересказ новостей с фиксированным system prompt
    r"|english_orig"           # английские диалоги lmsys
    r"|math|physics|sdamgia|MATH-500|orca-math|grade_school"  # длинные решения задач с формулами
    r"|codefeedback", re.I)
# Системные промпты из самого датасета, которые можно безопасно заменить на ARGOS
GENERIC_SYSTEM = re.compile(r"^Ты виртуальный ассистент\. Ты отвечаешь на вопросы людей")
SCORE_MIN = {"overall_score": 8, "quality": 8, "correctness": 8, "helpful": 7, "coherence": 8,
             "relevance": 8, "error_free": 8, "safety": 8, "rude_ethic": 8, "conciseness": 6,
             "no_useless_extra": 6}
COLUMNS = ["conversation", "source", "has_reasoning", "classified_topic", "refusal", "role_play", "pii_leak",
           *SCORE_MIN]
MAX_USER, MAX_ASSISTANT = 500, 900
# Артефакты машинного перевода (OpenOrca и др.): токены скобок PTB, невидимые символы
ARTIFACT_RE = re.compile(r"-[lr][rcs]b-", re.I)
ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def clean_text(text: str) -> str:
    return ZERO_WIDTH_RE.sub("", text or "")


def cyr_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum("а" <= c.lower() <= "я" or c.lower() == "ё" for c in letters) / len(letters) if letters else 0.0


def reject(row) -> str | None:
    """Причина отказа или None."""
    if EXCLUDE_SOURCE.search(row["source"] or ""):
        return "source_excluded"
    if row["has_reasoning"]:
        return "reasoning"
    if row["refusal"] == 1 or row["role_play"] == 1 or row["pii_leak"] == 1:
        return "refusal_roleplay_pii"
    for key, low in SCORE_MIN.items():
        value = row[key]
        if value is None or (value != -1 and value < low):
            return f"score_{key}"
    conv = row["conversation"] or []
    system = [m["content"] for m in conv if m["role"] == "system"]
    if system and not GENERIC_SYSTEM.search(system[0] or ""):
        return "custom_system_prompt"
    dialog = [m for m in conv if m["role"] in ("user", "assistant")]
    if not dialog or dialog[0]["role"] != "user" or dialog[-1]["role"] != "assistant" or len(dialog) > 6:
        return "shape"
    for m in dialog:
        text = m["content"] or ""
        if "<think>" in text:
            return "reasoning"
        if ARTIFACT_RE.search(text):
            return "translation_artifacts"
        limit = MAX_USER if m["role"] == "user" else MAX_ASSISTANT
        if len(text) > limit:
            return f"{m['role']}_too_long"
        if cyr_ratio(text) < 0.6:
            return "not_russian"
    return None


def iter_rows(files):
    import pyarrow.parquet as pq

    for path in files:
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=4000, columns=COLUMNS):
            yield from batch.to_pylist()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--target", type=int, default=TARGET)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.dir, "data", "train-*.parquet")))
    if not files:
        raise SystemExit(f"Нет train-*.parquet в {args.dir}/data")

    rng = random.Random(args.seed)
    reasons, passed_by_topic, passed_by_source = collections.Counter(), collections.Counter(), collections.Counter()
    # Резервуарная выборка по каждой теме: память не растёт с размером датасета
    reservoir: dict[str, list] = collections.defaultdict(list)
    seen_topic: collections.Counter = collections.Counter()
    cap_per_topic = max(50, int(args.target * PER_TOPIC_SHARE))
    total = 0
    for row in iter_rows(files):
        total += 1
        why = reject(row)
        if why:
            reasons[why] += 1
            continue
        topic = (row["classified_topic"] or "none").strip().lower() or "none"
        passed_by_topic[topic] += 1
        passed_by_source[row["source"]] += 1
        seen_topic[topic] += 1
        item = {"source": row["source"], "topic": topic,
                "messages": [{"role": m["role"], "content": clean_text(m["content"])} for m in row["conversation"]
                             if m["role"] in ("user", "assistant")]}
        bucket = reservoir[topic]
        if len(bucket) < cap_per_topic:
            bucket.append(item)
        else:
            j = rng.randrange(seen_topic[topic])
            if j < cap_per_topic:
                bucket[j] = item

    # Равномерно по темам, затем ограничение доли одного источника
    pools = {t: rng.sample(b, len(b)) for t, b in reservoir.items()}
    chosen, by_source = [], collections.Counter()
    source_cap = int(args.target * PER_SOURCE_SHARE)
    while len(chosen) < args.target and any(pools.values()):
        for topic in sorted(pools):
            if not pools[topic] or len(chosen) >= args.target:
                continue
            item = pools[topic].pop()
            if by_source[item["source"]] >= source_cap:
                continue
            chosen.append(item)
            by_source[item["source"]] += 1
    rng.shuffle(chosen)

    out = os.path.join(args.dir, "candidates.jsonl")
    with open(out, "w", encoding="utf-8") as fh:
        for item in chosen:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    stats = {"files": [os.path.basename(f) for f in files], "rows_read": total,
             "passed": sum(passed_by_topic.values()), "chosen": len(chosen),
             "reject_reasons": dict(reasons.most_common()),
             "passed_by_topic": dict(passed_by_topic.most_common(40)),
             "passed_by_source": dict(passed_by_source.most_common()),
             "chosen_by_source": dict(by_source.most_common()),
             "chosen_by_topic": dict(collections.Counter(i["topic"] for i in chosen).most_common(40))}
    with open(os.path.join(args.dir, "candidates_stats.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    print(f"прочитано {total}, прошло {stats['passed']}, выбрано {len(chosen)} → {out}")
    print("причины отказа:", dict(reasons.most_common(12)))
    print("по источникам:", dict(by_source.most_common(12)))


if __name__ == "__main__":
    main()
