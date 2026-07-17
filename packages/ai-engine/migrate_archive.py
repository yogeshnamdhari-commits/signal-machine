#!/usr/bin/env python3
"""
One-time migration: Move closed positions from `positions` to `positions_archive`
and deduplicate. Run once to fix existing data.
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "institutional_v1.db"

def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 1. Count before
    cur.execute("SELECT COUNT(*) FROM positions WHERE status='closed'")
    closed = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM positions WHERE status='open'")
    open_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM positions_archive")
    arch = cur.fetchone()[0]
    print(f"BEFORE: positions(closed={closed}, open={open_count}), archive={arch}")

    # 2. Move closed positions to archive
    cur.execute("SELECT * FROM positions WHERE status='closed'")
    rows = cur.fetchall()
    cols = [d[1] for d in cur.execute("PRAGMA table_info(positions)").fetchall()]

    moved = 0
    for row in rows:
        r = dict(zip([c[1] for c in cur.execute("PRAGMA table_info(positions)").fetchall()], row))
        # Skip if already in archive (same signal_id)
        sig_id = r.get("signal_id", "")
        if sig_id:
            cur.execute("SELECT COUNT(*) FROM positions_archive WHERE signal_id=?", (sig_id,))
            if cur.fetchone()[0] > 0:
                continue

        try:
            cur.execute(
                """INSERT INTO positions_archive
                   (signal_id, symbol, side, entry_price, quantity, leverage,
                    stop_loss, take_profit, pnl, fees, status, opened_at, closed_at,
                    take_profit_2, take_profit_3, current_tp_index, exit_reason,
                    strategy_version, confidence, regime, institutional_score,
                    risk_reward, hold_minutes, session, mfe_pct, mae_pct,
                    alpha_score, alpha_tier, mss_score, fvg_score, entry_reason,
                    outcome, realized_r, planned_rr, at_open_regime, at_open_session,
                    volatility_score, quiet_market_blocked)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.get("signal_id", ""), r.get("symbol", ""), r.get("side", ""),
                    r.get("entry_price", 0), r.get("quantity", 0), r.get("leverage", 1),
                    r.get("stop_loss", 0), r.get("take_profit", 0),
                    r.get("pnl", 0), r.get("fees", 0), "closed",
                    r.get("opened_at", 0), r.get("closed_at", 0),
                    r.get("take_profit_2", 0), r.get("take_profit_3", 0),
                    r.get("current_tp_index", 1), r.get("exit_reason", ""),
                    r.get("strategy_version", "current"),
                    r.get("confidence", 0), r.get("regime", ""),
                    r.get("institutional_score", 0), r.get("risk_reward", 0),
                    r.get("hold_minutes", 0), r.get("session", ""),
                    r.get("mfe_pct", 0), r.get("mae_pct", 0),
                    r.get("alpha_score", 0), r.get("alpha_tier", "C"),
                    r.get("mss_score", 0), r.get("fvg_score", 0),
                    r.get("entry_reason", ""), r.get("outcome", ""),
                    r.get("realized_r", 0), r.get("planned_rr", 0),
                    r.get("at_open_regime", ""), r.get("at_open_session", ""),
                    r.get("volatility_score", 0), r.get("quiet_market_blocked", 0),
                ),
            )
            moved += 1
        except Exception as e:
            print(f"  Skip {r.get('symbol')}: {e}")

    # 3. Delete closed positions from positions table
    cur.execute("DELETE FROM positions WHERE status='closed'")
    deleted = cur.rowcount

    # 4. Deduplicate archive (keep latest by signal_id)
    cur.execute("""
        DELETE FROM positions_archive WHERE id NOT IN (
            SELECT MAX(id) FROM positions_archive
            WHERE signal_id IS NOT NULL AND signal_id != ''
            GROUP BY signal_id
        ) AND signal_id IS NOT NULL AND signal_id != ''
    """)
    deduped = cur.rowcount

    # 5. Deduplicate archive by symbol+side+entry_price (keep latest)
    cur.execute("""
        DELETE FROM positions_archive WHERE id NOT IN (
            SELECT MAX(id) FROM positions_archive
            GROUP BY symbol, side, entry_price
        )
    """)
    deduped2 = cur.rowcount

    conn.commit()

    # 6. Count after
    cur.execute("SELECT COUNT(*) FROM positions WHERE status='closed'")
    closed_after = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM positions WHERE status='open'")
    open_after = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM positions_archive")
    arch_after = cur.fetchone()[0]

    print(f"\nRESULTS:")
    print(f"  Moved to archive: {moved}")
    print(f"  Deleted from positions: {deleted}")
    print(f"  Deduped archive: {deduped + deduped2}")
    print(f"\nAFTER: positions(closed={closed_after}, open={open_after}), archive={arch_after}")
    print(f"  Total trades: {closed_after + open_after + arch_after}")

    conn.close()

if __name__ == "__main__":
    migrate()
