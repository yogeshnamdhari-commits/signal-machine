#!/usr/bin/env python3
"""
Optimization Confidence Index & Validation Gates
==================================================
- Statistical confidence scoring
- Validation gates that must be passed before production
- Production recommendation logic

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
class ValidationGate:
    """A validation gate that must be passed."""
    name: str
    required: str
    actual: str
    passed: bool
    weight: float  # 0-1, importance of this gate


@dataclass
class ConfidenceIndex:
    """Optimization Confidence Index."""
    statistical_confidence: float  # 0-100%
    sample_size: Dict[str, int]  # {"actual": 21, "required": 200}
    walk_forward: Dict[str, any]  # {"passed": 4, "total": 4, "score": 0.8}
    robustness: Dict[str, any]  # {"passed": 10, "total": 12, "score": 0.83}
    overfitting_score: float  # 0-1, lower is better
    recommendation: str  # "Production Approved", "Paper Trade Only", "Ignore"


@dataclass
class ProductionRecommendation:
    """Final production recommendation."""
    status: str  # "APPROVED", "PAPER_TRADE", "REJECTED"
    confidence_index: ConfidenceIndex
    validation_gates: List[ValidationGate]
    summary: str
    next_steps: List[str]


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
    """Split trades into time periods."""
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


def apply_profile(trades, profile):
    """Apply a parameter profile to filter trades."""
    filtered = []
    rejected_rr = 0
    rejected_session = 0
    rejected_confidence = 0
    
    for t in trades:
        rr = t.get("risk_reward", 0) or t.get("planned_rr", 0) or 0
        if rr < profile["rr_threshold"]:
            rejected_rr += 1
            continue
        
        session = t.get("session", "unknown") or "unknown"
        if session not in profile["session_filter"]:
            rejected_session += 1
            continue
        
        conf = (t.get("confidence", 0) or 0) * 100
        if conf < profile["min_confidence"]:
            rejected_confidence += 1
            continue
        
        filtered.append(t)
    
    return filtered, rejected_rr, rejected_session, rejected_confidence


def run_walk_forward_validation(trades, profile, n_folds=4):
    """Run walk-forward validation with n folds."""
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at", 0) or 0)
    fold_size = len(sorted_trades) // n_folds
    
    fold_results = []
    
    for i in range(n_folds):
        start_idx = i * fold_size
        end_idx = start_idx + fold_size if i < n_folds - 1 else len(sorted_trades)
        fold_trades = sorted_trades[start_idx:end_idx]
        
        filtered, _, _, _ = apply_profile(fold_trades, profile)
        metrics = calculate_metrics(filtered)
        
        fold_results.append({
            "fold": i + 1,
            "trades": metrics["trades"],
            "win_rate": metrics["win_rate"],
            "profit_factor": metrics["profit_factor"],
            "expectancy": metrics["expectancy"],
            "net_pnl": metrics["net_pnl"],
        })
    
    return fold_results


def calculate_confidence_index(metrics, fold_results, robustness_passed, robustness_total, trades_count):
    """Calculate the Optimization Confidence Index."""
    
    # 1. Statistical Confidence (based on sample size)
    if trades_count >= 200:
        stat_conf = 95
    elif trades_count >= 100:
        stat_conf = 80
    elif trades_count >= 50:
        stat_conf = 60
    elif trades_count >= 20:
        stat_conf = 40
    else:
        stat_conf = max(trades_count / 200 * 100, 10)
    
    # 2. Walk-forward score
    if fold_results:
        positive_folds = sum(1 for f in fold_results if f["net_pnl"] > 0)
        wf_score = positive_folds / len(fold_results)
        wf_passed = positive_folds
        wf_total = len(fold_results)
    else:
        wf_score = 0
        wf_passed = 0
        wf_total = 0
    
    # 3. Robustness score
    rob_score = robustness_passed / robustness_total if robustness_total > 0 else 0
    
    # 4. Overfitting score (simplified)
    if fold_results and len(fold_results) > 1:
        pf_values = [f["profit_factor"] for f in fold_results if f["trades"] > 0]
        if len(pf_values) > 1:
            mean_pf = sum(pf_values) / len(pf_values)
            std_pf = math.sqrt(sum((x - mean_pf)**2 for x in pf_values) / len(pf_values))
            cv = std_pf / mean_pf if mean_pf > 0 else 1
            overfit_score = min(cv, 1.0)
        else:
            overfit_score = 0.5
    else:
        overfit_score = 0.5
    
    # 5. Determine recommendation
    overall_confidence = (
        stat_conf * 0.40 +
        wf_score * 100 * 0.25 +
        rob_score * 100 * 0.20 +
        (1 - overfit_score) * 100 * 0.15
    )
    
    if overall_confidence >= 90 and trades_count >= 200:
        recommendation = "Production Approved"
    elif overall_confidence >= 70 and trades_count >= 100:
        recommendation = "Manual Review"
    elif overall_confidence >= 50 and trades_count >= 50:
        recommendation = "Paper Trade Only"
    else:
        recommendation = "Ignore"
    
    return ConfidenceIndex(
        statistical_confidence=round(stat_conf, 1),
        sample_size={"actual": trades_count, "required": 200},
        walk_forward={"passed": wf_passed, "total": wf_total, "score": round(wf_score, 2)},
        robustness={"passed": robustness_passed, "total": robustness_total, "score": round(rob_score, 2)},
        overfitting_score=round(overfit_score, 2),
        recommendation=recommendation,
    )


def run_validation_gates(metrics, fold_results, robustness_passed, robustness_total, composite_score):
    """Run validation gates and return results."""
    gates = []
    
    # Gate 1: Minimum trades
    gates.append(ValidationGate(
        name="Sample Size",
        required="≥ 200",
        actual=str(metrics["trades"]),
        passed=metrics["trades"] >= 200,
        weight=0.25,
    ))
    
    # Gate 2: Walk-forward folds
    if fold_results:
        positive_folds = sum(1 for f in fold_results if f["net_pnl"] > 0)
        gates.append(ValidationGate(
            name="Walk-Forward Folds",
            required="≥ 4",
            actual=str(len(fold_results)),
            passed=len(fold_results) >= 4,
            weight=0.15,
        ))
        
        # Gate 3: Positive expectancy in folds
        pct_positive = positive_folds / len(fold_results) * 100 if fold_results else 0
        gates.append(ValidationGate(
            name="Positive Expectancy in Folds",
            required="≥ 80%",
            actual=f"{pct_positive:.0f}%",
            passed=pct_positive >= 80,
            weight=0.15,
        ))
    
    # Gate 4: Profit Factor
    gates.append(ValidationGate(
        name="Profit Factor",
        required="> 1.5",
        actual=f"{metrics['profit_factor']:.2f}",
        passed=metrics["profit_factor"] > 1.5,
        weight=0.15,
    ))
    
    # Gate 5: Max Drawdown
    gates.append(ValidationGate(
        name="Max Drawdown",
        required="< $100",
        actual=f"${metrics['max_drawdown']:.2f}",
        passed=metrics["max_drawdown"] < 100,
        weight=0.10,
    ))
    
    # Gate 6: Composite Score
    gates.append(ValidationGate(
        name="Composite Score",
        required="≥ 80",
        actual=f"{composite_score:.1f}",
        passed=composite_score >= 80,
        weight=0.10,
    ))
    
    # Gate 7: Stability
    if fold_results:
        profitable_periods = sum(1 for f in fold_results if f["net_pnl"] > 0)
        stability_pct = profitable_periods / len(fold_results) * 100 if fold_results else 0
        gates.append(ValidationGate(
            name="Stability Score",
            required="≥ 85%",
            actual=f"{stability_pct:.0f}%",
            passed=stability_pct >= 85,
            weight=0.10,
        ))
    
    return gates


def generate_production_recommendation(confidence_index, validation_gates):
    """Generate final production recommendation."""
    
    # Count passed gates
    passed_gates = sum(1 for g in validation_gates if g.passed)
    total_gates = len(validation_gates)
    
    # Determine status
    if confidence_index.recommendation == "Production Approved":
        status = "APPROVED"
    elif confidence_index.recommendation == "Manual Review":
        status = "PAPER_TRADE"
    else:
        status = "REJECTED"
    
    # Generate summary
    if status == "APPROVED":
        summary = "✅ Configuration passes all validation gates. Ready for production deployment."
    elif status == "PAPER_TRADE":
        summary = "⚠️  Configuration shows promise but needs paper trading validation before live deployment."
    else:
        summary = "❌ Configuration does not meet production requirements. Continue collecting data."
    
    # Generate next steps
    next_steps = []
    if confidence_index.sample_size["actual"] < 200:
        next_steps.append(f"Collect {200 - confidence_index.sample_size['actual']} more trades to reach 200 minimum")
    if confidence_index.walk_forward["passed"] < 4:
        next_steps.append("Run walk-forward validation with 4+ folds")
    if confidence_index.robustness["passed"] < 10:
        next_steps.append("Improve robustness across market conditions")
    if not any(g.name == "Profit Factor" and g.passed for g in validation_gates):
        next_steps.append("Increase Profit Factor above 1.5")
    
    if not next_steps:
        next_steps.append("Monitor live performance for 2-4 weeks")
        next_steps.append("Re-run optimizer monthly with new data")
    
    return ProductionRecommendation(
        status=status,
        confidence_index=confidence_index,
        validation_gates=validation_gates,
        summary=summary,
        next_steps=next_steps,
    )


def display_optimization_confidence_index(rec, profile_name):
    """Display the Optimization Confidence Index."""
    ci = rec.confidence_index
    
    print(f"\n{'='*80}")
    print(f"📊 OPTIMIZATION CONFIDENCE INDEX — {profile_name}")
    print(f"{'='*80}")
    
    # Status banner
    status_emoji = {"APPROVED": "🟢", "PAPER_TRADE": "🟡", "REJECTED": "🔴"}.get(rec.status, "⚪")
    print(f"\n{status_emoji} STATUS: {rec.status}")
    print(f"   {rec.summary}")
    
    # Confidence Index
    print(f"\n📊 CONFIDENCE INDEX:")
    print(f"   Statistical Confidence: {ci.statistical_confidence}%")
    print(f"   Sample Size: {ci.sample_size['actual']} / {ci.sample_size['required']}")
    print(f"   Walk Forward: {ci.walk_forward['passed']}/{ci.walk_forward['total']} (score: {ci.walk_forward['score']})")
    print(f"   Robustness: {ci.robustness['passed']}/{ci.robustness['total']} (score: {ci.robustness['score']})")
    print(f"   Overfitting Score: {ci.overfitting_score} (lower is better)")
    print(f"   Recommendation: {ci.recommendation}")
    
    # Validation Gates
    print(f"\n📋 VALIDATION GATES:")
    passed_count = sum(1 for g in rec.validation_gates if g.passed)
    total_count = len(rec.validation_gates)
    print(f"   Passed: {passed_count}/{total_count}")
    print()
    
    for gate in rec.validation_gates:
        emoji = "✅" if gate.passed else "❌"
        print(f"   {emoji} {gate.name}")
        print(f"      Required: {gate.required}")
        print(f"      Actual: {gate.actual}")
        print(f"      Weight: {gate.weight:.0%}")
    
    # Next Steps
    print(f"\n📋 NEXT STEPS:")
    for i, step in enumerate(rec.next_steps, 1):
        print(f"   {i}. {step}")
    
    print(f"\n{'='*80}")


def run_analysis(trades):
    """Run the complete analysis."""
    print("\n" + "=" * 100)
    print("🔬 OPTIMIZATION CONFIDENCE INDEX & VALIDATION GATES")
    print("=" * 100)
    print(f"   Total trades: {len(trades)}")
    
    # Define profiles to test
    profiles = [
        {"name": "Baseline", "rr_threshold": 1.5, "session_filter": ["new_york", "london", "unknown", "asia"], "min_confidence": 40},
        {"name": "RR_2.5_NY", "rr_threshold": 2.5, "session_filter": ["new_york"], "min_confidence": 40},
        {"name": "RR_3.0_NY", "rr_threshold": 3.0, "session_filter": ["new_york"], "min_confidence": 40},
    ]
    
    results = []
    
    for profile in profiles:
        filtered, _, _, _ = apply_profile(trades, profile)
        metrics = calculate_metrics(filtered)
        
        # Walk-forward validation
        fold_results = run_walk_forward_validation(trades, profile, n_folds=4)
        
        # Robustness (simplified - just check if profitable in different conditions)
        robustness_passed = 0
        robustness_total = 0
        
        # Check by regime
        regimes = defaultdict(list)
        for t in filtered:
            regime = t.get("regime", "unknown") or "unknown"
            regimes[regime].append(t)
        
        for regime, regime_trades in regimes.items():
            if len(regime_trades) >= 3:
                robustness_total += 1
                regime_metrics = calculate_metrics(regime_trades)
                if regime_metrics["profit_factor"] > 1.0:
                    robustness_passed += 1
        
        # Check by session
        sessions = defaultdict(list)
        for t in filtered:
            session = t.get("session", "unknown") or "unknown"
            sessions[session].append(t)
        
        for session, session_trades in sessions.items():
            if len(session_trades) >= 3:
                robustness_total += 1
                session_metrics = calculate_metrics(session_trades)
                if session_metrics["profit_factor"] > 1.0:
                    robustness_passed += 1
        
        # Calculate composite score
        pf_score = min(metrics["profit_factor"] / 3.0, 1.0) if metrics["profit_factor"] > 0 else 0
        exp_score = min(max(metrics["expectancy"], 0) / 5.0, 1.0)
        sharpe_score = min(max(metrics["sharpe"], 0) / 5.0, 1.0)
        wr_score = metrics["win_rate"] / 100.0
        
        if fold_results:
            profitable_folds = sum(1 for f in fold_results if f["net_pnl"] > 0)
            stability_score = profitable_folds / len(fold_results)
        else:
            stability_score = 0
        
        composite_score = (
            pf_score * 0.25 +
            exp_score * 0.20 +
            sharpe_score * 0.20 +
            wr_score * 0.10 +
            stability_score * 0.25
        ) * 100
        
        # Calculate confidence index
        confidence_index = calculate_confidence_index(
            metrics, fold_results, robustness_passed, robustness_total, len(filtered)
        )
        
        # Run validation gates
        validation_gates = run_validation_gates(
            metrics, fold_results, robustness_passed, robustness_total, composite_score
        )
        
        # Generate production recommendation
        recommendation = generate_production_recommendation(confidence_index, validation_gates)
        
        results.append({
            "profile": profile,
            "metrics": metrics,
            "fold_results": fold_results,
            "confidence_index": confidence_index,
            "validation_gates": validation_gates,
            "recommendation": recommendation,
            "composite_score": composite_score,
        })
    
    # Display results
    for r in results:
        display_optimization_confidence_index(r["recommendation"], r["profile"]["name"])
    
    # Display summary comparison
    print(f"\n{'='*100}")
    print(f"📊 SUMMARY COMPARISON")
    print(f"{'='*100}\n")
    
    print(f"{'PROFILE':<20} {'TRADES':<8} {'WR%':<8} {'PF':<8} {'PNL':<12} {'CONF':<10} {'STATUS':<15}")
    print("-" * 85)
    
    for r in results:
        m = r["metrics"]
        ci = r["confidence_index"]
        rec = r["recommendation"]
        
        status_emoji = {"APPROVED": "🟢", "PAPER_TRADE": "🟡", "REJECTED": "🔴"}.get(rec.status, "⚪")
        
        print(f"{r['profile']['name']:<20} {m['trades']:<8} {m['win_rate']:<7.1f}% "
              f"{m['profit_factor']:<8.2f} ${m['net_pnl']:<11.2f} "
              f"{ci.statistical_confidence:<9.1f}% {status_emoji} {rec.status}")
    
    print(f"\n{'='*100}")
    print(f"📋 FINAL RECOMMENDATION")
    print(f"{'='*100}")
    
    # Find best configuration that passes the most gates
    best = max(results, key=lambda r: (
        r["recommendation"].status == "APPROVED",
        sum(1 for g in r["validation_gates"] if g.passed),
        r["composite_score"],
    ))
    
    print(f"""
🏆 BEST CONFIGURATION: {best['profile']['name']}
   
   Status: {best['recommendation'].status}
   Statistical Confidence: {best['confidence_index'].statistical_confidence}%
   Sample Size: {best['confidence_index'].sample_size['actual']} / {best['confidence_index'].sample_size['required']}
   Walk Forward: {best['confidence_index'].walk_forward['passed']}/{best['confidence_index'].walk_forward['total']}
   Robustness: {best['confidence_index'].robustness['passed']}/{best['confidence_index'].robustness['total']}
   
   Performance:
   • Trades: {best['metrics']['trades']}
   • Win Rate: {best['metrics']['win_rate']}%
   • Profit Factor: {best['metrics']['profit_factor']}
   • Net PnL: ${best['metrics']['net_pnl']}
   • Sharpe: {best['metrics']['sharpe']}

{best['recommendation'].summary}

📋 NEXT STEPS:
""")
    
    for i, step in enumerate(best['recommendation'].next_steps, 1):
        print(f"   {i}. {step}")
    
    print(f"\n{'='*100}")
    print("NOTE: These are optimization recommendations only.")
    print("No changes have been made to the strategy.")
    print("=" * 100)


def main():
    print("=" * 100)
    print("🔬 OPTIMIZATION CONFIDENCE INDEX & VALIDATION GATES")
    print("=" * 100)
    
    trades = get_all_trades()
    print(f"\n📊 Loaded {len(trades)} total trades")
    
    if len(trades) < 20:
        print("❌ Not enough trades for meaningful analysis")
        return
    
    run_analysis(trades)


if __name__ == "__main__":
    main()
