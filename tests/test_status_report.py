import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "status_report.py"


@pytest.fixture
def report():
    spec = importlib.util.spec_from_file_location("status_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cli_writes_json_report(tmp_path):
    output = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--out", str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["summary"]["error"] == 0
    assert data["sections"]


def test_missing_core_file_is_an_error(report, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPO_ROOT", tmp_path)
    checks = report.check_core_files().checks
    assert any(check.name == "main.py" and check.status == "error" for check in checks)


def test_static_checks_do_not_execute_application(report, tmp_path, monkeypatch):
    marker = tmp_path / "application-started"
    source = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    for relative in ("main.py", "src/core.py", "src/connectivity/telegram_bot.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    monkeypatch.setattr(report, "REPO_ROOT", tmp_path)
    checks = report.check_argos_runtime().checks
    assert not marker.exists()
    assert not any(check.status == "error" for check in checks)
    assert any(check.status == "skip" for check in checks)


@pytest.mark.parametrize("source", ["def broken(:\n", "return 1\n"])
def test_invalid_python_is_reported(report, tmp_path, monkeypatch, source):
    (tmp_path / "main.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(report, "REPO_ROOT", tmp_path)
    checks = report.check_argos_runtime().checks
    assert any(check.name == "main.py синтаксис" and check.status == "error" for check in checks)


def test_git_command_failures_are_not_reported_as_ok(report, monkeypatch):
    def failed_git(command):
        if command == ["git", "--version"]:
            return 0, "git version 2.43.0"
        return 128, "fatal: not a git repository"

    monkeypatch.setattr(report, "_run", failed_git)
    checks = report.check_git().checks
    assert all(check.status != "ok" for check in checks if check.name != "git")
    assert any(check.status == "error" for check in checks)


@pytest.mark.parametrize("flag", ["--json", "--md", None])
def test_critical_errors_still_write_report(report, tmp_path, monkeypatch, flag):
    section = report.Section("Test")
    section.add("Missing required file", "error", "Missing")
    monkeypatch.setattr(report, "collect_report", lambda: [section])
    output = tmp_path / "report"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), *([flag] if flag else []), "--out", str(output)])
    assert report.main() == 1
    assert output.is_file()
    if flag == "--json":
        assert json.loads(output.read_text(encoding="utf-8"))["summary"]["error"] == 1
    else:
        assert "Missing required file" in output.read_text(encoding="utf-8")
