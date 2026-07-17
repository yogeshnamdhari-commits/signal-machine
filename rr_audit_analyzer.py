#!/usr/bin/env python3
"""
📊 Risk:Reward Audit Analyzer
==============================
Analyzes RR rejection patterns from the RR audit CSV logs.

Usage:
    python rr_audit_analyzer.py                    # Analyze today's rejections
    python rr_audit_analyzer.py --date 2026-07-10  # Analyze specific date
    python rr_audit_analyzer.py --last 500         # Analyze last N rejections
    python rr_audit_analyzer.py --symbol BTCUSDT   # Analyze specific symbol
    python rr_audit_analyzer.py --report           # Full report to file
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# Add ai-engine to path
PROJECT_ROOT = Path(__file__).resolve().parent
AI_ROOT = PROJECT_ROOT / "packages" / "ai-engine"
sys.path.insert(0, str(AI_ROOT))


def load_rejections(
    csv_dir: Optional[str] = None,
    date: Optional[str] = None,
    last_n: Optional[int] = None,
    symbol: Optional[str] = None,
) -> List[Dict]:
    """Load rejections from CSV files."""
    if csv_dir is None:
        csv_dir = AI_ROOT / "data" / "logs" / "rr_audit"
    else:
        csv_dir = Path(csv_dir)
    
    if not csv_dir.exists():
        print(f"❌ RR audit directory not found: {csv_dir}")
        return []
    
    # Find CSV files
    if date:
        csv_files = [csv_dir / f"rr_rejections_{date}.csv"]
    else:
        csv_files = sorted(csv_dir.glob("rr_rejections_*.csv"))
    
    if not csv_files:
        print(f"❌ No RR rejection CSV files found in {csv_dir}")
        return []
    
    # Load all rejections
    rejections = []
    for csv_file in csv_files:
        if not csv_file.exists():
            continue
        try:
            with open(csv_file, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Convert numeric fields
                    for key in ["entry", "stop_loss", "tp1", "tp2", "tp3",
                                "risk", "reward", "rr_actual", "rr_required",
                                "rr_deficit", "risk_pct", "reward_pct", "rr_gap",
                                "atr_value", "sl_atr_mult", "sl_dist_pct",
                                "tp1_rr_mult", "confidence"]:
                        if key in row and row[key]:
                            try:
                                row[key] = float(row[key])
                            except (ValueError, TypeError):
                                row[key] = 0.0
                    rejections.append(row)
        except Exception as e:
            print(f"⚠️  Error reading {csv_file}: {e}")
    
    # Filter by symbol if specified
    if symbol:
        rejections = [r for r in rejections if r.get("symbol", "").upper() == symbol.upper()]
    
    # Limit to last N if specified
    if last_n and len(rejections) > last_n:
        rejections = rejections[-last_n:]
    
    return rejections


def analyze_rejections(rejections: List[Dict]) -> Dict:
    """Perform comprehensive analysis of RR rejections."""
    if not rejections:
        return {"error": "No rejections to analyze"}
    
    analysis = {
        "total_rejections": len(rejections),
        "summary": {},
        "rr_distribution": defaultdict(int),
        "sl_distance_analysis": {},
        "symbol_analysis": {},
        "session_analysis": {},
        "regime_analysis": {},
        "root_causes": [],
        "recommendations": [],
    }
    
    # ── Summary Statistics ──
    rr_values = [r.get("rr_actual", 0) for r in rejections if r.get("rr_actual")]
    sl_dists = [r.get("sl_dist_pct", 0) for r in rejections if r.get("sl_dist_pct")]
    risks = [r.get("risk", 0) for r in rejections if r.get("risk")]
    rewards = [r.get("reward", 0) for r in rejections if r.get("reward")]
    
    analysis["summary"] = {
        "avg_rr": sum(rr_values) / len(rr_values) if rr_values else 0,
        "min_rr": min(rr_values) if rr_values else 0,
        "max_rr": max(rr_values) if rr_values else 0,
        "median_rr": sorted(rr_values)[len(rr_values) // 2] if rr_values else 0,
        "avg_sl_dist_pct": sum(sl_dists) / len(sl_dists) if sl_dists else 0,
        "avg_risk": sum(risks) / len(risks) if risks else 0,
        "avg_reward": sum(rewards) / len(rewards) if rewards else 0,
    }
    
    # ── RR Distribution ──
    for rr in rr_values:
        bucket = f"{rr:.1f}"
        analysis["rr_distribution"][bucket] += 1
    
    # ── SL Distance Analysis ──
    sl_buckets = defaultdict(int)
    for dist in sl_dists:
        bucket = f"{int(dist * 2) / 2:.1f}%"
        sl_buckets[bucket] += 1
    analysis["sl_distance_analysis"] = dict(sorted(sl_buckets.items()))
    
    # ── Symbol Analysis ──
    symbol_data = defaultdict(list)
    for r in rejections:
        sym = r.get("symbol", "?")
        symbol_data[sym].append(r)
    
    for sym, sym_rej in symbol_data.items():
        sym_rr = [r.get("rr_actual", 0) for r in sym_rej if r.get("rr_actual")]
        sym_sl = [r.get("sl_dist_pct", 0) for r in sym_rej if r.get("sl_dist_pct")]
        analysis["symbol_analysis"][sym] = {
            "count": len(sym_rej),
            "avg_rr": sum(sym_rr) / len(sym_rr) if sym_rr else 0,
            "avg_sl_dist": sum(sym_sl) / len(sym_sl) if sym_sl else 0,
            "worst_rr": min(sym_rr) if sym_rr else 0,
        }
    
    # ── Session Analysis ──
    session_data = defaultdict(int)
    for r in rejections:
        session_data[r.get("session", "unknown")] += 1
    analysis["session_analysis"] = dict(sorted(session_data.items(), key=lambda x: -x[1]))
    
    # ── Regime Analysis ──
    regime_data = defaultdict(int)
    for r in rejections:
        regime_data[r.get("regime", "unknown")] += 1
    analysis["regime_analysis"] = dict(sorted(regime_data.items(), key=lambda x: -x[1]))
    
    # ── Root Cause Analysis ──
    avg_rr = analysis["summary"]["avg_rr"]
    avg_sl = analysis["summary"]["avg_sl_dist_pct"]
    
    # Check if SL is too wide
    wide_sl_count = sum(1 for d in sl_dists if d > 3.0)
    if wide_sl_count > len(sl_dists) * 0.3:
        analysis["root_causes"].append({
            "issue": "STOP LOSS TOO WIDE",
            "severity": "HIGH",
            "detail": f"{wide_sl_count / len(sl_dists) * 100:.1f}% of rejections have SL distance > 3%",
            "impact": "Increases risk, decreases RR ratio",
        })
        analysis["recommendations"].append(
            "Consider reducing sl_atr_mult from 1.5 to 1.0-1.2"
        )
    
    # Check if reward is too low
    low_reward_count = sum(1 for r in rewards if r < 0.01)  # Less than 1% reward
    if low_reward_count > len(rewards) * 0.3:
        analysis["root_causes"].append({
            "issue": "REWARD TOO LOW",
            "severity": "HIGH",
            "detail": f"{low_reward_count / len(rewards) * 100:.1f}% of rejections have reward < 1%",
            "impact": "TP1 too close to entry",
        })
        analysis["recommendations"].append(
            "Consider increasing tp1_rr from 1.5 to 2.0-2.5"
        )
    
    # Check if RR threshold is too high
    near_miss_count = sum(1 for r in rejections 
                         if 0 < r.get("rr_actual", 0) < r.get("rr_required", 1.5) * 1.1)
    if near_miss_count > len(rejections) * 0.2:
        analysis["root_causes"].append({
            "issue": "RR THRESHOLD TOO STRICT",
            "severity": "MEDIUM",
            "detail": f"{near_miss_count / len(rejections) * 100:.1f}% of rejections are within 10% of passing",
            "impact": "Rejecting near-profitable setups",
        })
        analysis["recommendations"].append(
            "Consider lowering min_rr from 1.5 to 1.3"
        )
    
    # Check for specific symbol patterns
    top_symbols = sorted(analysis["symbol_analysis"].items(), 
                        key=lambda x: -x[1]["count"])[:5]
    if top_symbols and top_symbols[0][1]["count"] > len(rejections) * 0.1:
        sym_name = top_symbols[0][0]
        sym_count = top_symbols[0][1]["count"]
        analysis["root_causes"].append({
            "issue": f"SYMBOL CONCENTRATION: {sym_name}",
            "severity": "MEDIUM",
            "detail": f"{sym_name} accounts for {sym_count / len(rejections) * 100:.1f}% of all rejections",
            "impact": "May indicate symbol-specific volatility issues",
        })
        analysis["recommendations"].append(
            f"Investigate {sym_name} volatility and consider symbol-specific SL adjustments"
        )
    
    return analysis


def print_report(analysis: Dict, detailed: bool = True):
    """Print formatted analysis report."""
    if "error" in analysis:
        print(f"❌ {analysis['error']}")
        return
    
    print("=" * 80)
    print("📊 RISK:REWARD AUDIT ANALYSIS REPORT")
    print(f"   Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)
    
    # ── Summary ──
    s = analysis["summary"]
    print(f"\n📈 SUMMARY ({analysis['total_rejections']} rejections)")
    print(f"   Average RR:           {s['avg_rr']:.3f}")
    print(f"   Min RR:               {s['min_rr']:.3f}")
    print(f"   Max RR:               {s['max_rr']:.3f}")
    print(f"   Median RR:            {s['median_rr']:.3f}")
    print(f"   Average SL Distance:  {s['avg_sl_dist_pct']:.2f}%")
    print(f"   Average Risk:         {s['avg_risk']:.6f}")
    print(f"   Average Reward:       {s['avg_reward']:.6f}")
    
    # ── RR Distribution ──
    print("\n📊 RR DISTRIBUTION")
    dist = analysis["rr_distribution"]
    max_count = max(dist.values()) if dist else 1
    for rr_val in sorted(dist.keys(), key=lambda x: float(x)):
        count = dist[rr_val]
        pct = count / analysis["total_rejections"] * 100
        bar_len = int(count / max_count * 30)
        bar = "█" * bar_len
        print(f"   RR={rr_val:>4s}: {count:>4d} ({pct:>5.1f}%) {bar}")
    
    # ── SL Distance Analysis ──
    print("\n📏 STOP LOSS DISTANCE ANALYSIS")
    sl_dist = analysis["sl_distance_analysis"]
    max_count = max(sl_dist.values()) if sl_dist else 1
    for bucket in sorted(sl_dist.keys(), key=lambda x: float(x.rstrip("%"))):
        count = sl_dist[bucket]
        pct = count / analysis["total_rejections"] * 100
        bar_len = int(count / max_count * 30)
        bar = "█" * bar_len
        print(f"   SL dist {bucket:>8s}: {count:>4d} ({pct:>5.1f}%) {bar}")
    
    # ── Top Rejected Symbols ──
    print("\n🔴 TOP REJECTED SYMBOLS")
    sym_analysis = analysis["symbol_analysis"]
    sorted_syms = sorted(sym_analysis.items(), key=lambda x: -x[1]["count"])[:15]
    for sym, data in sorted_syms:
        print(f"   {sym:<16s} {data['count']:>4d} rejections | "
              f"avg RR={data['avg_rr']:.2f} | avg SL dist={data['avg_sl_dist']:.2f}%")
    
    # ── By Session ──
    print("\n🕐 BY SESSION")
    for sess, count in analysis["session_analysis"].items():
        pct = count / analysis["total_rejections"] * 100
        print(f"   {sess:<20s} {count:>4d} ({pct:>5.1f}%)")
    
    # ── By Regime ──
    print("\n📊 BY REGIME")
    for regime, count in analysis["regime_analysis"].items():
        pct = count / analysis["total_rejections"] * 100
        print(f"   {regime:<20s} {count:>4d} ({pct:>5.1f}%)")
    
    # ── Root Causes ──
    if analysis["root_causes"]:
        print("\n🔍 ROOT CAUSE ANALYSIS")
        for cause in analysis["root_causes"]:
            severity_icon = "🔴" if cause["severity"] == "HIGH" else "🟡"
            print(f"\n   {severity_icon} {cause['issue']}")
            print(f"      {cause['detail']}")
            print(f"      Impact: {cause['impact']}")
    
    # ── Recommendations ──
    if analysis["recommendations"]:
        print("\n💡 RECOMMENDATIONS")
        for i, rec in enumerate(analysis["recommendations"], 1):
            print(f"   {i}. {rec}")
    
    print("\n" + "=" * 80)


def save_report(analysis: Dict, output_path: Optional[str] = None):
    """Save analysis report to file."""
    if output_path is None:
        output_path = PROJECT_ROOT / "rr_audit_report.json"
    
    # Convert defaultdict to dict for JSON serialization
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_rejections": analysis["total_rejections"],
        "summary": analysis["summary"],
        "rr_distribution": dict(analysis["rr_distribution"]),
        "sl_distance_analysis": analysis["sl_distance_analysis"],
        "symbol_analysis": analysis["symbol_analysis"],
        "session_analysis": analysis["session_analysis"],
        "regime_analysis": analysis["regime_analysis"],
        "root_causes": analysis["root_causes"],
        "recommendations": analysis["recommendations"],
    }
    
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\n💾 Report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="📊 Risk:Reward Audit Analyzer — Analyze RR rejection patterns"
    )
    parser.add_argument("--date", "-d", help="Analyze specific date (YYYY-MM-DD)")
    parser.add_argument("--last", "-n", type=int, help="Analyze last N rejections")
    parser.add_argument("--symbol", "-s", help="Analyze specific symbol")
    parser.add_argument("--report", "-r", action="store_true", help="Save full report to file")
    parser.add_argument("--output", "-o", help="Output file path for report")
    parser.add_argument("--csv-dir", help="Custom CSV directory path")
    
    args = parser.parse_args()
    
    # Load rejections
    rejections = load_rejections(
        csv_dir=args.csv_dir,
        date=args.date,
        last_n=args.last,
        symbol=args.symbol,
    )
    
    if not rejections:
        print("❌ No rejections found. The RR audit system may not have recorded any rejections yet.")
        print("   Check that the engine is running and rejecting signals for RR too low.")
        return
    
    # Analyze
    analysis = analyze_rejections(rejections)
    
    # Print report
    print_report(analysis, detailed=True)
    
    # Save if requested
    if args.report:
        save_report(analysis, args.output)


if __name__ == "__main__":
    main()
