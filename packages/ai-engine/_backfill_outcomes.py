#!/usr/bin/env python3
"""
Backfill outcomes for expired trades that remain outcome='pending'.

The lifecycle bug: when positions closed, engine.py only updated status='expired'
but never set outcome. This script backfills by:
1. Finding all expired+pending trades
2. Computing P&L from entry/SL/TP and price data
3. Classifying as win/loss based on realized R
4. Updating the database

Usage:
    python _backfill_outcomes.py              # Dry run (shows what would change)
    python _backfill_outcomes.py --apply      # Actually updates the database
"""
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

DB_PATH = Path("packages/ai-engine/data/institutional_v1.db")

def classify_outcome(
    side: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    realized_r: float,
) -> str:
    """Classify a trade outcome based on realized R multiple."""
    if realized_r > 0:
        return "tp_hit"
    elif realized_r < 0:
        return "sl_hit"
    else:
        return "timeout"


def backfill_outcomes(dry_run: bool = True) -> Dict:
    """Backfill outcomes for expired+pending trades."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    # Find all expired trades with pending outcome
    cur = conn.execute("""
        SELECT id, symbol, side, entry, stop_loss, take_profit, risk_reward,
               realized_r, mae_pct, mfe_pct, outcome, status, confidence,
               market_regime, metadata
        FROM signals
        WHERE status = 'expired' AND outcome = 'pending'
        ORDER BY timestamp ASC
    """)
    trades = cur.fetchall()
    cols = [d[0] for d in cur.description]

    results = {
        "total_pending": len(trades),
        "updated": 0,
        "skipped_no_entry": 0,
        "skipped_no_sl": 0,
        "wins": 0,
        "losses": 0,
        "timeout": 0,
        "changes": [],
    }

    for row in trades:
        d = dict(zip(cols, row))
        sig_id = d["id"]
        symbol = d["symbol"]
        side = d["side"]
        entry = d["entry"]
        stop_loss = d["stop_loss"]
        take_profit = d["take_profit"]
        risk_reward = d["risk_reward"]
        realized_r = d["realized_r"]
        current_outcome = d["outcome"]

        # Skip if we can't compute outcome
        if not entry or entry <= 0:
            results["skipped_no_entry"] += 1
            continue
        if not stop_loss or stop_loss <= 0:
            results["skipped_no_sl"] += 1
            continue

        # If realized_r is already set, just classify
        if realized_r and realized_r != 0:
            outcome = classify_outcome(side, entry, stop_loss, take_profit, realized_r)
        else:
            # We don't have exit price data for these historical trades.
            # Mark as "expired_no_data" rather than forcing a false win/loss.
            outcome = "expired_no_data"
            realized_r = 0.0

        # Update the database
        if not dry_run:
            conn.execute("""
                UPDATE signals
                SET outcome = ?, realized_r = ?
                WHERE id = ? AND outcome = 'pending'
            """, (outcome, realized_r, sig_id))

        results["updated"] += 1
        if outcome == "tp_hit":
            results["wins"] += 1
        elif outcome == "sl_hit":
            results["losses"] += 1
        else:
            results["timeout"] += 1

        results["changes"].append({
            "id": sig_id,
            "symbol": symbol,
            "side": side,
            "outcome": outcome,
            "realized_r": realized_r,
        })

    if not dry_run:
        conn.commit()

    conn.close()
    return results


def main():
    dry_run = "--apply" not in sys.argv

    print("=" * 60)
    print("  EMA V5 OUTCOME BACKFILL")
    print("=" * 60)
    print()

    if dry_run:
        print("  Mode: DRY RUN (no database changes)")
        print("  Add --apply to actually update the database")
    else:
        print("  Mode: APPLY (will update database)")

    print()

    results = backfill_outcomes(dry_run=dry_run)

    print(f"  Total pending trades:  {results['total_pending']}")
    print(f"  Updated:               {results['updated']}")
    print(f"  Skipped (no entry):    {results['skipped_no_entry']}")
    print(f"  Skipped (no SL):       {results['skipped_no_sl']}")
    print()
    print(f"  Wins (tp_hit):         {results['wins']}")
    print(f"  Losses (sl_hit):       {results['losses']}")
    print(f"  Timeouts:              {results['timeout']}")
    print()

    if results['updated'] > 0:
        total = results['wins'] + results['losses'] + results['timeout']
        if total > 0:
            wr = results['wins'] / total * 100
            print(f"  Win Rate:              {wr:.1f}%")

    if dry_run and results['updated'] > 0:
        print()
        print("  First 10 changes:")
        for c in results["changes"][:10]:
            print(f"    {c['symbol']:15} {c['side']:6} → {c['outcome']:10} R={c['realized_r']:+.2f}")

    print()
    print("=" * 60)
    if dry_run:
        print("  Run with --apply to execute changes")
    else:
        print("  Backfill complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
