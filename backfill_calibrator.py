#!/usr/bin/env python3
"""
Backfill Confidence Calibrator with real trade outcomes from institutional_v1.db

This transforms the calibrator from a penalty system (497 pending, 0 resolved)
into a genuine filter with real historical data.
"""
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
INST_DB = BASE / "packages/ai-engine" / "data" / "institutional_v1.db"
CAL_DB = BASE / "packages/ai-engine" / "data" / "database" / "confidence_calibration.db"

def backfill():
    print("=" * 70)
    print("BACKFILL CONFIDENCE CALIBRATOR")
    print("=" * 70)

    # Check current state
    cal_db = sqlite3.connect(str(CAL_DB))
    total_before = cal_db.execute("SELECT COUNT(*) FROM calibration_log").fetchone()[0]
    pending_before = cal_db.execute("SELECT COUNT(*) FROM calibration_log WHERE outcome='pending'").fetchone()[0]
    resolved_before = total_before - pending_before
    print(f"\nBefore backfill:")
    print(f"  Total records: {total_before}")
    print(f"  Pending: {pending_before}")
    print(f"  Resolved: {resolved_before}")

    # Read all closed trades from institutional_v1.db
    inst_db = sqlite3.connect(str(INST_DB))
    inst_db.row_factory = sqlite3.Row

    trades = inst_db.execute("""
        SELECT 
            p.symbol, p.side, p.confidence * 100 as raw_confidence,
            p.institutional_score * 100 as inst_score,
            p.pnl, p.realized_r, p.regime, p.outcome,
            p.opened_at as timestamp
        FROM positions p
        WHERE p.outcome IS NOT NULL 
        AND p.status = 'closed'
        AND p.confidence IS NOT NULL 
        AND p.confidence > 0
    """).fetchall()

    print(f"\nFound {len(trades)} closed trades in institutional_v1.db")

    # Insert resolved records into calibration_log
    inserted = 0
    skipped = 0

    for trade in trades:
        symbol = trade["symbol"]
        side = trade["side"]
        raw_conf = trade["raw_confidence"]
        inst_score = trade["inst_score"]
        pnl = trade["pnl"] or 0
        r_multiple = trade["realized_r"] or 0
        regime = trade["regime"] or "unknown"
        outcome = "win" if pnl > 0 else "loss"
        ts = trade["timestamp"] or time.time()

        # Skip if raw_confidence is invalid
        if raw_conf is None or raw_conf <= 0 or raw_conf > 100:
            skipped += 1
            continue

        try:
            cal_db.execute(
                """INSERT INTO calibration_log 
                   (symbol, side, regime, raw_confidence, institutional_score, 
                    outcome, pnl, r_multiple, timestamp, calibrated_confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol, side, regime, raw_conf, inst_score, 
                 outcome, pnl, r_multiple, ts, raw_conf)
            )
            inserted += 1
        except Exception as e:
            print(f"  Error inserting {symbol}: {e}")
            skipped += 1

    cal_db.commit()

    # Check final state
    total_after = cal_db.execute("SELECT COUNT(*) FROM calibration_log").fetchone()[0]
    pending_after = cal_db.execute("SELECT COUNT(*) FROM calibration_log WHERE outcome='pending'").fetchone()[0]
    resolved_after = total_after - pending_after
    wins_after = cal_db.execute("SELECT COUNT(*) FROM calibration_log WHERE outcome='win'").fetchone()[0]

    print(f"\nAfter backfill:")
    print(f"  Total records: {total_after}")
    print(f"  Pending: {pending_after}")
    print(f"  Resolved: {resolved_after}")
    print(f"  Wins: {wins_after}")
    print(f"  Losses: {resolved_after - wins_after}")
    print(f"  Win rate: {wins_after/resolved_after*100:.1f}%" if resolved_after > 0 else "  Win rate: N/A")

    # Show bucket distribution
    print(f"\nBucket Distribution:")
    buckets = [
        (0, 50, "0-50"),
        (50, 60, "50-60"),
        (60, 70, "60-70"),
        (70, 80, "70-80"),
        (80, 90, "80-90"),
        (90, 95, "90-95"),
        (95, 101, "95-100"),
    ]

    for lo, hi, label in buckets:
        rows = cal_db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) FROM calibration_log WHERE raw_confidence >= ? AND raw_confidence < ? AND outcome != 'pending'",
            (lo, hi)
        ).fetchone()
        cnt = rows[0] or 0
        wins = rows[1] or 0
        wr = wins / cnt * 100 if cnt > 0 else 0
        bar = "█" * int(cnt / 20) if cnt > 0 else ""
        print(f"  {label:>8}: {cnt:>5} trades, WR={wr:>5.1f}% {bar}")

    # Test calibrator behavior
    print(f"\n{'─'*70}")
    print("CALIBRATOR BEHAVIOR AFTER BACKFILL")
    print(f"{'─'*70}")

    # Import and test
    import sys
    sys.path.insert(0, str(BASE / "packages" / "ai-engine"))
    from scanner.confidence_calibrator import ConfidenceCalibrator

    cal = ConfidenceCalibrator()
    # Force cache refresh
    cal._cache.clear()

    test_scores = [40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95]
    print(f"\n  {'Raw':<8} {'Calibrated':<12} {'Change':<10}")
    print(f"  {'─'*8} {'─'*12} {'─'*10}")

    for raw in test_scores:
        calibrated = cal.calibrate(raw)
        change = calibrated - raw
        marker = " ← FLOOR" if 54 <= raw <= 56 else ""
        print(f"  {raw:<8} {calibrated:<12.1f} {change:+<10.1f}{marker}")

    cal_db.close()
    inst_db.close()

    print(f"\n{'='*70}")
    print(f"BACKFILL COMPLETE: {inserted} records inserted, {skipped} skipped")
    print(f"{'='*70}")


if __name__ == "__main__":
    backfill()
