#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 🚀 DeltaTerminal — Quick Start Dashboard
# 
# Starts the always-on dashboard with watchdog.
# The dashboard will NEVER stop — auto-restarts on any crash.
#
# Usage:
#   ./start_dashboard.sh              # Start (default port 8501)
#   ./start_dashboard.sh 8502         # Custom port
#   ./start_dashboard.sh --stop       # Stop running instance
#   ./start_dashboard.sh --status     # Check if running
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.dashboard.pid"

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ── Functions ──

show_status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo -e "${GREEN}✅ Dashboard is RUNNING${NC} (PID: $PID)"
            echo -e "   🌐 http://localhost:8501"
            echo -e "   📡 http://localhost:8501/_Live_Sheet"
            return 0
        else
            echo -e "${YELLOW}⚠️  Stale PID file found${NC}"
            rm -f "$PID_FILE"
            return 1
        fi
    else
        echo -e "${RED}❌ Dashboard is NOT running${NC}"
        return 1
    fi
}

stop_dashboard() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo -e "${YELLOW}🛑 Stopping dashboard (PID: $PID)...${NC}"
            kill "$PID" 2>/dev/null
            sleep 2
            if kill -0 "$PID" 2>/dev/null; then
                kill -9 "$PID" 2>/dev/null
            fi
            rm -f "$PID_FILE"
            echo -e "${GREEN}✅ Dashboard stopped.${NC}"
        else
            echo -e "${YELLOW}ℹ️  Process not found. Cleaning up.${NC}"
            rm -f "$PID_FILE"
        fi
    else
        echo -e "${BLUE}ℹ️  No running dashboard found.${NC}"
    fi
}

start_dashboard() {
    PORT=${1:-8501}

    # Check if already running
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo -e "${YELLOW}⚠️  Dashboard already running (PID: $PID)${NC}"
            echo -e "   🌐 http://localhost:$PORT"
            echo -e "   Use '$0 --stop' to stop it first"
            return 0
        fi
    fi

    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}⚡ DeltaTerminal — Always-On Dashboard${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo -e "  🌐 Dashboard:  ${GREEN}http://localhost:$PORT${NC}"
    echo -e "  📡 Live Sheet: ${GREEN}http://localhost:$PORT/_Live_Sheet${NC}"
    echo -e "  🔄 Watchdog:   ${GREEN}Active (auto-restart)${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo ""

    # Activate venv if available
    VENV="$SCRIPT_DIR/packages/ai-engine/venv/bin/activate"
    if [ -f "$VENV" ]; then
        source "$VENV"
    fi

    # Launch in foreground (keeps running, Ctrl+C to stop)
    python3 "$SCRIPT_DIR/launch_dashboard.py" --port "$PORT"
}

# ── Main ──

case "${1}" in
    --stop|-s)
        stop_dashboard
        ;;
    --status|-t)
        show_status
        ;;
    --help|-h)
        echo "Usage: $0 [PORT|--stop|--status|--help]"
        echo ""
        echo "  PORT        Start dashboard on port (default: 8501)"
        echo "  --stop      Stop running dashboard"
        echo "  --status    Check dashboard status"
        echo "  --help      Show this help"
        ;;
    *)
        start_dashboard "$1"
        ;;
esac
