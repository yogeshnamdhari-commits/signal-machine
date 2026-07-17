# Pipeline Diagnostics — Volume Fix (2026-06-28)

## Changes Made

### 1. Volume Engine (`volume_engine.py`)
- **Before:** `min_volume_ratio = 1.0` (volume must be ≥ 100% of SMA20)
- **After:** `pullback_threshold = 0.4` (volume must be ≥ 40% of SMA20)
- Added: volume expansion check (current volume > previous volume)
- Added: expansion bonus (+10 points) in scoring

### 2. Cache (`cache.py`)
- Added: `prev_volume` field (previous candle's volume)
- Used for expansion comparison

### 3. Scanner (`scanner.py`)
- Updated: volume rejection logging includes expansion status

## Results (4000 candidates)

| Stage | Rejected | % |
|-------|----------|---|
| fast_filter | 3459 | 86.5% |
| regime | 216 | 5.4% |
| trend | 0 | 0.0% |
| pullback | 116 | 2.9% |
| candle | 172 | 4.3% |
| volume | 35 | 0.9% |
| confidence | 0 | 0.0% |
| signal_engine | 0 | 0.0% |

**Signals generated: 2**

## Before vs After

| Metric | Before | After |
|--------|--------|-------|
| Volume threshold | 1.0x | 0.4x |
| Signals generated | 0 | 2 |
| Pipeline flow | Blocked at volume | Flows end-to-end |

## Key Insight

Pullback candles naturally have lower volume (27–50% of average). Requiring 100%+ volume at pullback candles eliminates valid setups. The 0.4x threshold allows pullback-volume candles while still filtering dead markets.

## Next Steps

1. Let scanner run for 24+ hours with new volume logic
2. Collect 100+ signals for statistical analysis
3. Review confidence distribution
4. Adjust confidence threshold only if evidence supports it
