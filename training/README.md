# argos-v2: датасет и дообучение локальной модели ARGOS

Здесь лежит всё для следующей версии локальной модели ARGOS (`argos-v2`, основа Qwen2.5-1.5B-Instruct):

| Файл | Назначение |
|---|---|
| `argos_v2_seeds.py` | канонический system prompt, написанные вручную примеры (seed), отложенные тестовые seed и 10 проб |
| `build_argos_v2_dataset.py` | детерминированная сборка train/val/test из датасетов на HF + seed (seed=42) |
| `make_notebook.py` | генерирует `argos_v2_finetune.ipynb` (system prompt и пробы берутся из `argos_v2_seeds.py`) |
| `argos_v2_finetune.ipynb` | обучение на Kaggle/Colab: Unsloth + TRL, LoRA r=16, экспорт GGUF Q4_K_M + Modelfile |
| `eval_local.py` | сравнение моделей Ollama на пробах или отложенном test, отчёт в markdown |

## Почему v1 оказалась хуже стоковой модели

Датасет `AvaSiG/argos-canonical` почти целиком состоит из логов работающей системы, а не из хороших диалогов:
системные дампы («👁️ argos [system] ✅ задача #3…», «долгосрочная память аргоса…»), выдуманные отчёты о статусе
(«27 из 33 нод онлайн», CPU/RAM, IP-адреса, пути Windows), заявления о выполненных действиях без результата
инструмента, болтовня внутренних агентов, NSFW, тексты длиной на страницу. Источник `argos_dal_full_dataset`
лишь частично состоит из статей словаря Даля: из 4142 примеров «Объясни слово…» / «Что значит слово…» — всего
около 240, ещё 291 — «Напиши отрывок из русской классической литературы» (один и тот же вопрос, случайный текст),
остальное — те же системные логи. `argos-chat-dataset` и `argos-quantum-train-v2` — надмножества тех же логов,
плюс дампы файлов («Tell me about: SKILL.md»). `AvaSiG/ru-reasoning-train` на Hub пустой (только `.gitattributes`).

Кроме того, у v1 были разные системные промпты, у Modelfile — temperature 1.5 и системный промпт Qwen.
Сейчас `argos-v1` в Ollama на любой вопрос сразу выдаёт конец ответа (пустая строка).

## Сборка датасета

```bash
# 1. скачать исходники (анонимно, публичные репозитории) — только в /home, не на /
for d in argos-canonical argos-chat-dataset argos-quantum-train-v2 ru-reasoning-train; do
  hf download AvaSiG/$d --repo-type dataset --local-dir /home/.argos-storage/datasets/$d
done
# 2. собрать (нужен pyarrow для parquet; ~15 с)
/root/argos-recovery-venv/bin/python training/build_argos_v2_dataset.py
# → /home/.argos-storage/datasets/argos-v2/{train,val,test}.jsonl, probes.jsonl, stats.json
```

Что делает скрипт:

1. читает все строки всех источников, **выбрасывает исходный system prompt** и ставит один канонический:
   личность АРГОС + правила (русский язык, кратко, не заявлять о действии без результата инструмента,
   «не знаю»/«нет данных» вместо выдумок, код только по просьбе);
2. убирает префиксы вида `ARGOS [ARGOS]` и фильтрует диалоги, записывая причину отбраковки
   (см. `stats.json` → `drop_reasons`, `drop_examples`):
   - пользователь: < 3 значимых символов; не похоже на настоящий вопрос или просьбу; системные и
     файловые дампы; куски логов и кода; секреты (API-ключи); команды бота; вставки из экспорта чата; NSFW;
   - ассистент: пустой ответ, слишком длинный, не по-русски, повтор инструкции («Отвечай на русском»), ошибки и
     шаблонные отказы, «Чем могу помочь?», заявления о выполненных действиях («запустил», «готово», ✅, «Прочитал…»),
     выдуманные статусы (CPU, ноды, %, IP, `C:\`), болтовня внутренних агентов, устаревшие детали ARGOS
     (машины, навыки), ссылки, JSON-дампы, тяжёлый markdown, код, о котором не просили;
3. статьи словаря Даля (и «народные» вопросы в том же стиле) ограничены **≤10 %** итогового набора;
   остаются самые полезные: с синонимами, несколькими толкованиями, умеренной длины;
4. убирает дубликаты по нормализованному тексту user+assistant (между всеми источниками);
5. добавляет **292 написанных вручную примера** (личность, честность, отказ выдумывать устройства и пакеты,
   умный дом, факты, бытовые вопросы, уточнение непонятного ввода, многоходовые диалоги) и **392 шаблонных**
   (арифметика, единицы, столицы, показания Home Assistant в контексте, результаты команд света — все числа
   вычисляются, а не выдумываются);
6. test (100) = 40 отложенных seed с другими формулировками + 60 из пула (шаблоны, немного Даля и реальных
   данных); всё, что совпадает с test или пробами по вопросу, удаляется из train/val. Написанные вручную seed
   в train повторяются 2 раза.

Результат при seed=42 (подробности в `stats.json`):

| | примеров |
|---|---|
| сырых строк во всех источниках | 42 949 |
| реальных после фильтров и дедупликации | 97 (из них словарь Даля 82 — это ровно 10 % при потолке) |
| seed вручную / шаблонные / тестовые seed | 292 / 392 / 40 |
| **train / val / test** | **963 / 50 / 100** |

Набор получился маленьким (~0,8 тыс. уникальных примеров вместо ожидаемых 5–8 тыс.): качественных
диалогов в исходниках почти нет, а ослаблять фильтры значит вернуть то, что испортило v1. Для LoRA,
которая должна закрепить личность, честность и стиль, этого достаточно. Ноутбук сам выбирает 3 эпохи
для набора < 2000 примеров. Если захочется больше общих знаний, можно добавить 2–3 тыс. примеров из
открытого русскоязычного instruct-датасета с подходящей лицензией (прогнав их через те же фильтры
и заменив system prompt).

## Общие знания: Big Russian Dataset (2026-09-24)

Маленький набор ARGOS (~1 тыс.) дополнен выборкой из **Big Russian Dataset** (ZeroAgency, MIT; зеркало —
бакет `hf://buckets/AvaSiG/ru-big-russian-dataset-bucket`, 1,71 млн диалогов с оценками GPT-4.1 по 17 критериям).

```bash
D=/home/.argos-storage/datasets/big-russian; mkdir -p $D/data
for i in $(seq -w 0 18); do
  hf buckets cp hf://buckets/AvaSiG/ru-big-russian-dataset-bucket/data/train-000$i-of-00019.parquet $D/data/
done
python training/select_big_russian.py        # → big-russian/candidates.jsonl (8000) + candidates_stats.json
python training/build_argos_v2_dataset.py    # подхватывает candidates.jsonl автоматически
```

- `select_big_russian.py`: оценки ≥ 8 (overall, quality, correctness, coherence, relevance, error_free, safety),
  без `<think>`-рассуждений, ролевых игр, отказов и PII; только стандартный system prompt датасета (его можно
  заменить); без пересказа новостей, английских lmsys, длинных решений задач с формулами и кода;
  user ≤ 500, assistant ≤ 900 символов, ≥ 60 % кириллицы; резервуарная выборка по темам (≤ 8 % на тему,
  ≤ 20 % на источник). Читает parquet пакетами — память не растёт с размером датасета.
- Сборщик прогоняет кандидатов через **те же фильтры**, что и данные ARGOS (для них дополнительно
  засчитываются просьбы в повелительном наклонении: «составь», «перефразируй», «посоветуй»…), заменяет
  system prompt на канонический и берёт не больше `BIG_RU_MAX = 2500`, чтобы личность ARGOS не утонула.

## Вариант D — Google Диск (Colab)

Положить `train.jsonl`, `val.jsonl`, `test.jsonl` в `Мой диск/ARGOS REBOOT/argos-v2/` (тот же Google-аккаунт, что и в Colab)
(с X230: `rclone copy /home/.argos-storage/datasets/argos-v2 "gdrive:ARGOS REBOOT/argos-v2" --include "*.jsonl"`).
В ноутбуке `DATA_SOURCE = "auto"` сам подключит Диск; результат (GGUF, Modelfile) копируется в
`Мой диск/ARGOS REBOOT/argos-v2/release/` (`SAVE_TO_DRIVE = True`).

## Загрузка данных для обучения (делает владелец своим токеном)

**Вариант A — датасет на Hugging Face `AvaSiG/argos-v2-sft`:**
```bash
hf auth login                       # токен с правом write, вводится интерактивно
hf upload AvaSiG/argos-v2-sft /home/.argos-storage/datasets/argos-v2 . \
    --repo-type dataset --private --exclude "eval/*"
```
В ноутбуке: `DATA_SOURCE = "hf"` (или `"auto"`). Для приватного датасета токен берётся из Kaggle Secrets /
Colab Secrets (`HF_TOKEN`) или вводится через `getpass`.

**Вариант B — датасет на Kaggle:** kaggle.com → Datasets → New Dataset → загрузить `train.jsonl`, `val.jsonl`,
`test.jsonl` (можно и `stats.json`), название `argos-v2-sft`, Private. В ноутбуке: Add Input → этот датасет.
Ноутбук сам найдёт `train.jsonl` в `/kaggle/input/…`.

**Вариант C — Colab без HF:** загрузить три файла в папку `./argos-v2` в Colab, `DATA_SOURCE = "local"`.

## Обучение (Kaggle или Colab)

1. Kaggle: New Notebook → File → Import Notebook → `training/argos_v2_finetune.ipynb`.
   Settings: Accelerator **GPU T4 x1** (или P100), Internet **On**. Для HF: Add-ons → Secrets → `HF_TOKEN`.
   Colab: File → Upload notebook, Runtime → Change runtime type → **T4 GPU**.
2. В ячейке 2 проверить `DATA_SOURCE`, при желании `PUSH_TO_HF = True`.
3. Run All. Что происходит: установка Unsloth → данные → ответы базовой модели на пробы → LoRA r=16
   (все проекции, alpha 16) → SFTTrainer (lr 2e-4, cosine, warmup 5 %, эффективный batch 16, max_seq_len 1024,
   adamw_8bit) с `train_on_responses_only` (loss только на токенах ассистента, в т.ч. в многоходовых
   диалогах; ячейка 10 печатает, что именно обучается) → val/test loss → таблица «база vs argos-v2»
   на 15 пробах → ответы на отложенный test рядом с эталоном → LoRA + GGUF Q4_K_M → Modelfile.
4. Забрать `argos-v2-release/` (или `argos-v2-release.zip`): `argos-v2-Q4_K_M.gguf` (~1 ГБ), `Modelfile`,
   `probes_side_by_side.json`. Kaggle: Save Version → Output. Colab: скачивание начнётся само.
5. (Опционально) `PUSH_TO_HF = True` загрузит папку в `AvaSiG/argos-v2-gguf` (по умолчанию приватно);
   токен запрашивается во время выполнения и нигде не сохраняется.

Modelfile использует шаблон ChatML Qwen2.5 (`<|im_start|>` / `<|im_end|>`), `temperature 0.3`, `num_ctx 4096`,
stop-токены `<|im_start|>`, `<|im_end|>`, `<|endoftext|>` и канонический system prompt.

## Установка в Ollama на X230 и проверка

```bash
mkdir -p /home/.argos-storage/models/argos-v2 && cd /home/.argos-storage/models/argos-v2
# положить сюда argos-v2-Q4_K_M.gguf и Modelfile (или: hf download AvaSiG/argos-v2-gguf --local-dir .)
ollama create argos-v2 -f Modelfile
cd /root/argos-improve
/root/argos-recovery-venv/bin/python training/eval_local.py --models argos-local argos-v2
/root/argos-recovery-venv/bin/python training/eval_local.py --models argos-local argos-v2 --set test --limit 30
```
Отчёты пишутся в `/home/.argos-storage/datasets/argos-v2/eval/`. Переключать ARGOS на `argos-v2` стоит,
только если на пробах и test он не хуже `argos-local`: без выдуманных фактов, пустых ответов и иероглифов.

## eval_local.py

- `/api/chat`, `temperature 0`, `num_predict 96` (меняется флагом `--num-predict`), только stdlib;
- `--system canonical` (по умолчанию) отправляет канонический system prompt — так же, как будет в работе;
  `--system modelfile` полагается на SYSTEM из Modelfile;
- для проб автоматически ставит флаги OK / BAD / MISSING_EXPECTED / EMPTY / INSTRUCTION_ECHO / CJK_LEAK /
  UNASKED_CODE (это грубая эвристика на регулярках — ответы надо читать);
- базовый отчёт: `/home/.argos-storage/datasets/argos-v2/eval/baseline_probes.md`.
