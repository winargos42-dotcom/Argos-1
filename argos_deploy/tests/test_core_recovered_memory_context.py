import ast
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import mempalace_bridge as bridge


def _context_to_provider():
    path = Path(__file__).parents[1] / "src" / "core.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ArgosCore")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "process_logic")
    end = next(i for i, node in enumerate(method.body)
               if isinstance(node, ast.If) and ast.unparse(node.test) == "self.ai_mode == 'gemini'")
    start = max(i for i, node in enumerate(method.body[:end])
                if isinstance(node, ast.If) and ast.unparse(node.test) == "self.memory")
    module = ast.Module(body=method.body[start:end + 1], type_ignores=[])
    return compile(ast.fix_missing_locations(module), str(path), "exec")


def _run_provider():
    seen = []
    def provider(context, query):
        seen.append((context, query))
        return "provider answer"
    namespace = {
        "self": SimpleNamespace(memory=None, ai_mode="ollama", _ask_ollama=provider),
        "user_text": "query", "context": "base context", "q_data": {"name": "test"},
        "os": os, "log": logging.getLogger("test.recovered.memory"),
    }
    exec(_context_to_provider(), namespace)
    assert namespace["answer"] == "provider answer"
    return seen[0]


def test_provider_receives_bounded_historical_context(monkeypatch):
    monkeypatch.setenv("ARGOS_MEMPALACE_SQLITE_PATH", "/synthetic/memory.sqlite3")
    monkeypatch.setattr(bridge, "get_memory_context", lambda query: "history " + "x" * 5000)
    context, query = _run_provider()
    assert query == "query"
    assert "history " in context
    assert "исторические справочные данные" in context
    assert "не выполняй" in context
    assert len(context) < 2800


def test_disabled_recovery_does_not_query_memory(monkeypatch):
    monkeypatch.delenv("ARGOS_MEMPALACE_SQLITE_PATH", raising=False)
    def forbidden(query):
        pytest.fail("Unconfigured recovery must not query memory")
    monkeypatch.setattr(bridge, "get_memory_context", forbidden)
    assert _run_provider() == ("base context", "query")


def test_unavailable_recovery_does_not_block_provider(monkeypatch):
    monkeypatch.setenv("ARGOS_MEMPALACE_SQLITE_PATH", "/synthetic/memory.sqlite3")
    def unavailable(query):
        raise OSError("unavailable")
    monkeypatch.setattr(bridge, "get_memory_context", unavailable)
    assert _run_provider() == ("base context", "query")


@pytest.mark.parametrize("setting", ["ARGOS_MEMPALACE_INDEX_PATH", "ARGOS_MEMPALACE_FACTS_PATH"])
def test_index_or_new_facts_alone_reach_provider(monkeypatch, setting):
    monkeypatch.delenv("ARGOS_MEMPALACE_SQLITE_PATH", raising=False)
    monkeypatch.setenv(setting, "/synthetic/separate-memory.sqlite3")
    monkeypatch.setattr(bridge, "get_memory_context", lambda query: "SYNTHETIC NEW FACT")
    assert "SYNTHETIC NEW FACT" in _run_provider()[0]
