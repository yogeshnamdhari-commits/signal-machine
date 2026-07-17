#!/usr/bin/env python3
"""Check DB trades today."""
import os, sqlite3, time
from datetime import datetime, timezone

__file__ = "packages/ai-engine/core/engine.py"
_db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "institutional_v1.db")
print(f"DB path: {_db_path}")
print(f"Exists: {os.path.exists(_db_path)}")

conn = sqlite3.connect(_db_path)
c = conn.cursor()

# Today start (UTC)
today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
print(f"Today start: {datetime.fromtimestamp(today_start, tz=timezone.utc)}")

c.execute("SELECT COUNT(*) FROM positions")
print(f"Total positions: {c.fetchone()[0]}")

c.execute("SELECT COUNT(*) FROM positions WHERE status='open'")
print(f"Open positions: {c.fetchone()[0]}")

c.execute("SELECT symbol, side, entry_price, status, opened_at FROM positions WHERE status='open' LIMIT 10")
for row in c.fetchall():
    sym, side, entry, status, opened = row
    o_str = datetime.fromtimestamp(opened, tz=timezone.utc).strftime("%H:%M UTC") if opened else "N/A"
    print(f"  OPEN: {side} {sym} entry=${entry:.4f} opened={o_str}")

c.execute("SELECT COUNT(*) FROM positions WHERE status='closed' AND closed_at > ?", (today_start,))
print(f"Closed today: {c.fetchone()[0]}")

c.execute("SELECT symbol, side, pnl, closed_at FROM positions_archive WHERE closed_at > ? LIMIT 10", (today_start,))
rows = c.fetchall()
print(f"Archived today: {len(rows)}")
for sym, side, pnl, closed in rows:
    c_str = datetime.fromtimestamp(closed, tz=timezone.utc).strftime("%H:%M UTC") if closed else "N/A"
    print(f"  {c_str} {side} {sym} pnl=${pnl:.2f}")

# Also check signals today
c.execute("SELECT COUNT(*) FROM signals WHERE timestamp > ?", (today_start,))
print(f"\nSignals today: {c.fetchone()[0]}")

conn.close()
