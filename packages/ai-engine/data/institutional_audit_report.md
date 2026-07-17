# INSTITUTIONAL PRODUCTION READINESS AUDIT
# Generated: 2026-06-11 21:38:18 UTC
# Database: /Users/targetmobile/Documents/signal machine/packages/ai-engine/data/institutional_v1.db

================================================================================
PHASE 1 — DATA INTEGRITY AUDIT
================================================================================
Signals table: 216682 rows
Positions table: 1437 rows
Closed trades: 1436

| Field | Records | Non-Zero % | Null % | Sample Value | First Value | Latest Value |
|-------|---------|-----------|--------|-------------|-------------|-------------|
| confidence | 1436 | 100.0% | 0.0% | 0.5339 | 0.4672020537949632 | 0.5339 |
| institutional_score | 1436 | 100.0% | 0.0% | 49.5000 | 46.72020537949632 | 49.5 |
| regime | 1436 | 100.0% | 0.0% | trending_bear | ranging | trending_bear |
| session | 1436 | 100.0% | 0.0% | new_york | asia | new_york |
| risk_reward | 1436 | 94.3% | 0.0% | 1.8000 | 0.0 | 1.8 |
| hold_minutes | 1436 | 92.8% | 0.0% | 360.0000 | 446.9 | 360.0 |
| mae_pct | 1436 | 0.0% | 0.0% | 0.0000 | 0.0 | 0.0 |
| mfe_pct | 1436 | 0.0% | 0.0% | 0.0000 | 0.0 | 0.0 |
| exit_reason | 1436 | 0.0% | 0.0% | unknown | unknown | unknown |
| outcome | 1436 | 100.0% | 0.0% | pending | pending | pending |
| mss_score | 1436 | 0.0% | 100.0% | None | None | None |
| fvg_score | 1436 | 0.0% | 100.0% | None | None | None |
| entry_reason | 1436 | 0.0% | 100.0% | None | None | None |
| realized_r | 1436 | 0.0% | 0.0% | 0.0000 | 0.0 | 0.0 |
| planned_rr | 1436 | 0.0% | 0.0% | 0.0000 | 0.0 | 0.0 |
| volatility_score | 1436 | 0.0% | 0.0% | 0.0000 | 0.0 | 0.0 |

**DATA INTEGRITY SCORE: 42.9/100**

================================================================================
PHASE 2 — SIGNAL PIPELINE TRACE
================================================================================
Latest 250 signals analyzed

| Gate | Count | % |
|------|-------|---|
| Total Scanned (latest 250) | 250 | 100% |
| Has Institutional Score | 250 | 100.0% |
| Has Confidence | 250 | 100.0% |
| Has Regime | 250 | 100.0% |
| Has Sweep Score | 0 | 0.0% |
| Has MSS Score | 0 | 0.0% |
| Has FVG Score | 0 | 0.0% |
| Has Entry Reason | 0 | 0.0% |
| Has Risk/Reward | 250 | 100.0% |

| Status | Count |
|--------|-------|
| active | 232 |
| expired | 18 |

================================================================================
PHASE 3 — PHASE1 VALIDATION
================================================================================
| Bucket | Count | % | Pass (≥50) |
|--------|-------|---|-----------|
| 0-40 | 0 | 0.0% | ❌ |
| 40-50 | 0 | 0.0% | ❌ |
| 50-55 | 162 | 64.8% | ✅ |
| 55-60 | 70 | 28.0% | ✅ |
| 60-70 | 0 | 0.0% | ✅ |
| 70-80 | 5 | 2.0% | ✅ |
| 80-101 | 13 | 5.2% | ✅ |

**Phase1 threshold: 50**
**Pass: 250/250 (100.0%)**
**Reject: 0/250 (0.0%)**

Mean score: 56.5
Median score: 54.42
Min: 50.0, Max: 100.0

**Is Phase1 behaving correctly? YES** — Scores are normally distributed around 55-65

================================================================================
PHASE 4 — CONFIDENCE ANALYSIS
================================================================================
Total closed trades: 1436

| Bucket | Trades | WR% | PF | Expectancy | Net PnL |
|--------|--------|-----|----|-----------|---------|
| 0.50-0.55 | 307 | 36.5% | 0.84 | $-5.45 | $-1,674.09 |
| 0.55-0.60 | 548 | 33.2% | 1.18 | $3.07 | $1,684.78 |
| 0.60-0.65 | 170 | 37.1% | 0.6 | $-5.53 | $-940.88 |
| 0.65-0.70 | 65 | 38.5% | 1.06 | $0.77 | $49.73 |
| 0.70-0.75 | 9 | 44.4% | 2.22 | $12.86 | $115.72 |
| 0.75-1.01 | 1 | 100.0% | 99.9 | $176.60 | $176.60 |

**Is confidence predictive? YES** — Higher confidence → better expectancy (monotonic)
Monotonicity score: 80%

================================================================================
PHASE 5 — REGIME ANALYSIS
================================================================================
| Regime | Trades | WR% | PF | Expectancy | Net PnL | Rank |
|--------|--------|-----|----|-----------|---------|------|
| breakout | 138 | 38.4% | 4.82 | $44.41 | $6,128.19 | 🥇 |
| reversal | 366 | 35.0% | 0.98 | $-0.58 | $-212.85 | 🥈 |
| trending_bear | 156 | 36.5% | 0.8 | $-1.81 | $-283.01 | 🥉 |
| trending_bull | 181 | 31.5% | 0.66 | $-6.55 | $-1,185.07 | #4 |
| range | 317 | 35.0% | 0.53 | $-6.10 | $-1,934.17 | #5 |
| ranging | 37 | 43.2% | 0.63 | $-54.38 | $-2,012.08 | #6 |
| quiet | 241 | 38.2% | 0.3 | $-28.92 | $-6,970.03 | #7 |

**Best regime: breakout** (PF=4.82, PnL=$6,128.19)
**Worst regime: quiet** (PF=0.3, PnL=$-6,970.03)
**Dollar impact of worst regime: -$6,970.03**

================================================================================
PHASE 6 — SESSION ANALYSIS
================================================================================
| Session | Trades | WR% | PF | Expectancy | Net PnL | Verdict |
|---------|--------|-----|----|-----------|---------|---------|
| london | 296 | 36.1% | 1.4 | $7.66 | $2,266.93 | 💰 MAKES MONEY |
| new_york | 832 | 35.8% | 0.83 | $-4.64 | $-3,856.86 | 🔴 LOSES MONEY |
| asia | 228 | 40.8% | 0.54 | $-7.86 | $-1,791.47 | 🔴 LOSES MONEY |
| off_hours | 80 | 20.0% | 0.14 | $-38.60 | $-3,087.62 | 🔴 LOSES MONEY |

**Sessions making money: london**
**Sessions losing money: asia, new_york, off_hours**

================================================================================
PHASE 7 — HOLDING TIME ANALYSIS
================================================================================
| Window | Trades | WR% | PF | Expectancy | Net PnL | Verdict |
|--------|--------|-----|----|-----------|---------|---------|
| 0-15m | 720 | 26.4% | 0.4 | $-12.66 | $-9,118.27 | ❌ |
| 15-30m | 198 | 45.5% | 0.65 | $-8.40 | $-1,663.94 | ❌ |
| 30-60m | 200 | 45.5% | 2.6 | $26.64 | $5,328.82 | ✅ |
| 1-2h | 136 | 44.1% | 2.03 | $27.71 | $3,768.20 | ✅ |
| 2-4h | 68 | 36.8% | 0.38 | $-43.47 | $-2,956.23 | ❌ |
| 4h+ | 114 | 50.9% | 0.56 | $-16.03 | $-1,827.60 | ❌ |
| NO DATA | 104 | 22.1% | 0.56 | $-5.84 | $-607.77 | ⚠️ |

**Best hold window: 30-60m** (PF=2.6, PnL=$5,328.82)
**Worst hold window: 0-15m** (PF=0.4, PnL=$-9,118.27)
**Dollar impact: Best=$5,328.82, Worst=$-9,118.27**

================================================================================
PHASE 8 — MSS / SWEEP / FVG VALIDATION
================================================================================
Total trades: 1436
MSS present: 0, absent: 1436
Sweep present: 0, absent: 1436
FVG present: 0, absent: 1436

| Factor | Present | Absent | Verdict |
|--------|---------|--------|---------|
| MSS | 0 trades | 1436 trades, PF=0.82, $-6,469.02 | ABSENT ONLY |
| Sweep | 0 trades | 1436 trades, PF=0.82, $-6,469.02 | ABSENT ONLY |
| FVG | 0 trades | 1436 trades, PF=0.82, $-6,469.02 | ABSENT ONLY |

**Do MSS improve performance?** CANNOT VALIDATE — 100% zeros in positions table
**Do Sweeps improve performance?** CANNOT VALIDATE — no sweep entry_reason found
**Do FVGs improve performance?** CANNOT VALIDATE — 100% zeros in positions table

**NOTE**: MSS/Sweep/FVG scores are only populated for NEW signals generated AFTER persistence fixes.
Historical trades predate the persistence layer. Future signals will have non-zero values.

================================================================================
PHASE 9 — RR ANALYSIS
================================================================================
| RR Bucket | Trades | WR% | PF | Expectancy | Net PnL | Verdict |
|-----------|--------|-----|----|-----------|---------|---------|
| < 2.0 | 489 | 37.2% | 0.7 | $-11.99 | $-5,861.51 | ❌ |
| 2.0-2.5 | 289 | 36.7% | 1.3 | $6.95 | $2,008.43 | ✅ |
| 2.5-3.0 | 102 | 29.4% | 0.84 | $-2.54 | $-259.09 | ❌ |
| 3.0-5.0 | 255 | 32.9% | 0.66 | $-6.34 | $-1,617.10 | ❌ |
| 5.0+ | 301 | 37.2% | 0.77 | $-2.46 | $-739.75 | ❌ |

**Best RR bucket: 2.0-2.5** (PF=1.3, PnL=$2,008.43)
**Worst RR bucket: < 2.0** (PF=0.7, PnL=$-5,861.51)

================================================================================
PHASE 10 — EXIT ANALYSIS
================================================================================
| Exit Reason | Count | WR% | PF | Expectancy | Net PnL | Verdict |
|-------------|-------|-----|----|-----------|---------|---------|
| unknown | 1436 | 35.8% | 0.82 | $-4.50 | $-6,469.02 | ❌ |

MAE data available: 0/1436 trades
MFE data available: 0/1436 trades
**Premature exits (<15min): 616 trades, PF=0.38, PnL=$-8,510.50**
**Held too long (>4h): 114 trades, PF=0.56, PnL=$-1,827.60**

**Largest exit problem: unknown** (PnL=$-6,469.02, 1436 trades)
**Dollar impact: -$6,469.02**

================================================================================
PHASE 11 — SYMBOL EXPECTANCY
================================================================================
## Top 20 Profitable Symbols
| Symbol | Trades | WR% | PF | Expectancy | PnL | Recommend |
|--------|--------|-----|----|-----------|-----|-----------|
| APRUSDT | 33 | 51.5% | 14.79 | $121.90 | $4,022.56 | PROMOTE |
| PLAYUSDT | 13 | 92.3% | 13.17 | $273.43 | $3,554.55 | PROMOTE |
| 币安人生USDT | 31 | 61.3% | 8.97 | $48.97 | $1,518.14 | PROMOTE |
| UBUSDT | 6 | 33.3% | 9.28 | $155.05 | $930.32 | PROMOTE |
| BCHUSDT | 46 | 50.0% | 1.55 | $6.16 | $283.48 | PROMOTE |
| SUSHIUSDT | 1 | 100.0% | 99.9 | $246.35 | $246.35 | PROMOTE |
| BABYUSDT | 52 | 40.4% | 1.44 | $4.62 | $240.07 | PROMOTE |
| RAVEUSDT | 2 | 50.0% | 42.32 | $113.84 | $227.68 | PROMOTE |
| USUSDT | 23 | 65.2% | 1.26 | $9.50 | $218.61 | PROMOTE |
| BTCUSDT | 21 | 66.7% | 2.66 | $8.98 | $188.57 | PROMOTE |
| PARTIUSDT | 3 | 33.3% | 5.79 | $62.22 | $186.66 | PROMOTE |
| SIRENUSDT | 17 | 41.2% | 1.74 | $10.59 | $180.10 | PROMOTE |
| HOMEUSDT | 32 | 46.9% | 1.31 | $5.34 | $170.99 | PROMOTE |
| HIGHUSDT | 10 | 70.0% | 3.21 | $17.09 | $170.95 | PROMOTE |
| CHILLGUYUSDT | 3 | 66.7% | 15.09 | $52.08 | $156.24 | PROMOTE |
| BLESSUSDT | 1 | 100.0% | 99.9 | $149.42 | $149.42 | PROMOTE |
| AGTUSDT | 10 | 40.0% | 37.25 | $14.57 | $145.71 | PROMOTE |
| TONUSDT | 2 | 100.0% | 99.9 | $67.71 | $135.43 | PROMOTE |
| VELVETUSDT | 9 | 66.7% | 3.05 | $14.79 | $133.07 | PROMOTE |
| USELESSUSDT | 8 | 62.5% | 2.65 | $12.45 | $99.62 | PROMOTE |

## Top 20 Losing Symbols
| Symbol | Trades | WR% | PF | Expectancy | PnL | Recommend |
|--------|--------|-----|----|-----------|-----|-----------|
| HYPEUSDT | 9 | 11.1% | 0.08 | $-38.59 | $-347.31 | WATCHLIST |
| ZECUSDT | 25 | 32.0% | 0.26 | $-15.48 | $-387.02 | BLACKLIST |
| LDOUSDT | 18 | 27.8% | 0.22 | $-21.76 | $-391.67 | BLACKLIST |
| MBOXUSDT | 13 | 69.2% | 0.44 | $-30.40 | $-395.16 | BLACKLIST |
| INUSDT | 14 | 0.0% | 0.0 | $-29.22 | $-409.03 | BLACKLIST |
| PHBUSDT | 4 | 0.0% | 0.0 | $-103.56 | $-414.23 | WATCHLIST |
| ASRUSDT | 3 | 0.0% | 0.0 | $-160.40 | $-481.21 | WATCHLIST |
| MAGMAUSDT | 3 | 0.0% | 0.0 | $-194.95 | $-584.85 | WATCHLIST |
| WLDUSDT | 30 | 30.0% | 0.73 | $-19.60 | $-587.93 | BLACKLIST |
| ARUSDT | 12 | 0.0% | 0.0 | $-55.99 | $-671.91 | BLACKLIST |
| GUAUSDT | 21 | 28.6% | 0.25 | $-34.92 | $-733.33 | BLACKLIST |
| MYXUSDT | 20 | 15.0% | 0.09 | $-39.49 | $-789.74 | BLACKLIST |
| GRASSUSDT | 7 | 0.0% | 0.0 | $-113.48 | $-794.37 | WATCHLIST |
| TSTUSDT | 9 | 11.1% | 0.04 | $-90.24 | $-812.13 | WATCHLIST |
| STOUSDT | 5 | 20.0% | 0.47 | $-166.04 | $-830.22 | WATCHLIST |
| PORTALUSDT | 19 | 26.3% | 0.23 | $-48.55 | $-922.48 | BLACKLIST |
| DOGEUSDT | 16 | 12.5% | 0.02 | $-60.22 | $-963.48 | BLACKLIST |
| LABUSDT | 42 | 38.1% | 0.7 | $-25.62 | $-1,076.03 | BLACKLIST |
| ENAUSDT | 20 | 25.0% | 0.13 | $-78.69 | $-1,573.82 | BLACKLIST |
| AIAUSDT | 37 | 43.2% | 0.05 | $-65.54 | $-2,424.88 | BLACKLIST |

**Recommend BLACKLIST: 32 symbols**
**Recommend PROMOTE: 16 symbols**

================================================================================
PHASE 12 — SIGNAL QUALITY SCORECARD
================================================================================
| Dimension | Score (0-10) | Evidence |
|-----------|-------------|----------|
| Confidence Quality | 8.0/10 | Monotonicity: 80% |
| Regime Quality | 1.4/10 | 1/7 regimes profitable |
| Entry Timing | 3.6/10 | WR: 35.8% |
| RR Quality | 2.0/10 | 1/5 RR buckets profitable |
| Exit Quality | 0.0/10 | 0/1 exit types profitable |
| Sweep Quality | 5.0/10 | Cannot validate (100% zeros) |
| MSS Quality | 5.0/10 | Cannot validate (100% zeros) |
| FVG Quality | 5.0/10 | Cannot validate (100% zeros) |
| Order Flow Quality | 8.2/10 | Overall PF: 0.82 |

**Overall Signal Quality Score: 4.2/10**

================================================================================
PHASE 13 — ROOT CAUSE RANKING
================================================================================
| Rank | Root Cause | Loss $ | Trade Count | PF | Impact |
|------|-----------|--------|-------------|-----|--------|
| 1 | Wrong Regime (range/quiet/ranging) | $-10,916.28 | 595 | 0.44 | 🔴 CRITICAL |
| 2 | Quiet/Range Market Trades | $-8,904.20 | 558 | 0.36 | 🔴 CRITICAL |
| 3 | Premature Exits (<15 min) | $-8,510.50 | 616 | 0.38 | 🔴 CRITICAL |
| 4 | Unclassified Exits | $-6,469.02 | 1436 | 0.82 | 🔴 CRITICAL |
| 5 | Low RR (<2.0) | $-5,403.97 | 407 | 0.71 | 🔴 CRITICAL |
| 6 | Off Hours Trading (Asia/Off) | $-4,879.09 | 308 | 0.35 | 🔴 CRITICAL |

================================================================================
PHASE 14 — DEPLOYMENT VERDICT
================================================================================
**Overall: 1436 trades, WR=35.8%, PF=0.82, Expectancy=$-4.50, PnL=$-6,469.02**

**Q1: Is calibration broken?**
  Calibration formula: raw→calibrated mapping
  Average confidence of closed trades: 0.546
  Verdict: **NO** — Calibration formula is correct, scores are properly distributed

**Q2: Is confidence predictive?**
  Monotonicity: 80%
  Verdict: **YES** — Higher confidence consistently produces better trades

**Q3: Are MSS profitable?**
  Verdict: **CANNOT VALIDATE** — 100% zeros in historical data. Persistence fixes applied for new signals.

**Q4: Are sweeps profitable?**
  Verdict: **CANNOT VALIDATE** — No sweep entry_reason found in historical data.

**Q5: Are FVGs profitable?**
  Verdict: **CANNOT VALIDATE** — 100% zeros in historical data.

**Q6: Is regime engine profitable?**
  Trending trades: 337, PnL=$-1,468.08
  Breakout trades: 138, PnL=$6,128.19
  Verdict: **YES** — Trending + breakout profitable combined

**Q7: Biggest losing factor?**
  Verdict: **Wrong Regime (range/quiet/ranging)** — Loss: $-10,916.28 (595 trades)

**Q8: Highest profit opportunity?**
  Verdict: **Hold 30-120 min window** — PF=2.6, PnL=$5,328.82

**Q9: Is system production ready?**
  Verdict: **DO NOT DEPLOY** — PF=0.82, WR=35.8% (losing system)

**Q10: What single change gives highest impact?**
  **Enforce minimum 30-min hold** — Remove 616 premature exits
  Projected PnL improvement: $5,861.25
  Projected PF: 0.56

================================================================================
FINAL OUTPUT
================================================================================

## 1. Executive Summary
The production system has 1436 closed trades with a total PnL of $-6,469.02.
Overall Profit Factor: 0.82, Win Rate: 35.8%.
The system shows 7 regime types, 4 session types, and 124 unique symbols.
Key findings: premature exits (<15min) and wrong regime trades are the largest loss drivers.
MSS/Sweep/FVG cannot be validated due to 100% zeros in historical persistence.
New persistence fixes are in place for future signals.

## 2. Signal Quality Score
**4.2/10**

## 3. Production Grade
**4.1/10** (PF=0.82, WR=35.8%)

## 4. Profitability Grade
**4.1/10** (Expectancy=$-4.50, PnL=$-6,469.02)

## 5. Data Quality Grade
**4.29/10** (42.9% fields populated)

## 6. Institutional Grade?
**NO** (Regime profit=1/7, RR profit=1/5, Data=42.9%)

## 7. Deploy Recommendation
### **DO NOT DEPLOY**
- PF: 0.82 ❌
- WR: 35.8% ❌
- Expectancy: $-4.50 ❌
- Data Quality: 42.9% ❌

## 8. Exact Next Actions (Priority Order)
1. **Enforce 30-min minimum hold** — Blocks premature exits, projected highest impact
2. **Block quiet/range regime trades** — Already implemented via HARD_ALLOWED_REGIMES + quiet filter
3. **Block Asia/off-hours sessions** — Already implemented via SessionQualityFilter
4. **Wait for MSS/Sweep/FVG validation** — Need 50+ new trades with persistence data
5. **Monitor symbol expectancy** — Auto-blacklist after 20+ trades with negative expectancy
6. **Run for 7 days** — Collect new trade data with all institutional fields populated
7. **Re-audit** — Re-run this audit after 50+ new trades for definitive evidence
