import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_python_syntax.py"


@pytest.fixture
def repository(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def add_source(repository, relative, source):
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    subprocess.run(["git", "add", "--", relative], cwd=repository, check=True)
    return path


def run_checker(repository):
    return subprocess.run(
        [sys.executable, str(CHECKER), str(repository)],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_checker_limits_itself_to_tracked_first_party_sources(repository):
    marker = repository / "application-started"
    source = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    add_source(repository, "main.py", source)
    add_source(repository, "argos_deploy/src/file with space\nand newline.py", source)
    excluded = (
        ".venv/lib/dependency.py",
        "argos_deploy/.venv_arc/lib/dependency.py",
        "argos_deploy/node_modules/dependency.py",
        "src/__pycache__/cached.py",
        "argos_deploy/.pio/libdeps/dependency.py",
        "argos_deploy/backups/auto_20260411_161308/src/old.py",
        "argos_deploy/.argos_patch_backups/old.py",
        "argos_deploy/tmp/kolibrios/legacy.py",
        "argos_deploy/claude-code-templates/template.py",
        "argos_deploy/claude-code-config-main/hook.py",
        "argos_deploy/mempalace-develop/benchmark.py",
        "argos_deploy/openai-chatkit-advanced-samples/example.py",
        "argos_deploy/piper1-gpl-main/setup.py",
    )
    for relative in excluded:
        add_source(repository, relative, "print 'legacy vendor source'\n")
    (repository / "untracked.py").write_text("def broken(:\n", encoding="utf-8")

    result = run_checker(repository)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Tracked Python files: 15" in result.stdout
    assert "First-party Python files: 2" in result.stdout
    assert "Excluded Python files: 13" in result.stdout
    assert "argos_deploy/tmp/kolibrios" in result.stdout
    assert not marker.exists()
    assert not list(repository.rglob("*.pyc"))


@pytest.mark.parametrize(
    "relative, source",
    [
        ("main.py", "def broken(:\n"),
        ("argos_deploy/src/core.py", "return 1\n"),
        ("argos_deploy/tmp/custom_task.py", "break\n"),
        ("argos_deploy/data/patches/repair.py", "def broken(:\n"),
        ("src/venv_manager.py", "def broken(:\n"),
    ],
)
def test_checker_rejects_invalid_first_party_source(repository, relative, source):
    add_source(repository, relative, source)

    result = run_checker(repository)

    assert result.returncode == 1
    assert relative in result.stdout
    assert "failed" in result.stdout.lower()


def test_checker_rejects_missing_tracked_source(repository):
    path = add_source(repository, "missing.py", "value = 1\n")
    path.unlink()

    result = run_checker(repository)

    assert result.returncode == 1
    assert "missing.py" in result.stdout


def test_checker_rejects_empty_source_selection(repository):
    result = run_checker(repository)

    assert result.returncode == 1
    assert "No tracked first-party Python files" in result.stdout


def test_checker_reports_git_failure(tmp_path):
    result = run_checker(tmp_path)

    assert result.returncode == 1
    assert "Could not list tracked Python files" in result.stdout


@pytest.fixture
def validator_module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    spec = importlib.util.spec_from_file_location("project_validator", ROOT / "validate_project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_validation_never_executes_imported_code(repository, validator_module, monkeypatch):
    marker = repository / "import-executed"
    add_source(repository, "main.py", "import syntax_check_sentinel.child\n")
    add_source(
        repository,
        "syntax_check_sentinel/__init__.py",
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
    )
    add_source(repository, "syntax_check_sentinel/child.py", "raise RuntimeError('must not run')\n")
    monkeypatch.syspath_prepend(str(repository))
    validator = validator_module.ProjectValidator(repository)

    validator.check_python_syntax()
    validator.check_imports()

    assert not marker.exists()
    assert not validator.errors
    assert not validator.warnings


def test_project_validator_uses_shared_selection(repository, validator_module):
    add_source(repository, "main.py", "value = 1\n")
    add_source(repository, "argos_deploy/tmp/kolibrios/legacy.py", "print 'legacy source'\n")
    (repository / "untracked.py").write_text("def broken(:\n", encoding="utf-8")
    validator = validator_module.ProjectValidator(repository)

    validator.check_python_syntax()
    validator.check_imports()

    assert not validator.errors
    assert len(validator.success) == 1


def test_project_validator_preserves_syntax_failure(repository, validator_module):
    add_source(repository, "argos_deploy/src/core.py", "return 1\n")
    validator = validator_module.ProjectValidator(repository)

    validator.check_python_syntax()

    assert validator.errors
    assert not validator.generate_report()


def test_project_validator_reports_unavailable_absolute_import(repository, validator_module):
    add_source(repository, "main.py", "import argos_missing_dependency_for_validation\n")
    add_source(repository, "package/__init__.py", "from . import relative_module\n")
    add_source(repository, "package/relative_module.py", "value = 1\n")
    validator = validator_module.ProjectValidator(repository)

    validator.check_imports()

    assert len(validator.warnings) == 1
    assert "argos_missing_dependency_for_validation" in validator.warnings[0]
