#!/usr/bin/env bash
cd "$(dirname "$0")"
for f in data/runtime/brain.pid data/runtime/main.pid; do
    [ -f "$f" ] && { PID=$(cat "$f"); kill "$PID" 2>/dev/null && echo "Stopped PID $PID"; rm -f "$f"; }
done
