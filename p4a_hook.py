"""
p4a_hook.py — Хук Python-for-Android.
Патчит pyjnius для Python 3, отключает несовместимые C-модули,
и добавляет FileProvider в AndroidManifest.xml.
"""
import os
import re
from pathlib import Path

# Ensure third-party Cython builds default to Python 3 semantics during recipe compilation.
os.environ.setdefault("CYTHON_DEFAULT_LANGUAGE_LEVEL", "3")


def fix_pyjnius(arch):
    """Фикс pyjnius для Python 3 (убирает использование 'long' как тип Python 2).

    Использует sentinel для защиты ``long long`` (валидный тип C в Cython-кастах
    вида ``<long long>``), чтобы не получить ``int int``, которое Cython
    отвергает с ошибкой "Declarator should be empty".
    """
    _SENTINEL = "\x00LONGLONG\x00"
    site_packages = arch.get_env_vars().get("PYTHONPATH", "")
    for sp in site_packages.split(":"):
        jnius_src = Path(sp) / "jnius" / "jnius_utils.pxi"
        if jnius_src.exists():
            content = jnius_src.read_text(errors="replace")
            guarded = content.replace("long long", _SENTINEL)
            patched = re.sub(r"\blong\b", "int", guarded)
            patched = patched.replace(_SENTINEL, "long long")
            if patched != content:
                jnius_src.write_text(patched)
                print("[p4a_hook] pyjnius: fixed long → int (safe)")


def fix_jni_typedef(arch):
    """Исправляет некорректный typedef jlong в jni.pxi (ctypedef int int jlong)."""
    site_packages = arch.get_env_vars().get("PYTHONPATH", "")
    for sp in site_packages.split(":"):
        jni_pxi = Path(sp) / "jnius" / "jni.pxi"
        if jni_pxi.exists():
            content = jni_pxi.read_text(errors="replace")
            patched = re.sub(r"ctypedef\s+int\s+int\s+jlong", "ctypedef long jlong", content)
            if patched != content:
                jni_pxi.write_text(patched)
                print("[p4a_hook] pyjnius: fixed jlong typedef in jni.pxi")


def disable_broken_modules(arch):
    """Отключает C-модули несовместимые с Android."""
    broken = ["grp", "_uuid", "_lzma"]
    site_packages = arch.get_env_vars().get("PYTHONPATH", "")
    for sp in site_packages.split(":"):
        for mod in broken:
            path = Path(sp) / f"{mod}.py"
            if not path.exists():
                path.write_text(
                    f'''# {mod} disabled on Android\nraise ImportError("{mod} not available on Android")\n'''
                )


def _disable_android_incompatible_modules(toolchain):
    incompatible = {"grp", "_uuid", "_lzma"}
    try:
        from pythonforandroid.recipes.python3 import Python3Recipe  # type: ignore[import]
        existing = set(getattr(Python3Recipe, "disabled_modules", []))
        Python3Recipe.disabled_modules = sorted(existing | incompatible)
    except Exception as exc:
        print(f"[p4a_hook] WARNING: could not set disabled_modules ({exc})")


def add_file_provider(build_dir):
    """Добавляет FileProvider в AndroidManifest.xml."""
    manifest_path = Path(build_dir) / "AndroidManifest.xml"
    if not manifest_path.exists():
        # Ищем в .buildozer
        for p in Path(".buildozer").rglob("AndroidManifest.xml"):
            manifest_path = p
            break

    if not manifest_path.exists():
        print("[p4a_hook] AndroidManifest.xml не найден — пропускаю FileProvider")
        return

    content = manifest_path.read_text(encoding="utf-8", errors="replace")

    if "FileProvider" in content:
        print("[p4a_hook] FileProvider уже в манифесте")
        return

    provider_xml = '''
    <provider
        android:name="androidx.core.content.FileProvider"
        android:authorities="${applicationId}.provider"
        android:exported="false"
        android:grantUriPermissions="true">
        <meta-data
            android:name="android.support.FILE_PROVIDER_PATHS"
            android:resource="@xml/file_paths" />
    </provider>
'''

    # Вставляем перед </application>
    if "</application>" in content:
        content = content.replace("</application>", provider_xml + "</application>", 1)
        manifest_path.write_text(content, encoding="utf-8")
        print("[p4a_hook] FileProvider добавлен в AndroidManifest.xml")
    else:
        print("[p4a_hook] </application> не найден — FileProvider не добавлен")


def add_file_paths_xml(dist_dir):
    """Создаёт res/xml/file_paths.xml в дистрибутиве (Gradle src/main/res)."""
    # Gradle Android project expects resources under src/main/res/
    for res_base in (Path(dist_dir) / "src" / "main" / "res", Path(dist_dir) / "res"):
        xml_dir = res_base / "xml"
        xml_dir.mkdir(parents=True, exist_ok=True)
        file_paths = xml_dir / "file_paths.xml"
        if not file_paths.exists():
            file_paths.write_text('''<?xml version="1.0" encoding="utf-8"?>
<paths xmlns:android="http://schemas.android.com/apk/res/android">
    <external-path name="external_files" path="." />
    <files-path name="internal_files" path="." />
    <cache-path name="cache_files" path="." />
    <external-cache-path name="external_cache" path="." />
    <external-files-path name="external_app_files" path="." />
</paths>
''', encoding="utf-8")
            print(f"[p4a_hook] file_paths.xml создан в {xml_dir}")


def source_dirs(arch):
    fix_pyjnius(arch)
    fix_jni_typedef(arch)
    disable_broken_modules(arch)


def _ensure_language_level(toolchain):
    """Add ``# cython: language_level=3`` to all .pyx/.pxd files missing it."""
    import glob as _glob
    directive = "# cython: language_level=3\n"
    storage = getattr(toolchain, "storage_dir", None) or ""
    search_roots = [
        storage,
        os.path.expanduser("~/.buildozer"),
        os.path.join(os.getcwd(), ".buildozer"),
        os.getcwd(),
    ]

    patched_count = 0
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for ext in ("*.pyx", "*.pxd"):
            for match in _glob.glob(os.path.join(root, "**", ext), recursive=True):
                try:
                    with open(match, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                    if "language_level" in text:
                        continue
                    with open(match, "w", encoding="utf-8") as fh:
                        fh.write(directive + text)
                    patched_count += 1
                except OSError:
                    pass

    if patched_count:
        print(f"[p4a_hook] Added language_level=3 to {patched_count} .pyx/.pxd file(s)")


def before_apk_build(toolchain):
    """Called by p4a before APK assembly – extra safety-net to patch pyjnius."""
    try:
        _ensure_language_level(toolchain)
        arch_obj = getattr(toolchain, 'archs', [None])[0]
        if arch_obj:
            fix_pyjnius(arch_obj)
            fix_jni_typedef(arch_obj)
            disable_broken_modules(arch_obj)
        _disable_android_incompatible_modules(toolchain)
    except Exception as exc:
        print(f"[p4a_hook] before_apk_build warning: {exc}")


def after_apk_build(toolchain):
    """Called by p4a after manifest generation but before Gradle assembly.

    Injects the FileProvider <provider> element inside <application> in the
    generated AndroidManifest.xml.  Using android.extra_manifest_xml in
    buildozer places XML at manifest root level, causing:
        error: unexpected element <provider> found in <manifest>
    This hook patches the manifest directly so <provider> is correctly nested
    under <application>.
    """
    # p4a renders the manifest to src/main/AndroidManifest.xml relative to
    # the dist directory (which is the current working directory for hooks).
    for candidate_dir in ("src/main", "."):
        if Path(candidate_dir, "AndroidManifest.xml").exists():
            add_file_provider(candidate_dir)
            # Create res/xml/file_paths.xml required by the FileProvider
            dist_dir = Path(candidate_dir).parent if candidate_dir != "." else "."
            add_file_paths_xml(dist_dir)
            return

    print("[p4a_hook] after_apk_build: AndroidManifest.xml not found in expected locations, skipping FileProvider injection")
