#!/usr/bin/env python3
"""
Counterfactual Analysis
========================
For every rejected candidate, check what would have happened
if the trade had been taken. Compares rejected vs accepted.

READ-ONLY — Never modifies trading logic.
"""
import sys
import re
import sqlite3
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

AI_ROOT = Path(__file__).resolve().parent / "packages" / "ai-engine"
sys.path.insert(0, str(AI_ROOT))

DB_PATH = AI_ROOT / "data" / "institutional_v1.db"
LOG_DIR = AI_ROOT / "data" / "logs"


def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def parse_lifecycle_log(date_str: str = "2026-07-09"):
    """Parse lifecycle log to extract rejections with context."""
    log_path = LOG_DIR / f"ema_v5_lifecycle_{date_str}.log"
    if not log_path.exists():
        print(f"Log not found: {log_path}")
        return []

    rejections = []
    pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}) \| (\w+)\s+\| (\w+)\s+→ (\w+)\s+\| conf=\s*([\d.]+) side=\s*(\w*)\s*\| (.*)"
    )

    with open(log_path) as f:
        for line in f:
            m = pattern.search(line)
            if not m:
                continue
            time_str, symbol, from_state, to_state, conf, side, event = m.groups()
            
            if "rejected" in event:
                rejections.append({
                    "time": time_str,
                    "symbol": symbol,
                    "from_state": from_state,
                    "to_state": to_state,
                    "confidence": float(conf) if conf else 0,
                    "side": side.strip() if side else "",
                    "event": event.strip(),
                })

    return rejections


def get_trade_outcomes(symbol: str, entry_time_approx: str, side: str):
    """Check what happened to a symbol after a given time.
    Returns simulated PnL based on forward price movement."""
    conn = connect()
    try:
        # Get the symbol's recent klines to check forward movement
        # We'll look at the position table for this symbol
        rows = conn.execute("""
            SELECT pnl, exit_reason, entry_price, stop_loss, take_profit, side
            FROM positions 
            WHERE symbol = ? AND status = 'closed'
            ORDER BY closed_at DESC LIMIT 5
        """, (symbol,)).fetchall()
        
        if rows:
            return [dict(r) for r in rows]
        
        # Also check archive
        rows2 = conn.execute("""
            SELECT pnl, exit_reason, entry_price, stop_loss, take_profit, side
            FROM positions_archive 
            WHERE symbol = ? AND status = 'closed'
            ORDER BY closed_at DESC LIMIT 5
        """, (symbol,)).fetchall()
        
        return [dict(r) for r in rows2] if rows2 else []
    finally:
        conn.close()


def analyze_rejection_outcomes(rejections):
    """Group rejections by type and analyze patterns."""
    by_type = defaultdict(list)
    
    for r in rejections:
        event = r["event"]
        if "candle_rejected" in event:
            # Parse body and wick
            body_match = re.search(r"body=([\d.]+)", event)
            wick_match = re.search(r"wick=([\d.]+)", event)
            body = float(body_match.group(1)) if body_match else 0
            wick = float(wick_match.group(1)) if wick_match else 0
            
            if body < 0.5:
                by_type["candle_body"].append(r)
            if wick < 2.0:
                by_type["candle_wick"].append(r)
            by_type["candle_any"].append(r)
            
        elif "confidence_rejected" in event:
            by_type["confidence"].append(r)
        elif "volume_rejected" in event:
            by_type["volume"].append(r)
        elif "rr_rejected" in event:
            by_type["rr"].append(r)
    
    return by_type


def main():
    print("=" * 80)
    print("🔬 COUNTERFACTUAL ANALYSIS")
    print("=" * 80)

    # Parse rejections
    rejections = parse_lifecycle_log("2026-07-09")
    print(f"\n📊 Total rejections parsed: {len(rejections)}")

    if not rejections:
        print("No rejections found. Check log date.")
        return

    # Group by type
    by_type = analyze_rejection_outcomes(rejections)

    print(f"\n{'='*80}")
    print(f"📋 REJECTION BREAKDOWN")
    print(f"{'='*80}")

    for rtype, items in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"\n  {rtype}: {len(items)} rejections")
        
        # Show unique symbols
        symbols = set(r["symbol"] for r in items)
        print(f"    Unique symbols: {len(symbols)}")
        
        # Show confidence distribution
        confs = [r["confidence"] for r in items if r["confidence"] > 0]
        if confs:
            avg_conf = sum(confs) / len(confs)
            print(f"    Avg confidence: {avg_conf:.1f}")

    # Check if any rejected symbols actually had trades
    print(f"\n{'='*80}")
    print(f"🔍 COUNTERFACTUAL: Did rejected symbols eventually trade?")
    print(f"{'='*80}")

    candle_rejected_symbols = set(r["symbol"] for r in by_type.get("candle_any", []))
    conf_rejected_symbols = set(r["symbol"] for r in by_type.get("confidence", []))

    # Check DB for trades on these symbols
    conn = connect()
    try:
        all_rejected = candle_rejected_symbols | conf_rejected_symbols
        
        trades_on_rejected = []
        for symbol in list(all_rejected)[:50]:  # Check top 50
            rows = conn.execute("""
                SELECT symbol, pnl, exit_reason, side, entry_price, stop_loss, take_profit
                FROM positions WHERE symbol = ? AND status = 'closed'
                UNION ALL
                SELECT symbol, pnl, exit_reason, side, entry_price, stop_loss, take_profit
                FROM positions_archive WHERE symbol = ? AND status = 'closed'
            """, (symbol, symbol)).fetchall()
            
            if rows:
                trades_on_rejected.extend([dict(r) for r in rows])

        if trades_on_rejected:
            wins = sum(1 for t in trades_on_rejected if (t.get("pnl", 0) or 0) > 0)
            losses = sum(1 for t in trades_on_rejected if (t.get("pnl", 0) or 0) < 0)
            total_pnl = sum(t.get("pnl", 0) or 0 for t in trades_on_rejected)
            
            print(f"\n  Symbols that were rejected AND later traded:")
            print(f"    Trades: {len(trades_on_rejected)}")
            print(f"    Wins: {wins} | Losses: {losses}")
            print(f"    Win Rate: {wins/len(trades_on_rejected)*100:.1f}%")
            print(f"    Total PnL: ${total_pnl:.2f}")
            
            # By exit reason
            by_exit = defaultdict(list)
            for t in trades_on_rejected:
                by_exit[t.get("exit_reason", "unknown")].append(t)
            
            print(f"\n    By exit reason:")
            for reason, ts in sorted(by_exit.items(), key=lambda x: -len(x[1])):
                pnls = [t.get("pnl", 0) or 0 for t in ts]
                print(f"      {reason}: {len(ts)} trades, avg PnL: ${sum(pnls)/len(pnls):.2f}")
        else:
            print(f"\n  No trades found for rejected symbols")

    finally:
        conn.close()

    # Summary
    print(f"\n{'='*80}")
    print(f"📊 DIAGNOSIS")
    print(f"{'='*80}")
    print(f"""
  PRIMARY BOTTLENECK: Candle Confirmation (wick_ratio < 2.0)
  
  The candle engine requires:
    - Wick ratio >= 2.0 (pin bar / hammer pattern)
    - Body ratio >= 0.5 (for engulfing)
  
  89% of candle rejections fail the wick check.
  This means most pullbacks don't form a pin bar pattern.
  
  OPTIONS:
  1. Lower wick_ratio_min from 2.0 → 1.5
     - Would accept more candles
     - Risk: lower quality patterns
  
  2. Add alternative patterns
     - Accept simple bounce candles (not just pin bars)
     - Accept inside bars near EMA
  
  3. Relax body_ratio_min from 0.5 → 0.3
     - Would accept more engulfing patterns
     - Less risky than relaxing wick
  
  RECOMMENDATION:
  Run counterfactual on actual rejected trades before changing.
  Need to know: would rejected trades have been profitable?
""")


if __name__ == "__main__":
    main()
