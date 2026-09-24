from types import SimpleNamespace

from src.tool_calling import ArgosToolCallingEngine


def _core_with_context():
    return SimpleNamespace(
        context=SimpleNamespace(get_prompt_context=lambda _query: "ctx"),
        sensors=SimpleNamespace(get_full_report=lambda: "ok"),
        p2p=None,
        start_p2p=lambda: "started",
        _ask_gemini=lambda *_args, **_kwargs: None,
        _ask_gigachat=lambda *_args, **_kwargs: None,
        _ask_yandexgpt=lambda *_args, **_kwargs: None,
        ollama_url="http://localhost:11434/api/generate",
        skill_loader=None,
    )


def test_tool_calling_multi_turn_stops_on_confident_final_answer(monkeypatch):
    engine = ArgosToolCallingEngine(_core_with_context())
    calls = []

    def fake_plan(_user_text, context_text="", previous_outputs=None):
        calls.append((context_text, previous_outputs or []))
        if len(calls) == 1:
            return {
                "confidence": 0.3,
                "tool_calls": [{"name": "get_system_stats", "arguments": {}}],
                "final_answer": "",
            }
        return {"confidence": 0.9, "tool_calls": [], "final_answer": "Готово"}

    executed = []
    monkeypatch.setattr(engine, "_plan_calls", fake_plan)
    monkeypatch.setattr(
        engine,
        "_execute_tool",
        lambda name, arguments, admin, flasher: _append_and_return(executed, (name, arguments), "CPU ok"),
    )

    result = engine.try_handle("проверь статус", admin=SimpleNamespace(get_stats=lambda: "stats"), flasher=None)

    assert result == "[get_system_stats]: CPU ok"
    assert executed == [("get_system_stats", {})]
    assert len(calls) == 2
    assert calls[1][1] == [{"tool": "get_system_stats", "arguments": {}, "result": "CPU ok"}]


def test_tool_calling_synthesizes_if_planner_unavailable_after_steps(monkeypatch):
    engine = ArgosToolCallingEngine(_core_with_context())

    responses = [
        {
            "confidence": 0.3,
            "tool_calls": [{"name": "get_system_stats", "arguments": {}}],
            "final_answer": "",
        },
        None,
    ]
    monkeypatch.setattr(engine, "_plan_calls", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr(engine, "_execute_tool", lambda *_args, **_kwargs: "CPU ok")
    monkeypatch.setattr(engine, "_synthesize_answer", lambda _text, outputs: f"Итог: {len(outputs)}")

    result = engine.try_handle("проверь статус", admin=SimpleNamespace(get_stats=lambda: "stats"), flasher=None)

    assert result == "Итог: 1"


def test_tool_calling_skips_duplicate_calls_between_rounds(monkeypatch):
    engine = ArgosToolCallingEngine(_core_with_context())
    monkeypatch.setattr(
        engine,
        "_plan_calls",
        lambda *_args, **_kwargs: {
            "confidence": 0.2,
            "tool_calls": [{"name": "get_system_stats", "arguments": {}}],
            "final_answer": "",
        },
    )

    executed = []
    monkeypatch.setattr(
        engine,
        "_execute_tool",
        lambda name, arguments, admin, flasher: _append_and_return(executed, (name, arguments), "CPU ok"),
    )
    monkeypatch.setattr(engine, "_synthesize_answer", lambda _text, outputs: f"Итог: {len(outputs)}")

    result = engine.try_handle("проверь статус", admin=SimpleNamespace(get_stats=lambda: "stats"), flasher=None)

    assert result == "Итог: 1"
    assert executed == [("get_system_stats", {})]


def _append_and_return(target: list, item, value):
    target.append(item)
    return value
