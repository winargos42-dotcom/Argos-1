from pathlib import Path


def test_agent_guard_delegates_outbound_policy_before_sync_dispatch() -> None:
    root = Path(__file__).resolve().parents[1]
    agent = (root / "src" / "agent.py").read_text(encoding="utf-8")
    guard = (root / "src" / "agent_guard.py").read_text(encoding="utf-8")

    assert "from src.agent_guard import AgentGuard" in agent
    assert "decision = self._guard.validate_step(step)" in agent
    assert "from src.external_action_guard import ExternalActionGuard" in guard
    assert "self._external_guard = ExternalActionGuard()" in guard
    assert 'source="agent.validate_step"' in guard
    assert "external_decision = self._external_guard.evaluate(" in guard
    assert "if not external_decision.allowed:" in guard

    agent_guard_pos = agent.index("decision = self._guard.validate_step(step)")
    dispatch_pos = agent.index("self._execute_step(step, admin, flasher)")
    assert agent_guard_pos < dispatch_pos
