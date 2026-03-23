#!/bin/bash
# Start Perseus workers + official Hermes + dashboard
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="$ROOT_DIR/logs/pids"
LOG_DIR="$ROOT_DIR/logs"

# Ensure PATH includes Homebrew + Docker for LaunchAgent contexts
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
# Docker Desktop installs its CLI here
if [ -d "/Applications/Docker.app/Contents/Resources/bin" ]; then
    export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
fi

mkdir -p "$PID_DIR" "$LOG_DIR"

wait_for_pid() {
    local pid="$1"
    local name="$2"
    local attempts="${3:-5}"
    for _ in $(seq 1 "$attempts"); do
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    echo "  ✗ $name did not stay alive after start"
    return 1
}

wait_for_http() {
    local url="$1"
    local name="$2"
    local attempts="${3:-20}"
    for _ in $(seq 1 "$attempts"); do
        if curl -sf "$url" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "  ✗ $name did not become healthy at $url"
    return 1
}

echo "═══════════════════════════════════════"
echo "  OPENJARVIS — Starting The Boss"
echo "═══════════════════════════════════════"

# 0. Validate Python version
python3 -c "import sys; assert sys.version_info >= (3, 11), f'Python 3.11+ required, got {sys.version}'" || exit 1

# 1. Start Docker services
echo "[1/7] Starting Docker services..."
cd "$ROOT_DIR"
if ! command -v docker &> /dev/null; then
    echo "  ✗ docker not found in PATH — install Docker Desktop or add it to PATH"
    exit 1
fi
if ! docker info > /dev/null 2>&1; then
    echo "  Docker daemon not running — attempting to start Docker Desktop..."
    open -a Docker
    echo "  Waiting for Docker daemon (up to 60s)..."
    for i in $(seq 1 30); do
        if docker info > /dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    if ! docker info > /dev/null 2>&1; then
        echo "  ✗ Docker daemon did not start — please start Docker Desktop manually"
        exit 1
    fi
fi
docker compose up -d
echo "  Waiting for Postgres to accept connections..."
for i in $(seq 1 30); do
    if docker exec perseus-postgres pg_isready -U perseus > /dev/null 2>&1; then
        break
    fi
    sleep 1
done
if ! docker exec perseus-postgres pg_isready -U perseus > /dev/null 2>&1; then
    echo "  ✗ Postgres did not become ready in 30s"
    exit 1
fi
# Apply schema (safe to re-run — all IF NOT EXISTS / ON CONFLICT)
docker exec -i perseus-postgres psql -U perseus -d perseus < "$ROOT_DIR/scripts/init-db.sql" > /dev/null 2>&1
# Sync Postgres password from .env
if [ -f "$ROOT_DIR/.env" ]; then
    PG_PASS=$(grep '^POSTGRES_PASSWORD=' "$ROOT_DIR/.env" | cut -d= -f2)
    if [ -n "$PG_PASS" ]; then
        docker exec perseus-postgres psql -U perseus -d perseus -c "ALTER USER perseus WITH PASSWORD '$PG_PASS';" > /dev/null 2>&1
    fi
fi
echo "  ✓ Docker services running, Postgres ready, schema applied"

# 2. Start Ollama (if not already running)
echo "[2/7] Checking Ollama..."
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  Starting Ollama..."
    ollama serve &
    sleep 3
fi
echo "  ✓ Ollama running"

# 3. Sync Hermes soul + local skills
echo "[3/7] Syncing Hermes soul + skills..."
bash "$ROOT_DIR/scripts/sync-hermes-agent.sh"

# 4. Start official Hermes gateway
echo "[4/7] Starting official Hermes gateway..."
if ! hermes gateway start > /dev/null 2>&1; then
    hermes gateway install > /dev/null 2>&1 || true
    hermes gateway start > /dev/null 2>&1
fi
echo "  ✓ Hermes gateway running"

# 5. Start OpenJarvis Orchestrator (THE boss — manages Titan, Hermes, ClawdBot)
echo "[5/7] Starting OpenJarvis Orchestrator (boss)..."
cd "$ROOT_DIR"
LOG_TO_STDOUT=0 PYTHONPATH="$ROOT_DIR" ORCHESTRATOR_A2A=1 nohup python3 orchestrator.py > "$LOG_DIR/orchestrator.log" 2>&1 &
echo $! > "$PID_DIR/orchestrator.pid"
wait_for_pid "$(cat "$PID_DIR/orchestrator.pid")" "OpenJarvis"
echo "  ✓ OpenJarvis started (PID: $(cat $PID_DIR/orchestrator.pid))"
echo "  OpenJarvis will spawn Titan, Hermes, and ClawdBot as vassals."
echo "  Waiting for vassals to come up..."
sleep 5
wait_for_http "http://localhost:9000/.well-known/agent.json" "OpenJarvis A2A" 30
echo "  ✓ OpenJarvis A2A ready on :9000"
echo "  Metrics    OpenJarvis: http://localhost:9100/metrics"
echo "  Metrics    Titan:      http://localhost:9101/metrics"
echo "  Metrics    Hermes:     http://localhost:9102/metrics"
echo "  Metrics    ClawdBot:   http://localhost:9103/metrics"

# 6. Start dashboard backend
echo "[6/7] Starting dashboard backend..."
LOG_TO_STDOUT=0 PYTHONPATH="$ROOT_DIR" nohup python3 -m uvicorn hermes.web.app:app --host 0.0.0.0 --port 8500 > "$LOG_DIR/dashboard.log" 2>&1 &
echo $! > "$PID_DIR/dashboard.pid"
wait_for_pid "$(cat "$PID_DIR/dashboard.pid")" "Dashboard backend"
wait_for_http "http://localhost:8500/api/health" "Dashboard backend"
echo "  ✓ Dashboard backend started on :8500 (PID: $(cat $PID_DIR/dashboard.pid))"
echo "  Metrics    Dashboard:  http://localhost:8500/metrics"

# 7. Start War Room frontend (Next.js)
FRONTEND_DIR="$ROOT_DIR/hermes/web/frontend"
echo "[7/7] Starting War Room frontend..."
if [ -d "$FRONTEND_DIR" ] && [ -f "$FRONTEND_DIR/package.json" ]; then
    cd "$FRONTEND_DIR"
    # Build if not already built
    if [ ! -d "$FRONTEND_DIR/.next" ]; then
        echo "  Building frontend (first run)..."
        pnpm install --frozen-lockfile 2>/dev/null || pnpm install
        pnpm build
    fi
    BACKEND_URL=http://localhost:8500 nohup pnpm start --port 3000 --hostname 0.0.0.0 > "$LOG_DIR/frontend.log" 2>&1 &
    echo $! > "$PID_DIR/frontend.pid"
    wait_for_pid "$(cat "$PID_DIR/frontend.pid")" "War Room frontend"
    wait_for_http "http://localhost:3000" "War Room frontend" 30
    echo "  ✓ War Room started on :3000 (PID: $(cat $PID_DIR/frontend.pid))"
    cd "$ROOT_DIR"
else
    echo "  ⚠ Frontend not found at $FRONTEND_DIR — skipping"
fi

echo ""
echo "═══════════════════════════════════════"
echo "  OPENJARVIS IS LIVE — THE BOSS + VASSALS"
echo "═══════════════════════════════════════"
echo "  Hermes     Gateway: launchd service"
echo "  OpenJarvis PID: $(cat $PID_DIR/orchestrator.pid) (port 9000)"
echo "  Titan      managed by OpenJarvis (port 9001)"
echo "  Hermes     managed by OpenJarvis (port 9002)"
echo "  ClawdBot   managed by OpenJarvis (port 9003)"
echo "  Dashboard  PID: $(cat $PID_DIR/dashboard.pid)"
if [ -f "$PID_DIR/frontend.pid" ]; then
echo "  Frontend   PID: $(cat $PID_DIR/frontend.pid)"
echo "  Dashboard: http://localhost:3000"
fi
echo "  Prometheus: http://localhost:9090"
echo "  Grafana:    http://localhost:3001"
echo ""
echo "  Logs: $LOG_DIR/"
echo "  Stop: ./scripts/stop-perseus.sh"
echo "═══════════════════════════════════════"
