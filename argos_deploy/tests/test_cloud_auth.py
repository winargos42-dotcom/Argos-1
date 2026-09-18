import ast
import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


ENTRYPOINT = Path(__file__).resolve().parents[1] / "cloud_entry.py"
TEST_KEY = "cloud-access-test-key"


@pytest.fixture(autouse=True)
def clear_cloud_key(monkeypatch):
    monkeypatch.delenv("ARGOS_MCP_API_KEY", raising=False)


@pytest.fixture
def auth_module():
    module = importlib.import_module("src.cloud_auth")
    assert Path(module.__file__).resolve() == ENTRYPOINT.parent / "src" / "cloud_auth.py"
    return module


def _cloud_app_before_initializer():
    tree = ast.parse(ENTRYPOINT.read_text(encoding="utf-8"))
    bootstrap = []
    found_initializer = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_init_orchestrator":
            found_initializer = True
            break
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "_report_codex_status"
        ):
            continue
        bootstrap.append(node)
    assert found_initializer, "Cloud runtime boundary was not found; refusing to execute bootstrap"
    namespace = {"__name__": "_cloud_auth_bootstrap_test", "__file__": str(ENTRYPOINT)}
    exec(compile(ast.Module(body=bootstrap, type_ignores=[]), str(ENTRYPOINT), "exec"), namespace)
    return namespace["app"]


def test_cloud_entry_installs_guard_before_runtime_initialization(monkeypatch, auth_module):
    monkeypatch.setenv("ARGOS_MCP_API_KEY", TEST_KEY)
    app = _cloud_app_before_initializer()
    dispatched = []
    mcp = FastAPI()

    @mcp.post("/mcp")
    def protected():
        dispatched.append(True)
        return {"ok": True}

    app.mount("/", mcp)
    with TestClient(app) as client:
        preflight = client.options("/mcp", headers={
            "Origin": "https://example.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        })
        assert preflight.status_code == 200
        assert dispatched == []
        rejected = client.post("/mcp", json={"method": "tools/call"})
        assert rejected.status_code == 401
        assert dispatched == []
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200
        accepted = client.post("/mcp", headers={"Authorization": f"Bearer {TEST_KEY}"})
        assert accepted.status_code == 200
        assert dispatched == [True]


@pytest.mark.parametrize("entrypoint_name", ["cloud_entry.py", "main.py"])
def test_entrypoint_dotenv_preserves_deployed_environment(monkeypatch, tmp_path, entrypoint_name):
    from dotenv import load_dotenv

    monkeypatch.setenv("ARGOS_MCP_API_KEY", TEST_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", "deployed-provider-test-key")
    fixture_env = tmp_path / "fixture.env"
    fixture_env.write_text(
        "ARGOS_MCP_API_KEY=old-cloud-test-key\nOPENAI_API_KEY=old-provider-test-key\n",
        encoding="utf-8",
    )
    entrypoint = ENTRYPOINT.parent / entrypoint_name
    tree = ast.parse(entrypoint.read_text(encoding="utf-8"))
    dotenv_call = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "load_dotenv"
    )
    statement = ast.fix_missing_locations(ast.Module(body=[ast.Expr(value=dotenv_call)], type_ignores=[]))
    exec(compile(statement, str(entrypoint), "exec"), {
        "load_dotenv": load_dotenv,
        "env_path": str(fixture_env),
        "_env_path": str(fixture_env),
    })
    import os

    assert os.environ["ARGOS_MCP_API_KEY"] == TEST_KEY
    assert os.environ["OPENAI_API_KEY"] == "deployed-provider-test-key"


@pytest.fixture
def guarded_app(auth_module):
    app = FastAPI()
    app.add_middleware(auth_module.CloudBearerAuthMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    dispatched = []

    @app.get("/")
    @app.get("/health")
    def public():
        return {"ok": True}

    @app.api_route("/mcp", methods=["GET", "POST", "OPTIONS"])
    def protected():
        dispatched.append("http")
        return {"ok": True}

    @app.websocket("/socket")
    @app.websocket("/health")
    async def socket(websocket: WebSocket):
        dispatched.append("websocket")
        await websocket.accept()
        await websocket.send_text("accepted")
        await websocket.close()

    return app, dispatched


@pytest.mark.parametrize("authorization", [
    None,
    "Basic cloud-access-test-key",
    "Bearer",
    "Bearer ",
    "Bearer wrong-key",
    "Bearer cloud-access-test-key extra",
    "Bearer\tcloud-access-test-key",
    " Bearer cloud-access-test-key",
    "Bearer cloud-access-test-key, Bearer cloud-access-test-key",
])
def test_invalid_authorization_never_dispatches(guarded_app, monkeypatch, authorization):
    monkeypatch.setenv("ARGOS_MCP_API_KEY", TEST_KEY)
    app, dispatched = guarded_app
    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(app) as client:
        response = client.post("/mcp", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert TEST_KEY not in response.text
    assert dispatched == []


@pytest.mark.parametrize("headers", [
    [("Authorization", f"Bearer {TEST_KEY}"), ("authorization", f"Bearer {TEST_KEY}")],
    [("Authorization", "Bearer wrong"), ("Authorization", f"Bearer {TEST_KEY}")],
    [("Authorization", f"Bearer {TEST_KEY}"), ("Authorization", "Bearer wrong")],
])
def test_duplicate_authorization_is_rejected(guarded_app, monkeypatch, headers):
    monkeypatch.setenv("ARGOS_MCP_API_KEY", TEST_KEY)
    app, dispatched = guarded_app
    with TestClient(app) as client:
        response = client.post("/mcp", headers=headers)
    assert response.status_code == 401
    assert dispatched == []


@pytest.mark.parametrize("key", [None, "", "   "])
def test_unconfigured_access_fails_closed_but_status_stays_public(guarded_app, monkeypatch, key):
    if key is not None:
        monkeypatch.setenv("ARGOS_MCP_API_KEY", key)
    app, dispatched = guarded_app
    with TestClient(app) as client:
        response = client.post("/mcp", headers={"Authorization": f"Bearer {TEST_KEY}"})
        assert response.status_code == 503
        assert client.get("/").status_code == 200
        assert client.get("/health").status_code == 200
    assert dispatched == []


@pytest.mark.parametrize("scheme", ["Bearer ", "bearer ", "BEARER  "])
def test_valid_bearer_dispatches(guarded_app, monkeypatch, scheme):
    monkeypatch.setenv("ARGOS_MCP_API_KEY", TEST_KEY)
    app, dispatched = guarded_app
    with TestClient(app) as client:
        response = client.post("/mcp", headers={"Authorization": f"{scheme}{TEST_KEY}"})
    assert response.status_code == 200
    assert dispatched == ["http"]


@pytest.mark.parametrize("method,path", [
    ("GET", "/docs"),
    ("GET", "/openapi.json"),
    ("GET", "/health/internal"),
    ("POST", "/health"),
    ("POST", "/"),
    ("OPTIONS", "/mcp"),
])
def test_only_exact_status_reads_are_public(guarded_app, monkeypatch, method, path):
    monkeypatch.setenv("ARGOS_MCP_API_KEY", TEST_KEY)
    app, dispatched = guarded_app
    with TestClient(app) as client:
        response = client.request(method, path)
    assert response.status_code == 401
    assert dispatched == []


@pytest.mark.parametrize("key,authorization,path", [
    (None, None, "/socket"),
    (TEST_KEY, None, "/socket"),
    (TEST_KEY, "Bearer wrong", "/socket"),
    (TEST_KEY, None, "/health"),
])
def test_websocket_auth_is_required_before_dispatch(guarded_app, monkeypatch, key, authorization, path):
    if key is not None:
        monkeypatch.setenv("ARGOS_MCP_API_KEY", key)
    app, dispatched = guarded_app
    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(path, headers=headers):
                pytest.fail("Unauthenticated websocket was accepted")
    assert error.value.code == 1008
    assert dispatched == []


def test_valid_bearer_allows_websocket(guarded_app, monkeypatch):
    monkeypatch.setenv("ARGOS_MCP_API_KEY", TEST_KEY)
    app, dispatched = guarded_app
    with TestClient(app) as client:
        with client.websocket_connect("/socket", headers={"Authorization": f"Bearer {TEST_KEY}"}) as websocket:
            assert websocket.receive_text() == "accepted"
    assert dispatched == ["websocket"]
