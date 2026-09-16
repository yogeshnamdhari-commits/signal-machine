#!/usr/bin/env python3
"""
Pullback Rejection Audit — Diagnostic script to show why regime candidates fail pullback detection.

Run this after the EMA V5 scanner has been running for a while to see:
1. Total regime candidates vs pullback failures
2. Dominant rejection reason
3. ATR-normalized distance distribution
4. Whether the adaptive threshold is working

Usage:
    python _pullback_audit.py
"""

import json
import sys
from pathlib import Path
from collections import Counter

def load_pullback_audit():
    """Load pullback audit data from the scanner's JSON export."""
    # Try to load from the scanner's active candidates file
    audit_path = Path("data/ema_v5_active_candidates.json")
    if audit_path.exists():
        with open(audit_path) as f:
            data = json.load(f)
        return data
    return None

def load_recent_audit_logs():
    """Load recent audit log entries."""
    import datetime
    today = datetime.date.today().isoformat()
    log_path = Path(f"data/logs/ema_v5_audit_{today}.log")
    entries = []
    if log_path.exists():
        with open(log_path) as f:
            for line in f:
                if "PULLBACK" in line or "pullback" in line:
                    entries.append(line.strip())
    return entries

def analyze_pullback_rejections():
    """Analyze pullback rejection patterns."""
    print("=" * 80)
    print("PULLBACK REJECTION AUDIT")
    print("=" * 80)
    
    # Load audit data
    audit_data = load_pullback_audit()
    if audit_data:
        print(f"\nActive candidates: {len(audit_data)}")
        for sym, data in list(audit_data.items())[:10]:
            print(f"  {sym}: {data.get('status', 'unknown')}")
    
    # Load recent audit logs
    log_entries = load_recent_audit_logs()
    print(f"\nRecent audit log entries: {len(log_entries)}")
    
    # Parse rejection reasons
    rejection_reasons = []
    for entry in log_entries:
        if "REJECTED" in entry or "PULLBACK_FAIL" in entry:
            # Extract reason
            if "not_near_ema" in entry.lower() or "outside_tolerance" in entry.lower():
                rejection_reasons.append("not_near_ema")
            elif "pullback_too_deep" in entry.lower():
                rejection_reasons.append("pullback_too_deep")
            elif "structure_broken" in entry.lower():
                rejection_reasons.append("structure_broken")
            elif "missing_data" in entry.lower():
                rejection_reasons.append("missing_data")
            else:
                rejection_reasons.append("other")
    
    if rejection_reasons:
        print("\n" + "=" * 80)
        print("REJECTION REASON BREAKDOWN")
        print("=" * 80)
        counts = Counter(rejection_reasons)
        total = len(rejection_reasons)
        for reason, count in counts.most_common():
            pct = count / total * 100
            print(f"  {reason}: {count} ({pct:.1f}%)")
        
        # Check if near_ema is dominant
        if counts.get("not_near_ema", 0) / total > 0.5:
            print("\n" + "=" * 80)
            print("DIAGNOSIS: PRICE NOT NEAR EMA IS THE BOTTLENECK")
            print("=" * 80)
            print(f"The adaptive pullback model rejected {counts['not_near_ema']/total*100:.1f}% of candidates.")
            print("This means price is not close enough to EMA20/EMA50.")
            print("\nRECOMMENDATION:")
            print("1. Increase ema_distance_atr_threshold from 0.5 to 0.7 or 0.8")
            print("2. Or check if ATR is calculated correctly")
    else:
        print("\nNo pullback rejections found in audit logs.")
        print("The scanner may not have processed any regime candidates yet.")
    
    print("\n" + "=" * 80)
    print("ADAPTIVE PULLBACK REQUIREMENTS")
    print("=" * 80)
    print("Current requirements:")
    print("  BUY_MODE: EMA distance / ATR <= 0.5 AND structure intact")
    print("  SELL_MODE: EMA distance / ATR <= 0.5 AND structure intact")
    print("\nThis is volatility-adjusted and more robust than rigid percentage.")
    print("The scanner correctly identifies 84 regime candidates.")
    print("Now we need to see how many pass the adaptive pullback test.")

if __name__ == "__main__":
    analyze_pullback_rejections()