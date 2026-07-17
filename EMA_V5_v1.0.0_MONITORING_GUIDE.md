# EMA_V5 v1.0.0 — Monitoring Guide

**Version:** EMA_V5 v1.0.0
**Date:** 2026-06-26

---

## Runtime Metrics

### System Metrics (Collect Every 60s)

| Metric | Source | Healthy Range |
|---|---|---|
| **CPU Usage** | `ps aux` | < 50% |
| **RAM Usage** | `vm_stat` | < 500MB |
| **Disk Usage** | `df -h` | < 80% |
| **Uptime** | `data/bridge/status.json` | > 0s |

### Bridge Metrics (Collect Every 10s)

| Metric | Source | Healthy Range |
|---|---|---|
| **Bridge Age** | `os.path.getmtime()` | < 30s |
| **Sync Spread** | Difference between files | < 10s |
| **File Count** | `data/bridge/*.json` | 17 files |

### WebSocket Metrics (Collect Every 30s)

| Metric | Source | Healthy Range |
|---|---|---|
| **Connected** | `status.json → ws_connected` | True |
| **Tick Count** | `status.json → tick_count` | Increasing |
| **Reconnects** | `status.json → reconnect_count` | 0 |
| **Dropped** | `status.json → dropped_count` | 0 |
| **Errors** | `status.json → error_count` | 0 |

### Signal Metrics (Collect Every 60s)

| Metric | Source | Healthy Range |
|---|---|---|
| **Signals Generated** | `engine_health.json` | Variable |
| **Elite Signals** | `engine_health.json` | Variable |
| **Dynamic Threshold** | `engine_health.json` | 70-95 |
| **State Distribution** | `ema_v5.json → state_counts` | Balanced |

### Performance Metrics (Collect Every 300s)

| Metric | Source | Healthy Range |
|---|---|---|
| **Scan Latency** | Engine logs | < 100ms |
| **Signal Gen Time** | Engine logs | < 500ms |
| **Dashboard Refresh** | Streamlit | < 5s |
| **JSON Write Time** | Bridge writes | < 10ms |

---

## Monitoring Script

### Quick Health Check
```bash
cd "/Users/targetmobile/Documents/signal machine"
python << 'EOF'
import json, time, os

now = time.time()

# Bridge freshness
for f in ['ema_v5.json', 'status.json', 'engine_health.json']:
    path = f'data/bridge/{f}'
    if os.path.exists(path):
        age = now - os.path.getmtime(path)
        status = '✅' if age < 300 else '⚠️'
        print(f'{status} {f}: {age:.0f}s')
    else:
        print(f'❌ {f}: MISSING')

# Engine status
with open('data/bridge/status.json') as f:
    s = json.load(f)['status']
    print(f"\n{'✅' if s['running'] else '❌'} Engine: {'Running' if s['running'] else 'STOPPED'}")
    print(f"{'✅' if s['ws_connected'] else '❌'} WebSocket: {'Connected' if s['ws_connected'] else 'Disconnected'}")
    print(f"  Uptime: {s['uptime']:.0f}s")
    print(f"  Symbols: {s['symbols']}")
    print(f"  Signals: {s['signals']}")
EOF
```

### Continuous Monitoring (Background)
```bash
# Monitor every 30 seconds
while true; do
    echo "$(date): $(python -c "
import json
with open('data/bridge/status.json') as f:
    s = json.load(f)['status']
print(f\"Running={s['running']} WS={s['ws_connected']} Uptime={s['uptime']:.0f}s\")
")"
    sleep 30
done
```

---

## Alert Thresholds

| Condition | Severity | Action |
|---|---|---|
| Bridge age > 60s | ⚠️ WARNING | Check engine process |
| Bridge age > 300s | 🔴 CRITICAL | Restart engine |
| WebSocket disconnected > 60s | ⚠️ WARNING | Check network |
| WebSocket disconnected > 300s | 🔴 CRITICAL | Restart engine |
| Error count > 0 | ⚠️ WARNING | Review error logs |
| CPU > 80% | ⚠️ WARNING | Check for loops |
| RAM > 1GB | 🔴 CRITICAL | Restart engine |
| 0 signals after 24h | ℹ️ INFO | Normal at high threshold |

---

## Log Locations

| Log | Location | Purpose |
|---|---|---|
| Engine logs | `data/logs/engine.log` | Main engine output |
| Audit logs | `data/logs/audit.log` | Security events |
| Error logs | `data/logs/error.log` | Error tracebacks |

---

## Dashboard Monitoring

The Streamlit dashboard provides real-time visualization:
```bash
bash start_dashboard.sh
```

Dashboard shows:
- Live price charts
- Signal funnel visualization
- State machine status
- Equity curve
- Trade history
- Smart money flow
