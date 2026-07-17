# EMA_V5 v1.0.0 — Bridge Report

**Date:** 2026-06-26 03:20 UTC

---

## 1. Bridge File Inventory

| File | Age | Size | Fresh | Purpose |
|---|---|---|---|---|
| ema_v5.json | 13s | 19,916 B | ✅ | EMA_V5 state + signals |
| status.json | 13s | 3,551 B | ✅ | Engine running status |
| engine_health.json | 13s | 389 B | ✅ | Health metrics |
| market_data.json | 13s | 405,547 B | ✅ | Live market prices |
| signals.json | 13s | 67 B | ✅ | Emitted signals |
| positions.json | 13s | 1,758 B | ✅ | Open positions |
| equity_history.json | 13s | 209,952 B | ✅ | Portfolio equity |
| funnel.json | 13s | 47,420 B | ✅ | Signal pipeline funnel |
| alerts.json | 13s | 2,697 B | ✅ | System alerts |
| metrics.json | 13s | 495 B | ✅ | Performance metrics |
| smart_money_map.json | 13s | 337,104 B | ✅ | Smart money flow |
| trade_history.json | 13s | 89,349 B | ✅ | Historical trades |
| death_report.json | 13s | 1,226 B | ✅ | Risk report |
| data_quality.json | 13s | 2,299 B | ✅ | Data quality |
| alpha_ranking.json | 55s | 23,611 B | ✅ | Alpha ranking |
| backtest_trades.json | 1314496s | 135,241 B | ❌ | Static backtest |

**Active Files:** 15/16 fresh
**Total Size:** ~1.2 MB

---

## 2. Atomic Write Verification

| Check | Method | Status |
|---|---|---|
| Write pattern | tmp → replace | ✅ Verified in `json_storage.py` |
| fsync | `os.fsync(f.fileno())` | ✅ |
| Crash safety | Atomic replacement | ✅ |
| No partial writes | tmp file only on success | ✅ |

---

## 3. Bridge Latency

| Metric | Value |
|---|---|
| Write Frequency | Every scan cycle (~111ms) |
| Max File Age | 13s |
| Write Latency | 0.56ms |
| Read Latency | < 0.01ms |
| Sync Spread | < 1s between files |

---

## 4. Bridge Data Sources

| File | Writer | Frequency |
|---|---|---|
| ema_v5.json | EMAv5Scanner.get_bridge_data() | Every scan |
| status.json | Engine health check | Every tick |
| engine_health.json | Engine health check | Every tick |
| market_data.json | WebSocket handler | Every tick |
| signals.json | Signal engine | On signal |
| positions.json | Position manager | On change |
