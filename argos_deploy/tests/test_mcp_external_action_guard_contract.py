from pathlib import Path


def test_cloud_runtime_wraps_core_before_mcp_is_created() -> None:
    root = Path(__file__).resolve().parents[1]
    cloud = (root / "cloud_entry.py").read_text(encoding="utf-8")
    mcp = (root / "src" / "mcp_api.py").read_text(encoding="utf-8")

    assert "from src.external_guard_runtime import install_external_action_guard" in cloud
    assert "install_external_action_guard(core)" in cloud
    assert "mcp = ArgosMCPServer(core=core, admin=admin)" in cloud
    assert "result = await self.core.process_logic_async(text, self.admin, None)" in mcp

    install_pos = cloud.index("install_external_action_guard(core)")
    mcp_pos = cloud.index("mcp = ArgosMCPServer(core=core, admin=admin)")
    assert install_pos < mcp_pos
