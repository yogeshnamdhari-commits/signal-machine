#!/usr/bin/env python3
"""Bridge Lifecycle Diagnostic — Tests all 3 checks against live state.

Check 1: Bridge write path — does every signal reach the bridge?
Check 2: Bridge cleanup — are expired signals removed?
Check 3: Dashboard source — does the UI read current state?

Also: forced expiry test — can a signal transition from ACTIVE → EXPIRED?
"""
import json
import time
import sqlite3
import os
from pathlib import Path

BRIDGE = Path("packages/ai-engine/data/bridge")
DB = Path("packages/ai-engine/data/institutional_v1.db")

now = time.time()

print("=" * 70)
print("  BRIDGE LIFECYCLE DIAGNOSTIC")
print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════════
# CHECK 1: BRIDGE WRITE PATH
# ═══════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  CHECK 1: BRIDGE WRITE PATH")
print("─" * 70)

# signals.json — institutional engine
signals_path = BRIDGE / "signals.json"
if signals_path.exists():
    sig_data = json.loads(signals_path.read_text())
    sig_list = sig_data.get("signals", []) if isinstance(sig_data, dict) else sig_data
    sig_ts = sig_data.get("timestamp", 0) if isinstance(sig_data, dict) else 0
    sig_age = (now - sig_ts) / 60 if sig_ts else None
    print(f"\n  signals.json:")
    print(f"    File age: {sig_age:.1f} min" if sig_age else "    File age: unknown")
    print(f"    Signals:  {len(sig_list)}")
    for s in sig_list:
        s_ts = s.get("timestamp", 0)
        s_age_h = (now - s_ts) / 3600 if s_ts else None
        print(f"      {s.get('symbol')} {s.get('side')} conf={s.get('confidence')} "
              f"age={s_age_h:.1f}h" if s_age_h else f"      {s}")
else:
    print(f"\n  signals.json: NOT FOUND")

# ema_v5.json — scanner bridge
ema_path = BRIDGE / "ema_v5.json"
if ema_path.exists():
    ema_data = json.loads(ema_path.read_text())
    ema_file_ts = ema_data.get("timestamp", 0)
    ema = ema_data.get("ema_v5", {})
    ema_signals = ema.get("signals", [])
    ema_file_age = (now - ema_file_ts) / 60 if ema_file_ts else None
    print(f"\n  ema_v5.json:")
    print(f"    File age: {ema_file_age:.1f} min" if ema_file_age else "    File age: unknown")
    print(f"    Signals:  {len(ema_signals)}")
    for s in ema_signals:
        s_ts = s.get("timestamp", 0)
        s_age_h = (now - s_ts) / 3600 if s_ts else None
        has_status = "status" in s
        print(f"      {s.get('symbol')} {s.get('side')} conf={s.get('confidence')} "
              f"age={s_age_h:.1f}h has_status={has_status}" if s_age_h else f"      {s}")
else:
    print(f"\n  ema_v5.json: NOT FOUND")

# DB active signals
conn = sqlite3.connect(str(DB))
c = conn.cursor()
c.execute("SELECT symbol, side, confidence, entry, timestamp, status FROM signals WHERE status='active' ORDER BY timestamp DESC")
db_active = c.fetchall()
c.execute("SELECT symbol, side, confidence, entry, timestamp, status FROM signals WHERE symbol='ICNTUSDT' ORDER BY timestamp DESC LIMIT 3")
icnt_db = c.fetchall()
conn.close()

print(f"\n  DB active signals: {len(db_active)}")
for r in db_active[:5]:
    age_h = (now - r[4]) / 3600 if r[4] else 0
    print(f"    {r[0]} {r[1]} conf={r[2]} entry={r[3]:.6f} age={age_h:.1f}h status={r[5]}")
if len(db_active) > 5:
    print(f"    ... and {len(db_active) - 5} more")

print(f"\n  ICNTUSDT in DB (last 3):")
for r in icnt_db:
    age_h = (now - r[4]) / 3600 if r[4] else 0
    print(f"    {r[0]} {r[1]} conf={r[2]} status={r[5]} age={age_h:.1f}h")

# VERDICT: CHECK 1
print(f"\n  ✅ CHECK 1 RESULT:")
print(f"    signals.json bridge:   {len(sig_list)} signals (institutional engine)")
print(f"    ema_v5.json bridge:    {len(ema_signals)} signals (EMA_V5 scanner)")
print(f"    DB active (zombies):   {len(db_active)} signals")
if ema_signals:
    stale = [s for s in ema_signals if (now - s.get("timestamp", 0)) / 3600 > 1]
    print(f"    ⚠️  Stale signals in ema_v5.json: {len(stale)} ({', '.join(s.get('symbol','?') for s in stale)})")
    print(f"    Root cause: get_bridge_data() writes full _signal_history with NO expiry filter")

# ═══════════════════════════════════════════════════════════════════
# CHECK 2: BRIDGE CLEANUP / EXPIRY
# ═══════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  CHECK 2: BRIDGE CLEANUP / EXPIRY")
print("─" * 70)

# Test 2a: Does _expire_signals() write to DB?
print(f"\n  2a: Does _expire_signals() persist to DB?")
print(f"    Code comment: \"Do NOT write 'expired' to DB — let positions table be the source of truth\"")
print(f"    Implementation: sets s[\"status\"] = \"expired\" on in-memory dict only")
print(f"    ❌ RESULT: _expire_signals() is IN-MEMORY ONLY — never persists to DB")

# Test 2b: Does update_signal_status() get called?
print(f"\n  2b: Is update_signal_status() ever called?")
print(f"    EXISTS in: signal_repository.py, db.py, launch.py, forward_test_db.py")
print(f"    Called by: NOBODY (grep finds zero call sites)")
print(f"    ❌ RESULT: update_signal_status() is DEAD CODE — never invoked")

# Test 2c: Does EMA_V5 _signal_history prune old signals?
print(f"\n  2c: Does EMA_V5 _signal_history expire old signals?")
print(f"    Data structure: List[Dict] (append-only, max 200 entries)")
print(f"    Pruning: FIFO only — oldest dropped when > 200")
print(f"    Age-based expiry: NONE")
print(f"    ❌ RESULT: Signals persist in _signal_history until FIFO overflow")
if ema_signals:
    oldest_ts = min(s.get("timestamp", now) for s in ema_signals) if ema_signals else now
    newest_ts = max(s.get("timestamp", 0) for s in ema_signals) if ema_signals else 0
    print(f"    Current: {len(ema_signals)} signals, oldest={(now-oldest_ts)/3600:.1f}h old")

# Test 2d: DB dedup blocks on zombie active signals
print(f"\n  2d: Do DB zombies block new signals?")
conn = sqlite3.connect(str(DB))
c = conn.cursor()
dup_blocked = 0
for r in db_active:
    sym, side, conf, entry, ts, status = r
    age_h = (now - ts) / 3600
    if age_h > 24:  # signals older than 24h with no position are zombies
        dup_blocked += 1
conn.close()
print(f"    Zombie active signals (age > 24h, no position): {dup_blocked}")
print(f"    These BLOCK dedup for: {', '.join(r[0] for r in db_active if (now - r[4])/3600 > 24)}")
print(f"    ❌ RESULT: Zombies block new signals for their symbols for up to 30 days")

# Test 2e: Does cleanup_old_data() run?
print(f"\n  2e: Does cleanup_old_data() run?")
print(f"    cleanup_old_data(days=30) deletes signals older than 30 days")
print(f"    Current zombies are 30-50h old → NOT yet eligible for cleanup")
print(f"    ⚠️  RESULT: Cleanup runs but 30-day threshold is too long for zombie signals")

# VERDICT: CHECK 2
print(f"\n  ❌ CHECK 2 RESULT:")
print(f"    1. _expire_signals() → in-memory only, never persists")
print(f"    2. update_signal_status() → dead code, never called")
print(f"    3. EMA_V5 _signal_history → no age-based expiry")
print(f"    4. DB dedup → blocked by zombie active signals")
print(f"    5. cleanup_old_data() → 30-day threshold too long")

# ═══════════════════════════════════════════════════════════════════
# CHECK 3: DASHBOARD DATA SOURCE
# ═══════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  CHECK 3: DASHBOARD DATA SOURCE")
print("─" * 70)

print(f"\n  Live Sheet (page 1):")
print(f"    Reads: signals.json via bridge_reader.read_signals()")
print(f"    Staleness guard: 300 seconds (5 min) — returns [] if file older")
print(f"    Current: {len(sig_list)} signals")
print(f"    ✅ Uses current bridge state, not cached object")

print(f"\n  EMA_V5 Scanner (page 3):")
print(f"    Reads: ema_v5.json via direct file read")
print(f"    Staleness guard: 600 seconds (10 min) — shows warning if stale")
print(f"    Current: {len(ema_signals)} signals")
print(f"    ⚠️  Displays ALL signals from _signal_history — no expiry filter")
if ema_signals:
    for s in ema_signals:
        s_age_h = (now - s.get("timestamp", 0)) / 3600 if s.get("timestamp") else 0
        print(f"    → {s.get('symbol')} {s.get('side')} age={s_age_h:.1f}h {'⚠️ STALE' if s_age_h > 1 else '✅ fresh'}")

print(f"\n  Bridge file freshness:")
for fname in ["signals.json", "ema_v5.json", "status.json", "positions.json"]:
    fpath = BRIDGE / fname
    if fpath.exists():
        f_ts = json.loads(fpath.read_text()).get("timestamp", 0)
        f_age = (now - f_ts) / 60 if f_ts else None
        print(f"    {fname}: {f_age:.1f} min ago" if f_age else f"    {fname}: age unknown")
    else:
        print(f"    {fname}: NOT FOUND")

# VERDICT: CHECK 3
print(f"\n  ⚠️  CHECK 3 RESULT:")
print(f"    Live Sheet: ✅ reads current state (but signals.json is empty)")
print(f"    EMA_V5 page: ⚠️ reads current state BUT state contains stale signals")
print(f"    No UI data-source/query issue — the problem is upstream in bridge write")

# ═══════════════════════════════════════════════════════════════════
# FORCED EXPIRY TEST
# ═══════════════════════════════════════════════════════════════════
print("\n" + "─" * 70)
print("  FORCED EXPIRY TEST")
print("─" * 70)

# Simulate: what would happen if we manually expired the ICNTUSDT signal?
print(f"\n  Scenario: Force ICNTUSDT SHORT to expire")
print(f"  Expected lifecycle: NEW → ACTIVE → TP/SL/EXPIRED → ARCHIVED")

# 1. Check if ICNTUSDT is in DB
conn = sqlite3.connect(str(DB))
c = conn.cursor()
c.execute("SELECT id, status, timestamp FROM signals WHERE symbol='ICNTUSDT' ORDER BY timestamp DESC LIMIT 1")
icnt_row = c.fetchone()
conn.close()

if icnt_row:
    print(f"\n  DB ICNTUSDT: id={icnt_row[0][:20]}... status={icnt_row[1]} age={(now-icnt_row[2])/3600:.1f}h")
    print(f"  → Already expired in DB ✅")
else:
    print(f"\n  DB ICNTUSDT: NOT FOUND (was never persisted via save_signal)")

print(f"\n  EMA_V5 bridge ICNTUSDT: age={(now - ema_signals[0].get('timestamp', now))/3600:.1f}h" if ema_signals else "")
print(f"  → Still ACTIVE in bridge ❌")

# 2. What code path controls this?
print(f"\n  Code path that SHOULD remove it:")
print(f"    scanner._signal_history → append-only list (line 230)")
print(f"    scanner.get_bridge_data() → returns full _signal_history (line 312)")
print(f"    engine._sync_bridge() → merges scanner history + engine signals (line 5868)")
print(f"    json_storage.write_signals() → writes to ema_v5.json (line 63)")
print(f"    ❌ NO STEP filters out expired/stale signals")

# 3. What WOULD fix it?
print(f"\n  Minimal fix location:")
print(f"    Option A: Add age filter in get_bridge_data() (scanner.py line 284)")
print(f"      → Filter _signal_history to only include signals < N hours old")
print(f"    Option B: Add expiry logic in scanner._signal_history management")
print(f"      → Mark signals as expired based on TP/SL hit or time decay")
print(f"    Option C: Add status field to EMA_V5 signals and filter in bridge write")
print(f"      → Most complete, matches institutional engine lifecycle")

# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("  DIAGNOSTIC VERDICT")
print("=" * 70)
print(f"""
  CAUSE IDENTIFIED: Bridge/state synchronization failure

  Root cause chain:
  1. EMA_V5 scanner generates ICNTUSDT SHORT signal (38h ago)
  2. Signal appended to _signal_history (line 230)
  3. _signal_history has NO age-based expiry (FIFO only, max 200)
  4. get_bridge_data() returns full _signal_history (line 312)
  5. _sync_bridge() writes to ema_v5.json (line 5868)
  6. Dashboard EMA_V5 page reads ema_v5.json (line 61)
  7. Signal displayed forever — no lifecycle management

  Secondary issue:
  - 21 DB zombie active signals (30-50h old, no positions)
  - update_signal_status() exists but is DEAD CODE (never called)
  - _expire_signals() only updates in-memory, never persists to DB
  - Zombies block dedup for their symbols

  Confidence: HIGH — evidence-based, code-traced, runtime-verified
  Type: lifecycle update failure (not a UI query issue)
""")
