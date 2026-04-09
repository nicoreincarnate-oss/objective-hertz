#!/bin/bash
# ─── PERSEUS V17 Health Check ────────────────────────────────────────
# Run via cron every 5 minutes. Sends Telegram alert on failures.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../.env" 2>/dev/null || true

FAILURES=""

# Check native Postgres
if ! pg_isready -q -h localhost -p 5432 2>/dev/null; then
    FAILURES="${FAILURES}\n❌ Postgres: not accepting connections on :5432"
fi

# Check native Qdrant
if ! curl -sf http://localhost:6333/healthz &>/dev/null; then
    FAILURES="${FAILURES}\n❌ Qdrant: not responding on :6333"
fi

# Check native Redis
if ! redis-cli ping &>/dev/null; then
    FAILURES="${FAILURES}\n❌ Redis: not responding"
fi

# Check Mem0
if ! curl -sf http://localhost:8888/healthz &>/dev/null; then
    FAILURES="${FAILURES}\n❌ Mem0: not responding on :8888"
fi

# Check N8N
if ! curl -sf http://localhost:5678/healthz &>/dev/null; then
    FAILURES="${FAILURES}\n❌ N8N: not responding on :5678"
fi

# Check Ollama
if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
    FAILURES="${FAILURES}\n❌ Ollama: not responding on :11434"
fi

# Check disk usage
DISK_PCT=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
if (( DISK_PCT > 80 )); then
    FAILURES="${FAILURES}\n⚠️ Disk usage: ${DISK_PCT}%"
fi

# Send alert if failures found
if [ -n "$FAILURES" ]; then
    MESSAGE="🚨 PERSEUS HEALTH ALERT\n$(date '+%Y-%m-%d %H:%M')\n${FAILURES}"

    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ] && \
       [ "$TELEGRAM_BOT_TOKEN" != "CHANGE_ME" ]; then
        curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${TELEGRAM_CHAT_ID}" \
            -d "text=${MESSAGE}" \
            -d "parse_mode=HTML" >/dev/null 2>&1
    fi

    echo "$MESSAGE" >> "$HOME/perseus-health.log"
fi
