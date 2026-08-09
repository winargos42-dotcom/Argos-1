#!/bin/sh
set -eu

ARGOS_STATE_ROOT="${ARGOS_STATE_ROOT:-/app/persist}"
CODEX_HOME="${CODEX_HOME:-$ARGOS_STATE_ROOT/codex}"
ARGOS_CODEX_WORKDIR="${ARGOS_CODEX_WORKDIR:-/app}"

mkdir -p "$ARGOS_STATE_ROOT" "$CODEX_HOME"
chown -R argos:argos "$ARGOS_STATE_ROOT" "$CODEX_HOME"

if [ -s "$CODEX_HOME/auth.json" ]; then
    echo "[CODEX] auth cache ready"
else
    echo "[CODEX] auth cache missing"
fi

echo "[CODEX] home=$CODEX_HOME"
echo "[CODEX] workspace=$ARGOS_CODEX_WORKDIR"
codex --version

exec gosu argos "$@"
