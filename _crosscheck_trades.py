#!/usr/bin/env python3
"""Cross-check trades today — positions, trade history, and database."""
import json, time, sqlite3
from datetime import datetime, timezone
from pathlib import Path

print("=" * 60)
print("  CROSS-CHECK: TRADES TODAY")
print("=" * 60)

# 1. Bridge positions
print("\n--- BRIDGE POSITIONS ---")
with open("data/bridge/positions.json") as f:
    pos = json.load(f)
positions = pos.get("positions", [])
print(f"Total open: {len(positions)}")
for p in positions:
    opened = p.get("opened_at", 0)
    o_str = datetime.fromtimestamp(opened, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if opened else "N/A"
    print(f"  {p.get('side','?'):5s} {p.get('symbol','?'):20s} entry=${p.get('entry_price',0):.4f} pnl=${p.get('pnl',0):.2f} opened={o_str}")

# 2. Trade history
print("\n--- TRADE HISTORY ---")
with open("data/bridge/trade_history.json") as f:
    th = json.load(f)
trades = th.get("trades", [])
today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
today_trades = []
for t in trades:
    et = t.get("entry_time", 0)
    if et:
        try:
            day = datetime.fromtimestamp(et, tz=timezone.utc).strftime("%Y-%m-%d")
            if day == today_str:
                today_trades.append(t)
        except Exception:
            pass
print(f"Total trades: {len(trades)}")
print(f"Today ({today_str}): {len(today_trades)} trades")
for t in today_trades:
    et = t.get("entry_time", 0)
    et_str = datetime.fromtimestamp(et, tz=timezone.utc).strftime("%H:%M UTC") if et else "N/A"
    print(f"  {et_str} {t.get('side','?'):5s} {t.get('symbol','?'):20s} entry=${t.get('entry_price',0):.4f} pnl=${t.get('net_pnl',0):.2f} reason={t.get('exit_reason','?')}")

# 3. Database
print("\n--- DATABASE POSITIONS ---")
db = sqlite3.connect(str(Path("data/institutional_v1.db")))
c = db.cursor()
c.execute("SELECT symbol, side, entry_price, pnl, opened_at, closed_at, status FROM positions ORDER BY opened_at DESC LIMIT 10")
rows = c.fetchall()
for sym, side, entry, pnl, opened, closed, status in rows:
    o_str = datetime.fromtimestamp(opened, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if opened else "N/A"
    c_str = datetime.fromtimestamp(closed, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if closed else "OPEN"
    print(f"  {o_str} {side:5s} {sym:20s} entry=${entry:.4f} pnl=${pnl:.2f} [{status}]")

# 4. DB trades by day
print("\n--- DB TRADES BY DAY ---")
c.execute("""SELECT date(opened_at, 'unixepoch') as day, COUNT(*) as cnt,
    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(pnl) as total_pnl
    FROM positions WHERE opened_at IS NOT NULL GROUP BY day ORDER BY day DESC LIMIT 7""")
for day, cnt, wins, pnl in c.fetchall():
    wr = (wins / cnt * 100) if cnt > 0 else 0
    print(f"  {day}: {cnt} trades, {wins} wins ({wr:.0f}% WR), PnL=${pnl:.2f}")
db.close()

# 5. Dashboard metrics source
print("\n--- DASHBOARD 'TRADES TODAY' SOURCE ---")
print("The 'TRADES TODAY' metric comes from metrics.get('trades_today', 0)")
print("This is computed by the engine's _sync_bridge() method.")
print("It counts signals emitted TODAY from self.signals list.")
print("If the engine restarted today, old signals are lost from memory.")

# 6. Check signals in bridge
print("\n--- BRIDGE SIGNALS ---")
try:
    with open("data/bridge/signals.json") as f:
        sig_data = json.load(f)
    signals = sig_data.get("signals", [])
    print(f"Active signals: {len(signals)}")
    for s in signals[:5]:
        print(f"  {s.get('side','?'):5s} {s.get('symbol','?'):20s} conf={s.get('confidence',0):.1f}")
except Exception as e:
    print(f"Error reading signals: {e}")

# 7. Check metrics bridge
print("\n--- BRIDGE METRICS ---")
try:
    with open("data/bridge/metrics.json") as f:
        met = json.load(f)
    m = met.get("metrics", {})
    print(f"trades_today: {m.get('trades_today', 'N/A')}")
    print(f"signals_generated: {m.get('signals_generated', 'N/A')}")
    print(f"signals_rejected: {m.get('signals_rejected', 'N/A')}")
    print(f"win_rate: {m.get('win_rate', 'N/A')}")
    print(f"total_pnl: {m.get('total_pnl', 'N/A')}")
except Exception as e:
    print(f"Error: {e}")
