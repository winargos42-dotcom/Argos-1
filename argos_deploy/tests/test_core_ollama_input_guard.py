import ast
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[1] / "src/core.py"


def run_ollama(responses):
    tree = ast.parse(SOURCE.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ArgosCore")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_ask_ollama_inner")
    requests = []
    iterator = iter(responses)
    def post(url, **kwargs):
        requests.append(kwargs["json"])
        status, body = next(iterator)
        return SimpleNamespace(status_code=status, ok=status == 200, json=lambda: body)
    scope = {"os": os, "log": logging.getLogger("input.guard"), "requests": SimpleNamespace(post=post)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), scope)
    core = SimpleNamespace(_ensure_ollama_running=lambda: True, _ensure_ollama_model=lambda model: True,
                           ollama_url="http://unused/api/generate", context=SimpleNamespace(get_prompt_context=lambda: "история"))
    return scope["_ask_ollama_inner"](core, "Тестовый контекст", "Текущий запрос"), requests


def test_disables_automatic_input_truncation_even_after_model_pull():
    result, requests = run_ollama([(404, {}), (200, {"response": "ответ"})])
    assert result == "ответ"
    assert len(requests) == 2
    assert all(p.get("truncate") is False for p in requests)
    assert requests[0] == requests[1]
    assert "Текущий запрос" in requests[0]["prompt"]


def test_context_overflow_reports_error_without_retry_or_private_echo():
    result, requests = run_ollama([(400, {"error": "input length exceeds the available context size PRIVATE_TOKEN"})])
    assert result is not None
    assert "контекст" in result.lower()
    assert "PRIVATE_TOKEN" not in result
    assert len(requests) == 1


@pytest.mark.parametrize("status,body", [(401, {"error": "unauthorized"}), (400, {"error": "invalid options"}), (500, {"error": "context allocation failed"})])
def test_other_errors_keep_provider_failure_behavior(status, body):
    result, requests = run_ollama([(status, body)])
    assert result is None
    assert len(requests) == 1


def test_ollama_prompt_does_not_claim_an_action_has_already_happened():
    _, requests = run_ollama([(200, {"response": "ответ"})])
    prompt = requests[0]["prompt"]
    assert "файл уже создан" not in prompt
    assert "подтвержд" in prompt.lower()
