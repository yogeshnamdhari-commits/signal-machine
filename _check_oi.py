#!/usr/bin/env python3
"""Check OI bias data."""
import json, sys

d = json.load(open("data/bridge/market_data.json"))
rows = d.get("rows", [])

# Redirect stdout to avoid engine log interference
out = open("/tmp/oi_diag.txt", "w")

regimes = {}
for r in rows:
    reg = r.get("oi_regime", "MISSING")
    regimes[reg] = regimes.get(reg, 0) + 1

out.write("OI Regime distribution:\n")
for k, v in sorted(regimes.items(), key=lambda x: -x[1]):
    out.write(f"  {k}: {v}\n")

out.write("\nSample OI data (top 5 by abs oi_change_pct):\n")
for r in sorted(rows, key=lambda x: abs(x.get("oi_change_pct", 0)), reverse=True)[:5]:
    out.write(f"  {r['symbol']:<14} oi_chg={r.get('oi_change_pct',0):+.6f}  oi_regime={r.get('oi_regime','?')}  oi_pos={r.get('oi_positioning','?')}  oi_str={r.get('oi_strength',0):.1f}  price_chg={r.get('change_24h',0):+.2f}%\n")

out.write("\nThreshold analysis:\n")
for thresh in [0.001, 0.002, 0.005, 0.01, 0.02, 0.05]:
    n = sum(1 for r in rows if abs(r.get("oi_change_pct", 0)) >= thresh)
    out.write(f"  |oi_chg| >= {thresh}: {n}/{len(rows)}\n")

expansion = sum(1 for r in rows if "expansion" in str(r.get("oi_regime", "")) or "contraction" in str(r.get("oi_regime", "")))
out.write(f"\nSymbols with expansion/contraction regime: {expansion}/{len(rows)}\n")

# Check what oi_bias the engine computed
oi_biases = {}
for r in rows:
    b = r.get("oi_bias", "MISSING")
    oi_biases[b] = oi_biases.get(b, 0) + 1
out.write("\nEngine oi_bias distribution:\n")
for k, v in sorted(oi_biases.items(), key=lambda x: -x[1]):
    out.write(f"  {k}: {v}\n")

out.close()
