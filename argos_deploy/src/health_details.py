"""Bounded, secret-free runtime telemetry for the cloud health endpoint."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def collect_nodes(core: Any) -> dict[str, Any]:
    if core is None:
        return _unavailable("core_unavailable")
    bridge = getattr(core, "p2p", None)
    if bridge is None:
        return _unavailable("p2p_unavailable")

    try:
        registry = getattr(bridge, "registry", None)
        peers = list(registry.all()) if registry and hasattr(registry, "all") else []
        local_profile = getattr(bridge, "profile", None)
        local = (
            local_profile.to_dict()
            if local_profile is not None and hasattr(local_profile, "to_dict")
            else {}
        )

        safe_peers = []
        for peer in peers:
            if not isinstance(peer, dict):
                continue
            power = peer.get("power")
            safe_peers.append(
                {
                    "hostname": peer.get("hostname"),
                    "role": peer.get("role"),
                    "last_seen": peer.get("last_seen"),
                    "age_days": peer.get("age_days"),
                    "power": (
                        power.get("index")
                        if isinstance(power, dict)
                        else None
                    ),
                }
            )

        return {
            "available": True,
            "online": len(safe_peers) + 1,
            "peers_online": len(safe_peers),
            "local": {
                "hostname": local.get("hostname"),
                "role": local.get("role"),
                "age_days": local.get("age_days"),
                "power": (
                    local.get("power", {}).get("index")
                    if isinstance(local.get("power"), dict)
                    else None
                ),
            },
            "peers": safe_peers,
        }
    except Exception as exc:
        return _unavailable(f"p2p_error:{type(exc).__name__}")


def collect_mempalace() -> dict[str, Any]:
    try:
        from src import mempalace_bridge

        details = mempalace_bridge.get_health_details()
        if isinstance(details, dict):
            return details
        return _unavailable("invalid_mempalace_status")
    except Exception as exc:
        return _unavailable(f"mempalace_error:{type(exc).__name__}")


def collect_system() -> dict[str, Any]:
    try:
        from src.connectivity import system_health

        return {
            "available": True,
            "cpu": system_health.get_cpu(),
            "ram": system_health.get_ram(),
            "disks": system_health.get_disks(),
            "gpus": system_health.get_gpu(),
        }
    except Exception as exc:
        return _unavailable(f"system_error:{type(exc).__name__}")


def _railway_metadata() -> dict[str, str | None]:
    safe_names = {
        "project_name": "RAILWAY_PROJECT_NAME",
        "service_name": "RAILWAY_SERVICE_NAME",
        "environment_name": "RAILWAY_ENVIRONMENT_NAME",
        "public_domain": "RAILWAY_PUBLIC_DOMAIN",
    }
    return {label: os.getenv(env_name) for label, env_name in safe_names.items()}


def _docker_state() -> dict[str, Any]:
    socket_path = Path("/var/run/docker.sock")
    docker_cmd = shutil.which("docker")
    if not socket_path.exists():
        return _unavailable("socket_unavailable")
    if not docker_cmd:
        return _unavailable("cli_unavailable")

    try:
        result = subprocess.run(
            [
                docker_cmd,
                "ps",
                "--format",
                "{{json .}}",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0:
            return _unavailable(f"docker_ps_exit_{result.returncode}")

        services = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            services.append(
                {
                    "name": item.get("Names"),
                    "state": item.get("State"),
                    "status": item.get("Status"),
                }
            )
        return {
            "available": True,
            "services": services,
            "running": sum(
                1 for item in services if item.get("state") == "running"
            ),
        }
    except Exception as exc:
        return _unavailable(f"docker_error:{type(exc).__name__}")


def collect_container() -> dict[str, Any]:
    volume_value = (
        os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        or os.getenv("ARGOS_STATE_ROOT")
        or ""
    )
    volume_path = Path(volume_value) if volume_value else None
    if volume_path is None:
        volume = _unavailable("volume_not_configured")
    else:
        exists = volume_path.exists()
        volume = {
            "available": exists,
            "configured": True,
            "writable": exists and os.access(volume_path, os.W_OK),
        }

    containerized = Path("/.dockerenv").exists()
    if not containerized:
        try:
            containerized = "docker" in Path("/proc/1/cgroup").read_text(
                encoding="utf-8", errors="ignore"
            ).lower()
        except Exception:
            containerized = False

    return {
        "available": True,
        "containerized": containerized,
        "railway": _railway_metadata(),
        "volume": volume,
        "docker": _docker_state(),
    }


def _collect_inference(core: Any) -> dict[str, Any]:
    if core is None:
        return _unavailable("core_unavailable")
    getter = getattr(core, "inference_health", None)
    if not callable(getter):
        return _unavailable("inference_health_unavailable")
    try:
        details = getter()
        if not isinstance(details, dict):
            return _unavailable("invalid_inference_status")
        if "available" not in details:
            providers = details.get("providers", {})
            details = {
                **details,
                "available": any(
                    isinstance(item, dict)
                    and item.get("available") is True
                    for item in providers.values()
                ),
            }
        return details
    except Exception as exc:
        return _unavailable(f"inference_error:{type(exc).__name__}")


def build_health_details(
    *,
    ready: bool,
    init_error: str | None,
    boot_time: float,
    core: Any,
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    nodes = collect_nodes(core)
    mempalace = collect_mempalace()
    system = collect_system()
    inference = _collect_inference(core)
    container = collect_container()

    if init_error or not ready:
        status = "unhealthy"
    elif any(
        item.get("available") is False
        for item in (nodes, mempalace, system, inference, container)
    ):
        status = "degraded"
    elif (
        isinstance(container.get("docker"), dict)
        and container["docker"].get("available") is False
    ):
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "timestamp": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "service": {
            "ready": bool(ready),
            "uptime_seconds": max(0, int(now - boot_time)),
            "error": init_error,
        },
        "nodes": nodes,
        "mempalace": mempalace,
        "system": system,
        "inference": inference,
        "container": container,
    }


class HealthDetailsCollector:
    """Small TTL cache so GPU and psutil probes do not block every request."""

    def __init__(self, ttl_seconds: float = 15.0):
        self.ttl_seconds = max(1.0, ttl_seconds)
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached: dict[str, Any] | None = None

    def get(
        self,
        *,
        ready: bool,
        init_error: str | None,
        boot_time: float,
        core: Any,
    ) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            if (
                self._cached is not None
                and now - self._cached_at < self.ttl_seconds
                and self._cached.get("service", {}).get("ready") == bool(ready)
                and self._cached.get("service", {}).get("error") == init_error
            ):
                return self._cached
            self._cached = build_health_details(
                ready=ready,
                init_error=init_error,
                boot_time=boot_time,
                core=core,
                now=now,
            )
            self._cached_at = now
            return self._cached


def _configured_ttl() -> float:
    try:
        return float(os.getenv("ARGOS_HEALTH_DETAILS_TTL", "15") or "15")
    except ValueError:
        return 15.0


_DEFAULT_COLLECTOR = HealthDetailsCollector(ttl_seconds=_configured_ttl())


def get_cached_health_details(
    *,
    ready: bool,
    init_error: str | None,
    boot_time: float,
    core: Any,
) -> dict[str, Any]:
    return _DEFAULT_COLLECTOR.get(
        ready=ready,
        init_error=init_error,
        boot_time=boot_time,
        core=core,
    )
