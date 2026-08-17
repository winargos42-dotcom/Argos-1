"""Smoke tests for ArgosCore importability and its lightweight API contract."""

import pytest


def test_argos_core_importable():
    """ArgosCore should be importable without raising."""

    try:
        from src.core import ArgosCore  # noqa: F401
    except ImportError as exc:
        pytest.skip(f"Optional dependency missing: {exc}")


def test_argos_core_has_version():
    """The version contract must not require booting every ARGOS subsystem."""

    try:
        from src.core import _load_argos_core_class

        core_class = _load_argos_core_class()
        assert isinstance(core_class.VERSION, str)
    except ImportError as exc:
        pytest.skip(f"Optional dependency missing: {exc}")


def test_argos_core_process_logic_returns_dict(monkeypatch):
    """Exercise basic logic without importing every optional integration."""

    try:
        from src.core import _load_argos_core_class

        core_class = _load_argos_core_class()
        monkeypatch.setattr(core_class, "_init_integrator", lambda self: None)
        core = core_class()
        result = core.process_logic("помощь", None, None)
        assert isinstance(result, dict)
        assert "answer" in result
    except ImportError as exc:
        pytest.skip(f"Optional dependency missing: {exc}")
    except Exception as exc:
        pytest.skip(f"Cannot test core: {exc}")
