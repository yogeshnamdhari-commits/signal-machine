#!/usr/bin/env python3
"""
Enhanced Portfolio Parameter Optimizer
========================================
- Composite scoring system (not just Profit Factor)
- Walk-forward validation
- Stability analysis across time periods
- Statistical significance checks

READ-ONLY — Never modifies trading logic.
"""
import sqlite3
import math
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional

DB_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/institutional_v1.db")


@dataclass
class ParameterProfile:
    """A parameter configuration to test."""
    name: str
    rr_threshold: float
    session_filter: List[str]
    min_confidence: float
    description: str = ""


@dataclass
class PeriodMetrics:
    """Metrics for a specific time period."""
    period: str
    trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    net_pnl: float
    max_drawdown: float


@dataclass
class OptimizationResult:
    """Results from testing a parameter profile."""
    profile: ParameterProfile
    # Overall metrics
    trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    net_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    avg_r: float
    # Period analysis
    period_metrics: List[PeriodMetrics]
    # Stability metrics
    profit_factor_std: float
    win_rate_std: float
    periods_profitable: int
    periods_total: int
    # Composite score
    composite_score: float
    # Rejection counts
    rejected_by_rr: int
    rejected_by_session: int
    rejected_by_confidence: int


def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def get_all_trades():
    """Get all trades from both tables."""
    conn = connect()
    rows = conn.execute("""
        SELECT * FROM positions WHERE status = 'closed' ORDER BY closed_at ASC
    """).fetchall()
    trades = [dict(r) for r in rows]
    
    rows2 = conn.execute("""
        SELECT * FROM positions_archive WHERE status = 'closed' ORDER BY closed_at ASC
    """).fetchall()
    trades.extend([dict(r) for r in rows2])
    conn.close()
    
    # Deduplicate
    seen = set()
    unique = []
    for t in trades:
        key = (t.get("symbol"), t.get("closed_at"))
        if key not in seen:
            seen.add(key)
            unique.append(t)
    
    return unique


def calculate_metrics(trades):
    """Calculate portfolio-level metrics."""
    if not trades:
        return {
            "trades": 0, "win_rate": 0, "profit_factor": 0,
            "expectancy": 0, "net_pnl": 0, "max_drawdown": 0,
            "sharpe": 0, "avg_r": 0,
        }
    
    pnls = [t.get("pnl", 0) or 0 for t in trades]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    
    wr = len(wins) / n * 100 if n else 0
    gp = sum(wins) if wins else 0
    gl = sum(losses) if losses else 0
    pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
    
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)
    
    # Max Drawdown
    cum = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    
    # Sharpe Ratio
    if n > 1:
        mean_pnl = sum(pnls) / n
        std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1))
        sharpe = (mean_pnl / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0
    else:
        sharpe = 0
    
    # Average R
    rs = [t.get("realized_r", 0) or 0 for t in trades if t.get("realized_r")]
    avg_r = sum(rs) / len(rs) if rs else 0
    
    return {
        "trades": n,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "expectancy": round(exp, 4),
        "net_pnl": round(sum(pnls), 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "avg_r": round(avg_r, 2),
    }


def split_into_periods(trades, period_days=7):
    """Split trades into time periods for stability analysis."""
    if not trades:
        return {}
    
    # Sort by closed_at
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at", 0) or 0)
    
    # Find time range
    first_time = sorted_trades[0].get("closed_at", 0) or 0
    last_time = sorted_trades[-1].get("closed_at", 0) or 0
    
    if first_time == 0 or last_time == 0:
        return {}
    
    # Split into periods
    period_seconds = period_days * 24 * 3600
    periods = defaultdict(list)
    
    for t in trades:
        closed_at = t.get("closed_at", 0) or 0
        if closed_at > 0:
            period_idx = int((closed_at - first_time) / period_seconds)
            period_label = f"Period_{period_idx}"
            periods[period_label].append(t)
    
    return periods


def calculate_composite_score(metrics, period_metrics, trades_count):
    """
    Calculate a composite optimization score.
    
    Weights:
    - Profit Factor: 25%
    - Expectancy: 20%
    - Sharpe Ratio: 20%
    - Win Rate: 10%
    - Stability (periods profitable): 15%
    - Trade count (penalty for too few): 10%
    """
    if trades_count < 10:
        return 0  # Too few trades
    
    # Normalize each metric to 0-1 scale
    pf_score = min(metrics["profit_factor"] / 3.0, 1.0) if metrics["profit_factor"] > 0 else 0
    exp_score = min(max(metrics["expectancy"], 0) / 5.0, 1.0)
    sharpe_score = min(max(metrics["sharpe"], 0) / 5.0, 1.0)
    wr_score = metrics["win_rate"] / 100.0
    
    # Stability score (what % of periods were profitable)
    if period_metrics:
        profitable_periods = sum(1 for p in period_metrics if p.net_pnl > 0)
        stability_score = profitable_periods / len(period_metrics)
    else:
        stability_score = 0
    
    # Trade count score (penalty for too few, diminishing returns for more)
    # Optimal is around 50-100 trades
    if trades_count < 20:
        count_score = trades_count / 20.0 * 0.5
    elif trades_count < 50:
        count_score = 0.5 + (trades_count - 20) / 30.0 * 0.3
    else:
        count_score = 0.8 + min((trades_count - 50) / 100.0, 1.0) * 0.2
    
    # Weighted composite
    composite = (
        pf_score * 0.25 +
        exp_score * 0.20 +
        sharpe_score * 0.20 +
        wr_score * 0.10 +
        stability_score * 0.15 +
        count_score * 0.10
    )
    
    return round(composite * 100, 1)  # Scale to 0-100


def apply_profile(trades, profile):
    """Apply a parameter profile to filter trades."""
    filtered = []
    rejected_rr = 0
    rejected_session = 0
    rejected_confidence = 0
    
    for t in trades:
        # RR filter
        rr = t.get("risk_reward", 0) or t.get("planned_rr", 0) or 0
        if rr < profile.rr_threshold:
            rejected_rr += 1
            continue
        
        # Session filter
        session = t.get("session", "unknown") or "unknown"
        if session not in profile.session_filter:
            rejected_session += 1
            continue
        
        # Confidence filter
        conf = (t.get("confidence", 0) or 0) * 100
        if conf < profile.min_confidence:
            rejected_confidence += 1
            continue
        
        filtered.append(t)
    
    return filtered, rejected_rr, rejected_session, rejected_confidence


def run_walk_forward_validation(trades, profile, train_pct=0.7):
    """Run walk-forward validation: optimize on train, validate on test."""
    # Sort by time
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at", 0) or 0)
    
    # Split into train/test
    split_idx = int(len(sorted_trades) * train_pct)
    train_trades = sorted_trades[:split_idx]
    test_trades = sorted_trades[split_idx:]
    
    # Apply profile to both
    train_filtered, _, _, _ = apply_profile(train_trades, profile)
    test_filtered, _, _, _ = apply_profile(test_trades, profile)
    
    # Calculate metrics for both
    train_metrics = calculate_metrics(train_filtered)
    test_metrics = calculate_metrics(test_filtered)
    
    return {
        "train": {
            "trades": len(train_filtered),
            "metrics": train_metrics,
        },
        "test": {
            "trades": len(test_filtered),
            "metrics": test_metrics,
        },
        "overfitting_score": calculate_overfitting_score(train_metrics, test_metrics),
    }


def calculate_overfitting_score(train_metrics, test_metrics):
    """
    Calculate overfitting score.
    Lower is better (less overfitting).
    """
    if train_metrics["trades"] < 5 or test_metrics["trades"] < 5:
        return 1.0  # Can't assess
    
    # Compare key metrics
    pf_diff = abs(train_metrics["profit_factor"] - test_metrics["profit_factor"])
    wr_diff = abs(train_metrics["win_rate"] - test_metrics["win_rate"])
    exp_diff = abs(train_metrics["expectancy"] - test_metrics["expectancy"])
    
    # Normalize
    pf_score = min(pf_diff / 2.0, 1.0)
    wr_score = min(wr_diff / 20.0, 1.0)
    exp_score = min(abs(exp_diff) / 3.0, 1.0)
    
    return round((pf_score + wr_score + exp_score) / 3, 2)


def run_optimization(trades):
    """Run optimization across all parameter profiles."""
    print("\n" + "=" * 100)
    print("🔬 ENHANCED PORTFOLIO PARAMETER OPTIMIZER")
    print("=" * 100)
    print(f"   Base trades: {len(trades)}")
    print(f"   Testing parameter profiles...\n")
    
    # Define parameter profiles
    profiles = [
        # Baseline
        ParameterProfile(
            name="Baseline",
            rr_threshold=1.5,
            session_filter=["new_york", "london", "unknown", "asia"],
            min_confidence=40,
            description="Current parameters"
        ),
        
        # RR Threshold variations
        ParameterProfile(name="RR_2.0", rr_threshold=2.0, session_filter=["new_york", "london", "unknown", "asia"], min_confidence=40, description="Higher RR threshold"),
        ParameterProfile(name="RR_2.5", rr_threshold=2.5, session_filter=["new_york", "london", "unknown", "asia"], min_confidence=40, description="Higher RR threshold"),
        ParameterProfile(name="RR_3.0", rr_threshold=3.0, session_filter=["new_york", "london", "unknown", "asia"], min_confidence=40, description="Higher RR threshold"),
        
        # Session filter variations
        ParameterProfile(name="NY_Only", rr_threshold=1.5, session_filter=["new_york"], min_confidence=40, description="New York session only"),
        ParameterProfile(name="NY_London", rr_threshold=1.5, session_filter=["new_york", "london"], min_confidence=40, description="NY + London sessions"),
        
        # Combined optimizations
        ParameterProfile(name="RR_2.5_NY", rr_threshold=2.5, session_filter=["new_york"], min_confidence=40, description="Higher RR + NY only"),
        ParameterProfile(name="RR_3.0_NY", rr_threshold=3.0, session_filter=["new_york"], min_confidence=40, description="Higher RR + NY only"),
        ParameterProfile(name="RR_2.5_NY_Conf90", rr_threshold=2.5, session_filter=["new_york"], min_confidence=90, description="Higher RR + NY + High confidence"),
        ParameterProfile(name="RR_3.0_NY_Conf90", rr_threshold=3.0, session_filter=["new_york"], min_confidence=90, description="Higher RR + NY + High confidence"),
    ]
    
    results = []
    
    for profile in profiles:
        filtered, rej_rr, rej_sess, rej_conf = apply_profile(trades, profile)
        metrics = calculate_metrics(filtered)
        
        # Period analysis
        periods = split_into_periods(filtered, period_days=7)
        period_metrics = []
        for period_label, period_trades in sorted(periods.items()):
            pm = calculate_metrics(period_trades)
            period_metrics.append(PeriodMetrics(
                period=period_label,
                trades=pm["trades"],
                win_rate=pm["win_rate"],
                profit_factor=pm["profit_factor"],
                expectancy=pm["expectancy"],
                net_pnl=pm["net_pnl"],
                max_drawdown=pm["max_drawdown"],
            ))
        
        # Stability metrics
        pf_values = [p.profit_factor for p in period_metrics if p.trades > 0]
        wr_values = [p.win_rate for p in period_metrics if p.trades > 0]
        
        pf_std = math.sqrt(sum((x - sum(pf_values)/len(pf_values))**2 for x in pf_values) / len(pf_values)) if len(pf_values) > 1 else 0
        wr_std = math.sqrt(sum((x - sum(wr_values)/len(wr_values))**2 for x in wr_values) / len(wr_values)) if len(wr_values) > 1 else 0
        
        profitable_periods = sum(1 for p in period_metrics if p.net_pnl > 0)
        
        # Composite score
        composite = calculate_composite_score(metrics, period_metrics, len(filtered))
        
        # Walk-forward validation
        wf = run_walk_forward_validation(trades, profile)
        
        result = OptimizationResult(
            profile=profile,
            trades=metrics["trades"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            expectancy=metrics["expectancy"],
            net_pnl=metrics["net_pnl"],
            max_drawdown=metrics["max_drawdown"],
            sharpe_ratio=metrics["sharpe"],
            avg_r=metrics["avg_r"],
            period_metrics=period_metrics,
            profit_factor_std=round(pf_std, 2),
            win_rate_std=round(wr_std, 2),
            periods_profitable=profitable_periods,
            periods_total=len(period_metrics),
            composite_score=composite,
            rejected_by_rr=rej_rr,
            rejected_by_session=rej_sess,
            rejected_by_confidence=rej_conf,
        )
        results.append(result)
    
    # Sort by composite score
    results.sort(key=lambda x: -x.composite_score)
    
    return results


def display_results(results):
    """Display optimization results with composite scores."""
    print(f"\n{'='*120}")
    print(f"📊 OPTIMIZATION RESULTS (Ranked by Composite Score)")
    print(f"{'='*120}\n")
    
    print(f"{'RANK':<5} {'PROFILE':<25} {'TRADES':<8} {'WR%':<8} {'PF':<8} {'EXP':<10} {'PNL':<12} {'DD':<10} {'SHARPE':<8} {'STABLE':<8} {'SCORE':<8}")
    print("-" * 120)
    
    for i, r in enumerate(results, 1):
        emoji = "🟢" if r.composite_score > 50 else ("🟡" if r.composite_score > 30 else "🔴")
        stability = f"{r.periods_profitable}/{r.periods_total}"
        print(f"{emoji} {i:<3} {r.profile.name:<25} {r.trades:<8} {r.win_rate:<7.1f}% "
              f"{r.profit_factor:<8.2f} {r.expectancy:<10.4f} "
              f"${r.net_pnl:<11.2f} ${r.max_drawdown:<9.2f} {r.sharpe_ratio:<8.2f} "
              f"{stability:<8} {r.composite_score:<8.1f}")


def display_stability_analysis(results):
    """Display stability analysis for top configurations."""
    print(f"\n{'='*100}")
    print(f"📊 STABILITY ANALYSIS (Top 5 Configurations)")
    print(f"{'='*100}\n")
    
    for r in results[:5]:
        print(f"📊 {r.profile.name}")
        print(f"   Composite Score: {r.composite_score}")
        print(f"   Profitable Periods: {r.periods_profitable}/{r.periods_total}")
        print(f"   PF Std Dev: {r.profit_factor_std}")
        print(f"   WR Std Dev: {r.win_rate_std}")
        
        if r.period_metrics:
            print(f"   Period Breakdown:")
            for p in r.period_metrics[:5]:  # Show first 5 periods
                emoji = "🟢" if p.net_pnl > 0 else "🔴"
                print(f"      {emoji} {p.period}: Trades={p.trades}, WR={p.win_rate}%, PF={p.profit_factor}, PnL=${p.net_pnl}")
        print()


def display_walk_forward_results(trades, results):
    """Display walk-forward validation results."""
    print(f"\n{'='*100}")
    print(f"📊 WALK-FORWARD VALIDATION")
    print(f"{'='*100}\n")
    
    print(f"{'PROFILE':<25} {'TRAIN':<15} {'TEST':<15} {'OVERFIT':<10} {'VALIDATED':<10}")
    print("-" * 75)
    
    for r in results[:10]:
        wf = run_walk_forward_validation(trades, r.profile)
        
        train_pf = wf["train"]["metrics"]["profit_factor"]
        test_pf = wf["test"]["metrics"]["profit_factor"]
        overfit = wf["overfitting_score"]
        
        # Validate: test should be similar to train
        validated = "✅" if overfit < 0.3 and test_pf > 1.0 else "⚠️"
        
        print(f"{r.profile.name:<25} PF={train_pf:<10.2f} PF={test_pf:<10.2f} {overfit:<10.2f} {validated}")


def generate_final_report(results, trades):
    """Generate final optimization report."""
    print(f"\n{'='*100}")
    print(f"📋 FINAL OPTIMIZATION REPORT")
    print(f"{'='*100}")
    
    best = results[0] if results else None
    baseline = next((r for r in results if r.profile.name == "Baseline"), None)
    
    if not best or not baseline:
        print("   Insufficient data for report")
        return
    
    # Walk-forward validation for best
    wf = run_walk_forward_validation(trades, best.profile)
    
    print(f"""
🏆 BEST CONFIGURATION: {best.profile.name}
   Description: {best.profile.description}
   
   Parameters:
   • RR Threshold: {best.profile.rr_threshold}
   • Session Filter: {best.profile.session_filter}
   • Min Confidence: {best.profile.min_confidence}%
   
   Performance:
   • Composite Score: {best.composite_score}/100
   • Trades: {best.trades}
   • Win Rate: {best.win_rate}%
   • Profit Factor: {best.profit_factor}
   • Expectancy: {best.expectancy}
   • Net PnL: ${best.net_pnl}
   • Max Drawdown: ${best.max_drawdown}
   • Sharpe Ratio: {best.sharpe_ratio}
   
   Stability:
   • Profitable Periods: {best.periods_profitable}/{best.periods_total}
   • PF Std Dev: {best.profit_factor_std}
   • WR Std Dev: {best.win_rate_std}
   
   Walk-Forward Validation:
   • Train PF: {wf["train"]["metrics"]["profit_factor"]}
   • Test PF: {wf["test"]["metrics"]["profit_factor"]}
   • Overfitting Score: {wf["overfitting_score"]}
   • Validated: {"✅" if wf["overfitting_score"] < 0.3 and wf["test"]["metrics"]["profit_factor"] > 1.0 else "⚠️"}

📈 IMPROVEMENT vs BASELINE:
   • Composite Score: {baseline.composite_score} → {best.composite_score} (+{best.composite_score - baseline.composite_score:.1f})
   • Profit Factor: {baseline.profit_factor} → {best.profit_factor} ({best.profit_factor - baseline.profit_factor:+.2f})
   • Win Rate: {baseline.win_rate}% → {best.win_rate}% ({best.win_rate - baseline.win_rate:+.1f}%)
   • Net PnL: ${baseline.net_pnl} → ${best.net_pnl} (${best.net_pnl - baseline.net_pnl:+.2f})
   • Stability: {baseline.periods_profitable}/{baseline.periods_total} → {best.periods_profitable}/{best.periods_total}

⚠️  STATISTICAL VALIDITY:
   • Sample Size: {best.trades} trades (need 200+ for high confidence)
   • Walk-Forward: {"PASSED" if wf["overfitting_score"] < 0.3 else "NEEDS MORE DATA"}
   • Period Stability: {best.periods_profitable}/{best.periods_total} profitable periods

📋 RECOMMENDATION:
   {"✅ Configuration shows good statistical properties" if best.composite_score > 50 and wf["overfitting_score"] < 0.3 else "⚠️  Need more data before promoting to production"}
   
   Next Steps:
   1. Continue collecting trades to reach 200+ sample
   2. Run paper trading with best configuration
   3. Monitor for 2-4 weeks
   4. Re-run optimizer with larger dataset
   5. Only then consider live deployment
""")
    
    print("=" * 100)
    print("NOTE: These are optimization recommendations only.")
    print("No changes have been made to the strategy.")
    print("=" * 100)


def main():
    print("=" * 100)
    print("🔬 ENHANCED PORTFOLIO PARAMETER OPTIMIZER")
    print("=" * 100)
    
    trades = get_all_trades()
    print(f"\n📊 Loaded {len(trades)} total trades")
    
    if len(trades) < 20:
        print("❌ Not enough trades for meaningful optimization")
        return
    
    # Run optimization
    results = run_optimization(trades)
    
    # Display results
    display_results(results)
    
    # Display stability analysis
    display_stability_analysis(results)
    
    # Display walk-forward results
    display_walk_forward_results(trades, results)
    
    # Generate final report
    generate_final_report(results, trades)


if __name__ == "__main__":
    main()
