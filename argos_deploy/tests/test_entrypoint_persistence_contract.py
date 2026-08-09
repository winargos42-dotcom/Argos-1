from pathlib import Path


def test_entrypoint_prepares_persistent_state_before_gosu() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "docker-entrypoint.sh").read_text(encoding="utf-8")

    mkdir = 'mkdir -p "$CODEX_HOME" "$ARGOS_STATE_ROOT/data" "$ARGOS_STATE_ROOT/config"'
    chown = 'chown -R argos:argos "$ARGOS_STATE_ROOT"'
    drop = 'exec gosu argos "$@"'

    assert mkdir in text
    assert chown in text
    assert drop in text
    assert text.index(mkdir) < text.index(chown) < text.index(drop)
