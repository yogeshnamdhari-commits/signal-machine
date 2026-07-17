#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# YOG'Z Signal Machine — Service Uninstaller
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.yogz.signalmachine"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
PID_DIR="$SCRIPT_DIR"

echo "═══════════════════════════════════════════════════════════════"
echo "🛑 YOG'Z Signal Machine — Service Uninstaller"
echo "═══════════════════════════════════════════════════════════════"

# ── Stop service ──────────────────────────────────────────────
echo ""
echo "🛑 Stopping service..."
launchctl bootout gui/$(id -u)/$PLIST_NAME 2>/dev/null || true
sleep 2
echo "   ✅ Service stopped"

# ── Kill any remaining processes ──────────────────────────────
echo ""
echo "🧹 Cleaning up processes..."

# Kill caffeinate
if [ -f "$PID_DIR/caffeinate.pid" ]; then
    kill "$(cat "$PID_DIR/caffeinate.pid")" 2>/dev/null || true
    rm -f "$PID_DIR/caffeinate.pid"
fi

# Kill engine
if [ -f "$PID_DIR/engine.pid" ]; then
    kill "$(cat "$PID_DIR/engine.pid")" 2>/dev/null || true
    rm -f "$PID_DIR/engine.pid"
fi

# Kill dashboard
if [ -f "$PID_DIR/dashboard.pid" ]; then
    kill "$(cat "$PID_DIR/dashboard.pid")" 2>/dev/null || true
    rm -f "$PID_DIR/dashboard.pid"
fi

# Kill API
if [ -f "$PID_DIR/api.pid" ]; then
    kill "$(cat "$PID_DIR/api.pid")" 2>/dev/null || true
    rm -f "$PID_DIR/api.pid"
fi

# Kill by port
lsof -ti:8501 | xargs kill 2>/dev/null || true
lsof -ti:3001 | xargs kill 2>/dev/null || true
lsof -ti:8001 | xargs kill 2>/dev/null || true

echo "   ✅ Processes cleaned up"

# ── Remove plist ──────────────────────────────────────────────
echo ""
echo "🗑️  Removing plist..."
if [ -f "$PLIST_DST" ]; then
    rm -f "$PLIST_DST"
    echo "   ✅ Plist removed: $PLIST_DST"
else
    echo "   ℹ️  Plist not found (already removed)"
fi

# ── Remove PID files ──────────────────────────────────────────
echo ""
echo "🗑️  Cleaning up PID files..."
rm -f "$PID_DIR"/*.pid
rm -f "$PID_DIR/restart_counter"
echo "   ✅ PID files cleaned"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "✅ YOG'Z Signal Machine service uninstalled!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "💡 To reinstall: bash $SCRIPT_DIR/install_service.sh"
echo ""
