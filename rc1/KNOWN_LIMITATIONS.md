# EMA_V5 v1.0.0 — Known Limitations

---

## By Design

1. **Conservative signal generation** — Requires all 5 conditions (regime + trend + pullback + candle + volume) at 90%+ confidence. May produce 0 signals for extended periods.

2. **Single exchange** — Only Binance futures configured. Bybit/OKX available in v1.1.

3. **Single timeframe** — Primary 1h analysis. Multi-timeframe available in v1.2.

4. **No order execution** — Generates signals only. Paper trading simulates fills.

5. **Altcoin performance** — Historical backtest shows weaker returns on BNB, XRP, DOGE. BTC/ETH recommended.

---

## Technical

6. **339 unused imports** — Cosmetic only. `from __future__ import annotations` and similar. No runtime impact.

7. **SQLite single-writer** — Acceptable for current volume. PostgreSQL migration available in v2.0.

8. **JSON bridge files** — File-based sync. Message queue available in v2.0.

9. **No Docker** — Manual deployment. Containerization available in v1.1.

10. **No Telegram alerts** — Module exists but not configured for production.

---

## Risk

11. **Market regime dependency** — Strategy performs best in trending markets. Range-bound markets produce fewer signals.

12. **Confidence threshold sensitivity** — Lowering from 90% to 75% increases signals but also false positives.

13. **Historical validation limited** — 33 months of data. Longer backtests would provide more robust statistics.
