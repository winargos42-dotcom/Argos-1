import importlib.util
from pathlib import Path
import shlex
from types import SimpleNamespace

import coverage
import pytest


@pytest.mark.parametrize("workflow", ["validate.yml", "ci.yml"])
def test_control_integration_workflow_installs_its_http_dependencies(workflow):
    source = (Path(__file__).parents[1] / ".github" / "workflows" / workflow).read_text()
    install = next(line.split("pip install", 1)[1] for line in source.splitlines() if "pip install" in line)
    assert {"aiohttp", "fastapi", "uvicorn"} <= set(shlex.split(install))


@pytest.fixture
def runner():
    path = Path(__file__).parents[1] / "scripts" / "test_deployed_app.py"
    spec = importlib.util.spec_from_file_location("deployed_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_coverage_tracks_deployed_application_not_root_or_selected_helpers(runner):
    command = runner.test_command(["tests/test_safe_arithmetic.py"], helpers=True)
    assert "--cov=" + str(runner.APP / "src") in command
    assert not any("omit" in arg or "include" in arg for arg in command)
    assert "--import-mode=importlib" in command


def test_unimported_namespace_and_duplicate_files_stay_in_denominator(runner, tmp_path, monkeypatch):
    app = tmp_path / "app"
    namespace = app / "src" / "namespace"
    namespace.mkdir(parents=True)
    files = [namespace / "unimported.py", app / "src" / "legacy (1).py"]
    for path in files:
        path.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(runner, "APP", app)
    data_file = tmp_path / ".coverage"

    runner.include_all_source(data_file)

    measured = coverage.Coverage(data_file=str(data_file))
    measured.load()
    assert set(measured.get_data().measured_files()) == {str(path) for path in files}
    assert all(measured.analysis2(str(path))[3] == [1] for path in files)


@pytest.mark.parametrize("failed_stage", ["tests", "broad", "gate", "source", None])
def test_runner_keeps_test_and_30_percent_gate_failures(runner, tmp_path, monkeypatch, failed_stage):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        fail = (failed_stage == "tests" and "pytest" in command or
                failed_stage == "gate" and "report" in command)
        return SimpleNamespace(returncode=int(bool(fail)))
    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "include_all_source", lambda path: None)
    monkeypatch.setattr(runner, "run_broad_tests", lambda output, env: ["test_bad.py"] if failed_stage == "broad" else [])
    hashes = iter([{"src/module.py": "before"}, {"src/module.py": "after" if failed_stage == "source" else "before"}])
    monkeypatch.setattr(runner, "source_hashes", lambda: next(hashes))

    assert runner.main(["--output", str(tmp_path)]) == (0 if failed_stage is None else 1)
    assert any("--fail-under=30" in cmd for cmd, _ in calls)
    assert any("json" in cmd for cmd, _ in calls)
    assert any("xml" in cmd for cmd, _ in calls)
    assert all(kwargs["cwd"] == runner.APP for _, kwargs in calls)


def test_offline_guard_rejects_external_operations_before_execution(tmp_path):
    import os
    import subprocess
    import sys

    scripts = Path(__file__).parents[1] / "scripts"
    env = dict(os.environ, PYTHONPATH=str(scripts), ARGOS_TEST_OUTPUT_ROOT=str(tmp_path))
    code = """
import subprocess,sys
from deployed_validation_guard import install
install()
for event,args in [
    ('socket.connect', (object(), ('203.0.113.1', 80))),
    ('socket.getaddrinfo', ('example.test', 80, 0, 0, 0)),
    ('open', ('/root/argos-test-write-prohibited', 'w', 577)),
    ('sqlite3.connect', ('file:/root/argos-test-write-prohibited.sqlite?mode=rwc',)),
]:
    try:
        sys.audit(event, *args)
    except OSError as exc:
        assert 'offline validation' in str(exc)
    else:
        raise AssertionError(event)
try:
    subprocess.run([sys.executable, '-c', 'print(123)'], check=True)
except OSError as exc:
    assert 'offline validation' in str(exc)
else:
    raise AssertionError('unapproved subprocess executed')
subprocess.run([sys.executable, '-m', 'unittest', '--help'], check=True, capture_output=True)
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
