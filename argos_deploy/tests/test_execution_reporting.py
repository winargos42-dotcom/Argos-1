from types import SimpleNamespace

import pytest

from src.agent import ArgosAgent
from src.execution_outcome import classify_execution
from src.tool_calling import ArgosToolCallingEngine


@pytest.mark.parametrize("result,status", [
    (None, "failed"), ("", "failed"), ("Ошибка создания файла: denied", "failed"),
    ("Запрос не обработан: превышен допустимый объём контекста модели.", "failed"),
    ("❌ Не найден", "failed"), ("✅ Готово", "unverified"),
    ({"answer": "Готово", "state": "Direct"}, "unverified"),
    ({"answer": "Готово", "execution_status": "succeeded"}, "succeeded"),
    ({"answer": "Готово", "execution_status": "invented"}, "unverified"),
    ({"answer": None, "execution_status": "succeeded"}, "failed"),
    ({"answer": "   ", "execution_status": "succeeded"}, "failed"),
    ({"answer": "Ошибка записи", "execution_status": "succeeded"}, "failed"),
    ({"answer": "❌ Не найден", "execution_status": "succeeded"}, "failed"),
    ({"answer": "Готово", "execution_status": "failed"}, "failed"),
])
def test_classification_requires_explicit_success(result, status):
    assert classify_execution(result)[1] == status


def agent_with_results(monkeypatch, results):
    pending = iter(results)
    core = SimpleNamespace(process_logic=lambda *args: next(pending),
                           process=lambda *args: next(pending))
    agent = ArgosAgent(core)
    agent._guard = SimpleNamespace(validate_step=lambda step:
        SimpleNamespace(allowed=True, sanitized=step))
    monkeypatch.setattr("src.agent.time.sleep", lambda _: None)
    return agent


def test_mixed_plan_counts_confirmed_failed_and_unverified(monkeypatch):
    agent = agent_with_results(monkeypatch, [
        {"answer": "created", "execution_status": "succeeded"},
        "Ошибка записи", {"answer": "Готово", "state": "Ollama"},
    ])
    report = agent.execute_plan("one затем two затем three", None, None)
    assert [r["status"] for r in agent._results] == ["succeeded", "failed", "unverified"]
    assert [r["ok"] for r in agent._results] == [True, False, False]
    assert "ПЛАН ВЫПОЛНЕН" not in report


def test_chain_counts_only_confirmed_success(monkeypatch):
    agent = agent_with_results(monkeypatch, [
        {"answer": "done", "execution_status": "succeeded"}, None, "Готово",
    ])
    agent._task_chain = ["one", "two", "three"]
    agent._running = True
    agent._execute_chain(None)
    assert agent._report["tasks_done"] == 1
    assert agent._report["errors"] == 1
    assert agent._report["unverified"] == 1


def test_all_confirmed_steps_report_completion(monkeypatch):
    agent = agent_with_results(monkeypatch, [
        {"answer": "done", "execution_status": "succeeded"},
        {"answer": "done", "execution_status": "succeeded"},
    ])
    assert "ПЛАН ВЫПОЛНЕН" in agent.execute_plan("one затем two", None, None)


def test_guard_block_is_failed(monkeypatch):
    agent = agent_with_results(monkeypatch, [])
    agent._guard = SimpleNamespace(validate_step=lambda step:
        SimpleNamespace(allowed=False, reason="synthetic block"))
    report = agent.execute_plan("one затем two", None, None)
    assert all(r["status"] == "failed" and not r["ok"] for r in agent._results)
    assert "ПЛАН ВЫПОЛНЕН" not in report


def test_planner_claim_without_execution_returns_none(monkeypatch):
    engine = ArgosToolCallingEngine(SimpleNamespace(context=None))
    monkeypatch.setattr(engine, "_plan_calls", lambda *a, **k:
        {"confidence": 1, "final_answer": "Файл создан", "tool_calls": []})
    assert engine.try_handle("сохрани документ", object(), None) is None


def test_planner_executes_calls_before_final_claim(monkeypatch):
    engine = ArgosToolCallingEngine(SimpleNamespace(context=None))
    monkeypatch.setattr(engine, "_plan_calls", lambda *a, **k: {
        "confidence": 1, "final_answer": "Готово",
        "tool_calls": [{"name": "create_file", "arguments": {"path": "synthetic"}}],
    })
    calls = []
    monkeypatch.setattr(engine, "_execute_tool", lambda *a:
        calls.append(a) or "Ошибка создания файла")
    result = engine.try_handle("сохрани документ", object(), None)
    assert len(calls) == 1
    assert "Ошибка создания файла" in result
    assert "Готово" not in result


@pytest.mark.parametrize("name", ["get_stats", "get_system_stats"])
def test_stats_aliases_execute_real_backend(name):
    engine = ArgosToolCallingEngine(SimpleNamespace())
    assert engine._execute_tool(name, {}, SimpleNamespace(get_stats=lambda: "actual stats"), None) == "actual stats"


def test_planner_overflow_stops_after_one_request(monkeypatch):
    from src.ollama_input_policy import context_limit_message
    engine = ArgosToolCallingEngine(SimpleNamespace(context=None))
    response = SimpleNamespace(status_code=400, json=lambda:
        {"error": "input length exceeds maximum context length"})
    message = context_limit_message(response)
    assert message
    posts = []
    def post(*args, **kwargs):
        posts.append(kwargs["json"])
        return response
    monkeypatch.setattr("src.tool_calling.requests.post", post)
    monkeypatch.setattr(engine, "_execute_tool", lambda *args:
        pytest.fail("overflow must not execute a tool"))
    assert engine.try_handle("сохрани документ", object(), None) == message
    assert len(posts) == 1
    assert posts[0]["truncate"] is False


def test_overflow_keeps_results_from_completed_tools(monkeypatch):
    engine = ArgosToolCallingEngine(SimpleNamespace(context=None))
    plans = iter([
        {"tool_calls": [{"name": "get_stats", "arguments": {}}]},
        {"error": "context_overflow", "message": "Контекст слишком длинный", "tool_calls": []},
    ])
    monkeypatch.setattr(engine, "_plan_calls", lambda *a, **k: next(plans))
    monkeypatch.setattr(engine, "_execute_tool", lambda *args: "actual stats")
    result = engine.try_handle("проверь статус", object(), None)
    assert "actual stats" in result
    assert "Контекст слишком длинный" in result


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_tool_empty_backend_result_is_explicitly_unconfirmed(raw):
    engine = ArgosToolCallingEngine(SimpleNamespace())
    result = engine._execute_tool("create_file", {"path": "synthetic"},
        SimpleNamespace(create_file=lambda *args: raw), None)
    assert result.startswith("❌")
    assert "подтверждения" in result


def test_callback_failure_does_not_change_execution_counts(monkeypatch):
    agent = agent_with_results(monkeypatch, [
        {"answer": "done", "execution_status": "succeeded"},
    ])
    agent._task_chain = ["one"]
    agent._running = True
    def broken_callback(*args):
        raise RuntimeError("synthetic delivery failure")
    agent._execute_chain(broken_callback)
    assert len(agent._chain_results) == 1
    assert agent._report["tasks_done"] == 1
    assert agent._report["errors"] == 0
    assert not agent._running


def test_failed_agent_report_has_neutral_voice_announcement():
    import ast
    from pathlib import Path
    source = Path(__file__).parents[1] / "src" / "core.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    core = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ArgosCore")
    process = next(node for node in core.body if isinstance(node, ast.FunctionDef) and node.name == "process_logic")
    branch = next(node for node in process.body if isinstance(node, ast.If)
                  and isinstance(node.test, ast.Name) and node.test.id == "agent_result")
    method = ast.FunctionDef(name="run_branch",
        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="self"), ast.arg(arg="user_text"), ast.arg(arg="agent_result")],
                           kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[branch], decorator_list=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(source), "exec"), namespace)
    announcements = []
    fake = SimpleNamespace(context=SimpleNamespace(add=lambda *args: None),
        _remember_dialog_turn=lambda *args: None, db=None, say=announcements.append)
    result = namespace["run_branch"](fake, "synthetic", "❌ Шаг заблокирован")
    assert result["answer"] == "❌ Шаг заблокирован"
    assert announcements == ["Обработка плана завершена. Проверьте результаты шагов."]
