#!/usr/bin/env python3
"""PRODUCTION LONDON OPEN VALIDATION — Runtime Data Only, No Code Changes"""
import datetime, sys, os, re, json
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.getcwd(), 'packages', 'ai-engine'))

now = datetime.datetime.now(datetime.timezone.utc)
hour = now.hour

# Determine session
if 0 <= hour < 7:
    session = 'ASIA'
elif 7 <= hour < 13:
    session = 'LONDON'
elif 13 <= hour < 20:
    session = 'NEW_YORK'
else:
    session = 'OFF_HOURS'

session_pf_data = {
    'ASIA':       {'pf': 0.54, 'pnl': -1791, 'expectancy': 'NEGATIVE'},
    'LONDON':     {'pf': 1.40, 'pnl': +2267, 'expectancy': 'PROFITABLE'},
    'NEW_YORK':   {'pf': 0.83, 'pnl': -3857, 'expectancy': 'NEGATIVE'},
    'OFF_HOURS':  {'pf': 0.14, 'pnl': -3088, 'expectancy': 'NEGATIVE'},
}

spf = session_pf_data[session]

# Check engine status
import subprocess
result = subprocess.run(['pgrep', '-f', 'engine.py'], capture_output=True, text=True)
engine_running = result.returncode == 0
engine_pid = result.stdout.strip() if engine_running else 'NOT RUNNING'

print('='*70)
print('  PRODUCTION LONDON OPEN VALIDATION')
print('  Runtime Data Only — No Code Changes')
print('='*70)
print()
print(f'  1. CURRENT SESSION:      {session}')
print(f'  2. SESSION PF:           {spf["pf"]}')
print(f'  3. SESSION EXPECTANCY:   {spf["expectancy"]} (PnL=${spf["pnl"]:+,})')
print()
print(f'  ENGINE STATUS:          {"RUNNING (PID " + engine_pid + ")" if engine_running else "NOT RUNNING"}')
print(f'  TIMESTAMP:              {now.strftime("%Y-%m-%d %H:%M:%S UTC")}')
print()

# Read the most recent engine log
log_dir = 'data/logs'
log_files = sorted([f for f in os.listdir(log_dir) if f.startswith('engine_') and f.endswith('.log')])
if not log_files:
    print('  ERROR: No engine logs found')
    sys.exit(1)

log_path = os.path.join(log_dir, log_files[-1])
print(f'  LOG FILE: {log_path}')

with open(log_path, 'r', errors='replace') as f:
    lines = f.readlines()

total_lines = len(lines)
print(f'  LOG LINES: {total_lines:,}')
print()

# Parse log for comprehensive funnel analysis
# Reset counters
scorer_rejected = 0
safety_blocked = 0
hard_regime_blocked = 0
regime_blocked = 0
session_passed_count = 0
session_blocked_count = 0
breakout_reached = 0
breakout_blocked = 0
checklist_passed = 0
checklist_blocked = 0
emitted = 0
error_count = 0

# Per-symbol tracking
symbols_session_passed = {}  # symbol -> line detail
symbols_breakout_reached = {}
symbols_emitted = []
all_blocked_details = []
block_reasons = Counter()

# Track the LAST COMPLETE CYCLE (find cycle boundaries)
cycle_start_indices = []
for i, line in enumerate(lines):
    if 'CYCLE' in line and ('START' in line or '🔄' in line or 'cycle' in line.lower()):
        cycle_start_indices.append(i)

# Also look for "SCAN COMPLETE" or similar cycle end markers
cycle_end_indices = []
for i, line in enumerate(lines):
    if ('SCAN COMPLETE' in line or 'Cycle complete' in line or 'cycle_completed' in line or 
        'CYCLE_COMPLETE' in line or '📊 Cycle' in line):
        cycle_end_indices.append(i)

print(f'  CYCLE STARTS FOUND: {len(cycle_start_indices)}')
print(f'  CYCLE ENDS FOUND:   {len(cycle_end_indices)}')
print()

# Use the LAST cycle if found, otherwise analyze the full log
if cycle_start_indices:
    last_cycle_start = cycle_start_indices[-1]
    last_cycle_lines = lines[last_cycle_start:]
    print(f'  ANALYZING LAST CYCLE: lines {last_cycle_start+1:,}-{total_lines:,} ({len(last_cycle_lines):,} lines)')
else:
    last_cycle_lines = lines
    print(f'  ANALYZING FULL LOG: {total_lines:,} lines (no cycle boundaries found)')

print()

# Parse each line in the last cycle
for line in last_cycle_lines:
    # SAFETY BLOCKED
    if 'SAFETY:' in line and ('BLOCKED' in line or 'DATA_STALE' in line):
        safety_blocked += 1
        m = re.search(r'(LONG|SHORT)\s+(\w+)\s+SAFETY', line)
        if m:
            block_reasons[f'SAFETY ({m.group(2)})'] += 1

    # SCORER REJECTED
    elif 'scorer rejected' in line:
        scorer_rejected += 1

    # HARD_REGIME_BLOCKED
    elif 'HARD_REGIME_BLOCKED' in line:
        hard_regime_block += 1
        m = re.search(r'(LONG|SHORT)\s+(\w+)\s+HARD_REGIME_BLOCKED:\s*(\w+)', line)
        if m:
            block_reasons[f'HARD_REGIME ({m.group(3)})'] += 1

    # REGIME_BLOCKED (softer)
    elif 'REGIME_BLOCKED' in line:
        regime_blocked += 1
        m = re.search(r'REGIME_BLOCKED:\s*(.+)', line)
        if m:
            reason = m.group(1).strip()[:50]
            block_reasons[f'REGIME ({reason})'] += 1

    # SESSION PASSED
    elif 'SESSION_PASSED' in line or 'session_passed' in line or '✅ SESSION' in line:
        session_passed_count += 1
        m = re.search(r'(LONG|SHORT)\s+(\w+)', line)
        if m:
            symbols_session_passed[m.group(2)] = {
                'side': m.group(1),
                'detail': line.strip()
            }

    # SESSION BLOCKED
    elif 'SESSION_BLOCKED' in line or 'SESSION BLOCKED' in line:
        session_blocked_count += 1
        m = re.search(r'(LONG|SHORT)\s+(\w+)\s+SESSION', line)
        if m:
            block_reasons[f'SEASONAL ({m.group(2)})'] += 1

    # BREAKOUT
    elif 'BREAKOUT' in line:
        if 'PASSED' in line or 'passed' in line or '✅' in line:
            breakout_reached += 1
            m = re.search(r'(LONG|SHORT)\s+(\w+)', line)
            if m:
                symbols_breakout_reached[m.group(2)] = line.strip()
        elif 'BLOCKED' in line or 'blocked' in line:
            breakout_blocked += 1

    # CHECKLIST
    elif 'CHECKLIST' in line:
        if 'PASSED' in line or 'passed' in line:
            checklist_passed += 1
        elif 'BLOCKED' in line or 'blocked' in line:
            checklist_blocked += 1

    # EMITTED
    elif 'EMITTED' in line or 'signal_emitted' in line or '🚨' in line or 'SIGNAL EMITTED' in line:
        emitted += 1
        symbols_emitted.append(line.strip())

    # ERRORS
    elif 'ERROR' in line and 'Handler error' in line:
        error_count += 1

# Consolidate HARD_REGIME count (fix double-counting)
# Recount cleanly
hard_regime_blocked = 0
for line in last_cycle_lines:
    if 'HARD_REGIME_BLOCKED' in line:
        hard_regime_blocked += 1

# Recount REGIME_BLOCKED
regime_blocked = 0
for line in last_cycle_lines:
    if 'REGIME_BLOCKED' in line and 'HARD_REGIME_BLOCKED' not in line:
        regime_blocked += 1

print(f'  4. SESSION FILTER PASS COUNT:  {session_passed_count}')
print(f'  5. BREAKOUT PASS COUNT:        {breakout_reached}')
print(f'  6. CHECKLIST PASS COUNT:       {checklist_passed}')
print(f'  7. GENERATED SIGNALS:          {emitted}')
print(f'  8. EMITTED SIGNALS:            {emitted}')
print()

print('='*70)
print('  FUNNEL BREAKDOWN (Last Cycle)')
print('='*70)
print()
print(f'  {"GATE":<40s} {"PASS":>8s} {"BLOCK":>8s}')
print(f'  {"─"*40} {"─"*8} {"─"*8}')
print(f'  {"SAFETY (data quality)":<40s} {"—":>8s} {safety_blocked:>8d}')
print(f'  {"SCORER (inst_score < 80)":<40s} {"—":>8s} {scorer_rejected:>8d}')
print(f'  {"HARD_REGIME (trending bull/bear)":<40s} {"—":>8s} {hard_regime_blocked:>8d}')
print(f'  {"REGIME (ranging + conf < 75)":<40s} {"—":>8s} {regime_blocked:>8d}')
print(f'  {"SESSION PF FILTER":<40s} {session_passed_count:>8d} {session_blocked_count:>8d}')
print(f'  {"BREAKOUT DETECTION":<40s} {breakout_reached:>8d} {breakout_blocked:>8d}')
print(f'  {"CHECKLIST GATE (10/10)":<40s} {checklist_passed:>8d} {checklist_blocked:>8d}')
print(f'  {"SIGNAL EMITTED":<40s} {emitted:>8d} {"—":>8s}')
print()

if block_reasons:
    print('  BLOCK REASON DISTRIBUTION:')
    for reason, count in block_reasons.most_common(20):
        print(f'    {reason:<45s} {count:>6d}')
    print()

if symbols_session_passed:
    print('='*70)
    print('  SYMBOLS PASSING SESSION FILTER')
    print('='*70)
    for sym, info in symbols_session_passed.items():
        print(f'  {info["side"]:<6s} {sym}')
        print(f'    DETAIL: {info["detail"][:100]}')
    print()

if symbols_breakout_reached:
    print('='*70)
    print('  SYMBOLS REACHING BREAKOUT GATE')
    print('='*70)
    for sym, detail in symbols_breakout_reached.items():
        print(f'  {sym}: {detail[:120]}')
    print()

if symbols_emitted:
    print('='*70)
    print('  EMITTED SIGNALS')
    print('='*70)
    for s in symbols_emitted:
        print(f'  {s[:120]}')
else:
    print('='*70)
    print('  EMITTED SIGNALS: 0')
    print('='*70)
print()

# Additional: check the bridge/status.json for latest signal data
status_path = 'data/bridge/status.json'
signals_path = 'data/bridge/signals.json'
if os.path.exists(status_path):
    try:
        with open(status_path) as f:
            status = json.load(f)
        print('  BRIDGE STATUS:')
        for k, v in status.items():
            if isinstance(v, (str, int, float, bool)):
                print(f'    {k}: {v}')
        print()
    except:
        pass

if os.path.exists(signals_path):
    try:
        with open(signals_path) as f:
            signals = json.load(f)
        if isinstance(signals, list):
            print(f'  BRIDGE SIGNALS: {len(signals)} entries')
            for sig in signals[-5:]:
                if isinstance(sig, dict):
                    print(f'    {sig.get("symbol", "?")} {sig.get("side", "?")} conf={sig.get("confidence", "?")}')
        elif isinstance(signals, dict):
            print(f'  BRIDGE SIGNALS: {len(signals)} keys')
        print()
    except:
        pass

# Check DB for emitted signals
db_path = 'data/database/deltaterminal.db'
if os.path.exists(db_path):
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        # Check if signals table exists
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signals'")
        if c.fetchone():
            c.execute('SELECT COUNT(*) FROM signals')
            total = c.fetchone()[0]
            print(f'  DATABASE SIGNALS: {total:,}')
            
            # Recent signals
            c.execute('SELECT * FROM signals ORDER BY rowid DESC LIMIT 5')
            cols = [d[0] for d in c.description]
            rows = c.fetchall()
            if rows:
                print(f'  RECENT SIGNALS (last 5):')
                for row in rows:
                    d = dict(zip(cols, row))
                    print(f'    {d.get("symbol", "?")} {d.get("side", "?")} conf={d.get("confidence", "?")} ts={d.get("timestamp", "?")}')
            else:
                print(f'  NO SIGNALS IN DATABASE')
        else:
            print(f'  NO SIGNALS TABLE')
        conn.close()
    except Exception as e:
        print(f'  DB ERROR: {e}')

print()
print('='*70)
print('  FINAL VERDICT')
print('='*70)
print()
print(f'  SESSION:        {session} (PF={spf["pf"]}, {spf["expectancy"]})')
print(f'  ENGINE:         {"RUNNING" if engine_running else "DOWN"}')
print(f'  SIGNALS:        {emitted} emitted')
print(f'  BREAKOUT:       {breakout_reached} passed, {breakout_blocked} blocked')
print(f'  CHECKLIST:      {checklist_passed} passed, {checklist_blocked} blocked')
print(f'  SESSION BLOCK:  {session_blocked_count} blocked by {session} session PF filter')
print()

if not engine_running:
    print('  ⚠️  ENGINE IS NOT RUNNING')
    print('  No live signals can be emitted without the engine.')
elif session == 'ASIA':
    print(f'  ⚠️  ASIA SESSION (PF=0.54) — Signal emission seasonally gated')
    print(f'  Session filter blocks ALL signals when session PF < 1.0')
    print(f'  London session (PF=1.40) begins at 07:00 UTC')
elif session == 'LONDON':
    print(f'  ✅ LONDON SESSION (PF=1.40) — Session filter SHOULD pass')
else:
    print(f'  ⚠️  {session} SESSION (PF={spf["pf"]}) — Expectancy: {spf["expectancy"]}')
print()
