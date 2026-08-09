from pathlib import Path


def test_railway_runs_persistent_entrypoint_and_device_auth_when_missing() -> None:
    root = Path(__file__).resolve().parents[1]
    entrypoint = (root / "docker-entrypoint.sh").read_text(encoding="utf-8")
    railway = (root / "railway.full.json").read_text(encoding="utf-8")

    assert (
        '"startCommand": "/usr/local/bin/argos-entrypoint python3 cloud_entry.py"'
        in railway
    )
    assert 'auth cache missing; starting device auth' in entrypoint
    assert 'pty.spawn(["codex", "login", "--device-auth"])' in entrypoint
    assert 'exec gosu argos "$@"' in entrypoint
