# Forward-Test Success Criteria — EMA V5 ONLY

**Date:** 2026-07-26  
**Scope:** EMA V5 strategy only (`strategy_version = 'ema_v5'`)  
**Status:** Data collection in progress  
**Objective:** Determine whether current rejection rules create a durable trading edge for EMA V5

> ⚠️ **Scope Note:** This document applies exclusively to EMA V5 trades. Other strategy versions (`production_v2`, `current`, `forward_test_v1`) are excluded from these criteria.

---

## Current Data Status — EMA V5 Only

### EMA V5 Completed Trades

| Source | Trades | Win Rate | PnL |
|--------|--------|----------|-----|
| Live (`positions`) | 10 | 30.0% | +$4.74 |
| Archive (`positions_archive`) | 37 | 37.8% | -$31.62 |
| **Total EMA V5** | **47** | **35.3%** | **-$26.88** |

### All Strategy Versions (for reference — NOT in scope)

| Strategy Version | Trades | Win Rate | PnL | In Scope? |
|------------------|--------|----------|-----|-----------|
| `ema_v5` | 47 | 35.3% | -$26.88 | ✅ YES |
| `production_v2` | 93 | 29.0% | -$241.51 | ❌ NO |
| `current` | 15 | 20.0% | -$53.94 | ❌ NO |
| `forward_test_v1` | 467 | 0.0% | -$32.22 | ❌ NO (simulated) |

### EMA V5 Regime Distribution

| Regime | Trades | Win Rate | PnL |
|--------|--------|----------|-----|
| trending_bull | ~18 | ~33% | TBD |
| trending_bear | ~14 | ~36% | TBD |
| SELL_MODE | ~10 | ~40% | TBD |
| BUY_MODE | ~3 | ~33% | TBD |
| range | ~2 | 0% | TBD |

### Time Range

- **First EMA V5 trade:** ~June 26, 2026
- **Last EMA V5 trade:** ~July 26, 2026
- **Duration:** ~30 days

---

## Predefined Success Criteria — EMA V5 Only

> **Evaluation is by statistical coverage, not raw trade count.**
> 
> 200 trades collected entirely during one volatility regime or one market trend
> can still produce misleading conclusions. EMA V5 must be validated across
> **diverse market conditions** before any rule is judged.

---

### Criterion 1: Market Condition Coverage (Primary)

Evaluation requires **sufficient EMA V5 samples across every major market condition**:

| Market Condition | Minimum Required | EMA V5 Current | Status |
|------------------|------------------|----------------|--------|
| Trending bull | ≥ 25 trades | ~18 | ❌ Insufficient |
| Trending bear | ≥ 25 trades | ~14 | ❌ Insufficient |
| Ranging / sideways | ≥ 20 trades | ~2 | ❌ Insufficient |
| High volatility | ≥ 15 trades | Unknown | ❓ Not tracked |
| Low volatility | ≥ 15 trades | Unknown | ❓ Not tracked |
| Bull market period | ≥ 20 trades | ~21 | ⚠️ Borderline |
| Bear market period | ≥ 20 trades | ~14 | ❌ Insufficient |

**Why this matters:** A rule that appears too strict in one environment may prove valuable in another. Evaluation without regime diversity risks eliminating rules that protect during underrepresented conditions.

**Recommendation:** EMA V5 must trade through **at least 20 ranging market periods** before evaluating the Cooldown rule's effectiveness.

---

### Criterion 2: Sample Size (Secondary to Coverage)

> Trade count alone is not sufficient. 200 trades in a single regime is NOT enough — regime diversity (Criterion 1) takes precedence. This criterion provides a baseline confidence floor.

| Threshold | Confidence Level | EMA V5 Status |
|-----------|------------------|---------------|
| ≥ 50 trades | Low confidence | ⏳ 47 (94%) |
| ≥ 100 trades | Moderate confidence | ❌ 47 (47%) |
| ≥ 200 trades | High confidence | ❌ 47 (23.5%) |
| ≥ 300 trades | Very high confidence | ❌ 47 (15.7%) |

**Recommendation:** Continue collecting EMA V5 trades. The target is sufficient coverage across all market conditions, with 200+ trades as a secondary sanity check.

---

### Criterion 3: Time-Based Minimum

| Duration | Requirement | EMA V5 Status |
|----------|-------------|---------------|
| Calendar days | ≥ 45 days | ⏳ ~30 days (67%) |
| Trading sessions | ≥ 100 EMA V5 sessions | ❌ ~47 sessions |
| Market conditions | Multiple regime transitions | ⚠️ Limited range data |

**Recommendation:** Continue for at least **15 more days** to reach 45-day minimum for EMA V5.

---

### Criterion 4: Rejection Rule Evaluation — Key Evidence Gap

> **Rejection classification is implemented. What's missing is enough forward-test outcome data to evaluate the incremental contribution of each rejection rule to overall trading performance.**

The software can record rejection reasons. But statistical evaluation requires sufficient completed trades where the rejection decision can be measured against actual outcomes.

Each rejection rule needs enough "rejected" EMA V5 samples to measure impact:

| Rule | Minimum Rejected Samples | Evaluation Method | Classification Status | Outcome Data |
|------|--------------------------|-------------------|----------------------|--------------|
| Volume filter | ≥ 50 rejected EMA V5 trades | Compare PF: accepted vs rejected | ✅ Implemented | ❌ Insufficient |
| RR rule | ≥ 30 rejected EMA V5 trades | Compare expectancy: accepted vs rejected | ✅ Implemented | ❌ Insufficient |
| Cooldown | ≥ 20 rejected EMA V5 trades | Compare drawdown: with vs without | ✅ Implemented | ❌ Insufficient |
| Session filter | ≥ 15 rejected EMA V5 trades | Compare performance across sessions | ✅ Implemented | ❌ Insufficient |

**What's in place:** Rejection classification, gate logic, reason assignment, lifecycle instrumentation.

**What's needed:** More completed EMA V5 forward-tested trades across diverse market conditions so each rejection category has enough observations for reliable statistical evaluation.

---

## Decision Framework — EMA V5 Only

### When to Draw Conclusions

**Do NOT change any EMA V5 rule if:**
- Market condition coverage is insufficient (any regime < 15 EMA V5 trades)
- Less than 45 days of EMA V5 data
- Market has been exclusively trending (no range data)
- Rejection classification lacks enough outcome data per rule

**CAN evaluate individual EMA V5 rules if:**
- Every market condition has ≥ 15 EMA V5 trades
- Total EMA V5 trades ≥ 200
- ≥ 45 days of EMA V5 data
- Rejection samples ≥ 30 per rule
- Data spans at least 2 bull periods and 2 bear periods

### Decision Criteria for Each EMA V5 Rule

For each rejection rule (Volume, RR, Cooldown, Session):

| Outcome | Action |
|---------|--------|
| Rule improves Profit Factor by ≥ 0.2 | **Retain** — rule adds edge |
| Rule improves Profit Factor by 0.05-0.2 | **Review** — marginal benefit, consider relaxing |
| Rule has no measurable impact | **Consider removing** — adds complexity without benefit |
| Rule reduces Profit Factor | **Remove** — rule destroys value |

---

## What Needs to Happen Now — EMA V5 Only

### Priority #1: Accumulate Rejection-Outcome Data

Rejection classification is implemented. What's needed is more completed EMA V5 forward-tested trades so each rejection category has enough observations for reliable statistical evaluation.

### Priority #2: Continue EMA V5 Data Collection

- Let EMA V5 run untouched
- Target: sufficient completed outcomes across **all market conditions**
- Monitor for **range regime** EMA V5 trades (currently only ~2)
- 200+ trades is a secondary benchmark, not the primary criterion

### Priority #3: Periodic Checkpoints (EMA V5 Only)

Run evaluation at these milestones:
- **50 EMA V5 trades** — Preliminary observation (no changes)
- **100 EMA V5 trades** — First formal evaluation
- **150 EMA V5 trades** — Confidence interval tightening
- **200 EMA V5 trades** — High-confidence evaluation

Each checkpoint must verify **regime coverage**, not just trade count. 200 trades concentrated in one market condition is not sufficient.

---

## Red Flags to Watch For — EMA V5

| Symptom | Possible Cause | Action |
|---------|----------------|--------|
| EMA V5 win rate drops below 20% | Market regime shift or rule degradation | Investigate, do not change rules yet |
| No EMA V5 trades for 72+ hours | Overly restrictive filters | Review EMA V5 rejection rates |
| Single regime dominates (>70%) | Market condition bias | Wait for regime rotation |
| EMA V5 PnL deterioration accelerates | Systematic issue | Full forensic audit |

---

## Project Maturity Assessment — EMA V5

| Area | Status |
|------|--------|
| Software engineering | ✅ Complete |
| Runtime reliability | ✅ High |
| Scanner architecture | ✅ Production-grade |
| State machine | ✅ Verified |
| Pipeline correctness | ✅ Verified |
| Observability | ✅ Excellent |
| Forward-test infrastructure | ✅ Ready |
| Calibration framework | ✅ Defined |
| Statistical evidence | 🟡 Growing |
| Strategy edge | ❓ Undetermined |

> "Production-grade" describes the software architecture, not the strategy's profitability.
> Strategy performance is under validation.

---

## Summary — EMA V5 Only

**Current State:** 47 EMA V5 trades collected — insufficient statistical coverage across market regimes

**Pipeline Observation:** Combined selectivity of regime + pullback + candle + volume filtered every observed opportunity during this forward-test period (~400 hours, ~462 perpetual futures). This is an empirical observation limited to the observed market conditions — it cannot be generalized beyond the current observation window.

**Key Distinction:** Rejection classification is implemented. The gap is insufficient outcome data across diverse market conditions to statistically evaluate the incremental contribution of each rejection rule to overall trading performance.

**Regime Coverage:** Trending bull/bear adequate, ranging market severely underrepresented

**Next Milestone:** 200 completed EMA V5 trades with full regime coverage

**Estimated Time to Evaluation:** ~100 days (at ~1.5 EMA V5 trades/day)

**Critical Constraint:** Do NOT change any EMA V5 strategy rule until sufficient statistical coverage is achieved across all market conditions. Engineering defects (logging, crashes, DB issues) can still be fixed.

---

## Calibration Trigger Criteria — EMA V5

The following are operational review triggers for EMA V5 (heuristics, not statistically derived limits):

| Trigger | Condition | Action |
|---------|-----------|--------|
| **T1** | 300+ hours with zero executable signals | Review selectivity of all gates |
| **T2** | 50+ regime-qualified symbols with zero executions | Review gate contribution analysis |
| **T3** | Any gate rejecting >95% of candidates for extended period | Investigate whether gate adapts to market regime |
| **T4** | Before changing any threshold | Compare accepted vs rejected candidates using Profit Factor, Expectancy, Drawdown, Risk-Adjusted Return |

These criteria convert diagnosis into a repeatable operational policy for EMA V5.

---

## Missing Metric: Opportunity Cost

The current analysis measures **how many** candidates were rejected at each stage. It does not yet measure **whether rejecting them was beneficial**.

For every rejected candidate, the following metrics are needed:

| Metric | Description |
|--------|-------------|
| **Take Profit Hit** | Did the candidate later hit what would have been TP? |
| **Stop Loss Hit** | Did the candidate later hit what would have been SL? |
| **Maximum Favorable Excursion (MFE)** | How far did price move in the favorable direction? |
| **Maximum Adverse Excursion (MAE)** | How far did price move against the entry? |
| **Risk-Adjusted Expectancy** | What is the expected return per unit of risk? |

Without these metrics, pipeline counts alone cannot determine whether the current selectivity improves or degrades trading performance. This is the final research layer.

---

## Interaction Analysis

Some filters may have little standalone value but become useful when combined:

| Combination | Question |
|-------------|----------|
| High confidence + high volume | Better than confidence alone? |
| Strong trend + bullish pin bar | Better than either individually? |
| Pullback + volume expansion | Does the combination matter? |
| Bull regime + high confidence | Regime-specific calibration? |

---

*This document defines objective criteria for evaluating the EMA V5 rejection rules. Evaluation is by statistical coverage across market conditions. The engineering investigation is complete. The quantitative validation phase is underway. All future EMA V5 strategy decisions must be based on measured outcomes from this forward-test period. Other strategy versions are excluded.*
