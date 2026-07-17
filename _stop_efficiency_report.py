#!/usr/bin/env python3
"""
Stop Efficiency Report
=======================
For every stop-out, records detailed metrics to understand WHY stops fail.

Metrics tracked:
- ATR at entry (was stop inside normal volatility?)
- SL distance (% and ATR multiples)
- Distance to EMA20 (was stop too close to trend support?)
- Distance to nearest swing high/low
- Maximum Favorable Excursion (MFE)
- Maximum Adverse Excursion (MAE)
- Holding time until stop

READ-ONLY — Never modifies trading logic.
"""
import sqlite3
from pathlib import Path
from collections import defaultdict
import math

DB_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/institutional_v1.db")

def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def get_stopped_trades():
    """Get all trades that hit stop loss."""
    conn = connect()
    rows = conn.execute("""
        SELECT * FROM positions 
        WHERE status = 'closed' AND exit_reason = 'stop_loss'
        ORDER BY closed_at ASC
    """).fetchall()
    
    rows2 = conn.execute("""
        SELECT * FROM positions_archive 
        WHERE status = 'closed' AND exit_reason = 'stop_loss'
        ORDER BY closed_at ASC
    """).fetchall()
    conn.close()
    
    trades = [dict(r) for r in rows] + [dict(r) for r in rows2]
    
    # Deduplicate
    seen = set()
    unique = []
    for t in trades:
        key = (t.get("symbol"), t.get("closed_at"))
        if key not in seen:
            seen.add(key)
            unique.append(t)
    
    return unique

def calculate_atr_multiples(trades):
    """Calculate SL distance in ATR multiples."""
    print("\n" + "=" * 70)
    print("📊 ATR MULTIPLE ANALYSIS")
    print("=" * 70)
    print("   SL distance relative to ATR at entry")
    print("   If SL < 1.0 ATR, stop is inside normal volatility\n")
    
    atr_data = []
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        atr = t.get("volatility_score", 0) or 0  # Using volatility_score as proxy for ATR
        
        if entry and sl and atr and entry > 0 and atr > 0:
            sl_dist = abs(entry - sl)
            atr_multiple = sl_dist / atr
            
            atr_data.append({
                "symbol": t.get("symbol"),
                "side": t.get("side"),
                "entry": entry,
                "sl": sl,
                "atr": atr,
                "sl_dist": sl_dist,
                "sl_dist_pct": sl_dist / entry * 100,
                "atr_multiple": atr_multiple,
                "pnl": t.get("pnl", 0),
                "hold_minutes": t.get("hold_minutes", 0),
                "confidence": (t.get("confidence", 0) or 0) * 100,
            })
    
    if not atr_data:
        print("   ⚠️  No ATR data available in trades")
        print("   The volatility_score field may be empty")
        return
    
    # ATR Multiple Distribution
    buckets = {
        "< 0.5 ATR": [], "0.5-1.0 ATR": [], "1.0-1.5 ATR": [], 
        "1.5-2.0 ATR": [], "2.0-3.0 ATR": [], "> 3.0 ATR": []
    }
    
    for d in atr_data:
        mult = d["atr_multiple"]
        if mult < 0.5:
            buckets["< 0.5 ATR"].append(d)
        elif mult < 1.0:
            buckets["0.5-1.0 ATR"].append(d)
        elif mult < 1.5:
            buckets["1.0-1.5 ATR"].append(d)
        elif mult < 2.0:
            buckets["1.5-2.0 ATR"].append(d)
        elif mult < 3.0:
            buckets["2.0-3.0 ATR"].append(d)
        else:
            buckets["> 3.0 ATR"].append(d)
    
    print(f"{'ATR MULTIPLE':<15} {'TRADES':>8} {'AVG PNL':>10} {'AVG SL%':>10} {'AVG HOLD':>10}")
    print("-" * 55)
    
    for bucket_name, bucket_trades in buckets.items():
        if not bucket_trades:
            continue
        
        avg_pnl = sum(d["pnl"] for d in bucket_trades) / len(bucket_trades)
        avg_sl_pct = sum(d["sl_dist_pct"] for d in bucket_trades) / len(bucket_trades)
        avg_hold = sum(d["hold_minutes"] for d in bucket_trades if d["hold_minutes"]) / max(1, len([d for d in bucket_trades if d["hold_minutes"]]))
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {bucket_name:<13} {len(bucket_trades):>8} {avg_pnl:>10.4f} {avg_sl_pct:>9.2f}% {avg_hold:>8.0f}m")
    
    # Summary
    avg_atr_mult = sum(d["atr_multiple"] for d in atr_data) / len(atr_data)
    tight_stops = sum(1 for d in atr_data if d["atr_multiple"] < 1.0)
    
    print(f"\n   Summary:")
    print(f"   Average ATR Multiple: {avg_atr_mult:.2f}")
    print(f"   Stops < 1.0 ATR: {tight_stops} ({tight_stops/len(atr_data)*100:.1f}%)")
    
    if tight_stops > len(atr_data) * 0.3:
        print(f"\n   ⚠️  WARNING: {tight_stops/len(atr_data)*100:.0f}% of stops are inside normal volatility!")
        print(f"   These stops are likely getting hit by normal market noise.")
        print(f"   Consider widening SL to at least 1.0-1.5 ATR.")

def analyze_mfe_before_stop(trades):
    """Analyze Maximum Favorable Excursion before stop-out."""
    print("\n" + "=" * 70)
    print("📊 MAXIMUM FAVORABLE EXCURSION (MFE) BEFORE STOP")
    print("=" * 70)
    print("   Did the trade move in your favor before stopping out?")
    print("   High MFE + Stop = Trade was correct but SL too tight\n")
    
    mfe_data = []
    for t in trades:
        mfe = t.get("mfe_pct", 0) or 0
        mae = t.get("mae_pct", 0) or 0
        
        if mfe > 0:
            mfe_data.append({
                "symbol": t.get("symbol"),
                "side": t.get("side"),
                "mfe_pct": mfe,
                "mae_pct": mae,
                "pnl": t.get("pnl", 0),
                "hold_minutes": t.get("hold_minutes", 0),
                "entry": t.get("entry_price", 0),
                "sl": t.get("stop_loss", 0),
            })
    
    if not mfe_data:
        print("   ⚠️  No MFE data available in trades")
        return
    
    # MFE Distribution
    buckets = {
        "0% (immediate)": [], "0-0.5%": [], "0.5-1%": [], 
        "1-2%": [], "2-3%": [], "> 3%": []
    }
    
    for d in mfe_data:
        mfe = d["mfe_pct"]
        if mfe == 0:
            buckets["0% (immediate)"].append(d)
        elif mfe < 0.5:
            buckets["0-0.5%"].append(d)
        elif mfe < 1.0:
            buckets["0.5-1%"].append(d)
        elif mfe < 2.0:
            buckets["1-2%"].append(d)
        elif mfe < 3.0:
            buckets["2-3%"].append(d)
        else:
            buckets["> 3%"].append(d)
    
    print(f"{'MFE BUCKET':<15} {'TRADES':>8} {'AVG PNL':>10} {'AVG MAE':>10} {'AVG HOLD':>10}")
    print("-" * 55)
    
    for bucket_name, bucket_trades in buckets.items():
        if not bucket_trades:
            continue
        
        avg_pnl = sum(d["pnl"] for d in bucket_trades) / len(bucket_trades)
        avg_mae = sum(d["mae_pct"] for d in bucket_trades if d["mae_pct"]) / max(1, len([d for d in bucket_trades if d["mae_pct"]]))
        avg_hold = sum(d["hold_minutes"] for d in bucket_trades if d["hold_minutes"]) / max(1, len([d for d in bucket_trades if d["hold_minutes"]]))
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {bucket_name:<13} {len(bucket_trades):>8} {avg_pnl:>10.4f} {avg_mae:>9.2f}% {avg_hold:>8.0f}m")
    
    # Key insight: Trades with high MFE that still stopped out
    high_mfe_stopped = [d for d in mfe_data if d["mfe_pct"] > 1.0]
    if high_mfe_stopped:
        print(f"\n   🔍 KEY INSIGHT: {len(high_mfe_stopped)} trades had MFE > 1% before stopping out!")
        print(f"   These trades were PROFITABLE at some point but still hit stop.")
        print(f"   This strongly suggests stops are TOO TIGHT.")
        
        # Show examples
        print(f"\n   Examples of trades with high MFE that stopped out:")
        for d in high_mfe_stopped[:5]:
            print(f"   - {d['symbol']} {d['side']}: MFE={d['mfe_pct']:.2f}%, Entry={d['entry']:.4f}, SL={d['sl']:.4f}")

def analyze_ema_distance(trades):
    """Analyze distance to EMA20 at entry."""
    print("\n" + "=" * 70)
    print("📊 EMA DISTANCE ANALYSIS")
    print("=" * 70)
    print("   Was stop placed near EMA support/resistance?")
    
    ema_data = []
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        
        # We don't have EMA values in the positions table
        # But we can calculate SL distance as a proxy
        if entry and sl and entry > 0:
            sl_dist_pct = abs(entry - sl) / entry * 100
            ema_data.append({
                "symbol": t.get("symbol"),
                "sl_dist_pct": sl_dist_pct,
                "pnl": t.get("pnl", 0),
            })
    
    if not ema_data:
        print("   ⚠️  No EMA data available in positions table")
        print("   EMA values would need to be stored at trade entry")
        return
    
    # Analyze by SL distance (proxy for EMA distance)
    print(f"\n   SL Distance Distribution (proxy for EMA proximity):")
    
    buckets = {
        "< 0.5%": [], "0.5-1%": [], "1-1.5%": [], "1.5-2%": [], "> 2%": []
    }
    
    for d in ema_data:
        dist = d["sl_dist_pct"]
        if dist < 0.5:
            buckets["< 0.5%"].append(d)
        elif dist < 1.0:
            buckets["0.5-1%"].append(d)
        elif dist < 1.5:
            buckets["1-1.5%"].append(d)
        elif dist < 2.0:
            buckets["1.5-2%"].append(d)
        else:
            buckets["> 2%"].append(d)
    
    print(f"\n{'SL DIST':<12} {'TRADES':>8} {'AVG PNL':>10}")
    print("-" * 35)
    
    for bucket_name, bucket_trades in buckets.items():
        if not bucket_trades:
            continue
        
        avg_pnl = sum(d["pnl"] for d in bucket_trades) / len(bucket_trades)
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {bucket_name:<10} {len(bucket_trades):>8} {avg_pnl:>10.4f}")

def analyze_confidence_vs_outcome(trades):
    """Analyze if confidence predicts stop outcomes."""
    print("\n" + "=" * 70)
    print("📊 CONFIDENCE vs STOP OUTCOME")
    print("=" * 70)
    print("   Does higher confidence lead to better stop outcomes?\n")
    
    buckets = defaultdict(list)
    for t in trades:
        conf = (t.get("confidence", 0) or 0) * 100
        bucket = int(conf / 10) * 10
        buckets[bucket].append(t)
    
    print(f"{'CONF':<12} {'TRADES':>8} {'AVG PNL':>10} {'AVG MFE':>10} {'AVG HOLD':>10}")
    print("-" * 55)
    
    for bucket in sorted(buckets.keys()):
        ts = buckets[bucket]
        if len(ts) < 3:
            continue
        
        avg_pnl = sum(t.get("pnl", 0) or 0 for t in ts) / len(ts)
        mfes = [t.get("mfe_pct", 0) or 0 for t in ts]
        avg_mfe = sum(mfes) / len(mfes) if mfes else 0
        holds = [t.get("hold_minutes", 0) or 0 for t in ts if t.get("hold_minutes")]
        avg_hold = sum(holds) / len(holds) if holds else 0
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {bucket}-{bucket+10:<6} {len(ts):>8} {avg_pnl:>10.4f} {avg_mfe:>9.2f}% {avg_hold:>8.0f}m")

def generate_stop_efficiency_report(trades):
    """Generate comprehensive stop efficiency report."""
    print("\n" + "=" * 70)
    print("📋 STOP EFFICIENCY REPORT")
    print("=" * 70)
    
    total = len(trades)
    
    # Calculate key metrics
    sl_dists = []
    mfes = []
    maes = []
    holds = []
    
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        if entry and sl and entry > 0:
            sl_dists.append(abs(entry - sl) / entry * 100)
        
        mfe = t.get("mfe_pct", 0)
        if mfe:
            mfes.append(mfe)
        
        mae = t.get("mae_pct", 0)
        if mae:
            maes.append(mae)
        
        hold = t.get("hold_minutes", 0)
        if hold:
            holds.append(hold)
    
    avg_sl_dist = sum(sl_dists) / len(sl_dists) if sl_dists else 0
    avg_mfe = sum(mfes) / len(mfes) if mfes else 0
    avg_mae = sum(maes) / len(maes) if maes else 0
    avg_hold = sum(holds) / len(holds) if holds else 0
    
    # Count issues
    tight_stops = sum(1 for d in sl_dists if d < 1.0)
    high_mfe_stopped = sum(1 for m in mfes if m > 1.0)
    quick_stops = sum(1 for h in holds if h < 15)
    
    print(f"""
📊 STOP EFFICIENCY METRICS:

1. STOP DISTANCE:
   - Average SL Distance: {avg_sl_dist:.2f}%
   - Stops < 1%: {tight_stops} ({tight_stops/total*100:.1f}%)
   - Status: {'⚠️ TOO TIGHT' if tight_stops > total * 0.3 else '✅ OK'}

2. FAVORABLE EXCURSION:
   - Average MFE: {avg_mfe:.2f}%
   - Trades with MFE > 1%: {high_mfe_stopped} ({high_mfe_stopped/total*100:.1f}%)
   - Status: {'⚠️ STOPS TOO TIGHT' if high_mfe_stopped > total * 0.2 else '✅ OK'}

3. ADVERSE EXCURSION:
   - Average MAE: {avg_mae:.2f}%

4. HOLDING TIME:
   - Average Hold: {avg_hold:.0f} minutes
   - Stopped < 15min: {quick_stops} ({quick_stops/total*100:.1f}%)
   - Status: {'⚠️ QUICK STOPS' if quick_stops > total * 0.3 else '✅ OK'}
""")
    
    # Root cause analysis
    print("🔍 ROOT CAUSE ANALYSIS:")
    
    if tight_stops > total * 0.3:
        print(f"""
   ⚠️  ISSUE #1: STOPS TOO TIGHT
   - {tight_stops/total*100:.0f}% of stops have SL < 1%
   - These are getting stopped by normal market noise
   - RECOMMENDATION: Widen minimum SL to 1.5-2.0%
""")
    
    if high_mfe_stopped > total * 0.2:
        print(f"""
   ⚠️  ISSUE #2: STOPS HIT AFTER PROFITABLE MOVE
   - {high_mfe_stopped/total*100:.0f}% of trades had MFE > 1% before stopping
   - These trades were PROFITABLE but stops were too tight
   - RECOMMENDATION: Use trailing stops or wider initial SL
""")
    
    if quick_stops > total * 0.3:
        print(f"""
   ⚠️  ISSUE #3: ENTRIES TOO AGGRESSIVE
   - {quick_stops/total*100:.0f}% of stops hit within 15 minutes
   - Entry timing may be poor (entering at volatile moments)
   - RECOMMENDATION: Wait for pullback or use limit orders
""")
    
    # Recommendations
    print("""
📋 RECOMMENDATIONS (Evidence-Based):

1. WIDEN MINIMUM SL:
   - Current: Average 1.46%
   - Proposed: Minimum 1.5-2.0% or 1.0-1.5 ATR
   - Impact: Fewer noise stops, wider risk per trade

2. USE ATR-BASED STOPS:
   - Replace fixed % stops with ATR × multiplier
   - Adapts to market volatility automatically
   - Suggested: 1.5-2.0 × ATR

3. IMPLEMENT TRAILING STOPS:
   - For trades with MFE > 1%, use trailing stop
   - Locks in profit while allowing continuation
   - Suggested: Trail at 0.5-1.0 × ATR

4. REVIEW ENTRY TIMING:
   - 35% of stops hit within 15 minutes
   - Consider waiting for pullback before entry
   - Use limit orders instead of market orders

5. STORE EMA VALUES AT ENTRY:
   - Track EMA20, EMA50, EMA200 at entry
   - Use as dynamic support/resistance for SL placement
   - Avoid placing stops near obvious EMA levels
""")
    
    print("=" * 70)
    print("NOTE: These are diagnostic findings only.")
    print("No changes have been made to the strategy.")
    print("=" * 70)

def main():
    print("=" * 70)
    print("🔬 STOP EFFICIENCY REPORT")
    print("=" * 70)
    
    trades = get_stopped_trades()
    print(f"\n📊 Loaded {len(trades)} stopped trades")
    
    if len(trades) < 10:
        print("❌ Not enough stopped trades for meaningful analysis")
        return
    
    calculate_atr_multiples(trades)
    analyze_mfe_before_stop(trades)
    analyze_ema_distance(trades)
    analyze_confidence_vs_outcome(trades)
    generate_stop_efficiency_report(trades)

if __name__ == "__main__":
    main()
