#!/usr/bin/env python3
"""
Strategy Performance Diagnostics
=================================
Answers critical questions from 802 completed trades:
1. Which exit loses the most?
2. Which symbols lose consistently?
3. Which market regimes lose?
4. Which sessions lose?
5. Which confidence buckets actually make money?
6. Which candle patterns make money?
7. Which pullback types perform best?

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

def get_closed_trades():
    conn = connect()
    # Get from both positions and positions_archive
    rows = conn.execute("""
        SELECT * FROM positions WHERE status = 'closed' ORDER BY closed_at ASC
    """).fetchall()
    trades = [dict(r) for r in rows]
    
    rows2 = conn.execute("""
        SELECT * FROM positions_archive WHERE status = 'closed' ORDER BY closed_at ASC
    """).fetchall()
    trades.extend([dict(r) for r in rows2])
    
    conn.close()
    
    # Deduplicate by symbol + closed_at
    seen = set()
    unique = []
    for t in trades:
        key = (t.get("symbol"), t.get("closed_at"))
        if key not in seen:
            seen.add(key)
            unique.append(t)
    
    return unique

def analyze_exit_reasons(trades):
    """Question 1: Which exit loses the most?"""
    print("\n" + "=" * 70)
    print("📊 QUESTION 1: WHICH EXIT LOSES THE MOST?")
    print("=" * 70)
    
    exits = defaultdict(list)
    for t in trades:
        reason = t.get("exit_reason", "unknown") or "unknown"
        exits[reason].append(t)
    
    print(f"\n{'EXIT REASON':<25} {'TRADES':>8} {'WIN%':>8} {'AVG PNL':>10} {'TOTAL PNL':>12} {'AVG R':>8}")
    print("-" * 75)
    
    for reason, ts in sorted(exits.items(), key=lambda x: -len(x[1])):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100 if pnls else 0
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        total_pnl = sum(pnls)
        
        rs = [t.get("realized_r", 0) or 0 for t in ts if t.get("realized_r")]
        avg_r = sum(rs) / len(rs) if rs else 0
        
        print(f"{reason:<25} {len(ts):>8} {wr:>7.1f}% {avg_pnl:>10.4f} {total_pnl:>12.2f} {avg_r:>8.2f}")

def analyze_symbols(trades):
    """Question 2: Which symbols lose consistently?"""
    print("\n" + "=" * 70)
    print("📊 QUESTION 2: WHICH SYMBOLS LOSE CONSISTENTLY?")
    print("=" * 70)
    
    symbols = defaultdict(list)
    for t in trades:
        sym = t.get("symbol", "unknown")
        symbols[sym].append(t)
    
    print(f"\n{'SYMBOL':<16} {'TRADES':>8} {'WIN%':>8} {'AVG PNL':>10} {'TOTAL PNL':>12} {'AVG R':>8}")
    print("-" * 75)
    
    # Sort by total PnL ascending (worst first)
    sym_data = []
    for sym, ts in symbols.items():
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100 if pnls else 0
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        total_pnl = sum(pnls)
        rs = [t.get("realized_r", 0) or 0 for t in ts if t.get("realized_r")]
        avg_r = sum(rs) / len(rs) if rs else 0
        sym_data.append((sym, len(ts), wr, avg_pnl, total_pnl, avg_r))
    
    # Sort by total PnL
    sym_data.sort(key=lambda x: x[4])
    
    for sym, count, wr, avg_pnl, total_pnl, avg_r in sym_data[:20]:
        emoji = "🔴" if total_pnl < 0 else "🟢"
        print(f"{emoji} {sym:<14} {count:>8} {wr:>7.1f}% {avg_pnl:>10.4f} {total_pnl:>12.2f} {avg_r:>8.2f}")

def analyze_regimes(trades):
    """Question 3: Which market regimes lose?"""
    print("\n" + "=" * 70)
    print("📊 QUESTION 3: WHICH MARKET REGIMES LOSE?")
    print("=" * 70)
    
    regimes = defaultdict(list)
    for t in trades:
        regime = t.get("regime", "unknown") or t.get("at_open_regime", "unknown") or "unknown"
        regimes[regime].append(t)
    
    print(f"\n{'REGIME':<20} {'TRADES':>8} {'WIN%':>8} {'AVG PNL':>10} {'TOTAL PNL':>12} {'AVG R':>8}")
    print("-" * 75)
    
    for regime, ts in sorted(regimes.items(), key=lambda x: -len(x[1])):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100 if pnls else 0
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        total_pnl = sum(pnls)
        rs = [t.get("realized_r", 0) or 0 for t in ts if t.get("realized_r")]
        avg_r = sum(rs) / len(rs) if rs else 0
        
        emoji = "🔴" if total_pnl < 0 else "🟢"
        print(f"{emoji} {regime:<18} {len(ts):>8} {wr:>7.1f}% {avg_pnl:>10.4f} {total_pnl:>12.2f} {avg_r:>8.2f}")

def analyze_sessions(trades):
    """Question 4: Which sessions lose?"""
    print("\n" + "=" * 70)
    print("📊 QUESTION 4: WHICH SESSIONS LOSE?")
    print("=" * 70)
    
    sessions = defaultdict(list)
    for t in trades:
        session = t.get("session", "unknown") or "unknown"
        sessions[session].append(t)
    
    print(f"\n{'SESSION':<20} {'TRADES':>8} {'WIN%':>8} {'AVG PNL':>10} {'TOTAL PNL':>12} {'AVG R':>8}")
    print("-" * 75)
    
    for session, ts in sorted(sessions.items(), key=lambda x: -len(x[1])):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100 if pnls else 0
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        total_pnl = sum(pnls)
        rs = [t.get("realized_r", 0) or 0 for t in ts if t.get("realized_r")]
        avg_r = sum(rs) / len(rs) if rs else 0
        
        emoji = "🔴" if total_pnl < 0 else "🟢"
        print(f"{emoji} {session:<18} {len(ts):>8} {wr:>7.1f}% {avg_pnl:>10.4f} {total_pnl:>12.2f} {avg_r:>8.2f}")

def analyze_confidence_buckets(trades):
    """Question 5: Which confidence buckets actually make money?"""
    print("\n" + "=" * 70)
    print("📊 QUESTION 5: WHICH CONFIDENCE BUCKETS MAKE MONEY?")
    print("=" * 70)
    
    buckets = {
        "< 50": [], "50-60": [], "60-70": [], "70-80": [], "80-90": [], "90-100": []
    }
    
    for t in trades:
        conf = (t.get("confidence", 0) or 0) * 100
        if conf < 50:
            buckets["< 50"].append(t)
        elif conf < 60:
            buckets["50-60"].append(t)
        elif conf < 70:
            buckets["60-70"].append(t)
        elif conf < 80:
            buckets["70-80"].append(t)
        elif conf < 90:
            buckets["80-90"].append(t)
        else:
            buckets["90-100"].append(t)
    
    print(f"\n{'BUCKET':<12} {'TRADES':>8} {'WIN%':>8} {'PF':>8} {'AVG PNL':>10} {'TOTAL PNL':>12} {'AVG R':>8}")
    print("-" * 75)
    
    for bucket_name, ts in buckets.items():
        if not ts:
            continue
        
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        
        wr = len(wins) / len(pnls) * 100 if pnls else 0
        gp = sum(wins) if wins else 0
        gl = sum(losses) if losses else 0
        pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        total_pnl = sum(pnls)
        
        rs = [t.get("realized_r", 0) or 0 for t in ts if t.get("realized_r")]
        avg_r = sum(rs) / len(rs) if rs else 0
        
        emoji = "🔴" if total_pnl < 0 else "🟢"
        print(f"{emoji} {bucket_name:<10} {len(ts):>8} {wr:>7.1f}% {pf:>8.2f} {avg_pnl:>10.4f} {total_pnl:>12.2f} {avg_r:>8.2f}")

def analyze_long_vs_short(trades):
    """Question 6: Long vs Short performance"""
    print("\n" + "=" * 70)
    print("📊 QUESTION 6: LONG vs SHORT PERFORMANCE")
    print("=" * 70)
    
    longs = [t for t in trades if t.get("side") == "LONG"]
    shorts = [t for t in trades if t.get("side") == "SHORT"]
    
    for label, ts in [("LONG", longs), ("SHORT", shorts)]:
        if not ts:
            continue
        
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls) * 100 if pnls else 0
        total_pnl = sum(pnls)
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        
        rs = [t.get("realized_r", 0) or 0 for t in ts if t.get("realized_r")]
        avg_r = sum(rs) / len(rs) if rs else 0
        
        emoji = "🔴" if total_pnl < 0 else "🟢"
        print(f"\n{emoji} {label}:")
        print(f"   Trades: {len(ts)}")
        print(f"   Win Rate: {wr:.1f}%")
        print(f"   Total PnL: ${total_pnl:.2f}")
        print(f"   Avg PnL: ${avg_pnl:.4f}")
        print(f"   Avg R: {avg_r:.2f}")

def analyze_drawdown(trades):
    """Question 7: Audit the drawdown calculation"""
    print("\n" + "=" * 70)
    print("📊 QUESTION 7: DRAWDOWN AUDIT")
    print("=" * 70)
    
    pnls = [t.get("pnl", 0) or 0 for t in trades]
    
    cum = 0
    peak = 0
    max_dd = 0
    max_dd_trade = 0
    
    for i, p in enumerate(pnls):
        cum += p
        peak = max(peak, cum)
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
            max_dd_trade = i
    
    print(f"\n   Total Trades: {len(pnls)}")
    print(f"   Cumulative PnL: ${sum(pnls):.2f}")
    print(f"   Peak Equity: ${peak:.2f}")
    print(f"   Max Drawdown: ${max_dd:.2f}")
    print(f"   Max DD at Trade #: {max_dd_trade}")
    
    # Calculate DD as percentage of peak
    if peak > 0:
        dd_pct = max_dd / peak * 100
        print(f"   Max DD %: {dd_pct:.1f}%")
    
    # Show the trades around max DD
    print(f"\n   Trades around max drawdown (#{max_dd_trade}):")
    start = max(0, max_dd_trade - 3)
    end = min(len(trades), max_dd_trade + 4)
    for i in range(start, end):
        t = trades[i]
        pnl = t.get("pnl", 0) or 0
        emoji = "✅" if pnl > 0 else "❌"
        print(f"   {emoji} #{i}: {t.get('symbol', '?')} {t.get('side', '?')} PnL=${pnl:.4f}")

def analyze_confidence_monotonicity(trades):
    """Question 8: Is confidence monotonic?"""
    print("\n" + "=" * 70)
    print("📊 QUESTION 8: CONFIDENCE MONOTONICITY CHECK")
    print("=" * 70)
    print("\n   Higher confidence should → Higher win rate & profit")
    print("   If not, the confidence model needs recalibration.\n")
    
    buckets = defaultdict(list)
    for t in trades:
        conf = (t.get("confidence", 0) or 0) * 100
        bucket = int(conf / 10) * 10  # Round to nearest 10
        buckets[bucket].append(t)
    
    print(f"   {'BUCKET':<12} {'TRADES':>8} {'WIN%':>8} {'PF':>8} {'AVG R':>8} {'MONOTONIC':>12}")
    print("   " + "-" * 65)
    
    prev_pf = 0
    monotonic = True
    for bucket in sorted(buckets.keys()):
        ts = buckets[bucket]
        if len(ts) < 3:
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
        
        is_mono = "✅" if pf >= prev_pf * 0.9 else "❌"
        if pf < prev_pf * 0.9:
            monotonic = False
        
        print(f"   {bucket}-{bucket+10:<6} {len(ts):>8} {wr:>7.1f}% {pf:>8.2f} {avg_r:>8.2f} {is_mono:>12}")
        prev_pf = pf
    
    print(f"\n   {'MONOTONIC' if monotonic else 'NOT MONOTONIC'}: ", end="")
    if monotonic:
        print("Confidence increases → Performance increases (GOOD)")
    else:
        print("Higher confidence does NOT consistently produce better trades (NEEDS RECALIBRATION)")

def main():
    print("=" * 70)
    print("🔬 STRATEGY PERFORMANCE DIAGNOSTICS")
    print("=" * 70)
    
    trades = get_closed_trades()
    print(f"\n📊 Loaded {len(trades)} closed trades")
    
    if len(trades) < 10:
        print("❌ Not enough trades for meaningful analysis")
        return
    
    analyze_exit_reasons(trades)
    analyze_symbols(trades)
    analyze_regimes(trades)
    analyze_sessions(trades)
    analyze_confidence_buckets(trades)
    analyze_long_vs_short(trades)
    analyze_drawdown(trades)
    analyze_confidence_monotonicity(trades)
    
    print("\n" + "=" * 70)
    print("📋 SUMMARY")
    print("=" * 70)
    print("""
Based on the analysis above, identify:

1. EXIT REASONS: Which exit type loses most? (SL, timeout, state regression?)
2. SYMBOLS: Which symbols should be excluded?
3. REGIMES: Which regimes should be avoided?
4. SESSIONS: Which sessions should be filtered?
5. CONFIDENCE: Is the confidence model working?
6. DIRECTION: Is LONG or SHORT performing better?

DO NOT change strategy logic until you have clear answers from the data.
""")
    print("=" * 70)

if __name__ == "__main__":
    main()
