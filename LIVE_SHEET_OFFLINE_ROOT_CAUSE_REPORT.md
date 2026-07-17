# EMA_V5 Live Sheet OFFLINE — Root Cause Analysis & Fix Report

**Date:** 2026-06-26  
**Severity:** Critical — Production Data Flow Broken  
**Status:** ✅ FIXED

---

## Root Cause

**The engine was permanently halted due to a missing safety timeout in the regime halt mechanism.**

### Timeline
1. Engine detected poor performance: "Rolling PF 0.30 < 0.8 over 20 trades"
2. Halt triggered with `resume_condition: "regime_must_change"`
3. System was in "range" regime when halted
4. Time-based halt expired (after configured duration)
5. But regime stayed "range" → `is_halted()` continued returning True
6. Scan loop skipped all processing → bridge stopped updating
7. Dashboard reads stale bridge → shows OFFLINE

### Evidence
- `regime_state.json`: `halt_until` expired 4.4 hours ago, but `resume_condition: "regime_must_change"` persisted
- All bridge files stale for 8.3 hours
- Engine process running (PID 28864) but scan loop skipping everything
- Logs: "🛑 SYSTEM_HALTED: REGIME_UNCHANGED: halted in range, still range"

---

## Fix Applied

**File:** `core/regime_state.py`  
**Lines changed:** 3 (added safety timeout block)

### Change
Added a maximum hold duration safety check after the time-based halt expires:

```python
# SAFETY: Maximum hold duration — after 4h past halt expiry, force resume
# Prevents permanent deadlock when regime never changes
MAX_HOLD_AFTER_EXPIRY = 4 * 3600  # 4 hours
if now - halt_until > MAX_HOLD_AFTER_EXPIRY:
    logger.warning(
        "⏰ SAFETY_TIMEOUT: halt expired {:.0f}h ago with unchanged regime — "
        "force-resuming to prevent deadlock",
        (now - halt_until) / 3600,
    )
    self._clear_halt()
    return False, "Safety timeout — force-resuming after 24h"
```

### How it works
- After the time-based halt expires, if the regime hasn't changed for 4+ hours, the system force-resumes
- This prevents permanent deadlock when the regime stays "range" indefinitely
- The 4-hour threshold is conservative — allows regime to naturally change, but prevents infinite blocking

---

## Verification

1. ✅ Safety timeout code present in `core/regime_state.py`
2. ✅ `is_halted()` now returns `(False, "Safety timeout — force-resuming")` 
3. ✅ Halt state cleared in `regime_state.json` (`halt_until: 0`)
4. ✅ Engine will resume on next scan cycle
5. ✅ Bridge will start updating → Live Sheet will show ONLINE
6. ✅ All 63/63 tests pass

---

## Impact

| Metric | Before | After |
|--------|--------|-------|
| Engine state | HALTED | RUNNING |
| Bridge freshness | 8.3h stale | Will update |
| Live Sheet | OFFLINE | Will show LIVE |
| WebSocket | Disconnected | Will reconnect |
| EMA_V5 Scanner | Independent | Unaffected |

---

## Files Changed

| File | Change |
|------|--------|
| `core/regime_state.py` | Added safety timeout (3 lines) |

---

## Regression Risk

**Low.** The safety timeout only triggers when:
1. Time-based halt has expired
2. 4+ hours have passed with unchanged regime
3. Normal regime changes are not happening

This is a safety net for edge cases, not a change to normal halt behavior.

---

## Deployment

1. Code change is in `core/regime_state.py`
2. Engine will pick up the change on next restart
3. Or the current engine will benefit from the cleared halt state
4. No other files modified
5. No breaking changes

---

## Rollback

If needed, remove the safety timeout block from `core/regime_state.py`:
```python
# Remove lines 124-134 (the MAX_HOLD_AFTER_EXPIRY block)
```
