#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  PERSEUS — First-Run Setup
#  Prompts for all required configuration, generates secrets,
#  creates .env, applies DB schema, and verifies readiness.
# ═══════════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE="$ROOT_DIR/.env.example"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
if [ -d "/Applications/Docker.app/Contents/Resources/bin" ]; then
    export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
fi

# ── Colors ────────────────────────────────────────────────────────
BOLD="\033[1m"
DIM="\033[2m"
GREEN="\033[32m"
GOLD="\033[33m"
RED="\033[31m"
RESET="\033[0m"

header() { echo -e "\n${BOLD}${GOLD}═══ $1 ═══${RESET}"; }
ok()     { echo -e "  ${GREEN}✓${RESET} $1"; }
warn()   { echo -e "  ${GOLD}⚠${RESET} $1"; }
fail()   { echo -e "  ${RED}✗${RESET} $1"; }
ask()    { echo -en "  ${BOLD}$1${RESET} "; }

generate_secret() {
    python3 -c "import secrets; print(secrets.token_urlsafe($1))" 2>/dev/null || openssl rand -base64 "$1" | tr -d '/+=' | head -c "$1"
}

# ── Prompt helper: ask for a value, show current, accept enter for default ──
prompt_value() {
    local key="$1"
    local label="$2"
    local current="$3"
    local required="$4"  # "required" or "optional"
    local is_secret="$5" # "secret" or ""

    if [ "$current" != "CHANGE_ME" ] && [ "$current" != "CHANGE_ME_TO_SECURE_PASSWORD" ] && \
       [ "$current" != "CHANGE_ME_TO_A_LONG_RANDOM_SECRET" ] && [ "$current" != "CHANGE_ME_TO_A_RANDOM_TOKEN" ] && \
       [ -n "$current" ]; then
        if [ "$is_secret" = "secret" ]; then
            ok "$label: ****${current: -4} (already set)"
        else
            ok "$label: $current (already set)"
        fi
        return
    fi

    if [ "$required" = "optional" ]; then
        ask "$label [optional, press Enter to skip]: "
    else
        ask "$label: "
    fi

    read -r value
    if [ -n "$value" ]; then
        sed -i '' "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
        ok "$label: set"
    elif [ "$required" = "required" ]; then
        fail "$label is required but was left empty"
        MISSING_REQUIRED=1
    else
        ok "$label: skipped"
    fi
}

# ═══════════════════════════════════════════════════════════════════

echo -e "${BOLD}"
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║          PERSEUS — First Run Setup        ║"
echo "  ║     Autonomous AI Revenue System v1.0     ║"
echo "  ╚═══════════════════════════════════════════╝"
echo -e "${RESET}"

MISSING_REQUIRED=0

# ── Step 1: Create .env from template ────────────────────────────

header "Environment File"

if [ -f "$ENV_FILE" ]; then
    ok ".env already exists"
else
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    ok "Created .env from .env.example"
fi

# ── Step 2: Auto-generate secrets ────────────────────────────────

header "Generating Secrets"

# Postgres password
CURRENT_PG=$(grep '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2)
if [ "$CURRENT_PG" = "CHANGE_ME_TO_SECURE_PASSWORD" ] || [ -z "$CURRENT_PG" ]; then
    PG_PASS=$(generate_secret 24)
    sed -i '' "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASS}|" "$ENV_FILE"
    ok "Postgres password: generated"
else
    ok "Postgres password: already set"
fi

# N8N password
CURRENT_N8N=$(grep '^N8N_PASSWORD=' "$ENV_FILE" | cut -d= -f2)
if [ "$CURRENT_N8N" = "CHANGE_ME_TO_SECURE_PASSWORD" ] || [ -z "$CURRENT_N8N" ]; then
    N8N_PASS=$(generate_secret 24)
    sed -i '' "s|^N8N_PASSWORD=.*|N8N_PASSWORD=${N8N_PASS}|" "$ENV_FILE"
    ok "N8N password: generated"
else
    ok "N8N password: already set"
fi

# Dashboard secret
CURRENT_DASH=$(grep '^DASHBOARD_SECRET=' "$ENV_FILE" | cut -d= -f2)
if [ "$CURRENT_DASH" = "CHANGE_ME_TO_A_RANDOM_TOKEN" ] || [ -z "$CURRENT_DASH" ]; then
    DASH_SECRET=$(generate_secret 32)
    sed -i '' "s|^DASHBOARD_SECRET=.*|DASHBOARD_SECRET=${DASH_SECRET}|" "$ENV_FILE"
    ok "Dashboard token: generated"
else
    DASH_SECRET="$CURRENT_DASH"
    ok "Dashboard token: already set"
fi

# Unsubscribe HMAC secret
CURRENT_UNSUB=$(grep '^UNSUBSCRIBE_SECRET=' "$ENV_FILE" | cut -d= -f2)
if [ "$CURRENT_UNSUB" = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET" ] || [ -z "$CURRENT_UNSUB" ] || [ ${#CURRENT_UNSUB} -lt 16 ]; then
    UNSUB_SECRET=$(generate_secret 32)
    sed -i '' "s|^UNSUBSCRIBE_SECRET=.*|UNSUBSCRIBE_SECRET=${UNSUB_SECRET}|" "$ENV_FILE"
    ok "Unsubscribe HMAC secret: generated (${#UNSUB_SECRET} chars)"
else
    ok "Unsubscribe HMAC secret: already set"
fi

# Telegram admin secret
CURRENT_TG_ADMIN=$(grep '^TELEGRAM_ADMIN_SECRET=' "$ENV_FILE" | cut -d= -f2)
if [ "$CURRENT_TG_ADMIN" = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET" ] || [ -z "$CURRENT_TG_ADMIN" ]; then
    TG_ADMIN=$(generate_secret 24)
    sed -i '' "s|^TELEGRAM_ADMIN_SECRET=.*|TELEGRAM_ADMIN_SECRET=${TG_ADMIN}|" "$ENV_FILE"
    ok "Telegram admin secret: generated"
else
    ok "Telegram admin secret: already set"
fi

# ── Step 3: Prompt for required API keys ─────────────────────────

header "Core API Keys (required for revenue)"

prompt_value "INSTANTLY_API_KEY" "Instantly.ai API key" "$(grep '^INSTANTLY_API_KEY=' "$ENV_FILE" | cut -d= -f2)" "required" "secret"

header "Email Compliance (required — CAN-SPAM / GDPR)"

echo -e "  ${DIM}These are stored in the database, not .env.${RESET}"
echo -e "  ${DIM}They'll be set after Docker starts.${RESET}"

ask "Physical mailing address for email footer: "
read -r COMPANY_ADDRESS
if [ -z "$COMPANY_ADDRESS" ]; then
    warn "Skipped — Titan will refuse to send until this is set"
    COMPANY_ADDRESS=""
fi

ask "Unsubscribe URL base (e.g. https://yourdomain.com): "
read -r UNSUB_URL
if [ -z "$UNSUB_URL" ]; then
    warn "Skipped — Titan will refuse to send until this is set"
    UNSUB_URL=""
fi

# ── Step 4: Optional integrations ────────────────────────────────

header "Optional Integrations (press Enter to skip any)"

prompt_value "ANTHROPIC_API_KEY" "Anthropic API key (Claude)" "$(grep '^ANTHROPIC_API_KEY=' "$ENV_FILE" | cut -d= -f2)" "optional" "secret"
prompt_value "TELEGRAM_BOT_TOKEN" "Telegram bot token" "$(grep '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2)" "optional" "secret"
prompt_value "TELEGRAM_CHAT_ID" "Telegram chat ID (your user ID)" "$(grep '^TELEGRAM_CHAT_ID=' "$ENV_FILE" | cut -d= -f2)" "optional" ""
prompt_value "FIRECRAWL_API_KEY" "Firecrawl API key" "$(grep '^FIRECRAWL_API_KEY=' "$ENV_FILE" | cut -d= -f2)" "optional" "secret"
prompt_value "V0_API_KEY" "v0.dev API key (site builder)" "$(grep '^V0_API_KEY=' "$ENV_FILE" | cut -d= -f2)" "optional" "secret"
prompt_value "STRIPE_API_KEY" "Stripe API key" "$(grep '^STRIPE_API_KEY=' "$ENV_FILE" | cut -d= -f2)" "optional" "secret"

# ── Step 5: Start Docker + apply schema ──────────────────────────

header "Infrastructure"

if ! command -v docker &> /dev/null; then
    fail "Docker not installed — install Docker Desktop from docker.com"
    exit 1
fi

if ! docker info > /dev/null 2>&1; then
    warn "Docker daemon not running — starting Docker Desktop..."
    open -a Docker
    for i in $(seq 1 30); do
        docker info > /dev/null 2>&1 && break
        sleep 2
    done
fi

if ! docker info > /dev/null 2>&1; then
    fail "Docker daemon didn't start — please start Docker Desktop manually and re-run"
    exit 1
fi
ok "Docker daemon running"

cd "$ROOT_DIR"
docker compose up -d 2>&1 | grep -E "Created|Started|Running" | head -10
ok "Docker services started"

echo "  Waiting for Postgres..."
for i in $(seq 1 30); do
    docker exec perseus-postgres pg_isready -U perseus > /dev/null 2>&1 && break
    sleep 1
done

if ! docker exec perseus-postgres pg_isready -U perseus > /dev/null 2>&1; then
    fail "Postgres didn't become ready"
    exit 1
fi
ok "Postgres ready"

# Set Postgres password to match .env
PG_PASS=$(grep '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2)
docker exec perseus-postgres psql -U perseus -d perseus -c "ALTER USER perseus WITH PASSWORD '$PG_PASS';" > /dev/null 2>&1
ok "Postgres password synced"

# Apply schema
docker exec -i perseus-postgres psql -U perseus -d perseus < "$ROOT_DIR/scripts/init-db.sql" > /dev/null 2>&1
ok "Schema applied"

# Set compliance config in DB
if [ -n "$COMPANY_ADDRESS" ]; then
    docker exec perseus-postgres psql -U perseus -d perseus -c \
        "UPDATE system_config SET value = '\"$COMPANY_ADDRESS\"', is_customized = TRUE WHERE key = 'company_address';" > /dev/null 2>&1
    ok "Company address set in DB"
fi

if [ -n "$UNSUB_URL" ]; then
    docker exec perseus-postgres psql -U perseus -d perseus -c \
        "UPDATE system_config SET value = '\"$UNSUB_URL\"', is_customized = TRUE WHERE key = 'unsubscribe_base_url';" > /dev/null 2>&1
    ok "Unsubscribe URL set in DB"
fi

# ── Step 6: Check Ollama ─────────────────────────────────────────

header "Ollama (Local LLM)"

if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    ok "Ollama running"
    MODELS=$(curl -sf http://localhost:11434/api/tags | python3 -c "import sys,json; [print(f'    {m[\"name\"]}') for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null)
    if [ -n "$MODELS" ]; then
        echo "$MODELS"
    else
        warn "No models installed — run: ollama pull qwen2.5:14b-instruct-q4_K_M"
    fi
else
    warn "Ollama not running — run: ollama serve"
    warn "Then install models: ollama pull qwen2.5:14b-instruct-q4_K_M && ollama pull llama3.2:3b && ollama pull nomic-embed-text"
fi

# ── Step 7: Build frontend ───────────────────────────────────────

header "War Room Frontend"

FRONTEND_DIR="$ROOT_DIR/hermes/web/frontend"
if [ -d "$FRONTEND_DIR" ] && [ -f "$FRONTEND_DIR/package.json" ]; then
    cd "$FRONTEND_DIR"
    if [ ! -d ".next" ]; then
        echo "  Building frontend (first run, may take a minute)..."
        pnpm install --frozen-lockfile 2>/dev/null || pnpm install 2>/dev/null
        pnpm build 2>/dev/null
    fi
    ok "Frontend built"
    cd "$ROOT_DIR"
else
    warn "Frontend not found at $FRONTEND_DIR"
fi

# ── Step 8: Summary ──────────────────────────────────────────────

header "Setup Complete"

DASH_SECRET=$(grep '^DASHBOARD_SECRET=' "$ENV_FILE" | cut -d= -f2)
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || echo "localhost")

echo ""
echo -e "  ${BOLD}Your War Room URL:${RESET}"
echo -e "  ${GREEN}http://${LOCAL_IP}:3000/?token=${DASH_SECRET}${RESET}"
echo ""
echo -e "  ${BOLD}To start Perseus:${RESET}"
echo -e "  ${DIM}make start${RESET}"
echo ""
echo -e "  ${BOLD}To check status:${RESET}"
echo -e "  ${DIM}make status${RESET}"
echo ""

if [ "$MISSING_REQUIRED" -eq 1 ]; then
    echo -e "  ${RED}⚠ Some required values were not provided.${RESET}"
    echo -e "  ${RED}  Edit .env manually and re-run: bash scripts/setup-perseus.sh${RESET}"
    echo ""
fi

# Check what's still placeholder
echo -e "  ${BOLD}Configuration status:${RESET}"
while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ ]] && continue
    [[ -z "$key" ]] && continue
    if [ "$value" = "CHANGE_ME" ] || [ "$value" = "CHANGE_ME_TO_SECURE_PASSWORD" ] || \
       [ "$value" = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET" ] || [ "$value" = "CHANGE_ME_TO_A_RANDOM_TOKEN" ]; then
        warn "$key — not configured"
    fi
done < "$ENV_FILE"

echo ""
ok "Perseus is ready. Run ${BOLD}make start${RESET} to go live."
