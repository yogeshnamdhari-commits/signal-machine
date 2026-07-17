#!/usr/bin/env python3
"""
Production Governance Layer
=============================
- Deployment maturity levels (Level 0-5)
- Stability monitoring
- Automatic rollback protection
- Configuration lifecycle management

READ-ONLY — Never modifies trading logic.
"""
import sqlite3
import json
import time
import math
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timezone

DB_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/institutional_v1.db")
GOVERNANCE_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/governance")


@dataclass
class DeploymentLevel:
    """Deployment maturity level."""
    level: int
    name: str
    meaning: str
    action: str
    requirements: List[str]


@dataclass
class StabilityMetrics:
    """Stability metrics for a configuration."""
    trades: int
    win_rate_trend: List[float]
    profit_factor_trend: List[float]
    drawdown_trend: List[float]
    sharpe_trend: List[float]
    monthly_expectancy: List[float]
    confidence_interval: Dict[str, float]
    drift_detected: bool
    drift_score: float


@dataclass
class RollbackTrigger:
    """Automatic rollback trigger conditions."""
    min_profit_factor: float = 1.2
    max_drawdown: float = 200.0
    min_expectancy: float = 0.0
    min_win_rate: float = 30.0
    max_drift_score: float = 0.5


@dataclass
class ConfigurationStatus:
    """Status of a configuration."""
    name: str
    deployment_level: DeploymentLevel
    stability: StabilityMetrics
    rollback_triggers: RollbackTrigger
    is_active: bool
    activated_at: Optional[float]
    suspended_at: Optional[float]
    suspension_reason: Optional[str]


# Deployment levels
DEPLOYMENT_LEVELS = [
    DeploymentLevel(
        level=0,
        name="Experimental",
        meaning="Internal testing only",
        action="No live trading",
        requirements=["Configuration created"],
    ),
    DeploymentLevel(
        level=1,
        name="Research Validated",
        meaning="Backtesting complete",
        action="Internal review",
        requirements=["Passes backtest", "Overfitting score < 0.5"],
    ),
    DeploymentLevel(
        level=2,
        name="Paper Trading",
        meaning="Forward test only",
        action="Paper trading for 2-4 weeks",
        requirements=["Passes walk-forward", "Sample size ≥ 50"],
    ),
    DeploymentLevel(
        level=3,
        name="Production Candidate",
        meaning="Manual approval required",
        action="Human review and approval",
        requirements=["Sample size ≥ 200", "PF > 1.5", "All validation gates pass"],
    ),
    DeploymentLevel(
        level=4,
        name="Production Approved",
        meaning="Live deployment",
        action="Deploy with monitoring",
        requirements=["Level 3 complete", "Manual approval", "Rollback triggers set"],
    ),
    DeploymentLevel(
        level=5,
        name="Long-term Verified",
        meaning="Stable over months",
        action="Full production",
        requirements=["Level 4 stable for 3+ months", "No rollbacks triggered"],
    ),
]


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
            "sharpe": 0,
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
    
    return {
        "trades": n,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "expectancy": round(exp, 4),
        "net_pnl": round(sum(pnls), 2),
        "max_drawdown": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
    }


def calculate_stability_metrics(trades, period_days=7):
    """Calculate stability metrics over time."""
    if not trades:
        return StabilityMetrics(
            trades=0,
            win_rate_trend=[],
            profit_factor_trend=[],
            drawdown_trend=[],
            sharpe_trend=[],
            monthly_expectancy=[],
            confidence_interval={"lower": 0, "upper": 0},
            drift_detected=False,
            drift_score=0,
        )
    
    # Sort by time
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at", 0) or 0)
    
    # Split into periods
    period_seconds = period_days * 24 * 3600
    first_time = sorted_trades[0].get("closed_at", 0) or 0
    periods = defaultdict(list)
    
    for t in sorted_trades:
        closed_at = t.get("closed_at", 0) or 0
        if closed_at > 0:
            period_idx = int((closed_at - first_time) / period_seconds)
            periods[period_idx].append(t)
    
    # Calculate metrics for each period
    win_rates = []
    profit_factors = []
    drawdowns = []
    sharpes = []
    expectancies = []
    
    for period_idx in sorted(periods.keys()):
        period_trades = periods[period_idx]
        metrics = calculate_metrics(period_trades)
        
        win_rates.append(metrics["win_rate"])
        profit_factors.append(metrics["profit_factor"])
        drawdowns.append(metrics["max_drawdown"])
        sharpes.append(metrics["sharpe"])
        expectancies.append(metrics["expectancy"])
    
    # Calculate drift score (simplified)
    if len(profit_factors) > 1:
        mean_pf = sum(profit_factors) / len(profit_factors)
        std_pf = math.sqrt(sum((x - mean_pf)**2 for x in profit_factors) / len(profit_factors))
        drift_score = std_pf / mean_pf if mean_pf > 0 else 1
        drift_detected = drift_score > 0.5
    else:
        drift_score = 0
        drift_detected = False
    
    # Confidence interval (simplified)
    pnls = [t.get("pnl", 0) or 0 for t in trades]
    if len(pnls) > 1:
        mean_pnl = sum(pnls) / len(pnls)
        std_pnl = math.sqrt(sum((p - mean_pnl)**2 for p in pnls) / (len(pnls) - 1))
        ci_lower = mean_pnl - 1.96 * std_pnl / math.sqrt(len(pnls))
        ci_upper = mean_pnl + 1.96 * std_pnl / math.sqrt(len(pnls))
    else:
        ci_lower = 0
        ci_upper = 0
    
    return StabilityMetrics(
        trades=len(trades),
        win_rate_trend=win_rates,
        profit_factor_trend=profit_factors,
        drawdown_trend=drawdowns,
        sharpe_trend=sharpes,
        monthly_expectancy=expectancies,
        confidence_interval={"lower": round(ci_lower, 4), "upper": round(ci_upper, 4)},
        drift_detected=drift_detected,
        drift_score=round(drift_score, 3),
    )


def check_rollback_triggers(metrics, triggers):
    """Check if rollback triggers are activated."""
    reasons = []
    
    if metrics["profit_factor"] < triggers.min_profit_factor:
        reasons.append(f"PF {metrics['profit_factor']:.2f} < {triggers.min_profit_factor}")
    
    if metrics["max_drawdown"] > triggers.max_drawdown:
        reasons.append(f"DD ${metrics['max_drawdown']:.0f} > ${triggers.max_drawdown:.0f}")
    
    if metrics["expectancy"] < triggers.min_expectancy:
        reasons.append(f"Expectancy {metrics['expectancy']:.4f} < {triggers.min_expectancy}")
    
    if metrics["win_rate"] < triggers.min_win_rate:
        reasons.append(f"WR {metrics['win_rate']:.1f}% < {triggers.min_win_rate}%")
    
    return reasons


def determine_deployment_level(metrics, stability, sample_size):
    """Determine the deployment level for a configuration."""
    
    # Level 5: Long-term verified (stable for months)
    if (sample_size >= 500 and
        metrics["profit_factor"] > 1.5 and
        not stability.drift_detected and
        len(stability.profit_factor_trend) >= 12):  # 12+ periods = ~3 months
        return DEPLOYMENT_LEVELS[5]
    
    # Level 4: Production approved
    if (sample_size >= 200 and
        metrics["profit_factor"] > 1.5 and
        metrics["max_drawdown"] < 200 and
        not stability.drift_detected):
        return DEPLOYMENT_LEVELS[4]
    
    # Level 3: Production candidate
    if (sample_size >= 200 and
        metrics["profit_factor"] > 1.2 and
        metrics["max_drawdown"] < 300):
        return DEPLOYMENT_LEVELS[3]
    
    # Level 2: Paper trading
    if (sample_size >= 50 and
        metrics["profit_factor"] > 1.0):
        return DEPLOYMENT_LEVELS[2]
    
    # Level 1: Research validated
    if sample_size >= 20:
        return DEPLOYMENT_LEVELS[1]
    
    # Level 0: Experimental
    return DEPLOYMENT_LEVELS[0]


def display_deployment_levels():
    """Display all deployment levels."""
    print(f"\n{'='*80}")
    print(f"📊 DEPLOYMENT MATURITY LEVELS")
    print(f"{'='*80}\n")
    
    for level in DEPLOYMENT_LEVELS:
        print(f"Level {level.level}: {level.name}")
        print(f"   Meaning: {level.meaning}")
        print(f"   Action: {level.action}")
        print(f"   Requirements: {', '.join(level.requirements)}")
        print()


def display_configuration_status(config):
    """Display configuration status."""
    print(f"\n{'='*80}")
    print(f"📊 CONFIGURATION STATUS: {config.name}")
    print(f"{'='*80}")
    
    # Deployment level
    level = config.deployment_level
    print(f"\n🎯 DEPLOYMENT LEVEL: {level.level} - {level.name}")
    print(f"   Meaning: {level.meaning}")
    print(f"   Action: {level.action}")
    
    # Stability metrics
    stability = config.stability
    print(f"\n📈 STABILITY METRICS:")
    print(f"   Trades: {stability.trades}")
    print(f"   Drift Detected: {'⚠️  YES' if stability.drift_detected else '✅ NO'}")
    print(f"   Drift Score: {stability.drift_score}")
    print(f"   Confidence Interval: ${stability.confidence_interval['lower']:.4f} - ${stability.confidence_interval['upper']:.4f}")
    
    # Trends
    if stability.profit_factor_trend:
        print(f"\n📊 TRENDS (Last {len(stability.profit_factor_trend)} periods):")
        print(f"   Win Rate: {stability.win_rate_trend[-1]:.1f}% (trend: {'↑' if len(stability.win_rate_trend) > 1 and stability.win_rate_trend[-1] > stability.win_rate_trend[-2] else '↓'})")
        print(f"   Profit Factor: {stability.profit_factor_trend[-1]:.2f} (trend: {'↑' if len(stability.profit_factor_trend) > 1 and stability.profit_factor_trend[-1] > stability.profit_factor_trend[-2] else '↓'})")
        print(f"   Max Drawdown: ${stability.drawdown_trend[-1]:.2f} (trend: {'↑' if len(stability.drawdown_trend) > 1 and stability.drawdown_trend[-1] > stability.drawdown_trend[-2] else '↓'})")
        print(f"   Sharpe: {stability.sharpe_trend[-1]:.2f} (trend: {'↑' if len(stability.sharpe_trend) > 1 and stability.sharpe_trend[-1] > stability.sharpe_trend[-2] else '↓'})")
    
    # Rollback triggers
    print(f"\n🔄 ROLLBACK TRIGGERS:")
    print(f"   Min Profit Factor: {config.rollback_triggers.min_profit_factor}")
    print(f"   Max Drawdown: ${config.rollback_triggers.max_drawdown}")
    print(f"   Min Expectancy: {config.rollback_triggers.min_expectancy}")
    print(f"   Min Win Rate: {config.rollback_triggers.min_win_rate}%")
    
    # Status
    print(f"\n📋 STATUS:")
    print(f"   Active: {'✅ YES' if config.is_active else '❌ NO'}")
    if config.activated_at:
        print(f"   Activated: {datetime.fromtimestamp(config.activated_at, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    if config.suspended_at:
        print(f"   Suspended: {datetime.fromtimestamp(config.suspended_at, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}")
        print(f"   Reason: {config.suspension_reason}")


def display_stability_dashboard(config):
    """Display stability dashboard."""
    stability = config.stability
    
    print(f"\n{'='*80}")
    print(f"📊 STABILITY DASHBOARD: {config.name}")
    print(f"{'='*80}")
    
    if not stability.profit_factor_trend:
        print("   No trend data available")
        return
    
    # Filter out NaN values
    def is_valid(x):
        if x is None:
            return False
        try:
            return not math.isnan(float(x))
        except (ValueError, TypeError):
            return False
    
    valid_pf = [x for x in stability.profit_factor_trend if is_valid(x)]
    valid_wr = [x for x in stability.win_rate_trend if is_valid(x)]
    valid_dd = [x for x in stability.drawdown_trend if is_valid(x)]
    
    if not valid_pf:
        print("   No valid trend data available")
        return
    
    # Display trends as ASCII charts
    print(f"\n📈 PROFIT FACTOR TREND:")
    max_pf = max(valid_pf) if valid_pf else 1
    for i, pf in enumerate(stability.profit_factor_trend):
        if not is_valid(pf):
            print(f"   Period {i}: ⚪ N/A")
            continue
        try:
            pf_val = float(pf)
            bar_len = int(pf_val / max_pf * 30) if max_pf > 0 else 0
            bar = "█" * bar_len
            emoji = "🟢" if pf_val > 1 else "🔴"
            print(f"   Period {i}: {emoji} {pf_val:.2f} {bar}")
        except (ValueError, TypeError):
            print(f"   Period {i}: ⚪ N/A")
    
    print(f"\n📈 WIN RATE TREND:")
    max_wr = max(valid_wr) if valid_wr else 100
    for i, wr in enumerate(stability.win_rate_trend):
        if not is_valid(wr):
            print(f"   Period {i}: ⚪ N/A")
            continue
        try:
            wr_val = float(wr)
            bar_len = int(wr_val / max_wr * 30) if max_wr > 0 else 0
            bar = "█" * bar_len
            emoji = "🟢" if wr_val > 40 else "🔴"
            print(f"   Period {i}: {emoji} {wr_val:.1f}% {bar}")
        except (ValueError, TypeError):
            print(f"   Period {i}: ⚪ N/A")
    
    print(f"\n📈 DRAWDOWN TREND:")
    max_dd = max(valid_dd) if valid_dd else 1
    for i, dd in enumerate(stability.drawdown_trend):
        if not is_valid(dd):
            print(f"   Period {i}: ⚪ N/A")
            continue
        try:
            dd_val = float(dd)
            bar_len = int(dd_val / max_dd * 30) if max_dd > 0 else 0
            bar = "█" * bar_len
            emoji = "🟢" if dd_val < 100 else "🔴"
            print(f"   Period {i}: {emoji} ${dd_val:.2f} {bar}")
        except (ValueError, TypeError):
            print(f"   Period {i}: ⚪ N/A")


def run_governance_analysis(trades):
    """Run governance analysis on all configurations."""
    print("\n" + "=" * 100)
    print("🔬 PRODUCTION GOVERNANCE LAYER")
    print("=" * 100)
    print(f"   Total trades: {len(trades)}")
    
    # Define configurations to analyze
    configs = [
        {"name": "Baseline", "rr_threshold": 1.5, "session_filter": ["new_york", "london", "unknown", "asia"], "min_confidence": 40},
        {"name": "RR_2.5_NY", "rr_threshold": 2.5, "session_filter": ["new_york"], "min_confidence": 40},
        {"name": "RR_3.0_NY", "rr_threshold": 3.0, "session_filter": ["new_york"], "min_confidence": 40},
    ]
    
    results = []
    
    for config in configs:
        # Filter trades
        filtered = []
        for t in trades:
            rr = t.get("risk_reward", 0) or t.get("planned_rr", 0) or 0
            if rr < config["rr_threshold"]:
                continue
            session = t.get("session", "unknown") or "unknown"
            if session not in config["session_filter"]:
                continue
            conf = (t.get("confidence", 0) or 0) * 100
            if conf < config["min_confidence"]:
                continue
            filtered.append(t)
        
        # Calculate metrics
        metrics = calculate_metrics(filtered)
        
        # Calculate stability
        stability = calculate_stability_metrics(filtered)
        
        # Determine deployment level
        deployment_level = determine_deployment_level(metrics, stability, len(filtered))
        
        # Check rollback triggers
        triggers = RollbackTrigger()
        rollback_reasons = check_rollback_triggers(metrics, triggers)
        
        # Create configuration status
        config_status = ConfigurationStatus(
            name=config["name"],
            deployment_level=deployment_level,
            stability=stability,
            rollback_triggers=triggers,
            is_active=False,
            activated_at=None,
            suspended_at=None,
            suspension_reason=None,
        )
        
        results.append({
            "config": config,
            "metrics": metrics,
            "stability": stability,
            "deployment_level": deployment_level,
            "rollback_reasons": rollback_reasons,
            "status": config_status,
        })
    
    # Display deployment levels
    display_deployment_levels()
    
    # Display summary
    print(f"\n{'='*100}")
    print(f"📊 CONFIGURATION SUMMARY")
    print(f"{'='*100}\n")
    
    print(f"{'CONFIG':<20} {'TRADES':<8} {'WR%':<8} {'PF':<8} {'PNL':<12} {'LEVEL':<8} {'STATUS':<15}")
    print("-" * 85)
    
    for r in results:
        m = r["metrics"]
        level = r["deployment_level"]
        
        emoji = "🟢" if level.level >= 4 else ("🟡" if level.level >= 2 else "🔴")
        
        print(f"{emoji} {r['config']['name']:<18} {m['trades']:<8} {m['win_rate']:<7.1f}% "
              f"{m['profit_factor']:<8.2f} ${m['net_pnl']:<11.2f} "
              f"L{level.level:<7} {level.name}")
    
    # Display detailed status for each configuration
    for r in results:
        display_configuration_status(r["status"])
        display_stability_dashboard(r["status"])
    
    # Display final recommendation
    print(f"\n{'='*100}")
    print(f"📋 FINAL GOVERNANCE RECOMMENDATION")
    print(f"{'='*100}")
    
    # Find best configuration
    best = max(results, key=lambda r: r["deployment_level"].level)
    
    print(f"""
🏆 BEST CONFIGURATION: {best['config']['name']}
   
   Deployment Level: {best['deployment_level'].level} - {best['deployment_level'].name}
   Meaning: {best['deployment_level'].meaning}
   Action: {best['deployment_level'].action}
   
   Performance:
   • Trades: {best['metrics']['trades']}
   • Win Rate: {best['metrics']['win_rate']}%
   • Profit Factor: {best['metrics']['profit_factor']}
   • Net PnL: ${best['metrics']['net_pnl']}
   • Max Drawdown: ${best['metrics']['max_drawdown']}
   • Sharpe: {best['metrics']['sharpe']}
   
   Stability:
   • Drift Detected: {'Yes' if best['stability'].drift_detected else 'No'}
   • Drift Score: {best['stability'].drift_score}

📋 NEXT STEPS:
""")
    
    if best["deployment_level"].level < 2:
        print("   1. Continue collecting trades to reach Level 2 (50+ trades)")
        print("   2. Run paper trading for 2-4 weeks")
        print("   3. Re-run governance analysis")
    elif best["deployment_level"].level < 4:
        print("   1. Continue collecting trades to reach Level 4 (200+ trades)")
        print("   2. Monitor stability metrics")
        print("   3. Re-run governance analysis monthly")
    else:
        print("   1. Configuration meets production requirements")
        print("   2. Set up rollback triggers")
        print("   3. Deploy with monitoring")
        print("   4. Re-run governance analysis monthly")
    
    print(f"\n{'='*100}")
    print("NOTE: These are governance recommendations only.")
    print("No changes have been made to the strategy.")
    print("=" * 100)


def main():
    print("=" * 100)
    print("🔬 PRODUCTION GOVERNANCE LAYER")
    print("=" * 100)
    
    trades = get_all_trades()
    print(f"\n📊 Loaded {len(trades)} total trades")
    
    if len(trades) < 20:
        print("❌ Not enough trades for governance analysis")
        return
    
    run_governance_analysis(trades)


if __name__ == "__main__":
    main()
