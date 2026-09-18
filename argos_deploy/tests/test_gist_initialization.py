import ast
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


DEPLOY_ROOT = Path(__file__).resolve().parents[1]
CORE_PATHS = [DEPLOY_ROOT.parent / "src" / "core.py", DEPLOY_ROOT / "src" / "core.py"]


@pytest.fixture
def gist_classes():
    path = DEPLOY_ROOT / "src" / "argos_c2_gist.py"
    spec = importlib.util.spec_from_file_location("argos_gist_constructor_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=CORE_PATHS, ids=["root-core", "deployment-core"])
def initializer(request, gist_classes, monkeypatch):
    for name in (
        "ARGOS_GIST_ID", "ARGOS_GITHUB_TOKEN", "GIST_ID", "GITHUB_TOKEN", "GIST_TOKEN"
    ):
        monkeypatch.delenv(name, raising=False)
    tree = ast.parse(request.param.read_text(encoding="utf-8-sig"))
    core = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ArgosCore")
    initialize = next(node for node in core.body if isinstance(node, ast.FunctionDef) and node.name == "_init_c2_system")
    read_secret = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_read_secret_env")
    placeholders = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_PLACEHOLDER_SECRET_VALUES" for target in node.targets)
    )
    namespace = {
        "os": os,
        "C2_AVAILABLE": True,
        "GistC2": gist_classes.GistC2,
        "GhostDroneClient": gist_classes.GhostDroneClient,
        "log": Mock(),
    }
    body = ast.Module(body=[placeholders, read_secret, initialize], type_ignores=[])
    exec(compile(body, str(request.param), "exec"), namespace)
    return namespace


def test_missing_modules_clear_clients_without_initializing(initializer):
    initializer["C2_AVAILABLE"] = False
    initializer["GistC2"] = Mock()
    initializer["GhostDroneClient"] = Mock()
    core = SimpleNamespace(c2_gist=object(), c2_drone=object())

    initializer["_init_c2_system"](core)

    assert core.c2_gist is None
    assert core.c2_drone is None
    initializer["GistC2"].assert_not_called()
    initializer["GhostDroneClient"].assert_not_called()


@pytest.mark.parametrize("values", [
    {},
    {"ARGOS_GIST_ID": "test-gist"},
    {"ARGOS_GITHUB_TOKEN": "test-gist-token"},
    {"GIST_ID": "test-generic-gist", "GITHUB_TOKEN": "test-generic-token", "GIST_TOKEN": "test-report-token"},
    {"ARGOS_GIST_ID": " ", "ARGOS_GITHUB_TOKEN": "test-gist-token"},
    {"ARGOS_GIST_ID": "test-gist", "ARGOS_GITHUB_TOKEN": "your_token_here"},
])
def test_unconfigured_c2_does_not_construct_clients(initializer, monkeypatch, values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    initializer["GistC2"] = Mock()
    initializer["GhostDroneClient"] = Mock()
    core = SimpleNamespace(c2_gist=object(), c2_drone=object())

    initializer["_init_c2_system"](core)

    assert core.c2_gist is None
    assert core.c2_drone is None
    initializer["GistC2"].assert_not_called()
    initializer["GhostDroneClient"].assert_not_called()


def test_explicit_configuration_matches_real_constructors_without_io(
    initializer, gist_classes, monkeypatch
):
    monkeypatch.setenv("ARGOS_GIST_ID", " test-gist ")
    monkeypatch.setenv("ARGOS_GITHUB_TOKEN", " test-gist-token ")

    def reject_io(*args, **kwargs):
        raise AssertionError("Initializing C2 must not start transports or threads")

    monkeypatch.setattr("requests.sessions.Session.request", reject_io)
    monkeypatch.setattr("socket.socket.connect", reject_io)
    monkeypatch.setattr("socket.getaddrinfo", reject_io)
    monkeypatch.setattr("threading.Thread.start", reject_io)
    core = SimpleNamespace()

    initializer["_init_c2_system"](core)

    assert isinstance(core.c2_gist, gist_classes.GistC2)
    assert isinstance(core.c2_drone, gist_classes.GhostDroneClient)
    for client in (core.c2_gist, core.c2_drone):
        assert client.gist_id == "test-gist"
        assert client.token == "test-gist-token"
    assert core.c2_gist.running is False
    assert core.c2_drone.last_ts == 0
    messages = " ".join(str(call) for call in initializer["log"].method_calls)
    assert "API не проверен" in messages
    assert "слушатели не запущены" in messages
    assert "test-gist-token" not in messages


def test_constructor_failure_leaves_both_clients_disabled(initializer, monkeypatch):
    monkeypatch.setenv("ARGOS_GIST_ID", "test-gist")
    monkeypatch.setenv("ARGOS_GITHUB_TOKEN", "test-gist-token")
    failed_constructor = Mock(side_effect=RuntimeError("Constructor unavailable"))
    initializer["GhostDroneClient"] = failed_constructor
    core = SimpleNamespace()

    initializer["_init_c2_system"](core)

    failed_constructor.assert_called_once_with(gist_id="test-gist", github_token="test-gist-token")
    assert core.c2_gist is None
    assert core.c2_drone is None
