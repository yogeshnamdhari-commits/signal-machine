#!/bin/bash
# YOG'Z INSTITUTIONAL TRADING COMPANY — Production Background Launcher
# Starts engine + dashboard as background daemons with auto-restart

PROJECT_DIR="/Users/targetmobile/Documents/signal machine"
LOG_DIR="$PROJECT_DIR/packages/ai-engine/data/logs"
mkdir -p "$LOG_DIR"

echo "🚀 YOG'Z INSTITUTIONAL TRADING — Production Mode"
echo "================================================="

# Kill any existing instances
pkill -f "main.py.*mode.*engine" 2>/dev/null
pkill -f "server.py" 2>/dev/null
sleep 2

# Check if Streamlit is already running
if pgrep -f "streamlit.*app.py" > /dev/null; then
    echo "✅ Dashboard already running on port 8501"
else
    echo "🔄 Starting Dashboard..."
    cd "$PROJECT_DIR"
    nohup .venv/bin/python3 -m streamlit run packages/ai-engine/dashboard/app.py \
        --server.port 8501 \
        --server.address 0.0.0.0 \
        --server.headless true \
        --browser.gatherUsageStats false \
        --server.enableCORS false \
        --server.enableXsrfProtection false \
        --theme.base dark \
        --theme.primaryColor "#00ff88" \
        --theme.backgroundColor "#0e1117" \
        --theme.secondaryBackgroundColor "#1a1a2e" \
        --theme.textColor "#e0e0e0" \
        > "$LOG_DIR/dashboard.log" 2>&1 &
    echo "✅ Dashboard PID: $! — http://localhost:8501"
fi

sleep 3

# Start Engine
echo "🔄 Starting Engine..."
cd "$PROJECT_DIR"
nohup .venv/bin/python3 packages/ai-engine/main.py --mode engine \
    > "$LOG_DIR/engine.log" 2>&1 &
ENGINE_PID=$!
echo "✅ Engine PID: $ENGINE_PID"

sleep 5

# Start Execution Server
echo "🔄 Starting Execution Server..."
cd "$PROJECT_DIR"
nohup .venv/bin/python3 packages/ai-engine/execution/server.py \
    > "$LOG_DIR/server.log" 2>&1 &
SERVER_PID=$!
echo "✅ Server PID: $SERVER_PID"

echo ""
echo "================================================="
echo "✅ ALL SYSTEMS RUNNING IN BACKGROUND"
echo "================================================="
echo "📊 Dashboard:  http://localhost:8501"
echo "📡 Live Sheet: http://localhost:8501/Live_Sheet"
echo "🧠 Smart Money: http://localhost:8501/Smart_Money"
echo ""
echo "📋 Logs:"
echo "  Engine:   tail -f $LOG_DIR/engine.log"
echo "  Dashboard: tail -f $LOG_DIR/dashboard.log"
echo "  Server:   tail -f $LOG_DIR/server.log"
echo ""
echo "🔧 Stop all: pkill -f 'main.py.*engine' && pkill -f 'streamlit.*app.py'"
echo "================================================="
