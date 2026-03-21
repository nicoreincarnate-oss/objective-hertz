#!/bin/bash
# Start all Perseus daemons
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="$ROOT_DIR/logs/pids"
LOG_DIR="$ROOT_DIR/logs"

mkdir -p "$PID_DIR" "$LOG_DIR"

echo "═══════════════════════════════════════"
echo "  PERSEUS — Starting All Systems"
echo "═══════════════════════════════════════"

# 1. Start Docker services
echo "[1/5] Starting Docker services..."
cd "$ROOT_DIR"
docker compose up -d
echo "  ✓ Docker services running"

# 2. Start Ollama (if not already running)
echo "[2/5] Checking Ollama..."
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  Starting Ollama..."
    ollama serve &
    sleep 3
fi
echo "  ✓ Ollama running"

# 3. Start Perseus daemon
echo "[3/5] Starting Perseus (master)..."
cd "$ROOT_DIR"
nohup python -m perseus.daemon > "$LOG_DIR/perseus.log" 2>&1 &
echo $! > "$PID_DIR/perseus.pid"
echo "  ✓ Perseus started (PID: $(cat $PID_DIR/perseus.pid))"

# 4. Start Titan daemon
echo "[4/5] Starting Titan (pipeline)..."
nohup python -m titan.daemon > "$LOG_DIR/titan.log" 2>&1 &
echo $! > "$PID_DIR/titan.pid"
echo "  ✓ Titan started (PID: $(cat $PID_DIR/titan.pid))"

# 5. Start Hermes daemon
echo "[5/6] Starting Hermes (interface)..."
nohup python -m hermes.daemon > "$LOG_DIR/hermes.log" 2>&1 &
echo $! > "$PID_DIR/hermes.pid"
echo "  ✓ Hermes started (PID: $(cat $PID_DIR/hermes.pid))"

# 6. Start ClawdBot daemon
echo "[6/6] Starting ClawdBot (skills + browser)..."
nohup python -m clawdbot.daemon > "$LOG_DIR/clawdbot.log" 2>&1 &
echo $! > "$PID_DIR/clawdbot.pid"
echo "  ✓ ClawdBot started (PID: $(cat $PID_DIR/clawdbot.pid))"

echo ""
echo "═══════════════════════════════════════"
echo "  PERSEUS IS LIVE — 4 DAEMONS RUNNING"
echo "═══════════════════════════════════════"
echo "  Perseus  PID: $(cat $PID_DIR/perseus.pid)"
echo "  Titan    PID: $(cat $PID_DIR/titan.pid)"
echo "  Hermes   PID: $(cat $PID_DIR/hermes.pid)"
echo "  ClawdBot PID: $(cat $PID_DIR/clawdbot.pid)"
echo ""
echo "  Logs: $LOG_DIR/"
echo "  Stop: ./scripts/stop-perseus.sh"
echo "═══════════════════════════════════════"
