import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone


def _distinct(destination, protected_paths):
    target = Path(destination).resolve()
    for item in protected_paths:
        if not item:
            continue
        other = Path(item).resolve()
        if target == other or (target.exists() and other.exists() and os.path.samefile(target, other)):
            raise ValueError("Источник и отдельное хранилище должны различаться")
    return target


def _read(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def _fingerprint(path):
    stat = Path(path).stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _metadata(conn):
    return {key: json.loads(value) for key, value in conn.execute("SELECT key,value FROM metadata")}


def _initialize(conn, kind):
    conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    conn.execute("CREATE TABLE documents(id TEXT,document TEXT NOT NULL,wing TEXT,room TEXT,source TEXT,source_file TEXT,date TEXT,origin TEXT)")
    backend = "lexical"
    try:
        conn.execute("CREATE VIRTUAL TABLE search USING fts5(document, tokenize='unicode61')")
        backend = "fts5"
    except sqlite3.OperationalError:
        pass
    conn.executemany("INSERT INTO metadata VALUES(?,?)", [
        ("kind", json.dumps(kind)), ("backend", json.dumps(backend)), ("version", "1"),
    ])
    return backend


def _insert(conn, values, backend):
    cursor = conn.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)", values)
    if backend == "fts5":
        conn.execute("INSERT INTO search(rowid,document) VALUES(?,?)", (cursor.lastrowid, values[1].casefold()))


def build_index(source_path, index_path):
    source = Path(source_path).resolve()
    target = _distinct(index_path, [source])
    if target.exists():
        with closing(_read(target)) as existing:
            if _metadata(existing).get("kind") != "recovered":
                raise ValueError("Нельзя заменить хранилище новых фактов индексом")
    fingerprint = _fingerprint(source)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".part", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with closing(_read(source)) as src, closing(sqlite3.connect(temporary)) as dst:
            columns = {row[1] for row in src.execute("PRAGMA table_info(drawers)")}
            if "document" not in columns:
                raise ValueError("Источник не содержит таблицу drawers с document")
            backend = _initialize(dst, "recovered")
            names = ["id", "document", "wing", "room", "source", "source_file", "filed_at", "ts"]
            selected = ",".join(name if name in columns else "NULL" for name in names)
            count = 0
            for row in src.execute("SELECT " + selected + " FROM drawers"):
                ident, document, wing, room, provenance, source_file, filed_at, timestamp = row
                count += 1
                _insert(dst, (str(ident) if ident is not None else str(count), document or "",
                              wing or "", room or "", provenance or "", source_file or "",
                              str(filed_at or timestamp or ""), "recovered"), backend)
            if _fingerprint(source) != fingerprint:
                raise ValueError("Источник изменился во время построения индекса")
            metadata = {"source_path": str(source), "source_sha256": digest.hexdigest(),
                        "source_fingerprint": fingerprint, "count": count, "backend": backend,
                        "built_at": datetime.now(timezone.utc).isoformat()}
            dst.executemany("INSERT OR REPLACE INTO metadata VALUES(?,?)",
                            [(key, json.dumps(value)) for key, value in metadata.items()])
            dst.commit()
        os.replace(temporary, target)
        return metadata
    finally:
        temporary.unlink(missing_ok=True)


def index_status(index_path):
    try:
        with closing(_read(index_path)) as conn:
            metadata = _metadata(conn)
            if metadata.get("kind") == "fact":
                return {"status": "ready", "kind": "fact", "backend": metadata["backend"],
                        "count": conn.execute("SELECT count(*) FROM documents").fetchone()[0]}
            stale = _fingerprint(metadata["source_path"]) != metadata["source_fingerprint"]
            return {"status": "stale" if stale else "ready", "kind": "recovered",
                    "backend": metadata["backend"], "count": metadata["count"],
                    "source_sha256": metadata["source_sha256"]}
    except (OSError, sqlite3.Error, ValueError, KeyError):
        return {"status": "unavailable", "count": 0}


def search_index(index_path, query, top_k=5, wing=""):
    terms = list(dict.fromkeys(re.findall(r"[^\W_]+", query[:512].casefold())))[:12]
    if not terms or top_k <= 0 or index_status(index_path)["status"] != "ready":
        return []
    try:
        with closing(_read(index_path)) as conn:
            conn.row_factory = sqlite3.Row
            meta = _metadata(conn)
            deadline = time.monotonic() + 5
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            if meta["backend"] == "fts5":
                statement = ("SELECT d.*, -bm25(search) AS score FROM search "
                             "JOIN documents d ON d.rowid=search.rowid WHERE search MATCH ? "
                             "AND (?='' OR d.wing=?) ORDER BY bm25(search),d.rowid LIMIT ?")
                rows = conn.execute(statement, (" OR ".join('"' + word + '"' for word in terms),
                                                wing, wing, min(top_k, 10))).fetchall()
            else:
                selected = set(terms)
                def score(document):
                    words = set(re.findall(r"[^\W_]+", document.casefold()))
                    return len(selected & words) / len(selected)
                conn.create_function("lexical_score", 1, score, deterministic=True)
                rows = conn.execute("SELECT *,lexical_score(document) AS score FROM documents "
                                    "WHERE (?='' OR wing=?) AND score>0 ORDER BY score DESC,rowid LIMIT ?",
                                    (wing, wing, min(top_k, 10))).fetchall()
            return [{"id": row["id"], "text": row["document"][:4096], "wing": row["wing"],
                     "room": row["room"], "source": row["source"], "source_file": row["source_file"],
                     "date": row["date"], "origin": row["origin"], "score": row["score"],
                     "score_kind": meta["backend"], "source_sha256": meta.get("source_sha256", "")}
                    for row in rows]
    except (OSError, sqlite3.Error, ValueError, KeyError):
        return []


def save_fact(facts_path, text, wing="technical", room="general", source="user", source_file="", protected_paths=()):
    if not isinstance(text, str) or not text.strip() or len(text) > 100000:
        raise ValueError("Факт должен содержать от 1 до 100000 символов")
    path = _distinct(facts_path, protected_paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            backend = _initialize(conn, "fact")
        else:
            metadata = _metadata(conn)
            if metadata.get("kind") != "fact":
                raise ValueError("Запись разрешена только в отдельное хранилище новых фактов")
            backend = metadata["backend"]
        ident = uuid.uuid4().hex
        _insert(conn, (ident, text.strip(), wing, room, source, source_file,
                       datetime.now(timezone.utc).isoformat(), "fact"), backend)
        conn.commit()
    return ident
