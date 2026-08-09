#!/bin/sh
set -eu

CODEX_HOME="${CODEX_HOME:-/codex-home}"
mkdir -p "$CODEX_HOME"
chown -R argos:argos "$CODEX_HOME"

exec gosu argos "$@"
