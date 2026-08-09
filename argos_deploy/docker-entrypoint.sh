#!/bin/sh
set -eu

CODEX_HOME="${CODEX_HOME:-/codex-home}"
ARGOS_CODEX_WORKDIR="${ARGOS_CODEX_WORKDIR:-/app}"
ARGOS_STATE_ROOT="${ARGOS_STATE_ROOT:-/app/persist}"

mkdir -p "$CODEX_HOME" "$ARGOS_STATE_ROOT/data" "$ARGOS_STATE_ROOT/config"
chown -R argos:argos "$ARGOS_STATE_ROOT"

if [ -s "$CODEX_HOME/auth.json" ]; then
    echo "[CODEX] auth cache ready"
else
    echo "[CODEX] auth cache missing"
fi

echo "[CODEX] workspace=$ARGOS_CODEX_WORKDIR"
echo "[CLOUD] state_root=$ARGOS_STATE_ROOT"
codex --version

exec gosu argos "$@"
