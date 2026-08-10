#!/bin/sh
set -eu

CODEX_HOME="${CODEX_HOME:-/codex-home}"
ARGOS_CODEX_WORKDIR="${ARGOS_CODEX_WORKDIR:-/app}"
ARGOS_STATE_ROOT="${ARGOS_STATE_ROOT:-/app/persist}"
EXTERNAL_SEND_ENABLED="${EXTERNAL_SEND_ENABLED:-false}"
EXTERNAL_DRAFT_ONLY="${EXTERNAL_DRAFT_ONLY:-true}"
EXTERNAL_REQUIRE_OWNER_APPROVAL="${EXTERNAL_REQUIRE_OWNER_APPROVAL:-true}"
EXTERNAL_ACTION_AUDIT_PATH="${EXTERNAL_ACTION_AUDIT_PATH:-$ARGOS_STATE_ROOT/logs/external_actions_audit.jsonl}"
PROBE_FILE="$ARGOS_STATE_ROOT/.railway_persistence_probe"

export CODEX_HOME ARGOS_CODEX_WORKDIR ARGOS_STATE_ROOT
export EXTERNAL_SEND_ENABLED EXTERNAL_DRAFT_ONLY EXTERNAL_REQUIRE_OWNER_APPROVAL
export EXTERNAL_ACTION_AUDIT_PATH

mkdir -p "$CODEX_HOME" "$ARGOS_STATE_ROOT/data" "$ARGOS_STATE_ROOT/config" "$ARGOS_STATE_ROOT/logs"
chown -R argos:argos "$ARGOS_STATE_ROOT"

if [ -s "$PROBE_FILE" ]; then
    echo "[PERSIST] probe=existing value=$(cat "$PROBE_FILE")"
else
    PROBE_VALUE="argos-$(date +%s)-$$"
    printf '%s\n' "$PROBE_VALUE" > "$PROBE_FILE"
    chown argos:argos "$PROBE_FILE"
    echo "[PERSIST] probe=created value=$PROBE_VALUE"
fi

echo "[PERSIST] railway_volume_name=${RAILWAY_VOLUME_NAME:-unset}"
echo "[PERSIST] railway_volume_mount=${RAILWAY_VOLUME_MOUNT_PATH:-unset}"
echo "[SECURITY] external_send=$EXTERNAL_SEND_ENABLED draft_only=$EXTERNAL_DRAFT_ONLY approval_required=$EXTERNAL_REQUIRE_OWNER_APPROVAL"
echo "[SECURITY] external_audit=$EXTERNAL_ACTION_AUDIT_PATH"

if [ -s "$CODEX_HOME/auth.json" ]; then
    echo "[CODEX] auth cache ready"
else
    case "${ARGOS_CODEX_DEVICE_LOGIN:-0}" in
        1|true|TRUE|yes|YES|on|ON)
            echo "[CODEX] auth cache missing; starting device auth in background"
            (gosu argos python3 -c 'import pty; raise SystemExit(pty.spawn(["codex", "login", "--device-auth"]))' || true) &
            ;;
        *)
            echo "[CODEX] auth cache missing; device auth disabled"
            ;;
    esac
fi

echo "[CODEX] workspace=$ARGOS_CODEX_WORKDIR"
echo "[CLOUD] state_root=$ARGOS_STATE_ROOT"
codex --version

exec gosu argos "$@"
