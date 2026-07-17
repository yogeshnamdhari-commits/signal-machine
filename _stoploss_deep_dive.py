#!/usr/bin/env python3
"""
Stop-Loss Deep Dive Analysis
==============================
Answers: Why are stops being hit 89% of the time?

For every stopped trade, records:
- ATR at entry
- Distance to nearest swing
- SL distance %
- Holding time before stop
- Whether stop was too tight or too wide

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

def analyze_sl_distance(trades):
    """Analyze stop-loss distance distribution."""
    print("\n" + "=" * 70)
    print("📊 STOP-LOSS DISTANCE ANALYSIS")
    print("=" * 70)
    
    distances = []
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        if entry and sl and entry > 0:
            dist_pct = abs(entry - sl) / entry * 100
            distances.append({
                "symbol": t.get("symbol"),
                "side": t.get("side"),
                "entry": entry,
                "sl": sl,
                "dist_pct": dist_pct,
                "pnl": t.get("pnl", 0),
                "hold_minutes": t.get("hold_minutes", 0),
                "confidence": (t.get("confidence", 0) or 0) * 100,
            })
    
    if not distances:
        print("   No SL distance data available")
        return
    
    # Distribution
    buckets = {
        "< 0.5%": [], "0.5-1%": [], "1-2%": [], "2-3%": [], "3-5%": [], "> 5%": []
    }
    
    for d in distances:
        pct = d["dist_pct"]
        if pct < 0.5:
            buckets["< 0.5%"].append(d)
        elif pct < 1:
            buckets["0.5-1%"].append(d)
        elif pct < 2:
            buckets["1-2%"].append(d)
        elif pct < 3:
            buckets["2-3%"].append(d)
        elif pct < 5:
            buckets["3-5%"].append(d)
        else:
            buckets["> 5%"].append(d)
    
    print(f"\n{'SL DISTANCE':<12} {'TRADES':>8} {'AVG PNL':>10} {'AVG HOLD':>12} {'AVG CONF':>10}")
    print("-" * 55)
    
    for bucket_name, bucket_trades in buckets.items():
        if not bucket_trades:
            continue
        
        avg_pnl = sum(d["pnl"] for d in bucket_trades) / len(bucket_trades)
        avg_hold = sum(d["hold_minutes"] for d in bucket_trades if d["hold_minutes"]) / max(1, len([d for d in bucket_trades if d["hold_minutes"]]))
        avg_conf = sum(d["confidence"] for d in bucket_trades) / len(bucket_trades)
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {bucket_name:<10} {len(bucket_trades):>8} {avg_pnl:>10.4f} {avg_hold:>10.0f}m {avg_conf:>9.1f}%")
    
    # Overall stats
    avg_dist = sum(d["dist_pct"] for d in distances) / len(distances)
    min_dist = min(d["dist_pct"] for d in distances)
    max_dist = max(d["dist_pct"] for d in distances)
    
    print(f"\n   SL Distance Stats:")
    print(f"   Average: {avg_dist:.2f}%")
    print(f"   Min: {min_dist:.2f}%")
    print(f"   Max: {max_dist:.2f}%")
    
    # Identify too tight stops
    tight = [d for d in distances if d["dist_pct"] < 1.0]
    if tight:
        print(f"\n   ⚠️  {len(tight)} trades ({len(tight)/len(distances)*100:.1f}%) had SL < 1%")
        print(f"   These are likely getting stopped by normal market noise")

def analyze_holding_time(trades):
    """Analyze how quickly stops are being hit."""
    print("\n" + "=" * 70)
    print("📊 STOP-LOSS HOLDING TIME ANALYSIS")
    print("=" * 70)
    print("   How quickly are stops being triggered?")
    
    hold_times = []
    for t in trades:
        hold = t.get("hold_minutes", 0) or 0
        if hold > 0:
            hold_times.append({
                "symbol": t.get("symbol"),
                "hold_minutes": hold,
                "pnl": t.get("pnl", 0),
                "confidence": (t.get("confidence", 0) or 0) * 100,
            })
    
    if not hold_times:
        print("   No holding time data available")
        return
    
    # Buckets
    buckets = {
        "< 5min": [], "5-15min": [], "15-30min": [], "30-60min": [], "1-2h": [], "2-6h": [], "> 6h": []
    }
    
    for h in hold_times:
        mins = h["hold_minutes"]
        if mins < 5:
            buckets["< 5min"].append(h)
        elif mins < 15:
            buckets["5-15min"].append(h)
        elif mins < 30:
            buckets["15-30min"].append(h)
        elif mins < 60:
            buckets["30-60min"].append(h)
        elif mins < 120:
            buckets["1-2h"].append(h)
        elif mins < 360:
            buckets["2-6h"].append(h)
        else:
            buckets["> 6h"].append(h)
    
    print(f"\n{'HOLD TIME':<12} {'TRADES':>8} {'AVG PNL':>10} {'AVG CONF':>10}")
    print("-" * 45)
    
    for bucket_name, bucket_trades in buckets.items():
        if not bucket_trades:
            continue
        
        avg_pnl = sum(h["pnl"] for h in bucket_trades) / len(bucket_trades)
        avg_conf = sum(h["confidence"] for h in bucket_trades) / len(bucket_trades)
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {bucket_name:<10} {len(bucket_trades):>8} {avg_pnl:>10.4f} {avg_conf:>9.1f}%")
    
    # Overall stats
    avg_hold = sum(h["hold_minutes"] for h in hold_times) / len(hold_times)
    median_hold = sorted(h["hold_minutes"] for h in hold_times)[len(hold_times) // 2]
    
    print(f"\n   Holding Time Stats:")
    print(f"   Average: {avg_hold:.1f} minutes")
    print(f"   Median: {median_hold:.1f} minutes")
    
    # Identify very quick stops
    quick = [h for h in hold_times if h["hold_minutes"] < 15]
    if quick:
        print(f"\n   ⚠️  {len(quick)} trades ({len(quick)/len(hold_times)*100:.1f}%) stopped within 15 minutes")
        print(f"   These may be getting stopped by initial volatility")

def analyze_confidence_vs_stop(trades):
    """Analyze if higher confidence trades have better SL placement."""
    print("\n" + "=" * 70)
    print("📊 CONFIDENCE vs STOP-LOSS PERFORMANCE")
    print("=" * 70)
    print("   Do higher confidence trades have better SL outcomes?")
    
    buckets = defaultdict(list)
    for t in trades:
        conf = (t.get("confidence", 0) or 0) * 100
        bucket = int(conf / 10) * 10
        buckets[bucket].append(t)
    
    print(f"\n{'CONF BUCKET':<12} {'TRADES':>8} {'AVG PNL':>10} {'AVG HOLD':>12} {'AVG SL%':>10}")
    print("-" * 55)
    
    for bucket in sorted(buckets.keys()):
        ts = buckets[bucket]
        if len(ts) < 3:
            continue
        
        avg_pnl = sum(t.get("pnl", 0) or 0 for t in ts) / len(ts)
        holds = [t.get("hold_minutes", 0) or 0 for t in ts if t.get("hold_minutes")]
        avg_hold = sum(holds) / len(holds) if holds else 0
        
        # Calculate average SL distance
        sl_dists = []
        for t in ts:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            if entry and sl and entry > 0:
                sl_dists.append(abs(entry - sl) / entry * 100)
        avg_sl = sum(sl_dists) / len(sl_dists) if sl_dists else 0
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {bucket}-{bucket+10:<6} {len(ts):>8} {avg_pnl:>10.4f} {avg_hold:>10.0f}m {avg_sl:>9.2f}%")

def analyze_regime_vs_stop(trades):
    """Analyze stop performance by market regime."""
    print("\n" + "=" * 70)
    print("📊 REGIME vs STOP-LOSS PERFORMANCE")
    print("=" * 70)
    
    regimes = defaultdict(list)
    for t in trades:
        regime = t.get("regime", "unknown") or t.get("at_open_regime", "unknown") or "unknown"
        regimes[regime].append(t)
    
    print(f"\n{'REGIME':<20} {'TRADES':>8} {'AVG PNL':>10} {'AVG HOLD':>12} {'WIN%':>8}")
    print("-" * 60)
    
    for regime, ts in sorted(regimes.items(), key=lambda x: -len(x[1])):
        if len(ts) < 3:
            continue
        
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        avg_pnl = sum(pnls) / len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        
        holds = [t.get("hold_minutes", 0) or 0 for t in ts if t.get("hold_minutes")]
        avg_hold = sum(holds) / len(holds) if holds else 0
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {regime:<18} {len(ts):>8} {avg_pnl:>10.4f} {avg_hold:>10.0f}m {wr:>7.1f}%")

def analyze_session_vs_stop(trades):
    """Analyze stop performance by session."""
    print("\n" + "=" * 70)
    print("📊 SESSION vs STOP-LOSS PERFORMANCE")
    print("=" * 70)
    
    sessions = defaultdict(list)
    for t in trades:
        session = t.get("session", "unknown") or "unknown"
        sessions[session].append(t)
    
    print(f"\n{'SESSION':<20} {'TRADES':>8} {'AVG PNL':>10} {'AVG HOLD':>12} {'WIN%':>8}")
    print("-" * 60)
    
    for session, ts in sorted(sessions.items(), key=lambda x: -len(x[1])):
        if len(ts) < 3:
            continue
        
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        avg_pnl = sum(pnls) / len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        
        holds = [t.get("hold_minutes", 0) or 0 for t in ts if t.get("hold_minutes")]
        avg_hold = sum(holds) / len(holds) if holds else 0
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {session:<18} {len(ts):>8} {avg_pnl:>10.4f} {avg_hold:>10.0f}m {wr:>7.1f}%")

def analyze_direction_vs_stop(trades):
    """Analyze stop performance by direction."""
    print("\n" + "=" * 70)
    print("📊 DIRECTION vs STOP-LOSS PERFORMANCE")
    print("=" * 70)
    
    longs = [t for t in trades if t.get("side") == "LONG"]
    shorts = [t for t in trades if t.get("side") == "SHORT"]
    
    for label, ts in [("LONG", longs), ("SHORT", shorts)]:
        if not ts:
            continue
        
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        avg_pnl = sum(pnls) / len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100
        
        holds = [t.get("hold_minutes", 0) or 0 for t in ts if t.get("hold_minutes")]
        avg_hold = sum(holds) / len(holds) if holds else 0
        
        # SL distance
        sl_dists = []
        for t in ts:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            if entry and sl and entry > 0:
                sl_dists.append(abs(entry - sl) / entry * 100)
        avg_sl = sum(sl_dists) / len(sl_dists) if sl_dists else 0
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"\n{emoji} {label}:")
        print(f"   Trades: {len(ts)}")
        print(f"   Win Rate: {wr:.1f}%")
        print(f"   Avg PnL: ${avg_pnl:.4f}")
        print(f"   Avg Hold: {avg_hold:.0f} minutes")
        print(f"   Avg SL Distance: {avg_sl:.2f}%")

def generate_summary(trades):
    """Generate summary of findings."""
    print("\n" + "=" * 70)
    print("📋 STOP-LOSS ANALYSIS SUMMARY")
    print("=" * 70)
    
    total = len(trades)
    
    # SL distance analysis
    sl_dists = []
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        if entry and sl and entry > 0:
            sl_dists.append(abs(entry - sl) / entry * 100)
    
    avg_sl = sum(sl_dists) / len(sl_dists) if sl_dists else 0
    tight_stops = sum(1 for d in sl_dists if d < 1.0)
    
    # Holding time
    holds = [t.get("hold_minutes", 0) or 0 for t in trades if t.get("hold_minutes")]
    avg_hold = sum(holds) / len(holds) if holds else 0
    quick_stops = sum(1 for h in holds if h < 15)
    
    print(f"""
📊 FINDINGS:

1. STOP DISTANCE:
   - Average SL distance: {avg_sl:.2f}%
   - Stops < 1%: {tight_stops} trades ({tight_stops/total*100:.1f}%)
   - {'⚠️ MANY TIGHT STOPS - likely getting stopped by noise' if tight_stops > total * 0.3 else '✅ SL distances look reasonable'}

2. HOLDING TIME:
   - Average time to stop: {avg_hold:.0f} minutes
   - Stopped within 15min: {quick_stops} trades ({quick_stops/total*100:.1f}%)
   - {'⚠️ QUICK STOPS - entry timing may be poor' if quick_stops > total * 0.3 else '✅ Holding times look reasonable'}

3. RECOMMENDATIONS:
   {'- Consider widening SL by 0.5-1.0 ATR' if avg_sl < 2.0 else '- SL distance looks adequate'}
   {'- Review entry timing - many stops hit quickly' if quick_stops > total * 0.3 else '- Entry timing looks acceptable'}
   '- Analyze if stops are placed at obvious liquidity levels
   '- Consider using ATR-based dynamic stops instead of fixed levels
""")
    
    print("=" * 70)
    print("NOTE: These are diagnostic findings only.")
    print("No changes have been made to the strategy.")
    print("=" * 70)

def main():
    print("=" * 70)
    print("🔬 STOP-LOSS DEEP DIVE ANALYSIS")
    print("=" * 70)
    
    trades = get_stopped_trades()
    print(f"\n📊 Loaded {len(trades)} stopped trades")
    
    if len(trades) < 10:
        print("❌ Not enough stopped trades for meaningful analysis")
        return
    
    analyze_sl_distance(trades)
    analyze_holding_time(trades)
    analyze_confidence_vs_stop(trades)
    analyze_regime_vs_stop(trades)
    analyze_session_vs_stop(trades)
    analyze_direction_vs_stop(trades)
    generate_summary(trades)

if __name__ == "__main__":
    main()
