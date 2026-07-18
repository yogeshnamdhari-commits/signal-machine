"""
Shadow Confidence Analyzer — Computes optimal threshold from shadow log data.

Usage:
    python shadow_confidence_analyzer.py

Reads: data/logs/shadow_confidence.jsonl
Outputs: Comprehensive threshold performance comparison
"""
import json
import math
from collections import defaultdict
from pathlib import Path


def compute_metrics(trades, rr=1.5):
    """Compute comprehensive trading metrics for a set of trades."""
    if not trades:
        return None
    
    wins = [t for t in trades if t.get("outcome") == "tp_hit"]
    losses = [t for t in trades if t.get("outcome") == "sl_hit"]
    total = len(wins) + len(losses)
    
    if total == 0:
        return None
    
    # Win rate
    wr = len(wins) / total * 100
    
    # PnL (wins = +rr, losses = -1)
    win_pnl = len(wins) * rr
    loss_pnl = len(losses) * 1.0
    net_pnl = win_pnl - loss_pnl
    
    # Profit factor
    pf = win_pnl / loss_pnl if loss_pnl > 0 else float('inf')
    
    # Expectancy
    expectancy = net_pnl / total
    
    # Average R
    avg_r = expectancy
    
    # MFE/MAE
    mfe_values = [t.get("mfe_pct", 0) for t in trades if t.get("mfe_pct", 0) > 0]
    mae_values = [abs(t.get("mae_pct", 0)) for t in trades if t.get("mae_pct", 0) < 0]
    avg_mfe = sum(mfe_values) / len(mfe_values) if mfe_values else 0
    avg_mae = sum(mae_values) / len(mae_values) if mae_values else 0
    
    # Hold time
    hold_times = [t.get("hold_minutes", 0) for t in trades if t.get("hold_minutes", 0) > 0]
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0
    
    # Max drawdown (simplified: worst consecutive loss streak)
    equity = 0
    peak = 0
    max_dd = 0
    for t in trades:
        if t.get("outcome") == "tp_hit":
            equity += rr
        elif t.get("outcome") == "sl_hit":
            equity -= 1.0
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
    
    # Sharpe (simplified: mean/stdev of returns)
    returns = []
    for t in trades:
        if t.get("outcome") == "tp_hit":
            returns.append(rr)
        elif t.get("outcome") == "sl_hit":
            returns.append(-1.0)
    if len(returns) >= 2:
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        stdev = math.sqrt(variance) if variance > 0 else 1
        sharpe = mean_r / stdev if stdev > 0 else 0
    else:
        sharpe = 0
    
    # Recovery factor
    recovery = net_pnl / max_dd if max_dd > 0 else float('inf')
    
    return {
        "trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "wr": wr,
        "pf": pf,
        "expectancy": expectancy,
        "avg_r": avg_r,
        "net_pnl": net_pnl,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "recovery": recovery,
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "avg_hold": avg_hold,
    }


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
    
    print(f"╔══════════════════════════════════════════════════════════════╗")
    print(f"║         SHADOW CONFIDENCE ANALYSIS — THRESHOLD COMPARISON   ║")
    print(f"╚══════════════════════════════════════════════════════════════╝")
    print(f"\nTotal candidates: {len(entries)}")
    published = sum(1 for e in entries if e.get("published"))
    print(f"Published: {published} ({published/len(entries)*100:.1f}%)")
    
    # ═══════════════════════════════════════════════════════════════
    # SECTION 1: Threshold Comparison
    # ═══════════════════════════════════════════════════════════════
    thresholds = [40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 65, 70]
    
    print(f"\n{'═'*85}")
    print(f"  SECTION 1: THRESHOLD COMPARISON")
    print(f"{'═'*85}")
    print(f"\n{'Thresh':>6s} {'Trades':>7s} {'WR':>6s} {'PF':>6s} {'Exp R':>7s} {'MaxDD':>6s} {'Sharpe':>7s} {'Recovery':>8s} {'MFE':>6s} {'MAE':>6s} {'Hold':>6s}")
    print(f"{'─'*85}")
    
    best_threshold = None
    best_expectancy = -999
    
    for threshold in thresholds:
        key = f"pass_{threshold}"
        passed = [e for e in entries if e.get("virtual_thresholds", {}).get(key, False)]
        
        metrics = compute_metrics(passed)
        if not metrics or metrics["trades"] < 5:
            print(f"{threshold:>5d}% {'-':>7s} {'-':>6s} {'-':>6s} {'-':>7s} {'-':>6s} {'-':>7s} {'-':>8s} {'-':>6s} {'-':>6s} {'-':>6s}")
            continue
        
        pf_str = f"{metrics['pf']:.2f}" if metrics['pf'] != float('inf') else "∞"
        rec_str = f"{metrics['recovery']:.1f}" if metrics['recovery'] != float('inf') else "∞"
        
        print(f"{threshold:>5d}% {metrics['trades']:>7d} {metrics['wr']:>5.1f}% {pf_str:>6s} {metrics['expectancy']:>+6.2f}R {metrics['max_dd']:>5.1f}R {metrics['sharpe']:>+6.2f} {rec_str:>8s} {metrics['avg_mfe']:>+5.1f}% {metrics['avg_mae']:>-5.1f}% {metrics['avg_hold']:>5.0f}m")
        
        if metrics["expectancy"] > best_expectancy and metrics["trades"] >= 10:
            best_expectancy = metrics["expectancy"]
            best_threshold = threshold
    
    # ═══════════════════════════════════════════════════════════════
    # SECTION 2: Confidence Bucket Analysis
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'═'*85}")
    print(f"  SECTION 2: CONFIDENCE BUCKET ANALYSIS")
    print(f"{'═'*85}")
    
    buckets = defaultdict(list)
    for e in entries:
        conf = e.get("confidence", 0)
        b = int(conf // 5) * 5
        buckets[b].append(e)
    
    print(f"\n{'Bucket':>8s} {'Trades':>7s} {'WR':>6s} {'PF':>6s} {'Exp R':>7s} {'MFE':>6s} {'MAE':>6s}")
    print(f"{'─'*55}")
    
    for b in sorted(buckets.keys()):
        metrics = compute_metrics(buckets[b])
        if not metrics or metrics["trades"] < 3:
            continue
        
        pf_str = f"{metrics['pf']:.2f}" if metrics['pf'] != float('inf') else "∞"
        print(f"{b:>3d}-{b+4:>3d}% {metrics['trades']:>7d} {metrics['wr']:>5.1f}% {pf_str:>6s} {metrics['expectancy']:>+6.2f}R {metrics['avg_mfe']:>+5.1f}% {metrics['avg_mae']:>-5.1f}%")
    
    # ═══════════════════════════════════════════════════════════════
    # SECTION 3: Pattern/Regime Analysis
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'═'*85}")
    print(f"  SECTION 3: PATTERN ANALYSIS")
    print(f"{'═'*85}")
    
    by_pattern = defaultdict(list)
    for e in entries:
        pattern = e.get("pattern", "unknown")
        by_pattern[pattern].append(e)
    
    print(f"\n{'Pattern':>25s} {'Trades':>7s} {'WR':>6s} {'PF':>6s} {'Exp R':>7s}")
    print(f"{'─'*55}")
    
    for pattern, trades in sorted(by_pattern.items(), key=lambda x: -len(x[1])):
        metrics = compute_metrics(trades)
        if not metrics or metrics["trades"] < 3:
            continue
        
        pf_str = f"{metrics['pf']:.2f}" if metrics['pf'] != float('inf') else "∞"
        print(f"{pattern:>25s} {metrics['trades']:>7d} {metrics['wr']:>5.1f}% {pf_str:>6s} {metrics['expectancy']:>+6.2f}R")
    
    # ═══════════════════════════════════════════════════════════════
    # SECTION 4: Direction Analysis
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'═'*85}")
    print(f"  SECTION 4: LONG vs SHORT")
    print(f"{'═'*85}")
    
    by_side = defaultdict(list)
    for e in entries:
        by_side[e.get("side", "?")].append(e)
    
    print(f"\n{'Direction':>10s} {'Trades':>7s} {'WR':>6s} {'PF':>6s} {'Exp R':>7s} {'MFE':>6s} {'MAE':>6s}")
    print(f"{'─'*55}")
    
    for side, trades in by_side.items():
        metrics = compute_metrics(trades)
        if not metrics:
            continue
        
        pf_str = f"{metrics['pf']:.2f}" if metrics['pf'] != float('inf') else "∞"
        print(f"{side:>10s} {metrics['trades']:>7d} {metrics['wr']:>5.1f}% {pf_str:>6s} {metrics['expectancy']:>+6.2f}R {metrics['avg_mfe']:>+5.1f}% {metrics['avg_mae']:>-5.1f}%")
    
    # ═══════════════════════════════════════════════════════════════
    # SECTION 5: Recommendation
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'═'*85}")
    print(f"  SECTION 5: RECOMMENDATION")
    print(f"{'═'*85}")
    
    if best_threshold:
        print(f"\n  ✅ Optimal threshold: {best_threshold}%")
        print(f"  ✅ Expected expectancy: {best_expectancy:+.2f}R")
        
        # Get metrics for best threshold
        key = f"pass_{best_threshold}"
        passed = [e for e in entries if e.get("virtual_thresholds", {}).get(key, False)]
        metrics = compute_metrics(passed)
        
        if metrics:
            print(f"\n  Performance at {best_threshold}% threshold:")
            print(f"    Trades: {metrics['trades']}")
            print(f"    Win Rate: {metrics['wr']:.1f}%")
            pf_str = f"{metrics['pf']:.2f}" if metrics['pf'] != float('inf') else "∞"
            print(f"    Profit Factor: {pf_str}")
            print(f"    Expectancy: {metrics['expectancy']:+.2f}R")
            print(f"    Max Drawdown: {metrics['max_dd']:.1f}R")
            print(f"    Sharpe: {metrics['sharpe']:+.2f}")
    else:
        print(f"\n  ⚠️  Insufficient data for recommendation.")
        print(f"  Need at least 10 outcomes per threshold.")
        print(f"  Continue collecting data and re-run analysis.")
    
    print(f"\n{'═'*85}")


if __name__ == "__main__":
    analyze_shadow_log()
