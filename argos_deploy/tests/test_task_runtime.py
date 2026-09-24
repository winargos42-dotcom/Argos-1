import threading
import time

import pytest

from src.task_runtime import TaskRunner
from src.task_control import checkpoint, emit_chunk


def wait_for(runner, task_id, states):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = runner.get(task_id)
        if result["status"] in states:
            return result
        time.sleep(0.01)
    pytest.fail(f"Task did not reach {states}: {runner.get(task_id)}")


def test_result_and_history_survive_restart(tmp_path):
    path = tmp_path / "tasks.db"
    runner = TaskRunner(path, lambda text: {"answer": "4", "execution_status": "succeeded"})
    task = runner.submit("2+2")
    result = wait_for(runner, task["id"], {"completed"})
    assert result["answer"] == "4"
    assert result["execution_status"] == "succeeded"
    runner.close()
    restarted = TaskRunner(path, lambda text: pytest.fail("Completed task replayed"))
    assert restarted.get(task["id"])["answer"] == "4"
    restarted.close()


def test_cancel_queued_never_executes_and_running_waits_for_ack(tmp_path):
    started = threading.Event()
    release = threading.Event()
    executed = []
    def execute(text):
        executed.append(text)
        started.set()
        release.wait(3)
        checkpoint()
        return "should not return"
    runner = TaskRunner(tmp_path / "tasks.db", execute)
    first = runner.submit("first")
    assert started.wait(2)
    second = runner.submit("second")
    assert runner.cancel(second["id"])["status"] == "cancelled"
    assert runner.cancel(first["id"])["status"] == "cancelling"
    release.set()
    wait_for(runner, first["id"], {"cancelled"})
    runner.close()
    assert executed == ["first"]


def test_cancel_does_not_claim_rollback_when_action_already_finished(tmp_path):
    started, release = threading.Event(), threading.Event()
    def execute(text):
        started.set()
        release.wait(3)
        return {"answer": "already written", "execution_status": "succeeded"}
    runner = TaskRunner(tmp_path / "tasks.db", execute)
    task = runner.submit("write")
    assert started.wait(2)
    runner.cancel(task["id"])
    release.set()
    result = wait_for(runner, task["id"], {"completed"})
    assert result["cancel_requested"]
    assert result["execution_status"] == "succeeded"
    runner.close()


def test_chunks_and_errors_are_visible(tmp_path):
    def execute(text):
        emit_chunk("часть ")
        emit_chunk("ответа")
        return "Ошибка: synthetic failure"
    runner = TaskRunner(tmp_path / "tasks.db", execute)
    task = runner.submit("question")
    result = wait_for(runner, task["id"], {"failed"})
    assert result["partial_answer"] == "часть ответа"
    assert result["answer"].startswith("Ошибка")
    assert result["version"] > task["version"]
    runner.close()


def test_unknown_and_invalid_requests(tmp_path):
    runner = TaskRunner(tmp_path / "tasks.db", lambda text: text)
    assert runner.get("missing") is None
    assert runner.cancel("missing") is None
    for text in ("", "  ", "a" * 24001):
        with pytest.raises(ValueError):
            runner.submit(text)
    runner.close()
    with pytest.raises(RuntimeError):
        runner.submit("after close")


def test_unfinished_tasks_are_interrupted_without_replay(tmp_path):
    path = tmp_path / "tasks.db"
    runner = TaskRunner(path, lambda text: "unused", autostart=False)
    task = runner.submit("queued before crash")
    restarted = TaskRunner(path, lambda text: pytest.fail("Unsafe replay"))
    assert restarted.get(task["id"])["status"] == "interrupted"
    restarted.close()
    runner.close()


def test_refuses_unrelated_database(tmp_path):
    import sqlite3
    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE valuable(data TEXT)")
    with pytest.raises(ValueError):
        TaskRunner(path, lambda text: text)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("valuable",)]
