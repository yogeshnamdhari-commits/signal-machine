# EMA_V5 v1.0.0 — Signal Report

**Date:** 2026-06-26 03:20 UTC

---

## 1. Signal Pipeline Lifecycle

```
Market Data
    ↓
Fast Filter (min candles, valid OHLCV)
    ↓
EMA Cache (EMA20/50/144/200, slopes, ATR)
    ↓
Regime Classification (BUY_MODE/SELL_MODE/NO_TREND)
    ↓
Trend Analysis (direction, score, chain alignment)
    ↓
Pullback Detection (EMA20/50 touch, bounce)
    ↓
Candlestick Pattern (engulfing, hammer, pin bar)
    ↓
Volume Confirmation (ratio vs SMA20)
    ↓
Confidence Scoring (weighted 0-100)
    ↓
Signal Generation (dedup + cooldown)
    ↓
Trade Manager (open position)
    ↓
Bridge Export (JSON files)
    ↓
Database Persist (SQLite)
```

---

## 2. Signal Pipeline Trace: BTCUSDT

| Stage | Input | Output | Pass? |
|---|---|---|---|
| Fast Filter | 300 candles | PASS | ✅ |
| EMA Cache | klines | ema20=73894.85 | ✅ |
| Regime | ema_data | NO_TREND | ❌ |
| Trend | ema_data + regime | NEUTRAL (score=0) | ❌ |
| Pullback | klines + ema_data | Not detected | ❌ |
| Candle | klines + regime | No pattern | ❌ |
| Volume | ema_data | ratio=0.99 (need 1.0) | ❌ |
| Confidence | 5 evals | 7.4 (need 90.0) | ❌ |
| Decision | — | REJECT | — |
| State | NO_TREND | NO_TREND (no change) | — |

**Result:** REJECT — 5 conditions failed, confidence 7.4 < 90.0

---

## 3. Historical Signal Quality

### Backtest Results (1h timeframe, min_confidence=0.50)

| Symbol | Trades | Win Rate | Profit Factor | Return | Max DD |
|---|---|---|---|---|---|
| BTCUSDT | 109 | 24.8% | 1.59 | +56.9% | — |
| ETHUSDT | 112 | 25.0% | 1.66 | +54.0% | — |
| BNBUSDT | 64 | 15.6% | 0.41 | -24.3% | — |
| SOLUSDT | 60 | 21.7% | 1.18 | +7.5% | — |
| XRPUSDT | 73 | 13.7% | 0.58 | -21.4% | — |
| DOGEUSDT | 42 | 19.0% | 0.93 | -1.9% | — |

### Aggregate
- **Total Trades:** 460
- **Profitable Symbols:** 3/6
- **Best:** ETHUSDT (+54.0%, PF=1.66)
- **Worst:** BNBUSDT (-24.3%, PF=0.41)

---

## 4. Signal Dedup & Cooldown

| Rule | Value | Source |
|---|---|---|
| Same-symbol cooldown | 3600s (1h) | `ema_v5_config.cooldown.same_symbol_sec` |
| Global cooldown | 60s (1min) | `ema_v5_config.cooldown.global_sec` |
| Max signals/hour | 10 | `ema_v5_config.cooldown.max_signals_per_hour` |
| Duplicate detection | Same symbol + same regime | `signal_engine._check_duplicate()` |

---

## 5. Signal Data Model

```json
{
  "action": "open_position",
  "strategy": "ema_v5",
  "symbol": "BTCUSDT",
  "side": "LONG",
  "entry": 76000.0,
  "sl": 75000.0,
  "take_profit_1": 77000.0,
  "take_profit_2": 78000.0,
  "take_profit_3": 79000.0,
  "confidence": 92.5,
  "regime": "BUY_MODE",
  "rr_1": 1.5,
  "rr_2": 3.0,
  "rr_3": 5.0,
  "ema_data": { "ema20": ..., "ema50": ..., "ema144": ..., "ema200": ... },
  "components": { "regime": ..., "trend": ..., "pullback": ..., "candle": ..., "volume": ... },
  "timestamp": 1782423947.97
}
```

---

## 6. Confidence Scoring

| Component | Weight | Range |
|---|---|---|
| Regime | 15% | 0 or 100 |
| Trend | 25% | 0-100 |
| Pullback | 25% | 0 or 100 |
| Candle | 20% | 0-100 |
| Volume | 15% | 0-100 |
| **Total** | **100%** | **0-100** |

**Min threshold:** 90.0 (all components must score high)
