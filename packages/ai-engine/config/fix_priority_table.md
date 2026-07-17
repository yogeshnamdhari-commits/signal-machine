# FIX PRIORITY TABLE — Phase 1 (14 Fixes) + Phase 2 (7 Fixes) + Phase 3 (v3.0 Gates)
# Date: 2026-06-24

## PHASE 3 — v3.0 Signal Generation Prompt (11 gates)

| Gate | Severity | Files Changed | Expected Impact |
|------|----------|---------------|-----------------|
| 0. System state (halt check) | 🔴 CRITICAL | `core/regime_state.py` | Circuit breaker persistence |
| 1. Session filter (UTC hours) | 🔴 CRITICAL | `scanner/session_filter.py` | Blocks dead zones |
| 2. Regime classification | 🔴 CRITICAL | `scanner/regime_filter.py` | Trend/range/volatile gating |
| 3. Daily signal budget | 🟠 HIGH | `scanner/session_filter.py` | Max 40/day, 8/hour |
| 4. Symbol blacklist | 🟠 HIGH | `scanner/symbol_expectancy_tracker.py` | Auto-blacklist losers |
| 5. MTF Confluence (4H+1H+15m) | 🟠 HIGH | `core/engine.py` | ≥2/3 TF agreement required |
| 6. CVD absorption/distribution | 🔴 CRITICAL | `core/engine.py` | Full fail = hard reject |
| 7. Entry type (LIMIT only) | 🟡 MEDIUM | `core/engine.py` | Structural level entries |
| 8. SL calculation (ATR+struct) | 🟠 HIGH | `scanner/production_targets.py` | Beyond structure + noise |
| 9. Triple TP mandatory | 🔴 CRITICAL | `core/engine.py` | All 3 TPs > 0 required |
| 10. Quality score (5-category) | 🟠 HIGH | `core/engine.py` | Regime+Confluence+Entry+CVD+SL |
| 11. Position sizing (1% risk) | 🟠 HIGH | `execution/risk_engine.py` | Session×Regime×Conf multipliers |

## PHASE 1 — Signal Quality (14 fixes)

| Fix | Severity | Lines Changed | Expected Impact |
|-----|----------|---------------|-----------------|
| 1. qty=0 ghost trade guard | 🔴 CRITICAL | ~1 line | Eliminates 50% wasted entries |
| 2. Store TP2/TP3 in position dict | 🔴 CRITICAL | ~2 lines | Activates 3R/5R profit targets |
| 3. TP1 = partial close, not full | 🔴 CRITICAL | ~5 lines | Locks 80% ride to TP2/TP3 |
| 4. Enter on pullback, not momentum | 🔴 CRITICAL | Logic change | Fixes LONG bias (-$30 all-time) |
| 5. ATR-based SL minimum | 🔴 CRITICAL | ~10 lines | Stops noise-out losses |
| 6. CVD adjustment applied once | 🟠 HIGH | Delete block | Fixes unintended SL compression |
| 7. Breakeven after TP1 only | 🟠 HIGH | ~5 lines | Stops premature SL moves |
| 8. Regime + directional filter | 🟠 HIGH | New module | Eliminates wrong-direction trades |
| 9. Archive metadata on every trade | 🟡 MEDIUM | ~15 lines | Enables post-mortem & tuning |
| 10. Pass TP2/TP3 to DB | 🟡 MEDIUM | ~2 lines | Survives restarts |
| 11. Unify PnL formula | 🟡 MEDIUM | ~5 lines | Accurate diagnostics |
| 12. Symbol blacklist | 🟢 LOW | New module | Stops repeated losers |
| 13. Tiered cooldown | 🟢 LOW | ~5 lines | Proportional recovery time |
| 14. Time-based exit | 🟢 LOW | ~10 lines | Releases stuck capital |

## PHASE 2 — Regime Continuity & Session Control (7 fixes)

| Fix | Severity | Files Changed | Expected Impact |
|-----|----------|---------------|-----------------|
| A. Regime halt persistence | 🔴 CRITICAL | `core/regime_state.py` (new) | Prevents resume into same bad market |
| B. TP values reaching DB | 🔴 CRITICAL | `core/engine.py`, `database/signal_repository.py` | TP2/TP3 always non-zero in archive |
| C. Market session filter | 🔴 CRITICAL | `scanner/session_filter.py` (new) | Blocks 00:00-07:00 UTC dead zone |
| D. Unknown regime hard block | 🟠 HIGH | `core/engine.py` | Unknown/volatile → NO signals |
| E. Daily signal budget | 🟠 HIGH | `scanner/session_filter.py` | Max 40/day, 8/hour, quality floor escalation |
| F. Archive confidence fix | 🟡 MEDIUM | `database/signal_repository.py` | Confidence preserved in archive |
| G. Regime halt on trade close | 🟡 MEDIUM | `core/engine.py` | Evaluates halt after every trade |

---

## FILES MODIFIED (9 total)

| File | Phase 1 | Phase 2 |
|------|---------|---------|
| `core/engine.py` | 1, 2, 3, 4, 5, 8, 9, 12, 13 | A, B, D, E, G |
| `scanner/production_targets.py` | 5, 6 | — |
| `execution/risk_engine.py` | 7, 8, 11, 13, 14 | — |
| `database/signal_repository.py` | 3, 9 | B, F |
| `scanner/liquidity_sweep_engine.py` | 4 | — |
| `engines/trade_engine.py` | 11 | — |
| `scanner/symbol_expectancy_tracker.py` | 12 | — |
| `core/regime_state.py` | — | A (NEW) |
| `scanner/session_filter.py` | — | C, E (NEW) |

## CONFIG FILES (3)

| File | Purpose |
|------|---------|
| `config/signal_generation_prompt.md` | Signal generation prompt v2.0 |
| `config/monitoring_prompt.md` | Monitoring rules v2.0 |
| `config/fix_priority_table.md` | This file — Phase 1+2 summary |

---

## EXPECTED RESULTS AFTER PHASE 2

| Metric | Phase 1 Result | Phase 2 Target | How |
|--------|---------------|----------------|-----|
| Win Rate | 35.9% | 42–46% | Session filter + regime halt |
| Profit Factor | 0.82 | 1.5–1.8 | TP2/TP3 activating + halt on bad days |
| Daily WR floor | 22% (Jun 18) | ≥ 35% | Regime continuity halt |
| Signals/day | 53 | 25–35 | Budget cap + session block |
| TP2 hit rate | ~0% | 25–35% | DB fix + proper partial close |
| Unknown regime trades | Many | 0 | Hardened regime block |
