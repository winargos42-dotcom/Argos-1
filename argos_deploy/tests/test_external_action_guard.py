from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "external_action_guard.py"


def _load_module():
    assert MODULE_PATH.exists(), "external_action_guard.py must exist"
    spec = importlib.util.spec_from_file_location("argos_deploy_external_action_guard", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clear_policy_env(monkeypatch):
    for name in (
        "EXTERNAL_SEND_ENABLED",
        "EXTERNAL_DRAFT_ONLY",
        "EXTERNAL_REQUIRE_OWNER_APPROVAL",
        "EXTERNAL_ACTION_AUDIT_PATH",
        "ARGOS_STATE_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_policy_blocks_outbound_email(monkeypatch, tmp_path):
    _clear_policy_env(monkeypatch)
    module = _load_module()
    guard = module.ExternalActionGuard(audit_path=tmp_path / "audit.jsonl")

    decision = guard.evaluate(
        "send email to support@example.com about ARGOS REBOOT",
        actor="argos-agent",
        source="agent",
    )

    assert decision.external is True
    assert decision.allowed is False
    assert decision.reason == "external_send_disabled"
    assert decision.target == "email"


def test_read_only_gmail_check_is_allowed(monkeypatch, tmp_path):
    _clear_policy_env(monkeypatch)
    module = _load_module()
    audit_path = tmp_path / "audit.jsonl"
    guard = module.ExternalActionGuard(audit_path=audit_path)

    decision = guard.evaluate(
        "Check Gmail for new replies from FastAPI Cloud and summarize them",
        actor="argos-watch",
        source="monitor",
    )

    assert decision.external is False
    assert decision.allowed is True
    assert decision.reason == "not_external_outbound"
    assert not audit_path.exists()


def test_draft_instruction_with_explicit_do_not_send_is_allowed(monkeypatch, tmp_path):
    _clear_policy_env(monkeypatch)
    module = _load_module()
    audit_path = tmp_path / "audit.jsonl"
    guard = module.ExternalActionGuard(audit_path=audit_path)

    decision = guard.evaluate(
        "напиши черновик письма в support, но не отправляй",
        actor="argos-agent",
        source="agent",
    )

    assert decision.external is False
    assert decision.allowed is True
    assert decision.reason == "not_external_outbound"
    assert not audit_path.exists()


def test_telegram_and_webhook_outreach_are_detected(monkeypatch, tmp_path):
    _clear_policy_env(monkeypatch)
    module = _load_module()
    guard = module.ExternalActionGuard(audit_path=tmp_path / "audit.jsonl")

    telegram = guard.evaluate("отправь сообщение в Telegram партнёру", source="agent")
    webhook = guard.evaluate("POST result to webhook https://example.test/hook", source="agent")

    assert telegram.external is True
    assert telegram.allowed is False
    assert telegram.target == "telegram"
    assert webhook.external is True
    assert webhook.allowed is False
    assert webhook.target == "webhook"


def test_enablement_without_owner_approval_is_still_blocked(monkeypatch, tmp_path):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("EXTERNAL_SEND_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_DRAFT_ONLY", "false")
    monkeypatch.setenv("EXTERNAL_REQUIRE_OWNER_APPROVAL", "true")
    module = _load_module()
    guard = module.ExternalActionGuard(audit_path=tmp_path / "audit.jsonl")

    decision = guard.evaluate("reply to support by email", approved=False)

    assert decision.external is True
    assert decision.allowed is False
    assert decision.reason == "owner_approval_required"


def test_explicit_approval_allows_send_when_policy_switches_allow_it(monkeypatch, tmp_path):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("EXTERNAL_SEND_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_DRAFT_ONLY", "false")
    monkeypatch.setenv("EXTERNAL_REQUIRE_OWNER_APPROVAL", "true")
    module = _load_module()
    guard = module.ExternalActionGuard(audit_path=tmp_path / "audit.jsonl")

    decision = guard.evaluate("reply to support by email", approved=True)

    assert decision.external is True
    assert decision.allowed is True
    assert decision.reason == "approved"


def test_allowed_send_fails_closed_when_audit_cannot_be_written(monkeypatch, tmp_path):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("EXTERNAL_SEND_ENABLED", "true")
    monkeypatch.setenv("EXTERNAL_DRAFT_ONLY", "false")
    monkeypatch.setenv("EXTERNAL_REQUIRE_OWNER_APPROVAL", "true")
    module = _load_module()
    guard = module.ExternalActionGuard(audit_path=tmp_path)

    decision = guard.evaluate("reply to support by email", approved=True)

    assert decision.external is True
    assert decision.allowed is False
    assert decision.reason == "audit_write_failed"
    assert decision.audit_written is False


def test_audit_log_redacts_sensitive_payload(monkeypatch, tmp_path):
    _clear_policy_env(monkeypatch)
    module = _load_module()
    audit_path = tmp_path / "audit.jsonl"
    guard = module.ExternalActionGuard(audit_path=audit_path)

    guard.evaluate(
        "send email to alice@example.com via webhook https://example.test/hook?token=supersecret "
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
        actor="argos-agent",
        source="agent",
    )

    event = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    encoded = json.dumps(event, ensure_ascii=False)

    assert event["actor"] == "argos-agent"
    assert event["source"] == "agent"
    assert event["prepared"] is True
    assert event["approved"] is False
    assert len(event["payload_sha256"]) == 64
    assert "alice@example.com" not in encoded
    assert "supersecret" not in encoded
    assert "abcdefghijklmnopqrstuvwxyz012345" not in encoded
    assert "https://example.test/hook?token=supersecret" not in encoded
