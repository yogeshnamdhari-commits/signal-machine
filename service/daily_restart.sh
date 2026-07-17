#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# YOG'Z Signal Machine — Daily Restart Script (Cron Backup)
# Ensures the engine restarts fresh every day even if launchd fails.
# Schedule: crontab runs at 23:50 UTC daily
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail

PROJECT_ROOT="/Users/targetmobile/Documents/signal machine"
SERVICE_DIR="$PROJECT_ROOT/service"
LOG_FILE="$SERVICE_DIR/daily_restart.log"
PLIST_NAME="com.yogz.signalmachine"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "═══════════════════════════════════════════════════════════════"
log "🔄 YOG'Z Daily Restart — Beginning"
log "═══════════════════════════════════════════════════════════════"

# 1. Stop existing service gracefully
log "🛑 Stopping existing service..."
launchctl bootout gui/$(id -u)/$PLIST_NAME 2>/dev/null || true
sleep 3

# 2. Kill any orphan processes
log "🧹 Cleaning up orphan processes..."
pkill -f "main.py.*engine" 2>/dev/null || true
pkill -f "streamlit.*dashboard" 2>/dev/null || true
pkill -f "caffeinate.*signal" 2>/dev/null || true
sleep 2

# 3. Clear stale PID files
rm -f "$SERVICE_DIR/engine.pid" "$SERVICE_DIR/dashboard.pid" "$SERVICE_DIR/caffeinate.pid" "$SERVICE_DIR/launcher.pid"

# 4. Clear old logs (keep last 1000 lines each)
for logfile in "$PROJECT_ROOT/packages/ai-engine/data/logs/engine_service.log" \
               "$PROJECT_ROOT/packages/ai-engine/data/logs/dashboard_service.log" \
               "$SERVICE_DIR/service.log"; do
    if [ -f "$logfile" ]; then
        tail -1000 "$logfile" > "$logfile.tmp" && mv "$logfile.tmp" "$logfile"
    fi
done

# 5. Re-install and load the service
log "📦 Reinstalling service..."
bash "$SERVICE_DIR/install_service.sh" >> "$LOG_FILE" 2>&1

# 6. Verify it started
sleep 10
if launchctl print gui/$(id -u)/$PLIST_NAME > /dev/null 2>&1; then
    log "✅ Service loaded and running"
else
    log "⚠️  Service may need a moment to start"
fi

# Check engine
sleep 5
if pgrep -f "main.py.*engine" > /dev/null 2>&1; then
    log "✅ Engine is running"
else
    log "⚠️  Engine may still be starting (takes ~15s)"
fi

# Check dashboard
if curl -sf http://localhost:8501/_stcore/health > /dev/null 2>&1; then
    log "✅ Dashboard is healthy on port 8501"
else
    log "⚠️  Dashboard may still be starting (takes ~30s)"
fi

log "═══════════════════════════════════════════════════════════════"
log "🔄 Daily restart complete"
log "═══════════════════════════════════════════════════════════════"
