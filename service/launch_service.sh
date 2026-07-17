#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# YOG'Z Signal Machine — Always-On Service Launcher (v2)
# Keeps engine + dashboard + caffeinate running 24/7 with auto-restart
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AI_ROOT="$PROJECT_ROOT/packages/ai-engine"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
PYTHON="${VENV_PYTHON}"
STREAMLIT="$PROJECT_ROOT/.venv/bin/streamlit"
LOG_DIR="$AI_ROOT/data/logs"
SERVICE_LOG="$PROJECT_ROOT/service/service.log"
PID_DIR="$PROJECT_ROOT/service"
ENGINE_PID="$PID_DIR/engine.pid"
DASHBOARD_PID="$PID_DIR/dashboard.pid"
CAFFEINATE_PID="$PID_DIR/caffeinate.pid"
LAUNCHER_PID="$PID_DIR/launcher.pid"
COUNTER_FILE="$PID_DIR/restart_counter"
HEALTHY_COUNT=0

mkdir -p "$LOG_DIR" "$PID_DIR"

# Truncate old logs on fresh start
: > "$SERVICE_LOG"
: > "$LOG_DIR/engine_service.log"
: > "$LOG_DIR/dashboard_service.log"

# ── Logging ──────────────────────────────────────────────────────
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$SERVICE_LOG"
}

# ── Prevent macOS sleep ─────────────────────────────────────────
prevent_sleep() {
    if [ -f "$CAFFEINATE_PID" ] && kill -0 "$(cat "$CAFFEINATE_PID")" 2>/dev/null; then
        return 0
    fi
    caffeinate -s -i -d -w 1 &
    echo $! > "$CAFFEINATE_PID"
    log "🛡️  caffeinate active (PID: $!) — system will not sleep"
}

# ── Is process alive? ───────────────────────────────────────────
is_alive() {
    local pid_file=$1
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# ── Start Engine ────────────────────────────────────────────────
start_engine() {
    cd "$PROJECT_ROOT"
    log "🧠 Starting Engine..."
    "$PYTHON" "$AI_ROOT/main.py" --mode engine \
        >> "$LOG_DIR/engine_service.log" 2>&1 &
    echo $! > "$ENGINE_PID"
    log "✅ Engine PID: $!"
}

# ── Start Dashboard ─────────────────────────────────────────────
start_dashboard() {
    cd "$PROJECT_ROOT"
    log "📊 Starting Dashboard..."
    "$STREAMLIT" run "$AI_ROOT/dashboard/app.py" \
        --server.port 8501 \
        --server.address 0.0.0.0 \
        --server.headless true \
        --browser.gatherUsageStats false \
        --server.enableCORS false \
        --server.enableXsrfProtection false \
        --theme.base dark \
        --theme.primaryColor '#00ff88' \
        --theme.backgroundColor '#0e1117' \
        --theme.secondaryBackgroundColor '#1a1a2e' \
        --theme.textColor '#e0e0e0' \
        >> "$LOG_DIR/dashboard_service.log" 2>&1 &
    echo $! > "$DASHBOARD_PID"
    log "✅ Dashboard PID: $! — http://localhost:8501"
}

# ── Restart counter ─────────────────────────────────────────────
get_restart_count() {
    [ -f "$COUNTER_FILE" ] && cat "$COUNTER_FILE" || echo "0"
}
increment_restart_count() {
    echo $(( $(get_restart_count) + 1 )) > "$COUNTER_FILE"
}
reset_restart_count() {
    echo "0" > "$COUNTER_FILE"
}

# ── Shutdown ────────────────────────────────────────────────────
shutdown_all() {
    log "🛑 Shutting down all services..."
    [ -f "$CAFFEINATE_PID" ] && kill "$(cat "$CAFFEINATE_PID")" 2>/dev/null; rm -f "$CAFFEINATE_PID"
    [ -f "$DASHBOARD_PID" ] && kill "$(cat "$DASHBOARD_PID")" 2>/dev/null; rm -f "$DASHBOARD_PID"
    [ -f "$ENGINE_PID" ] && kill "$(cat "$ENGINE_PID")" 2>/dev/null; rm -f "$ENGINE_PID"
    rm -f "$LAUNCHER_PID"
    log "✅ All services stopped."
}

# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════
main() {
    log "═══════════════════════════════════════════════════════════════"
    log "⚡ YOG'Z Signal Machine — Always-On Service v2"
    log "═══════════════════════════════════════════════════════════════"

    echo $$ > "$LAUNCHER_PID"
    trap 'shutdown_all; exit 0' SIGTERM SIGINT SIGHUP

    prevent_sleep

    # ── Initial startup ─────────────────────────────────────────
    start_engine
    log "⏳ Waiting 8s for engine to initialize..."
    sleep 8

    start_dashboard
    log "⏳ Waiting 20s for Streamlit to become healthy..."
    sleep 20

    # Verify dashboard HTTP health
    local retries=0
    while ! curl -sf http://localhost:8501/_stcore/health > /dev/null 2>&1; do
        retries=$((retries + 1))
        if [ "$retries" -gt 6 ]; then
            log "🚨 Dashboard failed after 60s — restarting..."
            kill "$(cat "$DASHBOARD_PID" 2>/dev/null)" 2>/dev/null || true
            sleep 3
            start_dashboard
            sleep 20
            break
        fi
        sleep 10
    done

    if curl -sf http://localhost:8501/_stcore/health > /dev/null 2>&1; then
        log "✅ Dashboard healthy on port 8501"
    fi

    log "🟢 All services running. Monitoring every 30s..."

    # ── Watchdog loop ───────────────────────────────────────────
    while true; do
        sleep 30

        # Keep caffeinate alive
        if ! kill -0 "$(cat "$CAFFEINATE_PID" 2>/dev/null)" 2>/dev/null; then
            prevent_sleep
        fi

        # Engine health
        if ! is_alive "$ENGINE_PID"; then
            local count
            count=$(get_restart_count)
            if [ "$count" -gt 10 ]; then
                log "🚨 Too many engine restarts ($count) — cooling down 5 min"
                sleep 300
                reset_restart_count
            fi
            increment_restart_count
            log "🔄 Engine died — restarting (attempt $(get_restart_count))..."
            start_engine
        fi

        # Dashboard PID health
        if ! is_alive "$DASHBOARD_PID"; then
            log "🔄 Dashboard died — restarting..."
            start_dashboard
            sleep 10
        fi

        # Dashboard HTTP health (only if PID alive)
        if is_alive "$DASHBOARD_PID" && ! curl -sf http://localhost:8501/_stcore/health > /dev/null 2>&1; then
            HEALTHY_COUNT=$((HEALTHY_COUNT + 1))
            if [ "$HEALTHY_COUNT" -ge 3 ]; then
                log "⚠️  Dashboard unresponsive for 90s — restarting..."
                kill "$(cat "$DASHBOARD_PID" 2>/dev/null)" 2>/dev/null || true
                sleep 3
                start_dashboard
                HEALTHY_COUNT=0
                sleep 10
            fi
        else
            HEALTHY_COUNT=0
        fi

        # Everything healthy — reset crash counter
        if is_alive "$ENGINE_PID" && is_alive "$DASHBOARD_PID"; then
            reset_restart_count
        fi
    done
}

main "$@"
