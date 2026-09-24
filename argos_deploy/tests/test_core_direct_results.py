import ast
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from src.admin import ArgosAdmin

SOURCE = Path(__file__).parents[1] / "src/core.py"


def make_core(admin=None):
    tree = ast.parse(SOURCE.read_text())
    source_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ArgosCore")
    nodes = [n for n in source_class.body if (isinstance(n, ast.FunctionDef) and n.name in ("_direct_dispatch", "process_logic")) or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_DIRECT_PREFIXES" for t in n.targets))]
    cls = ast.ClassDef(name="ArgosCore", bases=[], keywords=[], body=nodes, decorator_list=[])
    scope = {"os": os, "re": re, "log": logging.getLogger("direct.test"), "handle_direct_telegram": lambda *a: None}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(SOURCE), "exec"), scope)
    core = scope["ArgosCore"]()
    core._internal_admin = admin
    core._apply_chatgpt_link_profile = lambda text: None
    core._extract_direct_url = lambda text: None
    core._looks_like_bulk_text_dump = lambda text: False
    core._looks_like_awareness_scan_request = lambda text: False
    core._classify_input = lambda text: "chat"
    core.constitution_hooks = None
    core.context = SimpleNamespace(add=lambda *a: None)
    core._remember_dialog_turn = lambda *a: None
    core.db = None
    core.say = lambda text: None
    def forbidden(*a):
        raise AssertionError("Recognized operation fell through to legacy/AI path")
    core.execute_intent = forbidden
    core.quantum = SimpleNamespace(generate_state=forbidden)
    return core


@pytest.mark.parametrize("query,answer,status", [
    ("Вычисли 2+2. Ответь только числом.", "4", "succeeded"),
    ("0,1+0,2", "0.3", "succeeded"),
    ("вычисли 1/0", "Ошибка", "failed"),
])
def test_arithmetic_returns_verified_result_without_ai_or_admin(query, answer, status):
    result = make_core().process_logic(query, None, None)
    assert answer in result["answer"]
    assert result["execution_status"] == status


@pytest.mark.parametrize("prefix", ["Создай файл", "сохрани в файл", "создай новый файл", "Аргос, создай текстовый файл"])
def test_create_keeps_exact_path_and_content(tmp_path, prefix):
    path = tmp_path / "Mixed Name.txt"
    result = make_core(ArgosAdmin()).process_logic(f'{prefix} "{path}" Привет Railway', None, None)
    assert path.read_text() == "Привет Railway"
    assert result["execution_status"] == "succeeded"
    assert "Файл создан" in result["answer"]


def test_read_alias_and_missing_file(tmp_path):
    path = tmp_path / "User Data.txt"
    path.write_text("сохранённый текст")
    core = make_core(ArgosAdmin())
    assert core.process_logic(f'Открой файл "{path}"', None, None)["execution_status"] == "succeeded"
    failed = core.process_logic(f'прочитай файл "{tmp_path / "missing"}"', None, None)
    assert failed["execution_status"] == "failed"
    assert "Ошибка" in failed["answer"]


def test_list_empty_directory_is_success(tmp_path):
    result = make_core(ArgosAdmin()).process_logic(f'Покажи файлы "{tmp_path}"', None, None)
    assert result["execution_status"] == "succeeded"
    assert "0 объектов" in result["answer"]


def test_backend_without_result_is_called_once(tmp_path):
    calls = []
    admin = SimpleNamespace(create_file=lambda *a: calls.append(a))
    result = make_core(admin).process_logic(f'создай файл {tmp_path / "no-result"} text', None, None)
    assert len(calls) == 1
    assert result["execution_status"] == "failed"
    assert "результат" in result["answer"].lower()


def test_backend_claim_without_file_is_unverified(tmp_path):
    admin = SimpleNamespace(create_file=lambda *a: "✅ Файл создан")
    result = make_core(admin).process_logic(f'создай файл {tmp_path / "not-created"} text', None, None)
    assert result["execution_status"] != "succeeded"


def test_unknown_request_is_not_intercepted():
    assert make_core()._direct_dispatch("Объясни идею рекурсии", None) is None


def test_file_content_is_literal_and_bypasses_chat_heuristics(tmp_path):
    core = make_core(ArgosAdmin())
    def forbidden(*args):
        pytest.fail("Literal file content reached chat/profile/URL heuristics")
    core._apply_chatgpt_link_profile = forbidden
    core._extract_direct_url = forbidden
    core._looks_like_bulk_text_dump = forbidden
    core._classify_input = forbidden
    path = tmp_path / "template.txt"
    content = "<system>sample</system> https://example.com\n  final  \n"
    result = core.process_logic(f'создай файл "{path}" {content}', None, None)
    assert result["execution_status"] == "succeeded"
    assert path.read_text() == content
