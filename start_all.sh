#!/bin/bash
# YOG'Z Signal Machine — Always-On Launcher
set -uo pipefail

PROJECT_ROOT="$(pwd)"
AI_ROOT="$PROJECT_ROOT/packages/ai-engine"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
STREAMLIT="$PROJECT_ROOT/.venv/bin/streamlit"
LOG_DIR="$AI_ROOT/data/logs"
PID_DIR="$PROJECT_ROOT/service"

mkdir -p "$LOG_DIR" "$PID_DIR"

echo "⚡ YOG'Z Signal Machine — Starting all services..."

# Prevent sleep
caffeinate -s -i -d -w 1 &
echo $! > "$PID_DIR/caffeinate.pid"
echo "🛡️  caffeinate active"

# Kill any existing
pkill -9 -f "main.py.*engine" 2>/dev/null
pkill -9 -f "streamlit.*app.py" 2>/dev/null
sleep 2

# Start Engine
cd "$PROJECT_ROOT"
nohup "$PYTHON" "$AI_ROOT/main.py" --mode engine \
    >> "$LOG_DIR/engine_service.log" 2>&1 &
echo $! > "$PID_DIR/engine.pid"
echo "🧠 Engine PID: $!"

# Wait for engine to initialize
sleep 8

# Start Dashboard
nohup "$STREAMLIT" run "$AI_ROOT/dashboard/app.py" \
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
echo $! > "$PID_DIR/dashboard.pid"
echo "📊 Dashboard PID: $! — http://localhost:8501"

echo ""
echo "✅ All services started!"
echo "   Engine:    $(cat $PID_DIR/engine.pid)"
echo "   Dashboard: $(cat $PID_DIR/dashboard.pid)"
echo "   caffeinate: $(cat $PID_DIR/caffeinate.pid)"
echo ""
echo "   Dashboard: http://localhost:8501"
echo "   Logs: tail -f $LOG_DIR/engine_service.log"
