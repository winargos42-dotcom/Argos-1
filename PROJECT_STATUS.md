# PROJECT STATUS — ARGOS Universal OS

Last verified against repository: `winargos42-dotcom/Argos-1`  
Branch: `main`  
Repository HEAD at handoff: `3cb6d05dcbe2773a3b6bbf74816d574a5f47a096`  
HEAD date: 2026-08-02

## 1. Current goals

The current development stage is focused on making ARGOS distributable again after a long packaging-fix cycle, with priority on:

1. Stabilizing the Android APK build and launch path.
2. Stabilizing the Windows executable + Inno Setup installer build.
3. Restoring reliable CI/release validation so a green build is evidence-based rather than inferred from commit messages.
4. Reconciling release/version metadata before the next tagged release.

The most recent commit history is dominated by Android/python-for-Android/SDL2 fixes and Windows packaging fixes rather than new core features. Continue from packaging and validation first before expanding functionality.

## 2. Completed / implemented in this stage

### Android entry path

- `main.py` now detects Android through `ANDROID_ARGUMENT` / `ANDROID_PRIVATE` and delegates to `main_kivy.py`.
- `main_kivy.py` launches `src.interface.kivy_local_ui.ArgosLocalApp`, with fallback/error Kivy screens instead of terminating silently.
- Missing `__init__.py` files were added across key `src/` package directories so Android imports can resolve as normal Python packages.
- A minimal mobile Kivy UI was added in `src/interface/kivy_local_ui.py`.

### Android packaging compatibility

- `buildozer.spec` currently pins:
  - Python-for-Android: `v2024.01.21`
  - Kivy: `2.3.1`
  - pyjnius: `1.6.1`
  - Android API: `33`
  - min API: `24`
  - NDK: `25b`
  - architecture: `arm64-v8a`
- `p4a_hook.py` contains Android build-time compatibility patches for pyjnius/Cython and disables Android-incompatible Python extension modules.
- FileProvider injection was moved to `before_apk_build` so it happens before Gradle packaging.
- `res/xml/file_paths.xml` is present and the hook also ensures the Gradle resource path is created when required.
- `androidx.core:core:1.10.1` is explicitly included because the manifest uses AndroidX `FileProvider`.
- `argos_deploy/` is excluded from APK sources to avoid unrelated Python 2 / syntax-invalid deployment material entering Android compile/package stages.
- The APK workflow now deletes stale python-for-Android / SDL2 bootstrap cache content before build so an older cached SDL2 bootstrap cannot override the pinned toolchain.

### Windows packaging

- Windows CI builds through `argos.spec` and then `installer/argos_setup.iss`.
- Inno Setup source/output paths were corrected for the fact that the `.iss` file lives under `installer/`.
- Artifact upload paths were corrected to the current PyInstaller `dist/argos/argos.exe` layout.
- Custom Windows manifest/icon use was removed from `argos.spec` after resource update failures; the current spec builds without those resource parameters.
- Build CI creates missing `config/`, `assets/`, and placeholder `.env` paths before PyInstaller analysis because the spec includes them as data.

## 3. Key technical decisions now in force

### Android toolchain

Use the pinned `python-for-android` tag `v2024.01.21` rather than `stable` or `develop`. The current configuration is intended to combine a newer SDL2 with an otherwise predictable p4a release.

Do not rely on restore-key cache identity alone for p4a/SDL2 correctness. The workflow explicitly removes stale p4a source and SDL2 bootstrap output before rebuilding.

FileProvider must be injected before Gradle executes. `after_apk_build` is only a safety net and is too late to fix missing resources for the current APK build.

### Windows packaging

Treat the PyInstaller output as an `onedir` layout named `argos`, with the executable at `dist/argos/argos.exe`. Inno Setup paths are relative to `installer/argos_setup.iss`, not repository root.

Do not restore the removed custom manifest/icon settings in `argos.spec` without first reproducing and fixing the `UpdateResourceW` failure that caused their removal.

### Validation policy

A commit message that says a packaging failure was fixed is not proof of a successful APK or installer. The handoff should only consider Android/Windows packaging validated after a workflow run produces the expected artifact and, for Android, the APK is launched on a real device/emulator.

## 4. Main files changed during the current packaging-fix sequence

The 19 commits between `f1010e7b6100fe6607d1354dceeb7714b3db5eeb` and current HEAD changed these development-critical files:

- `.github/workflows/build_apk.yml` — Android build orchestration and stale cache cleanup.
- `.github/workflows/build_windows.yml` — Windows dependency/build/artifact paths.
- `buildozer.spec` — Android requirements, p4a tag, NDK, AndroidX and source exclusions.
- `p4a_hook.py` — pyjnius/Cython fixes, disabled modules, FileProvider/resource injection.
- `main.py` — Android delegation to Kivy entry point.
- `src/interface/kivy_local_ui.py` — minimal Android UI.
- `src/__init__.py` and package `__init__.py` files — package import correctness.
- `res/xml/file_paths.xml` — Android FileProvider paths resource.
- `argos.spec` — current Windows PyInstaller layout.
- `installer/argos_setup.iss` — corrected Windows installer source/output paths.
- `assets/argos_icon_512.png` — APK icon required by `buildozer.spec`.
- `debug_argos_client.py`, `argos_full_auto_patch.py`, `fix_vector.py` and deployment copies — syntax/encoding cleanup needed to stop packaging-time parsing failures.

## 5. Testing and verification status

### What is configured

`.github/workflows/ci.yml` currently performs:

1. Python 3.10 setup.
2. Dependency installation (`requirements.txt` failure is currently tolerated with `|| true`).
3. Encoding repair via `fix_encoding.py`.
4. Repository-wide Python syntax compilation.
5. `validate_project.py`, but its failure is currently tolerated with `|| true`.
6. `pytest --cov=src --cov-fail-under=30` as the blocking test step.

The release workflow additionally tries to execute four named tests before creating a ZIP release.

### What was actually verified during this handoff

- The repository HEAD, recent commit history, current file contents and the 19-commit packaging diff were inspected directly through GitHub.
- The current `build_apk.yml`, `build_windows.yml`, `buildozer.spec`, `p4a_hook.py`, `main.py`, `main_kivy.py`, `src/interface/kivy_local_ui.py`, `pyproject.toml`, `health_check.py`, `ci.yml`, `release.yml`, and `argos.spec` were checked against the handoff notes.
- GitHub returned no combined commit statuses for current HEAD through the available connector, so this document does **not** claim that HEAD has a green CI run.
- A local clone/test execution was not possible in the current handoff environment because outbound DNS/network access to GitHub was unavailable.

### Blocking validation gap

The release workflow references at least these test files, and direct repository fetches for them at HEAD returned `Not Found`:

- `tests/test_industrial_protocols.py`
- `tests/test_consciousness_module.py`

Therefore the current release validation must be treated as unverified/broken until the referenced test suite is restored or the workflow is corrected to match the real tests in the repository.

`health_check.py` also expects a `tests/` directory, while its own header still identifies itself as an older ARGOS version. Do not use it as release proof until its expectations and version metadata are reconciled with the current tree.

## 6. Known problems / inconsistencies

### Version drift

Version strings are inconsistent at current HEAD:

- `README.md`: `2.1.4`
- `pyproject.toml`: `2.1.4`
- `buildozer.spec`: `2.1.3`
- `main.py` header: `2.1.3`
- `src/interface/kivy_local_ui.py`: displays `2.1.3`
- `health_check.py` header: `v1.3`

Reconcile these before producing the next official APK/installer/release.

### Repository/link drift

- README badges and several README links still point at an upstream/other repository rather than this fork.
- `pyproject.toml` project URLs also point at a different historical repository path.

Until this is corrected, badges and release/documentation links may describe another repository rather than the code being handed off here.

### Android UI is currently a minimal shell

The Kivy mobile UI contains static status/log/topology text and environment-specific placeholders rather than live subsystem state. Treat it as a launch/packaging UI, not as proof that the full ARGOS core, MCP, P2P or hardware integrations are active on Android.

### CI has non-blocking checks that can hide faults

- `pip install -r requirements.txt --quiet || true`
- `python3 validate_project.py || true`
- release `python health_check.py || true`

These commands can fail without failing their workflow step. A future validation pass should decide which failures are acceptable and remove `|| true` from anything intended to be a release gate.

## 7. Approaches already tried and rejected / superseded

Do not repeat these without new evidence:

1. **python-for-Android `stable`** — brought in old SDL2 2.0.4 and was associated with the Android `Build.VERSION.SDK_INT` field-signature crash.
2. **python-for-Android `develop`** — tried as a way around the SDL2 issue, then abandoned because the newer recipe set introduced a `libthorvg` build/index failure in CI.
3. **FileProvider injection only in `after_apk_build`** — too late because Gradle had already resolved the manifest/resources.
4. **Creating `file_paths.xml` under the wrong distribution/resource root** — Gradle could not resolve `@xml/file_paths`; current logic targets `src/main/res/xml` and keeps a fallback resource path.
5. **NDK 23b with current p4a** — reverted because current p4a requires NDK >= 25; configuration is back on 25b.
6. **Custom PyInstaller Windows manifest** — caused `UpdateResourceW` / WinError 87 and was removed.
7. **Custom minimal Windows ICO in the PyInstaller spec** — also implicated in `UpdateResourceW` / WinError 87 and was removed from the spec.
8. **Excluding only selected `argos_deploy/tmp`/backup paths from APK packaging** — insufficient because more Python 2 / syntax-invalid deployment material remained; the entire `argos_deploy/` directory is now excluded from APK source collection.
9. **Trusting cached p4a/SDL2 bootstrap after changing p4a tag** — restore-key prefix matching could still resurrect old SDL2 output; explicit cleanup is now part of the build workflow.

## 8. Next development steps — execute in this order

1. **Restore a trustworthy test baseline.** Inventory the real `tests/` tree. Restore the release-referenced tests if they are intended to exist, or update `release.yml`/CI to run the actual test suite. Run the blocking CI test command and record the result.
2. **Run Android CI from current HEAD.** Confirm the build uses p4a `v2024.01.21` and SDL2 2.28.5, produces an APK artifact, and does not reuse the old SDL2 bootstrap.
3. **Install and launch that exact APK artifact.** Verify startup past AndroidX FileProvider initialization and the previous `SDK_INT` crash. Record device/API version and launch result in the next status update.
4. **Run Windows build CI.** Confirm both `dist/argos/argos.exe` and `installer/Output/ARGOS_Setup.exe` are actually produced and uploaded. Install/run the setup artifact on a clean Windows environment if available.
5. **Reconcile version metadata.** Define one canonical version source and update README, `pyproject.toml`, Buildozer metadata, runtime/UI display strings and health-check metadata from it.
6. **Remove environment-specific static data from the Android UI.** Replace static topology/status/log claims with live probes/configuration or clearly labelled unavailable states.
7. **Fix repository metadata.** Point README badges/links and `pyproject.toml` URLs to the intended canonical repository/upstream.
8. **Tighten release gates.** Remove `|| true` from validation steps that are supposed to block a release; keep optional dependency checks explicitly non-blocking instead of suppressing whole validation commands.
9. **Only after Android + Windows + tests are green, create the next tag/release.** Update this file with the exact successful workflow/artifact references and remove superseded failure notes.

## 9. Handoff rule

Before starting new feature work, first establish one reproducible green path for CI, Android APK and Windows packaging. The current code contains the intended fixes, but the repository state inspected here does not provide enough verified run evidence to claim those artifacts are fully validated.

Do not add secrets, tokens, private endpoints, passwords or machine-specific credentials to this document.
