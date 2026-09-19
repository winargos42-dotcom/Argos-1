import hashlib
import sqlite3

import pytest

from src import mempalace_bridge as bridge


@pytest.fixture
def palace(tmp_path, monkeypatch):
    path = tmp_path / "recovered.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE drawers(id TEXT, document TEXT, wing TEXT, room TEXT)")
        db.executemany("INSERT INTO drawers VALUES(?,?,?,?)", [
            ("one", "Аргос восстанавливает память", "technical", "recovery"),
            ("two", "Аргос готовит чай", "personal", "home"),
            ("three", "Unrelated document", "technical", "other"),
        ])
    monkeypatch.setenv("ARGOS_MEMPALACE_SQLITE_PATH", str(path))
    monkeypatch.setattr(bridge, "_MEMPALACE_ENABLED", True)
    def forbidden():
        pytest.fail("SQLite retrieval must not initialize ChromaDB")
    monkeypatch.setattr(bridge, "_ensure_init", forbidden)
    return path


def test_unicode_lexical_ranking_and_wing_filter(palace):
    hits = bridge.search_memory("АРГОС память", wing="technical")
    assert len(hits) == 1
    assert hits[0]["text"] == "Аргос восстанавливает память"
    assert hits[0]["score_kind"] == "lexical"
    assert hits[0]["score"] == 1.0


@pytest.mark.parametrize("query", ["неизвестно", "", "%_", "' OR 1=1 --"])
def test_no_match_returns_no_memories(palace, query):
    assert bridge.search_memory(query) == []


def test_context_is_bounded_and_does_not_include_unrelated_memory(palace):
    context = bridge.get_memory_context("память", wing="technical")
    assert "Аргос восстанавливает память" in context
    assert "готовит чай" not in context
    assert "lexical" in context
    assert len(context) <= 2400


def test_retrieval_and_store_leave_backup_unchanged(palace):
    before = hashlib.sha256(palace.read_bytes()).hexdigest()
    bridge.search_memory("Аргос")
    assert bridge.store_memory("new memory") is False
    assert "3" in bridge.status()
    assert hashlib.sha256(palace.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in palace.parent.iterdir()) == [palace.name]


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_database_fails_closed(tmp_path, monkeypatch, corrupt):
    path = tmp_path / "missing.sqlite3"
    if corrupt:
        path.write_bytes(b"not a database")
    monkeypatch.setenv("ARGOS_MEMPALACE_SQLITE_PATH", str(path))
    def forbidden():
        pytest.fail("Unavailable SQLite must not fall back to ChromaDB")
    monkeypatch.setattr(bridge, "_ensure_init", forbidden)
    assert bridge.search_memory("memory") == []
    assert "недоступ" in bridge.status()
    assert path.exists() == corrupt


def test_limits_and_disabled_mode(palace, monkeypatch):
    with sqlite3.connect(palace) as db:
        db.executemany("INSERT INTO drawers VALUES(?,?,?,?)", [
            (str(i), "Аргос " + "x" * 10000, "technical", "room") for i in range(30)
        ])
    hits = bridge.search_memory("Аргос", top_k=1000)
    assert len(hits) == 10
    assert all(len(hit["text"]) <= 4096 for hit in hits)
    assert bridge.search_memory("Аргос", top_k=0) == []
    monkeypatch.setattr(bridge, "_MEMPALACE_ENABLED", False)
    assert bridge.search_memory("Аргос") == []


def test_search_budget_interrupts_scan(palace, monkeypatch):
    with sqlite3.connect(palace) as db:
        db.executemany("INSERT INTO drawers VALUES(?,?,?,?)", [
            (str(i), "Аргос память", "technical", "room") for i in range(500)
        ])
    ticks = iter([0.0, 3.0])
    monkeypatch.setattr(bridge.time, "monotonic", lambda: next(ticks, 3.0))
    assert bridge.search_memory("Аргос") == []


def test_unconfigured_sqlite_keeps_chroma_query_contract(monkeypatch):
    monkeypatch.delenv("ARGOS_MEMPALACE_SQLITE_PATH", raising=False)
    monkeypatch.setattr(bridge, "_ensure_init", lambda: True)

    class Collection:
        def count(self):
            return 1

        def query(self, **kwargs):
            assert kwargs["query_texts"] == ["memory"]
            assert kwargs["where"] == {"wing": "technical"}
            return {"documents": [["existing memory"]],
                    "metadatas": [[{"wing": "technical", "room": "old"}]],
                    "distances": [[0.25]]}

    monkeypatch.setattr(bridge, "_collection", Collection())
    assert bridge.search_memory("memory", wing="technical") == [
        {"text": "existing memory", "wing": "technical", "room": "old", "score": 0.75}
    ]
