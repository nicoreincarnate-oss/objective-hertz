#!/bin/bash
# Shared donor sidecar lifecycle helpers.
# Source this from start/health scripts so they use the same registry and probe logic.

SIDECAR_ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIDECAR_PID_DIR="$SIDECAR_ROOT_DIR/logs/pids"
SIDECAR_LOG_DIR="$SIDECAR_ROOT_DIR/logs"
SIDECAR_PYTHON_BIN="$SIDECAR_ROOT_DIR/.venv312/bin/python"
if [ ! -x "$SIDECAR_PYTHON_BIN" ]; then
    SIDECAR_PYTHON_BIN="$SIDECAR_ROOT_DIR/.venv/bin/python"
fi
if [ ! -x "$SIDECAR_PYTHON_BIN" ]; then
    SIDECAR_PYTHON_BIN="python3"
fi

sidecar_ensure_dirs() {
    mkdir -p "$SIDECAR_PID_DIR" "$SIDECAR_LOG_DIR"
}

sidecar_env_value() {
    local name="$1"
    local value=""
    eval "value=\${$name:-}"
    printf '%s' "$value"
}

sidecar_trim_trailing_slash() {
    local value="$1"
    printf '%s' "${value%/}"
}

sidecar_probe_base_url() {
    local base_url="$1"
    base_url="$(sidecar_trim_trailing_slash "$base_url")"
    if [ -z "$base_url" ]; then
        return 1
    fi

    for path in /health /a2a/health; do
        if curl -sf "${base_url}${path}" > /dev/null 2>&1; then
            return 0
        fi
    done

    return 1
}

sidecar_local_status() {
    local name="$1"
    case "$name" in
        peekaboo)
            if command -v peekaboo >/dev/null 2>&1; then
                echo "local-cli"
                return 0
            fi
            ;;
    esac
    return 1
}

sidecar_status_line() {
    local label="$1"
    local base_url="$2"
    if [ -z "$base_url" ]; then
        echo "  ${label}: not configured"
        return 0
    fi

    if sidecar_probe_base_url "$base_url"; then
        echo "  ${label}: OK ($base_url)"
        return 0
    fi

    echo "  ${label}: DOWN ($base_url)"
    return 1
}

sidecar_start_command_for() {
    local name="$1"
    case "$name" in
        system_executor)
            printf '%s' "PYTHONPATH=\"$SIDECAR_ROOT_DIR\" SYSTEM_EXECUTOR_A2A=1 SYSTEM_EXECUTOR_A2A_PORT=9010 \"$SIDECAR_PYTHON_BIN\" -m system_executor.daemon"
            ;;
        deerflow_research)
            printf '%s' "PYTHONPATH=\"$SIDECAR_ROOT_DIR\" DEERFLOW_RESEARCH_A2A=1 DEERFLOW_RESEARCH_A2A_PORT=9011 \"$SIDECAR_PYTHON_BIN\" -m deerflow_research.daemon"
            ;;
        *)
            local env_name=""
            case "$name" in
                browser-use) env_name="BROWSER_USE_START_CMD" ;;
                peekaboo) env_name="PEEKABOO_START_CMD" ;;
                screenpipe) env_name="SCREENPIPE_START_CMD" ;;
                *) env_name="" ;;
            esac
            if [ -n "$env_name" ]; then
                local configured
                configured="$(sidecar_env_value "$env_name")"
                if [ -n "$configured" ]; then
                    printf '%s' "$configured"
                elif [ "$name" = "browser-use" ]; then
                    printf '%s' "cd \"$SIDECAR_ROOT_DIR\" && uv run --project \"$SIDECAR_ROOT_DIR/tools/browser-use\" python \"$SIDECAR_ROOT_DIR/scripts/browser-use-sidecar-server.py\""
                elif [ "$name" = "screenpipe" ]; then
                    printf '%s' "SCREENPIPE_URL=\"$(sidecar_health_url_for screenpipe)\" npx -y screenpipe@latest record"
                fi
            fi
            ;;
    esac
}

sidecar_health_url_for() {
    local name="$1"
    case "$name" in
        system_executor)
            if [ -n "$(sidecar_env_value SYSTEM_EXECUTOR_A2A_URL)" ]; then
                sidecar_env_value SYSTEM_EXECUTOR_A2A_URL
                return 0
            fi
            if [ -n "$(sidecar_env_value OPENHANDS_URL)" ]; then
                sidecar_env_value OPENHANDS_URL
                return 0
            fi
            printf '%s' "http://localhost:9010"
            ;;
        deerflow_research)
            if [ -n "$(sidecar_env_value DEERFLOW_RESEARCH_A2A_URL)" ]; then
                sidecar_env_value DEERFLOW_RESEARCH_A2A_URL
                return 0
            fi
            printf '%s' "http://localhost:9011"
            return 0
            ;;
        browser-use)
            if [ -n "$(sidecar_env_value BROWSER_USE_URL)" ]; then
                sidecar_env_value BROWSER_USE_URL
                return 0
            fi
            printf '%s' "http://localhost:3031"
            return 0
            ;;
        peekaboo)
            if [ -n "$(sidecar_env_value PEEKABOO_URL)" ]; then
                sidecar_env_value PEEKABOO_URL
                return 0
            fi
            ;;
        screenpipe)
            if [ -n "$(sidecar_env_value SCREENPIPE_URL)" ]; then
                sidecar_env_value SCREENPIPE_URL
                return 0
            fi
            printf '%s' "http://localhost:3030"
            return 0
            ;;
    esac
}

sidecar_pid_file_for() {
    printf '%s/%s.pid' "$SIDECAR_PID_DIR" "$1"
}

sidecar_log_file_for() {
    printf '%s/%s.log' "$SIDECAR_LOG_DIR" "$1"
}

sidecar_start_if_needed() {
    local name="$1"
    local label="$2"
    local health_url
    local start_cmd
    local pid_file
    local log_file

    health_url="$(sidecar_health_url_for "$name")"
    start_cmd="$(sidecar_start_command_for "$name")"
    pid_file="$(sidecar_pid_file_for "$name")"
    log_file="$(sidecar_log_file_for "$name")"

    if [ -n "$health_url" ] && sidecar_probe_base_url "$health_url"; then
        echo "  ✓ $label already healthy"
        return 0
    fi

    local local_backend=""
    local_backend="$(sidecar_local_status "$name" 2>/dev/null || true)"
    if [ -n "$local_backend" ]; then
        echo "  ✓ $label available via $local_backend"
        return 0
    fi

    if [ -z "$start_cmd" ]; then
        if [ -n "$health_url" ]; then
            echo "  ⚠ $label is configured but no start command is set"
            return 0
        fi
        echo "  ⚠ $label is not configured"
        return 0
    fi

    if [ -f "$pid_file" ]; then
        local existing_pid
        existing_pid="$(cat "$pid_file" 2>/dev/null || true)"
        if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
            echo "  ✓ $label already running (PID: $existing_pid)"
            return 0
        fi
    fi

    echo "  Starting $label..."
    nohup bash -lc "$start_cmd" > "$log_file" 2>&1 &
    echo $! > "$pid_file"
    sleep 1
    if [ -n "$health_url" ] && sidecar_probe_base_url "$health_url"; then
        echo "  ✓ $label started"
        return 0
    fi

    echo "  ⚠ $label started, waiting for health check to pass"
    return 0
}

sidecar_health_report() {
    local label="$1"
    local health_url="$2"
    local sidecar_name="$3"

    if [ -z "$health_url" ]; then
        local local_backend=""
        if [ -n "$sidecar_name" ]; then
            local_backend="$(sidecar_local_status "$sidecar_name" 2>/dev/null || true)"
        fi
        if [ -n "$local_backend" ]; then
            echo "  ${label}: OK (${local_backend})"
            return 0
        fi
        echo "  ${label}: not configured"
        return 0
    fi

    if sidecar_probe_base_url "$health_url"; then
        echo "  ${label}: OK ($health_url)"
        return 0
    fi

    echo "  ${label}: DOWN ($health_url)"
    return 1
}

sidecar_start_all() {
    sidecar_ensure_dirs

    sidecar_start_if_needed "system_executor" "OpenHands / system executor"
    sidecar_start_if_needed "deerflow_research" "DeerFlow research daemon"
    sidecar_start_if_needed "browser-use" "browser-use donor"
    sidecar_start_if_needed "peekaboo" "Peekaboo donor"
    sidecar_start_if_needed "screenpipe" "Screenpipe donor"
}

sidecar_health_all() {
    local failures=0

    if ! sidecar_health_report "OpenHands / system executor" "$(sidecar_health_url_for system_executor)" "system_executor"; then
        failures=1
    fi
    if ! sidecar_health_report "DeerFlow research daemon" "$(sidecar_health_url_for deerflow_research)" "deerflow_research"; then
        failures=1
    fi
    if ! sidecar_health_report "browser-use donor" "$(sidecar_health_url_for browser-use)" "browser-use"; then
        failures=1
    fi
    if ! sidecar_health_report "Peekaboo donor" "$(sidecar_health_url_for peekaboo)" "peekaboo"; then
        failures=1
    fi
    if ! sidecar_health_report "Screenpipe donor" "$(sidecar_health_url_for screenpipe)" "screenpipe"; then
        failures=1
    fi

    return "$failures"
}
