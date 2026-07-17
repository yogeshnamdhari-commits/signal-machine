#!/usr/bin/env python3
"""Test the _compute_oi_bias function against actual data."""
import json

d = json.load(open("data/bridge/market_data.json"))
rows = d.get("rows", [])

# Inline the dashboard function
def _compute_oi_bias(row):
    oi_chg = row.get("oi_change_pct", 0) or 0
    price_chg = row.get("change_24h", 0) or 0
    oi_regime = str(row.get("oi_regime", "neutral_oi"))
    oi_positioning = str(row.get("oi_positioning", "neutral"))
    oi_strength = row.get("oi_strength", 50) or 50
    if "expansion" in oi_regime or "contraction" in oi_regime:
        if oi_chg > 0:
            return "buy" if price_chg >= 0 else "sell"
        elif oi_chg < 0:
            return "sell" if price_chg >= 0 else "buy"
    if abs(oi_chg) < 0.005:
        return "neutral"
    if oi_chg > 0:
        return "buy" if price_chg >= 0 else "sell"
    else:
        return "sell" if price_chg >= 0 else "buy"

out = open("/tmp/oi_bias_test.txt", "w")
results = {}
for r in rows:
    bias = _compute_oi_bias(r)
    results[bias] = results.get(bias, 0) + 1
    if bias != "neutral":
        out.write(f"  {r['symbol']:<14} bias={bias}  oi_chg={r.get('oi_change_pct',0):+.6f}  price={r.get('change_24h',0):+.2f}%\n")

out.write(f"\nResult distribution: {results}\n")

# Show top non-neutral examples
out.write("\nTop non-neutral symbols:\n")
for r in sorted(rows, key=lambda x: abs(x.get("oi_change_pct", 0)), reverse=True)[:10]:
    bias = _compute_oi_bias(r)
    out.write(f"  {r['symbol']:<14} oi_chg={r.get('oi_change_pct',0):+.6f}  price={r.get('change_24h',0):+.2f}%  => {bias}\n")

out.close()
