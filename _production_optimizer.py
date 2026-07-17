#!/usr/bin/env python3
"""
Production-Grade Parameter Optimizer
======================================
- Confidence levels for each recommendation
- Explainability (why each config won)
- Robustness testing (stress test across market conditions)
- Overfitting prevention rules

READ-ONLY — Never modifies trading logic.
"""
import sqlite3
import math
from pathlib import Path
from collections import defaultdict
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
class RobustnessResult:
    """Results from robustness testing."""
    condition: str
    trades: int
    win_rate: float
    profit_factor: float
    net_pnl: float
    passed: bool


@dataclass
class ConfidenceAssessment:
    """Confidence assessment for a configuration."""
    level: str  # "High", "Medium", "Low", "Very Low"
    score: float  # 0-100
    factors: Dict[str, float]
    warnings: List[str]


@dataclass
class Explanation:
    """Explanation of why a configuration won."""
    improvements: List[Dict[str, str]]
    summary: str


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
    # Confidence assessment
    confidence: ConfidenceAssessment
    # Explanation
    explanation: Explanation
    # Robustness testing
    robustness: List[RobustnessResult]
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
    
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at", 0) or 0)
    first_time = sorted_trades[0].get("closed_at", 0) or 0
    last_time = sorted_trades[-1].get("closed_at", 0) or 0
    
    if first_time == 0 or last_time == 0:
        return {}
    
    period_seconds = period_days * 24 * 3600
    periods = defaultdict(list)
    
    for t in trades:
        closed_at = t.get("closed_at", 0) or 0
        if closed_at > 0:
            period_idx = int((closed_at - first_time) / period_seconds)
            period_label = f"Period_{period_idx}"
            periods[period_label].append(t)
    
    return periods


def calculate_confidence(metrics, period_metrics, trades_count, robustness_results):
    """
    Calculate confidence level for a configuration.
    
    Factors:
    - Sample size (more trades = higher confidence)
    - Stability (consistent across periods)
    - Walk-forward agreement
    - Robustness (performs across market conditions)
    - Low variance
    """
    factors = {}
    warnings = []
    
    # Sample size factor (0-100)
    if trades_count >= 200:
        factors["sample_size"] = 100
    elif trades_count >= 100:
        factors["sample_size"] = 70
    elif trades_count >= 50:
        factors["sample_size"] = 50
    elif trades_count >= 20:
        factors["sample_size"] = 30
    else:
        factors["sample_size"] = 10
        warnings.append(f"Only {trades_count} trades (need 200+)")
    
    # Stability factor (0-100)
    if period_metrics:
        profitable_periods = sum(1 for p in period_metrics if p.net_pnl > 0)
        stability_pct = profitable_periods / len(period_metrics) * 100
        factors["stability"] = stability_pct
        if stability_pct < 80:
            warnings.append(f"Only {profitable_periods}/{len(period_metrics)} periods profitable")
    else:
        factors["stability"] = 0
    
    # Variance factor (0-100)
    pf_values = [p.profit_factor for p in period_metrics if p.trades > 0]
    if len(pf_values) > 1:
        mean_pf = sum(pf_values) / len(pf_values)
        std_pf = math.sqrt(sum((x - mean_pf)**2 for x in pf_values) / len(pf_values))
        cv = std_pf / mean_pf if mean_pf > 0 else 1
        factors["variance"] = max(0, 100 - cv * 100)
        if cv > 0.5:
            warnings.append(f"High variance in Profit Factor (CV={cv:.2f})")
    else:
        factors["variance"] = 50
    
    # Robustness factor (0-100)
    if robustness_results:
        passed_conditions = sum(1 for r in robustness_results if r.passed)
        factors["robustness"] = passed_conditions / len(robustness_results) * 100
        if passed_conditions < len(robustness_results) * 0.7:
            warnings.append(f"Only {passed_conditions}/{len(robustness_results)} conditions passed")
    else:
        factors["robustness"] = 50
    
    # Overall confidence score
    weights = {
        "sample_size": 0.35,
        "stability": 0.25,
        "variance": 0.20,
        "robustness": 0.20,
    }
    
    score = sum(factors.get(k, 0) * v for k, v in weights.items())
    
    # Determine confidence level
    if score >= 80:
        level = "High"
    elif score >= 60:
        level = "Medium"
    elif score >= 40:
        level = "Low"
    else:
        level = "Very Low"
    
    return ConfidenceAssessment(
        level=level,
        score=round(score, 1),
        factors=factors,
        warnings=warnings,
    )


def calculate_explanation(baseline_metrics, best_metrics):
    """Calculate explanation of why a configuration won."""
    improvements = []
    
    # Profit Factor
    pf_diff = best_metrics["profit_factor"] - baseline_metrics["profit_factor"]
    if baseline_metrics["profit_factor"] > 0:
        pf_pct = pf_diff / baseline_metrics["profit_factor"] * 100
    else:
        pf_pct = 0
    improvements.append({
        "metric": "Profit Factor",
        "baseline": f"{baseline_metrics['profit_factor']:.2f}",
        "best": f"{best_metrics['profit_factor']:.2f}",
        "change": f"{pf_pct:+.1f}%",
    })
    
    # Win Rate
    wr_diff = best_metrics["win_rate"] - baseline_metrics["win_rate"]
    improvements.append({
        "metric": "Win Rate",
        "baseline": f"{baseline_metrics['win_rate']:.1f}%",
        "best": f"{best_metrics['win_rate']:.1f}%",
        "change": f"{wr_diff:+.1f}%",
    })
    
    # Expectancy
    exp_diff = best_metrics["expectancy"] - baseline_metrics["expectancy"]
    improvements.append({
        "metric": "Expectancy",
        "baseline": f"{baseline_metrics['expectancy']:.4f}",
        "best": f"{best_metrics['expectancy']:.4f}",
        "change": f"{exp_diff:+.4f}",
    })
    
    # Sharpe
    sharpe_diff = best_metrics["sharpe"] - baseline_metrics["sharpe"]
    improvements.append({
        "metric": "Sharpe Ratio",
        "baseline": f"{baseline_metrics['sharpe']:.2f}",
        "best": f"{best_metrics['sharpe']:.2f}",
        "change": f"{sharpe_diff:+.2f}",
    })
    
    # Max Drawdown
    dd_diff = baseline_metrics["max_drawdown"] - best_metrics["max_drawdown"]
    improvements.append({
        "metric": "Max Drawdown",
        "baseline": f"${baseline_metrics['max_drawdown']:.2f}",
        "best": f"${best_metrics['max_drawdown']:.2f}",
        "change": f"-${dd_diff:.2f}",
    })
    
    # Trade Count
    trades_diff = best_metrics["trades"] - baseline_metrics["trades"]
    improvements.append({
        "metric": "Trade Count",
        "baseline": f"{baseline_metrics['trades']}",
        "best": f"{best_metrics['trades']}",
        "change": f"{trades_diff:+d}",
    })
    
    # Summary
    summary = f"Configuration won because: "
    summary += f"PF {pf_pct:+.1f}%, "
    summary += f"WR {wr_diff:+.1f}%, "
    summary += f"DD -${dd_diff:.0f}"
    
    return Explanation(
        improvements=improvements,
        summary=summary,
    )


def run_robustness_tests(trades, profile):
    """Run robustness tests across different market conditions."""
    results = []
    
    # Test by regime
    regimes = defaultdict(list)
    for t in trades:
        regime = t.get("regime", "unknown") or t.get("at_open_regime", "unknown") or "unknown"
        regimes[regime].append(t)
    
    for regime, regime_trades in regimes.items():
        # Apply profile filters
        filtered = apply_profile_filters(regime_trades, profile)
        metrics = calculate_metrics(filtered)
        
        results.append(RobustnessResult(
            condition=f"Regime: {regime}",
            trades=metrics["trades"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            net_pnl=metrics["net_pnl"],
            passed=metrics["profit_factor"] > 1.0 and metrics["trades"] >= 5,
        ))
    
    # Test by session
    sessions = defaultdict(list)
    for t in trades:
        session = t.get("session", "unknown") or "unknown"
        sessions[session].append(t)
    
    for session, session_trades in sessions.items():
        filtered = apply_profile_filters(session_trades, profile)
        metrics = calculate_metrics(filtered)
        
        results.append(RobustnessResult(
            condition=f"Session: {session}",
            trades=metrics["trades"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            net_pnl=metrics["net_pnl"],
            passed=metrics["profit_factor"] > 1.0 and metrics["trades"] >= 5,
        ))
    
    # Test by direction
    for side in ["LONG", "SHORT"]:
        side_trades = [t for t in trades if t.get("side") == side]
        filtered = apply_profile_filters(side_trades, profile)
        metrics = calculate_metrics(filtered)
        
        results.append(RobustnessResult(
            condition=f"Direction: {side}",
            trades=metrics["trades"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            net_pnl=metrics["net_pnl"],
            passed=metrics["profit_factor"] > 1.0 and metrics["trades"] >= 5,
        ))
    
    return results


def apply_profile_filters(trades, profile):
    """Apply profile filters to trades."""
    filtered = []
    for t in trades:
        # RR filter
        rr = t.get("risk_reward", 0) or t.get("planned_rr", 0) or 0
        if rr < profile.rr_threshold:
            continue
        
        # Session filter
        session = t.get("session", "unknown") or "unknown"
        if session not in profile.session_filter:
            continue
        
        # Confidence filter
        conf = (t.get("confidence", 0) or 0) * 100
        if conf < profile.min_confidence:
            continue
        
        filtered.append(t)
    
    return filtered


def apply_profile(trades, profile):
    """Apply a parameter profile to filter trades with rejection counts."""
    filtered = []
    rejected_rr = 0
    rejected_session = 0
    rejected_confidence = 0
    
    for t in trades:
        rr = t.get("risk_reward", 0) or t.get("planned_rr", 0) or 0
        if rr < profile.rr_threshold:
            rejected_rr += 1
            continue
        
        session = t.get("session", "unknown") or "unknown"
        if session not in profile.session_filter:
            rejected_session += 1
            continue
        
        conf = (t.get("confidence", 0) or 0) * 100
        if conf < profile.min_confidence:
            rejected_confidence += 1
            continue
        
        filtered.append(t)
    
    return filtered, rejected_rr, rejected_session, rejected_confidence


def check_overfitting_rules(metrics, period_metrics):
    """Check overfitting prevention rules."""
    violations = []
    
    # Rule 1: Minimum 200 trades
    if metrics["trades"] < 200:
        violations.append(f"Sample size {metrics['trades']} < 200 minimum")
    
    # Rule 2: Positive expectancy in at least 80% of periods
    if period_metrics:
        profitable = sum(1 for p in period_metrics if p.net_pnl > 0)
        pct = profitable / len(period_metrics) * 100
        if pct < 80:
            violations.append(f"Only {pct:.0f}% periods profitable (need 80%+)")
    
    # Rule 3: No fold with Profit Factor < 1.0
    if period_metrics:
        bad_periods = [p for p in period_metrics if p.profit_factor < 1.0 and p.trades >= 5]
        if bad_periods:
            violations.append(f"{len(bad_periods)} periods with PF < 1.0")
    
    # Rule 4: Drawdown within acceptable limit
    if metrics["max_drawdown"] > 100:
        violations.append(f"Max drawdown ${metrics['max_drawdown']:.0f} > $100 limit")
    
    return violations


def run_optimization(trades, baseline_metrics):
    """Run optimization across all parameter profiles."""
    print("\n" + "=" * 120)
    print("🔬 PRODUCTION-GRADE PARAMETER OPTIMIZER")
    print("=" * 120)
    print(f"   Base trades: {len(trades)}")
    print(f"   Testing parameter profiles...\n")
    
    # Define parameter profiles
    profiles = [
        ParameterProfile(name="Baseline", rr_threshold=1.5, session_filter=["new_york", "london", "unknown", "asia"], min_confidence=40, description="Current parameters"),
        ParameterProfile(name="RR_2.0", rr_threshold=2.0, session_filter=["new_york", "london", "unknown", "asia"], min_confidence=40, description="Higher RR threshold"),
        ParameterProfile(name="RR_2.5", rr_threshold=2.5, session_filter=["new_york", "london", "unknown", "asia"], min_confidence=40, description="Higher RR threshold"),
        ParameterProfile(name="RR_3.0", rr_threshold=3.0, session_filter=["new_york", "london", "unknown", "asia"], min_confidence=40, description="Higher RR threshold"),
        ParameterProfile(name="NY_Only", rr_threshold=1.5, session_filter=["new_york"], min_confidence=40, description="New York session only"),
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
        
        # Robustness testing
        robustness = run_robustness_tests(trades, profile)
        
        # Confidence assessment
        confidence = calculate_confidence(metrics, period_metrics, len(filtered), robustness)
        
        # Explanation
        explanation = calculate_explanation(baseline_metrics, metrics)
        
        # Composite score
        composite = calculate_composite_score(metrics, period_metrics, len(filtered), confidence.score)
        
        # Overfitting check
        overfitting_violations = check_overfitting_rules(metrics, period_metrics)
        
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
            confidence=confidence,
            explanation=explanation,
            robustness=robustness,
            rejected_by_rr=rej_rr,
            rejected_by_session=rej_sess,
            rejected_by_confidence=rej_conf,
        )
        results.append(result)
    
    # Sort by composite score
    results.sort(key=lambda x: -x.composite_score)
    
    return results


def calculate_composite_score(metrics, period_metrics, trades_count, confidence_score):
    """Calculate composite optimization score."""
    if trades_count < 10:
        return 0
    
    pf_score = min(metrics["profit_factor"] / 3.0, 1.0) if metrics["profit_factor"] > 0 else 0
    exp_score = min(max(metrics["expectancy"], 0) / 5.0, 1.0)
    sharpe_score = min(max(metrics["sharpe"], 0) / 5.0, 1.0)
    wr_score = metrics["win_rate"] / 100.0
    
    if period_metrics:
        profitable_periods = sum(1 for p in period_metrics if p.net_pnl > 0)
        stability_score = profitable_periods / len(period_metrics)
    else:
        stability_score = 0
    
    if trades_count < 20:
        count_score = trades_count / 20.0 * 0.5
    elif trades_count < 50:
        count_score = 0.5 + (trades_count - 20) / 30.0 * 0.3
    else:
        count_score = 0.8 + min((trades_count - 50) / 100.0, 1.0) * 0.2
    
    # Include confidence score
    conf_score = confidence_score / 100.0
    
    composite = (
        pf_score * 0.20 +
        exp_score * 0.15 +
        sharpe_score * 0.15 +
        wr_score * 0.10 +
        stability_score * 0.15 +
        count_score * 0.10 +
        conf_score * 0.15
    )
    
    return round(composite * 100, 1)


def display_results(results):
    """Display optimization results with confidence levels."""
    print(f"\n{'='*140}")
    print(f"📊 OPTIMIZATION RESULTS (Ranked by Composite Score)")
    print(f"{'='*140}\n")
    
    print(f"{'RANK':<5} {'PROFILE':<25} {'TRADES':<8} {'WR%':<8} {'PF':<8} {'PNL':<12} {'STABLE':<8} {'SCORE':<8} {'CONFIDENCE':<15} {'LEVEL':<10}")
    print("-" * 140)
    
    for i, r in enumerate(results, 1):
        emoji = "🟢" if r.composite_score > 50 else ("🟡" if r.composite_score > 30 else "🔴")
        stability = f"{r.periods_profitable}/{r.periods_total}"
        
        conf_emoji = {"High": "🟢", "Medium": "🟡", "Low": "🔴", "Very Low": "⚫"}.get(r.confidence.level, "⚪")
        
        print(f"{emoji} {i:<3} {r.profile.name:<25} {r.trades:<8} {r.win_rate:<7.1f}% "
              f"{r.profit_factor:<8.2f} ${r.net_pnl:<11.2f} "
              f"{stability:<8} {r.composite_score:<8.1f} "
              f"{conf_emoji} {r.confidence.score:<13.1f} {r.confidence.level}")


def display_explanations(results, baseline_metrics):
    """Display explanations for top configurations."""
    print(f"\n{'='*100}")
    print(f"📋 EXPLANATIONS (Why Each Configuration Won)")
    print(f"{'='*100}\n")
    
    for r in results[:5]:
        print(f"📊 {r.profile.name} (Score: {r.composite_score})")
        print(f"   {r.explanation.summary}")
        print(f"   Improvements:")
        for imp in r.explanation.improvements:
            print(f"      • {imp['metric']}: {imp['baseline']} → {imp['best']} ({imp['change']})")
        print()


def display_confidence_details(results):
    """Display confidence assessment details."""
    print(f"\n{'='*100}")
    print(f"📊 CONFIDENCE ASSESSMENT DETAILS")
    print(f"{'='*100}\n")
    
    for r in results[:5]:
        print(f"📊 {r.profile.name}")
        print(f"   Confidence Level: {r.confidence.level} ({r.confidence.score:.1f}/100)")
        print(f"   Factors:")
        for factor, score in r.confidence.factors.items():
            print(f"      • {factor}: {score:.1f}")
        if r.confidence.warnings:
            print(f"   Warnings:")
            for w in r.confidence.warnings:
                print(f"      ⚠️  {w}")
        print()


def display_robustness_results(results):
    """Display robustness test results."""
    print(f"\n{'='*100}")
    print(f"📊 ROBUSTNESS TEST RESULTS (Top 3 Configurations)")
    print(f"{'='*100}\n")
    
    for r in results[:3]:
        print(f"📊 {r.profile.name}")
        passed = sum(1 for rob in r.robustness if rob.passed)
        total = len(r.robustness)
        print(f"   Passed: {passed}/{total} conditions")
        print(f"   Results:")
        for rob in r.robustness:
            emoji = "🟢" if rob.passed else "🔴"
            print(f"      {emoji} {rob.condition}: Trades={rob.trades}, WR={rob.win_rate}%, PF={rob.profit_factor}")
        print()


def generate_final_report(results, baseline_metrics, trades):
    """Generate final optimization report."""
    print(f"\n{'='*120}")
    print(f"📋 FINAL PRODUCTION-GRADE OPTIMIZATION REPORT")
    print(f"{'='*120}")
    
    best = results[0] if results else None
    baseline = next((r for r in results if r.profile.name == "Baseline"), None)
    
    if not best or not baseline:
        print("   Insufficient data for report")
        return
    
    # Check overfitting rules
    overfitting_violations = check_overfitting_rules(
        {"trades": best.trades, "max_drawdown": best.max_drawdown},
        best.period_metrics
    )
    
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
   
   Confidence Assessment:
   • Level: {best.confidence.level}
   • Score: {best.confidence.score}/100
   • Warnings: {len(best.confidence.warnings)}
   
   Robustness:
   • Passed: {sum(1 for r in best.robustness if r.passed)}/{len(best.robustness)} conditions
   
   Overfitting Check:
   • Violations: {len(overfitting_violations)}
   {chr(10).join(f'   • {v}' for v in overfitting_violations) if overfitting_violations else '   • None'}

📈 WHY THIS CONFIGURATION WON:
{chr(10).join(f'   • {imp["metric"]}: {imp["baseline"]} → {imp["best"]} ({imp["change"]})' for imp in best.explanation.improvements)}

⚠️  STATISTICAL VALIDITY:
   • Sample Size: {best.trades} trades (need 200+ for high confidence)
   • Confidence Level: {best.confidence.level}
   • Period Stability: {best.periods_profitable}/{best.periods_total} profitable periods
   • Overfitting Violations: {len(overfitting_violations)}

📋 OVERFITTING PREVENTION RULES:
   • Minimum 200 completed trades: {"✅ PASS" if best.trades >= 200 else f"❌ FAIL ({best.trades} trades)"}
   • Positive expectancy in 80%+ periods: {"✅ PASS" if best.periods_profitable / max(best.periods_total, 1) >= 0.8 else f"❌ FAIL ({best.periods_profitable}/{best.periods_total})"}
   • No period with PF < 1.0: {"✅ PASS" if all(p.profit_factor >= 1.0 for p in best.period_metrics if p.trades >= 5) else "❌ FAIL"}
   • Drawdown < $100: {"✅ PASS" if best.max_drawdown < 100 else f"❌ FAIL (${best.max_drawdown:.0f})"}

📋 RECOMMENDATION:
   {"✅ Configuration passes all overfitting rules" if not overfitting_violations else "⚠️  Configuration has overfitting violations — need more data"}
   
   Next Steps:
   1. {"Continue collecting trades to reach 200+ sample" if best.trades < 200 else "Sample size adequate"}
   2. Run paper trading with best configuration
   3. Monitor for 2-4 weeks
   4. Re-run optimizer with larger dataset
   5. Only then consider live deployment
""")
    
    print("=" * 120)
    print("NOTE: These are optimization recommendations only.")
    print("No changes have been made to the strategy.")
    print("=" * 120)


def main():
    print("=" * 120)
    print("🔬 PRODUCTION-GRADE PARAMETER OPTIMIZER")
    print("=" * 120)
    
    trades = get_all_trades()
    print(f"\n📊 Loaded {len(trades)} total trades")
    
    if len(trades) < 20:
        print("❌ Not enough trades for meaningful optimization")
        return
    
    # Calculate baseline metrics
    baseline_metrics = calculate_metrics(trades)
    
    # Run optimization
    results = run_optimization(trades, baseline_metrics)
    
    # Display results
    display_results(results)
    
    # Display explanations
    display_explanations(results, baseline_metrics)
    
    # Display confidence details
    display_confidence_details(results)
    
    # Display robustness results
    display_robustness_results(results)
    
    # Generate final report
    generate_final_report(results, baseline_metrics, trades)


if __name__ == "__main__":
    main()
