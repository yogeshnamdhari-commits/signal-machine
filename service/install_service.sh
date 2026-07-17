#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# YOG'Z Signal Machine — Service Installer
# Installs macOS launchd service for always-on operation
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.yogz.signalmachine"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
SERVICE_DIR="$SCRIPT_DIR"

echo "═══════════════════════════════════════════════════════════════"
echo "⚡ YOG'Z Signal Machine — Service Installer"
echo "═══════════════════════════════════════════════════════════════"

# ── Check prerequisites ────────────────────────────────────────
echo ""
echo "🔍 Checking prerequisites..."

# Check Python venv exists
if [ ! -f "$SERVICE_DIR/../.venv/bin/python" ]; then
    echo "❌ Python venv not found at .venv/"
    echo "   Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r packages/ai-engine/requirements.txt"
    exit 1
fi
echo "   ✅ Python venv found"

# Check streamlit exists
if [ ! -f "$SERVICE_DIR/../.venv/bin/streamlit" ]; then
    echo "❌ Streamlit not found in .venv"
    echo "   Run: source .venv/bin/activate && pip install streamlit"
    exit 1
fi
echo "   ✅ Streamlit found"

# Check main.py exists
if [ ! -f "$SERVICE_DIR/../packages/ai-engine/main.py" ]; then
    echo "❌ main.py not found"
    exit 1
fi
echo "   ✅ Engine entry point found"

# ── Stop existing service ─────────────────────────────────────
echo ""
echo "🛑 Stopping existing service (if running)..."
launchctl bootout gui/$(id -u)/$PLIST_NAME 2>/dev/null || true
sleep 1

# ── Install plist ─────────────────────────────────────────────
echo ""
echo "📦 Installing launchd service..."
echo "   Source: $PLIST_SRC"
echo "   Dest:   $PLIST_DST"

cp "$PLIST_SRC" "$PLIST_DST"
echo "   ✅ Plist installed"

# ── Load service ──────────────────────────────────────────────
echo ""
echo "🚀 Loading service..."
launchctl bootstrap gui/$(id -u) "$PLIST_DST"
echo "   ✅ Service loaded"

# ── Verify ────────────────────────────────────────────────────
echo ""
echo "🔍 Verifying service..."
sleep 3

if launchctl print gui/$(id -u)/$PLIST_NAME > /dev/null 2>&1; then
    echo "   ✅ Service is running!"
else
    echo "   ⚠️  Service may need a moment to start"
fi

# Check if caffeinate is running
if pgrep -x caffeinate > /dev/null 2>&1; then
    echo "   ✅ caffeinate is active — system will not sleep"
else
    echo "   ⚠️  caffeinate not yet running (will start with service)"
fi

# Check if engine is starting
sleep 5
if pgrep -f "main.py.*engine" > /dev/null 2>&1; then
    echo "   ✅ Engine is running"
else
    echo "   ⚠️  Engine may still be starting"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "✅ YOG'Z Signal Machine is now installed as a system service!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "📊 Dashboard:    http://localhost:8501"
echo "🧠 Engine:       Runs automatically in background"
echo "📡 API:          http://localhost:8001"
echo "🛡️  Sleep:        PREVENTED (caffeinate active)"
echo ""
echo "🔄 The service will:"
echo "   • Start automatically on boot/login"
echo "   • Auto-restart if it crashes"
echo "   • Survive VS Code closure"
echo "   • Survive laptop lid close (sleep prevented)"
echo "   • Restart after macOS sleep/wake"
echo ""
echo "📋 Commands:"
echo "   Status:   launchctl print gui/\$(id -u)/$PLIST_NAME"
echo "   Stop:     launchctl bootout gui/\$(id -u)/$PLIST_NAME"
echo "   Start:    launchctl bootstrap gui/\$(id -u) $PLIST_DST"
echo "   Logs:     tail -f $SERVICE_DIR/service.log"
echo "   Uninstall: bash $SCRIPT_DIR/uninstall_service.sh"
echo ""
