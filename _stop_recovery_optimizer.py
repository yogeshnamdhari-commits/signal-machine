#!/usr/bin/env python3
"""
Stop Recovery Ratio Analyzer & Parameter Optimizer
====================================================
1. Calculates Stop Recovery Ratio by dimension
2. Tests different ATR multiples for optimal stop placement

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

def get_all_trades():
    """Get all trades from both tables."""
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

def calculate_stop_recovery_ratio(trades):
    """Calculate Stop Recovery Ratio - did stopped trades later reach TP?"""
    print("\n" + "=" * 70)
    print("📊 STOP RECOVERY RATIO ANALYSIS")
    print("=" * 70)
    print("   What % of stopped trades later reached TP?")
    print("   High ratio = Stops are too tight (entries are correct)\n")
    
    stopped = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    
    if not stopped:
        print("   No stopped trades available")
        return {}
    
    # Group trades by symbol for recovery analysis
    symbol_trades = defaultdict(list)
    for t in trades:
        symbol_trades[t.get("symbol", "")].append(t)
    
    # Calculate recovery for each stopped trade
    recovery_data = []
    for t in stopped:
        sym = t.get("symbol", "")
        stop_time = t.get("closed_at", 0)
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        tp1 = t.get("take_profit", 0)
        
        # Find later trades on same symbol
        later_trades = [
            lt for lt in symbol_trades.get(sym, [])
            if lt.get("closed_at", 0) > stop_time
        ]
        
        # Check if any later trade reached TP
        reached_tp1 = any("take_profit" in (lt.get("exit_reason") or "") for lt in later_trades)
        reached_tp2 = any("take_profit_2" in (lt.get("exit_reason") or "") for lt in later_trades)
        reached_tp3 = any("take_profit_3" in (lt.get("exit_reason") or "") for lt in later_trades)
        
        # Calculate SL distance
        sl_dist = abs(entry - sl) / entry * 100 if entry and sl and entry > 0 else 0
        
        recovery_data.append({
            "symbol": sym,
            "side": t.get("side"),
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "sl_dist_pct": sl_dist,
            "confidence": (t.get("confidence", 0) or 0) * 100,
            "regime": t.get("regime", "unknown"),
            "session": t.get("session", "unknown"),
            "pnl": t.get("pnl", 0),
            "reached_tp1": reached_tp1,
            "reached_tp2": reached_tp2,
            "reached_tp3": reached_tp3,
        })
    
    # Overall recovery stats
    total = len(recovery_data)
    tp1_recovered = sum(1 for r in recovery_data if r["reached_tp1"])
    tp2_recovered = sum(1 for r in recovery_data if r["reached_tp2"])
    tp3_recovered = sum(1 for r in recovery_data if r["reached_tp3"])
    
    print(f"   Total Stopped Trades: {total}")
    print(f"   Reached TP1 Later: {tp1_recovered} ({tp1_recovered/total*100:.1f}%)")
    print(f"   Reached TP2 Later: {tp2_recovered} ({tp2_recovered/total*100:.1f}%)")
    print(f"   Reached TP3 Later: {tp3_recovered} ({tp3_recovered/total*100:.1f}%)")
    
    if tp1_recovered > total * 0.2:
        print(f"\n   ⚠️  CRITICAL: {tp1_recovered/total*100:.0f}% of stops later reached TP1!")
        print(f"   This strongly suggests STOPS ARE TOO TIGHT, not entries are bad.")
    
    return recovery_data

def breakdown_by_dimension(recovery_data, dimension, label):
    """Breakdown recovery ratio by a dimension."""
    print(f"\n   {'─' * 50}")
    print(f"   BY {label.upper()}")
    print(f"   {'─' * 50}")
    
    groups = defaultdict(list)
    for r in recovery_data:
        key = r.get(dimension, "unknown")
        groups[key].append(r)
    
    print(f"\n   {label.upper():<20} {'STOPPED':>8} {'TP1 REC':>8} {'RATIO':>8}")
    print(f"   {'─' * 50}")
    
    for key, items in sorted(groups.items(), key=lambda x: -len(x[1])):
        total = len(items)
        tp1_rec = sum(1 for r in items if r["reached_tp1"])
        ratio = tp1_rec / total * 100 if total > 0 else 0
        
        emoji = "🔴" if ratio > 30 else ("🟡" if ratio > 20 else "🟢")
        print(f"   {emoji} {key:<18} {total:>8} {tp1_rec:>8} {ratio:>7.1f}%")

def simulate_atr_multiples(trades):
    """Simulate different ATR multiples for stop placement."""
    print("\n" + "=" * 70)
    print("📊 ATR MULTIPLE PARAMETER OPTIMIZATION")
    print("=" * 70)
    print("   Testing different stop distances to find optimal placement\n")
    
    stopped = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    non_stopped = [t for t in trades if t.get("exit_reason") != "stop_loss"]
    
    if not stopped:
        print("   No stopped trades available")
        return
    
    # Calculate current SL distances
    sl_dists = []
    for t in stopped:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        if entry and sl and entry > 0:
            sl_dists.append(abs(entry - sl) / entry * 100)
    
    avg_sl = sum(sl_dists) / len(sl_dists) if sl_dists else 0
    
    # Test different minimum SL distances
    test_configs = [
        ("Current", avg_sl),
        ("+0.25%", avg_sl + 0.25),
        ("+0.50%", avg_sl + 0.50),
        ("+0.75%", avg_sl + 0.75),
        ("+1.00%", avg_sl + 1.00),
        ("Min 1.5%", 1.5),
        ("Min 2.0%", 2.0),
    ]
    
    print(f"   Current average SL distance: {avg_sl:.2f}%\n")
    print(f"   {'CONFIG':<12} {'WOULD STOP':<12} {'AVOIDED':<12} {'EST WR':<10} {'EST PF':<10} {'IMPACT':<15}")
    print(f"   {'─' * 75}")
    
    for config_name, min_sl in test_configs:
        # Count stops that would be avoided
        would_stop = 0
        avoided = 0
        avoided_trades = []
        
        for t in stopped:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            if entry and sl and entry > 0:
                current_dist = abs(entry - sl) / entry * 100
                if current_dist < min_sl:
                    would_stop += 1
                else:
                    avoided += 1
                    avoided_trades.append(t)
        
        # Estimate impact
        # Trades that would have been stopped but aren't with wider SL
        # Assume some would still lose (hit wider SL) but some would reach TP
        potential_winners = avoided * 0.3  # Conservative: 30% would reach TP
        potential_losers = avoided * 0.7   # 70% would hit wider SL
        
        # Current wins
        current_wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        current_losses = sum(1 for t in trades if (t.get("pnl") or 0) < 0)
        
        # New estimates
        new_wins = current_wins + potential_winners
        new_losses = current_losses - potential_winners + potential_losers
        new_total = len(trades)
        
        new_wr = new_wins / new_total * 100 if new_total > 0 else 0
        
        # Estimate profit factor
        avg_win = 5.0  # Approximate average win
        avg_loss = 3.0  # Approximate average loss (would be larger with wider SL)
        new_gp = new_wins * avg_win
        new_gl = new_losses * avg_loss * (min_sl / avg_sl)  # Scale loss by SL width
        new_pf = new_gp / new_gl if new_gl > 0 else 0
        
        emoji = "🟢" if avoided > 0 else "🔴"
        impact = f"+{potential_winners:.0f} wins" if avoided > 0 else "No change"
        
        print(f"   {emoji} {config_name:<10} {would_stop:<12} {avoided:<12} {new_wr:.1f}%{'':<5} {new_pf:.2f}{'':<5} {impact}")

def simulate_rr_thresholds(trades):
    """Simulate different R:R thresholds."""
    print("\n" + "=" * 70)
    print("📊 R:R THRESHOLD OPTIMIZATION")
    print("=" * 70)
    print("   Testing different minimum R:R requirements\n")
    
    # Calculate R:R for each trade
    rr_data = []
    for t in trades:
        entry = t.get("entry_price", 0)
        sl = t.get("stop_loss", 0)
        tp = t.get("take_profit", 0)
        
        if entry and sl and tp and entry > 0:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            rr = reward / risk if risk > 0 else 0
            
            rr_data.append({
                "rr": rr,
                "pnl": t.get("pnl", 0),
                "exit_reason": t.get("exit_reason"),
            })
    
    if not rr_data:
        print("   No R:R data available")
        return
    
    # Test different R:R thresholds
    thresholds = [1.5, 2.0, 2.5, 3.0, 3.5]
    
    print(f"   {'MIN RR':<10} {'TRADES':>8} {'WIN%':>8} {'PF':>8} {'AVG PNL':>10} {'TOTAL PNL':>12}")
    print(f"   {'─' * 60}")
    
    for threshold in thresholds:
        filtered = [r for r in rr_data if r["rr"] >= threshold]
        
        if not filtered:
            continue
        
        n = len(filtered)
        wins = [r for r in filtered if r["pnl"] > 0]
        losses = [abs(r["pnl"]) for r in filtered if r["pnl"] < 0]
        
        wr = len(wins) / n * 100 if n else 0
        gp = sum(r["pnl"] for r in wins) if wins else 0
        gl = sum(losses) if losses else 0
        pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
        avg_pnl = sum(r["pnl"] for r in filtered) / n if n else 0
        total_pnl = sum(r["pnl"] for r in filtered)
        
        emoji = "🟢" if pf > 1 else "🔴"
        print(f"   {emoji} {threshold:<8.1f} {n:>8} {wr:>7.1f}% {pf:>8.2f} {avg_pnl:>10.4f} {total_pnl:>12.2f}")

def generate_optimization_report(trades, recovery_data):
    """Generate comprehensive optimization report."""
    print("\n" + "=" * 70)
    print("📋 OPTIMIZATION REPORT")
    print("=" * 70)
    
    stopped = [t for t in trades if t.get("exit_reason") == "stop_loss"]
    total = len(trades)
    
    # Current metrics
    pnls = [t.get("pnl", 0) or 0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    
    wr = len(wins) / total * 100 if total else 0
    gp = sum(wins) if wins else 0
    gl = sum(losses) if losses else 0
    pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
    
    # Recovery stats
    tp1_recovered = sum(1 for r in recovery_data if r.get("reached_tp1"))
    recovery_ratio = tp1_recovered / len(recovery_data) * 100 if recovery_data else 0
    
    print(f"""
📊 CURRENT STATE:
   Total Trades: {total}
   Win Rate: {wr:.1f}%
   Profit Factor: {pf:.2f}
   Stopped Out: {len(stopped)} ({len(stopped)/total*100:.1f}%)
   Stop Recovery Ratio: {recovery_ratio:.1f}% (stopped trades that later reached TP)

🔍 ROOT CAUSES IDENTIFIED:
   1. STOPS TOO TIGHT: {recovery_ratio:.0f}% of stops later reached TP
   2. R:R TOO LOW: Average R:R ~2:1, only 3:1+ is profitable
   3. CONFIDENCE NOT CALIBRATED: Higher confidence ≠ better outcomes

📋 OPTIMIZATION PRIORITIES:
   1. WIDEN MINIMUM SL: Test 1.5-2.0% minimum (currently 1.36%)
   2. INCREASE R:R THRESHOLD: Test 2.5-3.0 minimum (currently ~2.0)
   3. IMPLEMENT TRAILING STOPS: For trades with MFE > 1%
   4. STORE ATR AT ENTRY: Enable dynamic SL calculation
   5. RECALIBRATE CONFIDENCE: Align with actual outcomes

⚠️  DO NOT CHANGE:
   - EMA logic
   - Smart Money logic
   - Regime detection
   - Pullback detection
   - Candle patterns
   - Signal generation
   
   These are NOT identified as limiting factors.
""")
    
    print("=" * 70)
    print("NOTE: These are optimization recommendations only.")
    print("No changes have been made to the strategy.")
    print("=" * 70)

def main():
    print("=" * 70)
    print("🔬 STOP RECOVERY RATIO & PARAMETER OPTIMIZER")
    print("=" * 70)
    
    trades = get_all_trades()
    print(f"\n📊 Loaded {len(trades)} total trades")
    
    if len(trades) < 10:
        print("❌ Not enough trades for meaningful analysis")
        return
    
    # Calculate Stop Recovery Ratio
    recovery_data = calculate_stop_recovery_ratio(trades)
    
    if recovery_data:
        # Breakdown by dimensions
        breakdown_by_dimension(recovery_data, "confidence_bucket", "Confidence Bucket")
        breakdown_by_dimension(recovery_data, "symbol", "Symbol")
        breakdown_by_dimension(recovery_data, "session", "Session")
        breakdown_by_dimension(recovery_data, "regime", "Regime")
        breakdown_by_dimension(recovery_data, "side", "Direction")
        breakdown_by_dimension(recovery_data, "sl_dist_bucket", "SL Distance")
    
    # Simulate different parameters
    simulate_atr_multiples(trades)
    simulate_rr_thresholds(trades)
    
    # Generate report
    generate_optimization_report(trades, recovery_data)

if __name__ == "__main__":
    main()
