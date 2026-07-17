# EMA_V5 v1.0.0 — Rollback Guide

**Version:** EMA_V5 v1.0.0
**Date:** 2026-06-26

---

## Rollback Scenarios

### Scenario 1: EMA_V5 Module Issues (Keep Existing Systems)

**Impact:** Minimal — EMA_V5 is isolated from existing systems.

```bash
cd "/Users/targetmobile/Documents/signal machine"

# 1. Stop engine
kill $(cat service/engine.pid) 2>/dev/null
kill $(cat service/api.pid) 2>/dev/null

# 2. Remove EMA_V5 module
rm -rf packages/ai-engine/scanner/ema_v5

# 3. Remove EMA_V5 data
rm -f data/ema_v5_signals.db
rm -f data/bridge/ema_v5.json

# 4. Restart (existing systems continue)
bash start_production.sh
```

**Verification:** Existing signals, funnel, and market data continue operating.

---

### Scenario 2: Full System Rollback

**Impact:** Complete system restart required.

```bash
cd "/Users/targetmobile/Documents/signal machine"

# 1. Stop all processes
kill $(cat service/engine.pid) 2>/dev/null
kill $(cat service/api.pid) 2>/dev/null
kill $(cat service/dashboard.pid) 2>/dev/null

# 2. Remove all generated data
rm -rf data/bridge/*.json
rm -f data/ema_v5_signals.db

# 3. Restore from backup (if available)
# cp -r /backup/data/bridge/ data/bridge/

# 4. Restart
bash start_production.sh
bash start_dashboard.sh
```

---

### Scenario 3: Git Rollback

**Impact:** Code reverts to previous commit.

```bash
cd "/Users/targetmobile/Documents/signal machine"

# 1. Stop engine
kill $(cat service/engine.pid) 2>/dev/null

# 2. Check current commit
git log --oneline -5

# 3. Revert to specific commit
git checkout <commit-hash> -- packages/ai-engine/scanner/ema_v5/

# 4. Restart
bash start_production.sh
```

---

## Rollback Checklist

Before rollback:
- [ ] Document current system state
- [ ] Export any pending signals
- [ ] Note active positions
- [ ] Backup bridge files

After rollback:
- [ ] Verify engine starts successfully
- [ ] Verify WebSocket connects
- [ ] Verify bridge files regenerate
- [ ] Verify dashboard shows correct state
- [ ] Monitor for 15 minutes

---

## Data Preservation

The following data is preserved across rollbacks:
- `data/database/historical_klines.db` — Historical market data
- `data/bridge/trade_history.json` — Trade history
- `data/bridge/equity_history.json` — Equity curve
- `data/bridge/backtest_trades.json` — Backtest results

The following data is regenerated:
- `data/bridge/ema_v5.json` — EMA_V5 state
- `data/bridge/status.json` — Engine status
- `data/bridge/engine_health.json` — Health metrics
- `data/ema_v5_signals.db` — Signal database (empty on fresh start)

---

## Emergency Contacts

If rollback fails:
1. Check engine logs in `data/logs/`
2. Verify Python environment: `which python`
3. Check disk space: `df -h`
4. Check memory: `vm_stat`
5. Restart macOS if needed
