# Production Verification Audit — EMA V5
## Date: 2026-07-17
## Status: IN PROGRESS

---

## P0: Critical Verification Items

### P0-1: Verify every dashboard's SQL/query
- [ ] Production Analytics — `SELECT * FROM positions_archive` (no filter)
- [ ] Research Platform — `SELECT * FROM ema_v5_trade_facts` (EMA V5 only)
- [ ] EMA V5 Scanner — runtime counter (not DB-backed)
- [ ] Candidate Repository — `SELECT * FROM ema_v5_candidates`
- [ ] Performance (In-Memory) — `self._closed_trades` list

### P0-2: Verify every metric has one authoritative source
- [ ] "Total Trades" → which table?
- [ ] "Win Rate" → which query?
- [ ] "Profit Factor" → which dataset?
- [ ] "Signals Generated" → runtime counter or DB?
- [ ] "Published Signals" → runtime counter or DB?

### P0-3: Verify EMA V5-only filtering everywhere it should exist
- [ ] Research Platform uses `ema_v5_trade_facts` view
- [ ] Production Analytics has strategy filter (default "All")
- [ ] Candidate Repository filters by strategy_version

### P0-4: Verify signal → trade → close reconciliation
- [ ] Scanner generates signal
- [ ] Signal saved to `signals` table
- [ ] Position opened in `positions` table
- [ ] Position closed → archived to `positions_archive`
- [ ] Rejection tracker logs every outcome

### P0-5: Verify 100 random trades manually against DB
- [x] Pick 10 random EMA V5 trades
- [x] Verify entry_price matches signal — ✅ All pass
- [x] Verify exit_reason matches engine log — ✅ All pass
- [x] Verify PnL calculation is correct — ✅ All pass
- [ ] Verify MFE/MAE tracking is accurate — ❌ **7 trades have MFE=0**
- [ ] Verify hold_minutes is accurate — ❌ **10 trades have hold_minutes=0**
- [ ] Verify realized_r is accurate — ❌ **9 trades have realized_r=0**

### P0-6: Fix data quality issues found in P0-5
- [ ] **MFE=0 bug**: 7 trades held >1 hour show MFE=0 (root cause: lifecycle engine not registered after restart)
- [ ] **hold_minutes=0 bug**: 10 trades show hold_minutes=0 despite being held for hours
- [ ] **realized_r=0 bug**: 9 trades with non-zero PnL show realized_r=0

---

## P1: Cleanup Items

### P1-1: Remove duplicate KPIs
- [ ] Identify overlapping metrics across dashboards
- [ ] Consolidate to single source per metric

### P1-2: Remove unused modules
- [ ] Identify modules with zero active usage
- [ ] Archive or delete

---

## P2: Optimization Items

### P2-1: Optimize performance
- [ ] Database query optimization
- [ ] Dashboard load time
