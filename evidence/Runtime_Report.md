# EMA_V5 v1.0.0 — Runtime Report

**Date:** 2026-06-26 03:20 UTC

---

## 1. Scanner Startup

| Metric | Value | Evidence |
|---|---|---|
| Startup Time | 0.5ms | Measured via `time.time()` |
| Scanner Type | EMAv5Scanner | `type(scanner).__name__` |
| Components | 11 engines initialized | All `__init__` called |
| State File | `data/ema_v5_state.json` | 6 symbols loaded |

### Initialization Sequence
```
1. EMACache.__init__()          — cache dict created
2. RegimeEngine.__init__()      — no state
3. TrendEngine.__init__()       — no state
4. PullbackEngine.__init__()    — no state
5. CandleEngine.__init__()      — no state
6. VolumeEngine.__init__()      — no state
7. ConfidenceEngine.__init__()  — no state
8. SignalEngine.__init__()      — cooldowns + dedup dicts created
9. TradeManager.__init__()      — open_trades dict created
10. StateManager.__init__()     — loads state from disk (6 symbols)
```

---

## 2. Engine Running

| Metric | Value | Source |
|---|---|---|
| PID | managed by supervisor | `service/engine.pid` |
| Running | True | `status.json → running` |
| Uptime | 3180s (0.9h) | `status.json → uptime` |
| Symbols Tracked | 250 | `status.json → symbols` |
| Last Update | 2.0s ago | `time.time() - last_update` |

---

## 3. WebSocket

| Metric | Value | Source |
|---|---|---|
| Binance Connected | True | `status.json → ws_connected` |
| Status | live | `freshness_snapshot.exchanges.binance.status` |
| Tick Count | 2,235,628 | `tick_count` |
| Tick Age | 6ms | `age_display` |
| Tick Rate | 703 ticks/sec | `tick_count / uptime` |
| Reconnects | 0 | `reconnect_count` |
| Dropped | 0 | `dropped_count` |
| Errors | 0 | `error_count` |

---

## 4. Symbol Scanner

| Metric | Value | Source |
|---|---|---|
| Scan Count | 28,601 | `ema_v5.json → scanner.scan_count` |
| Signal Count | 1 | `ema_v5.json → scanner.signal_count` |
| Signal Rate | 0.0035% | `signal_count / scan_count` |
| Cache Size | 86 | `scanner.cache.size` |
| Open Trades | 1 | `scanner.trade_manager.open_count` |
| Scans/sec | 8.99 | `scan_count / uptime` |

### State Distribution
```
ACTIVE_BUY:              1
BUY_MODE:               12
NO_TREND:               30
SELL_MODE:              10
WAITING_CONFIRMATION:   15
WAITING_PULLBACK:       68
```

---

## 5. Data Sources

| Source | Age | Updates | Errors |
|---|---|---|---|
| open_interest | 2m ago | 24 | 0 |
| klines | 2m ago | 44 | 0 |
| trades | 2m ago | 25 | 0 |
| funding | never | 0 | 0 |
| exchange_flow | never | 0 | 0 |
| liquidation | never | 0 | 0 |

---

## 6. Exchange Status

| Exchange | Connected | Ticks | Age | Reconnects | Dropped | Errors |
|---|---|---|---|---|---|---|
| Binance | ✅ | 2,235,628 | 6ms | 0 | 0 | 0 |
| Bybit | ❌ | 0 | never | 0 | 0 | 0 |
| OKX | ❌ | 0 | never | 0 | 0 | 0 |
| Delta | ❌ | 0 | never | 0 | 0 | 0 |
