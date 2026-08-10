from __future__ import annotations

import re
from dataclasses import dataclass

from src.external_action_guard import ExternalActionGuard


@dataclass
class GuardDecision:
    allowed: bool
    reason: str
    sanitized: str = ""


class AgentGuard:
    CODE_MARKERS = (
        "@dataclass",
        "class ",
        "def ",
        "from ",
        "import ",
        "return ",
        "optional[",
        "bool = ",
        "str = ",
        "none",
        "try:",
        "except",
        "`",
        "async def ",
        "await ",
    )

    COMMAND_WHITELIST = {
        "executelocal",
        "execute_local",
        "askcloud",
        "ask_cloud",
        "delegatep2p",
        "delegate_p2p",
        "defer",
        "status",
        "help",
        "memory",
        "note",
        "ping",
        "tail",
        "logs",
        "safe",
        "normal",
        "restart",
        "rollback",
    }

    def __init__(self) -> None:
        self._external_guard = ExternalActionGuard()

    def is_probably_code(self, text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return False
        if any(m in t for m in self.CODE_MARKERS):
            return True
        if "\n" in t and any(x in t for x in ("=", ":", "(", ")")):
            return True
        return False

    def sanitize(self, text: str) -> str:
        t = (text or "").strip()
        t = re.sub(r"\s+", " ", t)
        return t[:400]

    def validate_step(self, step_text: str) -> GuardDecision:
        raw = step_text or ""
        sanitized = self.sanitize(raw)
        lower = sanitized.lower()

        if not sanitized:
            return GuardDecision(False, "empty_step")

        external_decision = self._external_guard.evaluate(
            sanitized,
            actor="argos-agent",
            source="agent.validate_step",
            approved=False,
        )
        if not external_decision.allowed:
            return GuardDecision(False, f"external:{external_decision.reason}", sanitized)

        if self.is_probably_code(raw):
            return GuardDecision(False, "code_detected", sanitized)

        first_token = lower.split(" ", 1)[0]
        if first_token not in self.COMMAND_WHITELIST:
            return GuardDecision(False, f"command_not_whitelisted:{first_token}", sanitized)

        return GuardDecision(True, "ok", sanitized)

    def split_plan(self, text: str) -> list[str]:
        if not text:
            return []
        chunks = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            chunks.append(line)
        return chunks

    def validate_plan(self, text: str) -> list[GuardDecision]:
        return [self.validate_step(step) for step in self.split_plan(text)]


def handle_agent_step(step_text: str, execute_fn) -> str:
    guard = AgentGuard()
    decision = guard.validate_step(step_text)
    if not decision.allowed:
        return f"BLOCKED:{decision.reason}"
    return str(execute_fn(decision.sanitized))
