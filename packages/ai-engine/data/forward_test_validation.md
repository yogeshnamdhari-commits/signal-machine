# EMA V5 Forward Test Validation Report

Generated: 2026-07-20 19:25 UTC

## 1. Summary
- Trades: 24 | PF: 0.53 | WR: 33.3% | PnL: $-31.77

## 2. Candidate Filter Replay

| Filter | Trades | Wins | WR% | PnL | PF | Status |
|---|---|---|---|---|---|---|
| ALL | 24 | 8 | 33% | $-31.77 | 0.53 | ❌ |
| CONF≥0.93 | 15 | 6 | 40% | $-3.85 | 0.90 | ⚠️ Need 5 |
| TREND≥88 | 5 | 3 | 60% | $+0.49 | 1.02 | ⚠️ Need 15 |
| CONF≥0.93+TREND≥88 | 3 | 3 | 100% | $+22.16 | ∞ | ⚠️ Need 17 |

## 3. Feature Correlation with PnL

| Feature | r(PnL) | r(Win) | Mean(W) | Mean(L) | Strength |
|---|---|---|---|---|---|
| Confidence | +0.424 | +0.291 | 0.941 | 0.928 | **STRONG** |
| Trend Score | +0.094 | +0.361 | 90.625 | 84.062 | weak |
| Volume Ratio | +0.041 | -0.015 | 2.172 | 2.203 | weak |
| EMA 20-50 | -0.004 | +0.142 | 0.646 | 0.530 | weak |
| EMA 50-144 | -0.173 | +0.010 | 1.518 | 1.493 | *moderate* |
| Total Spread | -0.154 | -0.002 | 2.987 | 2.995 | *moderate* |
| ATR% | +0.000 | +0.000 | 0.000 | 0.000 | weak |
| SL Dist% | -0.864 | -0.604 | 0.000 | 0.719 | **STRONG** |
| R:R | -0.757 | -1.000 | 0.000 | 1.499 | **STRONG** |
| MFE% | +0.532 | +0.641 | 1.978 | 0.531 | **STRONG** |
| MAE% | -0.450 | -0.137 | 0.539 | 0.928 | **STRONG** |

## 4. Top Predictors

| Rank | Feature | r(PnL) |
|---|---|---|
| 1 | SL Dist% | -0.864 |
| 2 | R:R | -0.757 |
| 3 | MFE% | +0.532 |
| 4 | MAE% | -0.450 |
| 5 | Confidence | +0.424 |

## 5. Feature Importance by Threshold

*Which thresholds actually improve performance?*

| Feature | Threshold | Trades | Wins | WR% | PF | Avg PnL | vs Baseline |
|---|---|---|---|---|---|---|---|
| Confidence ≥0.90 | ≥0.90 | 24 | 8 | 33% | 0.53 | $-1.32 | +0.00 ❌ |
| Confidence ≥0.93 | ≥0.93 | 15 | 6 | 40% | 0.90 | $-0.26 | +0.37 🟡 |
| Confidence ≥0.95 | ≥0.95 | 3 | 2 | 67% | 9.49 | $+4.61 | +8.96 ✅ |
| Trend Score ≥80 | ≥80 | 21 | 8 | 38% | 0.56 | $-1.33 | +0.03 🟡 |
| Trend Score ≥85 | ≥85 | 21 | 8 | 38% | 0.56 | $-1.33 | +0.03 🟡 |
| Trend Score ≥88 | ≥88 | 5 | 3 | 60% | 1.02 | $+0.10 | +0.49 ✅ |
| Trend Score ≥100 | ≥100 | 5 | 3 | 60% | 1.02 | $+0.10 | +0.49 ✅ |
| Volume Ratio ≥1.0 | ≥1.0 | 24 | 8 | 33% | 0.53 | $-1.32 | +0.00 ❌ |
| Volume Ratio ≥1.5 | ≥1.5 | 18 | 5 | 28% | 0.59 | $-1.04 | +0.06 🟡 |
| Volume Ratio ≥2.0 | ≥2.0 | 12 | 4 | 33% | 0.53 | $-1.39 | +0.00 🟡 |
| EMA Spread ≥1.0% | ≥1.0% | 22 | 8 | 36% | 0.55 | $-1.33 | +0.02 🟡 |
| EMA Spread ≥2.0% | ≥2.0% | 14 | 4 | 29% | 0.46 | $-1.88 | -0.07 ❌ |
| EMA Spread ≥3.0% | ≥3.0% | 7 | 2 | 29% | 0.36 | $-2.51 | -0.17 ❌ |
| SL Distance <0.3% | <0.3% | 12 | 8 | 67% | 9.16 | $+2.67 | +8.63 ✅ |
| SL Distance <0.5% | <0.5% | 16 | 8 | 50% | 3.30 | $+1.57 | +2.76 ✅ |
| SL Distance =0% | =0% | 8 | 8 | 100% | ∞ | $+4.50 | ∞ ✅ |
| MFE ≥0.5% | ≥0.5% | 14 | 8 | 57% | 1.21 | $+0.45 | +0.68 ✅ |
| MFE ≥1.0% | ≥1.0% | 9 | 6 | 67% | 1.67 | $+1.48 | +1.14 ✅ |
| MFE ≥2.0% | ≥2.0% | 4 | 4 | 100% | ∞ | $+6.71 | ∞ ✅ |

## 6. Validation Progress

*Criteria: ≥20 trades, PF≥1.5, WR≥50%*
- CONF≥0.93: 15/20 [███████████████░░░░░]
- TREND≥88: 5/20 [█████░░░░░░░░░░░░░░░]

## 7. Recent Trades

| Symbol | Side | PnL | Exit | Conf | Trend | Spread | MFE |
|---|---|---|---|---|---|---|---|
| CHZUSDT | SHORT | $-2.65 | trailing_stop | 0.93 | 85 | 3.29% | 0.79% |
| AGLDUSDT | SHORT | $+4.66 | take_profit_1 | 0.93 | 85 | 2.84% | 2.08% |
| HUSDT | SHORT | $-7.32 | max_hold_24h | 0.94 | 85 | 1.73% | 0.00% |
| RAVEUSDT | SHORT | $+7.62 | take_profit_1 | 0.96 | 100 | 2.41% | 3.18% |
| HOTUSDT | SHORT | $-8.54 | max_hold_24h | 0.94 | 85 | 2.81% | 0.00% |
| PIPPINUSDT | SHORT | $-5.82 | max_hold_24h | 0.93 | 85 | 2.68% | 0.00% |
| PHAROSUSDT | SHORT | $-1.89 | trailing_stop | 0.91 | 85 | 3.37% | 1.97% |
| PHAROSUSDT | SHORT | $-1.89 | trailing_stop | 0.91 | 70 | 2.85% | 1.97% |
| MEUSDT | SHORT | $-3.61 | max_hold_24h | 0.92 | 85 | 2.79% | 0.00% |
| MEGAUSDT | SHORT | $+3.47 | take_profit_1 | 0.93 | 85 | 1.13% | 1.62% |