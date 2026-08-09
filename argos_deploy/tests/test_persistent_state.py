from pathlib import Path

import pytest

from src.persistent_state import PersistentStateError, prepare_persistent_state


def test_seeds_and_links_data_and_config(tmp_path: Path):
    app = tmp_path / "app"
    state = tmp_path / "persist"
    (app / "data").mkdir(parents=True)
    (app / "config").mkdir()
    (app / "data" / "memory.db").write_bytes(b"db")
    (app / "config" / "node_id").write_text("node-1", encoding="utf-8")

    mapping = prepare_persistent_state(app, state)

    assert (state / "data" / "memory.db").read_bytes() == b"db"
    assert (state / "config" / "node_id").read_text(encoding="utf-8") == "node-1"
    assert (app / "data").is_symlink()
    assert (app / "config").is_symlink()
    assert (app / "data").resolve() == (state / "data").resolve()
    assert (app / "config").resolve() == (state / "config").resolve()
    assert mapping == {
        "data": str((state / "data").resolve()),
        "config": str((state / "config").resolve()),
    }


def test_existing_persistent_state_wins(tmp_path: Path):
    app = tmp_path / "app"
    state = tmp_path / "persist"
    (app / "data").mkdir(parents=True)
    (app / "config").mkdir()
    (app / "data" / "memory.db").write_bytes(b"image")
    (app / "config" / "node_id").write_text("image-node", encoding="utf-8")
    (state / "data").mkdir(parents=True)
    (state / "config").mkdir()
    (state / "data" / "memory.db").write_bytes(b"persisted")
    (state / "config" / "node_id").write_text("persisted-node", encoding="utf-8")

    prepare_persistent_state(app, state)

    assert (app / "data" / "memory.db").read_bytes() == b"persisted"
    assert (app / "config" / "node_id").read_text(encoding="utf-8") == "persisted-node"


def test_invalid_state_root_raises(tmp_path: Path):
    app = tmp_path / "app"
    (app / "data").mkdir(parents=True)
    (app / "config").mkdir()
    state = tmp_path / "persist"
    state.write_text("not a directory", encoding="utf-8")

    with pytest.raises(PersistentStateError):
        prepare_persistent_state(app, state)
