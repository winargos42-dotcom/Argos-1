# Browser handoff and test recovery — 2026-09-18

## Observed failures

Deployment test collection failed because `src/connectivity/browser_conduit.py` contained only a placeholder. The existing 21 tests specify local handshake/session behavior. Separately, the real AWA caller constructs `BrowserConduit(core)` and calls `ask_external_ai(payload)`; an older unmerged patch lacked that contract and was not copied blindly.

The dependency/documentation tests also referenced missing quickstart copies and relied on the current directory. A focused run before repair produced one failure and two passes.

## Changes

- Restored local handshake formatting, per-session handshake state and thread-safe state updates.
- Accepted the AWA core argument and added an explicit unavailable result for external AI handoff: `ok=false`, `sent=false`, error `external_ai_transport_not_configured`, plus a prepared local message. No network, GUI automation, subprocess, external transport or credentials are used.
- Added regressions for the real constructor/caller and no-I/O behavior. Tests invoke AWA methods without starting its background services.
- Resolved deployment requirements/build files from the test location and checked the canonical repository `quickstart.md`, retaining the installation-command and safety-note assertions.
- Added a separate deployed-helper test process to `validate.yml`; root and deployment `src` packages are not mixed.
- Updated the project handoff with observed hosted results and the separate missing-inference-provider blocker.

## Verification and review

- `python -m pytest --noconftest tests/test_browser_conduit.py tests/test_requirements_runtime_deps.py -q -o addopts=''` from `argos_deploy`: **27 passed** (24 browser, 3 dependency/documentation).
- Root report/validation regression suite: **22 passed**.
- Deployment collection with `--rootdir=. -c ../pytest.ini tests --collect-only --ignore=tests/generated --import-mode=importlib -q -o addopts=''`: **888 tests collected, no collection errors**.
- Parent reviewed the delegated implementation, actual AWA caller, explicit unavailable return, locking, tests, and workflow source isolation. No external-send path was introduced. Full application execution and release coverage are not proven by these tests.
- Publication uses the existing repair branch for hosted Python 3.10 verification before applying to main. Hosted outcomes are available in Actions for this commit; local results alone are not described as hosted proof.

## Remaining blockers

Release CI still requires 30% coverage and currently measures 0% in root `src`; the gate was not lowered or suppressed. The complete deployed suite is not yet an isolated, passing runtime test suite. A read-only live Railway diagnostic reported 0 of 12 AI providers available; a running HTTP service is not evidence of working AI inference. The browser helper produces drafts only until a separately reviewed external transport is configured.

## Rollback

Revert this scoped repair commit. No production environment variable, volume, database, network transport or external-send setting was changed. Existing cloud data is preserved.
