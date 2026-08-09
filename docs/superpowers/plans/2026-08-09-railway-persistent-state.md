# ARGOS Railway Persistent State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist ARGOS runtime state across Railway redeploys while keeping application source code image-backed and continuously deployable from GitHub.

**Architecture:** Mount one Railway volume at `/app/persist`. A small pre-orchestrator bootstrap module seeds `/app/persist/data` and `/app/persist/config` on first use and replaces `/app/data` and `/app/config` with symlinks to those persistent directories. The service keeps its current public domain, port, start command, and healthcheck.

**Tech Stack:** Python 3.11, pathlib/shutil/os, pytest, Railway volumes/variables, FastAPI/Uvicorn.

## Global Constraints

- Do not modify the existing `argos` VPN/API service.
- Never commit or print `master.key` or any secret value.
- Never mount a Railway volume over `/app` or `/app/src`.
- `argos-full` must keep `python3 cloud_entry.py`, port `8080`, and healthcheck `/health`.
- Existing persistent contents must never be overwritten by image defaults on later boots.

---

### Task 1: Persistent state bootstrap

**Files:**
- Create: `argos_deploy/src/persistent_state.py`
- Create: `argos_deploy/tests/test_persistent_state.py`

**Interfaces:**
- Produces: `prepare_persistent_state(app_root: pathlib.Path, state_root: pathlib.Path) -> dict[str, str]`
- Raises: `PersistentStateError` when the persistent root cannot be prepared safely.

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
cd argos_deploy
pytest -q tests/test_persistent_state.py
```

Expected: import failure because `src.persistent_state` does not exist.

- [ ] **Step 3: Implement the minimal bootstrap module**

```python
from __future__ import annotations

import os
import shutil
from pathlib import Path


class PersistentStateError(RuntimeError):
    pass


def _is_empty(path: Path) -> bool:
    return not any(path.iterdir())


def _seed(source: Path, target: Path) -> None:
    if not source.exists() or not source.is_dir() or not _is_empty(target):
        return
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def _replace_with_symlink(runtime_path: Path, persistent_path: Path) -> None:
    if runtime_path.is_symlink():
        if runtime_path.resolve() == persistent_path.resolve():
            return
        runtime_path.unlink()
    elif runtime_path.exists():
        if runtime_path.is_dir():
            shutil.rmtree(runtime_path)
        else:
            runtime_path.unlink()
    os.symlink(persistent_path, runtime_path, target_is_directory=True)


def prepare_persistent_state(app_root: Path, state_root: Path) -> dict[str, str]:
    try:
        app_root = app_root.resolve()
        state_root.mkdir(parents=True, exist_ok=True)
        if not state_root.is_dir():
            raise PersistentStateError(f"state root is not a directory: {state_root}")

        mapping: dict[str, str] = {}
        for name in ("data", "config"):
            runtime_path = app_root / name
            persistent_path = state_root / name
            persistent_path.mkdir(parents=True, exist_ok=True)
            _seed(runtime_path, persistent_path)
            _replace_with_symlink(runtime_path, persistent_path)
            mapping[name] = str(persistent_path.resolve())
        return mapping
    except PersistentStateError:
        raise
    except Exception as exc:
        raise PersistentStateError(str(exc)) from exc
```

- [ ] **Step 4: Run the tests and verify pass**

```bash
cd argos_deploy
pytest -q tests/test_persistent_state.py
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add argos_deploy/src/persistent_state.py argos_deploy/tests/test_persistent_state.py
git commit -m "feat(cloud): persist ARGOS runtime state"
```

---

### Task 2: Integrate bootstrap into cloud startup

**Files:**
- Modify: `argos_deploy/cloud_entry.py`

**Interfaces:**
- Consumes: `prepare_persistent_state(app_root, state_root)` from Task 1.
- Produces: startup log `[CLOUD] Persistent state ready: ...` before `ArgosOrchestrator` initialization.

- [ ] **Step 1: Add state preparation before heavy ARGOS imports**

Inside `_init_orchestrator()`, after `.env` loading and before importing `ArgosOrchestrator`, add:

```python
from pathlib import Path
from src.persistent_state import prepare_persistent_state

app_root = Path(__file__).resolve().parent
state_root = Path(os.getenv("ARGOS_STATE_ROOT", "/app/persist"))
state_mapping = prepare_persistent_state(app_root, state_root)
print(f"[CLOUD] Persistent state ready: {state_mapping}", flush=True)
```

The existing outer `try/except` must remain in control so a persistence failure sets `_init_error` and leaves `_ready = False`.

- [ ] **Step 2: Run focused tests**

```bash
cd argos_deploy
pytest -q tests/test_persistent_state.py
python -m py_compile cloud_entry.py src/persistent_state.py
```

Expected: tests pass and compilation exits 0.

- [ ] **Step 3: Commit**

```bash
git add argos_deploy/cloud_entry.py
git commit -m "feat(cloud): initialize persistent state before ARGOS core"
```

---

### Task 3: Railway volume and state cutover

**Files:**
- Railway service configuration only; no source files.

**Interfaces:**
- Consumes: `ARGOS_STATE_ROOT=/app/persist` and the bootstrap from Tasks 1-2.
- Produces: one volume mounted at `/app/persist` for service `argos-full`.

- [ ] **Step 1: Capture current state metadata without exposing secrets**

Use Railway-side container inspection to record only non-secret verification values before cutover:

- current `node_id` hash or exact value if it is not treated as secret;
- SHA-256 of `master.key` only, never the key itself;
- filenames and sizes under `/app/data` and `/app/data/backups`.

- [ ] **Step 2: Create/attach the Railway volume**

Use Railway infrastructure tooling for service `argos-full` only:

- mount path: `/app/persist`;
- keep service domain `argos-full-production.up.railway.app` routed to port 8080;
- do not alter `argos`.

- [ ] **Step 3: Set the state root variable without a separate redeploy**

Set:

```text
ARGOS_STATE_ROOT=/app/persist
```

Use the connector's `skipDeploys=true` equivalent when possible so the variable and volume configuration are applied in one final deployment.

- [ ] **Step 4: Deploy the latest persistence commit**

Deploy the exact commit produced by Task 2 to `argos-full` and wait for Railway `SUCCESS`.

- [ ] **Step 5: Verify application readiness and storage mapping**

Check:

```text
GET https://argos-full-production.up.railway.app/health
GET https://argos-full-production.up.railway.app/mcp
```

Expected:

```json
{"ok": true, "ready": true, "error": null}
```

and `/mcp` returns HTTP 200.

Inspect the running container and verify:

- `/app/data` is a symlink to `/app/persist/data`;
- `/app/config` is a symlink to `/app/persist/config`;
- persistent copies contain `memory.db`, `argos_memory.db`, `life_support.db`, `node_id`, and `master.key`;
- `master.key` contents are never surfaced.

- [ ] **Step 6: Verify backup persistence**

Wait for or trigger one normal ARGOS automatic backup and confirm a ZIP appears in `/app/persist/data/backups`.

---

### Task 4: Redeploy durability test

**Files:**
- Railway deployment only.

**Interfaces:**
- Consumes: live `argos-full` with mounted persistent state.
- Produces: evidence that identity and database/backup files survive a second deployment.

- [ ] **Step 1: Record pre-redeploy fingerprints**

Record without exposing secrets:

- `node_id` value or hash;
- SHA-256 of `master.key`;
- size/mtime of `memory.db` and `argos_memory.db`;
- name of the newest backup ZIP.

- [ ] **Step 2: Redeploy the same commit once**

Redeploy `argos-full` with no configuration changes.

- [ ] **Step 3: Verify post-redeploy fingerprints**

Confirm:

- Railway status is `SUCCESS`;
- `/health` has `ready=true`;
- `/mcp` is HTTP 200;
- `node_id` fingerprint is unchanged;
- `master.key` SHA-256 is unchanged;
- the previously recorded backup ZIP still exists;
- SQLite database files still exist under `/app/persist/data`.

- [ ] **Step 4: Final status report**

Report the service URL, deployment ID, volume mount, health/MCP checks, and durability verification. Mention any modules still in fallback mode separately from persistence status.
