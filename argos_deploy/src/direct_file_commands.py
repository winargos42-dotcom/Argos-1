import re
from pathlib import Path


_COMMAND = re.compile(
    r"^(?:аргос\s*,\s*)?(?P<command>"
    r"создай (?:новый |текстовый )?файл|напиши файл|сохрани в файл|"
    r"прочитай файл|открой файл|покажи файлы|список файлов|файлы)(?=\s|$)",
    re.IGNORECASE,
)
_PATH = re.compile(r'''^(?:"([^"]+)"|'([^']+)'|(\S+))(?:\s(.*))?$''', re.DOTALL)


def is_file_command(text: str) -> bool:
    return _COMMAND.match(text.lstrip()) is not None


def handle_file_command(text: str, admin) -> dict | None:
    text = text.lstrip()
    match = _COMMAND.match(text)
    if match is None:
        return None

    def result(answer, status):
        return {"answer": answer, "execution_status": status}

    command = match.group("command").casefold()
    operation = "list" if command in ("покажи файлы", "список файлов", "файлы") else (
        "read" if command in ("прочитай файл", "открой файл") else "create"
    )
    remainder = text[match.end():].lstrip()
    parsed = _PATH.fullmatch(remainder) if remainder else None
    if not remainder and operation == "list":
        path, content = ".", ""
    elif parsed is None or (remainder.startswith(('"', "'")) and parsed.group(3)):
        return result("Ошибка: укажите путь; путь с пробелами заключите в кавычки.", "failed")
    else:
        path = next(value for value in parsed.groups()[:3] if value is not None)
        content = parsed.group(4) or ""
    if operation != "create" and content.strip():
        return result("Ошибка: после пути есть лишний текст; путь с пробелами заключите в кавычки.", "failed")
    if admin is None:
        return result("Ошибка: файловый модуль недоступен.", "failed")
    try:
        if operation == "create":
            answer = admin.create_file(path, content)
            prefix = f"✅ Файл создан: {path} ("
        elif operation == "read":
            answer = admin.read_file(path)
            prefix = f"📄 Файл '{path}' ("
        else:
            answer = admin.list_dir(path)
            prefix = f"📂 Содержимое '{path}' ("
        if not isinstance(answer, str) or not answer.strip():
            return result("Ошибка: файловый модуль не вернул результат операции.", "failed")
        if answer.lstrip().casefold().startswith(("ошибка", "error", "❌", "⛔")):
            return result(answer, "failed")
        verified = False
        if answer.startswith(prefix):
            target = Path(path)
            if operation == "create":
                verified = target.is_file() and target.read_text(encoding="utf-8") == content
            elif operation == "read":
                verified = target.is_file()
            else:
                verified = target.is_dir()
        if verified:
            return result(answer, "succeeded")
        return result("Результат операции не подтверждён. Ответ файлового модуля:\n" + answer, "unverified")
    except Exception as exc:
        return result(f"Ошибка файловой операции: {exc}", "failed")
