# ARGOS CI validation repair — 2026-09-15

## Observed failures

The September 13 main runs 34789684306, 34789684308, 34789684316 and 34789684335 failed while compiling every Python file in the checkout, including historical backups, installed environments and vendored Python 2 sources. First-party maintenance scripts also contained five genuine syntax errors. Release CI could not build PyAudio because `portaudio.h` was missing; `|| true` concealed the dependency installation failure.

## Repair scope

- `scripts/check_python_syntax.py` lists tracked Python files using Git's NUL-delimited output. It excludes named backup/vendor trees and environment/cache directories, prints exclusion counts, and compiles UTF-8 source in memory. Missing tracked files, Git errors, empty selection and syntax errors fail the command. Application code is never executed.
- `validate_project.py` uses the same source selection and checks top-level import availability without importing application modules. Its final message reports static checks only.
- Four workflows use the shared check. Automatic encoding rewrites and auto-commits were removed from validation. Release CI installs `portaudio19-dev` and no longer ignores dependency or validator failures.
- Fixed nested string delimiters in `argos_deploy/fix_script.py`, misplaced global declarations in both release_final.py copies, an incomplete duplicate condition in debug_argos_client.py, and broken import statements in scripts/add_apk.py.
- The existing 30% application coverage threshold is unchanged. No production application, credentials, database, volume or deployment configuration was modified.

## Proof and review

- Before implementation, the new regression suite produced 11 failures and 2 passes.
- After implementation, `python -m pytest tests/test_status_report.py tests/test_python_validation.py -q -o addopts=''` passed all 22 cases.
- Before staging the two new Python files, the scanner checked 778 first-party files out of 7,336 tracked Python files: 778 passed, 0 failed. It reported 6,558 excluded files by category.
- `validate_project.py` passed static checks with 0 errors; missing optional imports are warnings, not a runtime success claim.
- New checker, validator and tests parse with Python 3.10 grammar. Workflow YAML parsing and `git diff --check` passed.
- Independent automated Python review: Code Tytor, expert level, security/bugs/tests checks, September 15; returned 0 issues and 0 improvements for the checker, validator and regression tests. Workflow logic and the five syntax edits were additionally reviewed locally. This is automated review, not a human approval or application runtime test.
- First hosted branch run 34961600861 on Python 3.10 caught one additional pre-existing error in `argos_deploy/argoss/validate_project.py`: a backslash inside an f-string expression. Extracting the line count outside the expression restores compatibility. Local Python 3.12 grammar-version parsing did not catch this restriction, so the repeated hosted Python 3.10 check is required.

## Remaining application test gap

`pytest --cov=src --cov-fail-under=30 -q --tb=short` runs the 22 root report/validation tests and fails the coverage gate: 0 of 18,434 application statements covered (0.00%). The tests themselves pass. `pytest.ini` selects only the root `tests` directory; surviving application tests live under `argos_deploy/tests`, whose conftest selects the deployed source tree. Root and deployed source trees differ. Restoring the application suite requires the correct dependencies and source selection, not lowering the threshold or blindly copying tests.

## Delivery and rollback

Published as commits `740e9e132a7c6f0a1a19e83ce0e8129d525553b7` and `1a8cbea4b820865335cb334f9328c640cc7bfec8`, then applied to main on September 16. Branch runs 34961699155, 34961699164 and 34961699175 succeeded. Main runs 35039085439, 35039085526 and 35039085371 succeeded, as did Docker 35039085358 and Android 35039085484. Release CI 35039085678 installed dependencies, passed syntax and 22 tests, then correctly failed the unchanged 30% coverage gate at 0.00%.

Owner authorization: apply fixes so ARGOS works. Publish the repair branch, verify the relevant hosted checks, then apply this scoped CI repair to main. This is not a release or a claim that the coverage gate passes. Revert this repair commit to roll back; it has no state migration. Follow up on application tests and upstream approval permissions separately.
