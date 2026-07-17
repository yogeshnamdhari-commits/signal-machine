#!/usr/bin/env python3
"""Recover lost Jun 15-16 data from bridge files into DB."""
import json, sqlite3, time
from pathlib import Path
from datetime import datetime, timezone

DB = Path(__file__).resolve().parent / "data" / "institutional_v1.db"
BRIDGE = Path(__file__).resolve().parent / "data" / "bridge"

conn = sqlite3.connect(str(DB))
cur = conn.cursor()

# 1. Re-insert live positions from bridge into DB
positions_data = json.loads((BRIDGE / "positions.json").read_text())
live = positions_data.get("positions", [])
inserted = 0
for p in live:
    try:
        cur.execute(
            """INSERT INTO positions
               (signal_id, symbol, side, entry_price, quantity, leverage,
                stop_loss, take_profit, pnl, fees, status, opened_at,
                confidence, regime, institutional_score, risk_reward, session,
                hold_minutes, exit_reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p.get("signal_id", ""), p.get("symbol", ""), p.get("side", ""),
             p.get("entry_price", 0), p.get("quantity", 0), p.get("leverage", 10),
             p.get("stop_loss", 0), p.get("take_profit", 0),
             p.get("pnl", 0), 0, "open", p.get("opened_at", time.time()),
             p.get("confidence", 0), p.get("regime", ""),
             p.get("institutional_score", 0), p.get("risk_reward", 0),
             p.get("session", ""), 0, ""))
        inserted += 1
    except Exception as e:
        print(f"  Skip {p.get('symbol')}: {e}")

# 2. Re-insert closed trades from trade_history into archive
th_data = json.loads((BRIDGE / "trade_history.json").read_text())
trades = th_data.get("trades", [])
archived = 0
for t in trades:
    opened = t.get("timestamp", t.get("opened_at", 0))
    closed = t.get("exit_time", t.get("closed_at", 0))
    if not opened:
        continue
    try:
        cur.execute(
            """INSERT INTO positions_archive
               (signal_id, symbol, side, entry_price, quantity, leverage,
                stop_loss, take_profit, pnl, fees, status, opened_at, closed_at,
                exit_reason, hold_minutes, confidence, regime,
                institutional_score, risk_reward, session, realized_r)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t.get("signal_id", f"{t.get('symbol','')}_{int(opened)}"),
             t.get("symbol", ""), t.get("side", ""),
             t.get("entry_price", 0), t.get("quantity", 0), t.get("leverage", 10),
             t.get("stop_loss", 0), t.get("take_profit", 0),
             t.get("pnl", 0), t.get("fees", 0), "closed",
             opened, closed,
             t.get("exit_reason", ""), t.get("hold_minutes", 0),
             t.get("confidence", 0), t.get("regime", ""),
             t.get("institutional_score", 0), t.get("risk_reward", 0),
             t.get("session", ""), t.get("realized_r", 0)))
        archived += 1
    except Exception as e:
        print(f"  Skip trade {t.get('symbol')}: {e}")

conn.commit()

# Verify
cur.execute("SELECT COUNT(*) FROM positions")
pos = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM positions_archive")
arch = cur.fetchone()[0]
cur.execute("""SELECT date(opened_at, 'unixepoch', 'utc') as day, COUNT(*)
    FROM positions_archive GROUP BY day ORDER BY day DESC LIMIT 10""")
dates = cur.fetchall()

print(f"Recovered: {inserted} live positions, {archived} closed trades")
print(f"DB now: positions={pos}, archive={arch}")
print("Archive dates:")
for d, c in dates:
    print(f"  {d}: {c}")

_ts_start = datetime(2026, 6, 15, tzinfo=timezone.utc).timestamp()
_ts_end = datetime(2026, 6, 17, tzinfo=timezone.utc).timestamp()
cur.execute("SELECT COUNT(*) FROM positions WHERE opened_at >= ? AND opened_at < ?", (_ts_start, _ts_end))
pos_range = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM positions_archive WHERE opened_at >= ? AND opened_at < ?", (_ts_start, _ts_end))
arch_range = cur.fetchone()[0]
print(f"\nJun 15-16: {pos_range} open + {arch_range} closed = {pos_range + arch_range} total")

conn.close()
