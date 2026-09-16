"""
EMA V5 Maturity → Performance Analyzer

Generates the validation table:
  Maturity Class → Trades | Win Rate | PF | Avg R | PnL

Split by side (BUY/SELL) to detect differential effects.

Usage:
  python analyze_maturity_performance.py
  python analyze_maturity_performance.py --side LONG
  python analyze_maturity_performance.py --side SHORT
  python analyze_maturity_performance.py --since 2026-07-20
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent / "data" / "institutional_v1.db"

# Frozen baseline for comparison
BASELINE = {
    "trades": 36,
    "win_rate": 36.1,
    "profit_factor": 0.54,
    "expectancy_r": -0.96,
    "total_pnl": -34.58,
}


def get_trades(since: Optional[str] = None, side: Optional[str] = None) -> List[Dict]:
    """Fetch completed EMA V5 trades from positions_archive."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    query = """
        SELECT symbol, side, pnl, confidence, maturity_score, maturity_class,
               exit_reason, hold_minutes, mfe_pct, mae_pct, realized_r,
               opened_at, closed_at, entry_price, stop_loss
        FROM positions_archive
        WHERE strategy_version = 'ema_v5'
          AND status = 'closed'
          AND maturity_score IS NOT NULL
          AND maturity_score != 50
    """
    params = []

    if since:
        # Convert date string to timestamp
        dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        ts = dt.timestamp()
        query += " AND opened_at >= ?"
        params.append(ts)

    if side:
        query += " AND side = ?"
        params.append(side.upper())

    query += " ORDER BY opened_at ASC"

    cur.execute(query, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def classify_maturity(score: float) -> str:
    """Classify maturity score into buckets."""
    if score >= 80:
        return "FRESH"
    elif score >= 60:
        return "HEALTHY"
    elif score >= 40:
        return "MATURE"
    else:
        return "EXHAUSTED"


def compute_metrics(trades: List[Dict]) -> Dict:
    """Compute performance metrics for a list of trades."""
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "avg_r": 0,
            "total_pnl": 0,
            "avg_mfe": 0,
            "avg_mae": 0,
            "avg_hold": 0,
            "avg_confidence": 0,
            "avg_maturity": 0,
        }

    wins = [t for t in trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("pnl") or 0) <= 0]

    gross_profit = sum(t.get("pnl", 0) or 0 for t in wins)
    gross_loss = abs(sum(t.get("pnl", 0) or 0 for t in losses))

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0,
        "avg_r": sum(t.get("realized_r", 0) or 0 for t in trades) / len(trades),
        "total_pnl": sum(t.get("pnl", 0) or 0 for t in trades),
        "avg_mfe": sum(t.get("mfe_pct", 0) or 0 for t in trades) / len(trades),
        "avg_mae": sum(t.get("mae_pct", 0) or 0 for t in trades) / len(trades),
        "avg_hold": sum(t.get("hold_minutes", 0) or 0 for t in trades) / len(trades),
        "avg_confidence": sum(t.get("confidence", 0) or 0 for t in trades) / len(trades),
        "avg_maturity": sum(t.get("maturity_score", 0) or 0 for t in trades) / len(trades),
    }


def print_table(title: str, rows: List[Dict], show_baseline: bool = True) -> None:
    """Print a formatted maturity performance table."""
    print(f"\n{'=' * 90}")
    print(f"  {title}")
    print(f"{'=' * 90}")

    header = f"{'Class':12s} {'Trades':>6s} {'Wins':>5s} {'Loss':>5s} {'WR%':>6s} {'PF':>6s} {'Avg R':>7s} {'PnL':>10s} {'Avg MFE':>8s} {'Avg MAE':>8s} {'Avg Hold':>8s}"
    print(header)
    print("-" * 90)

    for row in rows:
        m = row["metrics"]
        print(
            f"{row['class']:12s} "
            f"{m['trades']:>6d} "
            f"{m['wins']:>5d} "
            f"{m['losses']:>5d} "
            f"{m['win_rate']:>5.1f}% "
            f"{m['profit_factor']:>6.2f} "
            f"{m['avg_r']:>+6.2f}R "
            f"${m['total_pnl']:>+9.2f} "
            f"{m['avg_mfe']:>7.2f}% "
            f"{m['avg_mae']:>7.2f}% "
            f"{m['avg_hold']:>7.0f}m"
        )

    if show_baseline:
        print("-" * 90)
        print(
            f"{'BASELINE':12s} "
            f"{BASELINE['trades']:>6d} "
            f"{'':>5s} "
            f"{'':>5s} "
            f"{BASELINE['win_rate']:>5.1f}% "
            f"{BASELINE['profit_factor']:>6.2f} "
            f"{BASELINE['expectancy_r']:>+6.2f}R "
            f"${BASELINE['total_pnl']:>+9.2f} "
            f"{'':>8s} "
            f"{'':>8s} "
            f"{'':>8s}"
        )

    print(f"{'=' * 90}")


def analyze(since: Optional[str] = None, side: Optional[str] = None) -> None:
    """Run the full maturity performance analysis."""
    trades = get_trades(since=since, side=side)

    if not trades:
        print("\n⚠️  No completed trades with maturity data found.")
        print("    The maturity filter was added on 2026-07-20.")
        print("    Trades completed before that date have default maturity=50 (excluded).")
        print("    Wait for new trades to complete, then run this analysis again.")
        return

    # ── Overall summary ──
    overall = compute_metrics(trades)
    print(f"\n📊 EMA V5 MATURITY PERFORMANCE ANALYSIS")
    print(f"   Trades analyzed: {len(trades)}")
    print(f"   Date range: {datetime.fromtimestamp(trades[0]['opened_at']).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(trades[-1]['opened_at']).strftime('%Y-%m-%d')}")

    # ── Group by maturity class ──
    classes = {"FRESH": [], "HEALTHY": [], "MATURE": [], "EXHAUSTED": []}
    for t in trades:
        score = t.get("maturity_score", 50)
        cls = classify_maturity(score)
        classes[cls].append(t)

    # ── Build table rows ──
    rows = []
    for cls in ["FRESH", "HEALTHY", "MATURE", "EXHAUSTED"]:
        if classes[cls]:
            rows.append({
                "class": cls,
                "metrics": compute_metrics(classes[cls]),
            })

    # ── Print table ──
    title = "Maturity Class → Performance"
    if side:
        title += f" ({side.upper()} only)"
    if since:
        title += f" (since {since})"

    print_table(title, rows)

    # ── Side-by-side comparison if not filtered ──
    if not side:
        print("\n" + "=" * 90)
        print("  BUY vs SELL Breakdown")
        print("=" * 90)

        for s in ["LONG", "SHORT"]:
            side_trades = [t for t in trades if t.get("side") == s]
            if not side_trades:
                print(f"\n  {s}: No trades")
                continue

            side_classes = {"FRESH": [], "HEALTHY": [], "MATURE": [], "EXHAUSTED": []}
            for t in side_trades:
                score = t.get("maturity_score", 50)
                cls = classify_maturity(score)
                side_classes[cls].append(t)

            side_rows = []
            for cls in ["FRESH", "HEALTHY", "MATURE", "EXHAUSTED"]:
                if side_classes[cls]:
                    side_rows.append({
                        "class": cls,
                        "metrics": compute_metrics(side_classes[cls]),
                    })

            print_table(f"{s} Trades by Maturity", side_rows, show_baseline=True)

    # ── Signal-level detail for recent trades ──
    print(f"\n📋 Recent Trades (last 10):")
    print(f"{'Symbol':15s} {'Side':6s} {'Maturity':>8s} {'Class':10s} {'Conf':>6s} {'PnL':>10s} {'R':>7s} {'Exit':15s}")
    print("-" * 90)
    for t in trades[-10:]:
        pnl = t.get("pnl", 0) or 0
        r = t.get("realized_r", 0) or 0
        print(
            f"{t['symbol']:15s} "
            f"{t.get('side', '?'):6s} "
            f"{t.get('maturity_score', 0):>7.1f} "
            f"{classify_maturity(t.get('maturity_score', 50)):10s} "
            f"{t.get('confidence', 0):>5.2f} "
            f"${pnl:>+9.2f} "
            f"{r:>+6.2f}R "
            f"{t.get('exit_reason', '?'):15s}"
        )

    # ── Exit type breakdown by maturity class ──
    print(f"\n{'=' * 90}")
    print("  Exit Type Breakdown by Maturity Class")
    print(f"{'=' * 90}")
    print(f"{'Class':12s} {'TP1':>6s} {'TP2':>6s} {'TP3':>6s} {'SL':>6s} {'Trail':>6s} {'Timeout':>8s} {'Other':>6s}")
    print("-" * 90)

    for cls in ["FRESH", "HEALTHY", "MATURE", "EXHAUSTED"]:
        if not classes[cls]:
            continue

        exit_types = {"tp1": 0, "tp2": 0, "tp3": 0, "sl": 0, "trailing": 0, "timeout": 0, "other": 0}
        for t in classes[cls]:
            exit_reason = (t.get("exit_reason") or "").lower()
            if "tp1" in exit_reason or "take_profit_1" in exit_reason:
                exit_types["tp1"] += 1
            elif "tp2" in exit_reason or "take_profit_2" in exit_reason:
                exit_types["tp2"] += 1
            elif "tp3" in exit_reason or "take_profit_3" in exit_reason:
                exit_types["tp3"] += 1
            elif "sl" in exit_reason or "stop_loss" in exit_reason or "stop" in exit_reason:
                exit_types["sl"] += 1
            elif "trail" in exit_reason:
                exit_types["trailing"] += 1
            elif "timeout" in exit_reason or "time" in exit_reason:
                exit_types["timeout"] += 1
            else:
                exit_types["other"] += 1

        total = sum(exit_types.values())
        print(
            f"{cls:12s} "
            f"{exit_types['tp1']:>5d} "
            f"{exit_types['tp2']:>5d} "
            f"{exit_types['tp3']:>5d} "
            f"{exit_types['sl']:>5d} "
            f"{exit_types['trailing']:>5d} "
            f"{exit_types['timeout']:>7d} "
            f"{exit_types['other']:>5d}"
        )

    print(f"{'=' * 90}")

    # ── Combined maturity matrix: TP1/TP2/SL/Trail + MFE/MAE/PF ──
    print(f"\n{'=' * 100}")
    print("  Combined Maturity Matrix")
    print(f"{'=' * 100}")
    print(f"{'Class':12s} {'Trades':>6s} {'TP1':>5s} {'TP2':>5s} {'SL':>5s} {'Trail':>6s} {'Avg MFE':>8s} {'Avg MAE':>8s} {'PF':>6s} {'WR%':>6s} {'Avg R':>7s}")
    print("-" * 100)

    for cls in ["FRESH", "HEALTHY", "MATURE", "EXHAUSTED"]:
        if not classes[cls]:
            continue

        m = compute_metrics(classes[cls])
        exit_counts = {"tp1": 0, "tp2": 0, "sl": 0, "trailing": 0}
        for t in classes[cls]:
            exit_reason = (t.get("exit_reason") or "").lower()
            if "tp1" in exit_reason or "take_profit_1" in exit_reason:
                exit_counts["tp1"] += 1
            elif "tp2" in exit_reason or "take_profit_2" in exit_reason:
                exit_counts["tp2"] += 1
            elif "trail" in exit_reason:
                exit_counts["trailing"] += 1
            elif "sl" in exit_reason or "stop" in exit_reason:
                exit_counts["sl"] += 1

        print(
            f"{cls:12s} "
            f"{m['trades']:>6d} "
            f"{exit_counts['tp1']:>5d} "
            f"{exit_counts['tp2']:>5d} "
            f"{exit_counts['sl']:>5d} "
            f"{exit_counts['trailing']:>5d} "
            f"{m['avg_mfe']:>7.2f}% "
            f"{m['avg_mae']:>7.2f}% "
            f"{m['profit_factor']:>6.2f} "
            f"{m['win_rate']:>5.1f}% "
            f"{m['avg_r']:>+6.2f}R"
        )

    print(f"{'=' * 100}")

    # ── BUY vs SELL PF by maturity class ──
    print(f"\n{'=' * 70}")
    print("  BUY vs SELL Profit Factor by Maturity Class")
    print(f"{'=' * 70}")
    print(f"{'Class':12s} {'BUY Trades':>10s} {'BUY PF':>8s} {'BUY WR%':>8s} {'SELL Trades':>11s} {'SELL PF':>8s} {'SELL WR%':>8s}")
    print("-" * 70)

    for cls in ["FRESH", "HEALTHY", "MATURE", "EXHAUSTED"]:
        if not classes[cls]:
            continue

        buy_trades = [t for t in classes[cls] if t.get("side") == "LONG"]
        sell_trades = [t for t in classes[cls] if t.get("side") == "SHORT"]

        buy_m = compute_metrics(buy_trades)
        sell_m = compute_metrics(sell_trades)

        buy_pf_str = f"{buy_m['profit_factor']:.2f}" if buy_trades else "—"
        sell_pf_str = f"{sell_m['profit_factor']:.2f}" if sell_trades else "—"
        buy_wr_str = f"{buy_m['win_rate']:.1f}%" if buy_trades else "—"
        sell_wr_str = f"{sell_m['win_rate']:.1f}%" if sell_trades else "—"

        print(
            f"{cls:12s} "
            f"{len(buy_trades):>10d} "
            f"{buy_pf_str:>8s} "
            f"{buy_wr_str:>8s} "
            f"{len(sell_trades):>11d} "
            f"{sell_pf_str:>8s} "
            f"{sell_wr_str:>8s}"
        )

    # Add baseline comparison
    print("-" * 70)
    print(
        f"{'BASELINE':12s} "
        f"{'8':>10s} "
        f"{'0.26':>8s} "
        f"{'25.0%':>8s} "
        f"{'28':>11s} "
        f"{'0.74':>8s} "
        f"{'39.3%':>8s}"
    )
    print(f"{'=' * 70}")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="EMA V5 Maturity Performance Analyzer")
    parser.add_argument("--side", choices=["LONG", "SHORT"], help="Filter by side")
    parser.add_argument("--since", type=str, help="Filter trades opened after this date (YYYY-MM-DD)")
    args = parser.parse_args()

    analyze(since=args.since, side=args.side)


if __name__ == "__main__":
    main()
