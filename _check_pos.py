import json, time

# === BRIDGE POSITIONS ===
print(f"=== BRIDGE POSITIONS ===")
with open("packages/ai-engine/data/bridge/positions.json") as f:
    data = json.load(f)
positions = data.get("positions", [])
print(f"Count: {len(positions)}")
total_pnl = 0
total_margin = 0
for p in positions:
    sym = p.get("symbol", "?")
    side = p.get("side", "?")
    entry = p.get("entry_price", 0) or 0
    sl = p.get("stop_loss", 0) or 0
    score = p.get("institutional_score", 0) or p.get("score", 0) or 0
    conf = p.get("confidence", 0) or 0
    qty = p.get("quantity", 0) or 0
    lev = p.get("leverage", 1) or 1
    margin = entry * qty / lev
    pnl = p.get("unrealized_pnl", 0) or 0
    pnl_pct = p.get("unrealized_pnl_pct", 0) or 0
    sl_dist = abs(entry - sl) / entry * 100 if entry and sl else 0
    total_pnl += pnl
    total_margin += margin
    tag = "⚠️" if score < 85 else "✅"
    print(f"  {tag} {sym:18s} {side:6s} score={score:5.1f} conf={conf:.3f} sl_dist={sl_dist:5.1f}% margin=${margin:6.2f} pnl=${pnl:7.2f} ({pnl_pct:+.1f}%)")
print(f"\nTotal margin: ${total_margin:.2f}")
print(f"Total unrealized: ${total_pnl:.2f}")

db.close()
