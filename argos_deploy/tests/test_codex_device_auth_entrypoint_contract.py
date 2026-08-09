from pathlib import Path


def test_railway_uses_single_docker_entrypoint_and_optional_device_auth() -> None:
    root = Path(__file__).resolve().parents[1]
    entrypoint = (root / "docker-entrypoint.sh").read_text(encoding="utf-8")
    railway = (root / "railway.full.json").read_text(encoding="utf-8")

    assert '"startCommand": "python3 cloud_entry.py"' in railway
    assert "/usr/local/bin/argos-entrypoint python3 cloud_entry.py" not in railway
    assert 'ARGOS_CODEX_DEVICE_LOGIN' in entrypoint
    assert 'pty.spawn(["codex", "login", "--device-auth"])' in entrypoint
    assert 'exec gosu argos "$@"' in entrypoint
