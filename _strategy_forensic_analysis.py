#!/usr/bin/env python3
"""
Strategy Forensic Analysis
============================
Uses the institutional analytics platform to diagnose
WHY the strategy is losing money and WHAT to fix.

READ-ONLY — Never modifies trading logic.
"""
import sys
from pathlib import Path

AI_ROOT = Path(__file__).resolve().parent / "packages" / "ai-engine"
sys.path.insert(0, str(AI_ROOT))

import sqlite3
import json
from collections import defaultdict
from datetime import datetime, timezone

DB_PATH = AI_ROOT / "data" / "institutional_v1.db"


def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def get_all_trades():
    conn = connect()
    rows = conn.execute("""
        SELECT * FROM positions WHERE status = 'closed' ORDER BY closed_at ASC
    """).fetchall()
    trades = [dict(r) for r in rows]
    
    try:
        rows2 = conn.execute("""
            SELECT * FROM positions_archive WHERE status = 'closed' ORDER BY closed_at ASC
        """).fetchall()
        trades.extend([dict(r) for r in rows2])
    except:
        pass
    
    conn.close()
    return trades


def analyze_exit_reasons(trades):
    """Analyze which exit reasons are causing losses."""
    print("\n" + "=" * 80)
    print("🔍 EXIT REASON ANALYSIS")
    print("=" * 80)
    
    by_exit = defaultdict(list)
    for t in trades:
        reason = t.get("exit_reason", "unknown") or "unknown"
        by_exit[reason].append(t)
    
    print(f"\n{'Exit Reason':<25} {'Count':>6} {'Win%':>8} {'Avg PnL':>10} {'Total PnL':>12}")
    print("-" * 65)
    
    for reason, ts in sorted(by_exit.items(), key=lambda x: -len(x[1])):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100 if n else 0
        avg = sum(pnls) / n if n else 0
        total = sum(pnls)
        
        emoji = "🟢" if total > 0 else "🔴"
        print(f"{emoji} {reason:<23} {n:>6} {wr:>7.1f}% {avg:>10.2f} {total:>12.2f}")


def analyze_symbols(trades):
    """Analyze performance by symbol."""
    print("\n" + "=" * 80)
    print("🔍 SYMBOL ANALYSIS")
    print("=" * 80)
    
    by_symbol = defaultdict(list)
    for t in trades:
        symbol = t.get("symbol", "unknown") or "unknown"
        by_symbol[symbol].append(t)
    
    print(f"\n{'Symbol':<15} {'Count':>6} {'Win%':>8} {'Avg PnL':>10} {'Total PnL':>12} {'Avg RR':>8}")
    print("-" * 65)
    
    for symbol, ts in sorted(by_symbol.items(), key=lambda x: -len(x[1])):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100 if n else 0
        avg = sum(pnls) / n if n else 0
        total = sum(pnls)
        
        rrs = [t.get("realized_r", 0) or 0 for t in ts if t.get("realized_r")]
        avg_rr = sum(rrs) / len(rrs) if rrs else 0
        
        emoji = "🟢" if total > 0 else "🔴"
        print(f"{emoji} {symbol:<13} {n:>6} {wr:>7.1f}% {avg:>10.2f} {total:>12.2f} {avg_rr:>7.2f}R")


def analyze_sessions(trades):
    """Analyze performance by session."""
    print("\n" + "=" * 80)
    print("🔍 SESSION ANALYSIS")
    print("=" * 80)
    
    by_session = defaultdict(list)
    for t in trades:
        session = t.get("session", "unknown") or "unknown"
        by_session[session].append(t)
    
    print(f"\n{'Session':<15} {'Count':>6} {'Win%':>8} {'Avg PnL':>10} {'Total PnL':>12}")
    print("-" * 55)
    
    for session, ts in sorted(by_session.items(), key=lambda x: -len(x[1])):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100 if n else 0
        avg = sum(pnls) / n if n else 0
        total = sum(pnls)
        
        emoji = "🟢" if total > 0 else "🔴"
        print(f"{emoji} {session:<13} {n:>6} {wr:>7.1f}% {avg:>10.2f} {total:>12.2f}")


def analyze_regimes(trades):
    """Analyze performance by regime."""
    print("\n" + "=" * 80)
    print("🔍 REGIME ANALYSIS")
    print("=" * 80)
    
    by_regime = defaultdict(list)
    for t in trades:
        regime = t.get("regime", "unknown") or "unknown"
        by_regime[regime].append(t)
    
    print(f"\n{'Regime':<20} {'Count':>6} {'Win%':>8} {'Avg PnL':>10} {'Total PnL':>12}")
    print("-" * 60)
    
    for regime, ts in sorted(by_regime.items(), key=lambda x: -len(x[1])):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100 if n else 0
        avg = sum(pnls) / n if n else 0
        total = sum(pnls)
        
        emoji = "🟢" if total > 0 else "🔴"
        print(f"{emoji} {regime:<18} {n:>6} {wr:>7.1f}% {avg:>10.2f} {total:>12.2f}")


def analyze_direction(trades):
    """Analyze performance by direction."""
    print("\n" + "=" * 80)
    print("🔍 DIRECTION ANALYSIS")
    print("=" * 80)
    
    by_side = defaultdict(list)
    for t in trades:
        side = t.get("side", "unknown") or "unknown"
        by_side[side].append(t)
    
    print(f"\n{'Direction':<15} {'Count':>6} {'Win%':>8} {'Avg PnL':>10} {'Total PnL':>12}")
    print("-" * 55)
    
    for side, ts in sorted(by_side.items(), key=lambda x: -len(x[1])):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100 if n else 0
        avg = sum(pnls) / n if n else 0
        total = sum(pnls)
        
        emoji = "🟢" if total > 0 else "🔴"
        print(f"{emoji} {side:<13} {n:>6} {wr:>7.1f}% {avg:>10.2f} {total:>12.2f}")


def analyze_confidence(trades):
    """Analyze performance by confidence bucket."""
    print("\n" + "=" * 80)
    print("🔍 CONFIDENCE ANALYSIS")
    print("=" * 80)
    
    buckets = {
        "0-20": [], "20-40": [], "40-60": [], "60-80": [], "80-100": []
    }
    
    for t in trades:
        conf = t.get("confidence", 0) or 0
        if conf < 20:
            buckets["0-20"].append(t)
        elif conf < 40:
            buckets["20-40"].append(t)
        elif conf < 60:
            buckets["40-60"].append(t)
        elif conf < 80:
            buckets["60-80"].append(t)
        else:
            buckets["80-100"].append(t)
    
    print(f"\n{'Confidence':<15} {'Count':>6} {'Win%':>8} {'Avg PnL':>10} {'Total PnL':>12}")
    print("-" * 55)
    
    for bucket, ts in buckets.items():
        if not ts:
            continue
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100 if n else 0
        avg = sum(pnls) / n if n else 0
        total = sum(pnls)
        
        emoji = "🟢" if total > 0 else "🔴"
        print(f"{emoji} {bucket:<13} {n:>6} {wr:>7.1f}% {avg:>10.2f} {total:>12.2f}")


def analyze_stop_loss(trades):
    """Analyze stop-loss efficiency."""
    print("\n" + "=" * 80)
    print("🔍 STOP-LOSS EFFICIENCY")
    print("=" * 80)
    
    sl_trades = [t for t in trades if (t.get("exit_reason", "") or "").lower() in ["stop_loss", "trailing_stop"]]
    tp_trades = [t for t in trades if (t.get("exit_reason", "") or "").lower() in ["take_profit", "tp1", "tp2", "tp3"]]
    
    print(f"\n  Stop-Loss exits: {len(sl_trades)}")
    print(f"  Take-Profit exits: {len(tp_trades)}")
    
    if sl_trades:
        sl_pnls = [t.get("pnl", 0) or 0 for t in sl_trades]
        avg_sl = sum(sl_pnls) / len(sl_pnls) if sl_pnls else 0
        print(f"  Avg SL loss: ${avg_sl:.2f}")
        
        # Check MFE before stop
        mfe_trades = [t for t in sl_trades if t.get("mfe_pct")]
        if mfe_trades:
            mfe_vals = [t["mfe_pct"] for t in mfe_trades if t["mfe_pct"]]
            if mfe_vals:
                avg_mfe = sum(mfe_vals) / len(mfe_vals)
                print(f"  Avg MFE before stop: {avg_mfe:.2f}%")
                
                # Count trades that went positive before stopping
                positive_before_stop = sum(1 for m in mfe_vals if m > 0)
                print(f"  Trades that went positive before stop: {positive_before_stop}/{len(mfe_vals)} ({positive_before_stop/len(mfe_vals)*100:.1f}%)")
    
    if tp_trades:
        tp_pnls = [t.get("pnl", 0) or 0 for t in tp_trades]
        avg_tp = sum(tp_pnls) / len(tp_pnls) if tp_pnls else 0
        print(f"  Avg TP profit: ${avg_tp:.2f}")


def analyze_sl_distance(trades):
    """Analyze stop-loss distance distribution."""
    print("\n" + "=" * 80)
    print("🔍 STOP-LOSS DISTANCE ANALYSIS")
    print("=" * 80)
    
    sl_trades = [t for t in trades if t.get("stop_loss") and t.get("entry_price")]
    
    if not sl_trades:
        print("  No trades with stop-loss data")
        return
    
    distances = []
    for t in sl_trades:
        entry = t.get("entry_price", 0) or 0
        sl = t.get("stop_loss", 0) or 0
        if entry > 0 and sl > 0:
            dist = abs(entry - sl) / entry * 100
            distances.append((dist, t))
    
    if not distances:
        print("  No valid distance calculations")
        return
    
    # Buckets
    buckets = {"<0.5%": [], "0.5-1%": [], "1-2%": [], "2-3%": [], ">3%": []}
    for dist, t in distances:
        if dist < 0.5:
            buckets["<0.5%"].append(t)
        elif dist < 1:
            buckets["0.5-1%"].append(t)
        elif dist < 2:
            buckets["1-2%"].append(t)
        elif dist < 3:
            buckets["2-3%"].append(t)
        else:
            buckets[">3%"].append(t)
    
    print(f"\n{'SL Distance':<15} {'Count':>6} {'Win%':>8} {'Avg PnL':>10} {'Total PnL':>12}")
    print("-" * 55)
    
    for bucket, ts in buckets.items():
        if not ts:
            continue
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100 if n else 0
        avg = sum(pnls) / n if n else 0
        total = sum(pnls)
        
        emoji = "🟢" if total > 0 else "🔴"
        print(f"{emoji} {bucket:<13} {n:>6} {wr:>7.1f}% {avg:>10.2f} {total:>12.2f}")


def analyze_holding_time(trades):
    """Analyze performance by holding time."""
    print("\n" + "=" * 80)
    print("🔍 HOLDING TIME ANALYSIS")
    print("=" * 80)
    
    timed_trades = [t for t in trades if t.get("hold_minutes") and t["hold_minutes"] > 0]
    
    if not timed_trades:
        print("  No trades with holding time data")
        return
    
    buckets = {
        "<15min": [], "15-30min": [], "30-60min": [], 
        "1-2hr": [], "2-4hr": [], ">4hr": []
    }
    
    for t in timed_trades:
        mins = t["hold_minutes"]
        if mins < 15:
            buckets["<15min"].append(t)
        elif mins < 30:
            buckets["15-30min"].append(t)
        elif mins < 60:
            buckets["30-60min"].append(t)
        elif mins < 120:
            buckets["1-2hr"].append(t)
        elif mins < 240:
            buckets["2-4hr"].append(t)
        else:
            buckets[">4hr"].append(t)
    
    print(f"\n{'Holding Time':<15} {'Count':>6} {'Win%':>8} {'Avg PnL':>10} {'Total PnL':>12}")
    print("-" * 55)
    
    for bucket, ts in buckets.items():
        if not ts:
            continue
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100 if n else 0
        avg = sum(pnls) / n if n else 0
        total = sum(pnls)
        
        emoji = "🟢" if total > 0 else "🔴"
        print(f"{emoji} {bucket:<13} {n:>6} {wr:>7.1f}% {avg:>10.2f} {total:>12.2f}")


def analyze_realized_r(trades):
    """Analyze realized R-multiples."""
    print("\n" + "=" * 80)
    print("🔍 REALIZED R-MULTIPLE ANALYSIS")
    print("=" * 80)
    
    r_trades = [t for t in trades if t.get("realized_r") is not None and t["realized_r"] != 0]
    
    if not r_trades:
        print("  No trades with realized R data")
        return
    
    r_vals = [t["realized_r"] for t in r_trades]
    
    print(f"\n  Trades with R data: {len(r_trades)}")
    print(f"  Average R: {sum(r_vals)/len(r_vals):.2f}")
    print(f"  Median R: {sorted(r_vals)[len(r_vals)//2]:.2f}")
    print(f"  Min R: {min(r_vals):.2f}")
    print(f"  Max R: {max(r_vals):.2f}")
    
    # R distribution
    buckets = {"<-2R": [], "-2 to -1R": [], "-1 to 0R": [], "0 to 1R": [], "1 to 2R": [], ">2R": []}
    for r in r_vals:
        if r < -2:
            buckets["<-2R"].append(r)
        elif r < -1:
            buckets["-2 to -1R"].append(r)
        elif r < 0:
            buckets["-1 to 0R"].append(r)
        elif r < 1:
            buckets["0 to 1R"].append(r)
        elif r < 2:
            buckets["1 to 2R"].append(r)
        else:
            buckets[">2R"].append(r)
    
    print(f"\n  R Distribution:")
    for bucket, vals in buckets.items():
        if vals:
            print(f"    {bucket}: {len(vals)} trades ({len(vals)/len(r_vals)*100:.1f}%)")


def analyze_equity_curve(trades):
    """Analyze equity curve and drawdown."""
    print("\n" + "=" * 80)
    print("🔍 EQUITY CURVE & DRAWDOWN ANALYSIS")
    print("=" * 80)
    
    pnls = [t.get("pnl", 0) or 0 for t in trades]
    
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    dd_start = 0
    dd_end = 0
    current_dd_start = 0
    
    equity = [0]
    for i, p in enumerate(pnls):
        cum += p
        equity.append(cum)
        if cum > peak:
            peak = cum
            current_dd_start = i + 1
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
            dd_start = current_dd_start
            dd_end = i + 1
    
    print(f"\n  Starting Equity: $0.00")
    print(f"  Current Equity: ${cum:.2f}")
    print(f"  Peak Equity: ${peak:.2f}")
    print(f"  Max Drawdown: ${max_dd:.2f}")
    print(f"  Max DD Period: Trade {dd_start} to {dd_end}")
    
    # Consecutive losses
    max_consecutive = 0
    current_consecutive = 0
    for p in pnls:
        if p < 0:
            current_consecutive += 1
            max_consecutive = max(max_consecutive, current_consecutive)
        else:
            current_consecutive = 0
    
    print(f"  Max Consecutive Losses: {max_consecutive}")


def generate_root_cause(trades):
    """Generate root cause analysis."""
    print("\n" + "=" * 80)
    print("🎯 ROOT CAUSE ANALYSIS")
    print("=" * 80)
    
    pnls = [t.get("pnl", 0) or 0 for t in trades]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    
    win_rate = len(wins) / n * 100 if n else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    gross_profit = sum(wins) if wins else 0
    gross_loss = sum(losses) if losses else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else 0
    expectancy = (win_rate/100 * avg_win) - ((100-win_rate)/100 * avg_loss)
    
    print(f"\n  📊 Current State:")
    print(f"     Win Rate: {win_rate:.1f}%")
    print(f"     Avg Win: ${avg_win:.2f}")
    print(f"     Avg Loss: ${avg_loss:.2f}")
    print(f"     Profit Factor: {pf:.2f}")
    print(f"     Expectancy: ${expectancy:.2f}")
    
    print(f"\n  🔍 Diagnosis:")
    
    issues = []
    
    if win_rate < 40:
        issues.append(("LOW WIN RATE", f"Win rate {win_rate:.1f}% is below 40%. Strategy needs better entry filters."))
    
    if avg_loss > avg_win * 1.5:
        issues.append(("ASYMMETRIC RISK", f"Avg loss ${avg_loss:.2f} is {avg_loss/avg_win:.1f}x avg win ${avg_win:.2f}. Stops may be too tight or exits too early."))
    
    if pf < 1.0:
        issues.append(("NEGATIVE EDGE", f"Profit Factor {pf:.2f} < 1.0. Strategy is losing money overall."))
    
    # Check exit reasons
    sl_count = sum(1 for t in trades if (t.get("exit_reason", "") or "").lower() in ["stop_loss", "trailing_stop"])
    tp_count = sum(1 for t in trades if (t.get("exit_reason", "") or "").lower() in ["take_profit", "tp1", "tp2", "tp3"])
    
    if sl_count > tp_count * 2:
        issues.append(("STOP-LOSS DOMINANCE", f"{sl_count} stops vs {tp_count} take-profits. Too many trades hitting stops."))
    
    # Check MFE
    mfe_trades = [t for t in trades if t.get("mfe_pct") and t["mfe_pct"] > 0]
    if mfe_trades:
        positive_mfe = sum(1 for t in mfe_trades if t["mfe_pct"] > 0)
        if positive_mfe / len(mfe_trades) > 0.5:
            issues.append(("PREMATURE STOPS", f"{positive_mfe}/{len(mfe_trades)} trades went positive before stopping. Stops may be too tight."))
    
    if not issues:
        print("     No critical issues detected (but sample size is small)")
    
    for i, (title, desc) in enumerate(issues, 1):
        print(f"\n     {i}. ❌ {title}")
        print(f"        {desc}")
    
    print(f"\n  💡 Recommendations:")
    
    recs = []
    if win_rate < 40:
        recs.append("Increase min_rr threshold to reduce low-quality signals")
    if avg_loss > avg_win:
        recs.append("Widen stop-loss or implement better trailing stops")
    if sl_count > tp_count:
        recs.append("Review entry timing — many trades reverse after entry")
    if n < 50:
        recs.append("Collect more trades before making parameter changes")
    if pf < 1.0:
        recs.append("Run parameter optimization to find profitable configurations")
    
    for i, rec in enumerate(recs, 1):
        print(f"     {i}. {rec}")


def main():
    print("=" * 80)
    print("🔬 STRATEGY FORENSIC ANALYSIS")
    print(f"   Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)
    
    trades = get_all_trades()
    print(f"\n📊 Total closed trades: {len(trades)}")
    
    if not trades:
        print("❌ No trades found in database")
        return
    
    analyze_exit_reasons(trades)
    analyze_symbols(trades)
    analyze_sessions(trades)
    analyze_regimes(trades)
    analyze_direction(trades)
    analyze_confidence(trades)
    analyze_stop_loss(trades)
    analyze_sl_distance(trades)
    analyze_holding_time(trades)
    analyze_realized_r(trades)
    analyze_equity_curve(trades)
    generate_root_cause(trades)
    
    print("\n" + "=" * 80)
    print("✅ FORENSIC ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
