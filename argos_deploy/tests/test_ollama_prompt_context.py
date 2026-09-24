import ast
import logging
import os
from pathlib import Path
from types import SimpleNamespace

from src.context_manager import DialogContext, IDENTITY_ANCHOR


SOURCE = Path(__file__).parents[1] / "src" / "core.py"


def method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ArgosCore")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def assembled_context(status="CPU 1%", state="Analytic"):
    node = next(n for n in method("process_logic").body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "context" for t in n.targets))
    return eval(compile(ast.Expression(node.value), str(SOURCE), "eval"),
                {"os": os, "_sys_status": status, "q_data": {"name": state}})


def prompt(context):
    namespace = {"os": os, "log": logging.getLogger("test.prompt")}
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs["json"]["prompt"])
        return SimpleNamespace(status_code=200, ok=True, json=lambda: {"response": "answer"})
    namespace["requests"] = SimpleNamespace(post=post)
    exec(compile(ast.Module(body=[method("_ask_ollama_inner")], type_ignores=[]), str(SOURCE), "exec"), namespace)
    history = DialogContext()
    history._cached_summary = "SYNTHETIC SUMMARY"
    history.memory_ref = SimpleNamespace(get_context=lambda: "SYNTHETIC LONGTERM")
    history.add("user", "SYNTHETIC HISTORY")
    core = SimpleNamespace(context=history, ollama_url="http://unused/api/generate",
                           _ensure_ollama_running=lambda: True)
    assert namespace["_ask_ollama_inner"](core, context, "SYNTHETIC USER") == "answer"
    return captured[0]


def test_anchor_once_and_unique_context_preserved():
    result = prompt(assembled_context() + "\nSYNTHETIC RAG\nSYNTHETIC RECOVERED")
    assert result.count(IDENTITY_ANCHOR) == 1
    for text in ("SYNTHETIC SUMMARY", "SYNTHETIC LONGTERM", "SYNTHETIC HISTORY",
                 "SYNTHETIC RAG", "SYNTHETIC RECOVERED", "SYNTHETIC USER"):
        assert text in result


def test_static_rules_precede_dynamic_context():
    result = prompt(assembled_context())
    assert result.index("[ARGOS EXECUTION RULES]") < result.index("CPU 1%")
    assert result.index("Сообщай об успехе только при подтверждённом результате инструмента.") < result.index("CPU 1%")
    assert result.index("Сообщай об успехе только при подтверждённом результате инструмента.") < result.index("Квантовое состояние: Analytic")


def test_default_history_keeps_identity():
    assert DialogContext().get_prompt_context().startswith(f"[SYSTEM] {IDENTITY_ANCHOR}")


def test_history_block_already_in_context_is_not_repeated():
    facts = "Известные факты о пользователе и системе:\n  [user] name: SYNTHETIC FACT"
    namespace = {"os": os, "log": logging.getLogger("test.prompt")}
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs["json"]["prompt"])
        return SimpleNamespace(status_code=200, ok=True, json=lambda: {"response": "answer"})
    namespace["requests"] = SimpleNamespace(post=post)
    exec(compile(ast.Module(body=[method("_ask_ollama_inner")], type_ignores=[]), str(SOURCE), "exec"), namespace)
    history = DialogContext()
    history.memory_ref = SimpleNamespace(get_context=lambda: facts)
    history.add("user", "SYNTHETIC HISTORY")
    core = SimpleNamespace(context=history, ollama_url="http://unused/api/generate",
                           _ensure_ollama_running=lambda: True)
    namespace["_ask_ollama_inner"](core, assembled_context() + "\n\n" + facts, "SYNTHETIC USER")
    assert captured[0].count("SYNTHETIC FACT") == 1
    assert "SYNTHETIC HISTORY" in captured[0]
