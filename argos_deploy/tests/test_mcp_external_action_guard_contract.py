from pathlib import Path


def test_mcp_command_checks_external_guard_before_core_dispatch() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "mcp_api.py").read_text(encoding="utf-8")

    assert "from src.external_action_guard import ExternalActionGuard" in source
    assert "self._external_guard = ExternalActionGuard()" in source
    assert 'source="mcp.command"' in source
    assert "if not external_decision.allowed:" in source

    guard_pos = source.index('source="mcp.command"')
    dispatch_pos = source.index("result = await self.core.process_logic_async(text, self.admin, None)")
    assert guard_pos < dispatch_pos
