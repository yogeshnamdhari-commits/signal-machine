# Confidence Calibration Analysis

## Question
Is the confidence threshold (70% NY, 82% London SHORT) correctly calibrated?

## Current Architecture (Verified from Code)

### Confidence Formula (scanner/ema_v5/confidence_engine.py)
```
confidence = inst_score × 0.50 + regime×0.10 + (-trend×0.10) + pullback×0.15 + (-candle×0.10) + (-volume×0.05)
```
Where `inst_score = trend_score` (institutional_score unavailable in EMA V5)

### Thresholds (Verified from Code)
- EMA V5 inner: `min_confidence = 40.0` (scanner/ema_v5/config.py)
- Session filter NY: `NY_MIN_CONFIDENCE = 0.70` → 70% (scanner/session_quality_filter.py)
- Session filter London SHORT: `LONDON_BEAR_MIN_CONFIDENCE = 97.0` → 97% (scanner/session_quality_filter.py)

### Typical Candidate Score
For trend=80, regime=BUY, pullback=detected, candle=80, volume=80:
```
= 80×0.50 + 100×0.10 + (-80×0.10) + 100×0.15 + (-80×0.10) + (-80×0.05)
= 40 + 10 - 8 + 15 - 8 - 4 = 45%
```

## Hypothesis (NOT YET PROVEN)
Confidence model produces ~45% for typical candidates, but session filter requires 70%+.
This MAY explain why most candidates are rejected at session filter gate.

## Evidence Required
For rejected candidates in 40-70% range, track price after rejection:
- Would they have hit TP?
- Would they have hit SL?
- If most would have lost → threshold is correct
- If many would have won → threshold too restrictive

## Status
- [x] Calibration mismatch verified from database, signal traces, and code
- [x] Historical trades: 90-95% confidence (database)
- [x] Current candidates: 42-51% confidence (signal traces)
- [x] Publication threshold: 70% (code)
- [x] Shadow tracker implemented: records all candidates and market outcomes
- [ ] Collect 100+ candidates with market outcomes
- [ ] Analyze optimal threshold from empirical data
- [ ] Apply threshold change based on evidence
