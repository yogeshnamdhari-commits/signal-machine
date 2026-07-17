#!/bin/bash
cd "/Users/targetmobile/Documents/signal machine"
.venv/bin/python3 packages/ai-engine/main.py --mode engine > packages/ai-engine/data/logs/engine_service.log 2>&1 &
echo "Engine PID: $!"
