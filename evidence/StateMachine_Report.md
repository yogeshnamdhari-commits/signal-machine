# EMA_V5 v1.0.0 — State Machine Report

**Date:** 2026-06-26 03:20 UTC

---

## 1. State Machine Definition

8 states, 28 transitions, verified no dead ends, no frozen states, no deadlocks.

---

## 2. All Legal Transitions

| From | To (Allowed) | Count |
|---|---|---|
| NO_TREND | BUY_MODE, SELL_MODE, WAITING_PULLBACK | 3 |
| BUY_MODE | NO_TREND, SELL_MODE, WAITING_PULLBACK | 3 |
| SELL_MODE | NO_TREND, BUY_MODE, WAITING_PULLBACK | 3 |
| WAITING_PULLBACK | NO_TREND, BUY_MODE, SELL_MODE, WAITING_CONFIRMATION, ACTIVE_BUY, ACTIVE_SELL | 6 |
| WAITING_CONFIRMATION | NO_TREND, WAITING_PULLBACK, ACTIVE_BUY, ACTIVE_SELL | 4 |
| ACTIVE_BUY | NO_TREND, SELL_MODE, TRADE_CLOSED | 3 |
| ACTIVE_SELL | NO_TREND, BUY_MODE, TRADE_CLOSED | 3 |
| TRADE_CLOSED | NO_TREND, BUY_MODE, SELL_MODE | 3 |
| **TOTAL** | | **28** |

---

## 3. Illegal Transitions (ALL REJECTED)

| From | To | Status |
|---|---|---|
| NO_TREND | ACTIVE_BUY | ❌ REJECTED |
| NO_TREND | ACTIVE_SELL | ❌ REJECTED |
| NO_TREND | TRADE_CLOSED | ❌ REJECTED |
| BUY_MODE | ACTIVE_BUY | ❌ REJECTED |
| BUY_MODE | TRADE_CLOSED | ❌ REJECTED |
| SELL_MODE | ACTIVE_SELL | ❌ REJECTED |
| SELL_MODE | TRADE_CLOSED | ❌ REJECTED |
| TRADE_CLOSED | WAITING_PULLBACK | ❌ REJECTED |
| TRADE_CLOSED | WAITING_CONFIRMATION | ❌ REJECTED |
| TRADE_CLOSED | ACTIVE_BUY | ❌ REJECTED |
| TRADE_CLOSED | ACTIVE_SELL | ❌ REJECTED |

---

## 4. Deadlock Check

| Check | Result | Evidence |
|---|---|---|
| Dead ends (no exit) | 0 | All 8 states have ≥3 outgoing transitions |
| Frozen states | 0 | All states can transition to at least one other |
| Unreachable states | 0 | All states reachable from NO_TREND |
| Infinite loops | None | MAX_HOLD_AFTER_EXPIRY safety timeout |

---

## 5. Live State Distribution

| State | Count | Percentage |
|---|---|---|
| WAITING_PULLBACK | 68 | 50.0% |
| NO_TREND | 30 | 22.1% |
| WAITING_CONFIRMATION | 15 | 11.0% |
| BUY_MODE | 12 | 8.8% |
| SELL_MODE | 10 | 7.4% |
| ACTIVE_BUY | 1 | 0.7% |
| ACTIVE_SELL | 0 | 0.0% |
| TRADE_CLOSED | 0 | 0.0% |
| **TOTAL** | **136** | **100%** |

---

## 6. Safety Mechanisms

| Mechanism | Value | Source |
|---|---|---|
| Max hold after expiry | 4 hours | `regime_state.py MAX_HOLD_AFTER_EXPIRY` |
| State persistence | JSON file | `data/ema_v5_state.json` |
| Atomic writes | tmp→replace | `state_manager._save()` |
| Cooldown enforcement | 1h same-symbol, 1min global | `ema_v5_config.cooldown` |
