#!/usr/bin/env python3
"""Show active production signals from bridge."""
import json

with open("packages/ai-engine/data/bridge/signals.json") as f:
    data = json.load(f)

sigs = data.get("signals", [])
print(f"Total active signals: {len(sigs)}")
print(f"Timestamp: {data.get('timestamp', 0)}")
print()

for i, s in enumerate(sigs[:10]):
    sym = s.get("symbol", "?")
    side = s.get("side", "?")
    score = s.get("institutional_score", 0)
    grade = s.get("signal_grade", "?")
    conf = s.get("confidence_100", 0)
    entry = s.get("entry_price", 0)
    sl = s.get("stop_loss", 0)
    tp1 = s.get("take_profit_1", 0)
    tp2 = s.get("take_profit_2", 0)
    tp3 = s.get("take_profit_3", 0)
    rr1 = s.get("rr_1", 0)
    rr2 = s.get("rr_2", 0)
    rr3 = s.get("rr_3", 0)
    qual = s.get("quality_grade", "?")
    sl_source = s.get("sl_source", "?")
    tp1_source = s.get("tp1_source", "?")
    regime = s.get("regime", "?")
    sweep_data = s.get("sweep_setup", {})
    sweep_comp = sweep_data.get("composite_score", 0)
    sweep_type = sweep_data.get("sweep_type", "?")
    status = s.get("status", "?")
    entry_type = s.get("entry_type", "?")
    grade_score = s.get("grade_score", 0)
    
    print(f"{'='*60}")
    print(f"SIGNAL #{i+1}: {side} {sym}")
    print(f"{'='*60}")
    print(f"  Status:     {status} | Entry Type: {entry_type}")
    print(f"  Regime:     {regime}")
    print(f"  Score:      {score:.1f} | Grade: {grade} ({grade_score:.1f}) | Conf: {conf:.1f} | Quality: {qual}")
    print(f"  Sweep:      {sweep_type} composite={sweep_comp:.1f}")
    print()
    print(f"  ENTRY:      ${entry:.6f}")
    print(f"  STOP LOSS:  ${sl:.6f} ({sl_source})")
    print(f"  TAKE PROF 1: ${tp1:.6f} ({tp1_source}) | R:R = {rr1:.2f}")
    print(f"  TAKE PROF 2: ${tp2:.6f} ({s.get('tp2_source', '?')}) | R:R = {rr2:.2f}")
    print(f"  TAKE PROF 3: ${tp3:.6f} ({s.get('tp3_source', '?')}) | R:R = {rr3:.2f}")
    print()
    
    # 7-Pillar breakdown
    pb = s.get("pillar_breakdown", {})
    if pb:
        print(f"  7-PILLAR INSTITUTIONAL BREAKDOWN:")
        print(f"    Sweep (25%):  {pb.get('sweep_score', 0):.1f}")
        print(f"    MSS   (20%):  {pb.get('mss_score', 0):.1f}")
        print(f"    FVG   (15%):  {pb.get('fvg_score', 0):.1f}")
        print(f"    OI    (15%):  {pb.get('oi_score', 0):.1f}")
        print(f"    Delta (10%):  {pb.get('delta_score', 0):.1f}")
        print(f"    CVD   (10%):  {pb.get('cvd_score', 0):.1f}")
        print(f"    Fund  (5%):   {pb.get('funding_score', 0):.1f}")
    print()
    
    # Additional confirmation data
    oi_label = s.get("oi_trend_label", "?")
    oi_exp = s.get("oi_expansion_pct", 0)
    sm_score = s.get("sm_score", 0)
    rsi = s.get("indicators", {}).get("rsi", 0)
    vol_ratio = s.get("indicators", {}).get("vol_ratio", 0)
    change_24h = s.get("change_24h", 0)
    
    print(f"  OI Trend:    {oi_label} ({oi_exp:+.1f}%)")
    print(f"  Smart Money: {sm_score:.1f}")
    print(f"  RSI:         {rsi:.1f} | Vol Ratio: {vol_ratio:.1f}x | 24h: {change_24h:+.1f}%")
    print(f"  Created:     {s.get('created_at', 0)}")
    print()
