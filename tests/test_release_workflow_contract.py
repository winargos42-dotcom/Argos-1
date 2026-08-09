from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_references_existing_tests():
    """Every explicit test path in release.yml must exist in the repository."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    referenced_tests = sorted(
        set(re.findall(r"tests/[A-Za-z0-9_./-]+\.py", workflow))
    )

    assert referenced_tests, "release.yml should declare at least one test path"

    missing = [path for path in referenced_tests if not (REPO_ROOT / path).is_file()]
    assert not missing, "release.yml references missing tests: " + ", ".join(missing)
