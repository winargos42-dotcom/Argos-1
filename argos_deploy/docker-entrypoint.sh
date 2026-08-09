#!/bin/sh
set -eu

CODEX_HOME="${CODEX_HOME:-/codex-home}"
ARGOS_CODEX_WORKDIR="${ARGOS_CODEX_WORKDIR:-/app}"
ARGOS_STATE_ROOT="${ARGOS_STATE_ROOT:-/app/persist}"
PROBE_FILE="$ARGOS_STATE_ROOT/.railway_persistence_probe"

export CODEX_HOME ARGOS_CODEX_WORKDIR ARGOS_STATE_ROOT

mkdir -p "$CODEX_HOME" "$ARGOS_STATE_ROOT/data" "$ARGOS_STATE_ROOT/config"
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

if [ -s "$CODEX_HOME/auth.json" ]; then
    echo "[CODEX] auth cache ready"
elif [ "${ARGOS_CODEX_DEVICE_LOGIN:-0}" = "1" ]; then
    echo "[CODEX] auth cache missing; starting device auth"
    gosu argos python3 -c 'import pty; raise SystemExit(pty.spawn(["codex", "login", "--device-auth"]))'
    if [ -s "$CODEX_HOME/auth.json" ]; then
        echo "[CODEX] auth cache ready"
    else
        echo "[CODEX] device auth ended without auth cache"
    fi
else
    echo "[CODEX] auth cache missing"
fi

echo "[CODEX] workspace=$ARGOS_CODEX_WORKDIR"
echo "[CLOUD] state_root=$ARGOS_STATE_ROOT"
codex --version

exec gosu argos "$@"
