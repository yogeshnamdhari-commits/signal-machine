#!/usr/bin/env python3
"""Phase 2 — DB query with correct column name + TF override trace"""
import os, sys, sqlite3, re
from collections import Counter

sys.path.insert(0, os.path.join(os.getcwd(), 'packages', 'ai-engine'))

print("=" * 80)
print("  PHASE 4B — DATABASE SQL PROOF (market_regime column)")
print("=" * 80)

db_path = "data/institutional_v1.db"
conn = sqlite3.connect(db_path)
c = conn.cursor()

c.execute("SELECT COUNT(*) FROM signals")
total = c.fetchone()[0]
print(f"\n  Total signals: {total:,}")

# Use correct column name
c.execute("SELECT market_regime, COUNT(*) as cnt FROM signals GROUP BY market_regime ORDER BY cnt DESC")
print(f"\n  MARKET_REGIME DISTRIBUTION (ALL TIME):")
print(f"  {'REGIME':<25s} {'COUNT':>10s} {'PCT':>8s}")
print(f"  {'─'*25} {'─'*10} {'─'*8}")
for regime, cnt in c.fetchall():
    pct = cnt / max(total, 1) * 100
    print(f"  {str(regime or 'NULL'):<25s} {cnt:>10d} {pct:>7.1f}%")

# Check if we have PnL data — look at status distribution
c.execute("SELECT status, COUNT(*) FROM signals GROUP BY status ORDER BY COUNT(*) DESC")
print(f"\n  STATUS DISTRIBUTION:")
for s, cnt in c.fetchall():
    print(f"    {s}: {cnt:,}")

# Check for pnl column existence
c.execute("PRAGMA table_info(signals)")
cols = {row[1]: row[2] for row in c.fetchall()}
print(f"\n  HAS PNL COLUMN: {'pnl' in cols}")

# If there's metadata, check if PnL is stored there
c.execute("SELECT metadata FROM signals WHERE metadata IS NOT NULL LIMIT 1")
row = c.fetchone()
if row:
    import json
    try:
        meta = json.loads(row[0])
        print(f"  METADATA KEYS: {list(meta.keys())[:10]}")
    except:
        print(f"  METADATA (raw): {str(row[0])[:200]}")

conn.close()

# ═══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 80)
print("  PHASE 6B — TF_BREAKOUT_OVERRIDE TRACING (880 symbols)")
print("=" * 80)

log_path = "data/logs/engine_2026-06-13.log"
with open(log_path, 'r') as f:
    lines = f.readlines()

# Find TF override symbols and trace what happened to them
tf_override_symbols = []
for line in lines:
    if 'TF_BREAKOUT_OVERRIDE' in line:
        m = re.search(r'(LONG|SHORT)\s+(\w+)\s+TF_BREAKOUT_OVERRIDE', line)
        if m:
            tf_override_symbols.append((m.group(2), m.group(1)))

print(f"\n  TF BREAKOUT OVERRIDE symbols (sample): {len(tf_override_symbols)} total")
for sym, side in tf_override_symbols[:10]:
    print(f"    {side} {sym}")

# Now check what happened AFTER TF override — did they hit session/checklist?
# Look for lines mentioning these symbols after TF override
print(f"\n  POST-OVERRIDE FATE (first 10 TF override symbols):")
for sym, side in tf_override_symbols[:10]:
    # Find what happened to this symbol after TF override
    found_events = []
    capture = False
    for line in lines:
        if f'{side} {sym} TF_BREAKOUT_OVERRIDE' in line:
            capture = True
            continue
        if capture and sym in line:
            if 'SESSION_BLOCKED' in line:
                found_events.append('SESSION_BLOCKED')
                break
            elif 'CHECKLIST' in line:
                found_events.append('CHECKLIST_PASSED')
                break
            elif 'HARD_REGIME' in line:
                found_events.append('HARD_REGIME_BLOCKED')
                break
            elif 'EMITTED' in line:
                found_events.append('EMITTED')
                break
            elif 'GENERATED' in line:
                found_events.append('GENERATED')
                break
            elif 'PROCESSING SYMBOL' in line and sym not in line:
                found_events.append('NEXT_SYMBOL (no further event)')
                break
    fate = found_events[0] if found_events else 'UNKNOWN'
    print(f"    {side} {sym}: → {fate}")

# Count the fates of ALL TF override symbols
print(f"\n  FATE DISTRIBUTION (all 880 TF override symbols):")
fate_counter = Counter()
for sym, side in tf_override_symbols:
    found_events = []
    capture = False
    for line in lines:
        if f'{side} {sym} TF_BREAKOUT_OVERRIDE' in line:
            capture = True
            continue
        if capture and sym in line:
            if 'SESSION_BLOCKED' in line:
                found_events.append('SESSION_BLOCKED')
                break
            elif 'CHECKLIST' in line:
                found_events.append('CHECKLIST_PASSED')
                break
            elif 'HARD_REGIME' in line:
                found_events.append('HARD_REGIME_BLOCKED')
                break
            elif 'EMITTED' in line:
                found_events.append('EMITTED')
                break
            elif 'GENERATED' in line:
                found_events.append('GENERATED')
                break
            elif 'scorer rejected' in line:
                found_events.append('SCORER_REJECTED')
                break
            elif 'PROCESSING SYMBOL' in line and sym not in line:
                found_events.append('NEXT_SYMBOL')
                break
    fate = found_events[0] if found_events else 'NO_MATCH'
    fate_counter[fate] += 1

for fate, cnt in fate_counter.most_common():
    print(f"    {fate}: {cnt}")

# ═══════════════════════════════════════════════════════════════════════════════
# Check the OTHER regime gate (REGIME_BLOCKED: RANGING)
# ═══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 80)
print("  PHASE 6C — DUAL REGIME GATE ANALYSIS")
print("=" * 80)

# Count both types of regime blocks
old_regime = 0
hard_regime = 0
for line in lines:
    if 'REGIME_BLOCKED: RANGING' in line or 'REGIME_BLOCKED: TRENDING' in line:
        old_regime += 1
    elif 'HARD_REGIME_BLOCKED' in line:
        hard_regime += 1

print(f"\n  OLD REGIME GATE (institutional_signal_engine ALLOWED_REGIMES): {old_regime:,}")
print(f"  NEW HARD REGIME GATE (engine.py HARD_ALLOWED_REGIMES):      {hard_regime:,}")
print()
print("  These are SEPARATE gates applied at DIFFERENT pipeline stages:")
print("  1. institutional_signal_engine: allows {trending_bull, trending_bear, range, volatile}")
print("  2. engine.py HARD gate:          allows {breakout}")
print()
print("  CONFLICT: The institutional engine ALLOWS trending/range,")
print("  but the HARD gate BLOCKS trending/range.")
print("  Only 'breakout' passes both gates.")
print()

# Also check the REGIME_BLOCKED old messages
print(f"  SAMPLE OLD-STYLE REGIME BLOCKS:")
count = 0
for line in lines:
    if 'REGIME_BLOCKED:' in line and 'HARD_REGIME' not in line:
        print(f"    {line.strip()[-120:]}")
        count += 1
        if count >= 5:
            break

# ═══════════════════════════════════════════════════════════════════════════════
# Find the EXACT code path for REGIME_BLOCKED: RANGING
# ═══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 80)
print("  PHASE 6D — REGIME BLOCKED: RANGING SOURCE CODE")
print("=" * 80)

# Search for "RANGING" in engine code
engine_path = "packages/ai-engine/core/engine.py"
with open(engine_path, 'r') as f:
    engine_lines = f.readlines()

for i, line in enumerate(engine_lines):
    if 'RANGING' in line or 'Trend-following not allowed' in line:
        start = max(0, i - 3)
        end = min(len(engine_lines), i + 3)
        print(f"\n  LINE {i+1}:")
        for j in range(start, end):
            marker = ">>>" if j == i else "   "
            print(f"  {marker} {j+1:5d}: {engine_lines[j].rstrip()}")

# Also search institutional_signal_engine
inst_path = "packages/ai-engine/scanner/institutional_signal_engine.py"
with open(inst_path, 'r') as f:
    inst_lines = f.readlines()

for i, line in enumerate(inst_lines):
    if 'RANGING' in line or 'Trend-following' in line:
        start = max(0, i - 3)
        end = min(len(inst_lines), i + 3)
        print(f"\n  INSTITUTIONAL ENGINE LINE {i+1}:")
        for j in range(start, end):
            marker = ">>>" if j == i else "   "
            print(f"  {marker} {j+1:5d}: {inst_lines[j].rstrip()}")

print()
print("  ═══ END OF FORENSICS ═══")
