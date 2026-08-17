"""tests/test_ollama_timeout_autostart.py

Проверяет:
  1. Таймаут Ollama в _ask_ollama читается из OLLAMA_TIMEOUT (по умолчанию 600)
  2. Таймаут Ollama в ArgosToolCallingEngine читается из OLLAMA_TIMEOUT (по умолчанию 600)
  3. _ensure_ollama_running() вызывается перед каждым запросом
  4. _ensure_ollama_running() возвращает True когда сервис уже доступен
  5. _ensure_ollama_running() запускает `ollama serve` если сервис недоступен
  6. _ensure_ollama_running() возвращает False если ollama не установлена
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call


# ──────────────────────────────────────────────────────────
# Хелпер: создаём минимальный core без полной инициализации
# ──────────────────────────────────────────────────────────

def _make_dummy_core(ollama_url: str = "http://localhost:11434/api/generate"):
    """Возвращает SimpleNamespace, имитирующий ArgosCore для тестов Ollama."""
    dummy = SimpleNamespace(
        ollama_url=ollama_url,
        context=SimpleNamespace(get_prompt_context=lambda: ""),
    )
    # Привязываем реальные методы из ArgosCore
    from src.core import ArgosCore
    dummy._set_ollama_unavailable = ArgosCore._set_ollama_unavailable.__get__(dummy, type(dummy))
    dummy._set_ollama_available = ArgosCore._set_ollama_available.__get__(dummy, type(dummy))
    dummy._ensure_ollama_running = ArgosCore._ensure_ollama_running.__get__(dummy, type(dummy))
    dummy._ensure_ollama_model = ArgosCore._ensure_ollama_model.__get__(dummy, type(dummy))
    dummy._ask_ollama = ArgosCore._ask_ollama.__get__(dummy, type(dummy))
    return dummy


# ──────────────────────────────────────────────────────────
# 1. Таймаут в _ask_ollama читается из OLLAMA_TIMEOUT
# ──────────────────────────────────────────────────────────

def test_ask_ollama_uses_env_timeout(monkeypatch):
    dummy = _make_dummy_core()

    captured: list[int] = []

    def fake_ensure(self=None):  # noqa: ANN001
        return True

    def fake_post(url, *, json=None, timeout=None, **kwargs):
        captured.append(timeout)
        resp = MagicMock()
        resp.json.return_value = {"response": "ok"}
        return resp

    monkeypatch.setattr(dummy, "_ensure_ollama_running", lambda: True)

    import requests as req_module
    monkeypatch.setattr(req_module, "post", fake_post)

    # Default (no env override) → 600
    monkeypatch.delenv("OLLAMA_TIMEOUT", raising=False)
    dummy._ask_ollama("system", "hello")
    assert captured, "_ask_ollama не вызвала requests.post"
    assert captured[0] == 600, f"Ожидался timeout=600 (default), получен {captured[0]}"

    # Explicit env override
    captured.clear()
    monkeypatch.setenv("OLLAMA_TIMEOUT", "300")
    dummy._ask_ollama("system", "hello2")
    assert captured[0] == 300, f"Ожидался timeout=300 (из OLLAMA_TIMEOUT=300), получен {captured[0]}"


# ──────────────────────────────────────────────────────────
# 2. Таймаут в tool_calling читается из OLLAMA_TIMEOUT
# ──────────────────────────────────────────────────────────

def test_tool_calling_ollama_uses_env_timeout(monkeypatch):
    from src.tool_calling import ArgosToolCallingEngine

    core = SimpleNamespace(
        context=SimpleNamespace(get_prompt_context=lambda _query="": "ctx"),
        sensors=SimpleNamespace(get_full_report=lambda: "ok"),
        p2p=None,
        start_p2p=lambda: "started",
        _ask_gemini=lambda *a, **kw: None,
        _ask_gigachat=lambda *a, **kw: None,
        _ask_yandexgpt=lambda *a, **kw: None,
        _ensure_ollama_running=lambda: True,
        ollama_url="http://localhost:11434/api/generate",
        skill_loader=None,
    )
    engine = ArgosToolCallingEngine(core)

    captured: list[int] = []

    def fake_post(url, *, json=None, timeout=None, **kwargs):
        captured.append(timeout)
        raise ConnectionError("ollama offline (test)")

    import requests as req_module
    monkeypatch.setattr(req_module, "post", fake_post)

    # Default (no env override) → 600
    monkeypatch.delenv("OLLAMA_TIMEOUT", raising=False)
    engine._plan_calls("тест")
    assert captured, "tool_calling не вызвал requests.post к Ollama"
    assert captured[0] == 600, f"Ожидался timeout=600 (default), получен {captured[0]}"

    # Explicit env override
    captured.clear()
    monkeypatch.setenv("OLLAMA_TIMEOUT", "120")
    engine._plan_calls("тест2")
    assert captured[0] == 120, f"Ожидался timeout=120 (из OLLAMA_TIMEOUT=120), получен {captured[0]}"


# ──────────────────────────────────────────────────────────
# 3. _ensure_ollama_running вызывается до requests.post
# ──────────────────────────────────────────────────────────

def test_ensure_ollama_running_called_before_request(monkeypatch):
    dummy = _make_dummy_core()
    order: list[str] = []

    def fake_ensure():
        order.append("ensure")
        return True

    def fake_post(url, *, json=None, timeout=None, **kwargs):
        order.append("post")
        resp = MagicMock()
        resp.json.return_value = {"response": "reply"}
        return resp

    monkeypatch.setattr(dummy, "_ensure_ollama_running", fake_ensure)

    import requests as req_module
    monkeypatch.setattr(req_module, "post", fake_post)

    dummy._ask_ollama("ctx", "hi")

    assert order == ["ensure", "post"], f"Неверный порядок вызовов: {order}"


# ──────────────────────────────────────────────────────────
# 4. _ensure_ollama_running возвращает True если сервис уже работает
# ──────────────────────────────────────────────────────────

def test_ensure_ollama_running_returns_true_when_already_up(monkeypatch):
    dummy = _make_dummy_core()

    import requests as req_module

    def fake_get(url, *, timeout=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(req_module, "get", fake_get)

    result = dummy._ensure_ollama_running()
    assert result is True


# ──────────────────────────────────────────────────────────
# 5. _ensure_ollama_running запускает `ollama serve` если недоступен
# ──────────────────────────────────────────────────────────

def test_ensure_ollama_running_starts_process_when_down(monkeypatch):
    from src.core import ArgosCore
    import requests as req_module
    import subprocess as sp

    call_count = {"get": 0}

    def fake_get(url, *, timeout=None, **kwargs):
        call_count["get"] += 1
        # Первый вызов — недоступен; второй (после запуска) — OK
        if call_count["get"] <= 2:
            raise ConnectionError("down")
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(req_module, "get", fake_get)

    fake_proc = MagicMock()
    fake_proc.pid = 9999

    monkeypatch.setattr(sp, "Popen", lambda *a, **kw: fake_proc)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ollama")

    # Сбрасываем класс-переменную между тестами
    ArgosCore._ollama_proc = None
    original_lock = ArgosCore._ollama_start_lock
    ArgosCore._ollama_start_lock = threading.Lock()

    dummy = _make_dummy_core()
    result = dummy._ensure_ollama_running()

    ArgosCore._ollama_start_lock = original_lock

    assert result is True
    assert ArgosCore._ollama_proc is fake_proc


# ──────────────────────────────────────────────────────────
# 6. _ensure_ollama_running возвращает False если ollama не установлена
# ──────────────────────────────────────────────────────────

def test_ensure_ollama_running_returns_false_when_not_installed(monkeypatch):
    from src.core import ArgosCore
    import requests as req_module
    import subprocess as sp

    monkeypatch.setattr(req_module, "get", lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("down")))

    def raise_file_not_found(*a, **kw):
        raise FileNotFoundError("ollama not found")

    monkeypatch.setattr(sp, "Popen", raise_file_not_found)

    ArgosCore._ollama_proc = None
    original_lock = ArgosCore._ollama_start_lock
    ArgosCore._ollama_start_lock = threading.Lock()

    dummy = _make_dummy_core()
    result = dummy._ensure_ollama_running()

    ArgosCore._ollama_start_lock = original_lock

    assert result is False


# ──────────────────────────────────────────────────────────
# 7. HTTP 404 → автоматическая загрузка модели и повтор запроса
# ──────────────────────────────────────────────────────────

def test_ask_ollama_pulls_model_on_404_and_retries(monkeypatch):
    """При HTTP 404 _ask_ollama должна скачать модель и повторить запрос."""
    dummy = _make_dummy_core()

    monkeypatch.setattr(dummy, "_ensure_ollama_running", lambda: True)

    import requests as req_module
    import subprocess as sp

    call_count = {"post": 0}

    def fake_post(url, *, json=None, timeout=None, **kwargs):
        call_count["post"] += 1
        resp = MagicMock()
        if call_count["post"] == 1:
            resp.status_code = 404
            resp.json.return_value = {}
        else:
            resp.status_code = 200
            resp.json.return_value = {"response": "повтор успешен"}
        return resp

    monkeypatch.setattr(req_module, "post", fake_post)

    # Мокаем requests.get для _ensure_ollama_model (список моделей без нужной)
    def fake_get(url, *, timeout=None, **kwargs):
        resp = MagicMock()
        resp.json.return_value = {"models": []}
        return resp

    monkeypatch.setattr(req_module, "get", fake_get)

    # Мокаем subprocess.run для `ollama pull`
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stderr = ""
    monkeypatch.setattr(sp, "run", lambda *a, **kw: fake_result)

    result = dummy._ask_ollama("system", "hello")

    assert call_count["post"] == 2, "Ожидалось два вызова requests.post (первый — 404, второй — повтор)"
    assert result == "повтор успешен", f"Неверный ответ: {result!r}"


# ──────────────────────────────────────────────────────────
# 8. HTTP 404 + неудачная загрузка модели → возвращает None
# ──────────────────────────────────────────────────────────

def test_ask_ollama_returns_none_when_model_pull_fails(monkeypatch):
    """Если скачать модель не удалось — _ask_ollama возвращает None."""
    dummy = _make_dummy_core()

    monkeypatch.setattr(dummy, "_ensure_ollama_running", lambda: True)

    import requests as req_module
    import subprocess as sp

    def fake_post(url, *, json=None, timeout=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {}
        return resp

    monkeypatch.setattr(req_module, "post", fake_post)

    def fake_get(url, *, timeout=None, **kwargs):
        resp = MagicMock()
        resp.json.return_value = {"models": []}
        return resp

    monkeypatch.setattr(req_module, "get", fake_get)

    # `ollama pull` завершается с ошибкой
    fake_result = MagicMock()
    fake_result.returncode = 1
    fake_result.stderr = "model not found"
    monkeypatch.setattr(sp, "run", lambda *a, **kw: fake_result)

    result = dummy._ask_ollama("system", "hello")

    assert result is None, f"Ожидался None при ошибке загрузки, получен: {result!r}"
