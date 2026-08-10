"""Fail-closed policy gate for autonomous external communications.

The guard intentionally protects *communication* commands, not ordinary network
access such as model inference or read-only web/Gmail checks. Callers that may
execute autonomous text commands should evaluate the command before dispatch.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_TRUE_VALUES = {"1", "true", "yes", "on", "да", "вкл"}

_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("send", re.compile(r"\b(send|sending)\b|\bотправ(?:ь|ить|ьте|ляй|лять)\w*", re.IGNORECASE)),
    ("reply", re.compile(r"\b(reply|respond)\b|\bответ(?:ь|ить|ьте)\b", re.IGNORECASE)),
    ("forward", re.compile(r"\bforward\b|\bперешл(?:и|ите|ать)\w*", re.IGNORECASE)),
    ("contact", re.compile(r"\bcontact\b|\bсвяж(?:ись|итесь)|\bсвязаться\b", re.IGNORECASE)),
    ("publish", re.compile(r"\bpublish\b|\bопубликовать\b|\bопубликуй\w*", re.IGNORECASE)),
    ("post", re.compile(r"\bpost\b|\bзапост(?:ить|и)\w*", re.IGNORECASE)),
)

_TARGET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("webhook", re.compile(r"\bwebhook\b|\bвебхук\b", re.IGNORECASE)),
    ("telegram", re.compile(r"\btelegram\b|\bтелеграм(?:м)?\w*", re.IGNORECASE)),
    ("email", re.compile(r"\b(?:e-?mail|gmail|smtp|mail)\b|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)),
    ("slack", re.compile(r"\bslack\b", re.IGNORECASE)),
    ("whatsapp", re.compile(r"\bwhats?app\b|\bватсап\w*", re.IGNORECASE)),
    ("max", re.compile(r"\bmax\b.*\bbot\b|\bmax messenger\b", re.IGNORECASE)),
    ("support", re.compile(r"\bsupport\b|\bhelpdesk\b|\bподдержк\w*", re.IGNORECASE)),
    ("press", re.compile(r"\bpress\b|\bmedia\b|\bпресс\w*|\bсми\b", re.IGNORECASE)),
    ("company", re.compile(r"\bcompany\b|\bpartner\b|\bкомпани\w*|\bпартн[её]р\w*", re.IGNORECASE)),
    ("community", re.compile(r"\bhabr\b|\b4pda\b|\bforum\b|\bcommunity\b|\bфорум\w*|\bсообществ\w*", re.IGNORECASE)),
)

_NEGATED_ACTION_RE = re.compile(
    r"(?:\bdo\s+not\s+|\bdon['’]t\s+)(?:send|reply|respond|forward|contact|publish|post)\b"
    r"|\bне\s+(?:отправ(?:ляй|лять|ь|ить|ьте)\w*|ответ(?:ь|ить|ьте|чай)\w*|"
    r"перешл(?:и|ите|ать)\w*|связывайся|связываться|свяжись|публикуй\w*|"
    r"опубликовать|постить|запости\w*)",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_OPAQUE_SECRET_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


class ExternalActionDecision:
    __slots__ = ("allowed", "external", "reason", "action", "target", "audit_written")

    def __init__(
        self,
        *,
        allowed: bool,
        external: bool,
        reason: str,
        action: str = "none",
        target: str = "none",
        audit_written: bool = False,
    ) -> None:
        self.allowed = allowed
        self.external = external
        self.reason = reason
        self.action = action
        self.target = target
        self.audit_written = audit_written

    def __repr__(self) -> str:
        return (
            "ExternalActionDecision("
            f"allowed={self.allowed!r}, external={self.external!r}, reason={self.reason!r}, "
            f"action={self.action!r}, target={self.target!r}, audit_written={self.audit_written!r})"
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _canonical_match(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> str | None:
    for name, pattern in patterns:
        if pattern.search(text):
            return name
    return None


def _redacted_preview(text: str, limit: int = 220) -> str:
    preview = " ".join((text or "").split())
    preview = _BEARER_RE.sub("Bearer <secret>", preview)
    preview = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=<secret>", preview)
    preview = _EMAIL_RE.sub("<email>", preview)
    preview = _URL_RE.sub("<url>", preview)
    preview = _OPAQUE_SECRET_RE.sub("<secret>", preview)
    if len(preview) > limit:
        preview = preview[: limit - 1] + "…"
    return preview


class ExternalActionGuard:
    """Evaluate outbound communication policy and append privacy-safe audit events."""

    def __init__(self, audit_path: str | os.PathLike[str] | None = None) -> None:
        self.audit_path = Path(audit_path) if audit_path is not None else self._default_audit_path()

    @staticmethod
    def _default_audit_path() -> Path:
        configured = (
            os.getenv("EXTERNAL_ACTION_AUDIT_PATH", "").strip()
            or os.getenv("EXTERNAL_ACTION_LOG", "").strip()
        )
        if configured:
            return Path(configured)
        state_root = Path(os.getenv("ARGOS_STATE_ROOT", "persist") or "persist")
        return state_root / "logs" / "external_actions_audit.jsonl"

    @staticmethod
    def classify(text: str) -> tuple[bool, str, str]:
        clean = text or ""
        action_text = _NEGATED_ACTION_RE.sub(" ", clean)
        action = _canonical_match(action_text, _ACTION_PATTERNS)
        target = _canonical_match(clean, _TARGET_PATTERNS)
        return bool(action and target), action or "none", target or "none"

    def evaluate(
        self,
        text: str,
        *,
        actor: str = "argos",
        source: str = "unknown",
        approved: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ExternalActionDecision:
        external, action, target = self.classify(text)
        if not external:
            return ExternalActionDecision(
                allowed=True,
                external=False,
                reason="not_external_outbound",
                action=action,
                target=target,
                audit_written=False,
            )

        send_enabled = _env_bool("EXTERNAL_SEND_ENABLED", False)
        draft_only = _env_bool("EXTERNAL_DRAFT_ONLY", True)
        approval_required = _env_bool("EXTERNAL_REQUIRE_OWNER_APPROVAL", True)

        if not send_enabled:
            allowed = False
            reason = "external_send_disabled"
        elif draft_only:
            allowed = False
            reason = "draft_only"
        elif approval_required and not approved:
            allowed = False
            reason = "owner_approval_required"
        else:
            allowed = True
            reason = "approved"

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": str(actor or "argos"),
            "source": str(source or "unknown"),
            "action": action,
            "target": target,
            "payload_sha256": hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest(),
            "payload_preview": _redacted_preview(text or ""),
            "prepared": True,
            "status": "allowed" if allowed else "blocked",
            "approval_required": approval_required,
            "approved": bool(approved),
            "external_send_enabled": send_enabled,
            "draft_only": draft_only,
        }
        if metadata:
            event["metadata"] = self._sanitize_metadata(metadata)

        try:
            self._append_event(event)
            audit_written = True
        except Exception:
            audit_written = False
            if allowed:
                allowed = False
                reason = "audit_write_failed"

        return ExternalActionDecision(
            allowed=allowed,
            external=True,
            reason=reason,
            action=action,
            target=target,
            audit_written=audit_written,
        )

    @staticmethod
    def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in metadata.items():
            key_s = str(key)[:80]
            if isinstance(value, (str, int, float, bool)) or value is None:
                value_s = str(value) if isinstance(value, str) else value
                safe[key_s] = _redacted_preview(value_s, limit=160) if isinstance(value_s, str) else value_s
            else:
                safe[key_s] = f"<{type(value).__name__}>"
        return safe

    def _append_event(self, event: dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
