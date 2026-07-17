#!/usr/bin/env python3
"""Quick check for bridge data quality."""
import json, time

d = json.load(open("data/bridge/market_data.json"))
rows = d.get("rows", [])
age = time.time() - d.get("timestamp", 0)
print(f"{len(rows)} rows, age={age:.0f}s")

# Field coverage
nsp = sum(1 for r in rows if r.get("sweep_price", 0) > 0)
nfh = sum(1 for r in rows if r.get("fvg_gap_high", 0) > 0)
nef = sum(1 for r in rows if r.get("exchange_flow", 0) != 0)
nhi = sum(1 for r in rows if r.get("open_interest", 0) > 0)
nfd = sum(1 for r in rows if r.get("funding", 0) != 0)
nsw = sum(1 for r in rows if r.get("sw_signal", "neutral") != "neutral")
nfvg = sum(1 for r in rows if r.get("fvg_alignment", "neutral") != "neutral")

print(f"Open Interest: {nhi}/{len(rows)}")
print(f"Exchange Flow: {nef}/{len(rows)}")
print(f"Funding: {nfd}/{len(rows)}")
print(f"Sweep Price: {nsp}/{len(rows)}")
print(f"Sweep Signal (non-neutral): {nsw}/{len(rows)}")
print(f"FVG Gap: {nfh}/{len(rows)}")
print(f"FVG Alignment (non-neutral): {nfvg}/{len(rows)}")
print()

# Top 5 by exchange flow
for r in sorted(rows, key=lambda x: abs(x.get("exchange_flow", 0)), reverse=True)[:5]:
    sp = r.get("sweep_price", 0)
    srp = r.get("sweep_reject_price", 0)
    fgh = r.get("fvg_gap_high", 0)
    fgl = r.get("fvg_gap_low", 0)
    fgs = r.get("fvg_latest_strength", 0)
    ef = r.get("exchange_flow", 0)
    oi = r.get("open_interest", 0)
    fund = r.get("funding", 0)
    fnd_dir = r.get("funding_bias", "?")
    sw_sig = r.get("sw_signal", "neutral")
    fvg_al = r.get("fvg_alignment", "neutral")
    fvg_sc = r.get("fvg_score", 0)
    print(f"{r['symbol']:<14} SP={sp:>10.4f} SRP={srp:>10.4f} | FGH={fgh:>10.4f} FGL={fgl:>10.4f} str={fgs:.2f} | EF={ef:>12.2f} OI={oi:>14,.2f} fund={fund:.4f} {fnd_dir} | sw={sw_sig} fvg={fvg_al}[{fvg_sc:.0f}]")
