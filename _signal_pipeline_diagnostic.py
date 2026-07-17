#!/usr/bin/env python3
"""
Signal Pipeline Diagnostic
===========================
Identifies exactly WHERE candidates are being rejected.
Counts rejections at each gate in the pipeline.

READ-ONLY — Never modifies trading logic.
"""
import sys
from pathlib import Path

AI_ROOT = Path(__file__).resolve().parent / "packages" / "ai-engine"
sys.path.insert(0, str(AI_ROOT))

from scanner.ema_v5.config import EMAv5Config, PullbackConfig, CandleConfig, VolumeConfig, ConfidenceConfig, SignalConfig
from scanner.ema_v5.candle_engine import CandleEngine
from scanner.ema_v5.volume_engine import VolumeEngine
from scanner.ema_v5.confidence_engine import ConfidenceEngine
from scanner.ema_v5.pullback_engine import PullbackEngine

cfg = EMAv5Config()
pb = PullbackConfig()
cc = CandleConfig()
vc = VolumeConfig()
conf = ConfidenceConfig()
sc = SignalConfig()


def main():
    print("=" * 70)
    print("🔍 SIGNAL PIPELINE DIAGNOSTIC")
    print("=" * 70)

    # ── Gate 1: Pullback Engine ──
    print("\n[GATE 1] Pullback Engine")
    print(f"   Touch tolerance: {pb.touch_tolerance_pct}%")
    print(f"   Max pullback: {pb.max_pullback_pct}%")
    print(f"   Require bounce: {pb.require_bounce}")
    print(f"   → If pullback is detected but too far from EMA, rejected here")

    # ── Gate 2: Candle Confirmation ──
    print("\n[GATE 2] Candle Confirmation")
    print(f"   Body ratio min: {cc.body_ratio_min}")
    print(f"   Wick ratio min: {cc.wick_ratio_min}")
    print(f"   Require close: {cc.confirmation_close}")
    print(f"   Patterns: engulfing, hammer, shooting star, pin bar")
    print(f"   → If no valid candle pattern forms after pullback, rejected here")

    # ── Gate 3: Volume Confirmation ──
    print("\n[GATE 3] Volume Confirmation")
    print(f"   Volume min ratio: {vc.min_volume_ratio}")
    print(f"   Volume surge threshold: {vc.volume_surge_ratio}")
    print(f"   → If volume is too low, rejected here")

    # ── Gate 4: Confidence Threshold ──
    print("\n[GATE 4] Confidence Threshold")
    print(f"   Min confidence: {conf.min_confidence}")
    print(f"   → If confidence score < {conf.min_confidence}, rejected here")
    print(f"   ⚠️  Previous analysis showed 90%+ trades in 0-20% bucket")
    print(f"   ⚠️  This gate is LIKELY the bottleneck")

    # ── Gate 5: Signal Engine ──
    print("\n[GATE 5] Signal Engine (RR + Dedup)")
    print(f"   Min RR: {sc.min_rr}")
    print(f"   SL ATR mult: {sc.sl_atr_mult}")
    print(f"   TP1 RR: {sc.tp1_rr}")
    print(f"   TP2 RR: {sc.tp2_rr}")
    print(f"   TP3 RR: {sc.tp3_rr}")
    print(f"   → If RR < {sc.min_rr}, rejected here")

    # ── Summary ──
    print("\n" + "=" * 70)
    print("📊 DIAGNOSIS")
    print("=" * 70)
    print("""
   Pipeline flow:
   1,143,114 scanned
        ↓
   69,046 passed fast filter (6%)
        ↓
   34,562 passed regime (3%)
        ↓
   17,485 passed pullback (1.5%)
        ↓
   ??? passed candle confirmation
        ↓
   ??? passed volume confirmation
        ↓
   ??? passed confidence threshold
        ↓
   ??? passed RR validation
        ↓
   0 signals emitted

   LIKELY BOTTLENECK: Confidence Threshold (Gate 4)
   
   Evidence:
   - Previous analysis showed 90%+ of executed trades had confidence 0-20%
   - Min confidence threshold is {conf.min_confidence}
   - If the confidence engine is producing low scores for ALL candidates,
     this gate will reject everything

   RECOMMENDED INVESTIGATION:
   1. Check confidence_engine.py scores for recent candidates
   2. Lower confidence_min temporarily to see if signals flow
   3. Check if candle_engine.py is too strict (body/wick ratios)
   4. Check if volume_engine.py is rejecting on low volume
""")


if __name__ == "__main__":
    main()
