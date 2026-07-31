#!/usr/bin/env bash
cd "$(dirname "$0")"
PORT=$(grep -E '^BRAIN_API_PORT=' .env | cut -d= -f2)
echo "=== ARGOS Status ==="
echo "Brain:  $(curl -s http://localhost:${PORT}/health 2>/dev/null || echo 'OFFLINE')"
echo "Nodes:  $(curl -s http://localhost:${PORT}/brain/nodes 2>/dev/null | python3 -m json.tool 2>/dev/null || echo 'N/A')"
echo "Memory: $(curl -s http://localhost:${PORT}/memory/ping 2>/dev/null || echo 'OFFLINE')"
