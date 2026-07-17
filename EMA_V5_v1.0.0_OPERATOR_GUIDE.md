# EMA_V5 v1.0.0 — Operator Guide

**Version:** EMA_V5 v1.0.0
**Date:** 2026-06-26

---

## Quick Start

### Start the System
```bash
cd "/Users/targetmobile/Documents/signal machine"
source .venv/bin/activate
bash start_production.sh
```

### Stop the System
```bash
cd "/Users/targetmobile/Documents/signal machine"
# Kill engine
kill $(cat service/engine.pid) 2>/dev/null
# Kill supervisor
kill $(cat service/api.pid) 2>/dev/null
```

### Check Status
```bash
# View bridge status
cat data/bridge/status.json | python -m json.tool

# View engine health
cat data/bridge/engine_health.json | python -m json.tool

# View EMA_V5 state
cat data/bridge/ema_v5.json | python -m json.tool
```

---

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│                   BINANCE WEBSOCKET                  │
│              (Live Futures Market Data)              │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                   ENGINE (Python)                    │
│  ┌────────────┐  ┌────────────┐  ┌──────────────┐  │
│  │   EMA_V5   │  │   Regime   │  │   Smart      │  │
│  │  Scanner   │  │   State    │  │   Money      │  │
│  └────────────┘  └────────────┘  └──────────────┘  │
│  ┌────────────┐  ┌────────────┐  ┌──────────────┐  │
│  │   Signal   │  │   Backtest │  │   Security   │  │
│  │  Pipeline  │  │   Engine   │  │   Monitor    │  │
│  └────────────┘  └────────────┘  └──────────────┘  │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│                 BRIDGE (JSON Files)                  │
│         data/bridge/*.json (17 files)                │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│               DASHBOARD (Streamlit)                  │
│              Real-time Visualization                 │
└─────────────────────────────────────────────────────┘
```

---

## Bridge Files Reference

| File | Purpose | Max Age |
|---|---|---|
| `ema_v5.json` | EMA_V5 signals, states, state counts | 30s |
| `status.json` | Engine running state, WebSocket status | 30s |
| `engine_health.json` | Signal counts, confidence, win rate | 30s |
| `market_data.json` | Live price data for all symbols | 10s |
| `signals.json` | Emitted trade signals | 60s |
| `positions.json` | Open positions | 30s |
| `equity_history.json` | Portfolio equity curve | 60s |
| `funnel.json` | Signal pipeline funnel stages | 30s |
| `alerts.json` | System alerts | 30s |
| `metrics.json` | Performance metrics | 30s |
| `smart_money_map.json` | Smart money flow analysis | 60s |
| `trade_history.json` | Historical trade records | 60s |

---

## State Machine Reference

| State | Description | Action |
|---|---|---|
| `NO_TREND` | No clear trend detected | Monitor only |
| `BUY_MODE` | Uptrend confirmed | Look for pullback entries |
| `SELL_MODE` | Downtrend confirmed | Look for pullback entries |
| `WAITING_PULLBACK` | Trend active, waiting for retracement | Monitor for pullback |
| `WAITING_CONFIRMATION` | Pullback detected, waiting for candle confirmation | Monitor for engulfing |
| `ACTIVE_BUY` | Long position active | Manage position |
| `ACTIVE_SELL` | Short position active | Manage position |

---

## Signal Confidence Thresholds

| Confidence | Interpretation |
|---|---|
| 0-50 | Low — rejected by pipeline |
| 50-70 | Medium — meets minimum threshold |
| 70-85 | High — strong signal quality |
| 85-95 | Elite — institutional grade |
| 95-100 | Maximum — highest conviction |

**Dynamic Threshold:** 85.0 (adjusts based on market conditions)

---

## Troubleshooting

### Live Sheet Shows OFFLINE
1. Check engine PID: `cat service/engine.pid`
2. Check if process is running: `ps aux | grep engine`
3. Check bridge age: `stat data/bridge/ema_v5.json`
4. Restart if needed: `bash start_production.sh`

### No Signals Generated
This is expected behavior. The system requires ALL of:
- Engulfing candlestick pattern
- 1.5x average volume
- Strong EMA trend alignment
- Pullback confirmation

These conditions rarely align on the same bar, especially at 90%+ confidence.

### WebSocket Disconnected
1. Check internet connectivity
2. Check Binance API status
3. Engine auto-reconnects within 30 seconds
4. If persistent, restart engine

### Bridge Stale (>30s old)
1. Engine may be stuck — check CPU usage
2. Kill and restart engine
3. Bridge files auto-regenerate on restart

---

## Daily Operations

### Morning Check
```bash
# Verify system is running
cat data/bridge/status.json | python -c "import sys,json; s=json.load(sys.stdin)['status']; print(f'Running: {s[\"running\"]}, WS: {s[\"ws_connected\"]}, Uptime: {s[\"uptime\"]:.0f}s')"
```

### Review Signals
```bash
# View emitted signals
cat data/bridge/signals.json | python -m json.tool

# View signal funnel
cat data/bridge/funnel.json | python -m json.tool
```

### Review Health
```bash
# Engine health
cat data/bridge/engine_health.json | python -m json.tool
```
