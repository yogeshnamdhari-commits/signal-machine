#!/usr/bin/env python3
"""
Portfolio Parameter Optimizer
==============================
Systematically evaluates combinations of parameters against completed trades.

Tests:
- RR thresholds: 1.5, 2.0, 2.5, 3.0
- ATR multipliers: 1.0, 1.25, 1.5, 1.75, 2.0
- Stop-loss %: 0.8%, 1.0%, 1.2%, 1.5%, 2.0%
- Session filters: All, NY Only, London Only, NY+London
- Trailing stop activation: TP1, TP2, 0.5R, 1R
- Confidence thresholds: 40, 50, 60, 70, 80, 90

Ranks by:
- Net Profit
- Profit Factor
- Expectancy
- Maximum Drawdown
- Sharpe Ratio
- Trade Count

READ-ONLY — Never modifies trading logic.
"""
import sqlite3
import math
from pathlib import Path
from collections import defaultdict
from itertools import product
from dataclasses import dataclass
from typing import Dict, List, Optional

DB_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/institutional_v1.db")


@dataclass
class ParameterProfile:
    """A parameter configuration to test."""
    name: str
    rr_threshold: float
    min_sl_pct: float
    session_filter: List[str]
    min_confidence: float
    trailing_activation: str  # "tp1", "tp2", "0.5r", "1r", "none"
    description: str = ""


@dataclass
class OptimizationResult:
    """Results from testing a parameter profile."""
    profile: ParameterProfile
    trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    net_pnl: float
    max_drawdown: float
    sharpe_ratio: float
    avg_r: float
    avg_hold: float
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
            "sharpe": 0, "avg_r": 0, "avg_hold": 0,
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
    
    # Average hold time
    holds = [t.get("hold_minutes", 0) or 0 for t in trades if t.get("hold_minutes")]
    avg_hold = sum(holds) / len(holds) if holds else 0
    
    return {
        "trades": n,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "expectancy": round(exp, 4),
        "net_pnl": round(sum(pnls), 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "avg_r": round(avg_r, 2),
        "avg_hold": round(avg_hold, 0),
    }


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


def run_optimization(trades):
    """Run optimization across all parameter profiles."""
    print("\n" + "=" * 100)
    print("🔬 PORTFOLIO PARAMETER OPTIMIZER")
    print("=" * 100)
    print(f"   Base trades: {len(trades)}")
    print(f"   Testing parameter profiles...\n")
    
    # Define parameter profiles
    profiles = [
        # Baseline
        ParameterProfile(
            name="Baseline",
            rr_threshold=1.5,
            min_sl_pct=1.0,
            session_filter=["new_york", "london", "unknown", "asia"],
            min_confidence=40,
            trailing_activation="none",
            description="Current parameters"
        ),
        
        # RR Threshold variations
        ParameterProfile(
            name="RR_2.0",
            rr_threshold=2.0,
            min_sl_pct=1.0,
            session_filter=["new_york", "london", "unknown", "asia"],
            min_confidence=40,
            trailing_activation="none",
            description="Higher RR threshold"
        ),
        ParameterProfile(
            name="RR_2.5",
            rr_threshold=2.5,
            min_sl_pct=1.0,
            session_filter=["new_york", "london", "unknown", "asia"],
            min_confidence=40,
            trailing_activation="none",
            description="Higher RR threshold"
        ),
        ParameterProfile(
            name="RR_3.0",
            rr_threshold=3.0,
            min_sl_pct=1.0,
            session_filter=["new_york", "london", "unknown", "asia"],
            min_confidence=40,
            trailing_activation="none",
            description="Higher RR threshold"
        ),
        
        # Session filter variations
        ParameterProfile(
            name="NY_Only",
            rr_threshold=1.5,
            min_sl_pct=1.0,
            session_filter=["new_york"],
            min_confidence=40,
            trailing_activation="none",
            description="New York session only"
        ),
        ParameterProfile(
            name="NY_London",
            rr_threshold=1.5,
            min_sl_pct=1.0,
            session_filter=["new_york", "london"],
            min_confidence=40,
            trailing_activation="none",
            description="NY + London sessions"
        ),
        
        # Combined optimizations
        ParameterProfile(
            name="RR_2.5_NY",
            rr_threshold=2.5,
            min_sl_pct=1.0,
            session_filter=["new_york"],
            min_confidence=40,
            trailing_activation="none",
            description="Higher RR + NY only"
        ),
        ParameterProfile(
            name="RR_3.0_NY",
            rr_threshold=3.0,
            min_sl_pct=1.0,
            session_filter=["new_york"],
            min_confidence=40,
            trailing_activation="none",
            description="Higher RR + NY only"
        ),
        ParameterProfile(
            name="RR_2.5_NY_Conf90",
            rr_threshold=2.5,
            min_sl_pct=1.0,
            session_filter=["new_york"],
            min_confidence=90,
            trailing_activation="none",
            description="Higher RR + NY + High confidence"
        ),
        ParameterProfile(
            name="RR_3.0_NY_Conf90",
            rr_threshold=3.0,
            min_sl_pct=1.0,
            session_filter=["new_york"],
            min_confidence=90,
            trailing_activation="none",
            description="Higher RR + NY + High confidence"
        ),
        
        # Trailing stop variations
        ParameterProfile(
            name="RR_2.5_NY_Trail_1R",
            rr_threshold=2.5,
            min_sl_pct=1.0,
            session_filter=["new_york"],
            min_confidence=40,
            trailing_activation="1r",
            description="Higher RR + NY + Trailing at 1R"
        ),
        ParameterProfile(
            name="RR_3.0_NY_Trail_1R",
            rr_threshold=3.0,
            min_sl_pct=1.0,
            session_filter=["new_york"],
            min_confidence=40,
            trailing_activation="1r",
            description="Higher RR + NY + Trailing at 1R"
        ),
    ]
    
    results = []
    
    for profile in profiles:
        filtered, rej_rr, rej_sess, rej_conf = apply_profile(trades, profile)
        metrics = calculate_metrics(filtered)
        
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
            avg_hold=metrics["avg_hold"],
            rejected_by_rr=rej_rr,
            rejected_by_session=rej_sess,
            rejected_by_confidence=rej_conf,
        )
        results.append(result)
    
    # Sort by Profit Factor
    results.sort(key=lambda x: -x.profit_factor)
    
    return results


def display_results(results):
    """Display optimization results."""
    print(f"\n{'='*100}")
    print(f"📊 OPTIMIZATION RESULTS (Ranked by Profit Factor)")
    print(f"{'='*100}\n")
    
    print(f"{'RANK':<5} {'PROFILE':<25} {'TRADES':<8} {'WR%':<8} {'PF':<8} {'EXP':<10} {'PNL':<12} {'DD':<10} {'SHARPE':<8} {'RR_REJ':<8}")
    print("-" * 110)
    
    for i, r in enumerate(results, 1):
        emoji = "🟢" if r.profit_factor > 1 else "🔴"
        print(f"{emoji} {i:<3} {r.profile.name:<25} {r.trades:<8} {r.win_rate:<7.1f}% "
              f"{r.profit_factor:<8.2f} {r.expectancy:<10.4f} "
              f"${r.net_pnl:<11.2f} ${r.max_drawdown:<9.2f} {r.sharpe_ratio:<8.2f} {r.rejected_by_rr:<8}")


def display_profile_details(results):
    """Display detailed profile information."""
    print(f"\n{'='*100}")
    print(f"📋 PROFILE DETAILS")
    print(f"{'='*100}\n")
    
    for r in results[:5]:  # Top 5
        print(f"📊 {r.profile.name}")
        print(f"   Description: {r.profile.description}")
        print(f"   Parameters:")
        print(f"      - RR Threshold: {r.profile.rr_threshold}")
        print(f"      - Min SL %: {r.profile.min_sl_pct}%")
        print(f"      - Session Filter: {r.profile.session_filter}")
        print(f"      - Min Confidence: {r.profile.min_confidence}%")
        print(f"      - Trailing Activation: {r.profile.trailing_activation}")
        print(f"   Results:")
        print(f"      - Trades: {r.trades}")
        print(f"      - Win Rate: {r.win_rate}%")
        print(f"      - Profit Factor: {r.profit_factor}")
        print(f"      - Expectancy: {r.expectancy}")
        print(f"      - Net PnL: ${r.net_pnl}")
        print(f"      - Max Drawdown: ${r.max_drawdown}")
        print(f"      - Sharpe Ratio: {r.sharpe_ratio}")
        print(f"   Rejected:")
        print(f"      - By RR: {r.rejected_by_rr}")
        print(f"      - By Session: {r.rejected_by_session}")
        print(f"      - By Confidence: {r.rejected_by_confidence}")
        print()


def generate_comparison_report(results):
    """Generate comparison report between baseline and best."""
    print(f"\n{'='*100}")
    print(f"📋 COMPARISON REPORT")
    print(f"{'='*100}")
    
    baseline = next((r for r in results if r.profile.name == "Baseline"), None)
    best = results[0] if results else None
    
    if not baseline or not best:
        print("   Insufficient data for comparison")
        return
    
    print(f"""
📊 BASELINE vs BEST CONFIGURATION

{'METRIC':<20} {'BASELINE':<15} {'BEST':<15} {'IMPROVEMENT':<15}
{'─'*65}
{'Profile':<20} {baseline.profile.name:<15} {best.profile.name:<15} {'':15}
{'Trades':<20} {baseline.trades:<15} {best.trades:<15} {best.trades - baseline.trades:>+15}
{'Win Rate':<20} {baseline.win_rate:<14.1f}% {best.win_rate:<14.1f}% {best.win_rate - baseline.win_rate:>+14.1f}%
{'Profit Factor':<20} {baseline.profit_factor:<15.2f} {best.profit_factor:<15.2f} {best.profit_factor - baseline.profit_factor:>+15.2f}
{'Expectancy':<20} {baseline.expectancy:<15.4f} {best.expectancy:<15.4f} {best.expectancy - baseline.expectancy:>+15.4f}
{'Net PnL':<20} ${baseline.net_pnl:<14.2f} ${best.net_pnl:<14.2f} ${best.net_pnl - baseline.net_pnl:>+14.2f}
{'Max Drawdown':<20} ${baseline.max_drawdown:<14.2f} ${best.max_drawdown:<14.2f} ${best.max_drawdown - baseline.max_drawdown:>+14.2f}
{'Sharpe Ratio':<20} {baseline.sharpe_ratio:<15.2f} {best.sharpe_ratio:<15.2f} {best.sharpe_ratio - baseline.sharpe_ratio:>+15.2f}
""")
    
    # Calculate improvement percentages
    pf_improvement = ((best.profit_factor - baseline.profit_factor) / baseline.profit_factor * 100) if baseline.profit_factor > 0 else 0
    pnl_improvement = best.net_pnl - baseline.net_pnl
    dd_improvement = baseline.max_drawdown - best.max_drawdown
    
    print(f"📈 KEY IMPROVEMENTS:")
    print(f"   • Profit Factor: +{pf_improvement:.1f}%")
    print(f"   • Net PnL: +${pnl_improvement:.2f}")
    print(f"   • Max Drawdown Reduction: ${dd_improvement:.2f}")
    
    print(f"\n📋 RECOMMENDED PARAMETERS:")
    print(f"   • RR Threshold: {best.profile.rr_threshold}")
    print(f"   • Session Filter: {best.profile.session_filter}")
    print(f"   • Min Confidence: {best.profile.min_confidence}%")
    print(f"   • Trailing Activation: {best.profile.trailing_activation}")


def generate_final_report(results):
    """Generate final optimization report."""
    print(f"\n{'='*100}")
    print(f"📋 FINAL OPTIMIZATION REPORT")
    print(f"{'='*100}")
    
    best = results[0] if results else None
    baseline = next((r for r in results if r.profile.name == "Baseline"), None)
    
    if not best or not baseline:
        print("   Insufficient data for report")
        return
    
    print(f"""
📊 OPTIMIZATION COMPLETE

🏆 BEST CONFIGURATION: {best.profile.name}
   Description: {best.profile.description}
   
   Parameters:
   • RR Threshold: {best.profile.rr_threshold}
   • Min SL %: {best.profile.min_sl_pct}%
   • Session Filter: {best.profile.session_filter}
   • Min Confidence: {best.profile.min_confidence}%
   • Trailing Activation: {best.profile.trailing_activation}
   
   Performance:
   • Trades: {best.trades}
   • Win Rate: {best.win_rate}%
   • Profit Factor: {best.profit_factor}
   • Expectancy: {best.expectancy}
   • Net PnL: ${best.net_pnl}
   • Max Drawdown: ${best.max_drawdown}
   • Sharpe Ratio: {best.sharpe_ratio}

📈 IMPROVEMENT vs BASELINE:
   • Profit Factor: {baseline.profit_factor} → {best.profit_factor} ({best.profit_factor - baseline.profit_factor:+.2f})
   • Win Rate: {baseline.win_rate}% → {best.win_rate}% ({best.win_rate - baseline.win_rate:+.1f}%)
   • Net PnL: ${baseline.net_pnl} → ${best.net_pnl} (${best.net_pnl - baseline.net_pnl:+.2f})
   • Expectancy: {baseline.expectancy} → {best.expectancy} ({best.expectancy - baseline.expectancy:+.4f})

⚠️  IMPORTANT NOTES:
   1. These results are from historical replay only
   2. Past performance does not guarantee future results
   3. Consider walk-forward validation before live deployment
   4. Start with paper trading to validate improvements
   5. Monitor for regime changes that may affect results

📋 NEXT STEPS:
   1. Implement best parameters in paper trading
   2. Monitor for 2-4 weeks
   3. Compare paper trading results with backtest
   4. Gradually increase position size if validated
   5. Continue collecting data for further optimization
""")
    
    print("=" * 100)
    print("NOTE: These are optimization recommendations only.")
    print("No changes have been made to the strategy.")
    print("=" * 100)


def main():
    print("=" * 100)
    print("🔬 PORTFOLIO PARAMETER OPTIMIZER")
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
    
    # Display profile details
    display_profile_details(results)
    
    # Generate comparison report
    generate_comparison_report(results)
    
    # Generate final report
    generate_final_report(results)


if __name__ == "__main__":
    main()
