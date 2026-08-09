from configparser import RawConfigParser
from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]


def _canonical_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_buildozer_version_matches_pyproject():
    parser = RawConfigParser(interpolation=None)
    parser.read(REPO_ROOT / "buildozer.spec", encoding="utf-8")

    assert parser["app"]["version"] == _canonical_version()


def test_runtime_and_android_ui_display_canonical_version():
    version = _canonical_version()
    main_text = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    ui_text = (REPO_ROOT / "src" / "interface" / "kivy_local_ui.py").read_text(
        encoding="utf-8"
    )

    assert f"ArgosUniversal OS v{version}" in main_text
    assert f"ARGOS Universal OS v{version}" in ui_text
