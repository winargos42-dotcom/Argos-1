#!/usr/bin/env python3
"""
make_notebook.py — generates training/argos_v2_finetune.ipynb.

The system prompt and probe prompts are taken from argos_v2_seeds.py so the notebook, the dataset
builder and eval_local.py always agree. Re-run after editing the seeds:
    python training/make_notebook.py
Requires nbformat (validation) — pip install nbformat.
"""

from __future__ import annotations

import ast
import json
import os
import sys

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from argos_v2_seeds import PROBES, SYSTEM_PROMPT  # noqa: E402

EXTRA_PROBES = [
    "Какая погода будет завтра?",
    "Сколько нод сейчас онлайн?",
    "Ты ChatGPT?",
    "Привет!",
    ",??",
]

MODELFILE_TEMPLATE = r'''{{- if .Messages }}
{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}
{{- range $i, $_ := .Messages }}
{{- $last := eq (len (slice $.Messages $i)) 1 -}}
{{- if eq .Role "user" }}<|im_start|>user
{{ .Content }}<|im_end|>
{{ else if eq .Role "assistant" }}<|im_start|>assistant
{{ .Content }}{{ if not $last }}<|im_end|>
{{ end }}
{{- end }}
{{- if and (ne .Role "assistant") $last }}<|im_start|>assistant
{{ end }}
{{- end }}
{{- else }}
{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ end }}{{ .Response }}{{ if .Response }}<|im_end|>{{ end }}'''

cells = []
md = lambda s: cells.append(new_markdown_cell(s.strip()))  # noqa: E731
code = lambda s: cells.append(new_code_cell(s.strip()))  # noqa: E731

md("""
# ARGOS v2 — дообучение Qwen2.5-1.5B-Instruct (Unsloth, LoRA r=16)

Ноутбук для **Kaggle** (GPU T4 ×1 или P100, Internet: On) или **Google Colab** (T4).
Время: ~15–25 минут обучения + ~10 минут экспорт в GGUF.

Шаги: установка → данные → ответы базовой модели → LoRA → обучение только на ответах ассистента →
оценка (loss + пробы бок о бок) → GGUF Q4_K_M + Modelfile → (опционально) загрузка на HF.

**Токены вводятся только во время выполнения** (getpass или Kaggle Secrets) и нигде не сохраняются.
""")

code("""
# 1. Установка (2–4 минуты). На Kaggle включите: Settings → Accelerator: GPU T4 x1, Internet: On.
%pip install -q unsloth
%pip install -q "datasets>=2.18" pandas
""")

code(f'''
# 2. Настройки
import os, json, glob, getpass

DATA_SOURCE   = "auto"   # "drive" | "hf" | "kaggle" | "local" | "auto" (Kaggle input → Google Drive в Colab → HF)
DRIVE_DIR     = "/content/drive/MyDrive/ARGOS REBOOT/argos-v2"   # Colab: папка с train/val/test.jsonl на Google Диске
SAVE_TO_DRIVE = True     # Colab: копировать результат (GGUF, Modelfile) в DRIVE_DIR/release
HF_DATASET    = "AvaSiG/argos-v2-sft"          # приватный или публичный датасет владельца
KAGGLE_DIR    = "/kaggle/input/argos-v2-sft"   # Kaggle: Add Input → ваш датасет с train/val/test.jsonl
LOCAL_DIR     = "./argos-v2"                   # Colab: загрузите файлы сюда вручную
BASE_MODEL    = "unsloth/Qwen2.5-1.5B-Instruct"
MAX_SEQ_LEN   = 1024
LORA_R        = 16
LR            = 2e-4
NUM_EPOCHS    = None     # None = авто: 3 эпохи если <2000 примеров, 2 если <6000, иначе 1
SEED          = 42
OUT_NAME      = "argos-v2"
PUSH_TO_HF    = False    # True → загрузить GGUF в HF_GGUF_REPO (понадобится токен с правом write)
HF_GGUF_REPO  = "AvaSiG/argos-v2-gguf"
HF_PRIVATE    = True

SYSTEM_PROMPT = {json.dumps(SYSTEM_PROMPT, ensure_ascii=False)}

# Первые 5 — сравнительные пробы (stock vs v1), затем личность/честность
PROBES = {json.dumps(PROBES + EXTRA_PROBES, ensure_ascii=False, indent=4)}

IN_KAGGLE = os.path.exists("/kaggle")
WORK = "/kaggle/working" if IN_KAGGLE else os.getcwd()
print("Kaggle" if IN_KAGGLE else "Colab/другое", "| рабочая папка:", WORK)
''')

code("""
# 3. Токен Hugging Face (нужен только для приватного датасета и для загрузки GGUF).
#    Kaggle: Add-ons → Secrets → HF_TOKEN.  Colab: вводится вручную. Токен не печатается и не сохраняется.
HF_TOKEN = None
def get_hf_token():
    global HF_TOKEN
    if HF_TOKEN:
        return HF_TOKEN
    try:
        from kaggle_secrets import UserSecretsClient
        HF_TOKEN = UserSecretsClient().get_secret("HF_TOKEN")
    except Exception:
        pass
    if not HF_TOKEN:
        try:
            from google.colab import userdata
            HF_TOKEN = userdata.get("HF_TOKEN")
        except Exception:
            pass
    if not HF_TOKEN:
        HF_TOKEN = getpass.getpass("HF token (Enter — без токена): ").strip() or None
    return HF_TOKEN
""")

code("""
# 4. Данные: train / val / test в chat-формате {"messages": [...], "source": ...}
from datasets import load_dataset

def mount_drive():
    # Colab: подключить Google Диск (спросит разрешение один раз). Вне Colab — False.
    if os.path.isdir("/content/drive/MyDrive"):
        return True
    try:
        from google.colab import drive
    except ImportError:
        return False
    drive.mount("/content/drive")
    return os.path.isdir("/content/drive/MyDrive")

def pick_source():
    if DATA_SOURCE != "auto":
        return DATA_SOURCE
    global KAGGLE_DIR
    if os.path.exists(os.path.join(KAGGLE_DIR, "train.jsonl")):
        return "kaggle"
    found = sorted(glob.glob("/kaggle/input/**/train.jsonl", recursive=True))
    if found:  # путь монтирования Kaggle может отличаться — ищем train.jsonl во всех входах
        KAGGLE_DIR = os.path.dirname(found[0])
        return "kaggle"
    if os.path.exists(os.path.join(LOCAL_DIR, "train.jsonl")):
        return "local"
    if mount_drive() and os.path.exists(os.path.join(DRIVE_DIR, "train.jsonl")):
        return "drive"
    return "hf"

src = pick_source()
files = {"train": "train.jsonl", "validation": "val.jsonl", "test": "test.jsonl"}
if src == "hf":
    try:
        ds = load_dataset(HF_DATASET, data_files=files)
    except Exception:
        ds = load_dataset(HF_DATASET, data_files=files, token=get_hf_token())
else:
    if src == "drive":
        assert mount_drive(), "Google Диск не подключён"
    base = {"kaggle": KAGGLE_DIR, "drive": DRIVE_DIR}.get(src, LOCAL_DIR)
    ds = load_dataset("json", data_files={k: os.path.join(base, v) for k, v in files.items()})
print("источник:", src)
print(ds)
from collections import Counter
print(Counter(s.split(":")[0] for s in ds["train"]["source"]).most_common())
print(json.dumps(ds["train"][0]["messages"], ensure_ascii=False, indent=1)[:800])
""")

code("""
# 5. Модель 4-bit + токенизатор с родным chat template Qwen2.5
import torch
from unsloth import FastLanguageModel, is_bfloat16_supported

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=BASE_MODEL, max_seq_length=MAX_SEQ_LEN, dtype=None, load_in_4bit=True,
)
assert "<|im_start|>" in (tokenizer.chat_template or ""), "ожидался chat template Qwen2.5"
print(torch.cuda.get_device_name(0), "| bf16:", is_bfloat16_supported())
""")

code("""
# 6. Генерация (жадная, как temperature 0) и ответы БАЗОВОЙ модели до обучения
def generate(prompt, history=None, max_new_tokens=128):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + (history or []) + [{"role": "user", "content": prompt}]
    ids = tokenizer.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt").to("cuda")
    with torch.no_grad():
        out = model.generate(input_ids=ids, max_new_tokens=max_new_tokens, do_sample=False,
                             repetition_penalty=1.05, pad_token_id=tokenizer.eos_token_id)
    return tokenizer.decode(out[0, ids.shape[1]:], skip_special_tokens=True).strip()

FastLanguageModel.for_inference(model)
base_answers = {p: generate(p) for p in PROBES}
for p, a in base_answers.items():
    print(f"Q: {p}\\nBASE: {a}\\n")
""")

code("""
# 7. LoRA r=16 на все проекционные слои
FastLanguageModel.for_training(model)
model = FastLanguageModel.get_peft_model(
    model, r=LORA_R, lora_alpha=LORA_R, lora_dropout=0, bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth", random_state=SEED,
)
model.print_trainable_parameters()
""")

code("""
# 8. Текст по chat template (system prompt уже внутри каждого примера)
def to_text(batch):
    return {"text": [tokenizer.apply_chat_template(m, tokenize=False) for m in batch["messages"]]}

train_ds = ds["train"].map(to_text, batched=True, remove_columns=ds["train"].column_names)
val_ds   = ds["validation"].map(to_text, batched=True, remove_columns=ds["validation"].column_names)
test_ds  = ds["test"].map(to_text, batched=True, remove_columns=ds["test"].column_names)
lens = [len(tokenizer(t).input_ids) for t in train_ds["text"]]
print("примеров:", len(train_ds), "| токенов max/avg:", max(lens), sum(lens) // len(lens),
      "| длиннее MAX_SEQ_LEN:", sum(l > MAX_SEQ_LEN for l in lens))
print(train_ds[0]["text"][:600])
""")

code("""
# 9. SFTTrainer (совместим со старыми и новыми версиями TRL) + обучение только на ответах ассистента
import inspect
from trl import SFTTrainer, SFTConfig
from unsloth.chat_templates import train_on_responses_only

n = len(train_ds)
epochs = NUM_EPOCHS or (3 if n < 2000 else 2 if n < 6000 else 1)
batch, accum = 8, 2
steps_per_epoch = max(1, n // (batch * accum))
eval_every = max(10, steps_per_epoch // 2)

cfg = dict(
    output_dir=os.path.join(WORK, "outputs"), per_device_train_batch_size=batch, per_device_eval_batch_size=batch,
    gradient_accumulation_steps=accum, num_train_epochs=epochs, learning_rate=LR, lr_scheduler_type="cosine",
    warmup_ratio=0.05, weight_decay=0.01, optim="adamw_8bit", fp16=not is_bfloat16_supported(),
    bf16=is_bfloat16_supported(), logging_steps=5, eval_steps=eval_every, save_strategy="no",
    seed=SEED, report_to="none", dataset_text_field="text", packing=False, dataset_num_proc=2,
)
params = inspect.signature(SFTConfig.__init__).parameters
cfg["max_length" if "max_length" in params else "max_seq_length"] = MAX_SEQ_LEN
cfg["eval_strategy" if "eval_strategy" in params else "evaluation_strategy"] = "steps"
cfg = {k: v for k, v in cfg.items() if k in params}

tr_params = inspect.signature(SFTTrainer.__init__).parameters
tok_kw = {"processing_class": tokenizer} if "processing_class" in tr_params else {"tokenizer": tokenizer}
trainer = SFTTrainer(model=model, train_dataset=train_ds, eval_dataset=val_ds, args=SFTConfig(**cfg), **tok_kw)

# маскируем system и user: loss считается только по токенам ассистента (в т.ч. в многоходовых диалогах)
trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\\n",
                                  response_part="<|im_start|>assistant\\n")
print(f"epochs={epochs}, steps≈{steps_per_epoch * epochs}, eval каждые {eval_every} шагов")
""")

code("""
# 10. Проверка маски: должны остаться только ответы ассистента
ex = trainer.train_dataset[0]
print("ОБУЧАЕМЫЕ ТОКЕНЫ:", repr(tokenizer.decode([t for t, l in zip(ex["input_ids"], ex["labels"]) if l != -100])))
assert any(l != -100 for l in ex["labels"]), "маска пустая — проверьте instruction_part/response_part"
""")

code("""
# 11. Обучение
base_eval = trainer.evaluate()
print("val loss ДО обучения:", round(base_eval["eval_loss"], 4))
stats = trainer.train()
print(stats)
""")

code("""
# 12. Оценка: loss на val и на отложенном test
val_eval = trainer.evaluate()
test_eval = trainer.evaluate(eval_dataset=test_ds, metric_key_prefix="test")
print(f"val loss:  {base_eval['eval_loss']:.4f} → {val_eval['eval_loss']:.4f}")
print(f"test loss: {test_eval['test_loss']:.4f}")
import pandas as pd
hist = pd.DataFrame(trainer.state.log_history)
display(hist[[c for c in ["step", "loss", "eval_loss", "learning_rate"] if c in hist]].dropna(how="all", subset=["loss", "eval_loss"]).tail(20))
""")

code("""
# 13. Пробы бок о бок: базовая модель vs argos-v2
FastLanguageModel.for_inference(model)
tuned_answers = {p: generate(p) for p in PROBES}
pd.set_option("display.max_colwidth", 400)
cmp = pd.DataFrame({"проба": PROBES, "base Qwen2.5-1.5B": [base_answers[p] for p in PROBES],
                    "argos-v2": [tuned_answers[p] for p in PROBES]})
display(cmp)
""")

code("""
# 14. Отложенный test: ответы против эталона (первые 25)
rows = []
for r in ds["test"].select(range(min(25, len(ds["test"])))):
    msgs = [m for m in r["messages"] if m["role"] != "system"]
    last = max(i for i, m in enumerate(msgs) if m["role"] == "user")
    ref = msgs[last + 1]["content"] if last + 1 < len(msgs) else ""
    rows.append({"source": r["source"], "вопрос": msgs[last]["content"][:200], "эталон": ref[:200],
                 "argos-v2": generate(msgs[last]["content"], history=msgs[:last])[:300]})
display(pd.DataFrame(rows))
""")

code(f'''
# 15. Сохранение LoRA и экспорт в GGUF Q4_K_M (llama.cpp собирается Unsloth автоматически, ~5–10 мин)
lora_dir = os.path.join(WORK, OUT_NAME + "-lora")
model.save_pretrained(lora_dir); tokenizer.save_pretrained(lora_dir)

gguf_dir = os.path.join(WORK, OUT_NAME + "-gguf")
model.save_pretrained_gguf(gguf_dir, tokenizer, quantization_method="q4_k_m")

cands = [p for p in glob.glob(os.path.join(WORK, "**", "*.gguf"), recursive=True) if "q4_k_m" in p.lower()]
assert cands, "GGUF не найден — смотрите лог выше"
src_gguf = max(cands, key=os.path.getmtime)
final_dir = os.path.join(WORK, OUT_NAME + "-release"); os.makedirs(final_dir, exist_ok=True)
gguf_path = os.path.join(final_dir, OUT_NAME + "-Q4_K_M.gguf")
os.replace(src_gguf, gguf_path)
print(gguf_path, round(os.path.getsize(gguf_path) / 2**20), "MiB")
''')

code(f'''
# 16. Modelfile для Ollama: шаблон Qwen2.5 (ChatML), temperature 0.3, num_ctx 4096, канонический system prompt
TEMPLATE = {json.dumps(MODELFILE_TEMPLATE, ensure_ascii=False)}
modelfile = f"""FROM ./{{OUT_NAME}}-Q4_K_M.gguf

TEMPLATE \\"\\"\\"{{TEMPLATE}}\\"\\"\\"

SYSTEM \\"\\"\\"{{SYSTEM_PROMPT}}\\"\\"\\"

PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER repeat_penalty 1.05
PARAMETER num_ctx 4096
PARAMETER stop <|im_start|>
PARAMETER stop <|im_end|>
PARAMETER stop <|endoftext|>
"""
with open(os.path.join(final_dir, "Modelfile"), "w", encoding="utf-8") as f:
    f.write(modelfile)
with open(os.path.join(final_dir, "probes_side_by_side.json"), "w", encoding="utf-8") as f:
    json.dump({{"val_loss": val_eval["eval_loss"], "test_loss": test_eval["test_loss"],
               "probes": [{{"prompt": p, "base": base_answers[p], "argos_v2": tuned_answers[p]}} for p in PROBES]}},
              f, ensure_ascii=False, indent=2)
print(modelfile)
''')

code("""
# 17. Забрать результат
#  Kaggle: всё в /kaggle/working/argos-v2-release попадёт во вкладку Output после "Save Version"
#          (или скачайте файлы из панели справа). Colab: скачивание ниже или копия на Google Drive.
import shutil
archive = shutil.make_archive(os.path.join(WORK, OUT_NAME + "-release"), "zip", final_dir)
print(archive, round(os.path.getsize(archive) / 2**20), "MiB")
if not IN_KAGGLE and SAVE_TO_DRIVE and mount_drive():
    dest = os.path.join(DRIVE_DIR, "release")
    shutil.copytree(final_dir, dest, dirs_exist_ok=True)
    print("скопировано на Google Диск:", dest)
elif not IN_KAGGLE:
    try:
        from google.colab import files
        files.download(archive)
    except Exception as e:
        print("скачайте вручную:", archive, e)
""")

code("""
# 18. (Опционально) загрузка на Hugging Face — только если PUSH_TO_HF = True. Токен с правом write.
if PUSH_TO_HF:
    from huggingface_hub import HfApi
    api = HfApi(token=get_hf_token())
    api.create_repo(HF_GGUF_REPO, repo_type="model", private=HF_PRIVATE, exist_ok=True)
    card = (f"# {OUT_NAME}\\n\\nARGOS local model: Qwen2.5-1.5B-Instruct + LoRA r={LORA_R} (Unsloth), GGUF Q4_K_M.\\n\\n"
            f"val loss {val_eval['eval_loss']:.4f}, test loss {test_eval['test_loss']:.4f}.\\n\\n"
            "```\\nollama create argos-v2 -f Modelfile\\n```\\n")
    with open(os.path.join(final_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(card)
    api.upload_folder(folder_path=final_dir, repo_id=HF_GGUF_REPO, repo_type="model")
    print("загружено: https://huggingface.co/" + HF_GGUF_REPO)
else:
    print("PUSH_TO_HF = False — пропускаю загрузку")
""")

md("""
## Установка в Ollama на X230

```bash
# положить argos-v2-Q4_K_M.gguf и Modelfile в одну папку, например /home/.argos-storage/models/argos-v2
cd /home/.argos-storage/models/argos-v2
ollama create argos-v2 -f Modelfile
python training/eval_local.py --models argos-local argos-v2      # сравнение на пробах
python training/eval_local.py --models argos-v2 --set test --limit 30
```
Переключать ARGOS на `argos-v2` стоит только если он лучше `argos-local` на пробах и test.
""")

nb = new_notebook(cells=cells, metadata={
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
    "accelerator": "GPU",
    "colab": {"provenance": [], "gpuType": "T4"},
    "kaggle": {"accelerator": "gpu", "isInternetEnabled": True},
})


def check(nb):
    nbformat.validate(nb)
    for i, c in enumerate(nb.cells):
        if c.cell_type != "code":
            continue
        src = "\n".join(l for l in c.source.splitlines() if not l.lstrip().startswith(("%", "!")))
        ast.parse(src, filename=f"cell{i}")


if __name__ == "__main__":
    check(nb)
    out = os.path.join(HERE, "argos_v2_finetune.ipynb")
    nbformat.write(nb, out)
    check(nbformat.read(out, as_version=4))
    json.load(open(out, encoding="utf-8"))
    print(f"written + validated: {out} ({len(nb.cells)} cells)")
