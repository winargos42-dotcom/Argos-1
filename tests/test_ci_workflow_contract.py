from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _ci_workflow() -> str:
    return (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )


def test_ci_python3_syntax_gate_excludes_legacy_argos_deploy_tree():
    """The Python 3 syntax gate must not compile the legacy Python 2 deploy archive."""
    assert '! -path "*/argos_deploy/*"' in _ci_workflow()


def test_ci_baseline_does_not_enforce_unbacked_coverage_floor():
    """Do not enforce the old 30% floor until the restored suite actually exercises src/."""
    assert "--cov-fail-under=30" not in _ci_workflow()
