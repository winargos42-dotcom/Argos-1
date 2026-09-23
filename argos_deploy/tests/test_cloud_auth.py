"""Exercise actual cloud bootstrap and ASGI authorization without starting providers."""
import importlib.util
import asyncio
import sys
import threading
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
import httpx


class TestClient:
    """In-process ASGI transport: no background portal thread or network socket."""
    __test__ = False

    def __init__(self, app):
        self.app = app

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def request(self, method, path, **kwargs):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cloud(monkeypatch):
    # The real entrypoint otherwise starts provider initialization at import time.
    package = types.ModuleType("src")
    package.__path__ = [str(ROOT / "src")]
    monkeypatch.setitem(sys.modules, "src", package)
    for name in ("src.cloud_auth", "src.argos_network"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setenv("ARGOS_MCP_API_KEY", "owner-test-key")
    monkeypatch.setenv("ARGOS_NETWORK_KEY", "network-test-key")
    spec = importlib.util.spec_from_file_location("cloud_entry_auth_test", ROOT / "cloud_entry.py")
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as import_patch:
        import_patch.setattr(threading.Thread, "start", lambda self: None)
        spec.loader.exec_module(module)
    # Protected echo exposes the middleware decision without core command execution.
    @module.app.api_route("/mcp", methods=["GET", "POST"])
    async def owner_echo():
        return {"owner": True}
    @module.app.get("/networking")
    async def similar_prefix():
        return {"owner": True}
    return module


def test_owner_api_rejects_missing_and_network_keys(cloud):
    with TestClient(cloud.app) as client:
        assert client.post("/mcp").status_code == 401
        assert client.post("/mcp", headers={"Authorization": "Bearer network-test-key"}).status_code == 401
        assert client.post("/mcp", headers={"Authorization": "Bearer owner-test-key"}).status_code == 200


def test_network_key_is_separate_and_prefix_is_exact(cloud):
    with TestClient(cloud.app) as client:
        for path in ("/network", "/network/status", "/network/not-found"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer owner-test-key"}).status_code == 401
        assert client.get("/network/status", headers={"Authorization": "Bearer network-test-key"}).status_code == 200
        assert client.get("/networking", headers={"Authorization": "Bearer network-test-key"}).status_code == 401
        assert client.get("/networking", headers={"Authorization": "Bearer owner-test-key"}).status_code == 200


def test_absent_keys_fail_closed_independently(cloud, monkeypatch):
    with TestClient(cloud.app) as client:
        monkeypatch.delenv("ARGOS_NETWORK_KEY")
        assert client.get("/network/status", headers={"Authorization": "Bearer owner-test-key"}).status_code == 503
        assert client.get("/mcp", headers={"Authorization": "Bearer owner-test-key"}).status_code == 200
        monkeypatch.delenv("ARGOS_MCP_API_KEY")
        assert client.get("/mcp").status_code == 503
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200


def test_malformed_duplicate_credentials_rejected(cloud):
    with TestClient(cloud.app) as client:
        for headers in ([('Authorization', 'Bearer owner-test-key'), ('Authorization', 'Bearer owner-test-key')],
                        {'Authorization': 'Basic owner-test-key'},
                        {'Authorization': 'Bearer owner-test-key extra'}):
            assert client.get("/mcp", headers=headers).status_code == 401


def test_real_bootstrap_preserves_env_and_network_route_before_mount(cloud, monkeypatch, tmp_path):
    # Only provider/persistence boundaries are substituted: actual bootstrap, dotenv,
    # middleware and network route installation execute unchanged.
    env = tmp_path / ".env"
    env.write_text("ARGOS_MCP_API_KEY=dotenv-owner\nARGOS_NETWORK_KEY=dotenv-network\n")
    import dotenv
    monkeypatch.setattr(dotenv, "find_dotenv", lambda **kwargs: str(env))
    modules = {
        "src.persistent_state": {"prepare_persistent_state": lambda *args: {}},
        "src.external_guard_runtime": {"install_external_action_guard": lambda core: None},
        "main": {"ArgosOrchestrator": lambda: types.SimpleNamespace(core=None, admin=None)},
        "src.mcp_api": {"ArgosMCPServer": lambda **kwargs: types.SimpleNamespace(app=FastAPI())},
    }
    for name, attributes in modules.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, mod)
    cloud._init_orchestrator()
    assert cloud._ready is True, cloud._init_error
    with TestClient(cloud.app) as client:
        response = client.get("/network/status", headers={"Authorization": "Bearer network-test-key"})
        assert response.status_code == 200
        assert response.json()["network_id"] == "argos-recovery"
        assert client.get("/mcp", headers={"Authorization": "Bearer owner-test-key"}).status_code == 200


def test_public_read_methods_and_websocket_auth_boundary(cloud):
    from src.cloud_auth import CloudBearerAuthMiddleware

    async def probe(path, scope_type="http", method="GET", credential=None):
        passed = []
        sent = []
        async def downstream(scope, receive, send):
            passed.append(True)
        async def receive():
            return {"type": "http.request", "body": b""}
        async def send(message):
            sent.append(message)
        headers = [] if credential is None else [(b"authorization", credential)]
        await CloudBearerAuthMiddleware(downstream)(
            {"type": scope_type, "method": method, "path": path, "headers": headers}, receive, send)
        return bool(passed), sent

    for path in ("/", "/health", "/ui"):
        for method in ("GET", "HEAD"):
            assert asyncio.run(probe(path, method=method))[0]
        assert not asyncio.run(probe(path, method="POST"))[0]
    passed, sent = asyncio.run(probe("/network/ws", "websocket"))
    assert not passed and sent == [{"type": "websocket.close", "code": 1008}]
    assert asyncio.run(probe("/network/ws", "websocket", credential=b"Bearer network-test-key"))[0]
    assert not asyncio.run(probe("/mcp", "websocket", credential=b"Bearer network-test-key"))[0]
