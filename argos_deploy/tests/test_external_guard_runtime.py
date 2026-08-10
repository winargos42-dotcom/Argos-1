from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "external_guard_runtime.py"


def _load_module():
    assert MODULE_PATH.exists(), "external_guard_runtime.py must exist"
    spec = importlib.util.spec_from_file_location("argos_deploy_external_guard_runtime", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Decision:
    def __init__(self, allowed: bool, reason: str):
        self.allowed = allowed
        self.reason = reason


class _FakeGuard:
    def __init__(self, allowed: bool):
        self.allowed = allowed
        self.calls = []

    def evaluate(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return _Decision(self.allowed, "approved" if self.allowed else "external_send_disabled")


class _Core:
    def __init__(self):
        self.calls = []

    def process(self, text):
        self.calls.append(("process", text))
        return {"answer": f"process:{text}"}

    def process_logic(self, text, admin=None, flasher=None):
        self.calls.append(("process_logic", text))
        return {"answer": f"logic:{text}"}

    async def process_logic_async(self, text, admin=None, flasher=None):
        self.calls.append(("process_logic_async", text))
        return {"answer": f"async:{text}"}


def test_runtime_wrapper_blocks_sync_core_dispatch():
    module = _load_module()
    core = _Core()
    guard = _FakeGuard(allowed=False)

    module.install_external_action_guard(core, guard=guard)
    result = core.process("send email to support@example.com")

    assert result["state"] == "Blocked"
    assert "external_send_disabled" in result["answer"]
    assert core.calls == []
    assert guard.calls[0][1]["source"] == "core.process"
    assert guard.calls[0][1]["approved"] is False


def test_runtime_wrapper_allows_nonblocked_sync_dispatch():
    module = _load_module()
    core = _Core()
    guard = _FakeGuard(allowed=True)

    module.install_external_action_guard(core, guard=guard)
    result = core.process_logic("status", None, None)

    assert result == {"answer": "logic:status"}
    assert core.calls == [("process_logic", "status")]


def test_runtime_wrapper_blocks_async_core_dispatch():
    module = _load_module()
    core = _Core()
    guard = _FakeGuard(allowed=False)

    module.install_external_action_guard(core, guard=guard)
    result = asyncio.run(core.process_logic_async("post result to webhook", None, None))

    assert result["state"] == "Blocked"
    assert core.calls == []
    assert guard.calls[0][1]["source"] == "core.process_logic_async"


def test_runtime_wrapper_is_idempotent():
    module = _load_module()
    core = _Core()
    guard = _FakeGuard(allowed=True)

    module.install_external_action_guard(core, guard=guard)
    module.install_external_action_guard(core, guard=guard)
    core.process("status")

    assert len(guard.calls) == 1
    assert core.calls == [("process", "status")]
