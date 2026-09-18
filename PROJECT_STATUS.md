# PROJECT STATUS — ARGOS Universal OS

Checkpoint: 2026-09-18. Repository: `winargos42-dotcom/Argos-1`.
The browser handoff repair is applied as `89c3267291bcc4c5dba5ea80a733e11fa816b4c2` on `main`. This checkpoint adds cloud access protection, accurate GigaChat configuration detection, and runtime test isolation.

## Current goal

Restore reliable ARGOS checks, retain surviving cloud state, and establish a tested application path after the loss of local hardware. Successful packaging and static checks are recorded separately from application execution.

## Applied repairs and evidence

| Area | Result | Evidence |
|---|---|---|
| Daily Status Report | Fixed missing root script; reports survive failed checks | Main run [34789684325](https://github.com/winargos42-dotcom/Argos-1/actions/runs/34789684325), subsequent scheduled runs 34864635100 and 34980438453 succeeded |
| Python syntax | 780 tracked first-party files pass on hosted Python 3.10 | Main run [35039085526](https://github.com/winargos42-dotcom/Argos-1/actions/runs/35039085526) succeeded |
| UTF-8 validation | Read-only check; no automatic source rewriting or commits | Main run [35039085371](https://github.com/winargos42-dotcom/Argos-1/actions/runs/35039085371) succeeded |
| Static project checks and root tests | 22 tests pass; project validation no longer imports or starts application code | Main run [35039085439](https://github.com/winargos42-dotcom/Argos-1/actions/runs/35039085439) succeeded |
| Release dependencies | PortAudio headers installed; dependency installation succeeds and failures are no longer ignored | Run [35039085678](https://github.com/winargos42-dotcom/Argos-1/actions/runs/35039085678) reached tests, then failed coverage: 22 passed, 0% versus required 30% |
| Docker | Build/publish succeeded after the second repair | Main run [35039085358](https://github.com/winargos42-dotcom/Argos-1/actions/runs/35039085358) |
| Android artifact | September 16 build succeeded; device launch unverified | Run [35039085484](https://github.com/winargos42-dotcom/Argos-1/actions/runs/35039085484); earlier verified artifact `argos-apk-debug-63` remains in run 34789684294 |
| Railway runtime | `/health` returned HTTP 200, `ok=true`, `ready=true`, no startup error | `argos-full-production.up.railway.app`, probe 2026-09-16 00:16 UTC; uptime 3,167,406 seconds |

The Railway health response confirms initialized service availability. Read-only provider diagnostics on September 16 and 18 displayed zero configured/available providers, with AI mode Auto. This report checks environment names and a local port, not authenticated inference; its GigaChat check also missed the client-ID/client-secret configuration accepted by the core. Runtime logs repeatedly report absent Ollama and connection refusal on localhost:11434. No cloud AI credential variable names were present in the eight inspected ARGOS services. These findings do not establish that historical credentials are lost. Existing persistent volumes and deployment configuration were preserved.

## Recovered configuration and cloud access

The user clarified on September 18 that working credentials were in the prior history. Personal Context located the historical `.env.bak` in the upstream repository; its blob matches the backup in this fork. The file contains populated AI-provider settings, including GigaChat's client-ID/client-secret pair. Values were not printed or added to repair commits. Their present validity has **not** been verified: automatic approval review rejected the attempted GigaChat and DeepSeek authenticated checks and requires explicit consent to use recovered credentials with those providers. No recovered key was applied to Railway.

This repair adds a cloud-only bearer guard using `ARGOS_MCP_API_KEY`. Public status reads at `/` and `/health` remain available; protected requests require a valid bearer token, with missing configuration failing closed. CORS preflight can complete without dispatching a protected handler. Both cloud and main dotenv loaders preserve deployed environment values. Local MCP behavior is unchanged. The repository repair is prepared for deployment; the running Railway service remains on its previously verified deployment until the cloud rollout and credential setup are performed.

GigaChat diagnostics now recognize access tokens and the complete client credential pair as well as the legacy API-key variable. The report explicitly describes detected configuration and does not claim that an API request succeeded.

## Technical decisions

- `scripts/check_python_syntax.py` checks Git-tracked ARGOS source, with explicit counts for excluded dependencies, environments and backup trees. It does not execute code or write bytecode. A missing tracked file, failed Git command, empty source set or syntax error fails validation.
- `validate_project.py` uses the same selection and checks import availability without importing application modules. Optional dependency warnings are not presented as runtime success.
- Six damaged maintenance files were repaired, including the Python 3.10-incompatible f-string in `argos_deploy/argoss/validate_project.py`. A hosted 3.10 run caught that issue after local 3.12 checks; grammar-version parsing alone was insufficient.
- Release CI retains `--cov=src --cov-fail-under=30`. Neither the threshold nor failure reporting was weakened.
- External sending remains governed by `security/EXTERNAL_COMMUNICATION_POLICY.md`. No email, support or Telegram outreach was sent during this work.

## Application test recovery in progress

Root `pytest.ini` selects root `tests`, which currently contains only the report and validation tests. They pass, but cover 0 of 18,434 application statements. The local release command therefore fails its existing 30% coverage gate.

Application tests survive under `argos_deploy/tests`. After restoring the browser helper, local collection with the generated placeholder directory explicitly excluded succeeds: **888 tests collected, zero collection errors**. This is collection evidence, not execution of all 888 tests. Root and deployed source trees differ; combining their tests into one Python process can load the wrong `src` package.

`argos_deploy/src/connectivity/browser_conduit.py` now provides thread-safe handshake/session formatting and supports the actual AWA constructor and `ask_external_ai` call. Without a transport, it returns `ok=false`, `sent=false`, `external_ai_transport_not_configured` and a prepared draft. It performs no external send and does not fabricate an AI response. All 24 focused browser tests pass, including the AWA caller and blocked-I/O regression.

Three dependency/documentation tests now resolve the real deployment files and canonical repository quickstart independently of the working directory. They pass. `validate.yml` runs these 27 deployment-helper tests in a separate process with no application conftest startup, in addition to the 22 root tests. The prior recovery branch's 13.5% coverage floor was not adopted; the 30% gate remains unchanged.

The next isolated test repair removes eager core loading from the deployment autouse fixture. It patches the lazy loader and any already cached class, with four regressions proving deferred loading and restoration of patched methods. Sixty-nine existing file-operation, self-healing, pricing and tool-calling tests pass with the normal deployment conftest. Validation now includes these modules and the cloud/configuration regressions in explicit deployment processes. This is a bounded runtime suite, not proof that every collected application test passes. Main release run 35334279832 still measured 0% against the unchanged 30% gate.

## Other known gaps

- Upstream run [31480145521](https://github.com/poilopr57-a11y/Argos/actions/runs/31480145521) expired awaiting owner approval and ran zero jobs. The connected account has read-only access there; fork code changes cannot retroactively approve it.
- Android launch on a phone/emulator and the current Windows installer remain unverified. The Android UI is a minimal shell and contains static status text.
- Version metadata and historical repository links still differ across README, package metadata, Buildozer and the mobile UI. Resolve them before a tagged release.
- Public ARGOS model/dataset copies remain on Hugging Face; recovery of the latest lost local model and MemPalace state is not established.
- Markdown inventory including this checkpoint: 380 files, with 47 project documents/logs and 333 vendored npm documents. Project instructions, recovery notes and release guidance were reviewed for this repair; dependency manuals were indexed, not audited.
- The two supplied ChatGPT share links could not be fetched by the browsing service (`DisabledError`); they were not treated as read history.

## Next steps

1. Restore application tests with separate root/deployment processes and explicit source paths; resolve real module contract failures without enabling external messages.
2. Run the application suite, report failures and measured coverage, and meet the unchanged release threshold before claiming release readiness.
3. Install and launch the exact built APK, then validate Windows packaging. Retain the existing pinned Android toolchain until a failing result justifies changing it.
4. Reconcile version/repository metadata only after the tested runtime path is clear. Create a release only after its required checks pass.

## Handoff and rollback

Detailed repair records: `02 Logs/2026-09-13_StatusReportRepair.md`, `02 Logs/2026-09-15_CIValidationRepair.md`, `02 Logs/2026-09-18_BrowserHandoffRepair.md` and `02 Logs/2026-09-18_CloudAccessRecovery.md`. Revert the corresponding repair commit to roll back. No database migration or persistent-data rewrite was performed. The previous packaging handoff remains in Git history at `4345b417599905411326a8e9d7df9df7e48c1034:PROJECT_STATUS.md`.
