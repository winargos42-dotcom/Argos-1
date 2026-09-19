import pytest

from src.safe_arithmetic import try_calculate


@pytest.mark.parametrize(("text", "expected"), [
    ("2+2", "4"),
    ("Вычисли 2+2. Ответь только числом.", "4"),
    ("посчитай (2 + 3) * 4", "20"),
    ("сколько будет 10 / 4?", "2.5"),
    ("calculate -2 * -(3 + 4)", "14"),
    ("0.1 + 0.2", "0.3"),
    ("1,25 + 2,75", "4"),
    (".5 + 0.25", "0.75"),
    ("5-8", "-3"),
    ("2/4+3/4", "1.25"),
    ("Вычисли 2026", "2026"),
    ("-0 + 0", "0"),
    ("9007199254740993+1", "9007199254740994"),
    ("0.12345678901234567890123456789+0", "0.12345678901234567890123456789"),
    ("(" * 16 + "1+2" + ")" * 16, "3"),
])
def test_arithmetic(text, expected):
    assert try_calculate(text) == expected


@pytest.mark.parametrize("text", [
    "2026", "-2026", "3.14", "", "Привет", "версия 2+2",
    "Расскажи про 2026 год", "argos status", "abc + 2",
])
def test_unrelated_text_is_not_intercepted(text):
    assert try_calculate(text) is None


@pytest.mark.parametrize("expression", [
    "1/0", "0/0", "2**100", "2//1", "2+", "(2+3", "abs(-1)",
    "x+1", "().__class__", "[1,2]", "1e100", "9" * 51 + "+1",
    "1" + "0" * 49 + "*100", "(" * 17 + "1+2" + ")" * 17,
    "-" * 17 + "2", "+".join(["1"] * 70), "1+" + "1" * 512,
    "выведи сведения о проекте",
    "1/" + "9" * 50 + "/100",
])
def test_invalid_or_excessive_recognized_request_has_explicit_error(expression):
    assert try_calculate("вычисли " + expression).startswith("Ошибка:")


def test_division_by_zero_error_is_specific():
    assert "ноль" in try_calculate("1/0")


def test_python_payload_is_not_executed(tmp_path):
    target = tmp_path / "should_not_exist"
    text = f"вычисли __import__('pathlib').Path('{target}').touch()"
    assert try_calculate(text).startswith("Ошибка:")
    assert not target.exists()


def test_recurring_decimal_is_bounded():
    result = try_calculate("1/3")
    assert result.startswith("0.3333333333")
    assert len(result) <= 52
