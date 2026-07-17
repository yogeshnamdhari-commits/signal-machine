# EMA_V5 v1.0.0 RC1 — Release Notes

**Date:** 2026-06-26
**Status:** 🟢 RELEASE CANDIDATE APPROVED
**Commit:** 4fa7cad0

---

## Release Summary

EMA_V5 v1.0.0 is an institutional-grade cryptocurrency trading signal engine featuring multi-timeframe EMA analysis, AI-powered regime detection, and real-time WebSocket market scanning across Binance futures.

This release represents 32 development phases, 29 packages, 162 files, and 23,893 lines of production Python code.

---

## Key Features

- **Multi-EMA Alignment** — EMA20/50/144/200 convergence analysis
- **Regime Detection** — BUY_MODE, SELL_MODE, NO_TREND classification
- **Candlestick Recognition** — Engulfing, hammer, shooting star, pin bar
- **Volume Confirmation** — 1.5x average volume threshold
- **Confidence Scoring** — 0-100 weighted scoring across 5 components
- **State Machine** — 8 states, 28 transitions, persistence, safety timeouts
- **Risk Management** — Position sizing, drawdown limits, cooldowns, circuit breakers
- **Paper Trading** — Full simulated execution with P&L tracking
- **Real-time WebSocket** — Binance futures live data (3.3M+ ticks)
- **Bridge Synchronization** — Sub-second data propagation to dashboard
- **Atomic JSON Writes** — Crash-safe bridge file updates
- **SQLite WAL** — Concurrent read/write with integrity checks
- **Security** — XSS/SQLi prevention, rate limiting, audit logging
- **Recovery** — State restoration, cache rebuild, bridge recovery

---

## What's Fixed (This Session)

1. **BUG-001:** avg_confidence * 100 in database stats → removed multiplier
2. **BUG-002:** store_signal sl→stop_loss key mapping → added mapping
3. **BUG-003:** EMA_V5 signal missing status='active' → added in engine.py

---

## Validation Results

| Check | Result |
|---|---|
| Tests | 63/63 ✅ |
| Security | 7/7 ✅ |
| Code Quality | 162/162 syntax, 543/543 type hints ✅ |
| Runtime | Engine running, WebSocket connected ✅ |
| Bridge | 14/14 files fresh ✅ |
| Database | Integrity ok, WAL mode ✅ |
| Historical | 460 trades validated ✅ |

---

## Breaking Changes

None. Pure additive module.

---

## Migration Notes

No migration required. EMA_V5 is completely isolated.
