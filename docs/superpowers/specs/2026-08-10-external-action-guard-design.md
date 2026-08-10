# External Action Guard Design

## Goal

Prevent autonomous ARGOS workflows from sending external communications unless a caller supplies explicit owner approval, while preserving normal read-only monitoring and interactive Telegram replies to the owner.

## Scope

Protected outbound communication intents include email/Gmail/SMTP, support/helpdesk/contact forms, proactive Telegram outreach, webhook posting, Slack/WhatsApp/MAX outreach, press/company/community publication, and similar agent-initiated messaging.

Read-only operations (checking Gmail, reading replies, fetching status, searching the web) remain allowed. Normal Telegram replies that are a direct response to an authenticated inbound owner message are not treated as outreach and are not globally disabled.

## Default policy

- `EXTERNAL_SEND_ENABLED=false`
- `EXTERNAL_DRAFT_ONLY=true`
- `EXTERNAL_REQUIRE_OWNER_APPROVAL=true`
- Default audit path: `${ARGOS_STATE_ROOT:-persist}/logs/external_actions_audit.jsonl`

An outbound communication is allowed only when all of the following are true:

1. external sending is enabled;
2. draft-only mode is disabled;
3. owner approval is required and the caller provides `approved=True` (or approval is explicitly configured as not required).

Autonomous paths in this change always call the guard with `approved=False`, therefore they cannot send externally.

## Architecture

Create `src/external_action_guard.py` as the single policy and audit component. It exposes a small decision API that classifies text commands, records a privacy-safe JSONL event, and returns an allow/block decision.

Integrate it at two high-leverage execution boundaries in the Railway runtime copy:

- `src/agent.py`: before an agent executes each step or asynchronous chain task;
- `src/mcp_api.py`: before the generic MCP `command` tool dispatches text into ARGOS core.

This blocks agent/MCP routes even if a downstream skill later acquires Gmail, SMTP, Telegram, or webhook capabilities.

## Classification

A command is considered outbound communication only when it contains both:

- an outbound action marker, such as send/reply/contact/forward/publish/post or Russian equivalents; and
- an external channel/target marker, such as email/Gmail/SMTP/support/Telegram/webhook/Slack/WhatsApp/MAX/press/company/community.

Read verbs without outbound markers do not match.

## Audit format

Each detected outbound attempt appends one JSON object with:

- `timestamp`
- `actor`
- `source`
- `action`
- `target`
- `payload_sha256`
- `payload_preview` (redacted and truncated)
- `prepared`
- `status`
- `approval_required`
- `approved`
- `external_send_enabled`
- `draft_only`

The audit must never store obvious bearer tokens, API keys, full URLs with query strings, or raw email addresses.

## Failure behavior

Fail closed for outbound intent: if the guard cannot evaluate policy or write the audit, the autonomous outbound action is blocked rather than sent.

Non-outbound commands must not be blocked by audit filesystem failures.

## Tests

Tests must prove:

1. default environment blocks outbound email/support requests;
2. read-only Gmail checks are allowed;
3. proactive Telegram/webhook sends are detected;
4. enablement alone is insufficient when approval is missing;
5. explicit approval can allow a send when all policy switches permit it;
6. audit JSONL records required fields and redacts sensitive values;
7. agent and MCP generic command paths call the guard before core execution.

## Non-goals

This change does not rewrite every network client in ARGOS and does not block AI inference/network fetches. It establishes a centralized policy boundary for communication commands and protects the autonomous execution surfaces currently exposed by Railway.