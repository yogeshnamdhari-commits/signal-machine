#!/usr/bin/env python3
"""Quick check: what symbols hit TF_BREAKOUT_OVERRIDE and what happened next."""
import re

log_path = 'data/logs/engine_2026-06-13.log'
with open(log_path, 'r', errors='replace') as f:
    lines = f.readlines()

# Find all TF_BREAKOUT_OVERRIDE lines
override_lines = [l.strip() for l in lines if 'TF_BREAKOUT_OVERRIDE' in l]

# Extract symbols
override_syms = set()
for line in override_lines:
    m = re.search(r'(LONG|SHORT)\s+(\w+)\s+TF_BREAKOUT', line)
    if m:
        override_syms.add((m.group(1), m.group(2)))

out = open('/tmp/override_report.txt', 'w')
P = out.write

P(f'TF_BREAKOUT_OVERRIDE HITS: {len(override_lines)}\n')
P(f'UNIQUE SYMBOLS: {len(override_syms)}\n\n')

P(f'{"SIDE":<7s} {"SYMBOL":<20s}\n')
P(f'{"─"*7} {"─"*20}\n')
for side, sym in sorted(override_syms):
    P(f'{side:<7s} {sym:<20s}\n')

P('\n')

# For each symbol, check what happened next
for side, sym in sorted(override_syms):
    # Find all log lines for this symbol
    sym_lines = [l.strip() for l in lines if sym in l and 'DATA_QUALITY' not in l]
    
    P(f'═══ {side} {sym} ═══\n')
    for line in sym_lines[-10:]:
        # Clean up loguru prefix
        if '|' in line:
            parts = line.split('|', 2)
            if len(parts) >= 3:
                msg = parts[2].strip()
            else:
                msg = line
        else:
            msg = line
        P(f'  {msg[:120]}\n')
    P('\n')

# Count session blocks vs other outcomes
session_blocks = sum(1 for l in override_lines if 'SESSION_BLOCKED' not in l)
# Actually, find lines that have BOTH TF_BREAKOUT and SESSION in nearby lines
# Better: for each override symbol, check next occurrence
next_events = {'SESSION_BLOCKED': 0, 'CHECKLIST_PASSED': 0, 'CHECKLIST_REJECTED': 0, 
               'EMITTED': 0, 'HARD_REGIME_BLOCKED': 0, 'SCORER_REJECTED': 0, 'OTHER': 0}

P('='*60 + '\n')
P('  OUTCOME AFTER TF_BREAKOUT_OVERRIDE\n')
P('='*60 + '\n\n')

for side, sym in sorted(override_syms):
    # Find the TF_BREAKOUT line index
    for i, line in enumerate(lines):
        if 'TF_BREAKOUT_OVERRIDE' in line and sym in line:
            # Look at next 20 lines for this symbol
            found = False
            for j in range(i+1, min(i+30, len(lines))):
                nl = lines[j]
                if sym in nl:
                    if 'SESSION_BLOCKED' in nl:
                        next_events['SESSION_BLOCKED'] += 1
                        P(f'  {sym}: SESSION_BLOCKED (ASIA)\n')
                        found = True
                        break
                    elif 'CHECKLIST_PASSED' in nl:
                        next_events['CHECKLIST_PASSED'] += 1
                        P(f'  {sym}: CHECKLIST_PASSED ✅\n')
                        found = True
                        break
                    elif 'CHECKLIST_REJECTED' in nl:
                        next_events['CHECKLIST_REJECTED'] += 1
                        P(f'  {sym}: CHECKLIST_REJECTED\n')
                        found = True
                        break
                    elif 'EMITTED' in nl or 'signal_emitted' in nl:
                        next_events['EMITTED'] += 1
                        P(f'  {sym}: EMITTED 🚨\n')
                        found = True
                        break
                    elif 'HARD_REGIME_BLOCKED' in nl:
                        next_events['HARD_REGIME_BLOCKED'] += 1
                        P(f'  {sym}: HARD_REGIME_BLOCKED (next cycle)\n')
                        found = True
                        break
                    elif 'scorer rejected' in nl:
                        next_events['SCORER_REJECTED'] += 1
                        P(f'  {sym}: SCORER_REJECTED (score<80)\n')
                        found = True
                        break
            if not found:
                next_events['OTHER'] += 1
                P(f'  {sym}: OTHER (no clear outcome)\n')
            break

P(f'\n  OUTCOME SUMMARY:\n')
for event, count in sorted(next_events.items(), key=lambda x: -x[1]):
    P(f'    {event:<25s} {count:>5d}\n')

out.close()
print('DONE')
