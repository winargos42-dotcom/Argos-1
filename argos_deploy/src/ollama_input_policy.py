from copy import deepcopy
import re


_CONTEXT_LIMIT_MESSAGE = (
    "Запрос не обработан: превышен допустимый объём контекста модели. "
    "Сократите историю диалога или запрос и повторите попытку."
)


def generate_payload(model: str, prompt: str, options: dict | None = None) -> dict:
    payload = {"model": model, "prompt": prompt, "stream": False, "truncate": False}
    if options is not None:
        payload["options"] = deepcopy(options)
    return payload


def context_limit_message(response) -> str | None:
    if getattr(response, "status_code", None) != 400:
        return None
    try:
        body = response.json()
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        error = error.get("message")
    if not isinstance(error, str):
        return None
    error = " ".join(error.casefold().split())
    if (
        "exceeds the available context size" in error
        or re.search(r"input length exceeds.{0,80}context (?:length|size|window)", error)
        or "prompt is longer than the context length" in error
        or re.search(r"(?:exceeds?|exceeded|too long|overflow).{0,80}context window", error)
        or re.search(r"context window.{0,40}(?:exceeded|overflow|is full)", error)
    ):
        return _CONTEXT_LIMIT_MESSAGE
    return None
