# EMA V5 Production Acceptance Criteria

## Rules
- ❌ No new dashboards, modules, or refactoring during stabilization
- ✅ Fix → Run → Measure → Repeat
- ✅ ONE strategy change at a time, compare before/after
- ✅ If change doesn't improve metrics → REVERT

---

## Phase 1: Verify Position Monitoring Fix

### Smoke Test (First 5-10 trades after restart)

For EVERY completed trade, verify:

| Check | Pass | Fail Action |
|-------|------|-------------|
| MFE updates recorded | ✅/❌ | Fix monitoring |
| MAE updates recorded | ✅/❌ | Fix monitoring |
| Hold time increments | ✅/❌ | Fix monitoring |
| Current price updates | ✅/❌ | Fix monitoring |
| Trailing stop moves (if applicable) | ✅/❌ | Fix monitoring |
| Breakeven executes (if applicable) | ✅/❌ | Fix monitoring |
| Exit reason recorded correctly | ✅/❌ | Fix monitoring |

### Trade Lifecycle Integrity

For each completed trade:
- Entry time → Entry price → Highest/Lowest → MFE → MAE → Exit trigger → Exit price → PnL
- Exit price matches candle data at exit time
- MFE never negative
- MAE never exceeds actual price movement
- Hold duration = exit_time - entry_time

**If ANY trade fails → STOP and fix before continuing.**

---

## Phase 2: Collect Clean Data

- No code changes affecting trading logic
- No threshold changes
- No confidence changes
- Collect trades under stable codebase

---

## Phase 3: Validate Analytics

| Metric | Target |
|--------|--------|
| Lifecycle completeness | 100% |
| Monitoring failures | 0 |
| Phantom trades | 0 |
| Analytics reconciliation | 100% |

---

## Phase 4: Strategy Investigation

One change at a time:
1. What changed?
2. Which metric was expected to improve?
3. Did it improve?
4. If not → REVERT

---

## Phase 5: Operational Reliability

| Scenario | Success Criterion |
|----------|-------------------|
| Restart recovery | No open trades lose monitoring |
| Network interruption | Monitoring resumes automatically |
| Database recovery | No duplicate trades or missing events |
| Memory stability | 24-72h run, no memory growth |
| Reconciliation | End-of-day audit passes |

---

## Phase 6: Promotion to Production

| Metric | Pass Criterion |
|--------|---------------|
| Trade lifecycle completeness | 100% |
| Monitoring failures | 0 |
| Analytics reconciliation | 100% |
| Profit Factor (new trades) | > 1.0 |
| Expectancy | Positive |
| Phantom trades | 0 |
| Runtime stability | No crashes during validation |

---

## Release History

| Date | Version | Changes | Status |
|------|---------|---------|--------|
| 2026-07-17 | v5.0-stabilization | Dashboard contamination fix, position monitoring fix, architecture freeze | In Progress |

---

## Rollback Conditions

Revert to previous version if:
- Any trade loses lifecycle monitoring
- Analytics show phantom data
- Engine crashes during validation period
- Expectancy drops below -0.5R on rolling 20-trade window
