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
import threading
import time
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
_mp_init_attempted = False
_mp_error: str | None = None
_chromadb_client = None
_collection = None
_init_lock = threading.Lock()


def _ensure_init() -> bool:
    """Initialize ChromaDB once per process (lazy and thread-safe)."""
    global _mp_ok, _mp_init_attempted, _mp_error
    global _chromadb_client, _collection
    if _mp_ok:
        return True
    if not _MEMPALACE_ENABLED:
        _mp_error = "disabled"
        return False
    if _mp_init_attempted:
        return False
    with _init_lock:
        if _mp_ok:
            return True
        if _mp_init_attempted:
            return False
        _mp_init_attempted = True
        try:
            import chromadb

            Path(_PALACE_PATH).mkdir(parents=True, exist_ok=True)
            _chromadb_client = chromadb.PersistentClient(path=_PALACE_PATH)
            _collection = _chromadb_client.get_or_create_collection(
                name=_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            _mp_ok = True
            _mp_error = None
            log.info(
                "[MemPalace] initialized: %s (%d drawers)",
                _PALACE_PATH,
                _collection.count(),
            )
        except ImportError:
            _mp_error = "chromadb_unavailable"
            log.warning("[MemPalace] chromadb is not installed")
        except Exception as exc:
            _mp_error = f"init_error:{type(exc).__name__}"
            log.warning("[MemPalace] initialization error: %s", exc)
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

def get_memory_context(query: str = "", wing: str = "") -> str:
    """
    Собирает контекст памяти для подстановки в AI-запрос.

    L0 всегда + L1 всегда.
    Если query задан → L3 deep search (топ-3 релевантных).
    Если wing задан → L2 on-demand.

    Итого: ~700-1200 токенов.
    """
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


def get_health_details() -> dict:
    """Structured, secret-free MemPalace telemetry for /health/details."""
    if not _ensure_init():
        return {
            "available": False,
            "reason": _mp_error or "unavailable",
            "enabled": _MEMPALACE_ENABLED,
            "drawers": None,
            "wings": {},
            "path": _PALACE_PATH,
            "storage_bytes": None,
        }

    try:
        drawers = int(_collection.count())
        wings: dict[str, int] = {}
        try:
            metadatas = _collection.get(include=["metadatas"]).get(
                "metadatas", []
            )
            for metadata in metadatas:
                if not isinstance(metadata, dict):
                    continue
                wing = str(metadata.get("wing") or "unknown")
                wings[wing] = wings.get(wing, 0) + 1
        except Exception:
            wings = {}

        storage_bytes = 0
        palace_path = Path(_PALACE_PATH)
        if palace_path.exists():
            for item in palace_path.rglob("*"):
                try:
                    if item.is_file():
                        storage_bytes += item.stat().st_size
                except OSError:
                    continue

        return {
            "available": True,
            "enabled": True,
            "drawers": drawers,
            "wings": wings,
            "path": _PALACE_PATH,
            "storage_bytes": storage_bytes,
        }
    except Exception as exc:
        return {
            "available": False,
            "reason": f"status_error:{type(exc).__name__}",
            "enabled": True,
            "drawers": None,
            "wings": {},
            "path": _PALACE_PATH,
            "storage_bytes": None,
        }


def status() -> str:
    """Быстрый статус palace для команды /memory в Telegram."""
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
