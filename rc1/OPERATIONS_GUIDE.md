# EMA_V5 v1.0.0 — Operations Guide

---

## Daily Operations

### Morning Check
```bash
cat data/bridge/status.json | python3 -c "
import sys,json
s=json.load(sys.stdin)['status']
print(f'Engine: {\"RUNNING\" if s[\"running\"] else \"STOPPED\"}')
print(f'WebSocket: {\"CONNECTED\" if s[\"ws_connected\"] else \"DISCONNECTED\"}')
print(f'Uptime: {s[\"uptime\"]/3600:.1f}h')
print(f'Symbols: {s[\"symbols\"]}')
"
```

### Monitor Bridge
```bash
ls -la data/bridge/*.json | awk '{print $6,$7,$8,$9}'
```

### Check EMA_V5 State
```bash
cat data/bridge/ema_v5.json | python3 -c "
import sys,json
e=json.load(sys.stdin)['ema_v5']
sc=e.get('state_counts',{})
print(f'States: {sc}')
print(f'Scans: {e[\"scanner\"][\"scan_count\"]}')
print(f'Cache: {e[\"scanner\"][\"cache_size\"]}')
"
```

---

## Troubleshooting

| Issue | Check | Fix |
|---|---|---|
| Engine stopped | `ps aux \| grep engine` | `bash start_production.sh` |
| Bridge stale | `stat data/bridge/status.json` | Restart engine |
| WebSocket disconnected | `status.json → ws_connected` | Auto-reconnects in 30s |
| No signals | `ema_v5.json → scanner.signal_count` | Expected at 90% threshold |
| High CPU | `ps aux \| grep python` | Check for tight loops |

---

## Monitoring

| Metric | Source | Healthy Range |
|---|---|---|
| Bridge Age | `stat data/bridge/*.json` | < 300s |
| Tick Count | `status.json → tick_count` | Increasing |
| Reconnects | `status.json → reconnect_count` | 0 |
| Errors | `status.json → error_count` | 0 |
| Memory | `ps aux \| grep python` | < 500MB |
| CPU | `top -l 1 \| grep CPU` | < 50% |
