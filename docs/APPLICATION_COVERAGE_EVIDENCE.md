# Deployed application validation — 2026-09-20

The complete `argos_deploy/src/**/*.py` tree measures **30.578250178472278% statement coverage**: **15,420 covered / 50,428 statements**, across **338 Python files**. The release threshold remains **30%**. Unimported namespace modules, platform modules and duplicate filenames remain in the denominator; no coverage omission was added.

Validation completed with **1,424 unique application tests passed, 22 skipped and 2 subtests passed**. The separate repository utility suite passed **53 tests**. Skipped application checks remain skipped, not represented as validated behavior. This is offline application regression evidence, not a hardware, external-provider or production deployment certification.

## Reproduce in CI

From the repository root, after installing the isolated test dependencies in `.github/workflows/ci.yml`:

```sh
python -m pytest tests -q
python scripts/test_deployed_app.py
```

The runner reports JSON/XML coverage and source hashes in `artifacts/app-coverage/`, rejects test failures and source changes during execution, and enforces `coverage report --precision=2 --fail-under=30`. CI uploads these artifacts even on failure. The local evidence used Python 3.14.7; the workflow uses Python 3.10, which still requires its own CI run.

## Recorded local execution

```sh
/root/argos-recovery-venv/bin/python scripts/test_deployed_app.py --output /tmp/argos-app-coverage
/root/argos-recovery-venv/bin/python /tmp/argos-final-supplement.py
```

The base run passed 364 helper tests, 69 runtime tests, and all 77 separately isolated broader test files. Its coverage gate correctly failed below 30%. Six supplementary files then passed: AI router behavior, deployed status reports, Gemini compatibility, industrial protocol behavior, KNX write execution, and offline Telegram contracts. The repeated 46 Gemini cases are counted once in the unique total. The supplementary final coverage command exited **0**, with no test failures.

The supplement retained coverage only for unchanged source files. It purged the previous `ai_router.py` and `connectivity/industrial_protocols.py` measurements, then measured their current tests again after the confirmed rate-limit and KNX execution fixes. The full runner now discovers all supplementary files automatically; the temporary supplement is not required for fresh CI execution.

The other source files matched base commit `b2ff627b0f2fd6c2ce34b2f185ffb7e2a7fa44c4`. All 338 source hashes were checked against the final working tree. Recorded SHA-256 values:

| Artifact / source | SHA-256 |
| --- | --- |
| Complete local `source-sha256.json` manifest | `e53e4d72574c77ead053a3b3c54e2936b12a3f7e565536feeb36dbc43817bf19` |
| `argos_deploy/src/ai_router.py` | `6be534b0ca0f89dd1c09ccd6ecbdcc3ba9b8b3e495df82f3b1417cfe68026831` |
| `argos_deploy/src/connectivity/industrial_protocols.py` | `ce7c64ae753d643443fe35d5f47f95727b4bceddf7ee237c756fd90b1f1f7fdb` |

Detailed local reports remain outside Git in `/tmp/argos-app-coverage/`. No recovered memory records, credentials or private application logs are included here.
