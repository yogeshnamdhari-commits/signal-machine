# EMA_V5 v1.0.0 — Rollback Guide

---

## Rollback Procedure

### Option 1: Remove EMA_V5 Only (Recommended)

```bash
cd "signal machine"

# 1. Stop engine
kill $(cat service/engine.pid) 2>/dev/null

# 2. Remove EMA_V5 module
rm -rf packages/ai-engine/scanner/ema_v5

# 3. Remove EMA_V5 data
rm -f data/ema_v5_signals.db
rm -f data/ema_v5_state.json
rm -f data/bridge/ema_v5.json

# 4. Restart (existing systems continue)
bash start_production.sh
```

### Option 2: Full Restart

```bash
# 1. Stop all
kill $(cat service/engine.pid) 2>/dev/null
kill $(cat service/api.pid) 2>/dev/null

# 2. Clean bridge
rm -f data/bridge/*.json

# 3. Restart
bash start_production.sh
```

---

## Verification After Rollback

```bash
# Engine running?
cat data/bridge/status.json | python3 -c "import sys,json;print(json.load(sys.stdin)['status']['running'])"

# Bridge fresh?
ls -la data/bridge/status.json
```

---

## Data Preserved

- `data/database/historical_klines.db` — Historical market data
- `data/bridge/trade_history.json` — Trade history
- `data/bridge/equity_history.json` — Equity curve
- `data/bridge/backtest_trades.json` — Backtest results

## Data Removed

- `data/ema_v5_signals.db` — Signal database
- `data/ema_v5_state.json` — State machine state
- `data/bridge/ema_v5.json` — EMA_V5 bridge
