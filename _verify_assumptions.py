"""Verify 4 assumptions about the stale signal problem."""
import sqlite3
import time
import json
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "packages/ai-engine/data/institutional_v1.db"
BRIDGE_DIR = Path(__file__).resolve().parent / "packages/ai-engine/data/bridge"

conn = sqlite3.connect(str(DB_PATH), timeout=10)
cur = conn.cursor()

print("=" * 60)
print("ASSUMPTION 1: ICNTUSDT zombie in DB")
print("=" * 60)
cur.execute(
    "SELECT id, symbol, side, confidence, entry, status, timestamp "
    "FROM signals WHERE symbol='ICNTUSDT' ORDER BY timestamp DESC"
)
rows = cur.fetchall()
for r in rows:
    ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(r[6]))
    age = (time.time() - r[6]) / 3600
    print(f"  id={r[0]} | {r[2]} | conf={r[3]:.4f} | entry={r[4]} | status={r[5]} | ts={ts} | age={age:.1f}h")
print(f"  Total ICNTUSDT signals: {len(rows)}")

cur.execute("SELECT COUNT(*) FROM positions WHERE symbol='ICNTUSDT'")
print(f"  ICNTUSDT positions: {cur.fetchone()[0]}")

# Check all 21 active signals
print("\n" + "=" * 60)
print("ALL ACTIVE SIGNALS IN DB (should be 21)")
print("=" * 60)
cur.execute(
    "SELECT s.symbol, s.side, s.confidence, s.entry, s.status, s.timestamp, s.id "
    "FROM signals s WHERE s.status='active' ORDER BY s.timestamp ASC"
)
rows = cur.fetchall()
for r in rows:
    ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(r[5]))
    age = (time.time() - r[5]) / 3600
    # Check if position exists
    cur2 = conn.cursor()
    cur2.execute("SELECT COUNT(*) FROM positions WHERE signal_id=?", (r[6],))
    has_pos = cur2.fetchone()[0]
    print(f"  {r[0]:15s} {r[1]:6s} conf={r[2]:.4f} status={r[4]} ts={ts} age={age:.0f}h has_pos={has_pos}")
print(f"  Total active: {len(rows)}")

print("\n" + "=" * 60)
print("ASSUMPTION 2: Are new signals generated but blocked by dedup?")
print("=" * 60)
# Check the dedup query — are there symbols with old active signals that would block new ones?
cur.execute(
    "SELECT symbol, side, entry, timestamp FROM signals WHERE status='active' "
    "ORDER BY timestamp DESC"
)
active = cur.fetchall()
print(f"  Active signals that could block new ones via dedup: {len(active)}")
for r in active:
    ts = time.strftime('%Y-%m-%d %H:%M', time.localtime(r[3]))
    print(f"    {r[0]} {r[1]} entry={r[2]} (since {ts})")

print("\n" + "=" * 60)
print("ASSUMPTION 3: What does the UI read from?")
print("=" * 60)
# Check signals.json bridge
sig_file = BRIDGE_DIR / "signals.json"
if sig_file.exists():
    with open(sig_file) as f:
        data = json.load(f)
    signals = data.get("signals", [])
    ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data.get("timestamp", 0)))
    print(f"  signals.json: {len(signals)} signals, written at {ts}")
    for s in signals:
        print(f"    {s.get('symbol')} {s.get('side')} conf={s.get('confidence',0)} status={s.get('status')}")
else:
    print("  signals.json: DOES NOT EXIST")

# Check ema_v5.json bridge
ema_file = BRIDGE_DIR / "ema_v5.json"
if ema_file.exists():
    with open(ema_file) as f:
        data = json.load(f)
    ema = data.get("ema_v5", {})
    signals = ema.get("signals", [])
    ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data.get("timestamp", 0)))
    print(f"  ema_v5.json: {len(signals)} signals, written at {ts}")
    for s in signals:
        st = time.strftime('%Y-%m-%d %H:%M', time.localtime(s.get("timestamp", 0)))
        print(f"    {s.get('symbol')} {s.get('side')} conf={s.get('confidence',0)} ts={st}")
else:
    print("  ema_v5.json: DOES NOT EXIST")

# Check status.json
status_file = BRIDGE_DIR / "status.json"
if status_file.exists():
    with open(status_file) as f:
        data = json.load(f)
    s = data.get("status", {})
    print(f"  status.json: running={s.get('running')} signals={s.get('signals')} uptime={s.get('uptime',0)/3600:.1f}h")

print("\n" + "=" * 60)
print("ASSUMPTION 4: Expiration intent")
print("=" * 60)
print("  _expire_signals() in engine.py sets status='expired' IN MEMORY ONLY")
print("  Comment: 'Do NOT write expired to DB — let positions table be source of truth'")
print("  update_signal_status() EXISTS in signal_repository.py but is NEVER CALLED")
print("  cleanup_old_data(days=30) deletes signals > 30 days — runs every ~5 min")
print("  So: signals become zombies for up to 30 days, blocking dedup checks")

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
conn.close()
