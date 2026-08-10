from pathlib import Path


def test_entrypoint_exports_safe_external_action_defaults() -> None:
    root = Path(__file__).resolve().parents[1]
    entrypoint = (root / "docker-entrypoint.sh").read_text(encoding="utf-8")

    assert 'EXTERNAL_SEND_ENABLED="${EXTERNAL_SEND_ENABLED:-false}"' in entrypoint
    assert 'EXTERNAL_DRAFT_ONLY="${EXTERNAL_DRAFT_ONLY:-true}"' in entrypoint
    assert 'EXTERNAL_REQUIRE_OWNER_APPROVAL="${EXTERNAL_REQUIRE_OWNER_APPROVAL:-true}"' in entrypoint
    assert "export EXTERNAL_SEND_ENABLED EXTERNAL_DRAFT_ONLY EXTERNAL_REQUIRE_OWNER_APPROVAL" in entrypoint
