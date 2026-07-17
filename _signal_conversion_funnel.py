#!/usr/bin/env python3
"""
Signal Conversion Funnel
=========================
Shows exactly WHERE BUY_MODE and SELL_MODE candidates are rejected.
Answers: "Why do 17 BUY_MODE symbols never become signals?"

READ-ONLY — Never modifies trading logic.
"""
import sys
import re
from pathlib import Path
from collections import defaultdict, OrderedDict

AI_ROOT = Path(__file__).resolve().parent / "packages" / "ai-engine"
LOG_DIR = AI_ROOT / "data" / "logs"


def parse_conversion_funnel(date_str: str = "2026-07-09"):
    """Parse lifecycle log to build conversion funnel for BUY/SELL MODE."""
    log_path = LOG_DIR / f"ema_v5_lifecycle_{date_str}.log"
    if not log_path.exists():
        print(f"Log not found: {log_path}")
        return None

    # Track each symbol's journey through the pipeline
    # Key: symbol, Value: list of events
    symbol_journeys = OrderedDict()
    
    pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}) \| (\w+)\s+\| (\w+)\s+→ (\w+)\s+\| conf=\s*([\d.]+) side=\s*(\w*)\s*\| (.*)"
    )

    with open(log_path) as f:
        for line in f:
            m = pattern.search(line)
            if not m:
                continue
            time_str, symbol, from_state, to_state, conf, side, event = m.groups()
            
            if symbol not in symbol_journeys:
                symbol_journeys[symbol] = []
            
            symbol_journeys[symbol].append({
                "time": time_str,
                "from": from_state.strip(),
                "to": to_state.strip(),
                "confidence": float(conf) if conf else 0,
                "side": side.strip(),
                "event": event.strip(),
            })

    return symbol_journeys


def build_funnel(journeys, mode_filter="BUY_MODE"):
    """Build conversion funnel for a specific mode."""
    # Find all symbols that entered the target mode
    mode_symbols = {}
    
    for symbol, events in journeys.items():
        for i, event in enumerate(events):
            if event["to"] == mode_filter:
                # Found a symbol entering BUY_MODE/SELL_MODE
                # Track what happens next
                subsequent = events[i:]
                mode_symbols[symbol] = subsequent
                break
    
    if not mode_symbols:
        return None
    
    # Build the funnel
    funnel = OrderedDict()
    funnel[f"{mode_filter} Entry"] = {"count": len(mode_symbols), "symbols": list(mode_symbols.keys())}
    
    # Track rejections at each stage
    rejection_reasons = defaultdict(list)
    stage_counts = defaultdict(int)
    
    for symbol, events in mode_symbols.items():
        passed_pullback = False
        passed_candle = False
        passed_volume = False
        passed_confidence = False
        passed_rr = False
        signal_emitted = False
        
        for event in events:
            ev = event["event"]
            
            if "pullback_detected" in ev:
                passed_pullback = True
                stage_counts["Pullback Detected"] += 1
            
            if "candle_rejected" in ev:
                rejection_reasons["Candle"].append({
                    "symbol": symbol,
                    "detail": ev,
                    "confidence": event["confidence"],
                })
            elif "candle" in ev and "rejected" not in ev and passed_pullback:
                passed_candle = True
                stage_counts["Candle Passed"] += 1
            
            if "volume_rejected" in ev:
                rejection_reasons["Volume"].append({
                    "symbol": symbol,
                    "detail": ev,
                    "confidence": event["confidence"],
                })
            elif "volume" in ev and "rejected" not in ev and passed_candle:
                passed_volume = True
                stage_counts["Volume Passed"] += 1
            
            if "confidence_rejected" in ev:
                rejection_reasons["Confidence"].append({
                    "symbol": symbol,
                    "detail": ev,
                    "confidence": event["confidence"],
                })
            elif "confidence" in ev and "rejected" not in ev and passed_volume:
                passed_confidence = True
                stage_counts["Confidence Passed"] += 1
            
            if "rr_rejected" in ev:
                rejection_reasons["RR"].append({
                    "symbol": symbol,
                    "detail": ev,
                })
            elif "signal_emitted" in ev or "SIGNAL" in event["to"]:
                signal_emitted = True
                stage_counts["Signal Emitted"] += 1
    
    return {
        "mode": mode_filter,
        "total": len(mode_symbols),
        "funnel": funnel,
        "stage_counts": dict(stage_counts),
        "rejection_reasons": dict(rejection_reasons),
    }


def print_funnel(result):
    """Print the conversion funnel."""
    if not result:
        print("  No data for this mode")
        return
    
    mode = result["mode"]
    total = result["total"]
    stages = result["stage_counts"]
    reasons = result["rejection_reasons"]
    
    print(f"\n  {mode} CONVERSION FUNNEL")
    print(f"  {'─' * 50}")
    
    # Build funnel display
    stages_order = [
        (f"{mode} Entry", total),
        ("Pullback Detected", stages.get("Pullback Detected", 0)),
        ("Candle Passed", stages.get("Candle Passed", 0)),
        ("Volume Passed", stages.get("Volume Passed", 0)),
        ("Confidence Passed", stages.get("Confidence Passed", 0)),
        ("Signal Emitted", stages.get("Signal Emitted", 0)),
    ]
    
    for i, (name, count) in enumerate(stages_order):
        pct = count / total * 100 if total > 0 else 0
        bar = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
        
        if i == 0:
            print(f"  {name:<25} {count:>5} ({pct:>5.1f}%)")
        else:
            prev_name, prev_count = stages_order[i-1]
            drop = prev_count - count
            drop_pct = drop / prev_count * 100 if prev_count > 0 else 0
            print(f"  ↓")
            print(f"  {name:<25} {count:>5} ({pct:>5.1f}%)  ← dropped {drop} ({drop_pct:.0f}%)")
    
    # Print rejection details
    if reasons:
        print(f"\n  📋 REJECTION DETAILS:")
        for reason, items in sorted(reasons.items(), key=lambda x: -len(x[1])):
            print(f"\n    {reason}: {len(items)} rejections")
            for item in items[:5]:
                print(f"      {item['symbol']}: {item['detail']}")
            if len(items) > 5:
                print(f"      ... and {len(items) - 5} more")


def main():
    print("=" * 70)
    print("🔍 SIGNAL CONVERSION FUNNEL")
    print("=" * 70)
    
    journeys = parse_conversion_funnel("2026-07-09")
    if not journeys:
        print("No data found.")
        return
    
    print(f"\n📊 Total symbols tracked: {len(journeys)}")
    
    # BUY_MODE funnel
    buy_result = build_funnel(journeys, "BUY_MODE")
    print_funnel(buy_result)
    
    # SELL_MODE funnel
    sell_result = build_funnel(journeys, "SELL_MODE")
    print_funnel(sell_result)
    
    # Summary
    print(f"\n{'='*70}")
    print(f"📊 DIAGNOSIS")
    print(f"{'='*70}")
    
    if buy_result and sell_result:
        buy_total = buy_result["total"]
        sell_total = sell_result["total"]
        buy_signals = buy_result["stage_counts"].get("Signal Emitted", 0)
        sell_signals = sell_result["stage_counts"].get("Signal Emitted", 0)
        
        print(f"\n  BUY_MODE: {buy_total} candidates → {buy_signals} signals")
        print(f"  SELL_MODE: {sell_total} candidates → {sell_signals} signals")
        
        # Find biggest drop
        all_reasons = {}
        for result in [buy_result, sell_result]:
            for reason, items in result.get("rejection_reasons", {}).items():
                all_reasons[reason] = all_reasons.get(reason, 0) + len(items)
        
        if all_reasons:
            biggest = max(all_reasons.items(), key=lambda x: x[1])
            print(f"\n  Biggest bottleneck: {biggest[0]} ({biggest[1]} rejections)")
    
    print(f"\n  This shows exactly WHERE candidates die after entering BUY/SELL MODE.")
    print(f"  Use this to decide which gate to investigate, not which gate to loosen.")


if __name__ == "__main__":
    main()
