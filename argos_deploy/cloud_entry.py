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


@app.get("/")
def root():
    return {"service": "argos-core", "ready": _ready}


def _init_orchestrator():
    global _ready, _init_error

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
        from src.mcp_api import ArgosMCPServer

        orchestrator = ArgosOrchestrator()
        core  = getattr(orchestrator, "core",  None)
        admin = getattr(orchestrator, "admin", None)

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
