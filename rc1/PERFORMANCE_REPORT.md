# EMA_V5 v1.0.0 — Performance Report

---

## Benchmarks (Measured 2026-06-26)

| Operation | Time | Iterations | Method |
|---|---|---|---|
| Startup | 0.5ms | 1 | `time.time()` |
| EMA Compute | 0.42ms | 100 | `time.time()` |
| Full Pipeline | 0.46ms | 100 | 9 stages |
| Bridge Write | 0.84ms | 100 | Atomic JSON |
| Memory Peak | 136KB | — | `tracemalloc` |

---

## Runtime Metrics (Live System)

| Metric | Value | Source |
|---|---|---|
| Engine Uptime | 8.8h | `status.json` |
| Tick Count | 3,392,624 | `status.json` |
| Tick Rate | ~107 ticks/sec | `tick_count / uptime` |
| Scan Count | 47,414 | `ema_v5.json` |
| Scans/sec | ~5.4 | `scan_count / uptime` |
| Cache Size | 86 symbols | `scanner.cache.size` |
| Bridge Files | 14/14 fresh | All < 300s |

---

## Scaling

| Resource | Current | Max | Headroom |
|---|---|---|---|
| Cached Symbols | 86 | 500 | 83% |
| Concurrent Positions | 1 | 3 | 67% |
| State Machine | 136 | 250 | 46% |
| Diagnostics | 0 | 10,000 | 100% |
