# EMA_V5 v1.0.0 — Scanner Report

**Date:** 2026-06-26 03:20 UTC

---

## 1. Scanner Architecture

```
EMAv5Scanner
├── EMACache          — EMA value caching (TTL=300s, max=500 symbols)
├── RegimeEngine      — BUY_MODE / SELL_MODE / NO_TREND classification
├── TrendEngine       — Trend direction and strength (0-100 score)
├── PullbackEngine    — EMA20/EMA50 touch detection
├── CandleEngine      — Engulfing, hammer, shooting star, pin bar
├── VolumeEngine      — Volume ratio vs SMA20
├── ConfidenceEngine  — Weighted scoring (0-100)
├── SignalEngine      — Signal generation with dedup and cooldown
├── TradeManager      — Trade lifecycle (open, monitor, close)
└── StateManager      — Per-symbol state machine (8 states)
```

---

## 2. Scanner Metrics

| Metric | Value | Evidence |
|---|---|---|
| Total Scans | 28,601 | `ema_v5.json → scanner.scan_count` |
| Signals Generated | 1 | `ema_v5.json → scanner.signal_count` |
| Signal Rate | 0.0035% | `1 / 28601` |
| Cache Size | 86 symbols | `scanner.cache.size` |
| Open Trades | 1 | `scanner.trade_manager.open_count` |
| Scans/sec | 8.99 | `28601 / 3180s` |
| Uptime | 3180s (0.9h) | `status.json → uptime` |

---

## 3. Pipeline Stages (13 stages verified)

| # | Stage | Latency | Status |
|---|---|---|---|
| 0 | Fast Filter | 0.00ms | ✅ |
| 1 | EMA Cache | 0.32ms | ✅ |
| 2 | Regime Classification | 0.01ms | ✅ |
| 3 | Trend Analysis | 0.00ms | ✅ |
| 4 | Pullback Detection | 0.00ms | ✅ |
| 5 | Candlestick Pattern | 0.01ms | ✅ |
| 6 | Volume Confirmation | 0.01ms | ✅ |
| 7 | Confidence Scoring | 0.01ms | ✅ |
| 8 | Signal Generation | <0.01ms | ✅ |
| 9 | State Transition | <0.01ms | ✅ |
| 10 | Trade Management | <0.01ms | ✅ |
| 11 | Bridge Export | 0.56ms | ✅ |
| 12 | Database Persist | 0.83ms | ✅ |

---

## 4. State Distribution (Live Evidence)

| State | Count | Percentage |
|---|---|---|
| WAITING_PULLBACK | 68 | 50.0% |
| NO_TREND | 30 | 22.1% |
| WAITING_CONFIRMATION | 15 | 11.0% |
| BUY_MODE | 12 | 8.8% |
| SELL_MODE | 10 | 7.4% |
| ACTIVE_BUY | 1 | 0.7% |
| **TOTAL** | **136** | **100%** |

---

## 5. Pipeline Trace: BTCUSDT (1h)

### Raw Market Data
- Symbol: BTCUSDT
- Timeframe: 1h
- Candles: 300
- Last: O=73892.50 H=73956.50 L=73799.60 C=73799.60 V=38269

### Stage 1: EMA Cache
| EMA | Value | Slope |
|---|---|---|
| EMA20 | 73894.85 | -0.0048 |
| EMA50 | 73858.65 | 0.0005 |
| EMA144 | 74524.13 | -0.0126 |
| EMA200 | 74906.15 | -0.0141 |
| ATR(14) | 157.80 | — |
| Vol SMA20 | 38648.79 | — |

### Stage 2: Regime
- **Regime:** NO_TREND
- EMA chain aligned: False
- EMA144 slope ok: True
- EMA200 slope ok: True
- Reason: ema_chain_not_aligned

### Stage 3: Trend
- **Trend:** NEUTRAL
- Direction: None
- Score: 0
- Reason: no_trend_regime

### Stage 4: Pullback
- **Detected:** False
- Reason: no_trend

### Stage 5: Candle
- **Pattern:** None
- Reason: no_pattern

### Stage 6: Volume
- **Volume OK:** False
- Ratio: 0.99 (below 1.0x threshold)
- Score: 49.5

### Stage 7: Confidence
- **Score:** 7.4
- **Passed:** False
- Min threshold: 90.0
- Breakdown: regime=0, trend=0, pullback=0, candle=0, volume=49.5

### Stage 8: Decision
- **Result:** ❌ REJECT
- Reasons: regime=NO_TREND | no_pullback | no_candle_pattern | volume=0.99x | conf=7.4<90.0

### Stage 9: State Transition
- From: NO_TREND → To: NO_TREND (no change)

---

## 6. Rejection Analysis

The pipeline correctly rejects BTCUSDT because:
1. EMA chain not aligned (EMA20=73894 < EMA144=74524 < EMA200=74906)
2. No pullback detected (no trend to pull back from)
3. No candlestick pattern
4. Volume ratio 0.99x (below 1.0x threshold)
5. Confidence 7.4 << 90.0 minimum

**This is correct behavior.** The system is designed to be conservative.
