"""
src/mempalace_bridge.py — ARGOS ↔ MemPalace Integration

4-layer memory stack:
  Layer 0: Identity (~100 tokens)   — кто такой ARGOS, всегда загружен
  Layer 1: Essential (~500-800)     — топ воспоминаний из palace, всегда загружен
  Layer 2: On-Demand (~200-500)     — конкретное крыло при упоминании темы
  Layer 3: Deep Search (unlimited)  — полный семантический поиск по ChromaDB

Использование:
  from src.mempalace_bridge import get_memory_context, store_memory, search_memory
  ctx = get_memory_context()          # L0 + L1 (~700 токенов)
  store_memory("Redis упал", wing="errors", room="infra")
  results = search_memory("watson timeout", top_k=5)
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Optional

log = logging.getLogger("argos.mempalace")

# ── Переменные окружения ───────────────────────────────────────────────────
_MEMPALACE_ENABLED = os.getenv("ARGOS_MEMPALACE", "1").strip() in ("1", "true", "on", "yes")
_PALACE_PATH = os.getenv(
    "MEMPALACE_PALACE_PATH",
    str(Path(__file__).parent.parent / "data" / "mempalace"),
)
_IDENTITY_PATH = os.getenv(
    "MEMPALACE_IDENTITY",
    str(Path(__file__).parent.parent / ".mempalace" / "identity.txt"),
)
_COLLECTION = "argos_palace"

# ── Lazy imports ────────────────────────────────────────────────────────────
_mp_ok = False
_chromadb_client = None
_collection = None
_init_lock = threading.Lock()


def _sqlite_path() -> str:
    return os.getenv("ARGOS_MEMPALACE_SQLITE_PATH", "").strip()


def _sqlite_connection():
    uri = Path(_sqlite_path()).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=0.2)
    deadline = time.monotonic() + 2.0
    steps = 0

    def budget():
        nonlocal steps
        steps += 1000
        return int(steps > 10000000 or time.monotonic() > deadline)

    conn.set_progress_handler(budget, 1000)
    return conn


def _sqlite_search(query: str, top_k: int, wing: str) -> list[dict]:
    if not _MEMPALACE_ENABLED or top_k <= 0:
        return []
    words = dict.fromkeys(re.findall(r"[^\W_]+", query[:512].casefold()))
    terms = set(list(words)[:12])
    if not terms:
        return []

    def lexical_score(document):
        words = set(re.findall(r"[^\W_]+", (document or "").casefold()))
        return len(terms & words) / len(terms)

    try:
        with closing(_sqlite_connection()) as conn:
            conn.create_function("lexical_score", 1, lexical_score, deterministic=True)
            rows = conn.execute(
                "SELECT substr(document,1,4096), substr(wing,1,128), "
                "substr(room,1,128), lexical_score(substr(document,1,4096)) AS score "
                "FROM drawers WHERE (? = '' OR wing = ?) AND score > 0 "
                "ORDER BY score DESC LIMIT 10",
                (wing, wing),
            ).fetchall()
        rows.sort(key=lambda row: row[3], reverse=True)
        return [{"text": doc, "wing": w or "", "room": room or "",
                 "score": round(score, 4), "score_kind": "lexical"}
                for doc, w, room, score in rows[:min(top_k, 10)]]
    except (sqlite3.Error, OSError, ValueError):
        return []


def _ensure_init() -> bool:
    """Инициализация ChromaDB (lazy, thread-safe)."""
    global _mp_ok, _chromadb_client, _collection
    if _mp_ok:
        return True
    if not _MEMPALACE_ENABLED:
        return False
    with _init_lock:
        if _mp_ok:
            return True
        try:
            import chromadb

            Path(_PALACE_PATH).mkdir(parents=True, exist_ok=True)
            _chromadb_client = chromadb.PersistentClient(path=_PALACE_PATH)
            _collection = _chromadb_client.get_or_create_collection(
                name=_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            _mp_ok = True
            log.info("[MemPalace] Инициализирован: %s (%d drawers)", _PALACE_PATH, _collection.count())
        except ImportError:
            log.warning("[MemPalace] chromadb не установлен — pip install mempalace")
        except Exception as exc:
            log.warning("[MemPalace] Ошибка инициализации: %s", exc)
    return _mp_ok


# ── Layer 0: Identity ───────────────────────────────────────────────────────

def _layer0() -> str:
    """Читает identity.txt (~100 токенов). Всегда быстро."""
    try:
        if os.path.exists(_IDENTITY_PATH):
            with open(_IDENTITY_PATH, encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return "I am ARGOS — autonomous AI ecosystem by Всеволод."


# ── Layer 1: Essential Story ─────────────────────────────────────────────────

def _layer1(max_drawers: int = 12, max_chars: int = 2400) -> str:
    """Топ воспоминаний по importance из palace (~500-800 токенов)."""
    if not _ensure_init():
        return ""
    try:
        results = _collection.get(include=["documents", "metadatas"])
        docs = results.get("documents", [])
        metas = results.get("metadatas", [])
        if not docs:
            return ""

        scored = []
        for doc, meta in zip(docs, metas):
            imp = 3.0
            for key in ("importance", "weight", "emotional_weight"):
                v = meta.get(key)
                if v is not None:
                    try:
                        imp = float(v)
                        break
                    except (TypeError, ValueError):
                        pass
            scored.append((imp, meta, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_drawers]

        from collections import defaultdict
        by_room: dict = defaultdict(list)
        for imp, meta, doc in top:
            room = meta.get("room", "general")
            by_room[room].append((imp, meta, doc))

        lines = ["## ARGOS MEMORY [L1 — Essential]"]
        total = 0
        for room, entries in sorted(by_room.items()):
            lines.append(f"\n[{room}]")
            for _, meta, doc in entries:
                snippet = doc.strip().replace("\n", " ")
                if len(snippet) > 180:
                    snippet = snippet[:177] + "..."
                wing = meta.get("wing", "")
                line = f"  [{wing}] {snippet}"
                lines.append(line)
                total += len(line)
                if total >= max_chars:
                    break
            if total >= max_chars:
                break

        return "\n".join(lines)
    except Exception as exc:
        log.debug("[MemPalace] L1 error: %s", exc)
        return ""


# ── Layer 2: On-Demand ───────────────────────────────────────────────────────

def _layer2(wing: str, max_drawers: int = 6) -> str:
    """Воспоминания конкретного крыла по запросу (~200-500 токенов)."""
    if not _ensure_init():
        return ""
    try:
        results = _collection.get(
            where={"wing": wing},
            include=["documents", "metadatas"],
        )
        docs = results.get("documents", [])
        metas = results.get("metadatas", [])
        if not docs:
            return ""

        lines = [f"## ARGOS MEMORY [L2 — {wing}]"]
        for doc, meta in zip(docs[:max_drawers], metas[:max_drawers]):
            snippet = doc.strip().replace("\n", " ")[:200]
            room = meta.get("room", "?")
            lines.append(f"  [{room}] {snippet}")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("[MemPalace] L2 error: %s", exc)
        return ""


# ── Layer 3: Deep Search ─────────────────────────────────────────────────────

def search_memory(query: str, top_k: int = 5, wing: str = "") -> list[dict]:
    """
    Семантический поиск по всему palace.
    Возвращает список: [{"text": ..., "wing": ..., "room": ..., "score": ...}]
    """
    index_path = os.getenv("ARGOS_MEMPALACE_INDEX_PATH", "").strip()
    facts_path = os.getenv("ARGOS_MEMPALACE_FACTS_PATH", "").strip()
    if index_path or facts_path:
        if not _MEMPALACE_ENABLED or top_k <= 0:
            return []
        from src.memory_index import search_index
        recovered = search_index(index_path, query, top_k, wing) if index_path else _sqlite_search(query, top_k, wing) if _sqlite_path() else []
        groups = [recovered]
        if facts_path:
            groups.append(search_index(facts_path, query, top_k, wing))
        hits = [dict(hit, rank_score=1 / (60 + rank))
                for group in groups for rank, hit in enumerate(group, 1)]
        return sorted(hits, key=lambda hit: hit["rank_score"], reverse=True)[:min(top_k, 10)]
    if _sqlite_path():
        return _sqlite_search(query, top_k, wing)
    if not _ensure_init():
        return []
    try:
        kwargs: dict = {
            "query_texts": [query],
            "n_results": min(top_k, max(_collection.count(), 1)),
            "include": ["documents", "metadatas", "distances"],
        }
        if wing:
            kwargs["where"] = {"wing": wing}

        res = _collection.query(**kwargs)
        out = []
        for doc, meta, dist in zip(
            res.get("documents", [[]])[0],
            res.get("metadatas", [[]])[0],
            res.get("distances", [[]])[0],
        ):
            out.append({
                "text": doc,
                "wing": meta.get("wing", ""),
                "room": meta.get("room", ""),
                "score": round(1.0 - dist, 4),
            })
        return out
    except Exception as exc:
        log.debug("[MemPalace] search error: %s", exc)
        return []


# ── Запись в palace ─────────────────────────────────────────────────────────

def store_memory(
    text: str,
    wing: str = "technical",
    room: str = "general",
    importance: float = 3.0,
    source: str = "argos",
) -> bool:
    """
    Сохранить воспоминание в palace.

    wing   — крыло: technical | decisions | integrations | errors | memory | p2p | user
    room   — комната внутри крыла (произвольная строка)
    importance — 1-5, влияет на L1 приоритет
    """
    if os.getenv("ARGOS_MEMPALACE_FACTS_PATH", "").strip():
        try:
            save_fact(text, wing=wing, room=room, source=source)
            return True
        except (OSError, sqlite3.Error, ValueError):
            return False
    if _sqlite_path() or os.getenv("ARGOS_MEMPALACE_INDEX_PATH", "").strip():
        return False
    if not _ensure_init():
        return False
    if not text or not text.strip():
        return False
    try:
        import hashlib

        drawer_id = hashlib.sha256(f"{time.time()}{text[:80]}".encode()).hexdigest()[:16]
        _collection.add(
            documents=[text.strip()],
            ids=[drawer_id],
            metadatas=[{
                "wing": wing,
                "room": room,
                "importance": importance,
                "source": source,
                "ts": int(time.time()),
            }],
        )
        log.debug("[MemPalace] stored: wing=%s room=%s len=%d", wing, room, len(text))
        return True
    except Exception as exc:
        log.warning("[MemPalace] store error: %s", exc)
        return False


# ── Главный API ─────────────────────────────────────────────────────────────

def save_fact(text: str, wing: str = "technical", room: str = "general", source: str = "user", source_file: str = "") -> dict:
    from src.memory_index import save_fact as persist
    path = os.getenv("ARGOS_MEMPALACE_FACTS_PATH", "").strip()
    if not _MEMPALACE_ENABLED or not path:
        raise ValueError("Отдельное хранилище новых фактов не настроено или память отключена")
    ident = persist(path, text, wing=wing, room=room, source=source, source_file=source_file,
                    protected_paths=[_sqlite_path(), os.getenv("ARGOS_MEMPALACE_INDEX_PATH", "")])
    return {"id": ident, "origin": "fact", "saved": True}


def memory_status() -> dict:
    from src.memory_index import index_status
    index = os.getenv("ARGOS_MEMPALACE_INDEX_PATH", "").strip()
    facts = os.getenv("ARGOS_MEMPALACE_FACTS_PATH", "").strip()
    return {"enabled": _MEMPALACE_ENABLED,
            "index": index_status(index) if index else {"status": "not_configured"},
            "facts": index_status(facts) if facts else {"status": "not_configured"}}


_CONTEXT_STOPWORDS = frozenset((
    "а и или но в во на за из к ко о об от по с со у до для при без не ни "
    "это этот эта эти то что кто где когда почему зачем как какой какая какие "
    "ли же бы я ты вы мы он она они мне меня мой моя мое мои пожалуйста "
    "ответь отвечай расскажи объясни кратко коротко подробно одним одной одно "
    "коротким короткой кратким краткой предложением предложении предложениях "
    "словом словами русском русски языке "
    "a an the and or in on at to of for is are was why what how please "
    "answer respond briefly short concise sentence sentences one"
).split())


def get_memory_context(query: str = "", wing: str = "") -> str:
    """
    Собирает контекст памяти для подстановки в AI-запрос.

    Запрос без предметных слов не добавляет память.
    Для SQLite используются топ-3 лексических совпадения предметных слов.
    Для ChromaDB добавляются L0/L1, L2 по wing и L3 по предметному запросу.

    Итого: ~700-1200 токенов.
    """
    terms = list(dict.fromkeys(re.findall(r"[^\W_]+", query[:512].casefold())))
    query = " ".join(term for term in terms if term not in _CONTEXT_STOPWORDS)
    if not query:
        return ""
    if _sqlite_path() or os.getenv("ARGOS_MEMPALACE_INDEX_PATH", "").strip() or os.getenv("ARGOS_MEMPALACE_FACTS_PATH", "").strip():
        hits = search_memory(query, top_k=3, wing=wing)
        if not hits:
            return ""
        lines = ["## ARGOS MEMORY [Recovered — lexical matches]"]
        for hit in hits:
            snippet = hit["text"][:500].replace("\n", " ")
            provenance = ""
            if hit.get("id"):
                provenance = f" [{hit.get('origin', 'recovered')}:{hit['id']}; source={hit.get('source', '')}; date={hit.get('date', '')}; file={hit.get('source_file', '')}]"
            lines.append(f"  [{hit['wing']}/{hit['room']}]{provenance} {snippet}")
        return "\n".join(lines)[:2400]

    parts: list[str] = []

    # L0 — Identity (всегда)
    l0 = _layer0()
    if l0:
        parts.append(l0)

    # L1 — Essential Story (всегда)
    l1 = _layer1()
    if l1:
        parts.append(l1)

    # L2 — On-Demand wing
    if wing:
        l2 = _layer2(wing)
        if l2:
            parts.append(l2)

    # L3 — Deep Search
    if query:
        hits = search_memory(query, top_k=3)
        if hits:
            lines = ["## ARGOS MEMORY [L3 — Search results]"]
            for h in hits:
                snippet = h["text"][:200].replace("\n", " ")
                lines.append(f"  [{h['wing']}/{h['room']}] (score={h['score']}) {snippet}")
            parts.append("\n".join(lines))

    return "\n\n".join(parts)


def status() -> str:
    """Быстрый статус palace для команды /memory в Telegram."""
    if os.getenv("ARGOS_MEMPALACE_INDEX_PATH", "").strip() or os.getenv("ARGOS_MEMPALACE_FACTS_PATH", "").strip():
        info = memory_status()
        if not info["enabled"]:
            return "⚫ MemPalace: отключён (ARGOS_MEMPALACE=0)"
        index = info["index"]
        return (f"🧠 MemPalace: index {index['status']} / {index.get('backend', 'unknown')}; "
                f"Drawers: {index.get('count', 0)}; Facts: {info['facts'].get('count', 0)}")
    if _sqlite_path():
        if not _MEMPALACE_ENABLED:
            return "⚫ MemPalace: отключён (ARGOS_MEMPALACE=0)"
        try:
            with closing(_sqlite_connection()) as conn:
                count = conn.execute("SELECT COUNT(*) FROM drawers").fetchone()[0]
            return f"🧠 MemPalace: SQLite read-only / lexical; Drawers: {count}"
        except (sqlite3.Error, OSError, ValueError):
            return "🔴 MemPalace: SQLite недоступен"
    if not _ensure_init():
        if not _MEMPALACE_ENABLED:
            return "⚫ MemPalace: отключён (ARGOS_MEMPALACE=0)"
        return "🔴 MemPalace: ChromaDB недоступен"

    count = _collection.count()
    try:
        metas = _collection.get(include=["metadatas"])["metadatas"]
        wings: dict = {}
        for m in metas:
            w = m.get("wing", "?")
            wings[w] = wings.get(w, 0) + 1
        wing_str = "  ".join(f"{k}:{v}" for k, v in sorted(wings.items()))
    except Exception:
        wing_str = "?"

    return (
        f"🧠 *MemPalace*\n"
        f"  Drawers: {count}\n"
        f"  Wings:   {wing_str}\n"
        f"  Path:    `{_PALACE_PATH}`"
    )
