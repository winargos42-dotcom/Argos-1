import importlib
import importlib.util
import inspect
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def core_loader(monkeypatch):
    core_module = importlib.import_module("src.core")
    calls = []

    class Core:
        def _ensure_ollama_running(self):
            return True

        def _ensure_ollama_model(self, model):
            return model

    def load():
        calls.append("loaded")
        return Core

    monkeypatch.setattr(core_module, "_load_argos_core_class", load)
    monkeypatch.delitem(sys.modules, "src._argos_core_impl", raising=False)
    return core_module, Core, calls


@contextmanager
def run_blocking_fixture(test_file="test_file_operations.py"):
    path = Path(__file__).with_name("conftest.py")
    spec = importlib.util.spec_from_file_location("argos_isolation_conftest", path)
    module = importlib.util.module_from_spec(spec)
    request = SimpleNamespace(fspath=SimpleNamespace(basename=test_file))
    with pytest.MonkeyPatch.context() as scoped_patch:
        scoped_patch.setattr(sys, "path", sys.path.copy())
        spec.loader.exec_module(module)
        fixture = inspect.unwrap(module._patch_argoscore_blocking)(request, scoped_patch)
        next(fixture)
        try:
            yield
        finally:
            with pytest.raises(StopIteration):
                next(fixture)


def test_unrelated_test_does_not_load_core(core_loader):
    _, _, calls = core_loader

    with run_blocking_fixture():
        assert calls == []


def test_deferred_core_load_keeps_ollama_blocked_and_restores_methods(core_loader):
    core_module, core_class, calls = core_loader

    with run_blocking_fixture():
        loaded = core_module._load_argos_core_class()
        assert calls == ["loaded"]
        assert loaded is core_class
        assert loaded()._ensure_ollama_running() is False
        assert loaded()._ensure_ollama_model("model") is False

    assert core_class()._ensure_ollama_running() is True
    assert core_class()._ensure_ollama_model("model") == "model"


def test_cached_core_is_patched_without_loading_it_again(core_loader, monkeypatch):
    _, core_class, calls = core_loader
    monkeypatch.setitem(
        sys.modules, "src._argos_core_impl", SimpleNamespace(ArgosCore=core_class)
    )

    with run_blocking_fixture():
        assert calls == []
        assert core_class()._ensure_ollama_running() is False
        assert core_class()._ensure_ollama_model("model") is False


def test_ollama_behavior_tests_keep_their_own_mocks(core_loader):
    core_module, core_class, calls = core_loader

    with run_blocking_fixture("test_ollama_timeout_autostart.py"):
        assert calls == []
        assert core_module._load_argos_core_class() is core_class
        assert core_class()._ensure_ollama_running() is True
        assert core_class()._ensure_ollama_model("model") == "model"
