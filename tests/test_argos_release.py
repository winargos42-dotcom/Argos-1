import importlib.util
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location("argos_release", Path(__file__).parents[1] / "scripts/argos_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()


@pytest.fixture
def project(tmp_path):
    repo, runtime = tmp_path / "repo", tmp_path / "runtime"
    repo.mkdir()
    runtime.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    source = repo / "argos_deploy/src/a.py"
    source.parent.mkdir(parents=True)
    source.write_text("old\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (runtime / "src").mkdir()
    (runtime / "src/a.py").write_text("old\n")
    source.write_text("new\n")
    (source.parent / "new.py").write_text("created\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "next")
    return repo, runtime, base, tmp_path / "plan.json"


def plan(project):
    repo, runtime, base, output = project
    release.plan(repo, runtime, base, output)
    return output


def test_apply_and_rollback_created_file(project):
    repo, runtime, _, _ = project
    output = plan(project)
    assert (runtime / "src/a.py").read_text() == "old\n"
    receipt = release.apply(output)
    assert (runtime / "src/a.py").read_text() == "new\n"
    assert (runtime / "src/new.py").read_text() == "created\n"
    release.rollback(receipt)
    assert (runtime / "src/a.py").read_text() == "old\n"
    assert not (runtime / "src/new.py").exists()


@pytest.mark.parametrize("drift", ["source", "runtime", "head"])
def test_apply_refuses_drift_before_any_write(project, drift):
    repo, runtime, _, _ = project
    output = plan(project)
    if drift == "source":
        (repo / "argos_deploy/src/new.py").write_text("drift")
    elif drift == "runtime":
        (runtime / "src/a.py").write_text("drift")
    else:
        git(repo, "commit", "--allow-empty", "-qm", "drift")
    with pytest.raises(release.ReleaseError):
        release.apply(output)
    assert not (runtime / "src/new.py").exists()


def test_rollback_refuses_drift_before_restoring(project):
    _, runtime, _, _ = project
    receipt = release.apply(plan(project))
    (runtime / "src/new.py").write_text("user edit")
    with pytest.raises(release.ReleaseError):
        release.rollback(receipt)
    assert (runtime / "src/a.py").read_text() == "new\n"


def test_untracked_secret_excluded(project):
    repo, _, _, _ = project
    (repo / "argos_deploy/.env").write_text("PRIVATE_TOKEN=value")
    content = plan(project).read_text()
    assert ".env" not in content and "PRIVATE_TOKEN" not in content


def test_committed_tests_are_not_packaged(project):
    repo, _, _, _ = project
    path = repo / "argos_deploy/tests/test_a.py"
    path.parent.mkdir()
    path.write_text("test fixture")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "tests")
    assert "tests/test_a.py" not in plan(project).read_text()


@pytest.mark.parametrize("kind", ["deleted", "symlink", "extension"])
def test_invalid_source_changes_refused(project, kind):
    repo, _, _, _ = project
    source = repo / "argos_deploy/src/a.py"
    if kind == "deleted":
        source.unlink()
    elif kind == "symlink":
        source.unlink()
        source.symlink_to("new.py")
    else:
        (source.parent / "private.env").write_text("PRIVATE")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "invalid")
    with pytest.raises(release.ReleaseError):
        plan(project)


def test_plan_cannot_write_into_runtime(project):
    repo, runtime, base, _ = project
    with pytest.raises(release.ReleaseError):
        release.plan(repo, runtime, base, runtime / "src/a.py")
    assert (runtime / "src/a.py").read_text() == "old\n"


def test_runtime_symlink_refused(project):
    _, runtime, _, _ = project
    target = runtime / "src/a.py"
    other = runtime / "outside.py"
    other.write_text("old\n")
    target.unlink()
    target.symlink_to(other)
    with pytest.raises(release.ReleaseError):
        plan(project)


@pytest.mark.parametrize("bad", ["argos_deploy/../outside.py", "argos_deploy/.env", "/tmp/file.py", "argos_deploy/src/a.txt"])
def test_invalid_paths_rejected(bad):
    with pytest.raises(release.ReleaseError):
        release.allowed_path(bad)


def test_apply_failure_restores_previous_files(project, monkeypatch):
    _, runtime, _, _ = project
    output = plan(project)
    original = release.atomic_write
    def fail_new(path, data, mode=0o644):
        if Path(path).name == "new.py":
            raise OSError("simulated failure")
        return original(path, data, mode)
    monkeypatch.setattr(release, "atomic_write", fail_new)
    with pytest.raises(OSError):
        release.apply(output)
    assert (runtime / "src/a.py").read_text() == "old\n"
    assert not (runtime / "src/new.py").exists()
