"""
cloud_entry.py -- entry point for Cloud Run.

Rule: uvicorn.run() ONLY in the main thread (otherwise signal.signal -> ValueError).
Orchestrator is initialized in background; port 8080 opens immediately.
After init, PeerAutoConnect connects to all known static peers from config/peers.json.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Thread

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_boot_time = time.time()
_ready = False
_init_error = None
_core = None


def _report_codex_status() -> None:
    codex_home = Path(os.getenv("CODEX_HOME", "/codex-home"))
    workspace = Path(os.getenv("ARGOS_CODEX_WORKDIR", "/app"))
    auth_status = "ready" if (codex_home / "auth.json").is_file() else "missing"
    launcher_status = "ready" if Path("/usr/local/bin/codex-agent").is_file() else "missing"
    print(f"[CODEX] auth cache {auth_status}", flush=True)
    print(f"[CODEX] agent launcher {launcher_status}; workspace={workspace}", flush=True)


_report_codex_status()

# Lightweight app -- no heavy imports here
app = FastAPI(title="Argos Cloud", version="2.1.3")
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


@app.get("/health/details")
def health_details_endpoint():
    from src.health_details import get_cached_health_details

    return get_cached_health_details(
        ready=_ready,
        init_error=_init_error,
        boot_time=_boot_time,
        core=_core,
    )


@app.get("/")
def root():
    return {"service": "argos-core", "ready": _ready}


def _init_orchestrator():
    global _ready, _init_error, _core

    try:
        print("[CLOUD] Loading .env ...", flush=True)
        from dotenv import find_dotenv, load_dotenv
        env_path = find_dotenv(usecwd=True) or find_dotenv()
        if env_path:
            load_dotenv(env_path, override=True)

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
        _core = core

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
        # Mount at the application root so the MCP server's own /mcp route
        # is exposed publicly as /mcp rather than the accidental /mcp/mcp.
        # /health and / are declared above and therefore keep precedence.
        app.mount("/", mcp.app)

        _ready = True
        elapsed = time.time() - _boot_time
        print(f"[CLOUD] Argos ready! uptime={elapsed:.1f}s", flush=True)

        # ── P2P auto-connect to known peers ──────────────────────────────
        try:
            p2p = getattr(orchestrator.core, "p2p", None) if orchestrator.core else None
            if p2p:
                from src.connectivity.peer_autoconnect import start_autoconnect
                start_autoconnect(p2p)
                print("[CLOUD] P2P auto-connect started", flush=True)
            else:
                print("[CLOUD] P2P bridge not available, skipping auto-connect", flush=True)
        except Exception as p2p_exc:
            print(f"[CLOUD] P2P auto-connect warning: {p2p_exc}", flush=True)

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
