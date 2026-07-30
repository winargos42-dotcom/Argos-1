#!/usr/bin/env bash
# ============================================================================
#  ARGOS Universal OS v2.1.4 — Self-Extracting Installer
#  Repo:  https://github.com/poilopr57-a11y/Argos
#  Usage: curl -fsSL https://raw.githubusercontent.com/poilopr57-a11y/Argos/main/install_argos.sh | bash
#         bash install_argos.sh [--dest /opt/argos] [--node-name my-node] [--pc-ip 192.168.1.66]
# ============================================================================
set -euo pipefail

ARGOS_REPO="${ARGOS_REPO_URL:-https://github.com/winargos42-dotcom/Argos-1.git}"
ARGOS_BRANCH="main"
INSTALL_DIR="${ARGOS_INSTALL_DIR:-$HOME/argos}"
NODE_NAME=""
NODE_ROLE="agent"
NODE_CAPABILITIES="p2p,mcp,agent"
PC_IP="192.168.1.66"
BRAIN_PORT="5001"
MCP_PORT="8000"
IPC_TOKEN="argos_ipc_2026"
INTERACTIVE=true

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; CYN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GRN}[ARGOS]${NC} $*"; }
warn()  { echo -e "${YLW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERR]${NC} $*"; exit 1; }
step()  { echo -e "${CYN}[STEP]${NC} $*"; }

while [[ $# -gt 0 ]]; do
    case $1 in
        --dest)         INSTALL_DIR="$2"; shift 2 ;;
        --node-name)    NODE_NAME="$2"; shift 2 ;;
        --node-role)    NODE_ROLE="$2"; shift 2 ;;
        --pc-ip)        PC_IP="$2"; shift 2 ;;
        --brain-port)   BRAIN_PORT="$2"; shift 2 ;;
        --mcp-port)     MCP_PORT="$2"; shift 2 ;;
        --branch)       ARGOS_BRANCH="$2"; shift 2 ;;
        --non-interactive) INTERACTIVE=false; shift ;;
        --help|-h)
            echo "ARGOS Universal OS Installer v2.1.4"
            echo ""
            echo "Usage: bash install_argos.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --dest DIR         Install directory (default: \$HOME/argos)"
            echo "  --node-name NAME   P2P node name (default: argos-<hostname>)"
            echo "  --node-role ROLE   agent|compute|edge (default: agent)"
            echo "  --pc-ip IP         Existing ARGOS PC IP for P2P join (default: 192.168.1.66)"
            echo "  --brain-port PORT  Brain API port (default: 5001)"
            echo "  --mcp-port PORT    MCP port (default: 8000)"
            echo "  --branch BRANCH    Git branch (default: main)"
            echo "  --non-interactive  Skip prompts"
            exit 0 ;;
        *) error "Unknown option: $1" ;;
    esac
done

[[ -z "$NODE_NAME" ]] && NODE_NAME="argos-$(hostname 2>/dev/null || echo 'node')"

echo ""
echo -e "${CYN}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${CYN}║   ARGOS Universal OS v2.1.4 — Installer           ║${NC}"
echo -e "${CYN}╚════════════════════════════════════════════════════╝${NC}"
echo ""
info "Node:     $NODE_NAME"
info "Role:     $NODE_ROLE"
info "Install:  $INSTALL_DIR"
info "PC hub:   $PC_IP"
info "Brain:    :$BRAIN_PORT  MCP: :$MCP_PORT"
echo ""

if $INTERACTIVE; then
    read -rp "Continue? [Y/n] " confirm
    [[ "$confirm" =~ ^[Nn] ]] && exit 0
fi

# ── 1. Prerequisites ─────────────────────────────────────────────
step "1/7 — Prerequisites..."
command -v git    >/dev/null 2>&1 || error "git not found. Install: sudo apt install git"
command -v python3 >/dev/null 2>&1 || error "python3 not found. Install: sudo apt install python3 python3-venv python3-pip"

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "Python $PY_VER OK"

# ── 2. Clone ──────────────────────────────────────────────────────
step "2/7 — Cloning ARGOS..."
if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Existing repo, pulling..."
    cd "$INSTALL_DIR"
    git pull --ff-only origin "$ARGOS_BRANCH" 2>/dev/null || warn "Pull failed, using existing"
else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --depth 1 --branch "$ARGOS_BRANCH" "$ARGOS_REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
info "Repo ready: $INSTALL_DIR"

# ── 3. Venv ───────────────────────────────────────────────────────
step "3/7 — Virtualenv..."
if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
    info "Created .venv"
else
    info ".venv exists"
fi

if [[ -f ".venv/bin/python" ]]; then
    VPY=".venv/bin/python"
elif [[ -f ".venv/Scripts/python.exe" ]]; then
    VPY=".venv/Scripts/python.exe"
else
    error "venv python not found"
fi

# ── 4. Dependencies ───────────────────────────────────────────────
step "4/7 — Dependencies..."
"$VPY" -m pip install --upgrade pip --quiet
info "Installing core deps..."
"$VPY" -m pip install -e "." --quiet 2>&1 | tail -3
info "Installing Brain API deps (flask, flask-cors, openai)..."
"$VPY" -m pip install flask flask-cors openai --quiet 2>&1 | tail -3 || warn "Brain API deps failed"
info "Installing extras (voice, telegram, iot, vision, ml-local)..."
"$VPY" -m pip install -e ".[voice,telegram,iot,vision,ml-local]" --quiet 2>&1 | tail -3 || warn "Some extras failed (non-critical)"
info "Dependencies installed"

# ── 5. .env ───────────────────────────────────────────────────────
step "5/7 — .env configuration..."
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
[[ -z "$LOCAL_IP" ]] && LOCAL_IP="127.0.0.1"

[[ -f ".env" ]] && cp .env .env.bak.$(date +%s) 2>/dev/null || true

cat > .env << ENVEOF
# ════════════════════════════════════════════════════════════
# ARGOS Universal OS v2.1.4 — .env
# Node: $NODE_NAME | Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# ════════════════════════════════════════════════════════════

# ── P2P Identity ──
ARGOS_NODE_NAME=$NODE_NAME
ARGOS_NODE_ROLE=$NODE_ROLE
ARGOS_NODE_ADDRESS=${LOCAL_IP}:${MCP_PORT}
ARGOS_NODE_CAPABILITIES=$NODE_CAPABILITIES

# ── Brain API ──
ARGOS_BRAIN_ENABLED=1
BRAIN_API_PORT=$BRAIN_PORT
BRAIN_API_URL=http://127.0.0.1:$BRAIN_PORT
ARGOS_BRAIN_API_URL=http://127.0.0.1:$BRAIN_PORT
ARGOS_BRAIN_URL=http://127.0.0.1:$BRAIN_PORT
ARGOS_BRAIN_PORT=$BRAIN_PORT
ARGOS_BRAIN_API_PORT=$BRAIN_PORT
BRAIN_API_AUTOSTART=true
ARGOS_BRAIN_AUTOSTART=true

# ── P2P Network (join existing) ──
ARGOS_PC_IP=$PC_IP
ARGOS_BRAIN_API_FALLBACKS=http://${PC_IP}:$BRAIN_PORT
ARGOS_SHARED_MCP=http://${PC_IP}:$MCP_PORT
ARGOS_IPC_TOKEN=$IPC_TOKEN

# ── MCP ──
ARGOS_MCP_URL=http://127.0.0.1:${MCP_PORT}/mcp
ARGOS_VERSION=2.1.4

# ── Language ──
ARGOS_RESPONSE_LANGUAGE=ru
ARGOS_LANG=ru

# ── AI Mode ──
ARGOS_AI_MODE=auto
ARGOS_AI_PRIORITY=local
ARGOS_DEFAULT_MODEL=local
OLLAMA_MODEL=llama3.2:1b
OLLAMA_TIMEOUT=600

# ── Homeostasis ──
ARGOS_HOMEOSTASIS=on
ARGOS_HOMEOSTASIS_INTERVAL=8
ARGOS_CURIOSITY=on
ARGOS_TASK_WORKERS=2

# ── Logging ──
ARGOS_LOG_LEVEL=INFO

# ── API Keys (FILL IN) ──
# TELEGRAM_TOKEN=
# OPENAI_API_KEY=
# HUGGINGFACE_TOKEN=
# ANTHROPIC_API_KEY=
# GITHUB_TOKEN=

# ── Remote Access ──
ARGOS_REMOTE_TOKEN=CHANGE_ME

ENVEOF

info ".env generated (local IP: $LOCAL_IP)"
warn "Edit .env to add API keys (Telegram, OpenAI, etc.)"

# ── 6. Launcher scripts ───────────────────────────────────────────
step "6/7 — Launcher scripts..."
mkdir -p data/runtime data/vault logs

cat > start_argos.sh << 'SEOF'
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
SEOF
chmod +x start_argos.sh

cat > stop_argos.sh << 'XEOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
for f in data/runtime/brain.pid data/runtime/main.pid; do
    [ -f "$f" ] && { PID=$(cat "$f"); kill "$PID" 2>/dev/null && echo "Stopped PID $PID"; rm -f "$f"; }
done
XEOF
chmod +x stop_argos.sh

cat > status_argos.sh << 'STEOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
PORT=$(grep -E '^BRAIN_API_PORT=' .env | cut -d= -f2)
echo "=== ARGOS Status ==="
echo "Brain:  $(curl -s http://localhost:${PORT}/health 2>/dev/null || echo 'OFFLINE')"
echo "Nodes:  $(curl -s http://localhost:${PORT}/brain/nodes 2>/dev/null | python3 -m json.tool 2>/dev/null || echo 'N/A')"
echo "Memory: $(curl -s http://localhost:${PORT}/memory/ping 2>/dev/null || echo 'OFFLINE')"
STEOF
chmod +x status_argos.sh

info "Launchers: start_argos.sh, stop_argos.sh, status_argos.sh"

# ── 7. First boot ─────────────────────────────────────────────────
step "7/7 — First boot..."
if $INTERACTIVE; then
    read -rp "Start ARGOS now? [Y/n] " go
    [[ "$go" =~ ^[Nn] ]] && { info "Done. Run ./start_argos.sh to start."; exit 0; }
fi

./start_argos.sh

echo ""
echo -e "${GRN}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${GRN}║   ARGOS Universal OS — Installation Complete!      ║${NC}"
echo -e "${GRN}╚════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Dir:       $INSTALL_DIR"
echo "  Node:      $NODE_NAME"
echo "  Brain API: http://localhost:$BRAIN_PORT"
echo "  Dashboard: http://localhost:$BRAIN_PORT/dashboard"
echo ""
echo "  Commands:"
echo "    Start:   ./start_argos.sh"
echo "    Stop:    ./stop_argos.sh"
echo "    Status:  ./status_argos.sh"
echo "    Logs:    tail -f logs/brain_api.log logs/main.log"
echo ""
echo "  Next:"
echo "    1. Edit .env — add API keys"
echo "    2. Open dashboard in browser"
echo "    3. Check P2P: curl http://localhost:$BRAIN_PORT/brain/nodes"
echo ""