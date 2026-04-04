#!/bin/bash
# Stop Perseus workers + official Hermes + dashboard + donor sidecars
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="$ROOT_DIR/logs/pids"
SIGTERM_WAIT_SECONDS=30

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
if [ -d "/Applications/Docker.app/Contents/Resources/bin" ]; then
    export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
fi

echo "═══════════════════════════════════════"
echo "  PERSEUS — Stopping Hermes + Workers"
echo "═══════════════════════════════════════"

for agent in screenpipe system_executor deerflow_research browser-use peekaboo frontend dashboard orchestrator clawdbot titan perseus; do
    PID_FILE="$PID_DIR/$agent.pid"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "  Stopping $agent (PID: $PID)..."
            kill -TERM "$PID"
            stopped=0
            for _ in $(seq 1 "$SIGTERM_WAIT_SECONDS"); do
                if ! kill -0 "$PID" 2>/dev/null; then
                    stopped=1
                    break
                fi
                sleep 1
            done
            if [ "$stopped" -ne 1 ]; then
                echo "  $agent did not stop after ${SIGTERM_WAIT_SECONDS}s, sending SIGKILL"
                kill -KILL "$PID" 2>/dev/null || true
            fi
            echo "  ✓ $agent stopped"
        else
            echo "  $agent not running"
        fi
        rm -f "$PID_FILE"
    else
        echo "  $agent: no PID file"
    fi
done

echo ""
echo "Stopping Hermes gateway..."
hermes gateway stop > /dev/null 2>&1 || true
echo "  ✓ Hermes gateway stopped"

echo ""
echo "Stopping Docker services..."
cd "$ROOT_DIR"
docker compose down
echo "  ✓ Docker services stopped"

echo ""
echo "═══════════════════════════════════════"
echo "  PERSEUS STOPPED"
echo "═══════════════════════════════════════"
