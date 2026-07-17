# 🔍 Loss Forensic Report — June 16, 2026

## Executive Summary

**17 losing trades** on June 16 resulted in **-$52.46 total loss**. Investigation uncovered **5 distinct root causes**, ranging from critical engine bugs to configuration deficiencies. The #1 cause (time exit) accounts for **75.2% of all losses**.

| Root Cause | Trades | Total Loss | % of Losses |
|---|---|---|---|
| 🕐 Time Exit on Stale Trades | 5 | -$39.44 | 75.2% |
| 🚫 Zero-Confidence Trades | 3 | -$9.04 | 17.2% |
| ❌ Missing SL/TP Parameters | 3 | -$3.96 | 7.5% |
| ⚠️ SHORT SL Inversion Bug | 5 | -$2.89 | 5.5% |
| 📉 Tight Stop-Loss Hits (NY) | 9 | -$8.21 | 15.6% |

> Note: Some trades are affected by multiple root causes (e.g., PIPPINUSDT has inverted SL + trailing stop loss), so percentages overlap.

---

## ROOT CAUSE #1: 🕐 Time Exit on Stale Trades — $39.44 (75.2%)

### Evidence

| Symbol | Side | Entry | PnL | Hold | Exit | Conf | Regime |
|---|---|---|---|---|---|---|---|
| SPACEUSDT | LONG | 0.008531 | **-$24.64** | 487min | time_exit_6h | 93.2% | trending_bull |
| MITOUSDT | SHORT | 0.02018 | -$5.76 | 487min | time_exit_6h | 95.4% | trending_bear |
| HOMEUSDT | LONG | 0.0282 | -$5.34 | 483min | time_exit_6h | 0.0% | unknown |
| BABYUSDT | LONG | 0.01785 | -$2.56 | 475min | time_exit_6h | 0.0% | unknown |
| NAORISUSDT | SHORT | 0.03205 | -$1.14 | 475min | time_exit_6h | 0.0% | unknown |

### Analysis

**SPACEUSDT — The $24.64 Impossible Loss:**
- Entry: $0.008531, SL: $0.00845763 (0.86% below entry)
- Expected max SL loss: **$0.76** (at 10x leverage)
- Actual loss: **$24.64** (32.2× the expected max)
- Implied exit price: $0.006166 — a **27.7% drop** from entry
- MFE% recorded: 13.82% (trade reached +13.82% at peak)
- **The trade was up 13.82% at peak, then crashed 27.7% — a 41.5% reversal swing**

**Why time_exit fired incorrectly:**

The `check_exit_conditions()` in `risk_engine.py` (line 175):
```python
if opened_at > 0 and time.time() - opened_at > 21600:  # 6 hours
    if prev_peak < 1.2:    # ← ONLY fires if peak was below 1.2R
        return True, "time_exit_6h"
```

For SPACEUSDT with peak at 13.82% MFE and risk_per_unit using the 2% floor:
- `risk_per_unit = max(0.00007337, 0.00017062, 0.00001706) = 0.00017062`
- Peak R = 13.82% / 2.0% = **6.9R** — well above the 1.2R threshold

**The trailing stop should have protected this trade** (activates at 2.0R, trails at 65%). Peak of 6.9R → trail floor at 4.5R. But the trade lost 13.9R, meaning the trailing stop never activated.

**Root cause:** The engine state persistence (`_highest_pnl`) was either:
1. Lost during an engine restart (engine restarted at 04:06 UTC — 4.5 hours before trades opened)
2. Not populated correctly for these "current" version trades (legacy code path)
3. The MFE/MAE values in the DB are unreliable (MFE% = MAE% = 13.82% — identical values indicate a recording bug)

**PIPPINUSDT (trailing_stop loss):**
- Peak: 2.3R, exited at -1.0R
- Trailing stop should have fired at 1.5R (65% of 2.3R peak)
- Same root cause: `_highest_pnl` reset → peak tracking lost → trailing stop never activated

### Fix Required
1. **Persist `_highest_pnl` to DB on every scan cycle**, not just at shutdown
2. **Reconstruct peak from price history** on engine restart (query highest/lowest prices from klines during trade lifetime)
3. **Fix MFE/MAE recording** — identical MFE=MAE values indicate a bug in `trade_engine.py`

---

## ROOT CAUSE #2: 🚫 Zero-Confidence Trades — $9.04 (17.2%)

### Evidence

| Symbol | Side | Entry | PnL | Conf | Regime | Strat | Exit |
|---|---|---|---|---|---|---|---|
| HOMEUSDT | LONG | 0.0282 | -$5.34 | **0.0%** | **unknown** | current | time_exit_6h |
| BABYUSDT | LONG | 0.01785 | -$2.56 | **0.0%** | **unknown** | current | time_exit_6h |
| NAORISUSDT | SHORT | 0.03205 | -$1.14 | **0.0%** | **unknown** | current | time_exit_6h |

### Analysis

All 3 trades share:
- `confidence = 0%` — no quality score computed
- `regime = unknown` — no market regime detected
- `institutional_score = 0` — zero institutional analysis
- `strategy_version = "current"` — legacy engine code path
- No `session` assigned
- No `signal_id` — not linked to any scanned signal

These trades were opened by the **legacy code path** (strategy_version="current") which bypassed the confidence floor filter. The production_v2 engine enforces `CONFIDENCE_FLOOR` (typically 70%), but the legacy path had no such gate.

**Loss contribution:** 3 trades, -$9.04, **all 3 were losses** (0% win rate for zero-confidence trades)

### Fix Required
1. **Block all trades with confidence < 70%** regardless of code path
2. **Remove or fully disable legacy trade entry code** — force all trades through production_v2 pipeline
3. **Require regime detection** — block trades where regime = "unknown"

---

## ROOT CAUSE #3: ❌ Missing SL/TP Parameters — $3.96 (7.5%)

### Evidence

| Symbol | Side | Entry | SL | TP | PnL | Conf | Exit |
|---|---|---|---|---|---|---|---|
| BCHUSDT | LONG | 226.38 | **0.0** | **0.0** | -$1.21 | 95.0% | stop_loss |
| SYNUSDT | LONG | 0.05227 | **0.0** | **0.0** | -$1.93 | 90.2% | stop_loss |
| METUSDT | LONG | 0.1192 | **0.0** | **0.0** | -$0.82 | 82.1% | stop_loss |

### Analysis

All 3 trades have `stop_loss = 0` and `take_profit = 0` — the production target calculator was never invoked. These are **legacy trades** (`strategy_version = None`, no session assigned).

Without SL/TP, the risk engine falls back to:
```python
risk_per_unit = max(abs(entry - sl) if sl else entry * 0.02, entry * 0.002)
```
This uses a **2% of entry fallback** — extremely loose for a 10x leveraged position. The position was held with no defined exit point and eventually hit a dynamic fallback stop.

**BCHUSDT special case:** Opened AND closed at the same second (`08:34:03`), hold = 0min. This suggests an immediate execution error or market order that got filled and stopped simultaneously.

### Fix Required
1. **Reject any signal with SL=0 or TP=0** in the position opening code
2. **Validate production targets exist** before opening position
3. **Add DB constraint** that stop_loss > 0 for all new positions

---

## ROOT CAUSE #4: ⚠️ SHORT SL Inversion Bug — $2.89 (5.5%)

### Evidence

| Symbol | Side | Entry | SL | SL Position | SL% | PnL |
|---|---|---|---|---|---|---|
| MITOUSDT | SHORT | 0.02018 | 0.01958522 | **BELOW entry** ❌ | 2.95% | -$5.76 |
| PIPPINUSDT | SHORT | 0.01873 | 0.01846937 | **BELOW entry** ❌ | 1.39% | -$2.46 |
| PIPPINUSDT | SHORT | 0.01892 | 0.018495 | **BELOW entry** ❌ | 2.25% | -$0.02 |
| TAGUSDT | SHORT | 0.001039 | 0.00094797 | **BELOW entry** ❌ | 8.76% | -$0.07 |
| TRUUSDT | SHORT | 0.00102 | 0.00095794 | **BELOW entry** ❌ | 6.08% | -$0.07 |

### Analysis

**For a SHORT position:**
- You profit when price goes **DOWN**
- You lose when price goes **UP**
- Therefore: SL must be **ABOVE** entry (stop you out if price rises)

**All 5 trades have SL placed BELOW entry** — on the wrong side. This means:
- The "stop loss" acts as a **take-profit** level
- There is **NO protection** against price moving up (the actual loss direction)
- The engine's `check_exit_conditions()` checks `price >= sl` for SHORT — with SL below entry, this fires when price **drops** (a profit move!)

**Comparison — correctly placed SHORT SLs on the same day:**
| Symbol | Entry | SL | SL Position | Result |
|---|---|---|---|---|
| USELESSUSDT | 0.0729 | 0.0734832 | ABOVE ✅ | Correct stop |
| POWERUSDT | 0.0759 | 0.0765072 | ABOVE ✅ | Correct stop |
| HAEDALUSDT | 0.0192 | 0.01936512 | ABOVE ✅ | Correct stop |
| ZEREBROUSDT | 0.0231 | 0.02329866 | ABOVE ✅ | Correct stop |

**Pattern:** The inverted SLs occur in `_place_structural_sl()` when the structural levels for SHORT have issues:
```python
# SHORT: look for resistances above entry
for price, source, confidence in resistances:
    candidate_sl = price + buffer
    dist = candidate_sl - entry
    if 0.5 * atr_sl_dist <= dist <= 2.5 * atr_sl_dist:
        return candidate_sl, ...
```

But `all_resistances` for SHORT includes POC/VAL levels that can be **below** entry:
```python
# BUG: These can be below entry!
if poc < entry:
    all_resistances.append((poc, "volume_profile_poc", 0.85))
```

When sorted by `abs(price - entry)`, a close-below-entry POC can be selected, placing SL below entry. The distance check `0.5 * atr_sl_dist <= dist` can still pass if the ATR floor is small enough.

### Fix Required
1. **Add direction validation** in `_place_structural_sl()`:
   ```python
   if direction == "SHORT":
       assert candidate_sl > entry, f"SL must be above entry for SHORT"
   ```
2. **Filter POC/VAL from resistances** for SHORT SL placement (only use for TP targeting)
3. **Add post-computation validation**: if SL is on wrong side of entry, fall back to ATR-based SL

---

## ROOT CAUSE #5: 📉 Tight Stop-Loss Hits (NY Session) — $8.21 (15.6%)

### Evidence

| Symbol | Side | Entry | SL Width | Hold | PnL | Exit |
|---|---|---|---|---|---|---|
| USELESSUSDT | SHORT | 0.0729 | 0.80% | 46min | -$0.80 | stop_loss |
| SYNUSDT | LONG | 0.04615 | 0.80% | 503min | -$0.97 | stop_loss |
| TAGUSDT | SHORT | 0.001039 | 8.76%* | 2min | -$0.07 | stop_loss |
| POWERUSDT | SHORT | 0.0759 | 0.80% | 58min | -$0.94 | stop_loss |
| TRUUSDT | SHORT | 0.00102 | 6.08%* | 1min | -$0.07 | stop_loss |
| HAEDALUSDT | SHORT | 0.0192 | 0.86% | 48min | -$0.86 | stop_loss |
| SPACEUSDT | LONG | 0.007298 | 0.80% | 40min | -$1.13 | stop_loss |
| PIPPINUSDT | SHORT | 0.01892 | 2.25%* | 0min | -$0.02 | stop_loss |
| ZEREBROUSDT | SHORT | 0.0231 | 0.86% | 11min | -$0.88 | stop_loss |

*\* These are inverted SLs (see Root Cause #4) — the actual SL distance from entry may differ*

### Analysis

All production_v2 NY session trades used the same SL width configuration:
- `_sl_mults = {"trending_bull": 1.6, "trending_bear": 1.6, ...}`
- `_min_sl_pct = 0.0015` (0.15%)

This produces a typical SL width of **0.80-0.86%** — extremely tight for a volatile NY session. NY session (13:00-21:00 UTC) sees the highest intraday volatility with BTC and alts making 1-3% swings routinely.

**Key observation:** Most stop-outs occurred within 0-60 minutes of entry. The trades didn't have time to develop before being stopped out by normal intraday noise.

**Compare with time_exit trades:** The 08:30 UTC batch (Asian/London session) held for 6+ hours because their SL was wide enough to survive the quieter session. But the NY batch with tight 0.8% SL got stopped out quickly.

### Fix Required
1. **Widen SL for NY session** — increase session multiplier: `"us": 1.4` (was 1.15)
2. **Dynamic SL based on session volatility** — measure ATR during NY vs Asian sessions
3. **Consider 1.0-1.2% minimum SL** for NY session trades (vs 0.8% current)

---

## 📊 Summary Statistics

### Loss Distribution by Exit Type

| Exit Reason | Trades | Total PnL | Avg Hold | Root Causes |
|---|---|---|---|---|
| time_exit_6h | 5 | **-$39.44** | 481min | #1 + #2 |
| stop_loss | 12 | -$9.70 | 83min | #3 + #4 + #5 |
| trailing_stop | 1 | -$2.46 | 484min | #1 (state loss) |

### Strategy Version Performance

| Version | Wins | Losses | WR | PnL | Issues |
|---|---|---|---|---|---|
| current (legacy) | 2 | 6 | 25.0% | -$25.57 | Zero-conf, inverted SL, state loss |
| production_v2 | 4 | 9 | 30.8% | -$1.60 | Tight SL, inverted SL |
| legacy (None) | 2 | 3 | 40.0% | +$0.76 | Missing SL/TP |

### Key Metrics
- **Total trades June 16:** 28 (8 wins, 18 losses, 2 breakeven)
- **Overall win rate:** 30.8%
- **Total PnL:** -$26.41
- **Largest single loss:** SPACEUSDT -$24.64 (46.9% of all losses)
- **Profit factor:** 0.49
- **Trades with anomalies:** 11 of 17 losses (64.7%)

---

## 🛠️ Priority Fixes

### P0 — Critical Bugs (Fix Immediately)

1. **SHORT SL Inversion** (`production_targets.py:_place_structural_sl`)
   - Add direction validation: `if direction == "SHORT" and candidate_sl <= entry: continue`
   - Filter POC/VAL from SHORT resistance levels used for SL placement

2. **Zero-Confidence Trade Gate** (`engine.py:_scan_symbol`)
   - Enforce `confidence >= CONFIDENCE_FLOOR` for ALL code paths, including legacy
   - Block trades with `regime == "unknown"`

3. **Missing SL/TP Validation** (`engine.py:_scan_symbol`)
   - Reject positions with `stop_loss == 0` or `take_profit == 0`

### P1 — State Management (Fix This Week)

4. **Trailing Stop State Persistence** (`risk_engine.py`)
   - Persist `_highest_pnl` to DB on every position update (not just shutdown)
   - Reconstruct peak from kline data on engine restart

5. **MFE/MAE Recording Bug** (`trade_engine.py`)
   - Fix identical MFE=MAE values — investigate `LivePosition` tracking

### P2 — Configuration (Fix This Week)

6. **NY Session SL Width** (`production_targets.py`)
   - Increase `_session_tp_mult["us"]` from 1.15 to 1.4
   - Add `_session_sl_mult` for session-specific SL widening
   - Consider dynamic SL based on recent ATR percentile

---

*Report generated: June 17, 2026*
*Engine version: production_v2*
*Database: institutional_v1.db*
