#!/usr/bin/env python3
"""Check OI bias after fix."""
import json, sys

d = json.load(open("data/bridge/market_data.json"))
rows = d.get("rows", [])

# OI regime distribution
regimes = {}
for r in rows:
    reg = r.get("oi_regime", "MISSING")
    regimes[reg] = regimes.get(reg, 0) + 1

# OI bias distribution
oi_biases = {}
for r in rows:
    b = r.get("oi_bias", "MISSING")
    oi_biases[b] = oi_biases.get(b, 0) + 1

# Top OI samples
samples = []
for r in sorted(rows, key=lambda x: abs(x.get("oi_change_pct", 0)), reverse=True)[:10]:
    chg = r.get("change_24h") or 0
    samples.append(f"  {r['symbol']:<14} oi_chg={r.get('oi_change_pct',0):+.6f}  bias={r.get('oi_bias','?')}  regime={r.get('oi_regime','?')}  pos={r.get('oi_positioning','?')}  price={chg:+.2f}%")

with open("/tmp/oi_result.txt", "w") as f:
    f.write("=== OI Regime ===\n")
    for k, v in sorted(regimes.items(), key=lambda x: -x[1]):
        f.write(f"  {k}: {v}\n")
    f.write("\n=== Engine oi_bias ===\n")
    for k, v in sorted(oi_biases.items(), key=lambda x: -x[1]):
        f.write(f"  {k}: {v}\n")
    f.write(f"\n=== Top 10 OI changes ===\n")
    for s in samples:
        f.write(s + "\n")
    f.write(f"\nTotal rows: {len(rows)}\n")
