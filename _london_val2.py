#!/usr/bin/env python3
"""London Open Validation — writes to /tmp/validation_report.txt"""
import datetime, sys, os, re, json, sqlite3
from collections import Counter

now = datetime.datetime.now(datetime.timezone.utc)
hour = now.hour
if 0 <= hour < 7: session = 'ASIA'
elif 7 <= hour < 13: session = 'LONDON'  
elif 13 <= hour < 20: session = 'NEW_YORK'
else: session = 'OFF_HOURS'

spf = {'ASIA':0.54, 'LONDON':1.40, 'NEW_YORK':0.83, 'OFF_HOURS':0.14}
spnl = {'ASIA':'-1,791', 'LONDON':'+2,267', 'NEW_YORK':'-3,857', 'OFF_HOURS':'-3,088'}
sexp = {'ASIA':'NEGATIVE', 'LONDON':'PROFITABLE', 'NEW_YORK':'NEGATIVE', 'OFF_HOURS':'NEGATIVE'}

out = open('/tmp/validation_report.txt', 'w')
P = out.write

P('='*70 + '\n')
P('  PRODUCTION LONDON OPEN VALIDATION\n')
P('  Runtime Data Only — No Code Changes\n')
P('='*70 + '\n\n')

P(f'  1. CURRENT SESSION:      {session}\n')
P(f'  2. SESSION PF:           {spf[session]}\n')
P(f'  3. SESSION EXPECTANCY:   {sexp[session]} (PnL=${spnl[session]})\n')
P(f'  TIMESTAMP:               {now.strftime("%Y-%m-%d %H:%M:%S UTC")}\n\n')

# Engine status
import subprocess
r = subprocess.run(['pgrep', '-f', 'engine.py'], capture_output=True, text=True)
running = r.returncode == 0
pid = r.stdout.strip() if running else 'NOT RUNNING'
P(f'  ENGINE STATUS:           {"RUNNING (PID " + pid + ")" if running else "NOT RUNNING"}\n\n')

# Read last 5000 lines of log
log_path = 'data/logs/engine_2026-06-13.log'
if not os.path.exists(log_path):
    yesterday = (now - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    log_path = f'data/logs/engine_{yesterday}.log'

with open(log_path, 'rb') as f:
    f.seek(0, 2)
    fsize = f.tell()
    read_size = min(fsize, 2_000_000)  # Read last 2MB
    f.seek(max(0, fsize - read_size))
    raw = f.read().decode('utf-8', errors='replace')
log_lines = raw.split('\n')

P(f'  LOG FILE: {log_path}\n')
P(f'  LOG SIZE: {fsize/1e6:.1f} MB\n')
P(f'  LINES READ: {len(log_lines):,} (last {read_size/1e6:.1f} MB)\n\n')

# Count all gate outcomes from the last chunk of log
safety_blocked = 0
scorer_rejected = 0
hard_regime_blocked = 0
regime_blocked = 0
session_blocked = 0
session_passed_syms = {}
breakout_reached = 0
breakout_blocked_syms = []
checklist_passed = 0
checklist_blocked = 0
emitted = 0
emitted_details = []
error_count = 0
cycle_count = 0

for line in log_lines:
    if 'CYCLE' in line and ('START' in line or '🔄' in line):
        cycle_count += 1
    if 'SAFETY:' in line and ('BLOCKED' in line or 'DATA_STALE' in line or 'safety' in line.lower()):
        safety_blocked += 1
    elif 'scorer rejected' in line:
        scorer_rejected += 1
    elif 'HARD_REGIME_BLOCKED' in line:
        hard_regime_blocked += 1
    elif 'REGIME_BLOCKED' in line:
        regime_blocked += 1
    elif 'SESSION_BLOCKED' in line or 'SESSION BLOCKED' in line:
        session_blocked += 1
    elif ('session_passed' in line.lower() or 'SESSION PASSED' in line or 'session_filter_passed' in line):
        m = re.search(r'(LONG|SHORT)\s+(\w+)', line)
        if m:
            session_passed_syms[m.group(2)] = {'side': m.group(1), 'line': line.strip()[:150]}
    elif 'BREAKOUT' in line:
        if 'BLOCKED' in line or 'blocked' in line or 'BLOCK' in line:
            breakout_blocked_syms.append(line.strip()[:150])
            breakout_reached += 1  # reached but was blocked
        elif 'PASSED' in line or 'passed' in line or 'CLASS' in line:
            breakout_reached += 1
    elif 'CHECKLIST' in line:
        if 'PASSED' in line or 'passed' in line:
            checklist_passed += 1
        elif 'BLOCKED' in line or 'blocked' in line:
            checklist_blocked += 1
    elif 'signal_emitted' in line.lower() or 'SIGNAL EMITTED' in line or '🚨' in line:
        emitted += 1
        emitted_details.append(line.strip()[:200])
    elif 'EMITTED' in line:
        emitted += 1
        emitted_details.append(line.strip()[:200])
    if 'ERROR' in line and 'Handler error' in line:
        error_count += 1

P('='*70 + '\n')
P('  4-8. FUNNEL SUMMARY\n')
P('='*70 + '\n\n')
P(f'  4. SESSION FILTER PASS COUNT:    {len(session_passed_syms)}\n')
P(f'  5. BREAKOUT PASS COUNT:          {breakout_reached}\n')
P(f'  6. CHECKLIST PASS COUNT:         {checklist_passed}\n')
P(f'  7. GENERATED SIGNALS:            {emitted}\n')
P(f'  8. EMITTED SIGNALS:              {emitted}\n')
P(f'  CYCLE COUNT IN LOG CHUNK:        {cycle_count}\n\n')

P('  GATE BREAKDOWN:\n')
P(f'  {"GATE":<40s} {"COUNT":>10s}\n')
P(f'  {"─"*40} {"─"*10}\n')
P(f'  {"SAFETY (data stale)":<40s} {safety_blocked:>10d}\n')
P(f'  {"SCORER (score < 80)":<40s} {scorer_rejected:>10d}\n')
P(f'  {"HARD_REGIME_BLOCKED":<40s} {hard_regime_blocked:>10d}\n')
P(f'  {"REGIME_BLOCKED (ranging)":<40s} {regime_blocked:>10d}\n')
P(f'  {"SESSION_PASSED":<40s} {len(session_passed_syms):>10d}\n')
P(f'  {"SESSION_BLOCKED":<40s} {session_blocked:>10d}\n')
P(f'  {"BREAKOUT_REACHED (incl blocked)":<40s} {breakout_reached:>10d}\n')
P(f'  {"BREAKOUT_BLOCKED":<40s} {len(breakout_blocked_syms):>10d}\n')
P(f'  {"CHECKLIST_PASSED":<40s} {checklist_passed:>10d}\n')
P(f'  {"CHECKLIST_BLOCKED":<40s} {checklist_blocked:>10d}\n')
P(f'  {"EMITTED":<40s} {emitted:>10d}\n')
P(f'  {"HANDLER ERRORS":<40s} {error_count:>10d}\n')
P('\n')

# Session passed symbols
if session_passed_syms:
    P('='*70 + '\n')
    P('  SYMBOLS PASSING SESSION FILTER\n')
    P('='*70 + '\n\n')
    for sym, info in session_passed_syms.items():
        P(f'  SYMBOL:   {sym}\n')
        P(f'  SIDE:     {info["side"]}\n')
        P(f'  DETAIL:   {info["line"]}\n\n')
else:
    P('  SESSION PASSED SYMBOLS: 0 (none in log chunk)\n\n')

# Breakout details
if breakout_blocked_syms:
    P('='*70 + '\n')
    P('  SYMBOLS REACHING BREAKOUT (BLOCKED)\n')
    P('='*70 + '\n\n')
    for b in breakout_blocked_syms[:20]:
        P(f'  {b}\n')
    P('\n')

# Emitted
if emitted_details:
    P('='*70 + '\n')
    P('  EMITTED SIGNALS\n')
    P('='*70 + '\n\n')
    for e in emitted_details:
        P(f'  {e}\n')
    P('\n')
else:
    P('  EMITTED SIGNALS: 0\n\n')

# DB check
db_path = 'data/database/deltaterminal.db'
if os.path.exists(db_path):
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signals'")
        if c.fetchone():
            c.execute('SELECT COUNT(*) FROM signals')
            total = c.fetchone()[0]
            P(f'  DATABASE: {total:,} signals in deltaterminal.db\n')
            if total > 0:
                c.execute('PRAGMA table_info(signals)')
                cols = [r[1] for r in c.fetchall()]
                c.execute('SELECT * FROM signals ORDER BY rowid DESC LIMIT 3')
                rows = c.fetchall()
                for row in rows:
                    d = dict(zip(cols, row))
                    P(f'    {d.get("symbol","?")} {d.get("side","?")} conf={d.get("confidence","?")} ts={d.get("created_at", d.get("timestamp","?"))}\n')
        else:
            P('  DATABASE: No signals table\n')
        conn.close()
    except Exception as e:
        P(f'  DATABASE ERROR: {e}\n')
P('\n')

# Bridge files
for name in ['status.json', 'signals.json']:
    path = f'data/bridge/{name}'
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            P(f'  BRIDGE {name}:\n')
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, (str, int, float, bool)):
                        P(f'    {k}: {v}\n')
                    elif isinstance(v, list):
                        P(f'    {k}: [{len(v)} items]\n')
            elif isinstance(data, list):
                P(f'    entries: {len(data)}\n')
            P('\n')
        except:
            pass

# FINAL VERDICT
P('='*70 + '\n')
P('  FINAL VERDICT\n')
P('='*70 + '\n\n')
P(f'  SESSION:     {session} (PF={spf[session]}, {sexp[session]})\n')
P(f'  ENGINE:      {"RUNNING" if running else "DOWN — no live processing"}\n')
P(f'  SIGNALS:     {emitted} emitted\n')
P(f'  BREAKOUT:    {breakout_reached} reached gate\n')
P(f'  CHECKLIST:   {checklist_passed} passed\n')
P(f'  SESSION:     {len(session_passed_syms)} passed, {session_blocked} blocked\n\n')

if not running:
    P('  ⚠️  ENGINE IS NOT RUNNING\n')
    P('  No live signals can be emitted. Engine must be restarted.\n')
elif session == 'ASIA':
    P(f'  ⚠️  ASIA SESSION — Seasonal block active (PF=0.54)\n')
    P(f'  Session filter blocks all signals when PF < 1.0\n')
    P(f'  London opens at 07:00 UTC (PF=1.40)\n')
elif session == 'LONDON':
    P(f'  ✅ LONDON SESSION — PF=1.40, filter should pass\n')
    P(f'  Signals CAN be emitted if other gates pass\n')
else:
    P(f'  ⚠️  {session} SESSION — PF={spf[session]}, {sexp[session]}\n')

P('\n')
out.close()
print('DONE')
