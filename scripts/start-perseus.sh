#!/bin/bash
# Start Perseus workers + official Hermes + dashboard + donor sidecars
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PID_DIR="$ROOT_DIR/logs/pids"
LOG_DIR="$ROOT_DIR/logs"
source "$ROOT_DIR/.env" 2>/dev/null || true
source "$SCRIPT_DIR/sidecars.sh"
PYTHON_BIN="$ROOT_DIR/.venv312/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi
SCREENPIPE_URL="${SCREENPIPE_URL:-http://localhost:3030}"
SCREENPIPE_PID_FILE="$PID_DIR/screenpipe.pid"
SCREENPIPE_LOG_FILE="$LOG_DIR/screenpipe.log"

screenpipe_port() {
    local port
    port="$(printf '%s' "$SCREENPIPE_URL" | sed -n 's#.*://[^/]*:\([0-9][0-9]*\).*#\1#p')"
    if [ -z "$port" ]; then
        port=3030
    fi
    printf '%s\n' "$port"
}

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

stop_stale_listener() {
    local port="$1"
    local expected_cwd="$2"
    local label="$3"
    local pid
    pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1)"
    if [ -z "$pid" ]; then
        return 0
    fi

    local listener_cwd
    listener_cwd="$(lsof -nP -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
    if [ "$listener_cwd" != "$expected_cwd" ]; then
        echo "  ✗ Port $port is already in use by PID $pid outside $label ($listener_cwd)"
        return 1
    fi

    echo "  Found stale $label listener on :$port (PID: $pid), stopping it..."
    kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
        if ! lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
    if lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  ✗ Failed to free port $port for $label"
        return 1
    fi
}

start_screenpipe_sidecar() {
    local enabled="${SCREENPIPE_ENABLED:-true}"
    case "${enabled,,}" in
        false|0|no)
            echo "  ↷ Screenpipe sidecar disabled"
            return 0
            ;;
    esac

    if curl -sf "$SCREENPIPE_URL/health" > /dev/null 2>&1; then
        echo "  ✓ Screenpipe already healthy at $SCREENPIPE_URL"
        return 0
    fi

    local start_command="${SCREENPIPE_START_COMMAND:-npx -y screenpipe@latest record}"
    if [[ "$start_command" == *"npx"* ]] && ! command -v npx &> /dev/null; then
        echo "  ✗ Screenpipe requested but npx is not available"
        return 1
    fi

    echo "  Starting Screenpipe sidecar..."
    stop_stale_listener "$(screenpipe_port)" "$ROOT_DIR" "screenpipe" || exit 1
    SCREENPIPE_URL="$SCREENPIPE_URL" nohup bash -lc "$start_command" > "$SCREENPIPE_LOG_FILE" 2>&1 &
    echo $! > "$SCREENPIPE_PID_FILE"
    wait_for_pid "$(cat "$SCREENPIPE_PID_FILE")" "Screenpipe"
    wait_for_http "$SCREENPIPE_URL/health" "Screenpipe" 60
    echo "  ✓ Screenpipe sidecar healthy at $SCREENPIPE_URL (PID: $(cat "$SCREENPIPE_PID_FILE"))"
}

echo "═══════════════════════════════════════"
echo "  OPENJARVIS — Starting The Boss"
echo "═══════════════════════════════════════"

# 0. Validate Python version
"$PYTHON_BIN" -c "import sys; assert sys.version_info >= (3, 11), f'Python 3.11+ required, got {sys.version}'" || exit 1

# 1. Start Docker services
echo "[1/9] Starting Docker services..."
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
echo "[2/9] Checking Ollama..."
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  Starting Ollama..."
    ollama serve &
    sleep 3
fi
echo "  ✓ Ollama running"

# 3. Start Screenpipe sidecar for live screen context
echo "[3/9] Starting Screenpipe sidecar..."
start_screenpipe_sidecar

# 4. Sync Hermes soul + local skills
echo "[4/9] Syncing Hermes soul + skills..."
bash "$ROOT_DIR/scripts/sync-hermes-agent.sh"

# 5. Start official Hermes gateway
echo "[5/9] Starting official Hermes gateway..."
if ! hermes gateway start > /dev/null 2>&1; then
    hermes gateway install > /dev/null 2>&1 || true
    hermes gateway start > /dev/null 2>&1
fi
echo "  ✓ Hermes gateway running"

# 6. Start donor sidecars using the shared registry
echo "[6/9] Starting donor sidecars..."
sidecar_start_if_needed "system_executor" "OpenHands / system executor"
sidecar_start_if_needed "deerflow_research" "DeerFlow research daemon"
case "${CLAWDBOT_BROWSER_USE_ENABLED:-true}" in
    false|0|no)
        echo "  ↷ browser-use donor disabled"
        ;;
    *)
        sidecar_start_if_needed "browser-use" "browser-use donor"
        ;;
esac
sidecar_start_if_needed "peekaboo" "Peekaboo donor"

# 7. Start OpenJarvis Orchestrator (THE boss — manages Titan, Hermes, ClawdBot)
echo "[7/9] Starting OpenJarvis Orchestrator (boss)..."
cd "$ROOT_DIR"
LOG_TO_STDOUT=0 PYTHONPATH="$ROOT_DIR" ORCHESTRATOR_A2A=1 SKIP_INTERNAL_DASHBOARD=1 nohup "$PYTHON_BIN" orchestrator.py > "$LOG_DIR/orchestrator.log" 2>&1 &
echo $! > "$PID_DIR/orchestrator.pid"
wait_for_pid "$(cat "$PID_DIR/orchestrator.pid")" "OpenJarvis"
echo "  ✓ OpenJarvis started (PID: $(cat $PID_DIR/orchestrator.pid))"
echo "  OpenJarvis will spawn Titan, Hermes, and ClawdBot as vassals."
echo "  OpenJarvis will also spawn Ruflo for engineering tasks."
echo "  Waiting for vassals to come up..."
sleep 5
wait_for_http "http://localhost:9000/.well-known/agent.json" "OpenJarvis A2A" 30
echo "  ✓ OpenJarvis A2A ready on :9000"
echo "  Metrics    OpenJarvis: http://localhost:9100/metrics"
echo "  Metrics    Titan:      http://localhost:9101/metrics"
echo "  Metrics    Hermes:     http://localhost:9102/metrics"
echo "  Metrics    ClawdBot:   http://localhost:9103/metrics"

# 8. Start dashboard backend
echo "[8/9] Starting dashboard backend..."
LOG_TO_STDOUT=0 PYTHONPATH="$ROOT_DIR" nohup "$PYTHON_BIN" -m uvicorn hermes.web.app:app --host 0.0.0.0 --port 8500 > "$LOG_DIR/dashboard.log" 2>&1 &
echo $! > "$PID_DIR/dashboard.pid"
wait_for_pid "$(cat "$PID_DIR/dashboard.pid")" "Dashboard backend"
wait_for_http "http://localhost:8500/api/liveness" "Dashboard backend"
echo "  ✓ Dashboard backend started on :8500 (PID: $(cat $PID_DIR/dashboard.pid))"
echo "  Metrics    Dashboard:  http://localhost:8500/metrics"

# 9. Start War Room frontend (Next.js)
FRONTEND_DIR="$ROOT_DIR/hermes/web/frontend"
echo "[9/9] Starting War Room frontend..."
if [ -d "$FRONTEND_DIR" ] && [ -f "$FRONTEND_DIR/package.json" ]; then
    cd "$FRONTEND_DIR"
    stop_stale_listener 3000 "$FRONTEND_DIR" "frontend" || exit 1
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
if [ -f "$PID_DIR/system_executor.pid" ]; then
echo "  OpenHands / system executor PID: $(cat $PID_DIR/system_executor.pid)"
fi
echo "  Screenpipe Sidecar: $SCREENPIPE_URL"
echo "  OpenJarvis PID: $(cat $PID_DIR/orchestrator.pid) (port 9000)"
echo "  Titan      managed by OpenJarvis (port 9001)"
echo "  Hermes     managed by OpenJarvis (port 9002)"
echo "  ClawdBot   managed by OpenJarvis (port 9003)"
echo "  Ruflo      managed by OpenJarvis (port 9004)"
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
