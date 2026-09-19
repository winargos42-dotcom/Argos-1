import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.cloud_auth import CloudBearerAuthMiddleware
from src.control_api import create_control_router, panel_response
from src.task_runtime import TaskRunner


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGOS_MCP_API_KEY", "test-control-key")
    monkeypatch.setenv("ARGOS_MEMPALACE_FACTS_PATH", str(tmp_path / "facts.db"))
    monkeypatch.delenv("ARGOS_MEMPALACE_SQLITE_PATH", raising=False)
    monkeypatch.delenv("ARGOS_MEMPALACE_INDEX_PATH", raising=False)
    runner = TaskRunner(tmp_path / "tasks.db", lambda text: {"answer": "4", "execution_status": "succeeded"})
    app = FastAPI()
    app.add_middleware(CloudBearerAuthMiddleware)
    app.add_api_route("/ui", panel_response, methods=["GET"])
    app.include_router(create_control_router(runner, lambda: {"ready": True}))
    with TestClient(app) as client:
        yield client
    runner.close()


AUTH = {"Authorization": "Bearer test-control-key"}


def test_static_panel_public_but_state_and_mutations_protected(panel):
    page = panel.get("/ui")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "test-control-key" not in page.text
    for path in ("/api/tasks", "/api/status", "/api/memory/search?q=test"):
        assert panel.get(path).status_code == 401
    assert panel.post("/api/tasks", json={"text": "2+2"}).status_code == 401
    assert panel.post("/api/memory/facts", json={"text": "remember"}).status_code == 401


def test_submit_stream_read_result_and_terminal_cancel(panel):
    response = panel.post("/api/tasks", json={"text": "2+2"}, headers=AUTH)
    assert response.status_code == 202
    task_id = response.json()["id"]
    stream = panel.get(f"/api/tasks/{task_id}/events", headers=AUTH)
    assert stream.status_code == 200
    assert "text/event-stream" in stream.headers["content-type"]
    assert '"completed"' in stream.text
    result = panel.get(f"/api/tasks/{task_id}", headers=AUTH).json()
    assert result["answer"] == "4"
    assert result["execution_status"] == "succeeded"
    assert panel.post(f"/api/tasks/{task_id}/cancel", headers=AUTH).json()["status"] == "completed"
    assert panel.get("/api/tasks", headers=AUTH).json()[0]["id"] == task_id


def test_invalid_unknown_and_status(panel):
    assert panel.get("/api/status", headers=AUTH).json()["ready"]
    assert panel.post("/api/tasks", json={"text": " "}, headers=AUTH).status_code == 422
    assert panel.post("/api/tasks", json={"text": 123}, headers=AUTH).status_code == 422
    assert panel.get("/api/tasks/missing", headers=AUTH).status_code == 404
    assert panel.post("/api/tasks/missing/cancel", headers=AUTH).status_code == 404
    assert panel.get("/api/tasks/missing/events", headers=AUTH).status_code == 404


def test_memory_save_and_find_have_provenance(panel):
    saved = panel.post("/api/memory/facts", json={"text": "Синтетическая память quasar297"}, headers=AUTH)
    assert saved.status_code == 201
    results = panel.get("/api/memory/search?q=quasar297", headers=AUTH).json()
    assert len(results) == 1
    assert results[0]["origin"] == "fact"
    assert results[0]["id"] == saved.json()["id"]
    assert "quasar297" in results[0]["text"]
