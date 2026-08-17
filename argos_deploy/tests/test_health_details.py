from __future__ import annotations

from types import SimpleNamespace

from src import health_details, mempalace_bridge


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


def test_health_details_sanitizes_public_runtime_values(monkeypatch):
    core = SimpleNamespace(
        p2p=SimpleNamespace(
            profile=SimpleNamespace(
                to_dict=lambda: {"hostname": "private-local", "role": "core"}
            ),
            registry=SimpleNamespace(
                all=lambda: [
                    {"hostname": "private-peer", "role": "gpu", "last_seen": 10}
                ]
            ),
        ),
        inference_health=lambda: {
            "available": True,
            "mode": "auto",
            "selected": "CloudFallback",
            "providers": {
                "CloudFallback": {
                    "configured": True,
                    "available": True,
                    "status": "available",
                    "endpoint": "http://private-inference.internal/v1",
                }
            },
        },
    )
    monkeypatch.setattr(
        health_details,
        "collect_system",
        lambda: {"available": True, "cpu": {}, "ram": {}, "disks": [], "gpus": []},
    )
    monkeypatch.setattr(
        health_details,
        "collect_mempalace",
        lambda: {"available": True, "drawers": 42},
    )
    monkeypatch.setattr(
        health_details,
        "collect_container",
        lambda: {"available": True, "docker": {"available": True}},
    )

    payload = health_details.build_health_details(
        ready=False,
        init_error="secret path /app/persist failed",
        boot_time=0.0,
        core=core,
        now=1.0,
    )
    serialized = repr(payload)

    assert payload["service"]["error"] == "initialization_failed"
    assert "private-local" not in serialized
    assert "private-peer" not in serialized
    assert "private-inference.internal" not in serialized


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


def test_public_container_health_omits_internal_ids_paths_and_images(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "private-project-id")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "private-service-id")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "private-environment-id")
    monkeypatch.setenv("RAILWAY_RUN_UID", "private-run-id")
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "argos-full")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    monkeypatch.setattr(
        health_details,
        "_docker_state",
        lambda: {
            "available": True,
            "running": 1,
            "services": [{"name": "argos", "state": "running"}],
        },
    )

    container = health_details.collect_container()
    serialized = repr(container)

    assert container["railway"] == {"managed": True}
    assert container["volume"] == {
        "available": True,
        "configured": True,
        "writable": True,
    }
    assert "private-project-id" not in serialized
    assert "private-service-id" not in serialized
    assert "private-environment-id" not in serialized
    assert "private-run-id" not in serialized
    assert str(tmp_path) not in serialized
    assert "image" not in serialized.lower()


def test_mempalace_health_uses_only_constant_time_drawer_count(monkeypatch):
    class FakeCollection:
        def count(self):
            return 42638

        def get(self, *args, **kwargs):
            raise AssertionError("health telemetry must not enumerate metadata")

    monkeypatch.setattr(mempalace_bridge, "_mp_ok", True)
    monkeypatch.setattr(mempalace_bridge, "_collection", FakeCollection())

    assert mempalace_bridge.get_health_details() == {
        "available": True,
        "enabled": True,
        "drawers": 42638,
    }


def test_health_cache_reuses_sanitized_initialization_failure(monkeypatch):
    calls = []

    def fake_build(**kwargs):
        calls.append(kwargs)
        return {
            "service": {
                "ready": False,
                "error": "initialization_failed",
            }
        }

    monkeypatch.setattr(health_details, "build_health_details", fake_build)
    collector = health_details.HealthDetailsCollector(ttl_seconds=60)
    args = {
        "ready": False,
        "init_error": "private path /app/persist",
        "boot_time": 0.0,
        "core": None,
    }

    first = collector.get(**args)
    second = collector.get(**args)

    assert first is second
    assert len(calls) == 1
