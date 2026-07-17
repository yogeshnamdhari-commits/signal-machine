# Pipeline Diagnostics — Initial Analysis (2026-06-28)

## Raw Data (3000 candidates)

| Stage | Rejected | % |
|-------|----------|---|
| fast_filter | 2717 | 90.6% |
| regime | 101 | 3.4% |
| trend | 0 | 0.0% |
| pullback | 90 | 3.0% |
| candle | 68 | 2.3% |
| volume | 24 | 0.8% |
| confidence | 0 | 0.0% |
| signal_engine | 0 | 0.0% |

## Critical Finding

**Zero candidates reached the confidence stage.**

Candidates that passed candle patterns (68) were ALL rejected at the volume stage (24) or never reached confidence (44).

This means: **candle patterns at pullback coincide with below-average volume**.

## Why This Happens

Market microstructure:
- Pullback = price retracing toward EMA (calm, lower volume)
- Candle pattern at pullback = reversal signal (still low volume)
- Volume filter requires volume ≥ SMA20 (1.0x minimum)

**Pullbacks are inherently low-volume events.** Requiring above-average volume at a pullback candle is contradictory.

## The Real Bottleneck

Not the confidence threshold (90.0). The **volume filter** rejects candidates before they reach confidence scoring.

## Options

1. **Lower volume threshold** to 0.5x SMA20 (allow pullback-volume candles)
2. **Remove volume filter** from pipeline (let confidence scoring handle it)
3. **Change volume scoring** to reward low volume at pullbacks (contrarian approach)
4. **Keep as-is** (accept very low signal frequency)

## Recommendation

Do NOT change `min_confidence` yet. The bottleneck is earlier in the pipeline.

After 10,000+ candidates, re-analyze to confirm this pattern holds.
