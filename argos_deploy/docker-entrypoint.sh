#!/bin/sh
set -eu

CODEX_HOME="${CODEX_HOME:-/codex-home}"
ARGOS_CODEX_WORKDIR="${ARGOS_CODEX_WORKDIR:-/app}"

mkdir -p "$CODEX_HOME"
chown -R argos:argos "$CODEX_HOME"

if [ -s "$CODEX_HOME/auth.json" ]; then
    echo "[CODEX] auth cache ready"
else
    echo "[CODEX] auth cache missing"
fi

echo "[CODEX] workspace=$ARGOS_CODEX_WORKDIR"
codex --version

exec gosu argos "$@"
