import hashlib
import sqlite3

import pytest

from src.memory_index import build_index, search_index, save_fact, index_status


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "backup.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE drawers(id TEXT,document TEXT,wing TEXT,room TEXT,source TEXT,source_file TEXT,ts INTEGER,filed_at TEXT)")
        conn.executemany("INSERT INTO drawers VALUES(?,?,?,?,?,?,?,?)", [
            (str(i), "Аргос общее", "technical", "general", "synthetic", "", 1, "")
            for i in range(250)
        ])
        conn.execute("INSERT INTO drawers VALUES(?,?,?,?,?,?,?,?)", (
            "late", "Аргос редкий маяк", "technical", "recovery", "synthetic-backup",
            "https://example.invalid/source", 123, "2026-01-01"))
    return path


def test_full_index_late_hit_provenance_and_readonly_source(source, tmp_path):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    index = tmp_path / "index.sqlite3"
    metadata = build_index(source, index)
    assert metadata["count"] == 251
    assert metadata["source_sha256"] == digest
    hit = search_index(index, "Аргос редкий маяк", top_k=1)[0]
    assert hit["id"] == "late"
    assert hit["source"] == "synthetic-backup"
    assert hit["source_file"] == "https://example.invalid/source"
    assert hit["date"] == "2026-01-01"
    assert hit["source_sha256"] == digest
    assert hit["origin"] == "recovered"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert search_index(index, "редкий", wing="other") == []
    assert search_index(index, "редкий")[0]["id"] == "late"


def test_source_and_alias_cannot_be_destination(source, tmp_path):
    with pytest.raises(ValueError):
        build_index(source, source)
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(source)
    with pytest.raises(ValueError):
        build_index(source, alias)
    with pytest.raises(ValueError):
        save_fact(source, "new fact", protected_paths=[source])


def test_explicit_new_facts_separate_and_persist_after_reopen(source, tmp_path):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    index = tmp_path / "index.sqlite3"
    facts = tmp_path / "facts.sqlite3"
    build_index(source, index)
    fact_id = save_fact(facts, "новый факт маяк", source="user", protected_paths=[source, index])
    hit = search_index(facts, "маяк")[0]
    assert hit["id"] == fact_id and hit["origin"] == "fact"
    assert hit["source"] == "user" and hit["date"]
    assert search_index(index, "новый") == []
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_missing_index_read_does_not_create_file(tmp_path):
    path = tmp_path / "missing.sqlite3"
    assert search_index(path, "query") == []
    assert not path.exists()


def test_rebuild_failure_keeps_previous_index(source, tmp_path):
    index = tmp_path / "index.sqlite3"
    build_index(source, index)
    digest = hashlib.sha256(index.read_bytes()).hexdigest()
    bad = tmp_path / "bad.sqlite3"
    with sqlite3.connect(bad) as conn:
        conn.execute("CREATE TABLE unrelated(x)")
    with pytest.raises(ValueError):
        build_index(bad, index)
    assert hashlib.sha256(index.read_bytes()).hexdigest() == digest


def test_modified_source_reports_stale_without_search(source, tmp_path):
    index = tmp_path / "index.sqlite3"
    build_index(source, index)
    with sqlite3.connect(source) as conn:
        conn.execute("UPDATE drawers SET document='changed' WHERE id='late'")
    assert index_status(index)["status"] == "stale"
    assert search_index(index, "редкий") == []


def test_nonfts_fallback_searches_full_index(source, tmp_path, monkeypatch):
    original = sqlite3.connect
    class WithoutFTS(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("CREATE VIRTUAL TABLE"):
                raise sqlite3.OperationalError("no such module: fts5")
            return super().execute(sql, *args)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=WithoutFTS, **kw))
    index = tmp_path / "index.sqlite3"
    assert build_index(source, index)["backend"] == "lexical"
    assert search_index(index, "Аргос редкий маяк", top_k=1)[0]["id"] == "late"


def test_bridge_search_and_explicit_fact_save(source, tmp_path, monkeypatch):
    from src import mempalace_bridge as bridge
    index = tmp_path / "index.sqlite3"
    facts = tmp_path / "facts.sqlite3"
    build_index(source, index)
    monkeypatch.setenv("ARGOS_MEMPALACE_SQLITE_PATH", str(source))
    monkeypatch.setenv("ARGOS_MEMPALACE_INDEX_PATH", str(index))
    monkeypatch.setenv("ARGOS_MEMPALACE_FACTS_PATH", str(facts))
    monkeypatch.setattr(bridge, "_MEMPALACE_ENABLED", True)
    assert bridge.search_memory("редкий")[0]["id"] == "late"
    assert not facts.exists()
    saved = bridge.save_fact("Синтетический новый факт Straße", source_file="https://example.invalid/fact")
    hit = bridge.search_memory("STRASSE")[0]
    assert hit["id"] == saved["id"] and hit["origin"] == "fact"
    assert "source=user" in bridge.get_memory_context("STRASSE")
    assert bridge.memory_status()["index"]["count"] == 251
    assert bridge.memory_status()["facts"]["count"] == 1


def test_facts_cannot_repurpose_recovered_index(source, tmp_path):
    index = tmp_path / "index.sqlite3"
    build_index(source, index)
    digest = hashlib.sha256(index.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        save_fact(index, "new fact")
    assert hashlib.sha256(index.read_bytes()).hexdigest() == digest


def test_new_stores_are_private_and_reads_do_not_change_index(source, tmp_path):
    import stat
    index = tmp_path / "index.sqlite3"
    facts = tmp_path / "facts.sqlite3"
    build_index(source, index)
    save_fact(facts, "synthetic fact")
    assert stat.S_IMODE(index.stat().st_mode) == 0o600
    assert stat.S_IMODE(facts.stat().st_mode) == 0o600
    digest = hashlib.sha256(index.read_bytes()).hexdigest()
    search_index(index, "Аргос")
    index_status(index)
    assert hashlib.sha256(index.read_bytes()).hexdigest() == digest


def test_unindexed_fallback_does_not_stop_at_first_200(source, monkeypatch):
    from src import mempalace_bridge as bridge
    monkeypatch.setenv("ARGOS_MEMPALACE_SQLITE_PATH", str(source))
    monkeypatch.delenv("ARGOS_MEMPALACE_INDEX_PATH", raising=False)
    monkeypatch.delenv("ARGOS_MEMPALACE_FACTS_PATH", raising=False)
    monkeypatch.setattr(bridge, "_MEMPALACE_ENABLED", True)
    assert bridge.search_memory("Аргос редкий маяк", top_k=1)[0]["room"] == "recovery"


def test_bridge_interleaves_independent_rankings_without_comparing_bm25(monkeypatch):
    from src import mempalace_bridge as bridge
    monkeypatch.setattr(bridge, "_MEMPALACE_ENABLED", True)
    monkeypatch.setenv("ARGOS_MEMPALACE_INDEX_PATH", "synthetic-index")
    monkeypatch.setenv("ARGOS_MEMPALACE_FACTS_PATH", "synthetic-facts")
    def search(path, *args):
        origin = "fact" if path == "synthetic-facts" else "recovered"
        score = 0.000001 if origin == "fact" else 100
        return [{"id": f"{origin}-{i}", "text": "common synthetic query", "origin": origin,
                 "score": score / (i + 1), "score_kind": "fts5"} for i in range(3)]
    monkeypatch.setattr("src.memory_index.search_index", search)
    results = bridge.search_memory("common", top_k=3)
    assert [r["id"] for r in results] == ["recovered-0", "fact-0", "recovered-1"]
    assert results[1]["score"] == 0.000001
    assert results[1]["score_kind"] == "fts5"
    assert all("rank_score" in result for result in results)
