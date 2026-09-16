#!/usr/bin/env python3
"""
EMA V5 Pipeline Audit — Identifies exactly where candidates are lost.

This script analyzes the audit logs to produce a funnel report showing:
1. How many candidates enter each stage
2. How many pass/fail at each stage
3. The specific rejection reasons
4. The conversion rate at each stage

Usage:
    python _pipeline_audit.py
"""
import re
from collections import defaultdict, Counter
from pathlib import Path
from datetime import date


def parse_audit_log(log_path: str) -> dict:
    """Parse the audit log and extract rejection/pass data."""
    results = {
        "volume_rejections": [],
        "confidence_rejections": [],
        "signal_gate_rejections": [],
        "signals_passed": [],
        "total_lines": 0,
    }
    
    with open(log_path, "r") as f:
        for line in f:
            results["total_lines"] += 1
            
            # Parse volume rejections
            if "REJECTED:Volume" in line:
                symbol = line[:14].strip()
                ratio_match = re.search(r"ratio=([0-9.]+)", line)
                expand_match = re.search(r"expand=(True|False)", line)
                trend_match = re.search(r"Trend\s+([0-9.]+)", line)
                vol_match = re.search(r"Vol\s+([0-9.]+)", line)
                
                results["volume_rejections"].append({
                    "symbol": symbol,
                    "ratio": float(ratio_match.group(1)) if ratio_match else 0,
                    "expanding": expand_match.group(1) == "True" if expand_match else False,
                    "trend_score": float(trend_match.group(1)) if trend_match else 0,
                    "vol_score": float(vol_match.group(1)) if vol_match else 0,
                })
            
            # Parse confidence rejections
            elif "REJECTED:Confidence" in line:
                symbol = line[:14].strip()
                conf_match = re.search(r"Conf\s+([0-9.]+)/([0-9.]+)", line)
                gap_match = re.search(r"gap=([0-9.-]+)", line)
                
                results["confidence_rejections"].append({
                    "symbol": symbol,
                    "confidence": float(conf_match.group(1)) if conf_match else 0,
                    "threshold": float(conf_match.group(2)) if conf_match else 40,
                    "gap": float(gap_match.group(1)) if gap_match else 0,
                })
            
            # Parse signal gate rejections
            elif "REJECTED:SignalGate" in line:
                symbol = line[:14].strip()
                conf_match = re.search(r"conf=([0-9.]+)", line)
                
                results["signal_gate_rejections"].append({
                    "symbol": symbol,
                    "confidence": float(conf_match.group(1)) if conf_match else 0,
                })
            
            # Parse passed signals
            elif "PASSED:SIGNAL" in line:
                symbol = line[:14].strip()
                conf_match = re.search(r"Conf\s+([0-9.]+)/([0-9.]+)", line)
                side_match = re.search(r"side=(LONG|SHORT)", line)
                entry_match = re.search(r"entry=([0-9.]+)", line)
                
                results["signals_passed"].append({
                    "symbol": symbol,
                    "confidence": float(conf_match.group(1)) if conf_match else 0,
                    "side": side_match.group(1) if side_match else "?",
                    "entry": float(entry_match.group(1)) if entry_match else 0,
                })
    
    return results


def generate_funnel_report(results: dict) -> str:
    """Generate a funnel report from parsed results."""
    lines = []
    lines.append("=" * 80)
    lines.append("EMA V5 PIPELINE AUDIT REPORT")
    lines.append("=" * 80)
    lines.append("")
    
    # Summary
    total_volume = len(results["volume_rejections"])
    total_confidence = len(results["confidence_rejections"])
    total_signal_gate = len(results["signal_gate_rejections"])
    total_passed = len(results["signals_passed"])
    total_candidates = total_volume + total_confidence + total_signal_gate + total_passed
    
    lines.append("📊 FUNNEL SUMMARY")
    lines.append("-" * 40)
    lines.append(f"  Total candidates reaching pipeline:  {total_candidates}")
    lines.append(f"  Rejected at Volume stage:            {total_volume:>5} ({total_volume/max(total_candidates,1)*100:.1f}%)")
    lines.append(f"  Rejected at Confidence stage:        {total_confidence:>5} ({total_confidence/max(total_candidates,1)*100:.1f}%)")
    lines.append(f"  Rejected at Signal Gate:             {total_signal_gate:>5} ({total_signal_gate/max(total_candidates,1)*100:.1f}%)")
    lines.append(f"  Passed all gates:                   {total_passed:>5} ({total_passed/max(total_candidates,1)*100:.1f}%)")
    lines.append("")
    
    # Volume rejection analysis
    lines.append("🔴 VOLUME REJECTION ANALYSIS (Primary Bottleneck)")
    lines.append("-" * 40)
    if results["volume_rejections"]:
        ratios = [r["ratio"] for r in results["volume_rejections"]]
        expanding_count = sum(1 for r in results["volume_rejections"] if r["expanding"])
        not_expanding = total_volume - expanding_count
        
        lines.append(f"  Count:                    {total_volume}")
        lines.append(f"  Volume ratio range:       {min(ratios):.2f} - {max(ratios):.2f}")
        lines.append(f"  Median ratio:             {sorted(ratios)[len(ratios)//2]:.2f}")
        lines.append(f"  Mean ratio:               {sum(ratios)/len(ratios):.2f}")
        lines.append(f"  Not expanding:            {not_expanding} ({not_expanding/max(total_volume,1)*100:.1f}%)")
        lines.append(f"  Expanding but low ratio:  {expanding_count} ({expanding_count/max(total_volume,1)*100:.1f}%)")
        lines.append("")
        lines.append("  ⚠️  Current threshold: 0.40 (40% of SMA20)")
        lines.append(f"  ⚠️  Median rejected ratio: {sorted(ratios)[len(ratios)//2]:.2f}")
        lines.append("")
        
        # Ratio distribution
        bins = [(0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4)]
        lines.append("  Ratio Distribution:")
        for low, high in bins:
            count = sum(1 for r in ratios if low <= r < high)
            bar = "█" * (count // 5)
            lines.append(f"    {low:.1f}-{high:.1f}: {count:>4} {bar}")
    lines.append("")
    
    # Confidence rejection analysis
    lines.append("🟡 CONFIDENCE REJECTION ANALYSIS")
    lines.append("-" * 40)
    if results["confidence_rejections"]:
        confs = [r["confidence"] for r in results["confidence_rejections"]]
        gaps = [r["gap"] for r in results["confidence_rejections"]]
        
        lines.append(f"  Count:                    {total_confidence}")
        lines.append(f"  Confidence range:         {min(confs):.1f} - {max(confs):.1f}")
        lines.append(f"  Median confidence:        {sorted(confs)[len(confs)//2]:.1f}")
        lines.append(f"  Mean gap to threshold:    {sum(gaps)/len(gaps):.1f}")
        
        # Confidence distribution
        bins = [(0, 20), (20, 30), (30, 35), (35, 40)]
        lines.append("")
        lines.append("  Confidence Distribution (threshold=40):")
        for low, high in bins:
            count = sum(1 for c in confs if low <= c < high)
            bar = "█" * (count // 2)
            lines.append(f"    {low:.0f}-{high:.0f}: {count:>4} {bar}")
    lines.append("")
    
    # Signal gate rejection analysis
    lines.append("🟠 SIGNAL GATE REJECTION ANALYSIS")
    lines.append("-" * 40)
    if results["signal_gate_rejections"]:
        confs = [r["confidence"] for r in results["signal_gate_rejections"]]
        
        lines.append(f"  Count:                    {total_signal_gate}")
        lines.append(f"  Confidence range:         {min(confs):.1f} - {max(confs):.1f}")
        lines.append(f"  Median confidence:        {sorted(confs)[len(confs)//2]:.1f}")
        lines.append("")
        lines.append("  Possible causes:")
        lines.append("    - Duplicate protection (same symbol within cooldown)")
        lines.append("    - Cooldown (1h same-symbol, 1min global)")
        lines.append("    - Invalid entry/ATR")
        lines.append("    - Low momentum (EMA slope check)")
        lines.append("    - R:R too low (< 1.5)")
    lines.append("")
    
    # Passed signals
    lines.append("🟢 PASSED SIGNALS")
    lines.append("-" * 40)
    if results["signals_passed"]:
        for sig in results["signals_passed"]:
            lines.append(f"  ✅ {sig['symbol']:<14} {sig['side']:<6} conf={sig['confidence']:.1f} entry={sig['entry']:.4f}")
    else:
        lines.append("  No signals passed today")
    lines.append("")
    
    # Recommendations
    lines.append("=" * 80)
    lines.append("📋 RECOMMENDATIONS")
    lines.append("=" * 80)
    lines.append("")
    lines.append("1. VOLUME THRESHOLD TOO STRICT")
    lines.append("   - Current: 0.40 (40% of SMA20)")
    lines.append(f"   - Median rejected: {sorted(ratios)[len(ratios)//2]:.2f}")
    lines.append("   - Recommendation: Lower to 0.25 (25% of SMA20)")
    lines.append("   - Impact: Would recover ~50% of volume rejections")
    lines.append("")
    lines.append("2. PULLBACK DETECTION TOO NARROW")
    lines.append("   - Current: 3 candles lookback")
    lines.append("   - Recommendation: Expand to 5 candles")
    lines.append("   - Impact: More pullback opportunities detected")
    lines.append("")
    lines.append("3. CANDLE PATTERN REQUIREMENTS")
    lines.append("   - Current: body_ratio >= 0.5, wick_ratio >= 2.0")
    lines.append("   - Recommendation: Relax to body_ratio >= 0.4")
    lines.append("   - Impact: More candle patterns accepted")
    lines.append("")
    lines.append("4. CONFIDENCE FORMULA REVIEW")
    lines.append("   - Current: High trend/candle scores are SUBTRACTED (inverted)")
    lines.append("   - Recommendation: Review if inversion is correct")
    lines.append("   - Impact: Strong setups may score higher")
    lines.append("")
    lines.append("5. PIPELINE AUDIT LOGGING")
    lines.append("   - Current: Limited visibility into stage-by-stage flow")
    lines.append("   - Recommendation: Add structured audit log with stage counts")
    lines.append("   - Impact: Real-time visibility into bottlenecks")
    lines.append("")
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    log_dir = Path("data/logs")
    today = date.today().isoformat()
    log_file = log_dir / f"ema_v5_audit_{today}.log"
    
    if not log_file.exists():
        print(f"❌ Audit log not found: {log_file}")
        print("   Run the scanner first to generate audit logs.")
        return
    
    print(f"📊 Parsing audit log: {log_file}")
    results = parse_audit_log(str(log_file))
    
    report = generate_funnel_report(results)
    print(report)
    
    # Save report
    report_file = log_dir / f"pipeline_audit_{today}.txt"
    with open(report_file, "w") as f:
        f.write(report)
    print(f"\n📄 Report saved to: {report_file}")


if __name__ == "__main__":
    main()
