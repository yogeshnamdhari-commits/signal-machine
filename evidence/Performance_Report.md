# EMA_V5 v1.0.0 — Performance Report

**Date:** 2026-06-26 03:20 UTC

---

## 1. Computation Benchmarks

| Operation | Time (ms) | Iterations | Method |
|---|---|---|---|
| EMA Computation | 0.20 | 100 | `time.time()` |
| Full Pipeline | 0.23 | 100 | 9 stages end-to-end |
| Bridge Write | 0.56 | 100 | Atomic JSON write |
| Database Write | 0.83 | 100 | SQLite WAL INSERT |
| Cache Read | 0.003 | 1000 | Dict lookup |

---

## 2. Memory Usage

| Component | Size | Method |
|---|---|---|
| 100 Symbol Cache | 892.3 KB | `tracemalloc` |
| Peak Memory | 892.3 KB | `tracemalloc` |
| Per-Symbol | ~8.9 KB | 892.3 KB / 100 |
| Memory Leaks | None detected | Bounded data structures |

---

## 3. Bridge Latency

| File | Age | Write Frequency |
|---|---|---|
| ema_v5.json | 13s | Every scan cycle |
| status.json | 13s | Every engine tick |
| engine_health.json | 13s | Every engine tick |
| market_data.json | 13s | Every WebSocket tick |
| signals.json | 13s | On signal generation |
| positions.json | 13s | On position change |

---

## 4. Database Performance

| Metric | Value | Evidence |
|---|---|---|
| Journal Mode | WAL | `PRAGMA journal_mode` |
| Busy Timeout | 5000ms | `PRAGMA busy_timeout` |
| Write Latency | 0.83ms | Benchmark |
| Integrity | ok | `PRAGMA integrity_check` |
| Size | 69,632 bytes | `os.path.getsize()` |

---

## 5. Scanner Throughput

| Metric | Value | Calculation |
|---|---|---|
| Total Scans | 28,601 | `scanner.scan_count` |
| Uptime | 3180s | `status.uptime` |
| Scans/sec | 8.99 | `28601 / 3180` |
| Cache Hits | 86 | `scanner.cache.size` |
| Cache TTL | 300s | `ema_v5_config.cache.cache_ttl_sec` |

---

## 6. Scaling Limits

| Resource | Current | Max Configured | Headroom |
|---|---|---|---|
| Cached Symbols | 86 | 500 | 83% |
| Concurrent Positions | 1 | 3 | 67% |
| Diagnostics | 0 | 10,000 | 100% |
| Cooldowns | Active | Unlimited | OK |
| State Machine | 6 symbols | 250 symbols | 98% |
