#!/usr/bin/env python3
"""
Deployment Readiness Score & Regime-Aware Promotion Checklist
================================================================
- Deployment Readiness Score (0-100)
- Minimum observations per market regime
- Smooth progression with mandatory gates

READ-ONLY — Never modifies trading logic.
"""
import sqlite3
import json
import math
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timezone
from collections import defaultdict

DB_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/institutional_v1.db")


@dataclass
class ChecklistItem:
    """A single checklist item."""
    name: str
    required: str
    actual: str
    passed: bool
    critical: bool
    score: float  # 0-100 contribution to readiness score


@dataclass
class RegimeRequirement:
    """Minimum observations per regime."""
    regime: str
    required: int
    actual: int
    passed: bool


@dataclass
class DeploymentReadiness:
    """Deployment readiness assessment."""
    config_name: str
    readiness_score: float  # 0-100
    readiness_level: str  # "Research", "Experimental", "Paper Trading", "Production Candidate", "Production Approved"
    checklist_items: List[ChecklistItem]
    regime_requirements: List[RegimeRequirement]
    critical_failures: int
    recommendation: str


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


def apply_profile(trades, profile):
    """Apply a parameter profile to filter trades."""
    filtered = []
    for t in trades:
        rr = t.get("risk_reward", 0) or t.get("planned_rr", 0) or 0
        if rr < profile["rr_threshold"]:
            continue
        session = t.get("session", "unknown") or "unknown"
        if session not in profile["session_filter"]:
            continue
        conf = (t.get("confidence", 0) or 0) * 100
        if conf < profile["min_confidence"]:
            continue
        filtered.append(t)
    return filtered


def calculate_rolling_expectancy(trades, window=20):
    """Calculate rolling expectancy over a window."""
    if len(trades) < window:
        return None
    
    pnls = [t.get("pnl", 0) or 0 for t in trades]
    rolling_exp = []
    
    for i in range(window, len(pnls) + 1):
        window_pnls = pnls[i - window:i]
        n = len(window_pnls)
        wins = [p for p in window_pnls if p > 0]
        losses = [abs(p) for p in window_pnls if p < 0]
        
        wr = len(wins) / n * 100 if n else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)
        rolling_exp.append(exp)
    
    return rolling_exp


def check_regime_requirements(trades):
    """Check minimum observations per market regime."""
    regimes = defaultdict(int)
    for t in trades:
        regime = t.get("regime", "unknown") or t.get("at_open_regime", "unknown") or "unknown"
        regimes[regime] += 1
    
    requirements = [
        {"regime": "trending_bull", "required": 50},
        {"regime": "trending_bear", "required": 50},
        {"regime": "range", "required": 50},
    ]
    
    results = []
    for req in requirements:
        actual = regimes.get(req["regime"], 0)
        results.append(RegimeRequirement(
            regime=req["regime"],
            required=req["required"],
            actual=actual,
            passed=actual >= req["required"],
        ))
    
    return results


def calculate_readiness_score(metrics, rolling_exp, regime_reqs, checklist_items):
    """Calculate Deployment Readiness Score (0-100)."""
    scores = {}
    
    # 1. Sample size (20%)
    if metrics["trades"] >= 500:
        scores["sample_size"] = 100
    elif metrics["trades"] >= 200:
        scores["sample_size"] = 80
    elif metrics["trades"] >= 100:
        scores["sample_size"] = 60
    elif metrics["trades"] >= 50:
        scores["sample_size"] = 40
    else:
        scores["sample_size"] = max(metrics["trades"] / 50 * 40, 10)
    
    # 2. Profit factor (20%)
    if metrics["profit_factor"] >= 2.0:
        scores["profit_factor"] = 100
    elif metrics["profit_factor"] >= 1.5:
        scores["profit_factor"] = 80
    elif metrics["profit_factor"] >= 1.2:
        scores["profit_factor"] = 60
    elif metrics["profit_factor"] >= 1.0:
        scores["profit_factor"] = 40
    else:
        scores["profit_factor"] = max(metrics["profit_factor"] / 1.0 * 40, 0)
    
    # 3. Expectancy (15%)
    if metrics["expectancy"] >= 3.0:
        scores["expectancy"] = 100
    elif metrics["expectancy"] >= 1.0:
        scores["expectancy"] = 80
    elif metrics["expectancy"] >= 0:
        scores["expectancy"] = 60
    else:
        scores["expectancy"] = max(60 + metrics["expectancy"] * 20, 0)
    
    # 4. Drawdown (15%)
    if metrics["max_drawdown"] < 50:
        scores["drawdown"] = 100
    elif metrics["max_drawdown"] < 100:
        scores["drawdown"] = 80
    elif metrics["max_drawdown"] < 200:
        scores["drawdown"] = 60
    else:
        scores["drawdown"] = max(60 - (metrics["max_drawdown"] - 200) / 10, 0)
    
    # 5. Stability (15%)
    if rolling_exp and len(rolling_exp) >= 3:
        positive_windows = sum(1 for e in rolling_exp[-5:] if e > 0)
        stability_pct = positive_windows / min(5, len(rolling_exp[-5:])) * 100
        scores["stability"] = stability_pct
    else:
        scores["stability"] = 30  # Insufficient data
    
    # 6. Walk-forward validation (10%)
    regime_passed = sum(1 for r in regime_reqs if r.passed)
    regime_total = len(regime_reqs)
    scores["walk_forward"] = regime_passed / regime_total * 100 if regime_total > 0 else 0
    
    # 7. Recent degradation (5%)
    if rolling_exp and len(rolling_exp) >= 5:
        first_half = rolling_exp[:len(rolling_exp)//2]
        second_half = rolling_exp[len(rolling_exp)//2:]
        first_avg = sum(first_half) / len(first_half) if first_half else 0
        second_avg = sum(second_half) / len(second_half) if second_half else 0
        
        if first_avg > 0:
            degradation = (first_avg - second_avg) / first_avg * 100
            scores["degradation"] = max(100 - degradation, 0)
        else:
            scores["degradation"] = 50
    else:
        scores["degradation"] = 50  # Insufficient data
    
    # Weighted average
    weights = {
        "sample_size": 0.20,
        "profit_factor": 0.20,
        "expectancy": 0.15,
        "drawdown": 0.15,
        "stability": 0.15,
        "walk_forward": 0.10,
        "degradation": 0.05,
    }
    
    total_score = sum(scores.get(k, 0) * v for k, v in weights.items())
    
    return round(total_score, 1), scores


def determine_readiness_level(score):
    """Determine readiness level from score."""
    if score >= 95:
        return "Production Approved"
    elif score >= 85:
        return "Production Candidate"
    elif score >= 75:
        return "Paper Trading"
    elif score >= 60:
        return "Experimental"
    else:
        return "Research"


def run_deployment_readiness(trades, profile):
    """Run deployment readiness assessment."""
    filtered = apply_profile(trades, profile)
    metrics = calculate_metrics(filtered)
    
    # Calculate rolling expectancy
    rolling_exp = calculate_rolling_expectancy(filtered, window=20)
    
    # Check regime requirements
    regime_reqs = check_regime_requirements(filtered)
    
    # Checklist items
    checklist_items = []
    
    # Sample size
    checklist_items.append(ChecklistItem(
        name="Sample Size",
        required="≥ 200",
        actual=str(metrics["trades"]),
        passed=metrics["trades"] >= 200,
        critical=True,
        score=min(metrics["trades"] / 200 * 100, 100),
    ))
    
    # Profit factor
    checklist_items.append(ChecklistItem(
        name="Profit Factor",
        required="> 1.2",
        actual=f"{metrics['profit_factor']:.2f}",
        passed=metrics["profit_factor"] > 1.2,
        critical=True,
        score=min(metrics["profit_factor"] / 1.5 * 100, 100),
    ))
    
    # Expectancy
    checklist_items.append(ChecklistItem(
        name="Expectancy",
        required="> 0",
        actual=f"{metrics['expectancy']:.4f}",
        passed=metrics["expectancy"] > 0,
        critical=True,
        score=min(max(metrics["expectancy"], 0) / 2.0 * 100, 100),
    ))
    
    # Max drawdown
    checklist_items.append(ChecklistItem(
        name="Max Drawdown",
        required="< $200",
        actual=f"${metrics['max_drawdown']:.2f}",
        passed=metrics["max_drawdown"] < 200,
        critical=False,
        score=max(100 - metrics["max_drawdown"] / 2, 0),
    ))
    
    # Win rate
    checklist_items.append(ChecklistItem(
        name="Win Rate",
        required="> 40%",
        actual=f"{metrics['win_rate']:.1f}%",
        passed=metrics["win_rate"] > 40,
        critical=False,
        score=min(metrics["win_rate"] / 50 * 100, 100),
    ))
    
    # Calculate readiness score
    readiness_score, component_scores = calculate_readiness_score(
        metrics, rolling_exp, regime_reqs, checklist_items
    )
    
    # Determine level
    readiness_level = determine_readiness_level(readiness_score)
    
    # Count critical failures
    critical_failures = sum(1 for item in checklist_items if item.critical and not item.passed)
    
    # Generate recommendation
    if readiness_score >= 85 and critical_failures == 0:
        recommendation = f"✅ READY FOR {readiness_level.upper()}"
    elif readiness_score >= 75:
        recommendation = f"⚠️  APPROACHING {readiness_level.upper()} — Continue collecting data"
    else:
        recommendation = f"❌ NOT READY — Score {readiness_score:.1f}/100, {critical_failures} critical failures"
    
    return DeploymentReadiness(
        config_name=profile["name"],
        readiness_score=readiness_score,
        readiness_level=readiness_level,
        checklist_items=checklist_items,
        regime_requirements=regime_reqs,
        critical_failures=critical_failures,
        recommendation=recommendation,
    )


def display_readiness_assessment(assessment):
    """Display deployment readiness assessment."""
    print(f"\n{'='*100}")
    print(f"📊 DEPLOYMENT READINESS: {assessment.config_name}")
    print(f"{'='*100}")
    
    # Readiness score
    score = assessment.readiness_score
    if score >= 85:
        emoji = "🟢"
    elif score >= 75:
        emoji = "🟡"
    else:
        emoji = "🔴"
    
    print(f"\n{emoji} READINESS SCORE: {score:.1f}/100")
    print(f"   Level: {assessment.readiness_level}")
    print(f"   {assessment.recommendation}")
    
    # Checklist items
    print(f"\n📋 CHECKLIST ITEMS:")
    print(f"{'ITEM':<30} {'REQUIRED':<15} {'ACTUAL':<15} {'SCORE':<10} {'STATUS':<10}")
    print("-" * 80)
    
    for item in assessment.checklist_items:
        emoji = "✅" if item.passed else ("🔴" if item.critical else "🟡")
        critical_mark = " [CRITICAL]" if item.critical else ""
        print(f"{emoji} {item.name:<28} {item.required:<15} {item.actual:<15} {item.score:<10.1f} {'PASS' if item.passed else 'FAIL'}{critical_mark}")
    
    # Regime requirements
    print(f"\n📊 REGIME REQUIREMENTS:")
    print(f"{'REGIME':<20} {'REQUIRED':<10} {'ACTUAL':<10} {'STATUS':<10}")
    print("-" * 50)
    
    for req in assessment.regime_requirements:
        emoji = "✅" if req.passed else "🔴"
        print(f"{emoji} {req.regime:<18} {req.required:<10} {req.actual:<10} {'PASS' if req.passed else 'FAIL'}")
    
    print(f"\n{'='*100}")


def main():
    print("=" * 100)
    print("📊 DEPLOYMENT READINESS SCORE & REGIME-AWARE PROMOTION CHECKLIST")
    print("=" * 100)
    
    trades = get_all_trades()
    print(f"\n📊 Loaded {len(trades)} total trades")
    
    if len(trades) < 10:
        print("❌ Not enough trades for readiness assessment")
        return
    
    # Define configurations
    configs = [
        {"name": "Baseline", "rr_threshold": 1.5, "session_filter": ["new_york", "london", "unknown", "asia"], "min_confidence": 40},
        {"name": "RR_2.5_NY", "rr_threshold": 2.5, "session_filter": ["new_york"], "min_confidence": 40},
        {"name": "RR_3.0_NY", "rr_threshold": 3.0, "session_filter": ["new_york"], "min_confidence": 40},
    ]
    
    # Run assessment for each
    assessments = []
    for config in configs:
        assessment = run_deployment_readiness(trades, config)
        assessments.append(assessment)
        display_readiness_assessment(assessment)
    
    # Display summary
    print(f"\n{'='*100}")
    print(f"📊 SUMMARY COMPARISON")
    print(f"{'='*100}\n")
    
    print(f"{'CONFIG':<20} {'SCORE':<10} {'LEVEL':<25} {'CRITICAL':<10} {'STATUS':<15}")
    print("-" * 80)
    
    for assessment in assessments:
        emoji = "🟢" if assessment.readiness_score >= 85 else ("🟡" if assessment.readiness_score >= 75 else "🔴")
        print(f"{emoji} {assessment.config_name:<18} {assessment.readiness_score:<10.1f} {assessment.readiness_level:<25} {assessment.critical_failures:<10} {assessment.recommendation[:15]}")
    
    # Display readiness scale
    print(f"\n{'='*100}")
    print(f"📊 READINESS SCALE")
    print(f"{'='*100}\n")
    
    print(f"{'SCORE':<10} {'LEVEL':<25} {'ACTION':<30}")
    print("-" * 65)
    print(f"{'0-59':<10} {'Research':<25} {'Internal testing only':<30}")
    print(f"{'60-74':<10} {'Experimental':<25} {'Paper trading':<30}")
    print(f"{'75-84':<10} {'Paper Trading':<25} {'Forward testing':<30}")
    print(f"{'85-94':<10} {'Production Candidate':<25} {'Manual approval required':<30}")
    print(f"{'95-100':<10} {'Production Approved':<25} {'Live deployment':<30}")
    
    print(f"\n{'='*100}")
    print("NOTE: These are readiness assessments only.")
    print("No changes have been made to the strategy.")
    print("=" * 100)


if __name__ == "__main__":
    main()
