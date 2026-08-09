from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ci_python3_syntax_gate_excludes_legacy_argos_deploy_tree():
    """The Python 3 syntax gate must not compile the legacy Python 2 deploy archive."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert '! -path "*/argos_deploy/*"' in workflow
