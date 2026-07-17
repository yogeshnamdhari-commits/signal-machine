#!/usr/bin/env python3
"""Diagnostic: check signals for duplicates and BLESSUSDT."""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'packages', 'ai-engine'))

sig_file = 'packages/ai-engine/data/bridge/signals.json'
try:
    with open(sig_file) as f:
        data = json.load(f)
except Exception as e:
    print(f"Error reading {sig_file}: {e}")
    sys.exit(1)

sigs = data.get('signals', [])
print(f"Total signals: {len(sigs)}")
print(f"Bridge timestamp: {data.get('timestamp', 0)}")

# Check for duplicates by symbol+side
syms = {}
for s in sigs:
    sym = s.get('symbol', '?')
    side = s.get('type', s.get('side', '?'))
    key = f'{sym}_{side}'
    if key in syms:
        syms[key] += 1
    else:
        syms[key] = 1
dupes = {k: v for k, v in syms.items() if v > 1}
if dupes:
    print(f"\nDUPLICATES FOUND: {dupes}")
else:
    print("\nNo duplicates by symbol+side")

# Check same symbol different sides
sym_sides = {}
for s in sigs:
    sym = s.get('symbol', '?')
    side = s.get('type', s.get('side', '?'))
    if sym not in sym_sides:
        sym_sides[sym] = []
    sym_sides[sym].append(side)
multi_dir = {k: v for k, v in sym_sides.items() if len(v) > 1}
if multi_dir:
    print(f"\nMulti-direction symbols (LONG+SHORT for same symbol):")
    for sym, sides in multi_dir.items():
        print(f"  {sym}: {sides}")

# Check BLESSUSDT
print("\n--- BLESSUSDT signals ---")
for s in sigs:
    if 'BLESS' in s.get('symbol', '').upper():
        print(f"  side={s.get('type', s.get('side'))} conf={s.get('confidence',0):.2f} "
              f"score={s.get('institutional_score',0):.0f} "
              f"created={s.get('created_at',0):.0f} id={s.get('id')}")

# Show all signals
print(f"\n--- All {len(sigs)} signals ---")
for i, s in enumerate(sigs):
    ts = s.get('created_at', 0)
    from datetime import datetime
    ts_str = datetime.fromtimestamp(ts).strftime('%H:%M:%S') if ts else '?'
    print(f"  #{i+1:2d} {s.get('type','?'):5s} {s.get('symbol','?'):16s} "
          f"conf={s.get('confidence',0):.2f} score={s.get('institutional_score',0):5.0f} "
          f"time={ts_str}")
