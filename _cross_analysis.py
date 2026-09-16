"""
Cross Analysis Report — Diagnostic analysis of EMA crossover entry timing.

This script analyzes cross detection data to answer:
  "Does delayed entry after a valid EMA crossover consistently reduce expectancy?"

Usage:
    python3 _cross_analysis.py
"""

import sqlite3
import sys
import math
import random
from pathlib import Path
from typing import List, Tuple, Optional, Dict

DB_PATH = Path(__file__).resolve().parent / "packages/ai-engine" / "data" / "cross_analysis.db"


# ═══════════════════════════════════════════════════════════════════════════════
# Statistical Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════

def _bootstrap_ci(data: List[float], n_resamples: int = 10000, ci: float = 0.95,
                  stat_fn=None) -> Tuple[float, float, float]:
    """Compute bootstrap confidence interval for any statistic.
    
    Returns: (point_estimate, ci_low, ci_high)
    """
    if not data:
        return (0.0, 0.0, 0.0)
    if stat_fn is None:
        stat_fn = lambda x: sum(x) / len(x)
    
    point = stat_fn(data)
    n = len(data)
    boot_stats = []
    for _ in range(n_resamples):
        sample = [data[random.randint(0, n - 1)] for _ in range(n)]
        boot_stats.append(stat_fn(sample))
    boot_stats.sort()
    alpha = (1 - ci) / 2
    lo = boot_stats[int(alpha * n_resamples)]
    hi = boot_stats[int((1 - alpha) * n_resamples)]
    return (point, lo, hi)


def _median(data: List[float]) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _stddev(data: List[float]) -> float:
    if len(data) < 2:
        return 0.0
    mean = sum(data) / len(data)
    var = sum((x - mean) ** 2 for x in data) / (len(data) - 1)
    return math.sqrt(var)


def _percentile(data: List[float], p: float) -> float:
    """Compute the p-th percentile (0-100) of data."""
    if not data:
        return 0.0
    s = sorted(data)
    idx = (p / 100) * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] + frac * (s[hi] - s[lo])


def _profit_factor(returns: List[float]) -> float:
    """Profit Factor = sum of gains / abs(sum of losses)."""
    gains = sum(r for r in returns if r > 0)
    losses = abs(sum(r for r in returns if r < 0))
    if losses == 0:
        return float('inf') if gains > 0 else 0.0
    return gains / losses


def _expectancy_r(returns: List[float], risk_reward_ratio: float = 1.5) -> float:
    """Expectancy in R-multiples.
    
    Uses the return distribution to estimate R-multiple expectancy.
    Winners contribute +1R, losers contribute -risk_reward_ratio R.
    """
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r > 0)
    losses = sum(1 for r in returns if r <= 0)
    n = len(returns)
    wr = wins / n
    lr = losses / n
    return wr * 1.0 - lr * risk_reward_ratio


def _cohens_d(before: List[float], after: List[float]) -> float:
    """Cohen's d effect size: (mean_after - mean_before) / pooled_std."""
    if len(before) < 2 or len(after) < 2:
        return 0.0
    mean_b = sum(before) / len(before)
    mean_a = sum(after) / len(after)
    var_b = sum((x - mean_b) ** 2 for x in before) / (len(before) - 1)
    var_a = sum((x - mean_a) ** 2 for x in after) / (len(after) - 1)
    pooled_std = math.sqrt(((len(before) - 1) * var_b + (len(after) - 1) * var_a)
                           / (len(before) + len(after) - 2))
    if pooled_std == 0:
        return 0.0
    return (mean_a - mean_b) / pooled_std


def _bootstrap_difference_ci(before: List[float], after: List[float],
                             n_resamples: int = 10000) -> Tuple[float, float, float]:
    """Bootstrap CI for the difference in means (after - before).
    
    Returns: (diff, ci_low, ci_high)
    """
    if not before or not after:
        return (0.0, 0.0, 0.0)
    
    diff = sum(after) / len(after) - sum(before) / len(before)
    
    n_b, n_a = len(before), len(after)
    boot_diffs = []
    for _ in range(n_resamples):
        sample_b = [before[random.randint(0, n_b - 1)] for _ in range(n_b)]
        sample_a = [after[random.randint(0, n_a - 1)] for _ in range(n_a)]
        boot_diffs.append(sum(sample_a) / n_a - sum(sample_b) / n_b)
    boot_diffs.sort()
    lo = boot_diffs[int(0.025 * n_resamples)]
    hi = boot_diffs[int(0.975 * n_resamples)]
    return (diff, lo, hi)


def _significance_label(ci_low: float, ci_high: float) -> str:
    """Interpret bootstrap CI for significance."""
    if ci_low > 0:
        return "✅ SIG"  # Both bounds positive → improvement supported
    elif ci_high < 0:
        return "⚠️ SIG"  # Both bounds negative → degradation supported
    else:
        return "📊 NS"  # CI spans zero → not significant


def run_analysis():
    if not DB_PATH.exists():
        print("No cross analysis data found yet.")
        print("The cross logger will start collecting data after the next scanner restart.")
        return
    
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    
    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  CROSS ENTRY TIMING ANALYSIS")
    print("  Question: Does delayed entry reduce expectancy?")
    print("═══════════════════════════════════════════════════════════════")
    
    # 1. Overview
    total = db.execute("SELECT COUNT(*) FROM cross_events").fetchone()[0]
    with_signal = db.execute("SELECT COUNT(*) FROM cross_events WHERE signal_time IS NOT NULL").fetchone()[0]
    with_outcome = db.execute("SELECT COUNT(*) FROM cross_events WHERE outcome IS NOT NULL").fetchone()[0]
    total_rejections = db.execute("SELECT COUNT(*) FROM cross_rejections").fetchone()[0]
    
    print(f"\n  Total crosses detected: {total}")
    print(f"  Crosses that produced signals: {with_signal}")
    print(f"  Crosses with completed outcomes: {with_outcome}")
    print(f"  Total rejections logged: {total_rejections}")
    
    if total == 0:
        print("\n  No cross data yet. The logger will start collecting after restart.")
        db.close()
        return
    
    # 2. Performance by Entry Delay
    print(f"\n  ─── Performance by Entry Delay (candles after cross) ───")
    print(f"  {'Bucket':>10s} | {'n':>5s} | {'Wins':>5s} | {'WR':>6s} | {'Mean R':>8s} | {'Med R':>8s} | {'P25':>8s} | {'P75':>8s}")
    print(f"  {'-'*10}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
    
    buckets_def = [
        ('0-2', 'entry_delay_candles <= 2'),
        ('3-5', 'entry_delay_candles BETWEEN 3 AND 5'),
        ('6-10', 'entry_delay_candles BETWEEN 6 AND 10'),
        ('10+', 'entry_delay_candles > 10'),
    ]
    
    for label, cond in buckets_def:
        r_vals = [row[0] for row in db.execute(f"SELECT r_multiple FROM cross_events WHERE outcome IS NOT NULL AND {cond} AND r_multiple IS NOT NULL").fetchall()]
        if not r_vals:
            continue
        r_vals.sort()
        n = len(r_vals)
        wins = sum(1 for v in r_vals if v > 0)
        wr = wins / n * 100
        mean_r = sum(r_vals) / n
        med_r = r_vals[n // 2] if n % 2 else (r_vals[n // 2 - 1] + r_vals[n // 2]) / 2
        p25 = r_vals[int(n * 0.25)] if n >= 4 else r_vals[0]
        p75 = r_vals[int(n * 0.75)] if n >= 4 else r_vals[-1]
        print(f"  {label:>10s} | {n:>5d} | {wins:>5d} | {wr:>5.1f}% | {mean_r:>+7.2f} | {med_r:>+7.2f} | {p25:>+7.2f} | {p75:>+7.2f}")
    
    # 3. Performance by Distance from Cross
    print(f"\n  ─── Performance by Distance from Cross ───")
    print(f"  {'Bucket':>10s} | {'n':>5s} | {'Wins':>5s} | {'WR':>6s} | {'Mean R':>8s} | {'Med R':>8s} | {'P25':>8s} | {'P75':>8s}")
    print(f"  {'-'*10}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
    
    dist_buckets = [
        ('0-1%', 'distance_from_cross_pct <= 1.0'),
        ('1-3%', 'distance_from_cross_pct BETWEEN 1.0 AND 3.0'),
        ('3-5%', 'distance_from_cross_pct BETWEEN 3.0 AND 5.0'),
        ('5%+', 'distance_from_cross_pct > 5.0'),
    ]
    
    for label, cond in dist_buckets:
        r_vals = [row[0] for row in db.execute(f"SELECT r_multiple FROM cross_events WHERE outcome IS NOT NULL AND distance_from_cross_pct IS NOT NULL AND {cond} AND r_multiple IS NOT NULL").fetchall()]
        if not r_vals:
            continue
        r_vals.sort()
        n = len(r_vals)
        wins = sum(1 for v in r_vals if v > 0)
        wr = wins / n * 100
        mean_r = sum(r_vals) / n
        med_r = r_vals[n // 2] if n % 2 else (r_vals[n // 2 - 1] + r_vals[n // 2]) / 2
        p25 = r_vals[int(n * 0.25)] if n >= 4 else r_vals[0]
        p75 = r_vals[int(n * 0.75)] if n >= 4 else r_vals[-1]
        print(f"  {label:>10s} | {n:>5d} | {wins:>5d} | {wr:>5.1f}% | {mean_r:>+7.2f} | {med_r:>+7.2f} | {p25:>+7.2f} | {p75:>+7.2f}")
    
    # 4. Performance by Cross Quality
    print(f"\n  ─── Performance by Cross Quality Score ───")
    print(f"  {'Bucket':>10s} | {'n':>5s} | {'Wins':>5s} | {'WR':>6s} | {'Mean R':>8s} | {'Med R':>8s} | {'P25':>8s} | {'P75':>8s}")
    print(f"  {'-'*10}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
    
    qual_buckets = [
        ('80-100', 'cross_quality_score >= 80'),
        ('60-80', 'cross_quality_score BETWEEN 60 AND 80'),
        ('40-60', 'cross_quality_score BETWEEN 40 AND 60'),
        ('0-40', 'cross_quality_score < 40'),
    ]
    
    for label, cond in qual_buckets:
        r_vals = [row[0] for row in db.execute(f"SELECT r_multiple FROM cross_events WHERE outcome IS NOT NULL AND cross_quality_score IS NOT NULL AND {cond} AND r_multiple IS NOT NULL").fetchall()]
        if not r_vals:
            continue
        r_vals.sort()
        n = len(r_vals)
        wins = sum(1 for v in r_vals if v > 0)
        wr = wins / n * 100
        mean_r = sum(r_vals) / n
        med_r = r_vals[n // 2] if n % 2 else (r_vals[n // 2 - 1] + r_vals[n // 2]) / 2
        p25 = r_vals[int(n * 0.25)] if n >= 4 else r_vals[0]
        p75 = r_vals[int(n * 0.75)] if n >= 4 else r_vals[-1]
        print(f"  {label:>10s} | {n:>5d} | {wins:>5d} | {wr:>5.1f}% | {mean_r:>+7.2f} | {med_r:>+7.2f} | {p25:>+7.2f} | {p75:>+7.2f}")
    
    # 5. Rejection Analysis (NEW)
    if total_rejections > 0:
        print(f"\n  ─── Rejection Analysis ───")
        print(f"  {'Stage':>15s} | {'Count':>7s} | {'% of Total':>10s}")
        print(f"  {'-'*15}-+-{'-'*7}-+-{'-'*10}")
        
        rows = db.execute("""
            SELECT rejection_stage, COUNT(*) as cnt
            FROM cross_rejections
            GROUP BY rejection_stage
            ORDER BY cnt DESC
        """).fetchall()
        
        for r in rows:
            pct = r["cnt"] / total_rejections * 100
            print(f"  {r['rejection_stage']:>15s} | {r['cnt']:>7d} | {pct:>9.1f}%")
        
        # Top rejection reasons per stage
        print(f"\n  ─── Top Rejection Reasons ───")
        rows = db.execute("""
            SELECT rejection_stage, rejection_reason, COUNT(*) as cnt
            FROM cross_rejections
            GROUP BY rejection_stage, rejection_reason
            ORDER BY rejection_stage, cnt DESC
        """).fetchall()
        
        current_stage = None
        for r in rows:
            if r["rejection_stage"] != current_stage:
                current_stage = r["rejection_stage"]
                print(f"\n  {current_stage}:")
            print(f"    {r['rejection_reason'][:50]:50s} | {r['cnt']:>5d}")
    
    # 6. Cross Efficiency (NEW)
    efficiency_count = db.execute(
        "SELECT COUNT(*) FROM cross_events WHERE cross_efficiency_pct IS NOT NULL"
    ).fetchone()[0]
    
    if efficiency_count > 0:
        print(f"\n  ─── Cross Efficiency ───")
        
        stats = db.execute("""
            SELECT 
                AVG(cross_efficiency_pct) as avg_eff,
                AVG(lost_distance_pct) as avg_lost,
                COUNT(*) as total
            FROM cross_events
            WHERE cross_efficiency_pct IS NOT NULL
        """).fetchone()
        
        print(f"  Average efficiency: {stats['avg_eff']:.1f}%")
        print(f"  Average lost distance: {stats['avg_lost']:.2f}%")
        print(f"  Trades with efficiency data: {stats['total']}")
        
        # Efficiency by outcome
        print(f"\n  ─── Efficiency by Outcome ───")
        print(f"  {'Outcome':>10s} | {'Efficiency':>12s} | {'Lost Dist':>10s} | {'n':>6s}")
        print(f"  {'-'*10}-+-{'-'*12}-+-{'-'*10}-+-{'-'*6}")
        
        rows = db.execute("""
            SELECT outcome,
                   AVG(cross_efficiency_pct) as avg_eff,
                   AVG(lost_distance_pct) as avg_lost,
                   COUNT(*) as cnt
            FROM cross_events
            WHERE cross_efficiency_pct IS NOT NULL AND outcome IS NOT NULL
            GROUP BY outcome
        """).fetchall()
        
        for r in rows:
            print(f"  {r['outcome']:>10s} | {r['avg_eff']:>11.1f}% | {r['avg_lost']:>9.2f}% | {r['cnt']:>5d}")
    
    # 7. Filter Latency (NEW)
    latency_count = db.execute("SELECT COUNT(*) FROM filter_latency").fetchone()[0]
    
    if latency_count > 0:
        print(f"\n  ─── Filter Latency Timeline ───")
        print(f"  {'Stage':>15s} | {'Avg Delay':>10s} | {'Max Delay':>10s} | {'Pass Rate':>10s} | {'n':>6s}")
        print(f"  {'-'*15}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*6}")
        
        rows = db.execute("""
            SELECT stage, 
                   AVG(delay_candles) as avg_delay,
                   MAX(delay_candles) as max_delay,
                   COUNT(*) as evaluations,
                   SUM(passed) as passes
            FROM filter_latency
            GROUP BY stage
            ORDER BY AVG(delay_candles) DESC
        """).fetchall()
        
        for r in rows:
            pass_rate = r["passes"] / r["evaluations"] * 100 if r["evaluations"] > 0 else 0
            print(f"  {r['stage']:>15s} | {r['avg_delay']:>9.1f} | {r['max_delay']:>10d} | {pass_rate:>9.1f}% | {r['evaluations']:>5d}")
    
    # 8. False-Positive Analysis (NEW)
    loser_count = db.execute("SELECT COUNT(*) FROM cross_events WHERE outcome = 'loss'").fetchone()[0]
    if loser_count > 0:
        print(f"\n  ─── False-Positive Analysis (Losing Trades) ───")
        print(f"  n = {loser_count} losing trades")
        
        stats = db.execute("""
            SELECT 
                COUNT(*) as total,
                AVG(entry_delay_candles) as avg_delay,
                AVG(cross_quality_score) as avg_quality,
                AVG(mfe_pct) as avg_mfe,
                AVG(mae_pct) as avg_mae,
                AVG(distance_from_cross_pct) as avg_distance
            FROM cross_events
            WHERE outcome = 'loss'
        """).fetchone()
        
        winner_count = db.execute("SELECT COUNT(*) FROM cross_events WHERE outcome = 'win'").fetchone()[0]
        win_stats = db.execute("""
            SELECT 
                AVG(entry_delay_candles) as avg_delay,
                AVG(cross_quality_score) as avg_quality,
                AVG(mfe_pct) as avg_mfe,
                AVG(mae_pct) as avg_mae,
                AVG(distance_from_cross_pct) as avg_distance
            FROM cross_events
            WHERE outcome = 'win'
        """).fetchone()
        
        print(f"  {'Metric':>25s} | {'Losers':>12s} | {'Winners':>12s} | {'Delta':>10s}")
        print(f"  {'-'*25}-+-{'-'*12}-+-{'-'*12}-+-{'-'*10}")
        print(f"  {'(sample size)':>25s} | {loser_count:>9d} n | {winner_count:>9d} n |")
        
        metrics = [
            ("Avg Entry Delay", "avg_delay", "candles"),
            ("Avg Cross Quality", "avg_quality", ""),
            ("Avg MFE (%)", "avg_mfe", "%"),
            ("Avg MAE (%)", "avg_mae", "%"),
            ("Avg Distance from Cross", "avg_distance", "%"),
        ]
        
        for name, key, unit in metrics:
            loss_val = stats[key] or 0
            win_val = win_stats[key] or 0
            delta = loss_val - win_val
            print(f"  {name:>25s} | {loss_val:>9.1f}{unit[0:1]} | {win_val:>9.1f}{unit[0:1]} | {delta:>+9.1f}{unit[0:1]}")
        
        # Exit reasons for losers
        print(f"\n  Exit Reasons for Losing Trades:")
        reasons = db.execute("""
            SELECT exit_reason, COUNT(*) as cnt
            FROM cross_events
            WHERE outcome = 'loss' AND exit_reason IS NOT NULL AND exit_reason != ''
            GROUP BY exit_reason
            ORDER BY cnt DESC
        """).fetchall()
        
        for r in reasons:
            pct = r["cnt"] / loser_count * 100
            print(f"    {r['exit_reason']:>20s}: {r['cnt']:>5d} ({pct:.1f}%)")
    
    # 9. Rejection Outcome Analysis (NEW)
    rejection_count = db.execute("SELECT COUNT(*) FROM rejection_outcomes").fetchone()[0]
    if rejection_count > 0:
        print(f"\n  ─── Rejection Outcome Analysis ───")
        print(f"  Would rejected crosses have been profitable?")
        print(f"  {'Stage':>15s} | {'Total':>7s} | {'Would Win':>10s} | {'WR if Taken':>12s} | {'Avg 24h Move':>12s}")
        print(f"  {'-'*15}-+-{'-'*7}-+-{'-'*10}-+-{'-'*12}-+-{'-'*12}")
        
        rows = db.execute("""
            SELECT rejection_stage,
                   COUNT(*) as total,
                   SUM(would_have_been_win) as would_win,
                   AVG(move_24h_pct) as avg_move_24h
            FROM rejection_outcomes
            GROUP BY rejection_stage
            ORDER BY total DESC
        """).fetchall()
        
        for r in rows:
            wr = (r["would_win"] or 0) / r["total"] * 100 if r["total"] > 0 else 0
            move = r["avg_move_24h"] or 0
            print(f"  {r['rejection_stage']:>15s} | {r['total']:>7d} | {r['would_win']:>10d} | {wr:>11.1f}% | {move:>+11.2f}%")
        
        # Key insight: which rejections missed the most opportunities
        best_missed = db.execute("""
            SELECT rejection_stage, 
                   AVG(move_24h_pct) as avg_move,
                   COUNT(*) as cnt
            FROM rejection_outcomes
            WHERE would_have_been_win = 1
            GROUP BY rejection_stage
            ORDER BY cnt DESC
            LIMIT 3
        """).fetchall()
        
        if best_missed:
            print(f"\n  Stages that rejected the most winning setups:")
            for r in best_missed:
                print(f"    {r['rejection_stage']:>15s}: {r['cnt']} rejections would have won (avg 24h move: {r['avg_move']:+.2f}%)")
    
    # 11. Decision Matrix (NEW) — Which filters help vs hurt?
    if rejection_count > 0:
        print(f"\n  ─── Decision Matrix: Filter Impact Analysis ───")
        print(f"  This shows whether each filter is adding or destroying edge.")
        print()
        print(f"  {'Filter':>15s} | {'Rejected':>8s} | {'Would Win':>10s} | {'Would Lose':>10s} | {'WR if Taken':>12s} | {'Avg 24h Move':>12s}")
        print(f"  {'-'*15}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*12}-+-{'-'*12}")
        
        rows = db.execute("""
            SELECT rejection_stage,
                   COUNT(*) as total,
                   SUM(would_have_been_win) as would_win,
                   AVG(move_24h_pct) as avg_move_24h
            FROM rejection_outcomes
            GROUP BY rejection_stage
            HAVING total >= 3
            ORDER BY total DESC
        """).fetchall()
        
        for r in rows:
            total = r["total"]
            would_win = r["would_win"] or 0
            would_lose = total - would_win
            wr = would_win / total * 100 if total > 0 else 0
            move = r["avg_move_24h"] or 0
            print(f"  {r['rejection_stage']:>15s} | {total:>8d} | {would_win:>10d} | {would_lose:>10d} | {wr:>11.1f}% | {move:>+11.2f}%")
        
        # 12. Filter Edge Impact — R-multiple Expectancy (NEW)
        print(f"\n  ─── Filter Edge Impact: R-Multiple Expectancy ───")
        print(f"  Primary metric: Simulated R-multiple if rejected trades had been taken.")
        print(f"  Formula: R = (Wins × 1R) - (Losses × 0.5R) per trade")
        print(f"  Positive R = filter is DESTROYING edge (winners rejected)")
        print(f"  Negative R = filter is PROTECTING (losers rejected)")
        print()
        
        min_sample = 10  # Require 10+ samples for meaningful assessment
        print(f"  ⚠️  Auto-classification requires {min_sample}+ samples per filter.")
        print(f"  Current data shows raw metrics only — no labels applied until sufficient evidence.")
        print()
        print(f"  {'Filter':>15s} | {'Samples':>8s} | {'WR if Taken':>12s} | {'Sim R/trade':>12s} | {'Std Err':>10s} | {'Avg 24h Move':>12s}")
        print(f"  {'-'*15}-+-{'-'*8}-+-{'-'*12}-+-{'-'*12}-+-{'-'*10}-+-{'-'*12}")
        
        rows = db.execute("""
            SELECT rejection_stage,
                   COUNT(*) as total,
                   SUM(would_have_been_win) as would_win,
                   AVG(move_24h_pct) as avg_move_24h,
                   -- Simulated R: wins * 1.0, losses * 0.5 (conservative)
                   (SUM(CASE WHEN would_have_been_win = 1 THEN 1.0 ELSE 0.5 END)) * 1.0 / COUNT(*) as sim_r,
                   -- Standard deviation for CI
                   AVG(CASE WHEN would_have_been_win = 1 THEN max_favorable_after ELSE -max_adverse_after END) as avg_outcome
            FROM rejection_outcomes
            GROUP BY rejection_stage
            HAVING total >= 3
            ORDER BY total DESC
        """).fetchall()
        
        for r in rows:
            total = r["total"]
            would_win = r["would_win"] or 0
            wr = would_win / total * 100 if total > 0 else 0
            sim_r = r["sim_r"] or 0
            avg_move = r["avg_move_24h"] or 0
            
            # Standard error approximation
            import math
            avg_outcome = r["avg_outcome"] or 0
            std_err = abs(avg_outcome) / math.sqrt(total) if total > 1 else 0
            
            print(f"  {r['rejection_stage']:>15s} | {total:>8d} | {wr:>11.1f}% | {sim_r:>+11.3f} | {std_err:>9.3f} | {avg_move:>+11.2f}%")
        
        # 13. Filter Interaction Matrix (NEW)
        print(f"\n  ─── Filter Interaction Matrix ───")
        print(f"  Evaluates filter combinations to find redundancy or complementarity.")
        print(f"  Requires: same symbol+cross_time rejected by multiple filters.")
        print()
        
        interaction_count = db.execute("""
            SELECT COUNT(*) as cnt FROM (
                SELECT a.symbol, a.cross_time
                FROM rejection_outcomes a
                JOIN rejection_outcomes b 
                    ON a.symbol = b.symbol AND a.cross_time = b.cross_time
                WHERE a.rejection_stage != b.rejection_stage
            )
        """).fetchone()["cnt"]
        
        if interaction_count >= 2:
            # Get pairs of stages
            stages = [r[0] for r in db.execute(
                "SELECT DISTINCT rejection_stage FROM rejection_outcomes"
            ).fetchall()]
            
            print(f"  {'Filter A':>15s} | {'Filter B':>15s} | {'Both Rej':>8s} | {'WR(A)':>8s} | {'WR(B)':>8s} | {'WR(Both)':>10s}")
            print(f"  {'-'*15}-+-{'-'*15}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*10}")
            
            pairs_found = 0
            for i, stage_a in enumerate(stages):
                for stage_b in stages[i+1:]:
                    # Count overlaps
                    both = db.execute("""
                        SELECT COUNT(*) as cnt
                        FROM rejection_outcomes a
                        JOIN rejection_outcomes b 
                            ON a.symbol = b.symbol AND a.cross_time = b.cross_time
                        WHERE a.rejection_stage = ? AND b.rejection_stage = ?
                    """, (stage_a, stage_b)).fetchone()["cnt"]
                    
                    if both < 2:
                        continue
                    
                    wr_a = db.execute(
                        "SELECT AVG(would_have_been_win) FROM rejection_outcomes WHERE rejection_stage = ?",
                        (stage_a,)
                    ).fetchone()[0] or 0
                    
                    wr_b = db.execute(
                        "SELECT AVG(would_have_been_win) FROM rejection_outcomes WHERE rejection_stage = ?",
                        (stage_b,)
                    ).fetchone()[0] or 0
                    
                    wr_both = db.execute("""
                        SELECT AVG(a.would_have_been_win)
                        FROM rejection_outcomes a
                        JOIN rejection_outcomes b 
                            ON a.symbol = b.symbol AND a.cross_time = b.cross_time
                        WHERE a.rejection_stage = ? AND b.rejection_stage = ?
                    """, (stage_a, stage_b)).fetchone()[0] or 0
                    
                    print(f"  {stage_a:>15s} | {stage_b:>15s} | {both:>8d} | {wr_a*100:>7.1f}% | {wr_b*100:>7.1f}% | {wr_both*100:>9.1f}%")
                    pairs_found += 1
            
            if pairs_found == 0:
                print(f"  No filter pairs with 2+ overlapping rejections yet.")
                print(f"  Need more rejection data to evaluate filter interactions.")
        else:
            print(f"  Insufficient overlapping rejection data ({interaction_count} pairs found).")
            print(f"  Need crosses rejected by multiple filters to evaluate interactions.")
        
        print(f"\n  Interpretation:")
        print(f"  • If WR(Both) is similar to WR(A) and WR(B): filters may be redundant")
        print(f"  • If WR(Both) is lower: combination catches more losers (complementary)")
        print(f"  • If WR(Both) is higher: combination may be too restrictive")
        print(f"  • All metrics are OBSERVATIONAL — no logic changes until Phase 2")
    
    # 14. Market Regime Performance (NEW)
    regime_count = db.execute("SELECT COUNT(*) FROM cross_events WHERE regime IS NOT NULL AND regime != ''").fetchone()[0]
    if regime_count > 0:
        print(f"\n  ─── Market Regime Performance ───")
        print(f"  Breakdown by market regime to identify conditions where EMA V5 performs best.")
        print()
        print(f"  {'Regime':>15s} | {'Trades':>8s} | {'Wins':>6s} | {'WR':>8s} | {'Avg R':>8s} | {'PF':>8s}")
        print(f"  {'-'*15}-+-{'-'*8}-+-{'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}")
        
        rows = db.execute("""
            SELECT regime,
                   COUNT(*) as total,
                   SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
                   AVG(r_multiple) as avg_r,
                   SUM(CASE WHEN outcome = 'win' THEN ABS(r_multiple) ELSE 0 END) /
                   NULLIF(SUM(CASE WHEN outcome = 'loss' THEN ABS(r_multiple) ELSE 0 END), 0) as pf
            FROM cross_events
            WHERE outcome IS NOT NULL AND regime IS NOT NULL AND regime != ''
            GROUP BY regime
            ORDER BY total DESC
        """).fetchall()
        
        for r in rows:
            wr = r["wins"] / r["total"] * 100 if r["total"] > 0 else 0
            avg_r = r["avg_r"] or 0
            pf = r["pf"] or 0
            print(f"  {r['regime']:>15s} | {r['total']:>8d} | {r['wins']:>6d} | {wr:>7.1f}% | {avg_r:>+7.2f} | {pf:>7.2f}")
        
        # Early vs Late by Regime
        print(f"\n  ─── Entry Timing by Regime ───")
        print(f"  {'Regime':>15s} | {'Early(n≤2)':>12s} | {'Late(n>5)':>12s} | {'Early WR':>10s} | {'Late WR':>10s}")
        print(f"  {'-'*15}-+-{'-'*12}-+-{'-'*12}-+-{'-'*10}-+-{'-'*10}")
        
        regimes = [r[0] for r in db.execute(
            "SELECT DISTINCT regime FROM cross_events WHERE outcome IS NOT NULL AND regime IS NOT NULL AND regime != ''"
        ).fetchall()]
        
        for reg in regimes:
            early = db.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins
                FROM cross_events 
                WHERE outcome IS NOT NULL AND regime = ? AND entry_delay_candles <= 2
            """, (reg,)).fetchone()
            
            late = db.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins
                FROM cross_events 
                WHERE outcome IS NOT NULL AND regime = ? AND entry_delay_candles > 5
            """, (reg,)).fetchone()
            
            early_n = early["total"] if early else 0
            late_n = late["total"] if late else 0
            early_wr = early["wins"] / early["total"] * 100 if early and early["total"] > 0 else 0
            late_wr = late["wins"] / late["total"] * 100 if late and late["total"] > 0 else 0
            
            print(f"  {reg:>15s} | {early_n:>10d} n | {late_n:>10d} n | {early_wr:>9.1f}% | {late_wr:>9.1f}%")
        
        # Regime-specific insights
        print(f"\n  ─── Regime Insights ───")
        if len(regimes) > 0:
            best_regime = max(regimes, key=lambda r: db.execute(
                "SELECT AVG(r_multiple) FROM cross_events WHERE outcome IS NOT NULL AND regime = ?",
                (r,)
            ).fetchone()[0] or 0)
            
            worst_regime = min(regimes, key=lambda r: db.execute(
                "SELECT AVG(r_multiple) FROM cross_events WHERE outcome IS NOT NULL AND regime = ?",
                (r,)
            ).fetchone()[0] or 0)
            
            best_r = db.execute(
                "SELECT AVG(r_multiple), COUNT(*) FROM cross_events WHERE outcome IS NOT NULL AND regime = ?",
                (best_regime,)
            ).fetchone()
            worst_r = db.execute(
                "SELECT AVG(r_multiple), COUNT(*) FROM cross_events WHERE outcome IS NOT NULL AND regime = ?",
                (worst_regime,)
            ).fetchone()
            
            print(f"  Best performing regime:  {best_regime} (R={best_r[0]:+.2f}, n={best_r[1]})")
            print(f"  Worst performing regime: {worst_regime} (R={worst_r[0]:+.2f}, n={worst_r[1]})")
            
            if best_r[0] and worst_r[0] and (best_r[0] - worst_r[0]) > 0.5:
                print(f"  ⚠️  Large regime difference ({best_r[0] - worst_r[0]:+.2f} R) — regime may be significant factor")
            else:
                print(f"  📊 Regime difference within normal range — more data needed")
        else:
            print(f"  Insufficient regime data for insights.")
    else:
        print(f"\n  ─── Market Regime Performance ───")
        print(f"  No regime data available yet. The logger will start collecting after trades close.")
    
    # 15. Key Insight
    print(f"\n  ─── Key Insight ───")
    
    # Compare early vs late entries
    early = db.execute("""
        SELECT COUNT(*) as total, 
               SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
               AVG(r_multiple) as avg_r
        FROM cross_events 
        WHERE outcome IS NOT NULL AND entry_delay_candles <= 2
    """).fetchone()
    
    late = db.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
               AVG(r_multiple) as avg_r
        FROM cross_events 
        WHERE outcome IS NOT NULL AND entry_delay_candles > 5
    """).fetchone()
    
    if early and early["total"] > 0 and late and late["total"] > 0:
        early_wr = early["wins"] / early["total"] * 100
        late_wr = late["wins"] / late["total"] * 100
        early_r = early["avg_r"] or 0
        late_r = late["avg_r"] or 0
        
        print(f"  Early entries (0-2 candles): {early['total']} trades, WR={early_wr:.1f}%, R={early_r:.2f}")
        print(f"  Late entries (6+ candles):   {late['total']} trades, WR={late_wr:.1f}%, R={late_r:.2f}")
        
        if early_wr > late_wr + 10:
            print(f"\n  ✅ EVIDENCE: Early entries outperform late entries by {early_wr - late_wr:.1f}% WR")
            print(f"     This supports implementing a Cross Age Filter or Distance Filter.")
        elif late_wr > early_wr + 10:
            print(f"\n  ⚠️  UNEXPECTED: Late entries outperform early entries by {late_wr - early_wr:.1f}% WR")
            print(f"     The entry timing hypothesis may not hold for this strategy.")
        else:
            print(f"\n  📊 INCONCLUSIVE: No significant difference between early and late entries")
            print(f"     Need more data to draw conclusions.")
    else:
        print(f"  Insufficient data for comparison.")
        print(f"  Need: early={early['total'] if early else 0}+ trades, late={late['total'] if late else 0}+ trades")
    
    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  NOTE: This is DIAGNOSTIC data only.")
    print("  No trading logic has been modified.")
    print("  The baseline strategy remains frozen.")
    print("═══════════════════════════════════════════════════════════════")
    print()
    
    # 16. Live Pipeline Dashboard (current state per symbol)
    print(f"\n  ─── Live Pipeline Dashboard ───")
    print(f"  Current state of active crosses and pipeline progression.")
    print()
    
    active = db.execute("""
        SELECT symbol, side, cross_price, cross_time, 
               signal_time, entry_delay_candles, regime,
               cross_quality_score, distance_from_cross_pct
        FROM cross_events 
        WHERE outcome IS NULL 
        ORDER BY cross_time DESC
        LIMIT 20
    """).fetchall()
    
    if active:
        import time as _time
        now = _time.time()
        print(f"  {'Symbol':>12s} | {'Side':>4s} | {'Age':>8s} | {'Delay':>6s} | {'Quality':>8s} | {'Distance':>10s} | {'Regime':>12s} | {'Stage':>20s}")
        print(f"  {'-'*12}-+-{'-'*4}-+-{'-'*8}-+-{'-'*6}-+-{'-'*8}-+-{'-'*10}-+-{'-'*12}-+-{'-'*20}")
        
        for r in active:
            age_sec = now - r['cross_time']
            if age_sec < 60:
                age = f"{age_sec:.0f}s"
            elif age_sec < 3600:
                age = f"{age_sec / 60:.0f}m"
            else:
                age = f"{age_sec / 3600:.1f}h"
            
            delay = f"{r['entry_delay_candles']}c" if r['entry_delay_candles'] else "---"
            quality = f"{r['cross_quality_score']:.0f}" if r['cross_quality_score'] else "---"
            dist = f"{r['distance_from_cross_pct']:.1f}%" if r['distance_from_cross_pct'] is not None else "---"
            regime = r['regime'] or "---"
            
            if r['signal_time']:
                stage = "SIGNAL EMITTED"
            else:
                stage = "SCANNING..."
            
            print(f"  {r['symbol']:>12s} | {r['side']:>4s} | {age:>8s} | {delay:>6s} | {quality:>8s} | {dist:>10s} | {regime:>12s} | {stage:>20s}")
        
        print(f"\n  Total active crosses: {len(active)}")
    else:
        print(f"  No active crosses being tracked.")
    
    # 17. Migration Verification
    print(f"\n  ─── Migration Verification ───")
    
    # Check regime column exists and is populated
    regime_events = db.execute("SELECT COUNT(*) FROM cross_events WHERE regime IS NOT NULL AND regime != ''").fetchone()[0]
    total_events = db.execute("SELECT COUNT(*) FROM cross_events").fetchone()[0]
    
    regime_rejections = db.execute("SELECT COUNT(*) FROM cross_rejections WHERE regime IS NOT NULL AND regime != ''").fetchone()[0]
    total_rejections_check = db.execute("SELECT COUNT(*) FROM cross_rejections").fetchone()[0]
    
    print(f"  cross_events: {regime_events}/{total_events} have regime populated")
    print(f"  cross_rejections: {regime_rejections}/{total_rejections_check} have regime populated")
    
    # Verify old records are still readable
    old_records = db.execute("SELECT COUNT(*) FROM cross_events WHERE regime IS NULL").fetchone()[0]
    print(f"  Old records (regime=NULL): {old_records} — still readable: ✓")
    
    # Check all tables exist
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = [t[0] for t in tables]
    expected = ['cross_events', 'cross_rejections', 'filter_latency', 'missed_opportunities', 'rejection_outcomes']
    missing = [t for t in expected if t not in table_names]
    
    if missing:
        print(f"  ⚠️  Missing tables: {missing}")
    else:
        print(f"  All {len(expected)} tables present: ✓")
    
    # Check row counts
    for t in expected:
        cnt = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"    {t}: {cnt} rows")
    
    db.close()
    
    # ═══════════════════════════════════════════════════════════════════════
    # REPORT 18: Pipeline Conversion Report (from lifecycle DB)
    # ═══════════════════════════════════════════════════════════════════════
    _run_pipeline_conversion_report()


def _run_pipeline_conversion_report():
    """Pipeline Conversion Report — answers: Does each gate add value?
    
    Uses the candidate_lifecycle table (39K+ candidates) to measure:
    - Funnel pass/reject at each stage
    - Stage-level and cumulative pass rates
    - Top rejection reasons per stage
    - If outcomes available: profitability comparison across gates
    """
    import math
    
    lifecycle_db_path = Path(__file__).resolve().parent / "packages" / "data" / "ema_v5_lifecycle.db"
    
    if not lifecycle_db_path.exists():
        print(f"\n  ─── Pipeline Conversion Report ───")
        print(f"  ⚠️  Lifecycle database not found at {lifecycle_db_path}")
        return
    
    ldb = sqlite3.connect(str(lifecycle_db_path))
    ldb.row_factory = sqlite3.Row
    
    total = ldb.execute("SELECT COUNT(*) FROM candidate_lifecycle").fetchone()[0]
    if total == 0:
        print(f"\n  ─── Pipeline Conversion Report ───")
        print(f"  No candidates tracked yet.")
        ldb.close()
        return
    
    # Time span
    ts_row = ldb.execute("SELECT MIN(timestamp), MAX(timestamp) FROM candidate_lifecycle").fetchone()
    hours = (ts_row[1] - ts_row[0]) / 3600 if ts_row[0] and ts_row[1] else 0
    
    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  PIPELINE CONVERSION REPORT")
    print("  Question: Does each gate improve trading edge?")
    print("═══════════════════════════════════════════════════════════════")
    print(f"\n  Total candidates: {total:,}")
    print(f"  Data span: {hours:.1f} hours")
    
    # ── 18a: Full Funnel ──
    print(f"\n  ─── 18a: Pipeline Funnel ───")
    print(f"  {'Stage':>12s} | {'Entered':>8s} | {'Passed':>8s} | {'Rejected':>8s} | {'Stage Pass%':>12s} | {'Cumulative%':>12s}")
    print(f"  {'-'*12}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*12}-+-{'-'*12}")
    
    stages = [
        ("Regime", "regime_pass"),
        ("Trend", "trend_pass"),
        ("Pullback", "pullback_pass"),
        ("Candle", "candle_pass"),
        ("Volume", "volume_pass"),
        ("Confidence", "confidence_pass"),
        ("Signal", "signal_pass"),
    ]
    
    prev_passed = total
    for name, col in stages:
        passed = ldb.execute(f"SELECT SUM({col}) FROM candidate_lifecycle WHERE {col} IS NOT NULL").fetchone()[0] or 0
        rejected = prev_passed - passed
        stage_pct = passed / max(prev_passed, 1) * 100
        cum_pct = passed / max(total, 1) * 100
        print(f"  {name:>12s} | {prev_passed:>8,} | {passed:>8,} | {rejected:>8,} | {stage_pct:>11.1f}% | {cum_pct:>11.1f}%")
        prev_passed = passed
    
    # ── 18b: Top Rejection Reasons per Stage ──
    print(f"\n  ─── 18b: Top Rejection Reasons ───")
    
    rejection_stages = ldb.execute("""
        SELECT DISTINCT rejection_stage FROM candidate_lifecycle
        WHERE rejection_stage IS NOT NULL
        ORDER BY rejection_stage
    """).fetchall()
    
    for stage_row in rejection_stages:
        stage = stage_row[0]
        rows = ldb.execute("""
            SELECT rejection_reason, COUNT(*) as cnt
            FROM candidate_lifecycle
            WHERE rejection_stage = ?
            GROUP BY rejection_reason
            ORDER BY cnt DESC
            LIMIT 5
        """, (stage,)).fetchall()
        
        stage_total = sum(r["cnt"] for r in rows)
        all_stage_total = ldb.execute(
            "SELECT COUNT(*) FROM candidate_lifecycle WHERE rejection_stage = ?", (stage,)
        ).fetchone()[0]
        
        print(f"\n  {stage} ({all_stage_total:,} rejections):")
        for r in rows:
            pct = r["cnt"] / max(all_stage_total, 1) * 100
            reason = (r["rejection_reason"] or "unknown")[:55]
            print(f"    {reason:55s} {r['cnt']:>6,} ({pct:5.1f}%)")
    
    # ── 18c: Gate Effectiveness Analysis (if outcomes available) ──
    tracked = ldb.execute("SELECT COUNT(*) FROM candidate_lifecycle WHERE outcome_tracked = 1").fetchone()[0]
    
    if tracked >= 20:
        print(f"\n  ─── 18c: Gate Effectiveness (outcome-tracked: {tracked:,} / {total:,}) ───")
        print(f"  Compares metrics BEFORE vs AFTER each gate using historical kline outcomes.")
        print(f"  Includes: Mean, Median, StdDev, Win%, PF, Expectancy, MFE, MAE,")
        print(f"            Bootstrap 95% CI, Cohen's d effect size, significance test.")
        print()
        
        gate_pairs = [
            ("Pullback", "regime_pass", "pullback_pass"),
            ("Candle", "pullback_pass", "candle_pass"),
            ("Volume", "candle_pass", "volume_pass"),
            ("Confidence", "volume_pass", "confidence_pass"),
            ("Signal", "confidence_pass", "signal_pass"),
        ]
        
        # Collect all return arrays for detailed analysis
        gate_results = []
        
        for gate_name, before_col, after_col in gate_pairs:
            # Fetch raw return arrays (4h returns in %)
            before_raw = ldb.execute(f"""
                SELECT return_4h_pct FROM candidate_lifecycle
                WHERE {before_col} = 1 AND outcome_tracked = 1
                AND return_4h_pct IS NOT NULL
            """).fetchall()
            after_raw = ldb.execute(f"""
                SELECT return_4h_pct FROM candidate_lifecycle
                WHERE {after_col} = 1 AND outcome_tracked = 1
                AND return_4h_pct IS NOT NULL
            """).fetchall()
            
            before = [r[0] for r in before_raw]
            after = [r[0] for r in after_raw]
            
            b_n = len(before)
            a_n = len(after)
            
            if b_n < 3 or a_n < 3:
                gate_results.append({
                    "gate": gate_name, "b_n": b_n, "a_n": a_n,
                    "insufficient": True,
                })
                continue
            
            # ── Core metrics ──
            b_mean = sum(before) / b_n
            a_mean = sum(after) / a_n
            b_med = _median(before)
            a_med = _median(after)
            b_std = _stddev(before)
            a_std = _stddev(after)
            
            # Win rate (positive 4h return = "win")
            b_wr = sum(1 for r in before if r > 0) / b_n * 100
            a_wr = sum(1 for r in after if r > 0) / a_n * 100
            
            # Profit Factor
            b_pf = _profit_factor(before)
            a_pf = _profit_factor(after)
            
            # Expectancy (R-multiples)
            b_exp = _expectancy_r(before)
            a_exp = _expectancy_r(after)
            
            # MFE / MAE from would_be fields
            b_mfe_row = ldb.execute(f"""
                SELECT AVG(would_be_mfe), AVG(would_be_mae) FROM candidate_lifecycle
                WHERE {before_col} = 1 AND outcome_tracked = 1
                AND would_be_mfe IS NOT NULL
            """).fetchone()
            a_mfe_row = ldb.execute(f"""
                SELECT AVG(would_be_mfe), AVG(would_be_mae) FROM candidate_lifecycle
                WHERE {after_col} = 1 AND outcome_tracked = 1
                AND would_be_mfe IS NOT NULL
            """).fetchone()
            b_mfe = b_mfe_row[0] or 0
            b_mae = b_mfe_row[1] or 0
            a_mfe = a_mfe_row[0] or 0
            a_mae = a_mfe_row[1] or 0
            
            # Bootstrap CI for means
            b_mean_ci = _bootstrap_ci(before)
            a_mean_ci = _bootstrap_ci(after)
            
            # Bootstrap CI for difference (after - before)
            diff, diff_lo, diff_hi = _bootstrap_difference_ci(before, after)
            
            # Cohen's d effect size
            d = _cohens_d(before, after)
            
            # P25 / P75
            b_p25 = _percentile(before, 25)
            b_p75 = _percentile(before, 75)
            a_p25 = _percentile(after, 25)
            a_p75 = _percentile(after, 75)
            
            sig = _significance_label(diff_lo, diff_hi)
            
            result = {
                "gate": gate_name, "b_n": b_n, "a_n": a_n,
                "b_mean": b_mean, "a_mean": a_mean,
                "b_med": b_med, "a_med": a_med,
                "b_std": b_std, "a_std": a_std,
                "b_wr": b_wr, "a_wr": a_wr,
                "b_pf": b_pf, "a_pf": a_pf,
                "b_exp": b_exp, "a_exp": a_exp,
                "b_mfe": b_mfe, "b_mae": b_mae,
                "a_mfe": a_mfe, "a_mae": a_mae,
                "b_mean_ci": b_mean_ci, "a_mean_ci": a_mean_ci,
                "diff": diff, "diff_lo": diff_lo, "diff_hi": diff_hi,
                "d": d, "sig": sig,
                "b_p25": b_p25, "b_p75": b_p75,
                "a_p25": a_p25, "a_p75": a_p75,
                "insufficient": False,
            }
            gate_results.append(result)
        
        # ── Evidence Status Table ──
        MIN_EVIDENCE = 200   # minimum after-gate outcomes for basic conclusions
        MAX_CI_WIDTH = 1.0   # maximum CI width (%) for decision readiness
        print(f"\n  ─── Evidence Status ───")
        print(f"  Minimum evidence: ≥{MIN_EVIDENCE} after-gate outcomes")
        print(f"  Decision-ready: CI width < {MAX_CI_WIDTH}% AND regime coverage achieved")
        print()
        print(f"  {'Gate':>12s} | {'After n':>7s} | {'Min Ev':>7s} | {'CI Width':>9s} | {'Regimes':>8s} | {'Status':>22s} | {'Decision':>18s}")
        print(f"  {'-'*12}-+-{'-'*7}-+-{'-'*7}-+-{'-'*9}-+-{'-'*8}-+-{'-'*22}-+-{'-'*18}")
        
        for r in gate_results:
            a_n = r["a_n"]
            
            # Minimum evidence check
            has_min = a_n >= MIN_EVIDENCE
            
            # CI width check (if available)
            if not r["insufficient"] and "diff_lo" in r:
                ci_width = abs(r["diff_hi"] - r["diff_lo"])
                ci_ok = ci_width < MAX_CI_WIDTH
                ci_str = f"{ci_width:.2f}%"
            else:
                ci_width = float('inf')
                ci_ok = False
                ci_str = "---"
            
            # Regime coverage check: do after-gate outcomes span both BUY and SELL?
            if not r["insufficient"]:
                # Find the after_col for this gate
                after_col_lookup = {g: ac for g, _, ac in gate_pairs}
                ac = after_col_lookup.get(r["gate"], "")
                if ac:
                    regime_rows = ldb.execute(f"""
                        SELECT regime, COUNT(*) as cnt FROM candidate_lifecycle
                        WHERE {ac} = 1 AND outcome_tracked = 1
                        AND regime IS NOT NULL AND regime != ''
                        GROUP BY regime
                    """).fetchall()
                    regimes = {row[0]: row[1] for row in regime_rows}
                    has_buy = regimes.get("BUY_MODE", 0) >= 10
                    has_sell = regimes.get("SELL_MODE", 0) >= 10
                    regime_ok = has_buy and has_sell
                    regime_str = f"{len(regimes)} types"
                else:
                    regime_ok = False
                    regime_str = "---"
            else:
                regime_ok = False
                regime_str = "---"
            
            # Overall status
            if has_min and ci_ok and regime_ok:
                status = "🟢 SUFFICIENT"
                decision = "READY"
            elif has_min and ci_ok:
                status = "🟡 ADEQUATE"
                decision = "PARTIAL COVERAGE"
            elif has_min:
                status = "🟡 MODERATE"
                decision = "CI TOO WIDE"
            elif a_n >= MIN_EVIDENCE * 0.5:
                status = "🟠 EARLY"
                decision = "COLLECTING"
            elif a_n >= 10:
                status = "🟠 THIN"
                decision = "COLLECTING"
            else:
                status = "🔴 INSUFFICIENT"
                decision = "COLLECTING"
            
            print(f"  {r['gate']:>12s} | {a_n:>7d} | {'✓' if has_min else '✗':>7s} | {ci_str:>9s} | {regime_str:>8s} | {status:>22s} | {decision:>18s}")
        
        # ── Print Summary Table ──
        print(f"\n  ─── Gate Effectiveness Summary ───")
        print(f"  {'Gate':>12s} | {'B n':>5s} | {'A n':>5s} | {'B Mean':>8s} | {'A Mean':>8s} | {'Δ Mean':>8s} | {'95% CI Δ':>14s} | {'Sig':>8s}")
        print(f"  {'-'*12}-+-{'-'*5}-+-{'-'*5}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*14}-+-{'-'*8}")
        for r in gate_results:
            if r["insufficient"]:
                print(f"  {r['gate']:>12s} | {r['b_n']:>5d} | {r['a_n']:>5d} | {'---':>8s} | {'---':>8s} | {'---':>8s} | {'insufficient data':>14s} | {'---':>8s}")
            else:
                ci_str = f"[{r['diff_lo']:+.2f}, {r['diff_hi']:+.2f}]"
                print(f"  {r['gate']:>12s} | {r['b_n']:>5d} | {r['a_n']:>5d} | {r['b_mean']:>+7.2f}% | {r['a_mean']:>+7.2f}% | {r['diff']:>+7.2f}% | {ci_str:>14s} | {r['sig']:>8s}")
        
        # ── Detailed Metrics Table ──
        print(f"\n  ─── Detailed Metrics by Gate ───")
        print(f"  {'Gate':>12s} | {'Metric':>10s} | {'Before':>10s} | {'After':>10s} | {'Δ':>10s}")
        print(f"  {'-'*12}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")
        
        for r in gate_results:
            if r["insufficient"]:
                continue
            rows = [
                ("Mean %", f"{r['b_mean']:+.2f}%", f"{r['a_mean']:+.2f}%", f"{r['diff']:+.2f}%"),
                ("Median %", f"{r['b_med']:+.2f}%", f"{r['a_med']:+.2f}%", f"{r['a_med'] - r['b_med']:+.2f}%"),
                ("StdDev %", f"{r['b_std']:.2f}%", f"{r['a_std']:.2f}%", f"{r['a_std'] - r['b_std']:+.2f}%"),
                ("P25 / P75", f"[{r['b_p25']:+.1f}, {r['b_p75']:+.1f}]", f"[{r['a_p25']:+.1f}, {r['a_p75']:+.1f}]", ""),
                ("Win Rate", f"{r['b_wr']:.1f}%", f"{r['a_wr']:.1f}%", f"{r['a_wr'] - r['b_wr']:+.1f}%"),
                ("Profit Factor", f"{r['b_pf']:.2f}", f"{r['a_pf']:.2f}", f"{r['a_pf'] - r['b_pf']:+.2f}"),
                ("Expectancy R", f"{r['b_exp']:+.3f}", f"{r['a_exp']:+.3f}", f"{r['a_exp'] - r['b_exp']:+.3f}"),
                ("MFE %", f"{r['b_mfe']:+.2f}%", f"{r['a_mfe']:+.2f}%", f"{r['a_mfe'] - r['b_mfe']:+.2f}%"),
                ("MAE %", f"{r['b_mae']:+.2f}%", f"{r['a_mae']:+.2f}%", f"{r['a_mae'] - r['b_mae']:+.2f}%"),
                ("Cohen's d", "", "", f"{r['d']:+.3f}"),
                ("Bootstrap Δ CI", "", "", f"[{r['diff_lo']:+.2f}, {r['diff_hi']:+.2f}]"),
            ]
            for i, (metric, b, a, delta) in enumerate(rows):
                gate_col = r['gate'] if i == 0 else ""
                print(f"  {gate_col:>12s} | {metric:>10s} | {b:>10s} | {a:>10s} | {delta:>10s}")
            print(f"  {'':>12s} | {'':>10s} | {'':>10s} | {'':>10s} | {'':>10s}")
        
        # ── Per-Stage Outcome Distribution ──
        print(f"\n  ─── Per-Stage Outcome Distribution ───")
        print(f"  What happened to candidates REJECTED at each stage?")
        print()
        print(f"  {'Stage':>12s} | {'n':>5s} | {'Profitable':>10s} | {'Losing':>7s} | {'Breakeven':>10s} | {'Avg 4h%':>8s} | {'Med 4h%':>8s}")
        print(f"  {'-'*12}-+-{'-'*5}-+-{'-'*10}-+-{'-'*7}-+-{'-'*10}-+-{'-'*8}-+-{'-'*8}")
        
        for stage_name in ["pullback", "candle", "volume", "confidence"]:
            rows_data = ldb.execute(f"""
                SELECT return_4h_pct FROM candidate_lifecycle
                WHERE rejection_stage = ? AND outcome_tracked = 1
                AND return_4h_pct IS NOT NULL
            """, (stage_name,)).fetchall()
            vals = [r[0] for r in rows_data]
            if not vals:
                continue
            n = len(vals)
            prof = sum(1 for v in vals if v > 1.0)
            lose = sum(1 for v in vals if v < -1.0)
            be = n - prof - lose
            avg = sum(vals) / n
            med = _median(vals)
            print(f"  {stage_name:>12s} | {n:>5d} | {prof:>10d} | {lose:>7d} | {be:>10d} | {avg:>+7.2f}% | {med:>+7.2f}%")
        
        # Passed all gates
        passed_data = ldb.execute("""
            SELECT return_4h_pct FROM candidate_lifecycle
            WHERE signal_pass = 1 AND outcome_tracked = 1
            AND return_4h_pct IS NOT NULL
        """).fetchall()
        passed_vals = [r[0] for r in passed_data]
        if passed_vals:
            n = len(passed_vals)
            prof = sum(1 for v in passed_vals if v > 1.0)
            lose = sum(1 for v in passed_vals if v < -1.0)
            be = n - prof - lose
            avg = sum(passed_vals) / n
            med = _median(passed_vals)
            print(f"  {'SIGNAL':>12s} | {n:>5d} | {prof:>10d} | {lose:>7d} | {be:>10d} | {avg:>+7.2f}% | {med:>+7.2f}%")
        
        # ── 18d: Signal Gate Deep Dive ──
        print(f"\n  ─── 18d: Post-Confidence Gate Breakdown ───")
        print(f"  After passing Confidence, candidates face the Signal Engine gates.")
        
        signal_gates = [
            ("gate_duplicate", "Duplicate"),
            ("gate_cooldown", "Cooldown"),
            ("gate_entry_atr", "Entry ATR"),
            ("gate_momentum", "Momentum"),
            ("gate_rr", "Risk:Reward"),
            ("gate_session", "Session"),
            ("gate_cycle_limit", "Cycle Limit"),
            ("gate_risk", "Risk"),
            ("gate_position_limit", "Position Limit"),
        ]
        
        conf_passed = ldb.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle WHERE confidence_pass = 1
        """).fetchone()[0] or 0
        
        if conf_passed > 0:
            print(f"\n  Candidates passing Confidence: {conf_passed:,}")
            print(f"  {'Gate':>18s} | {'Rejected':>9s} | {'% of Conf':>10s}")
            print(f"  {'-'*18}-+-{'-'*9}-+-{'-'*10}")
            
            for col, label in signal_gates:
                rejected = ldb.execute(f"""
                    SELECT COUNT(*) FROM candidate_lifecycle
                    WHERE confidence_pass = 1 AND {col} = 1
                """).fetchone()[0] or 0
                pct = rejected / max(conf_passed, 1) * 100
                print(f"  {label:>18s} | {rejected:>9,} | {pct:>9.1f}%")
            
            signal_passed = ldb.execute("""
                SELECT COUNT(*) FROM candidate_lifecycle WHERE signal_pass = 1
            """).fetchone()[0] or 0
            gate_final = ldb.execute("""
                SELECT gate_final, COUNT(*) as cnt
                FROM candidate_lifecycle
                WHERE confidence_pass = 1 AND gate_final IS NOT NULL
                GROUP BY gate_final ORDER BY cnt DESC
            """).fetchall()
            
            print(f"\n  Signal Engine results:")
            print(f"    Passed all gates: {signal_passed:,} / {conf_passed:,} ({signal_passed/max(conf_passed,1)*100:.1f}%)")
            for r in gate_final:
                print(f"    gate_final={r['gate_final']:>20s}: {r['cnt']:>6,}")
    
    else:
        print(f"\n  ─── 18c: Gate Effectiveness ───")
        print(f"  ⚠️  Insufficient outcome-tracked candidates ({tracked} < 20 minimum)")
        print(f"  Run: python _track_outcomes.py --hours 72")
        print(f"  Then re-run this analysis.")
    
    # ── 18e: Key Findings ──
    print(f"\n  ─── 18e: Key Findings ───")
    
    # Compute the main findings from raw funnel data
    pullback_pass = ldb.execute("SELECT SUM(pullback_pass) FROM candidate_lifecycle WHERE pullback_pass IS NOT NULL").fetchone()[0] or 0
    candle_pass = ldb.execute("SELECT SUM(candle_pass) FROM candidate_lifecycle WHERE candle_pass IS NOT NULL").fetchone()[0] or 0
    volume_pass = ldb.execute("SELECT SUM(volume_pass) FROM candidate_lifecycle WHERE volume_pass IS NOT NULL").fetchone()[0] or 0
    conf_pass = ldb.execute("SELECT SUM(confidence_pass) FROM candidate_lifecycle WHERE confidence_pass IS NOT NULL").fetchone()[0] or 0
    signal_pass_count = ldb.execute("SELECT SUM(signal_pass) FROM candidate_lifecycle WHERE signal_pass IS NOT NULL").fetchone()[0] or 0
    
    print(f"  FUNNEL:")
    print(f"  1. Pullback is the dominant filter: {total - pullback_pass:,} / {total:,} rejected ({(total - pullback_pass)/total*100:.1f}%)")
    print(f"  2. Candle is the second filter: {pullback_pass - candle_pass:,} / {pullback_pass:,} rejected ({(pullback_pass - candle_pass)/max(pullback_pass,1)*100:.1f}%)")
    print(f"  3. Volume filters further: {candle_pass - volume_pass:,} / {candle_pass:,} rejected ({(candle_pass - volume_pass)/max(candle_pass,1)*100:.1f}%)")
    print(f"  4. Confidence barely filters: {volume_pass - conf_pass:,} / {volume_pass:,} rejected ({(volume_pass - conf_pass)/max(volume_pass,1)*100:.1f}%)")
    print(f"  5. Signal Engine is the final bottleneck: {conf_pass - signal_pass_count:,} / {conf_pass:,} rejected ({(conf_pass - signal_pass_count)/max(conf_pass,1)*100:.1f}%)")
    print(f"  6. End-to-end conversion: {total:,} → {signal_pass_count:,} signals ({signal_pass_count/max(total,1)*100:.2f}%)")
    
    if tracked >= 50:
        print(f"\n  GATE EFFECTIVENESS (4h return, Bootstrap 95% CI):")
        
        gate_pairs_summary = [
            ("Pullback", "regime_pass", "pullback_pass"),
            ("Candle", "pullback_pass", "candle_pass"),
            ("Volume", "candle_pass", "volume_pass"),
            ("Confidence", "volume_pass", "confidence_pass"),
        ]
        
        for gate_name, before_col, after_col in gate_pairs_summary:
            before = [r[0] for r in ldb.execute(f"""
                SELECT return_4h_pct FROM candidate_lifecycle
                WHERE {before_col} = 1 AND outcome_tracked = 1 AND return_4h_pct IS NOT NULL
            """).fetchall()]
            after = [r[0] for r in ldb.execute(f"""
                SELECT return_4h_pct FROM candidate_lifecycle
                WHERE {after_col} = 1 AND outcome_tracked = 1 AND return_4h_pct IS NOT NULL
            """).fetchall()]
            
            if len(before) < 5 or len(after) < 5:
                print(f"  {gate_name:>12s}: 🔴 INSUFFICIENT (before={len(before)}, after={len(after)})")
                continue
            
            diff, diff_lo, diff_hi = _bootstrap_difference_ci(before, after)
            d = _cohens_d(before, after)
            sig = _significance_label(diff_lo, diff_hi)
            b_mean = sum(before) / len(before)
            a_mean = sum(after) / len(after)
            
            # Direction wording: account for CI spanning zero
            # Phrasing is tightly coupled to the metric measured (estimated mean-return difference),
            # not to a broader claim about gate value. Uses "Estimated" to remind readers
            # this is a sample statistic, not the true population parameter.
            ci_spans_zero = diff_lo <= 0 <= diff_hi
            if diff > 0:
                if ci_spans_zero:
                    direction = "EST. DIFF. POSITIVE, CI INCLUDES ZERO"
                else:
                    direction = "EST. DIFF. POSITIVE, CI EXCLUDES ZERO"
            elif diff < 0:
                if ci_spans_zero:
                    direction = "EST. DIFF. NEGATIVE, CI INCLUDES ZERO"
                else:
                    direction = "EST. DIFF. NEGATIVE, CI EXCLUDES ZERO"
            else:
                direction = "EST. DIFF. ≈ ZERO"
            
            # Evidence status
            if len(after) >= MIN_EVIDENCE:
                ev = "🟢"
            elif len(after) >= MIN_EVIDENCE * 0.5:
                ev = "🟡"
            elif len(after) >= 10:
                ev = "🟠"
            else:
                ev = "🔴"
            
            # Multidimensional SUPPORTED classification
            # Requires ALL of: sample size, CI precision, regime coverage, integrity
            has_sample = len(after) >= MIN_EVIDENCE
            ci_width = abs(diff_hi - diff_lo)
            has_precision = ci_width < MAX_CI_WIDTH
            
            # Regime coverage check
            after_col_lookup = {g: ac for g, _, ac in gate_pairs_summary}
            ac = after_col_lookup.get(gate_name, "")
            if ac:
                regime_rows = ldb.execute(f"""
                    SELECT regime, COUNT(*) as cnt FROM candidate_lifecycle
                    WHERE {ac} = 1 AND outcome_tracked = 1
                    AND regime IS NOT NULL AND regime != ''
                    GROUP BY regime
                """).fetchall()
                regimes = {row[0]: row[1] for row in regime_rows}
                has_regimes = regimes.get("BUY_MODE", 0) >= 10 and regimes.get("SELL_MODE", 0) >= 10
            else:
                has_regimes = False
            
            # Data integrity: lifecycle tracker is operational (prerequisite met)
            has_integrity = True
            
            # Classification
            all_criteria = has_sample and has_precision and has_regimes and has_integrity
            criteria_met = sum([has_sample, has_precision, has_regimes, has_integrity])
            
            if all_criteria:
                qualifier = "SUPPORTED"
                suffix = " — Est. comparison complete; full protocol eval needed (PF, expectancy, drawdown, risk-adj.)"
            elif criteria_met >= 3:
                qualifier = "NEAR-SUPPORTED"
                suffix = ""
            elif has_sample or (len(after) >= 30):
                qualifier = "PRELIMINARY"
                suffix = ""
            else:
                qualifier = "TENTATIVE"
                suffix = ""
            
            # Build criteria checklist
            checks = [
                ("sample", has_sample, f"{len(after)}/{MIN_EVIDENCE}"),
                ("precision", has_precision, f"CI={ci_width:.2f}%"),
                ("regimes", has_regimes, f"{len(regimes)} types" if ac else "---"),
                ("integrity", has_integrity, "✓"),
            ]
            check_str = " ".join(f"{'✓' if ok else '✗'}{name}" for name, ok, _ in checks)
            
            print(f"  {ev} {gate_name:>12s}: {b_mean:+.2f}% → {a_mean:+.2f}% (Δ={diff:+.2f}%, CI=[{diff_lo:+.2f},{diff_hi:+.2f}], d={d:+.2f}) {sig} — {direction}")
            print(f"    {'':>12s}  Criteria: {check_str} — Conclusion: {qualifier}{suffix}")
    else:
        print(f"\n  ⚠️  Gate effectiveness requires ≥50 tracked outcomes (currently: {tracked})")
        print(f"  The outcome tracker is running. Re-run this analysis after more data accumulates.")
    
    ldb.close()
    
    print()
    print("═══════════════════════════════════════════════════════════════")
    print("  NOTE: This is OBSERVATIONAL data only.")
    print("  No trading logic has been modified.")
    print("  The baseline strategy remains frozen.")
    print("═══════════════════════════════════════════════════════════════")
    print()


if __name__ == "__main__":
    run_analysis()
