from __future__ import annotations

from types import SimpleNamespace

from src import health_details


def test_health_details_has_stable_live_telemetry_shape(monkeypatch):
    core = SimpleNamespace(
        p2p=SimpleNamespace(
            profile=SimpleNamespace(to_dict=lambda: {"node_id": "local"}),
            registry=SimpleNamespace(
                all=lambda: [
                    {"node_id": "peer-1", "last_seen": 100.0},
                    {"node_id": "peer-2", "last_seen": 101.0},
                ]
            ),
        ),
        inference_health=lambda: {
            "mode": "auto",
            "providers": {
                "Ollama": {
                    "configured": True,
                    "available": False,
                    "status": "unavailable",
                }
            },
        },
    )

    monkeypatch.setattr(
        health_details,
        "collect_system",
        lambda: {
            "available": True,
            "cpu": {"percent": 12.5},
            "ram": {"percent": 34.0},
            "disks": [{"mountpoint": "/", "percent": 40.0}],
            "gpus": [],
        },
    )
    monkeypatch.setattr(
        health_details,
        "collect_mempalace",
        lambda: {"available": True, "drawers": 42638, "wings": {"technical": 10}},
    )
    monkeypatch.setattr(
        health_details,
        "collect_container",
        lambda: {
            "available": True,
            "containerized": True,
            "railway": {"service_id": "service-id"},
            "volume": {"available": True, "writable": True},
            "docker": {"available": False, "reason": "socket_unavailable"},
        },
    )

    payload = health_details.build_health_details(
        ready=True,
        init_error=None,
        boot_time=90.0,
        core=core,
        now=100.0,
    )

    assert payload["status"] == "degraded"
    assert payload["service"]["ready"] is True
    assert payload["service"]["uptime_seconds"] == 10
    assert payload["nodes"]["available"] is True
    assert payload["nodes"]["online"] == 3
    assert payload["mempalace"]["drawers"] == 42638
    assert payload["system"]["cpu"]["percent"] == 12.5
    assert payload["inference"]["providers"]["Ollama"]["available"] is False
    assert payload["container"]["docker"]["available"] is False


def test_health_details_marks_missing_sources_unavailable(monkeypatch):
    monkeypatch.setattr(
        health_details, "collect_system", lambda: {"available": False, "reason": "missing"}
    )
    monkeypatch.setattr(
        health_details,
        "collect_mempalace",
        lambda: {"available": False, "reason": "chromadb_unavailable"},
    )
    monkeypatch.setattr(
        health_details,
        "collect_container",
        lambda: {"available": False, "reason": "unknown_runtime"},
    )

    payload = health_details.build_health_details(
        ready=False,
        init_error="boot failed",
        boot_time=99.0,
        core=None,
        now=100.0,
    )

    assert payload["status"] == "unhealthy"
    assert payload["nodes"] == {"available": False, "reason": "core_unavailable"}
    assert payload["inference"] == {
        "available": False,
        "reason": "core_unavailable",
    }
    assert payload["mempalace"]["available"] is False
    assert payload["system"]["available"] is False
    assert payload["container"]["available"] is False


def test_health_details_does_not_serialize_secret_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-leak")
    monkeypatch.setenv("ARGOS_INFERENCE_API_KEY", "also-do-not-leak")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "safe-service-id")
    monkeypatch.setattr(
        health_details, "collect_system", lambda: {"available": False, "reason": "test"}
    )
    monkeypatch.setattr(
        health_details,
        "collect_mempalace",
        lambda: {"available": False, "reason": "test"},
    )

    payload = health_details.build_health_details(
        ready=True,
        init_error=None,
        boot_time=0.0,
        core=None,
        now=1.0,
    )
    serialized = repr(payload)

    assert "do-not-leak" not in serialized
    assert "also-do-not-leak" not in serialized
