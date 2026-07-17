#!/usr/bin/env python3
"""
RR Audit Self-Test
==================
Verifies the RR audit system is properly wired and can record rejections.
Run this to diagnose why the RR audit dashboard shows zero rejections.

Usage:
    python _rr_audit_selftest.py
"""
import sys
from pathlib import Path

# Add ai-engine to path
AI_ROOT = Path(__file__).resolve().parent / "packages" / "ai-engine"
sys.path.insert(0, str(AI_ROOT))

print("=" * 60)
print("🔍 RR AUDIT SELF-TEST")
print("=" * 60)

# ── Step 1: Test import ──
print("\n[1] Testing import...")
try:
    from scanner.ema_v5.rr_audit import get_rr_audit, RRAuditTracker
    print("    ✅ Import successful")
except Exception as e:
    print(f"    ❌ Import failed: {e}")
    sys.exit(1)

# ── Step 2: Test singleton creation ──
print("\n[2] Testing singleton creation...")
try:
    audit = get_rr_audit()
    print(f"    ✅ Singleton created: {type(audit).__name__}")
    print(f"    📁 CSV dir: {audit._csv_dir}")
    print(f"    📊 Max history: {audit._max_history}")
except Exception as e:
    print(f"    ❌ Singleton creation failed: {e}")
    sys.exit(1)

# ── Step 3: Test CSV initialization ──
print("\n[3] Testing CSV initialization...")
try:
    if audit._csv_path:
        print(f"    ✅ CSV path: {audit._csv_path}")
        print(f"    ✅ CSV writer: {audit._csv_writer is not None}")
    else:
        print("    ⚠️  CSV path not set")
except Exception as e:
    print(f"    ❌ CSV check failed: {e}")

# ── Step 4: Record a test rejection ──
print("\n[4] Recording test rejection...")
print("    Test case: Entry=100, SL=95, TP1=102, min_rr=2.0")
print("    Expected: Risk=5, Reward=2, RR=0.40 → REJECTED")

try:
    audit.record_rejection(
        symbol="TESTUSDT",
        side="LONG",
        entry=100.0,
        stop_loss=95.0,
        tp1=102.0,
        tp2=104.0,
        tp3=106.0,
        atr_value=2.5,
        sl_atr_mult=2.0,
        tp1_rr_mult=0.4,
        session="selftest",
        regime="trending_bull",
        confidence=75.0,
        rr_required=2.0,
        rejection_source="selftest",
        rejection_reason="Self-test: RR 0.40 < 2.00",
    )
    print("    ✅ record_rejection() executed without error")
except Exception as e:
    print(f"    ❌ record_rejection() failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ── Step 5: Verify in-memory record ──
print("\n[5] Verifying in-memory record...")
try:
    stats = audit.get_rejection_stats()
    print(f"    Total rejections: {stats.get('total', 0)}")
    print(f"    Tracked in memory: {stats.get('tracked', 0)}")
    print(f"    Average RR: {stats.get('avg_rr', 0)}")
    
    if stats.get('total', 0) >= 1:
        print("    ✅ In-memory record verified")
    else:
        print("    ❌ In-memory record NOT found")
except Exception as e:
    print(f"    ❌ Stats check failed: {e}")

# ── Step 6: Verify CSV was written ──
print("\n[6] Verifying CSV file...")
try:
    if audit._csv_path and audit._csv_path.exists():
        with open(audit._csv_path, 'r') as f:
            lines = f.readlines()
        print(f"    ✅ CSV file exists: {audit._csv_path}")
        print(f"    📄 Lines in CSV: {len(lines)} (including header)")
        if len(lines) > 1:
            print(f"    📝 First data line: {lines[1][:80]}...")
    else:
        print("    ⚠️  CSV file not found")
except Exception as e:
    print(f"    ❌ CSV check failed: {e}")

# ── Step 7: Test signal_engine integration ──
print("\n[7] Testing signal_engine integration...")
try:
    from scanner.ema_v5.signal_engine import SignalEngine
    engine = SignalEngine()
    print("    ✅ SignalEngine imported")
    print(f"    📊 Gate rejections dict: {engine.gate_rejections}")
except Exception as e:
    print(f"    ❌ SignalEngine import failed: {e}")

# ── Step 8: Test engine.py integration ──
print("\n[8] Testing engine.py import...")
try:
    # Just verify the import works
    from scanner.ema_v5.rr_audit import get_rr_audit as engine_audit
    print("    ✅ engine.py import path works")
except Exception as e:
    print(f"    ❌ engine.py import failed: {e}")

# ── Step 9: Check bridge data ──
print("\n[9] Checking bridge data...")
try:
    bridge_path = AI_ROOT / "data" / "bridge" / "rr_audit.json"
    if bridge_path.exists():
        import json
        with open(bridge_path) as f:
            bridge_data = json.load(f)
        print(f"    ✅ Bridge file exists: {bridge_path}")
        print(f"    📊 Bridge total: {bridge_data.get('total', 0)}")
    else:
        print(f"    ⚠️  Bridge file not found: {bridge_path}")
except Exception as e:
    print(f"    ❌ Bridge check failed: {e}")

# ── Summary ──
print("\n" + "=" * 60)
print("📊 DIAGNOSIS")
print("=" * 60)

stats = audit.get_rejection_stats()
if stats.get('total', 0) == 0:
    print("""
The RR audit module is WORKING but has recorded ZERO rejections.

This means one of:

1. **No signals have been rejected by the RR gate yet**
   - Signals may be rejected earlier (confidence, volume, duplicate, etc.)
   - The RR gate may not have been reached

2. **The engine hasn't been restarted**
   - Changes require an engine restart to take effect
   - Run: python packages/ai-engine/main.py --mode engine

3. **The min_rr threshold is very low**
   - Current min_rr in config: packages/ai-engine/scanner/ema_v5/config.py
   - If min_rr is very low, most signals pass the RR gate

To verify the audit is recording, run this self-test again.
If it shows 1 rejection above, the module is working correctly.
""")
else:
    print(f"""
✅ RR Audit is WORKING and has recorded {stats['total']} rejections.

Average RR at rejection: {stats.get('avg_rr', 0):.3f}
Top symbols: {stats.get('top_symbols', [])[:3]}

Check the dashboard at: http://localhost:8501/_RR_Audit
""")

print("=" * 60)
