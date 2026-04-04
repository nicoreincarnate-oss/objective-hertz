#!/bin/bash
# ─── PERSEUS V18 Health Check ────────────────────────────────────────
# Run via cron every 5 minutes. Sends Telegram alerts on failures unless
# PERSEUS_HEALTH_NO_ALERT=1 is set for quiet/manual checks.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../.env" 2>/dev/null || true
source "$SCRIPT_DIR/sidecars.sh"

NO_ALERT="${PERSEUS_HEALTH_NO_ALERT:-0}"
FAILURES=""

append_failure() {
    local entry="$1"
    if [ -z "$FAILURES" ]; then
        FAILURES="$entry"
    else
        FAILURES="${FAILURES}\n${entry}"
    fi
}

report_status() {
    local label="$1"
    local status="$2"
    printf '%-11s %s\n' "$label:" "$status"
}

# Check Docker containers
for CONTAINER in perseus-postgres perseus-qdrant perseus-mem0 perseus-n8n; do
    STATUS=$(docker inspect --format='{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo "missing")
    if [[ "$STATUS" == "running" ]]; then
        report_status "$CONTAINER" "OK"
    else
        report_status "$CONTAINER" "DOWN ($STATUS)"
        append_failure "❌ $CONTAINER: $STATUS"
    fi
done

# Check Ollama
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    report_status "Ollama" "OK"
else
    report_status "Ollama" "DOWN"
    append_failure "❌ Ollama: not responding on :11434"
fi

# Check donor sidecars and the owned OpenHands/system executor
if ! sidecar_health_report "OpenHands / system executor" "$(sidecar_health_url_for system_executor)" "system_executor"; then
    append_failure "❌ OpenHands / system executor: down"
fi
if ! sidecar_health_report "DeerFlow research daemon" "$(sidecar_health_url_for deerflow_research)" "deerflow_research"; then
    append_failure "❌ DeerFlow research daemon: down"
fi
if curl -sf "${RUFLO_A2A_URL:-http://localhost:9004}/a2a/health" >/dev/null 2>&1 || \
   curl -sf "${RUFLO_A2A_URL:-http://localhost:9004}/.well-known/agent.json" >/dev/null 2>&1; then
    report_status "Ruflo" "OK"
else
    report_status "Ruflo" "DOWN"
    append_failure "❌ Ruflo: down"
fi
case "${CLAWDBOT_BROWSER_USE_ENABLED:-true}" in
    false|0|no)
        report_status "browser-use" "DISABLED"
        ;;
    *)
        if ! sidecar_health_report "browser-use donor" "$(sidecar_health_url_for browser-use)" "browser-use"; then
            append_failure "❌ browser-use donor: down"
        fi
        ;;
esac
if ! sidecar_health_report "Peekaboo donor" "$(sidecar_health_url_for peekaboo)" "peekaboo"; then
    append_failure "❌ Peekaboo donor: down"
fi
if ! sidecar_health_report "Screenpipe donor" "$(sidecar_health_url_for screenpipe)" "screenpipe"; then
    append_failure "❌ Screenpipe donor: down"
fi

# Check disk usage
DISK_PCT=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
if (( DISK_PCT > 80 )); then
    report_status "Disk" "${DISK_PCT}% USED"
    append_failure "⚠️ Disk usage: ${DISK_PCT}%"
else
    report_status "Disk" "${DISK_PCT}% USED"
fi

if [ -n "$FAILURES" ]; then
    MESSAGE="🚨 PERSEUS HEALTH ALERT\n$(date '+%Y-%m-%d %H:%M')\n${FAILURES}"
    if [ "$NO_ALERT" != "1" ]; then
        if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ] && \
           [ "$TELEGRAM_BOT_TOKEN" != "CHANGE_ME" ]; then
            curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
                -d "chat_id=${TELEGRAM_CHAT_ID}" \
                -d "text=${MESSAGE}" \
                -d "parse_mode=HTML" >/dev/null 2>&1
        fi

        echo "$MESSAGE" >> "$HOME/perseus-health.log"
    fi
    exit 1
fi

exit 0
