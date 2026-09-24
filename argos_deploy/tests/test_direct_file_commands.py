from types import SimpleNamespace

import pytest

from src.admin import ArgosAdmin
from src.direct_file_commands import handle_file_command


def test_create_preserves_trailing_content_whitespace(tmp_path):
    target = tmp_path / "Mixed File.txt"
    content = "line one\nline two  \n"
    result = handle_file_command(f'создай файл "{target}" {content}', ArgosAdmin())
    assert target.read_text(encoding="utf-8") == content
    assert result["execution_status"] == "succeeded"


def test_backend_reading_different_path_is_not_verified(tmp_path):
    target = tmp_path / "intended.txt"
    target.write_text("intended data", encoding="utf-8")
    admin = SimpleNamespace(read_file=lambda path: "📄 Файл 'other.txt' (5 байт):\nwrong")
    result = handle_file_command(f'прочитай файл "{target}"', admin)
    assert result["execution_status"] != "succeeded"


@pytest.mark.parametrize("command", [
    'создай файл "unterminated path',
    'прочитай файл "file name.txt" extra',
    "создай файл",
])
def test_invalid_syntax_never_calls_backend(command):
    def forbidden(*args):
        pytest.fail("Invalid command reached file backend")
    admin = SimpleNamespace(create_file=forbidden, read_file=forbidden)
    assert handle_file_command(command, admin)["execution_status"] == "failed"


def test_backend_exception_is_reported_once(tmp_path):
    calls = []
    def create(path, content):
        calls.append((path, content))
        raise PermissionError("denied")
    target = str(tmp_path / "blocked")
    result = handle_file_command(f"создай файл {target} payload", SimpleNamespace(create_file=create))
    assert calls == [(target, "payload")]
    assert result["execution_status"] == "failed"
    assert "denied" in result["answer"]


def test_shell_metacharacters_in_quoted_path_remain_literal(tmp_path):
    target = tmp_path / "$(echo unsafe); User File.txt"
    result = handle_file_command(f'создай файл "{target}" literal', ArgosAdmin())
    assert target.read_text(encoding="utf-8") == "literal"
    assert result["execution_status"] == "succeeded"
