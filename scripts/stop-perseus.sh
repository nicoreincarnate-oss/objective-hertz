#!/bin/bash
# Stop all Perseus daemons
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="$ROOT_DIR/logs/pids"

echo "═══════════════════════════════════════"
echo "  PERSEUS — Stopping All Systems"
echo "═══════════════════════════════════════"

for agent in hermes titan perseus; do
    PID_FILE="$PID_DIR/$agent.pid"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "  Stopping $agent (PID: $PID)..."
            kill -TERM "$PID"
            sleep 1
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
echo "Stopping Docker services..."
cd "$ROOT_DIR"
docker compose down
echo "  ✓ Docker services stopped"

echo ""
echo "═══════════════════════════════════════"
echo "  PERSEUS STOPPED"
echo "═══════════════════════════════════════"
