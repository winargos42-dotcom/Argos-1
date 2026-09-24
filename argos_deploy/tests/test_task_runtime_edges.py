import threading
import time

import pytest

from src.task_runtime import TaskRunner
from src.task_control import emit_chunk


def finished(runner, ident):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        task = runner.get(ident)
        if task["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        time.sleep(0.01)
    pytest.fail("Task did not finish")


def test_shutdown_cancels_entire_queue_above_list_page(tmp_path):
    runner = TaskRunner(tmp_path / "tasks.sqlite3", lambda text: pytest.fail("Unexpected execution"),
                        autostart=False, max_pending=256)
    identifiers = [runner.submit(f"synthetic {i}")["id"] for i in range(201)]
    runner.close()
    assert all(runner.get(ident)["status"] == "cancelled" for ident in identifiers)


def test_worker_survives_execution_error_and_preserves_partial_on_restart(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    def execute(text):
        if text == "fail":
            emit_chunk("synthetic partial")
            raise RuntimeError("synthetic failure")
        return {"answer": "4", "execution_status": "succeeded"}
    runner = TaskRunner(path, execute)
    try:
        first = runner.submit("fail")
        second = runner.submit("2+2")
        assert finished(runner, first["id"])["status"] == "failed"
        assert finished(runner, second["id"])["execution_status"] == "succeeded"
    finally:
        runner.close()
    restarted = TaskRunner(path, lambda text: pytest.fail("Unexpected replay"), autostart=False)
    try:
        assert restarted.get(first["id"])["partial_answer"] == "synthetic partial"
        assert restarted.get(second["id"])["answer"] == "4"
    finally:
        restarted.close()


def test_oversize_final_answer_is_not_silent_success(tmp_path):
    runner = TaskRunner(tmp_path / "tasks.sqlite3", lambda text:
        {"answer": "x" * 65537, "execution_status": "succeeded"})
    try:
        task = runner.submit("synthetic")
        result = finished(runner, task["id"])
        assert result["status"] == "failed"
        assert result["execution_status"] == "failed"
        assert result["answer"] != "x" * 65536
    finally:
        runner.close()


@pytest.mark.parametrize("bound", [0, -1, 257])
def test_invalid_queue_limit_is_rejected(tmp_path, bound):
    with pytest.raises(ValueError):
        TaskRunner(tmp_path / "tasks.sqlite3", lambda text: text, max_pending=bound, autostart=False)


def test_queue_full_does_not_persist_unaccepted_task(tmp_path):
    runner = TaskRunner(tmp_path / "tasks.sqlite3", lambda text: text, max_pending=1, autostart=False)
    try:
        task = runner.submit("accepted")
        with pytest.raises(RuntimeError):
            runner.submit("not accepted")
        assert [row["id"] for row in runner.list()] == [task["id"]]
    finally:
        runner.close()
