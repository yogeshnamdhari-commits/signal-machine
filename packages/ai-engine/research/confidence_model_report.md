# Confidence Model Analysis Report

**Generated:** 2026-07-16 01:28
**Trades analyzed:** 361
**Train/Test split:** 70%/30%

## Executive Summary

| Metric | Raw Confidence | Recalibrated |
|--------|---------------|--------------|
| Brier Score (test) | 0.6067 | 0.2058 |
| Log Loss (test) | 1.8454 | 0.6040 |
| **Improvement** | — | **66.1% better** |

## 1. Component Contribution (Logistic Regression)

Standardized coefficients show independent contribution of each component.
Positive = higher score → more likely to win. Negative = higher score → less likely to win.

| Component | Coefficient | Direction | Odds Ratio | Importance Rank |
|-----------|-------------|-----------|------------|-----------------|
| Institutional Score | +0.7140 | ✅ Positive | 2.042 | #1 |
| Fvg Score | +0.5408 | ✅ Positive | 1.717 | #2 |
| Mss Score | -0.3868 | ❌ Negative | 0.679 | #3 |
| Volatility Score | -0.1468 | ❌ Negative | 0.863 | #4 |

**Intercept:** -1.1234

### Model Performance

| Dataset | AUC | Brier | Log Loss | N |
|---------|-----|-------|----------|---|
| Train | 0.6327 | 0.1888 | 0.5544 | 252 |
| Test | 0.6498 | 0.1883 | 0.5519 | 109 |

## 2. Component Stability (5-Fold Cross-Validation)

Are component coefficients stable across different data splits?

| Component | Mean Coef | Std | CV | Consistent Sign? |
|-----------|-----------|-----|----|--------------------|
| Institutional Score | +0.8492 | 0.1772 | 0.21 | ✅ Yes |
| Mss Score | -0.2421 | 0.0815 | 0.34 | ✅ Yes |
| Fvg Score | +0.3352 | 0.0777 | 0.23 | ✅ Yes |
| Volatility Score | -0.3048 | 0.0372 | 0.12 | ✅ Yes |

**Cross-validated AUC:** 0.6242 ± 0.0547
**Cross-validated Brier:** 0.1907 ± 0.0073

### Interpretation

- **Institutional Score:** ✅ Consistently positive predictor. Higher values → better outcomes.
- **Mss Score:** ❌ Consistently negative predictor. Higher values → worse outcomes. Consider inverting or reducing weight.
- **Fvg Score:** ✅ Consistently positive predictor. Higher values → better outcomes.
- **Volatility Score:** ❌ Consistently negative predictor. Higher values → worse outcomes. Consider inverting or reducing weight.

## 3. Probability Recalibration (Isotonic Regression)

Non-parametric mapping from raw confidence to observed win probability.

| Raw Confidence | Calibrated Probability | Adjustment |
|----------------|------------------------|------------|
| 40% | 21.5% | -18.5% |
| 45% | 21.5% | -23.5% |
| 50% | 21.5% | -28.5% |
| 55% | 21.5% | -33.5% |
| 60% | 21.5% | -38.5% |
| 65% | 21.5% | -43.5% |
| 70% | 21.5% | -48.5% |
| 75% | 21.5% | -53.5% |
| 80% | 21.5% | -58.5% |
| 85% | 21.5% | -63.5% |
| 90% | 24.7% | -65.3% |
| 95% | 32.8% | -62.2% |
| 100% | 33.3% | -66.7% |

## 4. Expectancy-Maximizing Threshold

Using recalibrated confidence scores:

| Threshold | Trades | Win Rate | Profit Factor | Expectancy | Avg PnL |
|-----------|--------|----------|---------------|------------|---------|
| ≥30.0% | 168 | 29.2% | 1.00 | $-1.70 | $-0.01 |
| ≥32.5% | 168 | 29.2% | 1.00 | $-1.70 | $-0.01 |

**Optimal threshold:** ≥30.0% — 168 trades, WR 29.2%, PF 1.00, Exp $-1.70

## 5. Recommendations

### Evidence-Based Actions (ordered by confidence)

1. **Increase Institutional Score weight** — Consistently predicts better outcomes (coef=+0.7140, stable across CV folds).
2. **Reduce Mss Score weight** — Consistently predicts worse outcomes (coef=-0.3868, stable across CV folds).
3. **Increase Fvg Score weight** — Consistently predicts better outcomes (coef=+0.5408, stable across CV folds).
4. **Reduce Volatility Score weight** — Consistently predicts worse outcomes (coef=-0.1468, stable across CV folds).
5. **Apply isotonic recalibration** — Reduces Brier score by 66.1% on test data. Preserves ranking while fixing probability scale.

### What NOT to do

- Do not change weights based on univariate analysis alone.
- Do not lower the confidence threshold without validating on hold-out data.
- Do not interpret raw confidence as probability — use the recalibration mapping.

### Next Steps

1. Validate these findings on the next 100+ trades (out-of-sample).
2. If component findings hold, adjust weights in `confidence_engine.py`.
3. Implement isotonic recalibration as a post-processing step.
4. A/B test: current model vs recalibrated model with position sizing.
