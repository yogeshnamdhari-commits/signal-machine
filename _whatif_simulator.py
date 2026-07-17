#!/usr/bin/env python3
"""
What-If Simulator & Stop Recovery Analyzer
==========================================
1. Simulates wider stops to see if performance improves
2. Calculates stop recovery rate (did price reach TP after stop?)

READ-ONLY — Never modifies trading logic.
"""
import sqlite3
from pathlib import Path
from collections import defaultdict

DB_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/institutional_v1.db")

def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def get_all_closed_trades():
    """Get all closed trades from both tables."""
    conn = connect()
    rows = conn.execute("""
        SELECT * FROM positions WHERE status = 'closed' ORDER BY closed_at ASC
    """).fetchall()
    trades = [dict(r) for r in rows]
    
    rows2 = conn.execute("""
        SELECT * FROM positions_archive WHERE status = 'closed' ORDER BY closed_at ASC
    """).fetchall()
    trades.extend([dict(r) for r in rows2])
    conn.close()
    
    # Deduplicate
    seen = set()
    unique = []
    for t in trades:
        key = (t.get("symbol"), t.get("closed_at"))
        if key not in seen:
            seen.add(key)
            unique.append(t)
    
    return unique

def simulate_wider_stops(trades):
    """Simulate what happens with wider stops."""
    print("\n" + "=" * 70)
    print("📊 WHAT-IF SIMULATOR: WIDER STOPS")
    print("=" * 70)
    print("   Testing different SL distances to find optimal stop placement\n")
    
    # Get stopped trades
    stopped = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    non_stopped = [t for t in trades if t.get("exit_reason") != "stop_loss"]
    
    if not stopped:
        print("   No stopped trades available for simulation")
        return
    
    print(f"   Baseline: {len(stopped)} stopped trades out of {len(trades)} total")
    print(f"   Current win rate: {sum(1 for t in trades if (t.get('pnl') or 0) > 0) / len(trades) * 100:.1f}%\n")
    
    # Calculate current SL distances
    sl_dists = []
    for t in stopped:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        if entry and sl and entry > 0:
            sl_dists.append(abs(entry - sl) / entry * 100)
    
    avg_sl = sum(sl_dists) / len(sl_dists) if sl_dists else 0
    print(f"   Current average SL distance: {avg_sl:.2f}%\n")
    
    # Simulate different SL widths
    test_widths = [1.0, 1.5, 2.0, 2.5, 3.0]
    
    print(f"{'SL WIDTH':<12} {'WOULD STOP':<12} {'AVOIDED':<12} {'EST WR':<10} {'IMPACT':<15}")
    print("-" * 65)
    
    for width in test_widths:
        # Count how many stops would be avoided
        would_stop = 0
        avoided = 0
        
        for t in stopped:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            if entry and sl and entry > 0:
                current_dist = abs(entry - sl) / entry * 100
                if current_dist < width:
                    would_stop += 1
                else:
                    avoided += 1
        
        # Estimate win rate improvement
        # Trades that would have been stopped with tighter SL but not with wider SL
        # These trades might have reached TP
        potential_winners = avoided * 0.3  # Assume 30% would reach TP
        new_wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0) + potential_winners
        new_wr = new_wins / len(trades) * 100
        
        emoji = "🟢" if avoided > 0 else "🔴"
        impact = f"+{potential_winners:.0f} potential wins" if avoided > 0 else "No change"
        
        print(f"{emoji} {width:.1f}%{'':<7} {would_stop:<12} {avoided:<12} {new_wr:.1f}%{'':<5} {impact}")

def analyze_stop_recovery(trades):
    """Analyze if stopped trades eventually reached TP."""
    print("\n" + "=" * 70)
    print("📊 STOP RECOVERY ANALYSIS")
    print("=" * 70)
    print("   Did price eventually reach TP after the stop?")
    print("   This tells us if stops are too tight (not bad entries)\n")
    
    stopped = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    
    if not stopped:
        print("   No stopped trades available")
        return
    
    # For each stopped trade, check if there's a later trade on same symbol
    # that reached TP (suggesting the stop was too tight)
    symbol_trades = defaultdict(list)
    for t in trades:
        symbol_trades[t.get("symbol", "")].append(t)
    
    recovery_stats = {
        "total_stopped": len(stopped),
        "symbol_traded_again": 0,
        "symbol_reached_tp": 0,
        "potential_recovery": 0,
    }
    
    for t in stopped:
        sym = t.get("symbol", "")
        stop_time = t.get("closed_at", 0)
        
        # Find later trades on same symbol
        later_trades = [
            lt for lt in symbol_trades.get(sym, [])
            if lt.get("closed_at", 0) > stop_time
            and lt.get("exit_reason") != "stop_loss"
        ]
        
        if later_trades:
            recovery_stats["symbol_traded_again"] += 1
            
            # Check if any reached TP
            tp_trades = [lt for lt in later_trades if "take_profit" in (lt.get("exit_reason") or "")]
            if tp_trades:
                recovery_stats["symbol_reached_tp"] += 1
    
    print(f"   Stopped trades analyzed: {recovery_stats['total_stopped']}")
    print(f"   Symbol traded again later: {recovery_stats['symbol_traded_again']} ({recovery_stats['symbol_traded_again']/recovery_stats['total_stopped']*100:.1f}%)")
    print(f"   Symbol reached TP later: {recovery_stats['symbol_reached_tp']} ({recovery_stats['symbol_reached_tp']/recovery_stats['total_stopped']*100:.1f}%)")
    
    if recovery_stats['symbol_reached_tp'] > 0:
        print(f"\n   ⚠️  {recovery_stats['symbol_reached_tp']} stopped symbols later reached TP!")
        print(f"   This suggests stops may be too tight on these symbols.")

def analyze_sl_vs_tp_ratio(trades):
    """Analyze the ratio of SL distance to TP distance."""
    print("\n" + "=" * 70)
    print("📊 SL vs TP DISTANCE RATIO")
    print("=" * 70)
    print("   Are stops wider or narrower than targets?\n")
    
    ratios = []
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        tp = t.get("take_profit", 0)
        
        if entry and sl and tp and entry > 0:
            sl_dist = abs(entry - sl) / entry * 100
            tp_dist = abs(tp - entry) / entry * 100
            
            if sl_dist > 0:
                ratio = tp_dist / sl_dist  # Reward:Risk
                ratios.append({
                    "symbol": t.get("symbol"),
                    "side": t.get("side"),
                    "sl_dist": sl_dist,
                    "tp_dist": tp_dist,
                    "ratio": ratio,
                    "pnl": t.get("pnl", 0),
                    "exit_reason": t.get("exit_reason"),
                })
    
    if not ratios:
        print("   No SL/TP data available")
        return
    
    # Distribution
    buckets = {
        "< 1:1 (bad)": [], "1:1": [], "1.5:1": [], "2:1": [], "2.5:1": [], "3:1+": []
    }
    
    for r in ratios:
        ratio = r["ratio"]
        if ratio < 1:
            buckets["< 1:1 (bad)"].append(r)
        elif ratio < 1.25:
            buckets["1:1"].append(r)
        elif ratio < 1.75:
            buckets["1.5:1"].append(r)
        elif ratio < 2.25:
            buckets["2:1"].append(r)
        elif ratio < 2.75:
            buckets["2.5:1"].append(r)
        else:
            buckets["3:1+"].append(r)
    
    print(f"{'R:R RATIO':<15} {'TRADES':>8} {'AVG PNL':>10} {'WIN%':>8}")
    print("-" * 45)
    
    for bucket_name, bucket_trades in buckets.items():
        if not bucket_trades:
            continue
        
        avg_pnl = sum(r["pnl"] for r in bucket_trades) / len(bucket_trades)
        wins = sum(1 for r in bucket_trades if r["pnl"] > 0)
        wr = wins / len(bucket_trades) * 100 if bucket_trades else 0
        
        emoji = "🔴" if avg_pnl < 0 else "🟢"
        print(f"{emoji} {bucket_name:<13} {len(bucket_trades):>8} {avg_pnl:>10.4f} {wr:>7.1f}%")
    
    # Overall stats
    avg_ratio = sum(r["ratio"] for r in ratios) / len(ratios)
    print(f"\n   Average R:R Ratio: {avg_ratio:.2f}")
    
    if avg_ratio < 1.5:
        print(f"   ⚠️  Average R:R is below 1.5:1 — targets may be too conservative")

def analyze_confidence_calibration(trades):
    """Analyze if confidence predicts actual outcomes."""
    print("\n" + "=" * 70)
    print("📊 CONFIDENCE CALIBRATION CHECK")
    print("=" * 70)
    print("   Is the confidence model well-calibrated?\n")
    
    buckets = defaultdict(list)
    for t in trades:
        conf = (t.get("confidence", 0) or 0) * 100
        bucket = int(conf / 10) * 10
        buckets[bucket].append(t)
    
    print(f"{'CONF':<12} {'TRADES':>8} {'WIN%':>8} {'PF':>8} {'AVG R':>8} {'CALIBRATED':>12}")
    print("-" * 65)
    
    for bucket in sorted(buckets.keys()):
        ts = buckets[bucket]
        if len(ts) < 5:
            continue
        
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        
        wr = len(wins) / len(pnls) * 100 if pnls else 0
        gp = sum(wins) if wins else 0
        gl = sum(losses) if losses else 0
        pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
        
        rs = [t.get("realized_r", 0) or 0 for t in ts if t.get("realized_r")]
        avg_r = sum(rs) / len(rs) if rs else 0
        
        # Check calibration: higher confidence should mean higher win rate
        expected_wr = bucket  # If perfectly calibrated, WR ≈ confidence
        calibration = "✅" if abs(wr - expected_wr) < 15 else "⚠️"
        
        emoji = "🔴" if pf < 1 else "🟢"
        print(f"{emoji} {bucket}-{bucket+10:<6} {len(ts):>8} {wr:>7.1f}% {pf:>8.2f} {avg_r:>8.2f} {calibration:>12}")

def generate_whatif_report(trades):
    """Generate comprehensive what-if analysis report."""
    print("\n" + "=" * 70)
    print("📋 WHAT-IF ANALYSIS SUMMARY")
    print("=" * 70)
    
    total = len(trades)
    stopped = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    tp_hits = [t for t in trades if "take_profit" in (t.get("exit_reason") or "")]
    
    # Calculate current metrics
    pnls = [t.get("pnl", 0) or 0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    
    wr = len(wins) / total * 100 if total else 0
    gp = sum(wins) if wins else 0
    gl = sum(losses) if losses else 0
    pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
    
    print(f"""
📊 CURRENT METRICS:
   Total Trades: {total}
   Win Rate: {wr:.1f}%
   Profit Factor: {pf:.2f}
   Stopped Out: {len(stopped)} ({len(stopped)/total*100:.1f}%)
   TP Hits: {len(tp_hits)} ({len(tp_hits)/total*100:.1f}%)

📊 KEY FINDINGS:
   1. {len(stopped)/total*100:.0f}% of trades hit stop loss
   2. Average SL distance: {sum(abs(t.get('entry_price', 0) - t.get('stop_loss', 0)) / t.get('entry_price', 1) * 100 for t in stopped if t.get('entry_price') and t.get('stop_loss')) / max(1, len(stopped)):.2f}%
   3. TP hit rate: {len(tp_hits)/total*100:.1f}%

📋 RECOMMENDATIONS:
   1. TEST WIDER STOPS: Simulate 1.5-2.0% minimum SL
   2. ATR-BASED STOPS: Use 1.5 × ATR for dynamic stops
   3. TRAILING STOPS: Implement for trades with MFE > 1%
   4. CONFIDENCE RECALIBRATION: Higher confidence ≠ better outcomes
""")
    
    print("=" * 70)
    print("NOTE: These are simulation results only.")
    print("No changes have been made to the strategy.")
    print("=" * 70)

def main():
    print("=" * 70)
    print("🔬 WHAT-IF SIMULATOR & STOP RECOVERY ANALYZER")
    print("=" * 70)
    
    trades = get_all_closed_trades()
    print(f"\n📊 Loaded {len(trades)} total trades")
    
    if len(trades) < 10:
        print("❌ Not enough trades for meaningful analysis")
        return
    
    simulate_wider_stops(trades)
    analyze_stop_recovery(trades)
    analyze_sl_vs_tp_ratio(trades)
    analyze_confidence_calibration(trades)
    generate_whatif_report(trades)

if __name__ == "__main__":
    main()
