#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
VPY=".venv/bin/python"
[[ ! -f "$VPY" ]] && VPY=".venv/Scripts/python.exe"
PORT=$(grep -E '^BRAIN_API_PORT=' .env | cut -d= -f2)

echo "[ARGOS] Starting Brain API on :$PORT ..."
"$VPY" -u argos_brain_api.py > logs/brain_api.log 2>&1 &
BP=$!
echo $BP > data/runtime/brain.pid
sleep 3

if kill -0 $BP 2>/dev/null; then
    echo "[ARGOS] Brain API: PID $BP, http://localhost:$PORT"
else
    echo "[ERR] Brain API failed — see logs/brain_api.log"
    exit 1
fi

echo "[ARGOS] Starting Main (server mode) ..."
"$VPY" -u main.py --no-gui > logs/main.log 2>&1 &
MP=$!
echo $MP > data/runtime/main.pid
sleep 2
kill -0 $MP 2>/dev/null && echo "[ARGOS] Main: PID $MP" || echo "[WARN] Main may have failed — see logs/main.log"

# Register with P2P
NODE=$(grep -E '^ARGOS_NODE_NAME=' .env | cut -d= -f2)
ROLE=$(grep -E '^ARGOS_NODE_ROLE=' .env | cut -d= -f2)
ADDR=$(grep -E '^ARGOS_NODE_ADDRESS=' .env | cut -d= -f2)
CAPS=$(grep -E '^ARGOS_NODE_CAPABILITIES=' .env | cut -d= -f2)
curl -s -X POST "http://localhost:${PORT}/brain/register" \
    -H "Content-Type: application/json" \
    -d "{\"node_id\":\"$NODE\",\"role\":\"$ROLE\",\"address\":\"$ADDR\",\"capabilities\":[\"$CAPS\"]}" 2>/dev/null \
    && echo "[ARGOS] P2P registered: $NODE" || echo "[WARN] P2P register failed (will retry)"

echo ""
echo "[ARGOS] Running. Stop: ./stop_argos.sh  Status: ./status_argos.sh"
