# Signal Pipeline Trace — 2026-06-28

## Complete Pipeline Architecture

```
Scanner (scanner.py)
  │
  ├─ Stage 0: Fast Filter
  │   └─ Requires: klines >= 220 candles (5m) + valid OHLCV + volume > 0
  │   └─ REJECTION: Low — most symbols with history pass
  │
  ├─ Stage 1: EMA Cache (cache.py)
  │   └─ Computes: EMA20, EMA50, EMA144, EMA200 + slopes + ATR + vol_sma20
  │   └─ REJECTION: Low — pure math
  │
  ├─ Stage 2: Regime Engine (regime_engine.py)
  │   ├─ BUY_MODE: EMA20 > EMA50 > EMA144 > EMA200 + slopes > 0 + price > ema144/200
  │   ├─ SELL_MODE: EMA20 < EMA50 < EMA144 < EMA200 + slopes < 0 + price < ema144/200
  │   └─ NO_TREND → REJECTED (EXIT)
  │   └─ Dashboard: 31 Buy + 40 Sell = 71 symbols pass
  │
  ├─ Stage 3: Trend Engine (trend_engine.py)
  │   └─ Requires: regime == BUY/SELL + chain alignment + slope scores
  │   └─ trend_score < 50 → direction=None → REJECTED (EXIT)
  │   └─ Score: chain=40, ema20_slope=15, ema50_slope=15, ema144_slope=15, steep_bonus=15
  │
  ├─ Stage 4: Pullback Engine (pullback_engine.py)
  │   └─ Checks last 3 candles for EMA20/50 touch within 0.3% tolerance
  │   ├─ BUY: low <= ema AND close >= ema
  │   ├─ SELL: high >= ema AND close <= ema
  │   └─ No touch → REJECTED (EXIT)
  │   └─ Dashboard: 84 stuck in WAITING_PULLBACK — no EMA touch
  │
  ├─ State → WAITING_PULLBACK
  │
  ├─ Stage 5: Candle Engine (candle_engine.py)
  │   └─ Requires specific pattern on last 2 candles:
  │   ├─ Engulfing (body_ratio >= 0.5) → score 100
  │   ├─ Hammer/Shooting Star (wick >= 2x body) → score 85
  │   └─ Pin Bar (wick >= 2x body) → score 90
  │   └─ No pattern → REJECTED (EXIT)
  │   └─ Dashboard: 18 in WAITING_CONFIRMATION — pattern found but next stage failed
  │
  ├─ State → WAITING_CONFIRMATION
  │
  ├─ Stage 6: Volume Engine (volume_engine.py)
  │   └─ Requires: volume >= SMA20 (min ratio 1.0)
  │   └─ Score: ratio × 50 (1.0x=50, 2.0x=100)
  │   └─ Below SMA20 → REJECTED (EXIT)
  │
  ├─ Stage 7: Confidence Engine (confidence_engine.py) ← CRITICAL BOTTLENECK
  │   └─ MIN CONFIDENCE: 90.0
  │   └─ Formula: regime×0.15 + trend×0.25 + pullback×0.25 + candle×0.20 + volume×0.15
  │   └─ Below 90.0 → REJECTED (EXIT)
  │
  └─ Stage 8: Signal Engine (signal_engine.py)
      ├─ Duplicate check (same regime within 1h)
      ├─ Cooldown (1h same symbol, 1min global)
      ├─ R:R check (min 1.5)
      └─ Builds signal dict → ACTIVE_BUY or ACTIVE_SELL
```

## Dashboard State Distribution

| State | Count | Meaning |
|-------|-------|---------|
| NO_TREND | 31 | No EMA chain alignment |
| BUY_MODE | 31 | Regime = bullish, awaiting pullback |
| SELL_MODE | 40 | Regime = bearish, awaiting pullback |
| WAITING_PULLBACK | 84 | Pullback not detected (no EMA touch) |
| WAITING_CONFIRMATION | 18 | Pullback found but candle/volume/confidence failed |
| ACTIVE_BUY | 2 | Trade open |
| ACTIVE_SELL | 0 | No active short trades |

## The Critical Bottleneck: Confidence ≥ 90.0

### Mathematical Analysis

The confidence formula:
```
confidence = regime×0.15 + trend×0.25 + pullback×0.25 + candle×0.20 + volume×0.15
```

When all components pass (regime=100, pullback=100):
```
base = 15 + 25 = 40 points
remaining needed = 50 points from trend + candle + volume
```

### Scenario Scoring

| Scenario | Trend | Candle | Volume | Total | Pass? |
|----------|-------|--------|--------|-------|-------|
| Strong + engulfing + 2.0x vol | 100 (25.0) | 100 (20.0) | 100 (15.0) | **100.0** | ✅ |
| Strong + pin bar + 1.6x vol | 85 (21.25) | 90 (18.0) | 80 (12.0) | **91.25** | ✅ |
| Strong + hammer + 1.6x vol | 85 (21.25) | 85 (17.0) | 80 (12.0) | **90.25** | ✅ barely |
| Moderate + pin bar + 1.4x vol | 70 (17.5) | 90 (18.0) | 70 (10.5) | **86.0** | ❌ |
| Moderate + hammer + 1.2x vol | 65 (16.25) | 85 (17.0) | 60 (9.0) | **82.25** | ❌ |
| Weak + hammer + 1.0x vol | 50 (12.5) | 85 (17.0) | 50 (7.5) | **77.0** | ❌ |

### What This Means

To score ≥ 90.0, you need SIMULTANEOUSLY:
1. **Strong trend** (score ≥ 80) — perfect EMA chain + steep slopes
2. **Engulfing or pin bar** candle — specific shape on exact candle after pullback
3. **Volume ratio ≥ ~1.6x** — significantly above average

This combination is **extremely rare** on 5m candles.

## Why 18 Symbols Fail at WAITING_CONFIRMATION

The 18 symbols in WAITING_CONFIRMATION have:
- ✅ Regime classified (BUY/SELL)
- ✅ Trend confirmed (direction set)
- ✅ Pullback detected (EMA touch)
- ✅ Candle pattern found (engulfing/hammer/pin bar)
- ❌ Volume OR confidence failed

Most likely scenario: confidence scores 80-89 (below 90 threshold).

## Pipeline Health Assessment

| Aspect | Status | Notes |
|--------|--------|-------|
| Architecture | ✅ Correct | All stages connected properly |
| State machine | ✅ Working | Transitions occur as designed |
| Signal generation | ✅ Working | 2 active trades exist |
| Selectivity | ⚠️ Very high | 90% confidence on 5m is extremely strict |
| Signal frequency | ⚠️ Very low | ~2 signals per 27 hours |

## Recommendation

The pipeline is **not broken**. It is **extremely selective by design**.

The 90.0 confidence threshold on 5m timeframe requires near-perfect alignment of:
- Strong trending market
- Specific candlestick pattern
- Above-average volume

This is a valid approach for high-conviction trading, but it will produce very few signals.

**Options:**
1. Keep 90.0 threshold (accept very low signal frequency)
2. Reduce to 80.0 (moderate increase in signals)
3. Reduce to 75.0 (significant increase in signals)
4. Add additional scoring dimensions (ADX, multi-timeframe confirmation)

Each option trades signal frequency against selectivity.
