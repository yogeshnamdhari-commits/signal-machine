#!/usr/bin/env python3
"""
Candle Parameter Sweep
=======================
Sweeps wick_ratio and body_ratio thresholds to find optimal settings.
For each combination, counts how many candidates pass and checks outcomes.

READ-ONLY — Never modifies trading logic.
"""
import sys
import re
import sqlite3
from pathlib import Path
from collections import defaultdict

AI_ROOT = Path(__file__).resolve().parent / "packages" / "ai-engine"
sys.path.insert(0, str(AI_ROOT))

DB_PATH = AI_ROOT / "data" / "institutional_v1.db"
LOG_DIR = AI_ROOT / "data" / "logs"


def parse_candle_rejections(date_str: str = "2026-07-09"):
    """Parse lifecycle log to extract candle rejections with body/wick values."""
    log_path = LOG_DIR / f"ema_v5_lifecycle_{date_str}.log"
    if not log_path.exists():
        return []

    rejections = []
    pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}) \| (\w+)\s+\| (\w+)\s+→ (\w+)\s+\| conf=\s*([\d.]+) side=\s*(\w*)\s*\| candle_rejected: body=([\d.]+) wick=([\d.]+)"
    )

    with open(log_path) as f:
        for line in f:
            m = pattern.search(line)
            if m:
                time_str, symbol, from_state, to_state, conf, side, body, wick = m.groups()
                rejections.append({
                    "time": time_str,
                    "symbol": symbol,
                    "confidence": float(conf) if conf else 0,
                    "side": side.strip(),
                    "body": float(body),
                    "wick": float(wick),
                })

    return rejections


def get_trade_outcomes(symbol: str):
    """Get actual trade outcomes for a symbol."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT pnl, exit_reason, side, entry_price, stop_loss, take_profit
            FROM positions WHERE symbol = ? AND status = 'closed'
            UNION ALL
            SELECT pnl, exit_reason, side, entry_price, stop_loss, take_profit
            FROM positions_archive WHERE symbol = ? AND status = 'closed'
        """, (symbol, symbol)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def sweep_wick_ratio(rejections, wick_thresholds):
    """Sweep wick ratio thresholds and count passes."""
    results = []
    
    for threshold in wick_thresholds:
        passed = [r for r in rejections if r["wick"] >= threshold]
        failed = [r for r in rejections if r["wick"] < threshold]
        
        # Check outcomes for passed symbols
        passed_symbols = set(r["symbol"] for r in passed)
        outcomes = []
        for symbol in list(passed_symbols)[:100]:
            trades = get_trade_outcomes(symbol)
            outcomes.extend(trades)
        
        if outcomes:
            wins = sum(1 for t in outcomes if (t.get("pnl", 0) or 0) > 0)
            total_pnl = sum(t.get("pnl", 0) or 0 for t in outcomes)
            gp = sum(t.get("pnl", 0) or 0 for t in outcomes if (t.get("pnl", 0) or 0) > 0)
            gl = sum(abs(t.get("pnl", 0) or 0) for t in outcomes if (t.get("pnl", 0) or 0) < 0)
            pf = gp / gl if gl > 0 else 0
            wr = wins / len(outcomes) * 100 if outcomes else 0
            exp = total_pnl / len(outcomes) if outcomes else 0
        else:
            wins = 0; total_pnl = 0; pf = 0; wr = 0; exp = 0
        
        results.append({
            "threshold": threshold,
            "passed": len(passed),
            "failed": len(failed),
            "pass_rate": len(passed) / len(rejections) * 100 if rejections else 0,
            "unique_symbols": len(passed_symbols),
            "trades_checked": len(outcomes),
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2),
            "expectancy": round(exp, 2),
            "total_pnl": round(total_pnl, 2),
        })
    
    return results


def sweep_body_ratio(rejections, body_thresholds):
    """Sweep body ratio thresholds and count passes."""
    results = []
    
    for threshold in body_thresholds:
        passed = [r for r in rejections if r["body"] >= threshold]
        failed = [r for r in rejections if r["body"] < threshold]
        
        passed_symbols = set(r["symbol"] for r in passed)
        outcomes = []
        for symbol in list(passed_symbols)[:100]:
            trades = get_trade_outcomes(symbol)
            outcomes.extend(trades)
        
        if outcomes:
            wins = sum(1 for t in outcomes if (t.get("pnl", 0) or 0) > 0)
            total_pnl = sum(t.get("pnl", 0) or 0 for t in outcomes)
            gp = sum(t.get("pnl", 0) or 0 for t in outcomes if (t.get("pnl", 0) or 0) > 0)
            gl = sum(abs(t.get("pnl", 0) or 0) for t in outcomes if (t.get("pnl", 0) or 0) < 0)
            pf = gp / gl if gl > 0 else 0
            wr = wins / len(outcomes) * 100 if outcomes else 0
            exp = total_pnl / len(outcomes) if outcomes else 0
        else:
            wins = 0; total_pnl = 0; pf = 0; wr = 0; exp = 0
        
        results.append({
            "threshold": threshold,
            "passed": len(passed),
            "failed": len(failed),
            "pass_rate": len(passed) / len(rejections) * 100 if rejections else 0,
            "unique_symbols": len(passed_symbols),
            "trades_checked": len(outcomes),
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2),
            "expectancy": round(exp, 2),
            "total_pnl": round(total_pnl, 2),
        })
    
    return results


def main():
    print("=" * 90)
    print("🔬 CANDLE PARAMETER SWEEP")
    print("=" * 90)

    rejections = parse_candle_rejections("2026-07-09")
    print(f"\n📊 Total candle rejections: {len(rejections)}")

    if not rejections:
        print("No rejections found.")
        return

    # ── Wick Ratio Sweep ──
    print(f"\n{'='*90}")
    print(f"📈 WICK RATIO SWEEP (current: 2.0)")
    print(f"{'='*90}")

    wick_thresholds = [1.0, 1.2, 1.4, 1.5, 1.6, 1.8, 2.0, 2.2, 2.5]
    wick_results = sweep_wick_ratio(rejections, wick_thresholds)

    print(f"\n{'Wick':>6} {'Passed':>8} {'Pass%':>7} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Exp':>8} {'PnL':>10}")
    print("-" * 65)
    for r in wick_results:
        marker = " ← current" if r["threshold"] == 2.0 else ""
        emoji = "🟢" if r["profit_factor"] > 1.2 else "🟡" if r["profit_factor"] > 0.8 else "🔴"
        print(f"{emoji} {r['threshold']:>4.1f} {r['passed']:>8} {r['pass_rate']:>6.1f}% {r['trades_checked']:>7} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} {r['expectancy']:>7.2f} {r['total_pnl']:>10.2f}{marker}")

    # ── Body Ratio Sweep ──
    print(f"\n{'='*90}")
    print(f"📈 BODY RATIO SWEEP (current: 0.5)")
    print(f"{'='*90}")

    body_thresholds = [0.2, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7]
    body_results = sweep_body_ratio(rejections, body_thresholds)

    print(f"\n{'Body':>6} {'Passed':>8} {'Pass%':>7} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Exp':>8} {'PnL':>10}")
    print("-" * 65)
    for r in body_results:
        marker = " ← current" if r["threshold"] == 0.5 else ""
        emoji = "🟢" if r["profit_factor"] > 1.2 else "🟡" if r["profit_factor"] > 0.8 else "🔴"
        print(f"{emoji} {r['threshold']:>4.2f} {r['passed']:>8} {r['pass_rate']:>6.1f}% {r['trades_checked']:>7} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} {r['expectancy']:>7.2f} {r['total_pnl']:>10.2f}{marker}")

    # ── Recommendation ──
    print(f"\n{'='*90}")
    print(f"💡 RECOMMENDATION")
    print(f"{'='*90}")

    # Find best wick threshold
    best_wick = max(wick_results, key=lambda r: r["profit_factor"] if r["trades_checked"] >= 10 else 0)
    best_body = max(body_results, key=lambda r: r["profit_factor"] if r["trades_checked"] >= 10 else 0)

    print(f"\n  Best wick ratio: {best_wick['threshold']} (PF={best_wick['profit_factor']}, WR={best_wick['win_rate']}%)")
    print(f"  Best body ratio: {best_body['threshold']} (PF={best_body['profit_factor']}, WR={best_body['win_rate']}%)")

    if best_wick["profit_factor"] > 1.2 and best_wick["threshold"] != 2.0:
        print(f"\n  ✅ Wick ratio {best_wick['threshold']} shows PF > 1.2")
        print(f"     Consider lowering from 2.0 → {best_wick['threshold']}")
    else:
        print(f"\n  ⚠️ No wick threshold shows clear improvement over current (2.0)")

    if best_body["profit_factor"] > 1.2 and best_body["threshold"] != 0.5:
        print(f"\n  ✅ Body ratio {best_body['threshold']} shows PF > 1.2")
        print(f"     Consider lowering from 0.5 → {best_body['threshold']}")
    else:
        print(f"\n  ⚠️ No body threshold shows clear improvement over current (0.5)")

    print(f"\n  ⚠️  CAVEAT: These are counterfactual results on a small sample.")
    print(f"     Do NOT change production without larger validation.")


if __name__ == "__main__":
    main()
