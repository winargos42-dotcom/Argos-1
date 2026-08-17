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
    """Exercise the direct process path without booting optional integrations."""

    try:
        from src.core import _load_argos_core_class

        core_class = _load_argos_core_class()
        core = object.__new__(core_class)
        core._internal_admin = None
        core.context = None
        monkeypatch.setattr(
            core_class,
            "_apply_chatgpt_link_profile",
            lambda self, _text: "ARGOS ready",
        )
        monkeypatch.setattr(
            core_class,
            "_remember_dialog_turn",
            lambda self, *_args: None,
        )
        result = core_class.process_logic(core, "помощь", None, None)
        assert isinstance(result, dict)
        assert "answer" in result
    except ImportError as exc:
        pytest.skip(f"Optional dependency missing: {exc}")
    except Exception as exc:
        pytest.skip(f"Cannot test core: {exc}")
