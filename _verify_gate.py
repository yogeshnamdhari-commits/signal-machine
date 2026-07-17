import sqlite3, time, json

db = sqlite3.connect("packages/ai-engine/data/institutional_v1.db")
db.row_factory = sqlite3.Row

# Check new positions since engine restart
rows = db.execute("SELECT symbol, side, institutional_score, opened_at FROM positions WHERE status = ? AND opened_at > ? ORDER BY opened_at DESC", ("open", 1781898100)).fetchall()
print(f"New positions since restart: {len(rows)}")
for r in rows:
    print(f"  {r['symbol']} {r['side']} score={r['institutional_score']}")

# Check bridge status
with open("packages/ai-engine/data/bridge/status.json") as f:
    s = json.load(f)
status = s.get("status", {})
age = time.time() - status.get("last_update", 0)
print(f"\nEngine: running={status.get('running')} uptime={status.get('uptime',0):.0f}s status_age={age:.0f}s")
print(f"WS connected: {status.get('ws_connected')} symbols: {status.get('symbols')}")
db.close()
