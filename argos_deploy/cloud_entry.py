"""
cloud_entry.py -- entry point for Cloud Run.

Rule: uvicorn.run() ONLY in the main thread (otherwise signal.signal -> ValueError).
Orchestrator is initialized in background; port 8080 opens immediately.
After init, PeerAutoConnect connects to all known static peers from config/peers.json.
"""
from __future__ import annotations

import os
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Thread, Lock

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.cloud_auth import CloudBearerAuthMiddleware
from src.control_api import panel_response

_boot_time = time.time()
_ready = False
_init_error = None


def _report_codex_status() -> None:
    codex_home = Path(os.getenv("CODEX_HOME", "/codex-home"))
    workspace = Path(os.getenv("ARGOS_CODEX_WORKDIR", "/app"))
    auth_status = "ready" if (codex_home / "auth.json").is_file() else "missing"
    launcher_status = "ready" if Path("/usr/local/bin/codex-agent").is_file() else "missing"
    print(f"[CODEX] auth cache {auth_status}", flush=True)
    print(f"[CODEX] agent launcher {launcher_status}; workspace={workspace}", flush=True)


_report_codex_status()


def _start_p2p_if_enabled(core, application):
    """Explicit opt-in; no automatic connections to historical internet peers."""
    if os.getenv("ARGOS_P2P_AUTOSTART", "false").strip().lower() not in ("1", "true", "yes", "on"):
        return
    if core is None:
        raise RuntimeError("P2P autostart requires initialized core")
    with application.state.p2p_lifecycle_lock:
        if application.state.p2p_closing:
            return
        core.start_p2p()
        application.state.p2p = core.p2p


@asynccontextmanager
async def lifespan(application):
    try:
        yield
    finally:
        # Serializes ownership with background ArgosInit, including shutdown during init.
        with application.state.p2p_lifecycle_lock:
            application.state.p2p_closing = True
            p2p = getattr(application.state, "p2p", None)
            core = getattr(application.state, "core", None)
        try:
            stop_voice = getattr(core, "stop_wake_word", None)
            if callable(stop_voice):
                await asyncio.to_thread(stop_voice)
        finally:
            try:
                if p2p is not None:
                    await asyncio.to_thread(p2p.stop)
            finally:
                runner = getattr(application.state, "task_runner", None)
                if runner is not None:
                    await asyncio.to_thread(runner.close)

# Lightweight app -- no heavy imports here
app = FastAPI(title="Argos Cloud", version="2.1.3", lifespan=lifespan)
app.state.p2p_lifecycle_lock = Lock()
app.state.p2p_closing = False
app.state.p2p = None
app.state.core = None
app.add_middleware(CloudBearerAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "ok": True,
        "ready": _ready,
        "uptime_seconds": int(time.time() - _boot_time),
        "error": _init_error,
    }


@app.get("/")
def root():
    return {"service": "argos-core", "ready": _ready}


app.add_api_route("/ui", panel_response, methods=["GET"], include_in_schema=False)


def _init_orchestrator():
    global _ready, _init_error

    try:
        print("[CLOUD] Loading .env ...", flush=True)
        from dotenv import find_dotenv, load_dotenv
        env_path = find_dotenv(usecwd=True) or find_dotenv()
        if env_path:
            load_dotenv(env_path, override=False)

        from src.persistent_state import prepare_persistent_state

        app_root = Path(__file__).resolve().parent
        state_root = Path(os.getenv("ARGOS_STATE_ROOT", "/app/persist"))
        state_mapping = prepare_persistent_state(app_root, state_root)
        print(f"[CLOUD] Persistent state ready: {state_mapping}", flush=True)

        print("[CLOUD] Initializing ArgosOrchestrator ...", flush=True)
        from main import ArgosOrchestrator
        from src.external_guard_runtime import install_external_action_guard
        from src.mcp_api import ArgosMCPServer

        orchestrator = ArgosOrchestrator()
        core  = getattr(orchestrator, "core",  None)
        admin = getattr(orchestrator, "admin", None)
        with app.state.p2p_lifecycle_lock:
            app.state.core = core
            closing = app.state.p2p_closing
        if closing:
            stop_voice = getattr(core, "stop_wake_word", None)
            if callable(stop_voice):
                stop_voice()
            return  # shutdown already occurred while the core was initializing

        # Install the fail-closed communication policy before any generic MCP
        # command surface can dispatch text into ARGOS core.
        install_external_action_guard(core)
        print(
            "[SECURITY] External action guard active "
            f"send={os.getenv('EXTERNAL_SEND_ENABLED', 'false')} "
            f"draft_only={os.getenv('EXTERNAL_DRAFT_ONLY', 'true')} "
            f"approval={os.getenv('EXTERNAL_REQUIRE_OWNER_APPROVAL', 'true')}",
            flush=True,
        )

        mcp = ArgosMCPServer(core=core, admin=admin)
        from src.control_api import create_control_router
        from src.task_runtime import TaskRunner

        task_path = os.getenv("ARGOS_TASK_DB_PATH", str(state_root / "tasks.sqlite3"))
        runner = TaskRunner(task_path, lambda text: core.process_logic(text, admin, None))
        mcp.task_runner = runner
        app.state.task_runner = runner
        app.include_router(create_control_router(runner, lambda: {**mcp._status(), "ready": _ready}))
        # Mount at the application root so the MCP server's own /mcp route
        # is exposed publicly as /mcp rather than the accidental /mcp/mcp.
        # /health and / are declared above and therefore keep precedence.
        app.mount("/", mcp.app)

        _start_p2p_if_enabled(core, app)
        _ready = True
        elapsed = time.time() - _boot_time
        print(f"[CLOUD] Argos ready! uptime={elapsed:.1f}s", flush=True)


    except Exception as exc:
        _init_error = str(exc)
        print(f"[CLOUD] Init error: {exc}", flush=True)


# Start init in background BEFORE uvicorn.run()
Thread(target=_init_orchestrator, daemon=True, name="ArgosInit").start()


if __name__ == "__main__":
    host = os.getenv("ARGOS_MCP_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("PORT", os.getenv("ARGOS_MCP_PORT", "8080")) or "8080")
    print(f"[CLOUD] HTTP server starting on {host}:{port} ...", flush=True)
    # uvicorn.run in the MAIN thread -- required for signal handlers!
    uvicorn.run(app, host=host, port=port, log_level="info")
