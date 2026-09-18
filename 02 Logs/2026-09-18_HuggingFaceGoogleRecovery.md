# Hugging Face and Google recovery — 2026-09-18

## Recovered memory and training material

The connected Hugging Face account is `AvaSiG`. Its current connector grants repository reads and Jobs access, not repository writes. No training job, endpoint, paid hardware or new account was created.

The public `AvaSiG/mempalace-db` dataset contains `mempalace_vec.sqlite3` at revision `8b4bb12ef3024cc53758c8d2859ec017f6033f58`. The downloaded file was opened read-only with trusted schema disabled. Its integrity check returned `ok`; the `drawers` table contains **92,918 rows**. The file is **354,754,560 bytes**, with SHA-256:

`a50e87af84dec885f1712788222a71fd7c1d49354a7e591ec9f8f325bfc3e888`

That checksum matches the source file page on Hugging Face. The original bytes and a verification manifest were saved for the owner in a ZIP archive (57,780,644 bytes), with ZIP CRC verification passing. No memory records or database bytes were committed to this repository. No existing live memory store was replaced. This is a verified historical backup, not proof that every lost local record has survived.

`AvaSiG/argos-train-notebooks` contains `argos_train_all_datasets.ipynb` at revision `83d3ddc7decfa807dc4d8a7c3a732618452f5ab1`. The saved file is valid notebook JSON, version 4, with eight cells and 11,267 bytes. It was not executed. The source notebook and the memory archive were made available to the owner.

Other surviving repositories checked: `AvaSiG/argos-v1`, `AvaSiG/argos-v1-gguf`, `AvaSiG/argos-qwen2.5-7b-v1`, `AvaSiG/argos-qwen2.5-7b-v2`, `AvaSiG/argos-chat-dataset`, `AvaSiG/argos-canonical`, and `AvaSiG/argos-quantum-train-v2`. Correct the historical shorthand `argos-gguf` to `argos-v1-gguf`. Stored weights and datasets do not establish running hosted inference or recovery of the latest lost local model.

## Hugging Face integration repairs

The deployment helper displayed `HUGGINGFACE_TEXT_MODEL` in status but selected `HUGGINGFACE_MODEL` or the embedding default for actual text requests. Its resolver now honors the text setting first. Both source copies normalize a fallback text-model URL and reject Space URLs before requesting inference. Embedding settings remain independent.

The user's saved `HUGGINGFACE_MODEL` points to the existing Space `AvaSiG/sentence-transformers-all-MiniLM-L6-v2`. A Space URL alone is not a text-generation model. No arbitrary replacement model or paid inference endpoint was configured.

The live Space UI shows **Build error**. Its requirements file contains literal backslash-n separators on one line. The Docker build passes that invalid line to pip. The matching local copy in `argos_deploy/hf-harrier/requirements.txt` now has five real dependency lines; names and version bounds are unchanged. Before repair, parsing raised `InvalidRequirement`; afterward all five requirements parse. `hf-Argos/requirements.txt` was already valid and was left unchanged. The Space also runs a Harrier feature-extraction application despite its older MiniLM repository name; this name is not proof of the actual loaded model.

Publishing the requirements correction to the separate Hugging Face Space remains pending an authenticated write path. The browser currently shows Log In and the connector is read-only for repositories. No Space rebuild or working embedding response was claimed.

## Gemini repairs

The deployment router imported `google.generativeai`, although deployment requirements install `google-genai`. Both routers now make equivalent REST requests using `x-goog-api-key`, keeping credentials out of the URL. The disable flag is checked before proxy access or key loading. Direct requests retain a ten-second timeout and the existing GCP proxy request retains eight seconds.

Both core adapters preserve a 30,000 ms SDK timeout. `client_args` is passed only when the SDK's public `HttpOptions.model_fields` supports it. Inspection of official `googleapis/python-genai` tag `v0.8.0` confirmed that this supported minimum lacks `client_args`; tests cover both old and current schemas. Startup no longer performs `models.list`, and unavailable-model errors can select the next configured candidate.

Automatic defaults use `gemini-2.5-flash` and `gemini-2.5-flash-lite`. Explicit `GEMINI_MODEL` and candidate settings are preserved and normalized, including the `models/` prefix. Google documents the 2.0 family as retired; historical explicit 2.0 settings can fall through on a 404. The recovered configuration also explicitly disables Gemini, so a configured key alone does not enable it.

Review identified a second credential exposure path: Requests can include a malformed header value in `InvalidHeader`. Transport failures now report only their exception type, suppressing the original traceback context. Regressions check the message, traceback and outer logger using fake keys.

Official references:

- https://ai.google.dev/gemini-api/docs/deprecations
- https://googleapis.github.io/python-genai/
- https://github.com/googleapis/python-genai/blob/v0.8.0/google/genai/types.py

## Google resources and live limits

Drive searches for ARGOS, SiGtRiP, backups, archive formats and Colab notebooks found project documents but no backup archive or notebook in the returned results. The `ARGOS REBOOT` folder contains the existing brief and ledger. No Drive file or sharing permission was modified.

Historical Cloud Build recipes reference project `argos-489214`, region `us-central1` and services `argos-mcp`/`argos-core`, but their `Dockerfile.mcp` and `Dockerfile.core` build inputs are absent from tracked files. The separate VPN recipe references `argos-vpn-eu` in `europe-west4-a` and starts the VPN API; it does not establish that a complete ARGOS core or LLM still runs there. No GCP deployment or billing change was performed.

The owner has explicitly authorized recovered credential use. Direct GigaChat, DeepSeek, Gemini and Hugging Face authentication probes timed out; no inference response or invalid-key verdict followed from those timeouts. The existing Railway service has four control settings staged with deployment suppressed, but its provider rejected the deployment attempt. No recovered AI key was added to the old public service. Its persistent volume is intact; the new cloud-access code is not yet running.

## Verification and handoff

- Gemini regressions: initial 36 failures / 2 passes, followed by four failing malformed-key cases and four failing minimum-SDK cases; repaired total **46 passed**.
- Hugging Face regressions: initial five failures / 15 passes; repaired total **22 passed**, including the additional Space guard cases.
- Tests extract the actual selected source definitions and use fake SDK/HTTP transports, with real network connections forbidden. They neither load recovered credentials nor start the full application.
- Validation includes both new test files in the isolated deployment helper process. Root and deployment test suites remain separate; the existing 30% release coverage requirement is unchanged.
- Combined local verification: **788 tracked first-party Python files** pass syntax checks; **258 selected tests** pass (22 root, 167 isolated helpers and 69 runtime-module tests), plus two subtests. This is not an all-application test result or a successful cloud rollout.
- Independent review found no blocking defect; its malformed-key and minimum-SDK concerns were both addressed before publication.

Next runtime steps: obtain an authenticated write path for the existing HF Space; deploy the reviewed secured ARGOS code when the current provider permits it; verify a real AI response; then plan a non-destructive import of the recovered memory after comparing the live store. Preserve the original SQLite backup. Do not execute the training notebook or overwrite a persistent store as an implicit part of recovery.
