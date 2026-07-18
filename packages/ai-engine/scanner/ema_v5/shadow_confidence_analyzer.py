"""
Shadow Confidence Analyzer — Computes optimal threshold from shadow log data.

Usage:
    python shadow_confidence_analyzer.py

Reads: data/logs/shadow_confidence.jsonl
Outputs: Threshold performance comparison table
"""
import json
from collections import defaultdict
from pathlib import Path


def analyze_shadow_log():
    log_path = Path(__file__).resolve().parent.parent / "data" / "logs" / "shadow_confidence.jsonl"
    
    if not log_path.exists():
        print("No shadow log found. Run the engine to collect data first.")
        return
    
    entries = []
    with open(log_path) as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except:
                pass
    
    if not entries:
        print("Shadow log is empty. Wait for candidates to accumulate.")
        return
    
    print(f"=== SHADOW CONFIDENCE ANALYSIS ===")
    print(f"Total candidates: {len(entries)}")
    print(f"Date range: {entries[0].get('timestamp', 0)} to {entries[-1].get('timestamp', 0)}")
    
    # Analyze each virtual threshold
    thresholds = [40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 65, 70]
    
    print(f"\n{'Threshold':>10s} {'Trades':>7s} {'Wins':>6s} {'Losses':>7s} {'WR':>6s} {'PnL':>8s} {'Avg MFE':>8s} {'Avg MAE':>8s} {'Exp R':>7s}")
    print("-" * 75)
    
    for threshold in thresholds:
        key = f"pass_{threshold}"
        passed = [e for e in entries if e.get("virtual_thresholds", {}).get(key, False)]
        
        if not passed:
            print(f"{threshold:>9d}% {'0':>7s} {'-':>6s} {'-':>7s} {'-':>6s} {'-':>8s} {'-':>8s} {'-':>8s} {'-':>7s}")
            continue
        
        # Count outcomes
        wins = sum(1 for e in passed if e.get("outcome") == "tp_hit")
        losses = sum(1 for e in passed if e.get("outcome") == "sl_hit")
        neutral = sum(1 for e in passed if e.get("outcome") is None)
        
        total_with_outcome = wins + losses
        wr = wins / total_with_outcome * 100 if total_with_outcome > 0 else 0
        
        # Compute average MFE/MAE
        mfe_values = [e.get("mfe_pct", 0) for e in passed if e.get("mfe_pct", 0) > 0]
        mae_values = [e.get("mae_pct", 0) for e in passed if e.get("mae_pct", 0) < 0]
        
        avg_mfe = sum(mfe_values) / len(mfe_values) if mfe_values else 0
        avg_mae = sum(mae_values) / len(mae_values) if mae_values else 0
        
        # Estimate PnL (simplified: TP = +1R, SL = -1R)
        rr = 1.5  # Assume 1.5R target
        total_pnl = wins * rr - losses * 1.0
        avg_exp = total_pnl / total_with_outcome if total_with_outcome > 0 else 0
        
        print(f"{threshold:>9d}% {len(passed):>7d} {wins:>6d} {losses:>7d} {wr:>5.1f}% {total_pnl:>+7.1f}R {avg_mfe:>+7.1f}% {avg_mae:>+7.1f}% {avg_exp:>+6.2f}R")
    
    # Best threshold
    print(f"\n=== RECOMMENDATION ===")
    best_threshold = None
    best_exp = -999
    
    for threshold in thresholds:
        key = f"pass_{threshold}"
        passed = [e for e in entries if e.get("virtual_thresholds", {}).get(key, False)]
        wins = sum(1 for e in passed if e.get("outcome") == "tp_hit")
        losses = sum(1 for e in passed if e.get("outcome") == "sl_hit")
        total = wins + losses
        
        if total >= 10:  # Minimum sample size
            exp = (wins * 1.5 - losses * 1.0) / total
            if exp > best_exp:
                best_exp = exp
                best_threshold = threshold
    
    if best_threshold:
        print(f"Best threshold: {best_threshold}% (expectancy: {best_exp:+.2f}R)")
    else:
        print("Insufficient data for recommendation. Need at least 10 outcomes per threshold.")


if __name__ == "__main__":
    analyze_shadow_log()
