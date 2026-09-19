def classify_execution(result: object) -> tuple[str, str]:
    status = None
    if isinstance(result, dict):
        status = result.get("execution_status")
        result = result.get("answer")
    answer = "" if result is None else str(result)
    normalized = answer.strip().casefold()
    if not normalized or normalized.startswith((
        "❌", "⛔", "ошибка", "error", "blocked", "[tool ",
        "не найден", "невозможно", "не удалось", "stats unavailable",
    )):
        return answer, "failed"
    if status in ("succeeded", "failed", "unverified"):
        return answer, status
    return answer, "unverified"
