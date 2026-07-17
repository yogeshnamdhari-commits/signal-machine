#!/usr/bin/env python3
"""
Promotion Checklist — Automatic Configuration Advancement Gate
================================================================
Refuses advancement unless ALL required conditions are met.

READ-ONLY — Never modifies trading logic.
"""
import sqlite3
import json
import math
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime, timezone

DB_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/institutional_v1.db")


@dataclass
class ChecklistItem:
    """A single checklist item."""
    name: str
    required: str
    actual: str
    passed: bool
    critical: bool  # If True, blocks promotion even if other items pass


@dataclass
class PromotionChecklist:
    """Complete promotion checklist for a configuration."""
    config_name: str
    items: List[ChecklistItem]
    all_passed: bool
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


def run_promotion_checklist(trades, profile, target_level):
    """Run promotion checklist for a configuration."""
    filtered = apply_profile(trades, profile)
    metrics = calculate_metrics(filtered)
    
    items = []
    
    # 1. Minimum completed trades
    min_trades = {1: 20, 2: 50, 3: 200, 4: 200, 5: 500}.get(target_level, 200)
    items.append(ChecklistItem(
        name="Minimum Completed Trades",
        required=f"≥ {min_trades}",
        actual=str(metrics["trades"]),
        passed=metrics["trades"] >= min_trades,
        critical=True,
    ))
    
    # 2. Positive expectancy over recent rolling windows
    rolling_exp = calculate_rolling_expectancy(filtered, window=20)
    if rolling_exp and len(rolling_exp) >= 3:
        recent_positive = sum(1 for e in rolling_exp[-3:] if e > 0)
        positive_pct = recent_positive / 3 * 100
        items.append(ChecklistItem(
            name="Positive Expectancy (Recent Windows)",
            required="≥ 67% positive",
            actual=f"{positive_pct:.0f}%",
            passed=positive_pct >= 67,
            critical=True,
        ))
    else:
        items.append(ChecklistItem(
            name="Positive Expectancy (Recent Windows)",
            required="≥ 67% positive",
            actual="Insufficient data",
            passed=False,
            critical=True,
        ))
    
    # 3. Profit Factor above threshold
    pf_threshold = {1: 1.0, 2: 1.0, 3: 1.2, 4: 1.5, 5: 1.5}.get(target_level, 1.5)
    items.append(ChecklistItem(
        name="Profit Factor",
        required=f"> {pf_threshold}",
        actual=f"{metrics['profit_factor']:.2f}",
        passed=metrics["profit_factor"] > pf_threshold,
        critical=True,
    ))
    
    # 4. Maximum drawdown within acceptable limit
    dd_limit = {1: 500, 2: 300, 3: 200, 4: 150, 5: 100}.get(target_level, 200)
    items.append(ChecklistItem(
        name="Maximum Drawdown",
        required=f"< ${dd_limit}",
        actual=f"${metrics['max_drawdown']:.2f}",
        passed=metrics["max_drawdown"] < dd_limit,
        critical=False,
    ))
    
    # 5. No statistically significant degradation
    if rolling_exp and len(rolling_exp) >= 5:
        first_half = rolling_exp[:len(rolling_exp)//2]
        second_half = rolling_exp[len(rolling_exp)//2:]
        first_avg = sum(first_half) / len(first_half) if first_half else 0
        second_avg = sum(second_half) / len(second_half) if second_half else 0
        degradation = (first_avg - second_avg) / abs(first_avg) * 100 if first_avg != 0 else 0
        
        items.append(ChecklistItem(
            name="No Significant Degradation",
            required="< 30% degradation",
            actual=f"{degradation:.1f}%",
            passed=degradation < 30,
            critical=False,
        ))
    else:
        items.append(ChecklistItem(
            name="No Significant Degradation",
            required="< 30% degradation",
            actual="Insufficient data",
            passed=False,
            critical=False,
        ))
    
    # 6. Win rate above minimum
    wr_min = {1: 30, 2: 35, 3: 40, 4: 40, 5: 40}.get(target_level, 40)
    items.append(ChecklistItem(
        name="Win Rate",
        required=f"> {wr_min}%",
        actual=f"{metrics['win_rate']:.1f}%",
        passed=metrics["win_rate"] > wr_min,
        critical=False,
    ))
    
    # Calculate overall result
    all_passed = all(item.passed for item in items)
    critical_failures = sum(1 for item in items if item.critical and not item.passed)
    
    # Generate recommendation
    if all_passed:
        recommendation = f"✅ READY FOR LEVEL {target_level} — All checklist items passed"
    elif critical_failures > 0:
        recommendation = f"❌ NOT READY — {critical_failures} critical item(s) failed"
    else:
        recommendation = f"⚠️  CONDITIONAL — Non-critical items failed, review recommended"
    
    return PromotionChecklist(
        config_name=profile["name"],
        items=items,
        all_passed=all_passed,
        critical_failures=critical_failures,
        recommendation=recommendation,
    )


def display_checklist(checklist):
    """Display promotion checklist."""
    print(f"\n{'='*80}")
    print(f"📋 PROMOTION CHECKLIST: {checklist.config_name}")
    print(f"{'='*80}")
    
    print(f"\n{checklist.recommendation}")
    
    print(f"\n{'ITEM':<35} {'REQUIRED':<20} {'ACTUAL':<20} {'STATUS':<10}")
    print("-" * 85)
    
    for item in checklist.items:
        emoji = "✅" if item.passed else ("🔴" if item.critical else "🟡")
        critical_mark = " [CRITICAL]" if item.critical else ""
        print(f"{emoji} {item.name:<33} {item.required:<20} {item.actual:<20} {'PASS' if item.passed else 'FAIL'}{critical_mark}")
    
    print(f"\n{'='*80}")
    
    if checklist.all_passed:
        print("🟢 ALL ITEMS PASSED — Configuration is ready for promotion")
    elif checklist.critical_failures > 0:
        print(f"🔴 {checklist.critical_failures} CRITICAL ITEM(S) FAILED — Promotion blocked")
    else:
        print("🟡 NON-CRITICAL ITEMS FAILED — Review recommended before promotion")


def main():
    print("=" * 80)
    print("📋 PROMOTION CHECKLIST — Automatic Configuration Advancement Gate")
    print("=" * 80)
    
    trades = get_all_trades()
    print(f"\n📊 Loaded {len(trades)} total trades")
    
    if len(trades) < 10:
        print("❌ Not enough trades for checklist evaluation")
        return
    
    # Define configurations to check
    configs = [
        {"name": "Baseline", "rr_threshold": 1.5, "session_filter": ["new_york", "london", "unknown", "asia"], "min_confidence": 40},
        {"name": "RR_2.5_NY", "rr_threshold": 2.5, "session_filter": ["new_york"], "min_confidence": 40},
        {"name": "RR_3.0_NY", "rr_threshold": 3.0, "session_filter": ["new_york"], "min_confidence": 40},
    ]
    
    # Check each configuration for each level
    target_levels = [2, 3, 4]  # Paper Trading, Production Candidate, Production Approved
    
    for level in target_levels:
        print(f"\n{'='*80}")
        print(f"📊 LEVEL {level} PROMOTION CHECKLIST")
        print(f"{'='*80}")
        
        for config in configs:
            checklist = run_promotion_checklist(trades, config, level)
            display_checklist(checklist)
    
    # Display summary
    print(f"\n{'='*80}")
    print(f"📋 SUMMARY")
    print(f"{'='*80}\n")
    
    print(f"{'CONFIG':<20} {'L2 (Paper)':<15} {'L3 (Candidate)':<15} {'L4 (Production)':<15}")
    print("-" * 65)
    
    for config in configs:
        results = []
        for level in target_levels:
            checklist = run_promotion_checklist(trades, config, level)
            if checklist.all_passed:
                results.append("✅ PASS")
            elif checklist.critical_failures > 0:
                results.append(f"❌ FAIL ({checklist.critical_failures} critical)")
            else:
                results.append("⚠️  CONDITIONAL")
        
        print(f"{config['name']:<20} {results[0]:<15} {results[1]:<15} {results[2]:<15}")
    
    print(f"\n{'='*80}")
    print("NOTE: These are promotion checklist results only.")
    print("No changes have been made to the strategy.")
    print("=" * 80)


if __name__ == "__main__":
    main()
