#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# YOG'Z Signal Machine — Health Check Script
# Quick check if everything is running. Returns exit code 0 if healthy.
# ═══════════════════════════════════════════════════════════════════
set -uo pipefail

PROJECT_ROOT="/Users/targetmobile/Documents/signal machine"
SERVICE_DIR="$PROJECT_ROOT/service"
HEALTHY=true

echo "═══════════════════════════════════════════════════════════════"
echo "🏥 YOG'Z Signal Machine — Health Check"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# 1. Check launchd service
if launchctl print gui/$(id -u)/com.yogz.signalmachine > /dev/null 2>&1; then
    echo "✅ launchd service: LOADED"
else
    echo "❌ launchd service: NOT LOADED"
    HEALTHY=false
fi

# 2. Check engine process
if pgrep -f "main.py.*engine" > /dev/null 2>&1; then
    ENGINE_PID=$(pgrep -f "main.py.*engine" | head -1)
    echo "✅ Engine: RUNNING (PID: $ENGINE_PID)"
else
    echo "❌ Engine: NOT RUNNING"
    HEALTHY=false
fi

# 3. Check dashboard
if pgrep -f "streamlit.*dashboard" > /dev/null 2>&1; then
    DASH_PID=$(pgrep -f "streamlit.*dashboard" | head -1)
    echo "✅ Dashboard: RUNNING (PID: $DASH_PID)"
else
    echo "❌ Dashboard: NOT RUNNING"
    HEALTHY=false
fi

# 4. Check dashboard HTTP
if curl -sf http://localhost:8501/_stcore/health > /dev/null 2>&1; then
    echo "✅ Dashboard HTTP: HEALTHY (port 8501)"
else
    echo "⚠️  Dashboard HTTP: NOT RESPONDING (may be starting)"
fi

# 5. Check caffeinate
if pgrep -x caffeinate > /dev/null 2>&1; then
    echo "✅ caffeinate: ACTIVE (sleep prevented)"
else
    echo "⚠️  caffeinate: NOT RUNNING"
fi

# 6. Check engine data freshness
if [ -f "$PROJECT_ROOT/data/bridge/market_data.json" ]; then
    MOD_TIME=$(stat -f %m "$PROJECT_ROOT/data/bridge/market_data.json" 2>/dev/null || echo 0)
    NOW=$(date +%s)
    AGE=$(( NOW - MOD_TIME ))
    if [ "$AGE" -lt 120 ]; then
        echo "✅ Market data: FRESH (${AGE}s ago)"
    elif [ "$AGE" -lt 600 ]; then
        echo "⚠️  Market data: STALE (${AGE}s ago)"
    else
        echo "❌ Market data: VERY STALE (${AGE}s ago)"
        HEALTHY=false
    fi
else
    echo "❌ Market data: FILE NOT FOUND"
    HEALTHY=false
fi

# 7. Check open positions
if [ -f "$PROJECT_ROOT/data/bridge/positions.json" ]; then
    POS_COUNT=$(python3 -c "import json; d=json.load(open('$PROJECT_ROOT/data/bridge/positions.json')); print(len(d.get('positions',[])))" 2>/dev/null || echo "?")
    echo "📊 Open positions: $POS_COUNT"
fi

echo ""
echo "═══════════════════════════════════════════════════════════════"

if [ "$HEALTHY" = true ]; then
    echo "🟢 SYSTEM HEALTHY — All components running"
    exit 0
else
    echo "🔴 SYSTEM UNHEALTHY — Some components down"
    exit 1
fi
