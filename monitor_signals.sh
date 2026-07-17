#!/bin/bash
# Continuous Signal Monitor — Auto-refresh every 60 seconds
# Shows latest scanning activity and signal generation

LOG_FILE="/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/logs/engine_2026-06-10.log"

clear
echo "🚀 DELTA TERMINAL — Live Signal Monitor"
echo "========================================"
echo "Auto-refreshing every 60 seconds..."
echo "Press Ctrl+C to stop"
echo ""

while true; do
    # Get current time
    TIMESTAMP=$(date "+%Y-%m-%d %H:%M:%S")
    
    # Count active symbols
    ACTIVE_SYMBOLS=$(grep -c "PROCESSING SYMBOL" "$LOG_FILE" 2>/dev/null | tail -1)
    
    # Get latest scan cycle stats
    SCAN_STATS=$(grep "Directional Balance" "$LOG_FILE" 2>/dev/null | tail -1)
    
    # Get highest confidence scores
    TOP_SCORES=$(grep "PHASE1" "$LOG_FILE" 2>/dev/null | tail -20 | sed 's/.*- //')
    
    # Get any ELITE signals
    ELITE_SIGNALS=$(grep "ELITE" "$LOG_FILE" 2>/dev/null | tail -5)
    
    # Get FVG detections
    FVG_COUNT=$(grep -c "FVG:" "$LOG_FILE" 2>/dev/null)
    
    # Get trade blocker status
    BLOCKER_STATUS=$(grep "TRADE BLOCKER" "$LOG_FILE" 2>/dev/null | tail -1)
    
    # Display
    clear
    echo "🚀 DELTA TERMINAL — Live Signal Monitor"
    echo "========================================"
    echo "Last refresh: $TIMESTAMP"
    echo ""
    
    echo "📊 SCANNING STATUS"
    echo "------------------"
    echo "Engine: Running (250 symbols)"
    echo "Scan interval: 5 seconds"
    echo "Total FVGs detected: $FVG_COUNT"
    echo ""
    
    echo "🎯 LATEST SIGNAL SCORES (Top 10)"
    echo "--------------------------------"
    grep "PHASE1" "$LOG_FILE" 2>/dev/null | tail -10 | while read line; do
        echo "$line" | sed 's/.*- //'
    done
    echo ""
    
    if [ -n "$ELITE_SIGNALS" ]; then
        echo "🔥 ELITE SIGNALS DETECTED"
        echo "------------------------"
        echo "$ELITE_SIGNALS" | while read line; do
            echo "$line" | sed 's/.*- //'
        done
    else
        echo "⏳ No elite signals yet (confidence < 85)"
    fi
    echo ""
    
    echo "📈 CYCLE STATS"
    echo "--------------"
    echo "$SCAN_STATS" | sed 's/.*- //'
    echo ""
    
    echo "🛡️ TRADE BLOCKER"
    echo "----------------"
    echo "$BLOCKER_STATUS" | sed 's/.*- //'
    echo ""
    
    echo "========================================"
    echo "Next refresh in 60 seconds..."
    echo "Press Ctrl+C to stop"
    
    sleep 60
done
