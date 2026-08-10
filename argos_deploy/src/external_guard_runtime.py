"""Install external communication policy checks at ARGOS core command boundaries."""
from __future__ import annotations

import functools
import inspect
from typing import Any

from src.external_action_guard import ExternalActionGuard


_WRAPPED_ATTR = "_argos_external_guard_wrapped"


def _blocked_result(reason: str) -> dict[str, str]:
    return {
        "answer": f"BLOCKED:{reason}",
        "state": "Blocked",
    }


def install_external_action_guard(core: Any, guard: ExternalActionGuard | None = None) -> Any:
    """Wrap core text-command entry points once and fail closed on denied outreach.

    Autonomous runtime paths never provide owner approval here. A future explicit
    owner-confirmation surface may call ``ExternalActionGuard.evaluate`` itself with
    ``approved=True`` and invoke a lower-level sender only after that decision.
    """
    if core is None:
        return core

    guard = guard or ExternalActionGuard()

    for method_name in ("process", "process_logic", "process_logic_async"):
        original = getattr(core, method_name, None)
        if not callable(original) or getattr(original, _WRAPPED_ATTR, False):
            continue

        source = f"core.{method_name}"

        if inspect.iscoroutinefunction(original):

            @functools.wraps(original)
            async def async_wrapper(text, *args, __original=original, __source=source, **kwargs):
                decision = guard.evaluate(
                    str(text or ""),
                    actor="argos-core",
                    source=__source,
                    approved=False,
                )
                if not decision.allowed:
                    return _blocked_result(decision.reason)
                return await __original(text, *args, **kwargs)

            setattr(async_wrapper, _WRAPPED_ATTR, True)
            setattr(core, method_name, async_wrapper)
        else:

            @functools.wraps(original)
            def sync_wrapper(text, *args, __original=original, __source=source, **kwargs):
                decision = guard.evaluate(
                    str(text or ""),
                    actor="argos-core",
                    source=__source,
                    approved=False,
                )
                if not decision.allowed:
                    return _blocked_result(decision.reason)
                return __original(text, *args, **kwargs)

            setattr(sync_wrapper, _WRAPPED_ATTR, True)
            setattr(core, method_name, sync_wrapper)

    return core
