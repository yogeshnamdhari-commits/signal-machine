#!/usr/bin/env python3
"""
EMA V5 Locked Baseline Report
=============================
Establishes a locked performance baseline before any strategy changes.
Every future modification must be compared against this reference.

Metrics captured:
  - Expectancy (R)
  - Profit Factor
  - Win Rate
  - Average R (winners and losers)
  - Max Drawdown
  - Precision / Recall / F1
  - Volume, Candle, Pullback rejection rates
  - Regime-segmented metrics
  - Readiness gates (must pass before optimization)
  - Long/Short balance indicator
  - Regime coverage gates (trending + ranging)
  - Stability check (first-half vs second-half expectancy drift)

Usage:
    python _baseline_report.py
    python _baseline_report.py --hours 168
    python _baseline_report.py --lock          # Save as locked JSON baseline
"""
import sqlite3
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple


# Use the same path as CandidateLifecycleTracker
# From packages/ai-engine/_baseline_report.py: parent=ai-engine, parent=packages
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ema_v5_lifecycle.db"
LOCKED_BASELINE_PATH = Path(__file__).resolve().parent.parent / "data" / "baseline_locked.json"


# ── Readiness Gates ──────────────────────────────────────────────────────────

READINESS_GATES = {
    "min_completed_trades": 100,
    "min_long_trades": 20,
    "min_short_trades": 20,
    "min_buy_mode_trades": 20,
    "min_sell_mode_trades": 20,
    "min_winning_trades": 10,
    "min_losing_trades": 10,
    "min_days_observed": 7,
    # Regime coverage — ensure optimization isn't based on a single market type
    "min_trending_trades": 20,   # BUY_MODE + SELL_MODE combined
    "min_ranging_trades": 20,    # NO_TREND with signal_pass
    # Balance — informational, warns if Long/Short ratio is heavily skewed
    "balance_ratio_low": 0.40,   # Below this = Long-heavy warning
    "balance_ratio_high": 0.60,  # Above this = Short-heavy warning
    # Stability — first half vs second half of window
    "max_expectancy_drift_r": 0.50,  # Max allowed R drift between halves
}


def check_readiness(conn: sqlite3.Connection, cutoff: float) -> Tuple[bool, List[str]]:
    """Check if we have enough data to begin optimization.

    Returns:
        (ready, list of gate descriptions with pass/fail)
    """
    results: List[str] = []
    all_pass = True

    def _gate(label: str, actual: int, required: int) -> bool:
        nonlocal all_pass
        ok = actual >= required
        all_pass = all_pass and ok
        icon = "✅" if ok else "❌"
        results.append(f"  {icon} {label}: {actual} / {required}")
        return ok

    # 1: Completed trades
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1
    """, (cutoff,))
    _gate("Completed trades", cur.fetchone()[0] or 0, READINESS_GATES["min_completed_trades"])

    # 2: LONG trades
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1 AND direction = 'LONG'
    """, (cutoff,))
    long_count = cur.fetchone()[0] or 0
    _gate("LONG trades", long_count, READINESS_GATES["min_long_trades"])

    # 3: SHORT trades
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1 AND direction = 'SHORT'
    """, (cutoff,))
    short_count = cur.fetchone()[0] or 0
    _gate("SHORT trades", short_count, READINESS_GATES["min_short_trades"])

    # 3b: Balance check (informational)
    total_dir = long_count + short_count
    if total_dir > 0:
        long_ratio = long_count / total_dir
        balance_ok = READINESS_GATES["balance_ratio_low"] <= long_ratio <= READINESS_GATES["balance_ratio_high"]
        icon = "✅" if balance_ok else "⚠️"
        skew_note = ""
        if not balance_ok:
            if long_ratio > READINESS_GATES["balance_ratio_high"]:
                skew_note = " (Long-heavy)"
            else:
                skew_note = " (Short-heavy)"
        results.append(f"  {icon} Long/Short balance: {long_ratio:.0%} / {long_count}:{short_count}{skew_note}")
        # Informational — does not block readiness

    # 4: BUY_MODE regime
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1 AND regime = 'BUY_MODE'
    """, (cutoff,))
    _gate("BUY_MODE trades", cur.fetchone()[0] or 0, READINESS_GATES["min_buy_mode_trades"])

    # 5: SELL_MODE regime
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1 AND regime = 'SELL_MODE'
    """, (cutoff,))
    _gate("SELL_MODE trades", cur.fetchone()[0] or 0, READINESS_GATES["min_sell_mode_trades"])

    # 6: Winning trades
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1 AND return_4h_pct >= 1.0
    """, (cutoff,))
    _gate("Winning trades", cur.fetchone()[0] or 0, READINESS_GATES["min_winning_trades"])

    # 7: Losing trades
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1 AND return_4h_pct < 1.0
    """, (cutoff,))
    _gate("Losing trades", cur.fetchone()[0] or 0, READINESS_GATES["min_losing_trades"])

    # 8: Days observed
    cur = conn.execute("""
        SELECT MIN(timestamp), MAX(timestamp) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1
    """, (cutoff,))
    row = cur.fetchone()
    days = (row[1] - row[0]) / 86400 if row and row[0] and row[1] else 0
    _gate("Days observed", int(days), READINESS_GATES["min_days_observed"])

    # 9: Trending regime coverage (BUY_MODE + SELL_MODE)
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1
        AND regime IN ('BUY_MODE', 'SELL_MODE')
    """, (cutoff,))
    trending_count = cur.fetchone()[0] or 0
    _gate("Trending regime trades", trending_count, READINESS_GATES["min_trending_trades"])

    # 10: Ranging regime coverage (NO_TREND)
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1
        AND regime = 'NO_TREND'
    """, (cutoff,))
    ranging_count = cur.fetchone()[0] or 0
    _gate("Ranging regime trades", ranging_count, READINESS_GATES["min_ranging_trades"])

    # 11: Stability check — split window, compare 5 core metrics
    cur = conn.execute("""
        SELECT MIN(timestamp), MAX(timestamp) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1
    """, (cutoff,))
    ts_range = cur.fetchone()
    stability_lines: List[str] = []
    if ts_range and ts_range[0] and ts_range[1] and ts_range[1] > ts_range[0]:
        mid = (ts_range[0] + ts_range[1]) / 2

        def _compute_half_metrics(conn2: sqlite3.Connection, lo: float, hi: float) -> Dict:
            """Compute core metrics for trades within [lo, hi]."""
            cur2 = conn2.execute("""
                SELECT entry_price, atr_14, return_4h_pct FROM candidate_lifecycle
                WHERE timestamp > ? AND timestamp <= ? AND signal_pass = 1 AND outcome_tracked = 1
            """, (lo, hi))
            r_vals = []
            for ep, atr, ret in cur2.fetchall():
                if ep and atr and atr > 0:
                    risk = atr * 1.5
                    r_vals.append(((ret or 0) / 100 * ep) / risk)
            if not r_vals:
                return {}
            wins = [r for r in r_vals if r > 0]
            losses = [abs(r) for r in r_vals if r <= 0]
            total = len(r_vals)
            n_w = len(wins)
            n_l = len(losses)
            wr = n_w / max(total, 1) * 100
            avg_w = sum(wins) / n_w if wins else 0
            avg_l = sum(losses) / n_l if losses else 0
            expectancy = (wr / 100 * avg_w) - ((100 - wr) / 100 * avg_l)
            pf = sum(wins) / max(sum(losses), 0.01)
            return {
                "expectancy_r": round(expectancy, 4),
                "profit_factor": round(pf, 2),
                "win_rate": round(wr, 1),
                "avg_winner_r": round(avg_w, 3),
                "avg_loser_r": round(avg_l, 3),
            }

        first_half = _compute_half_metrics(conn, cutoff, mid)
        second_half = _compute_half_metrics(conn, mid, ts_range[1])

        if first_half and second_half:
            max_drift = READINESS_GATES["max_expectancy_drift_r"]
            metrics_to_check = [
                ("expectancy_r", "Expectancy", "R", max_drift),
                ("profit_factor", "Profit Factor", "", 0.5),
                ("win_rate", "Win Rate", "%", 10.0),
                ("avg_winner_r", "Avg Winner", "R", max_drift),
                ("avg_loser_r", "Avg Loser", "R", max_drift),
            ]
            any_unstable = False
            stability_lines.append("  Stability (1st half vs 2nd half):")
            stability_lines.append(f"  {'Metric':<20} {'1st Half':>10} {'2nd Half':>10} {'Drift':>10} {'Status':>8}")
            stability_lines.append("  " + "-" * 58)
            for key, label, unit, threshold in metrics_to_check:
                v1 = first_half[key]
                v2 = second_half[key]
                drift = abs(v1 - v2)
                stable = drift <= threshold
                icon = "✅" if stable else "⚠️"
                if not stable:
                    any_unstable = True
                stability_lines.append(
                    f"  {label:<20} {v1:>9}{unit} {v2:>9}{unit} {drift:>9}{unit} {icon:>8}"
                )
            if any_unstable:
                stability_lines.append("")
                stability_lines.append("      ⚠️  Some metrics unstable — baseline may reflect transient conditions.")
                stability_lines.append("      Consider extending the observation window before locking.")
            results.append("\n".join(stability_lines))
        # Informational — warns but does not block readiness

    return all_pass, results


# ── Metric Computation ───────────────────────────────────────────────────────

def compute_trading_metrics(conn: sqlite3.Connection, cutoff: float) -> Dict:
    """Expectancy, profit factor, win rate, avg R, max drawdown."""
    cur = conn.execute("""
        SELECT entry_price, atr_14, return_4h_pct, direction
        FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND outcome_tracked = 1
    """, (cutoff,))

    r_values: List[float] = []

    for entry_price, atr_14, return_4h, direction in cur.fetchall():
        if not entry_price or not atr_14 or atr_14 <= 0:
            continue
        risk = atr_14 * 1.5
        pnl_pct = return_4h or 0
        r = (pnl_pct / 100 * entry_price) / risk
        r_values.append(r)

    if not r_values:
        return {
            "total_trades": 0, "winners": 0, "losers": 0,
            "win_rate": 0, "avg_winner_r": 0, "avg_loser_r": 0,
            "expectancy_r": 0, "profit_factor": 0,
            "max_drawdown_r": 0, "total_r": 0,
        }

    winners_r = [r for r in r_values if r > 0]
    losers_r = [abs(r) for r in r_values if r <= 0]

    total = len(r_values)
    num_w = len(winners_r)
    num_l = len(losers_r)
    wr = num_w / max(total, 1) * 100
    avg_w = sum(winners_r) / len(winners_r) if winners_r else 0
    avg_l = sum(losers_r) / len(losers_r) if losers_r else 0
    expectancy = (wr / 100 * avg_w) - ((100 - wr) / 100 * avg_l)
    pf = sum(winners_r) / max(sum(losers_r), 0.01)

    # Max drawdown
    cum = peak = max_dd = 0.0
    for r in r_values:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    return {
        "total_trades": total, "winners": num_w, "losers": num_l,
        "win_rate": round(wr, 1),
        "avg_winner_r": round(avg_w, 3),
        "avg_loser_r": round(avg_l, 3),
        "expectancy_r": round(expectancy, 4),
        "profit_factor": round(pf, 2),
        "max_drawdown_r": round(max_dd, 3),
        "total_r": round(sum(r_values), 3),
    }


def compute_precision_recall(conn: sqlite3.Connection, cutoff: float) -> Dict:
    """Precision, recall, F1."""
    cur = conn.execute("""
        SELECT signal_pass, return_4h_pct
        FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1
    """, (cutoff,))

    tp = fp = tn = fn = 0
    for signal_pass, return_4h in cur.fetchall():
        profitable = (return_4h or 0) >= 1.0
        if signal_pass:
            if profitable:
                tp += 1
            else:
                fp += 1
        else:
            if profitable:
                fn += 1
            else:
                tn += 1

    sig = tp + fp
    opp = tp + fn
    prec = tp / max(sig, 1) * 100
    rec = tp / max(opp, 1) * 100
    f1 = 2 * prec * rec / max(prec + rec, 0.01)

    return {
        "precision_pct": round(prec, 1), "recall_pct": round(rec, 1),
        "f1_pct": round(f1, 1),
        "true_positives": tp, "false_positives": fp,
        "false_negatives": fn, "true_negatives": tn,
    }


def compute_filter_rates(conn: sqlite3.Connection, cutoff: float) -> Dict:
    """Rejection rates for Pullback, Candle, Volume."""
    cur = conn.execute("SELECT COUNT(*) FROM candidate_lifecycle WHERE timestamp > ?", (cutoff,))
    total = cur.fetchone()[0] or 1

    out: Dict[str, Dict] = {}
    for stage in ["pullback", "candle", "volume"]:
        cur = conn.execute(
            f"SELECT COUNT(*) FROM candidate_lifecycle WHERE timestamp > ? AND {stage}_pass = 0",
            (cutoff,),
        )
        rej = cur.fetchone()[0] or 0
        out[stage] = {
            "rejected": rej, "total": total,
            "rejection_rate_pct": round(rej / total * 100, 1),
        }
    return out


def compute_regime_segmentation(conn: sqlite3.Connection, cutoff: float) -> Dict:
    """Metrics broken down by regime."""
    regimes: Dict[str, Dict] = {}
    for regime in ("BUY_MODE", "SELL_MODE", "NO_TREND"):
        cur = conn.execute(
            "SELECT COUNT(*) FROM candidate_lifecycle WHERE timestamp > ? AND regime = ?",
            (cutoff, regime),
        )
        cands = cur.fetchone()[0] or 0

        cur = conn.execute(
            "SELECT COUNT(*) FROM candidate_lifecycle WHERE timestamp > ? AND regime = ? AND signal_pass = 1",
            (cutoff, regime),
        )
        sigs = cur.fetchone()[0] or 0

        cur = conn.execute(
            "SELECT COUNT(*) FROM candidate_lifecycle WHERE timestamp > ? AND regime = ? AND signal_pass = 1 AND return_4h_pct >= 1.0",
            (cutoff, regime),
        )
        wins = cur.fetchone()[0] or 0

        cur = conn.execute(
            "SELECT AVG(return_4h_pct) FROM candidate_lifecycle WHERE timestamp > ? AND regime = ? AND signal_pass = 1 AND outcome_tracked = 1",
            (cutoff, regime),
        )
        avg_ret = cur.fetchone()[0] or 0

        regimes[regime] = {
            "candidates": cands, "signals": sigs, "winners": wins,
            "precision_pct": round(wins / max(sigs, 1) * 100, 1),
            "avg_return_4h_pct": round(avg_ret, 2),
        }
    return regimes


def compute_pipeline_funnel(conn: sqlite3.Connection, cutoff: float) -> Dict:
    """Count passing candidates at each stage."""
    funnel: Dict[str, int] = {}
    cur = conn.execute("SELECT COUNT(*) FROM candidate_lifecycle WHERE timestamp > ?", (cutoff,))
    funnel["total"] = cur.fetchone()[0] or 0
    for stage in ("regime", "trend", "pullback", "candle", "volume", "confidence", "signal"):
        cur = conn.execute(
            f"SELECT COUNT(*) FROM candidate_lifecycle WHERE timestamp > ? AND {stage}_pass = 1",
            (cutoff,),
        )
        funnel[stage] = cur.fetchone()[0] or 0
    return funnel


# ── Report ───────────────────────────────────────────────────────────────────

def generate_report(hours: int = 168, lock: bool = False) -> str:
    if not DB_PATH.exists():
        return f"❌ Database not found: {DB_PATH}\n   Run the scanner with lifecycle tracking enabled first."

    conn = sqlite3.connect(str(DB_PATH))
    cutoff = datetime.now().timestamp() - (hours * 3600)

    trading = compute_trading_metrics(conn, cutoff)
    pr = compute_precision_recall(conn, cutoff)
    filters = compute_filter_rates(conn, cutoff)
    regimes = compute_regime_segmentation(conn, cutoff)
    funnel = compute_pipeline_funnel(conn, cutoff)
    ready, gates = check_readiness(conn, cutoff)
    conn.close()

    L: List[str] = []

    # Header
    L.append("=" * 80)
    L.append("  EMA V5 LOCKED BASELINE REPORT")
    L.append(f"  Window: {hours} hours ({hours / 24:.1f} days)")
    L.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"  Status: {'🟢 READY FOR OPTIMIZATION' if ready else '🔴 INSUFFICIENT DATA — keep collecting'}")
    L.append("=" * 80)
    L.append("")

    # Readiness gates
    L.append("🚦 READINESS GATES (all must pass before optimization)")
    L.append("-" * 40)
    for g in gates:
        L.append(g)
    L.append("")
    if not ready:
        L.append("  ⚠️  Do NOT begin optimization until all gates pass.\n")

    # Core metrics (locked baseline)
    L.append("📈 CORE TRADING METRICS  ← LOCKED BASELINE")
    L.append("-" * 40)
    L.append(f"  Expectancy:             {trading['expectancy_r']:+.4f}R")
    L.append(f"  Profit Factor:          {trading['profit_factor']:.2f}")
    L.append(f"  Win Rate:               {trading['win_rate']:.1f}%")
    L.append(f"  Avg Winner:             +{trading['avg_winner_r']:.3f}R")
    L.append(f"  Avg Loser:              -{trading['avg_loser_r']:.3f}R")
    L.append(f"  Max Drawdown:           -{trading['max_drawdown_r']:.3f}R")
    L.append(f"  Total R:                {trading['total_r']:+.3f}R")
    L.append(f"  Trades:                 {trading['total_trades']} ({trading['winners']}W / {trading['losers']}L)")
    L.append("")

    # Precision & recall
    L.append("🎯 PRECISION & RECALL")
    L.append("-" * 40)
    L.append(f"  Precision:              {pr['precision_pct']:.1f}%")
    L.append(f"  Recall:                 {pr['recall_pct']:.1f}%")
    L.append(f"  F1 Score:               {pr['f1_pct']:.1f}%")
    L.append(f"  True Positives:         {pr['true_positives']}")
    L.append(f"  False Positives:        {pr['false_positives']}")
    L.append(f"  False Negatives:        {pr['false_negatives']}")
    L.append("")

    # Filter rejection rates
    L.append("🔧 FILTER REJECTION RATES  ← LOCKED BASELINE")
    L.append("-" * 40)
    for name, d in filters.items():
        L.append(f"  {name:<12} rejection: {d['rejection_rate_pct']:>5.1f}%  ({d['rejected']}/{d['total']})")
    L.append("")

    # Regime segmentation
    L.append("🌍 REGIME SEGMENTATION")
    L.append("-" * 40)
    L.append(f"  {'Regime':<12} {'Cands':>7} {'Sigs':>6} {'Wins':>6} {'Prec':>8} {'AvgRet':>8}")
    L.append("  " + "-" * 47)
    for regime, d in regimes.items():
        L.append(
            f"  {regime:<12} {d['candidates']:>7} {d['signals']:>6} "
            f"{d['winners']:>6} {d['precision_pct']:>7.1f}% {d['avg_return_4h_pct']:>7.2f}%"
        )
    L.append("")

    # Pipeline funnel
    L.append("📊 PIPELINE FUNNEL")
    L.append("-" * 40)
    stages = ["total", "regime", "trend", "pullback", "candle", "volume", "confidence", "signal"]
    for i, stage in enumerate(stages):
        count = funnel.get(stage, 0)
        if i > 0:
            prev = funnel.get(stages[i - 1], 1)
            pct = count / max(prev, 1) * 100
            note = f" ({pct:.0f}% pass)" if stage != "total" else ""
        else:
            note = ""
        L.append(f"  {stage:<14} {count:>8}{note}")
    L.append("")

    # Comparison template
    L.append("=" * 80)
    L.append("📋 COMPARISON TEMPLATE — Fill after each strategy change")
    L.append("=" * 80)
    L.append("")
    L.append(f"  {'Metric':<26} {'Baseline':>12} {'After':>12} {'Delta':>12} {'Verdict':>10}")
    L.append("  " + "-" * 72)
    L.append(f"  {'Expectancy (R)':<26} {trading['expectancy_r']:>+11.4f} {'?':>12} {'?':>12} {'?':>10}")
    L.append(f"  {'Profit Factor':<26} {trading['profit_factor']:>11.2f} {'?':>12} {'?':>12} {'?':>10}")
    L.append(f"  {'Win Rate':<26} {trading['win_rate']:>10.1f}% {'?':>12} {'?':>12} {'?':>10}")
    L.append(f"  {'Avg Winner (R)':<26} {trading['avg_winner_r']:>+11.3f} {'?':>12} {'?':>12} {'?':>10}")
    L.append(f"  {'Avg Loser (R)':<26} {trading['avg_loser_r']:>11.3f} {'?':>12} {'?':>12} {'?':>10}")
    L.append(f"  {'Max Drawdown (R)':<26} {trading['max_drawdown_r']:>11.3f} {'?':>12} {'?':>12} {'?':>10}")
    L.append(f"  {'Precision':<26} {pr['precision_pct']:>10.1f}% {'?':>12} {'?':>12} {'?':>10}")
    L.append(f"  {'Recall':<26} {pr['recall_pct']:>10.1f}% {'?':>12} {'?':>12} {'?':>10}")
    for name, d in filters.items():
        L.append(f"  {f'{name} rejection':<26} {d['rejection_rate_pct']:>10.1f}% {'?':>12} {'?':>12} {'?':>10}")
    L.append("")
    L.append("  Verdict:  ✅ Improved  |  ⚠️ Degraded  |  ➡️ Unchanged")
    L.append("  Rule:     Only keep changes that improve expectancy AND preserve robustness across regimes.")
    L.append("")

    # Evaluation protocol
    L.append("=" * 80)
    L.append("📐 STRATEGY CHANGE EVALUATION PROTOCOL")
    L.append("=" * 80)
    L.append("")
    L.append("  Every strategy change MUST follow this sequence:")
    L.append("")
    L.append("  1. Generate new report       python _baseline_report.py --hours 168")
    L.append("  2. Compare global metrics    Fill comparison template above")
    L.append("  3. Compare by regime         Check BUY_MODE / SELL_MODE / NO_TREND separately")
    L.append("  4. Compare by direction      Check LONG vs SHORT separately")
    L.append("  5. Review stability          Verify 5-metric stability table still passes")
    L.append("  6. Accept or reject          Only if:")
    L.append("       • Expectancy improves")
    L.append("       • Profit factor improves or stays stable")
    L.append("       • No regime degrades beyond acceptable threshold")
    L.append("       • No direction degrades beyond acceptable threshold")
    L.append("       • Stability indicators remain within tolerance")
    L.append("")
    L.append("  Change ONE filter at a time. Never batch changes.")
    L.append("  Lock a new baseline after each accepted change.")
    L.append("")

    # Lock to JSON
    if lock:
        data = {
            "generated_at": datetime.now().isoformat(),
            "window_hours": hours,
            "trading_metrics": trading,
            "precision_recall": pr,
            "filter_rejection_rates": filters,
            "regime_segmentation": regimes,
            "pipeline_funnel": funnel,
            "readiness_gates_met": ready,
        }
        LOCKED_BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOCKED_BASELINE_PATH, "w") as f:
            json.dump(data, f, indent=2)
        L.append(f"🔒 BASELINE LOCKED → {LOCKED_BASELINE_PATH}")
        L.append("   Future comparison scripts will read this file automatically.\n")

    L.append("=" * 80)
    L.append("END OF BASELINE REPORT")
    L.append("=" * 80)
    return "\n".join(L)


def main():
    hours = 168
    lock = False

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--lock":
            lock = True
        elif arg == "--hours" and i + 1 < len(args):
            try:
                hours = int(args[i + 1])
            except ValueError:
                pass

    report = generate_report(hours=hours, lock=lock)
    print(report)

    report_file = Path(f"data/logs/baseline_report_{hours}h.txt")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with open(report_file, "w") as f:
        f.write(report)
    print(f"\n📄 Report saved to: {report_file}")


if __name__ == "__main__":
    main()
