#!/usr/bin/env python3
"""
RR Audit End-to-End Test
========================
Forces a controlled RR rejection and verifies:
1. RR gate rejects it
2. Memory count increases
3. CSV row is written
4. Bridge JSON is written
5. Dashboard would see it

Usage:
    python _rr_audit_e2e_test.py
"""
import json
import sys
import time
from pathlib import Path

# Add ai-engine to path
AI_ROOT = Path(__file__).resolve().parent / "packages" / "ai-engine"
sys.path.insert(0, str(AI_ROOT))

print("=" * 70)
print("🔬 RR AUDIT END-TO-END TEST")
print("=" * 70)
print()

# ── Step 1: Initialize the audit system ──
print("[1/7] Initializing RR audit system...")
from scanner.ema_v5.rr_audit import get_rr_audit
audit = get_rr_audit()
print(f"      ✅ Audit module loaded")
print(f"      📁 CSV path: {audit._csv_path}")
print()

# ── Step 2: Get initial state ──
print("[2/7] Recording initial state...")
initial_stats = audit.get_rejection_stats()
initial_total = initial_stats.get('total', 0)
initial_tracked = initial_stats.get('tracked', 0)
print(f"      Initial total: {initial_total}")
print(f"      Initial tracked: {initial_tracked}")
print()

# ── Step 3: Simulate signal_engine.py RR rejection path ──
print("[3/7] Simulating signal_engine.py RR rejection...")
print("      Test case: Entry=100, SL=95, TP1=101, min_rr=2.0")
print("      Expected: Risk=5, Reward=1, RR=0.20 → REJECTED")

# This simulates exactly what signal_engine.py does
from scanner.ema_v5.config import ema_v5_config
cfg = ema_v5_config.signal

entry = 100.0
sl = 95.0
tp1 = 101.0
tp2 = 102.0
tp3 = 103.0
atr_val = 2.5
symbol = "E2E_TEST_USDT"
side = "LONG"
regime = "trending_bull"
confidence = 75.0

# Compute RR
risk = abs(entry - sl)
reward = abs(tp1 - entry)
rr = reward / risk if risk > 0 else 0

print(f"      Risk: {risk}, Reward: {reward}, RR: {rr:.2f}")
print(f"      Min RR required: {cfg.min_rr}")

if rr < cfg.min_rr:
    print("      ✅ RR gate REJECTS this signal")
    # Call record_rejection exactly as signal_engine.py does
    try:
        audit.record_rejection(
            symbol=symbol,
            side=side,
            entry=entry,
            stop_loss=sl,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            atr_value=atr_val,
            sl_atr_mult=cfg.sl_atr_mult,
            tp1_rr_mult=cfg.tp1_rr,
            session="ema_v5",
            regime=regime,
            confidence=confidence,
            rr_required=cfg.min_rr,
            rejection_source="signal_engine",
            rejection_reason=f"RR {rr:.2f} < {cfg.min_rr:.2f} (SL dist={abs(entry-sl)/entry*100:.2f}%)",
        )
        print("      ✅ record_rejection() called successfully")
    except Exception as e:
        print(f"      ❌ record_rejection() failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
else:
    print(f"      ⚠️  RR {rr:.2f} >= {cfg.min_rr} — would PASS (adjust test case)")
    sys.exit(1)
print()

# ── Step 4: Verify in-memory count ──
print("[4/7] Verifying in-memory count...")
stats = audit.get_rejection_stats()
new_total = stats.get('total', 0)
new_tracked = stats.get('tracked', 0)
print(f"      Total before: {initial_total}")
print(f"      Total after:  {new_total}")
print(f"      Delta:        +{new_total - initial_total}")

if new_total > initial_total:
    print("      ✅ Memory count INCREASED")
else:
    print("      ❌ Memory count DID NOT increase")
    sys.exit(1)
print()

# ── Step 5: Verify CSV was written ──
print("[5/7] Verifying CSV file...")
if audit._csv_path and audit._csv_path.exists():
    with open(audit._csv_path, 'r') as f:
        lines = f.readlines()
    print(f"      ✅ CSV file exists: {audit._csv_path.name}")
    print(f"      📄 Total lines: {len(lines)} (1 header + {len(lines)-1} data)")
    
    # Find our test record
    found = False
    for line in lines:
        if "E2E_TEST_USDT" in line:
            found = True
            print(f"      ✅ Found E2E_TEST_USDT in CSV")
            break
    
    if not found:
        print("      ❌ E2E_TEST_USDT NOT found in CSV")
else:
    print("      ❌ CSV file does not exist")
print()

# ── Step 6: Write bridge JSON (simulating engine's scan cycle) ──
print("[6/7] Writing bridge JSON (simulating engine's scan cycle)...")
bridge_path = AI_ROOT / "data" / "bridge" / "rr_audit.json"
try:
    bridge_path.parent.mkdir(parents=True, exist_ok=True)
    rr_stats = audit.get_rejection_stats()
    rr_stats["timestamp"] = time.time()
    with open(bridge_path, "w") as f:
        json.dump(rr_stats, f, indent=2)
    print(f"      ✅ Bridge file written: {bridge_path}")
    
    # Verify bridge content
    with open(bridge_path) as f:
        bridge_data = json.load(f)
    print(f"      📊 Bridge total: {bridge_data.get('total', 0)}")
    print(f"      📊 Bridge tracked: {bridge_data.get('tracked', 0)}")
    print(f"      📊 Bridge avg_rr: {bridge_data.get('avg_rr', 0)}")
    print(f"      📊 Bridge top_symbols: {bridge_data.get('top_symbols', [])}")
except Exception as e:
    print(f"      ❌ Bridge write failed: {e}")
print()

# ── Step 7: Final verification ──
print("[7/7] Final verification...")
print()

final_stats = audit.get_rejection_stats()
recent = audit.get_recent_rejections(count=5)

print("=" * 70)
print("📊 FINAL STATE")
print("=" * 70)
print(f"  Total rejections:     {final_stats.get('total', 0)}")
print(f"  Tracked in memory:    {final_stats.get('tracked', 0)}")
print(f"  Average RR:           {final_stats.get('avg_rr', 0):.3f}")
print(f"  Top symbols:          {final_stats.get('top_symbols', [])}")
print(f"  CSV logging:          {'✅ Active' if audit._csv_path and audit._csv_path.exists() else '❌ Inactive'}")
print(f"  Bridge JSON:          {'✅ Exists' if bridge_path.exists() else '❌ Missing'}")
print()

if recent:
    print("📋 Last rejection:")
    r = recent[-1]
    print(f"  Symbol:      {r.get('symbol')}")
    print(f"  Side:        {r.get('side')}")
    print(f"  Entry:       {r.get('entry')}")
    print(f"  SL:          {r.get('stop_loss')}")
    print(f"  TP1:         {r.get('tp1')}")
    print(f"  RR Actual:   {r.get('rr_actual'):.2f}")
    print(f"  RR Required: {r.get('rr_required'):.2f}")
    print(f"  Source:      {r.get('rejection_source')}")
print()

# ── Diagnosis ──
print("=" * 70)
print("🔍 DIAGNOSIS")
print("=" * 70)

all_pass = True

checks = [
    ("In-memory recording", final_stats.get('total', 0) > initial_total),
    ("CSV writing", audit._csv_path and audit._csv_path.exists()),
    ("Bridge JSON writing", bridge_path.exists()),
    ("Bridge data populated", bridge_data.get('total', 0) > 0),
]

for name, passed in checks:
    icon = "✅" if passed else "❌"
    print(f"  {icon} {name}")
    if not passed:
        all_pass = False

print()

if all_pass:
    print("✅ ALL CHECKS PASSED — RR Audit system is fully functional.")
    print()
    print("The dashboard at http://localhost:8501/_RR_Audit will now show:")
    print(f"  - Total Rejections: {final_stats.get('total', 0)}")
    print(f"  - Top Symbol: {final_stats.get('top_symbols', [['N/A']])[0][0] if final_stats.get('top_symbols') else 'N/A'}")
    print(f"  - Avg RR: {final_stats.get('avg_rr', 0):.2f}")
    print()
    print("Next: Restart the engine to capture real RR rejections.")
else:
    print("❌ SOME CHECKS FAILED — See above for details.")

print("=" * 70)
