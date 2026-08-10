from pathlib import Path


def test_agent_checks_external_guard_before_core_dispatch() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "agent.py").read_text(encoding="utf-8")

    assert "from src.external_action_guard import ExternalActionGuard" in source
    assert "self._external_guard = ExternalActionGuard()" in source
    assert 'source="agent.execute_plan"' in source
    assert 'source="agent.run_chain"' in source
    assert "if not external_decision.allowed:" in source

    sync_guard = source.index('source="agent.execute_plan"')
    sync_dispatch = source.index("self._execute_step(step, admin, flasher)")
    assert sync_guard < sync_dispatch

    async_guard = source.index('source="agent.run_chain"')
    async_dispatch = source.index("result = self.core.process(task)")
    assert async_guard < async_dispatch
