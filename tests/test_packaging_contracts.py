from configparser import RawConfigParser
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _buildozer_app_config():
    parser = RawConfigParser(interpolation=None)
    parser.read(REPO_ROOT / "buildozer.spec", encoding="utf-8")
    return parser["app"]


def test_android_recovery_toolchain_is_pinned():
    app = _buildozer_app_config()

    assert app["source.main"] == "main_kivy.py"
    assert app["p4a.branch"] == "v2024.01.21"
    assert app["android.ndk"] == "25b"
    assert app["android.archs"] == "arm64-v8a"
    assert "kivy==2.3.1" in app["requirements"]
    assert "pyjnius==1.6.1" in app["requirements"]
    assert "androidx.core:core:1.10.1" in app["android.gradle_dependencies"]


def test_android_packaging_files_exist():
    assert (REPO_ROOT / "main_kivy.py").is_file()
    assert (REPO_ROOT / "src" / "interface" / "kivy_local_ui.py").is_file()
    assert (REPO_ROOT / "assets" / "argos_icon_512.png").is_file()
    assert (REPO_ROOT / "res" / "xml" / "file_paths.xml").is_file()


def test_windows_workflow_matches_current_pyinstaller_layout():
    workflow = (REPO_ROOT / ".github" / "workflows" / "build_windows.yml").read_text(
        encoding="utf-8"
    )
    spec = (REPO_ROOT / "argos.spec").read_text(encoding="utf-8")

    assert "name='argos'" in spec
    assert r"dist\argos\argos.exe" in workflow
    assert r"installer\Output\ARGOS_Setup.exe" in workflow
