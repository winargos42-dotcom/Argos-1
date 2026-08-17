"""Pytest configuration for Argoss test suite."""
import sys
import os
import tempfile
import threading
from unittest.mock import MagicMock, patch

# Ensure src/ is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_configure(config):
    """Configure pytest markers."""
    # Use system /tmp to avoid permission issues on mounted filesystems
    _sys_tmp = tempfile.gettempdir()  # capture BEFORE any override
    temp_root = os.path.join(_sys_tmp, "argoss-pytest-temp")
    os.makedirs(temp_root, exist_ok=True)
    for key in ("TMP", "TEMP", "TMPDIR"):
        os.environ[key] = temp_root
    tempfile.tempdir = temp_root
    os.environ.setdefault("ARGOS_P2P_BROADCAST_PORT", "0")

    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with -m 'not slow')")
    config.addinivalue_line("markers", "integration: marks tests requiring external services")
    config.addinivalue_line("markers", "hardware: marks tests requiring hardware (SDR, BLE, etc)")


import pytest


@pytest.fixture(autouse=True)
def _patch_argoscore_blocking(request, monkeypatch):
    """
    Патч для ArgosCore: блокирует запуск фоновых потоков и сетевых вызовов
    (Ollama, Redis, Telegram) во время тестов, чтобы избежать зависаний.
    Применяется автоматически ко всем тестам.
    """
    # Тесты в test_ollama_timeout_autostart.py проверяют реальное поведение Ollama —
    # не блокируем для них Ollama/localhost.
    _is_ollama_test = request.fspath.basename in {
        "test_ollama_timeout_autostart.py",
        "test_inference_resilience.py",
    }
    _noop = lambda *a, **kw: None
    _false = lambda *a, **kw: False
    _empty = lambda *a, **kw: {}

    # Патчим ollama — не пытаться подключиться
    if not _is_ollama_test:
        try:
            import ollama
            monkeypatch.setattr(ollama, "list", lambda: MagicMock(models=[]), raising=False)
            monkeypatch.setattr(ollama, "pull", _noop, raising=False)
        except ImportError:
            pass

    # Патчим redis — не подключаться
    try:
        import redis
        monkeypatch.setattr(redis.Redis, "ping", _false, raising=False)
        monkeypatch.setattr(redis.Redis, "set", _noop, raising=False)
        monkeypatch.setattr(redis.Redis, "get", lambda *a, **kw: None, raising=False)
    except ImportError:
        pass

    # Патчим requests.get/post для localhost — немедленный отказ (без 30с ожидания Ollama)
    # Не применяем для тестов Ollama (они тестируют реальное поведение)
    if not _is_ollama_test:
        try:
            import requests as _requests
            import requests.exceptions
            _orig_session_request = _requests.Session.request

            def _mock_session_request(self, method, url, *a, **kw):
                if any(h in str(url) for h in ("localhost", "127.0.0.1", "0.0.0.0")):
                    raise _requests.exceptions.ConnectionError("mock: localhost unreachable")
                return _orig_session_request(self, method, url, *a, **kw)

            monkeypatch.setattr(_requests.Session, "request", _mock_session_request, raising=False)
        except ImportError:
            pass

    # Патчим _ensure_ollama_running/model напрямую через реальный модуль
    # (ArgosCore загружается лениво через src._argos_core_impl)
    # Не применяем для тестов Ollama (они тестируют реальное поведение)
    if not _is_ollama_test:
        try:
            import sys as _sys
            from src.core import _load_argos_core_class
            _real_cls = _load_argos_core_class()
            monkeypatch.setattr(_real_cls, "_ensure_ollama_running",
                                lambda self: False, raising=False)
            monkeypatch.setattr(_real_cls, "_ensure_ollama_model",
                                lambda self, m: False, raising=False)
        except Exception:
            # Если класс ещё не загружен — патчим после загрузки через sys.modules
            _sentinel = {"patched": False}

            try:
                from src.core import _load_argos_core_class as _orig_load_fn

                def _patching_load():
                    cls = _orig_load_fn()
                    if not _sentinel["patched"]:
                        cls._ensure_ollama_running = lambda self: False
                        cls._ensure_ollama_model = lambda self, m: False
                        _sentinel["patched"] = True
                    return cls

                import src.core as _core_pkg
                monkeypatch.setattr(_core_pkg, "_load_argos_core_class",
                                    _patching_load, raising=False)
            except Exception:
                pass

    # Патчим Thread.start — позволяем daemon-потокам стартовать, но перехватываем
    # потоки с известными именами которые блокируют завершение
    _BLOCKING_THREAD_NAMES = {
        "_heartbeat_loop", "sysmon", "autobackup", "_dispatch_worker",
        "_dispatch_loop", "_loop", "arc3",
    }
    _original_start = threading.Thread.start

    def _safe_start(self):
        name = getattr(self, "_target", None)
        target_name = getattr(name, "__name__", "") if name else ""
        thread_name = getattr(self, "name", "") or ""
        if any(b in target_name or b in thread_name for b in _BLOCKING_THREAD_NAMES):
            self.daemon = True  # гарантируем daemon
        _original_start(self)

    monkeypatch.setattr(threading.Thread, "start", _safe_start, raising=False)

    yield
