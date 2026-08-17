# ARGOS inference, health, and full validation implementation plan

Date: 2026-08-16
Design: `docs/superpowers/specs/2026-08-16-argos-health-inference-ci-design.md`

## Scope and isolation

Work on branch `codex/argos-health-inference-ci`, created from main
`919c880dfc0a6a4f99e41a8c5adb5332922271bf`.

Expected files:

- `argos_deploy/src/core.py`
- `argos_deploy/src/mempalace_bridge.py`
- `argos_deploy/src/health_details.py` (new)
- `argos_deploy/cloud_entry.py`
- focused tests in `argos_deploy/tests/`
- `.github/workflows/ci.yml`
- `.github/workflows/build_apk.yml`
- `.github/workflows/build_windows.yml`

Protected/out of scope: root duplicate `src/`, persisted data, secrets,
Railway variables, service restarts, unrelated workflows, and external sends.

## Tasks

### 1. Red: Ollama circuit and cloud fallback

Add behavioral tests proving:

- missing `ollama` opens the circuit after one spawn attempt;
- a second request does not probe/spawn again;
- remote hosts are never auto-started locally;
- configured custom cloud fallback is ordered before Ollama;
- health state never includes the API key.

Run the focused tests and record the expected failures.

### 2. Green: inference policy

Add circuit state and transitions to `ArgosCore`, extend OpenAI-compatible
configuration with `CloudFallback`, and update Auto provider ordering. Make the
smallest change that passes the focused tests.

### 3. Red/green: detailed health

Add tests for the response shape, explicit unavailable values, MemPalace count,
node registry count, system/GPU metrics, endpoint state, volume status and
secret redaction. Implement `src.health_details`, a MemPalace structured status
function, retain the orchestrator reference in `cloud_entry.py`, and expose
`/health/details`.

### 4. Regression checks

Run focused tests, then the deployed-runtime suite from `argos_deploy`. Check
Python syntax for touched files and validate workflow YAML.

### 5. Sequential CI

Add `workflow_call` to APK and Windows workflows. Update CI so tests call APK
and APK calls Windows only on main/manual release validation. Open a PR and
inspect its test-only checks.

### 6. Review and shipping

Perform change review, resolve blocking findings, merge only after required
checks are green, then observe the main full-validation run.

### 7. Android verification

Download the APK artifact, confirm at least one `adb devices` target, install
with `adb install -r`, launch the package, inspect UI hierarchy/screenshot and
filtered logcat. If no device/emulator is available, report that exact external
verification blocker without claiming success.
