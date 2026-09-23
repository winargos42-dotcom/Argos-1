#!/usr/bin/env python3
"""
eval_local.py — side-by-side evaluation of local Ollama models on the ARGOS probe / test set.

Talks to Ollama at http://127.0.0.1:11434 (/api/chat, stream=false), temperature 0, short num_predict.
Writes a markdown report with every answer, its latency and a few automatic red flags.

Examples:
  # baseline on the 10 fixed probes (default)
  python training/eval_local.py --models argos-local argos-v1
  # held-out test set (first 30 examples), compare with the reference answers
  python training/eval_local.py --models argos-v2 --set test --limit 30
  # use each model's own Modelfile SYSTEM instead of the canonical ARGOS prompt
  python training/eval_local.py --models argos-local --system modelfile

Only the standard library is used.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from argos_v2_seeds import PROBES, SYSTEM_PROMPT  # noqa: E402

DEFAULT_DATA = "/home/.argos-storage/datasets/argos-v2"

# expected facts for the probes: (regex that a good answer should match, regex that marks a bad answer)
PROBE_CHECKS = {
    "Кто ты?": (r"аргос|argos", r"qwen|alibaba|отвечай на русском"),
    "Что такое ARGOS?": (r"умн\w+ дом|помощник|систем", r"^$"),
    "Самый большой океан?": (r"тих", r"аркус|атлант"),
    "Сколько будет 17 умножить на 3?": (r"\b51\b", r""),
    "Который час?": (r"час\w* нет|не знаю|навык|нет доступа|не могу", r"\b\d{1,2}:\d{2}\b"),
    "Кто тебя создал?": (r"(создал|сделал|разработал)\w*\s+(меня\s+)?всеволод|всеволод\w*\s*(создал|сделал|—|-)|проект\w* всеволод",
                         r"alibaba|openai"),
    "Включи свет в спальне.": (r"не знаю|нет|не могу|известн", r"включ(ил|ён|ен)\b|готово|выполнено"),
    "Сколько электричества мы потратили сегодня?": (r"home assistant|нет данных|не знаю|нет доступа|не могу", r"\d+[,.]?\d*\s*(квт|kwh|вт)"),
    "Какой курс доллара сегодня?": (r"не знаю|нет (данных|доступа|актуальн)|не могу", r"\b\d{2,3}[,.]\d{1,2}\s*(руб|₽)"),
    "Установи пакет argos-turbo-boost.": (r"пакет\w*.*(не знаю|не существует|нет такого|не найден)|не могу установ|провер\w* (название|на pypi)",
                                          r"установлен|успешно|готово"),
}


def chat(host: str, model: str, messages: list[dict], num_predict: int, timeout: int) -> tuple[str, float, dict]:
    body = json.dumps({"model": model, "messages": messages, "stream": False,
                       "options": {"temperature": 0, "num_predict": num_predict}}).encode()
    req = urllib.request.Request(f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data.get("message", {}).get("content", ""), time.time() - t0, data


def load_items(args) -> list[dict]:
    if args.set == "probes":
        return [{"id": f"p{i + 1}", "history": [], "prompt": p, "reference": None} for i, p in enumerate(PROBES)]
    path = os.path.join(args.data, "test.jsonl")
    items = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            r = json.loads(line)
            msgs = [m for m in r["messages"] if m["role"] != "system"]
            # evaluate the LAST user turn; earlier turns are given as history
            last_user = max(j for j, m in enumerate(msgs) if m["role"] == "user")
            items.append({"id": f"t{i + 1}", "history": msgs[:last_user], "prompt": msgs[last_user]["content"],
                          "reference": msgs[last_user + 1]["content"] if last_user + 1 < len(msgs) else None,
                          "source": r.get("source")})
    return items[: args.limit] if args.limit else items


def flags(prompt: str, answer: str) -> list[str]:
    out = []
    a = answer.strip()
    if not a:
        out.append("EMPTY")
    if re.search(r"отвечай (на )?(русском|по-русски)", a, re.I):
        out.append("INSTRUCTION_ECHO")
    if a and len(re.findall(r"[А-Яа-яЁё]", a)) < 0.5 * max(1, len(re.findall(r"[A-Za-zА-Яа-яЁё]", a))):
        out.append("NOT_RUSSIAN")
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", a):
        out.append("CJK_LEAK")
    if "```" in a and not re.search(r"код|скрипт|python|команд|функци", prompt, re.I):
        out.append("UNASKED_CODE")
    check = PROBE_CHECKS.get(prompt)
    if check and a:
        good, bad = check
        if bad and re.search(bad, a, re.I):
            out.append("BAD")
        elif not re.search(good, a, re.I):
            out.append("MISSING_EXPECTED")
        else:
            out.append("OK")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["argos-local", "argos-v1"])
    ap.add_argument("--set", choices=["probes", "test"], default="probes")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--system", choices=["canonical", "modelfile"], default="canonical",
                    help="canonical = send the ARGOS system prompt; modelfile = rely on the model's own SYSTEM")
    ap.add_argument("--num-predict", type=int, default=96)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    items = load_items(args)
    results = {m: [] for m in args.models}
    t_start = time.time()
    for model in args.models:
        for it in items:
            msgs = ([{"role": "system", "content": SYSTEM_PROMPT}] if args.system == "canonical" else [])
            msgs += it["history"] + [{"role": "user", "content": it["prompt"]}]
            try:
                ans, secs, raw = chat(args.host, model, msgs, args.num_predict, args.timeout)
                toks = raw.get("eval_count", 0)
            except Exception as e:  # keep going; a failed call is part of the report
                ans, secs, toks = f"[ОШИБКА ЗАПРОСА: {e}]", 0.0, 0
            results[model].append({"answer": ans, "secs": secs, "tokens": toks, "flags": flags(it["prompt"], ans)})
            print(f"[{model}] {it['id']} {secs:5.1f}s {it['prompt'][:50]!r} → {ans[:70]!r}", flush=True)
    total = time.time() - t_start

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    out = args.out or os.path.join(args.data, "eval", f"eval_{args.set}_{stamp}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    lines = [f"# ARGOS local eval — {args.set}", "",
             f"- date: {dt.datetime.now():%Y-%m-%d %H:%M}", f"- models: {', '.join(args.models)}",
             f"- system prompt: {args.system}", f"- options: temperature 0, num_predict {args.num_predict}",
             f"- items: {len(items)}, total wall time: {total:.0f} s", "", "## Summary", "",
             "| model | OK | BAD | MISSING | EMPTY | echo | CJK | avg s | avg tok/s |", "|---|---|---|---|---|---|---|---|---|"]
    for m in args.models:
        rs = results[m]
        cnt = lambda f: sum(f in r["flags"] for r in rs)  # noqa: E731
        secs = sum(r["secs"] for r in rs)
        toks = sum(r["tokens"] for r in rs)
        lines.append(f"| {m} | {cnt('OK')} | {cnt('BAD')} | {cnt('MISSING_EXPECTED')} | {cnt('EMPTY')} | "
                     f"{cnt('INSTRUCTION_ECHO')} | {cnt('CJK_LEAK')} | {secs / max(1, len(rs)):.1f} | {toks / secs if secs else 0:.1f} |")
    lines += ["", "OK/BAD/MISSING are regex heuristics for the probe set only — read the answers.", "", "## Answers", ""]
    for i, it in enumerate(items):
        lines.append(f"### {it['id']}. {it['prompt']}")
        if it.get("reference"):
            lines.append(f"*Эталон:* {it['reference']}")
        lines.append("")
        for m in args.models:
            r = results[m][i]
            ans = r["answer"].strip().replace("\n", "<br>") or "*(пусто)*"
            lines.append(f"- **{m}** ({r['secs']:.1f} s, {' '.join(r['flags'])}): {ans}")
        lines.append("")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.splitext(out)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump({"items": items, "results": results, "system": args.system}, f, ensure_ascii=False, indent=1)
    print(f"\nReport: {out}  (total {total:.0f} s)")


if __name__ == "__main__":
    main()
