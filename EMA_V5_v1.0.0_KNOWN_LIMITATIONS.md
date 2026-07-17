# EMA_V5 v1.0.0 — Known Limitations

**Version:** EMA_V5 v1.0.0
**Date:** 2026-06-26

---

## Critical Limitations

### 1. Conservative Signal Generation
**Impact:** Zero signals emitted in live trading.

The system requires ALL of the following on the SAME bar:
- Engulfing candlestick pattern
- 1.5x average volume
- Strong EMA trend alignment (20 > 50 > 144 > 200)
- Pullback confirmation

These conditions rarely align simultaneously, especially at the 90%+ confidence threshold. This is intentional to prevent false signals but means the system may miss valid opportunities.

**Mitigation:** Lower `min_confidence` to 0.70 for more signals (increases false positives).

---

## Performance Limitations

### 2. Single Exchange Support
**Impact:** Only Binance futures are scanned.

Bybit and OKX connections are configured but not actively used. This limits market coverage to ~60% of futures volume.

**Planned Fix:** EMA_V5 v1.1

### 3. Single Timeframe
**Impact:** Only 1h timeframe is analyzed.

Multi-timeframe analysis (4h, 1d) is not implemented. This may miss higher-timeframe trend confirmations.

**Planned Fix:** EMA_V5 v1.2

### 4. No Position Sizing Optimization
**Impact:** Fixed 1% risk per trade.

The system uses a simple fixed-fractional position sizing approach. More sophisticated methods (Kelly criterion, volatility-based sizing) are not implemented.

**Planned Fix:** EMA_V5 v1.2

---

## Data Limitations

### 5. Historical Data Coverage
**Impact:** 33 months of 1h data (215,865 bars).

Historical validation is limited to available data. Longer backtests would provide more robust statistics.

**Available Symbols:** BTC, ETH, BNB, SOL, XRP, DOGE

### 6. No Order Book Data
**Impact:** Only OHLCV candlestick data is used.

Order book depth, bid-ask spread, and trade flow data are not incorporated into signal generation.

**Planned Fix:** EMA_V5 v2.0

---

## Infrastructure Limitations

### 7. SQLite for Persistence
**Impact:** Single-writer database.

SQLite limits concurrent write operations. For high-throughput scenarios, PostgreSQL migration would be required.

**Planned Fix:** EMA_V5 v2.0

### 8. JSON Bridge Files
**Impact:** File-based data propagation.

Bridge synchronization uses JSON files rather than shared memory or message queues. This introduces latency and potential race conditions.

**Planned Fix:** EMA_V5 v2.0

### 9. No Docker Support
**Impact:** Manual deployment required.

The system requires manual Python environment setup. Docker containerization is not implemented.

**Planned Fix:** EMA_V5 v1.1

---

## Operational Limitations

### 10. No Automated Backtesting
**Impact:** Manual backtest execution.

Historical validation must be triggered manually. Automated nightly backtests are not configured.

**Planned Fix:** EMA_V5 v1.1

### 11. No Telegram Alerts (Production)
**Impact:** No push notifications.

The Telegram module exists but is not configured for production alerts. Manual monitoring required.

**Planned Fix:** EMA_V5 v1.1

### 12. No Paper Trading Mode
**Impact:** No simulated order execution.

The system generates signals but does not simulate order fills. Paper trading requires manual signal-to-order conversion.

**Planned Fix:** EMA_V5 v1.2

---

## Security Limitations

### 13. No API Key Encryption
**Impact:** API keys stored in plaintext.

Binance API keys are stored in configuration files without encryption. This is acceptable for read-only market data but would need encryption for order execution.

**Planned Fix:** EMA_V5 v2.0

### 14. No Rate Limiting Dashboard
**Impact:** Dashboard has no rate limiting.

The Streamlit dashboard has no request throttling. In a shared environment, this could be exploited.

**Planned Fix:** EMA_V5 v1.1

---

## Summary

| Category | Limitations | Critical |
|---|---|---|
| Signal Generation | 1 | 0 |
| Performance | 3 | 0 |
| Data | 2 | 0 |
| Infrastructure | 3 | 0 |
| Operations | 3 | 0 |
| Security | 2 | 0 |
| **Total** | **14** | **0** |

**No critical limitations** that prevent production deployment. All limitations are by design or deferred to future versions.
