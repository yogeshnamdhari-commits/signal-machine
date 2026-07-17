# Volume Rejection Evidence — 2026-06-28

## Volume Rejection Logs

```
BTCUSDT  last_vol=67   sma20=247   ratio=0.27  regime=SELL_MODE
ENSUSDT  last_vol=1432 sma20=2844  ratio=0.50  regime=SELL_MODE
LSKUSDT  last_vol=610  sma20=80723 ratio=0.01  regime=SELL_MODE
BTCUSDT  last_vol=67   sma20=247   ratio=0.27  regime=SELL_MODE
ENSUSDT  last_vol=1432 sma20=2844  ratio=0.50  regime=SELL_MODE
```

## Analysis

| Symbol | Last Volume | SMA20 | Ratio | Status |
|--------|------------|-------|-------|--------|
| BTCUSDT | 67 | 247 | 0.27 | REJECTED |
| ENSUSDT | 1,432 | 2,844 | 0.50 | REJECTED |
| LSKUSDT | 610 | 80,723 | 0.01 | REJECTED |

## Key Finding

Pullback candles have **27–50% of average volume**. The volume filter requires ≥ 100%.

This is a **design conflict**: pullbacks are low-volume by nature. Requiring above-average volume at a pullback candle eliminates most valid setups.

## Options

1. **Lower volume threshold** to 0.5x SMA20
2. **Remove volume filter** entirely (let confidence scoring handle it)
3. **Change volume scoring** to reward low volume at pullbacks
4. **Keep as-is** (accept very low signal frequency)

## Recommendation

Lower `min_volume_ratio` from 1.0 to 0.5 in `config.py`. This allows pullback candles with 50%+ of average volume to pass, while still filtering out dead/illiquid markets.

After adjustment, re-run diagnostics to verify candidates reach the confidence stage.
