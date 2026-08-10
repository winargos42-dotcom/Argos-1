# External Action Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed outbound communication guard and audit log to ARGOS autonomous execution paths.

**Architecture:** A focused `src/external_action_guard.py` classifies communication commands and emits privacy-safe JSONL audit events. `src/agent.py` and `src/mcp_api.py` invoke it before generic command dispatch so future email/Telegram/webhook skills cannot bypass policy through those autonomous surfaces.

**Tech Stack:** Python 3.11, dataclasses, pathlib, hashlib, json, pytest.

## Global Constraints

- `EXTERNAL_SEND_ENABLED=false` by default.
- `EXTERNAL_DRAFT_ONLY=true` by default.
- `EXTERNAL_REQUIRE_OWNER_APPROVAL=true` by default.
- Autonomous agent and MCP command paths pass `approved=False`.
- Interactive authenticated Telegram replies are not globally blocked.
- Outbound policy failures fail closed.
- Audit output must redact obvious secrets, URLs, and email addresses.

---

### Task 1: Guard behavior and audit tests

**Files:**
- Create: `argos_deploy/tests/test_external_action_guard.py`
- Create: `argos_deploy/src/external_action_guard.py`

**Interfaces:**
- Produces: `ExternalActionDecision`, `ExternalActionGuard.evaluate(text, actor="argos", source="unknown", approved=False)`.

- [ ] **Step 1: Write failing tests** for default blocking, read-only allowance, Telegram/webhook detection, approval gating, and audit redaction.
- [ ] **Step 2: Run `pytest argos_deploy/tests/test_external_action_guard.py -q` and confirm failure because the module does not exist.**
- [ ] **Step 3: Implement minimal guard** with environment parsing, text classification, fail-closed audit, SHA-256 payload hash, and redacted preview.
- [ ] **Step 4: Run the guard tests and confirm PASS.**
- [ ] **Step 5: Commit the guard and tests.**

### Task 2: Agent execution boundary

**Files:**
- Modify: `argos_deploy/src/agent.py`
- Create: `argos_deploy/tests/test_agent_external_action_guard_contract.py`

**Interfaces:**
- Consumes: `ExternalActionGuard.evaluate(...)`.

- [ ] **Step 1: Write a failing contract test** asserting that both synchronous plan steps and asynchronous chain tasks call the external guard before core dispatch.
- [ ] **Step 2: Run the contract test and confirm it fails.**
- [ ] **Step 3: Add one `ExternalActionGuard` instance to `ArgosAgent` and block matched outbound tasks with a clear `BLOCKED:external_...` result.**
- [ ] **Step 4: Run agent contract and existing agent tests.**
- [ ] **Step 5: Commit.**

### Task 3: MCP command execution boundary

**Files:**
- Modify: `argos_deploy/src/mcp_api.py`
- Create: `argos_deploy/tests/test_mcp_external_action_guard_contract.py`

**Interfaces:**
- Consumes: `ExternalActionGuard.evaluate(...)`.

- [ ] **Step 1: Write a failing contract test** asserting `_run_command` evaluates text through the guard before `process_logic_async`.
- [ ] **Step 2: Run the contract test and confirm it fails.**
- [ ] **Step 3: Integrate the guard into `ArgosMCPServer`; return a blocked policy message without invoking core when denied.**
- [ ] **Step 4: Run MCP contract and existing MCP tests.**
- [ ] **Step 5: Commit.**

### Task 4: Runtime defaults and verification

**Files:**
- Modify: `argos_deploy/docker-entrypoint.sh`
- Create: `argos_deploy/tests/test_external_action_runtime_defaults.py`

**Interfaces:**
- Produces exported runtime defaults visible to the Python process.

- [ ] **Step 1: Write a failing contract test** requiring the entrypoint to export `EXTERNAL_SEND_ENABLED`, `EXTERNAL_DRAFT_ONLY`, and `EXTERNAL_REQUIRE_OWNER_APPROVAL` with safe defaults.
- [ ] **Step 2: Run and confirm failure.**
- [ ] **Step 3: Add shell defaults using `${VAR:-safe_value}` so Railway can override intentionally without changing code.**
- [ ] **Step 4: Run focused tests and the full `argos_deploy/tests` suite.**
- [ ] **Step 5: Commit and inspect CI before merge.**
