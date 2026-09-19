import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.mcp_api import ArgosMCPServer
from src.task_runtime import TaskRunner


def test_real_cloud_lifespan_closes_queued_work(tmp_path):
    path = Path(__file__).parents[1] / "cloud_entry.py"
    tree = ast.parse(path.read_text())
    method = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
    scope = {"asynccontextmanager": asynccontextmanager, "asyncio": asyncio}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), scope)
    runner = TaskRunner(tmp_path / "tasks.db", lambda text: "unused", autostart=False)
    task = runner.submit("must not run after stop")
    app = FastAPI(lifespan=scope["lifespan"])
    app.state.task_runner = runner
    with TestClient(app):
        assert runner.get(task["id"])["status"] == "queued"
    assert runner.get(task["id"])["status"] == "cancelled"


def test_mcp_command_uses_shared_persistent_task_queue(tmp_path):
    runner = TaskRunner(tmp_path / "tasks.db", lambda text: {"answer": "4", "execution_status": "succeeded"})
    server = ArgosMCPServer()
    server.task_runner = runner
    assert asyncio.run(server._run_command("2+2")) == "4"
    assert runner.list()[0]["status"] == "completed"
    runner.close()


def test_cancelled_agent_clears_running_flag(monkeypatch):
    from src.agent import ArgosAgent
    from src.task_control import TaskCancelled
    def cancelled(*args):
        raise TaskCancelled()
    agent = ArgosAgent(SimpleNamespace(process_logic=cancelled))
    agent._guard = SimpleNamespace(validate_step=lambda step: SimpleNamespace(allowed=True, sanitized=step))
    try:
        agent.execute_plan("first затем second", None, None)
    except TaskCancelled:
        pass
    else:
        raise AssertionError("Cancellation swallowed")
    assert not agent._running
