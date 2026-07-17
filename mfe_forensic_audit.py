"""
MFE Forensic Audit — Validate stored MFE against raw candle data.

For each closed trade, fetches the actual kline data during the trade's
lifetime and calculates what MFE *should have been*, then compares
with the stored MFE in the database.
"""
import sqlite3
import time
import requests
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent / "packages" / "ai-engine" / "data" / "institutional_v1.db"

def get_binance_klines(symbol: str, start_ms: int, end_ms: int, interval: str = "1m", limit: int = 1000):
    """Fetch klines from Binance API."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    all_klines = []
    current_start = start_ms
    
    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": limit,
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            klines = resp.json()
            if not klines:
                break
            all_klines.extend(klines)
            current_start = klines[-1][0] + 1  # Next candle start
            time.sleep(0.1)  # Rate limit
        except Exception as e:
            print(f"  ⚠️ API error for {symbol}: {e}")
            break
    
    return all_klines


def calculate_mfe_from_klines(klines, side: str, entry_price: float):
    """Calculate MFE from raw kline data.
    
    For LONG: MFE = (highest_high - entry) / entry * 100
    For SHORT: MFE = (entry - lowest_low) / entry * 100
    """
    if not klines or entry_price <= 0:
        return 0.0, 0.0, None, None
    
    best_mfe = 0.0
    best_mae = 0.0
    best_mfe_time = None
    best_mae_time = None
    
    for k in klines:
        # kline format: [open_time, open, high, low, close, volume, ...]
        open_time = k[0]
        high = float(k[2])
        low = float(k[3])
        
        if side == "LONG":
            # MFE: how high did price go above entry?
            mfe = (high - entry_price) / entry_price * 100
            # MAE: how low did price go below entry?
            mae = (entry_price - low) / entry_price * 100
        else:  # SHORT
            # MFE: how low did price go below entry?
            mfe = (entry_price - low) / entry_price * 100
            # MAE: how high did price go above entry?
            mae = (high - entry_price) / entry_price * 100
        
        if mfe > best_mfe:
            best_mfe = mfe
            best_mfe_time = open_time
        if mae > best_mae:
            best_mae = mae
            best_mae_time = open_time
    
    return best_mfe, best_mae, best_mfe_time, best_mae_time


def main():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    
    # Get all closed EMA V5 trades
    cur.execute("""
        SELECT id, symbol, side, entry_price, stop_loss, take_profit,
               pnl, exit_reason, mfe_pct, mae_pct,
               opened_at, closed_at, hold_minutes,
               (closed_at - opened_at) / 3600.0 as hours_held
        FROM positions_archive 
        WHERE strategy_version = 'ema_v5' AND status = 'closed'
        ORDER BY opened_at
    """)
    
    trades = cur.fetchall()
    print(f"{'='*70}")
    print(f"MFE FORENSIC AUDIT — Validating {len(trades)} closed trades")
    print(f"{'='*70}\n")
    
    results = []
    discrepancies = []
    excluded = []
    
    for t in trades:
        (tid, sym, side, entry, sl, tp, pnl, exit_r, 
         stored_mfe, stored_mae, opened, closed, mins, hours) = t
        
        if not opened or not closed:
            excluded.append({"symbol": sym, "reason": "missing timestamps"})
            continue
        
        # Convert to milliseconds for Binance API
        start_ms = int(opened * 1000)
        end_ms = int(closed * 1000)
        
        # Skip trades held less than 60 seconds (klines are 1m minimum)
        hold_seconds = closed - opened
        if hold_seconds < 60:
            excluded.append({"symbol": sym, "reason": f"hold time {hold_seconds:.1f}s < 60s (below kline resolution)"})
            print(f"⏭️  {sym:<12} {side:<6} EXCLUDED — hold time {hold_seconds:.1f}s < 60s")
            continue
        
        # Fetch 1-minute klines during trade lifetime
        klines = get_binance_klines(sym, start_ms, end_ms, interval="1m", limit=1500)
        
        if not klines:
            print(f"  ⚠️ {sym} {side}: No kline data available")
            continue
        
        # Calculate actual MFE from candles
        actual_mfe, actual_mae, mfe_time, mae_time = calculate_mfe_from_klines(
            klines, side, entry
        )
        
        # Compare
        mfe_diff = abs(actual_mfe - (stored_mfe or 0))
        mae_diff = abs(actual_mae - (stored_mae or 0))
        
        result = {
            "id": tid,
            "symbol": sym,
            "side": side,
            "entry": entry,
            "pnl": pnl,
            "exit_reason": exit_r,
            "hours_held": hours,
            "stored_mfe": stored_mfe or 0,
            "actual_mfe": actual_mfe,
            "mfe_diff": mfe_diff,
            "stored_mae": stored_mae or 0,
            "actual_mae": actual_mae,
            "mae_diff": mae_diff,
            "candles": len(klines),
        }
        results.append(result)
        
        # Flag significant discrepancies (>0.5% difference)
        if mfe_diff > 0.5:
            discrepancies.append(result)
        
        # Print result
        status = "✅" if mfe_diff < 0.5 else "❌"
        print(f"{status} {sym:<12} {side:<6} held={hours:.1f}h  exit={exit_r:<20}")
        print(f"   Stored MFE: {(stored_mfe or 0):.2f}%  Actual MFE: {actual_mfe:.2f}%  Diff: {mfe_diff:.2f}%")
        print(f"   Stored MAE: {(stored_mae or 0):.2f}%  Actual MAE: {actual_mae:.2f}%  Diff: {mae_diff:.2f}%")
        if pnl:
            print(f"   PnL: ${pnl:.2f}")
        print()
        
        time.sleep(0.2)  # Rate limit between symbols
    
    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"Total trades in database: {len(trades)}")
    print(f"Trades analyzed: {len(results)}")
    print(f"Trades excluded: {len(excluded)}")
    if excluded:
        print(f"\nExcluded trades:")
        for e in excluded:
            print(f"  {e['symbol']}: {e['reason']}")
    print(f"Discrepancies (>0.5% MFE diff): {len(discrepancies)}")
    
    # Reconciliation identity
    if len(trades) == len(results) + len(excluded):
        print(f"\n✅ RECONCILIATION OK: {len(trades)} = {len(results)} analyzed + {len(excluded)} excluded")
    else:
        print(f"\n❌ RECONCILIATION FAIL: {len(trades)} ≠ {len(results)} + {len(excluded)}")
    
    if discrepancies:
        print(f"\n{'='*70}")
        print(f"SIGNIFICANT DISCREPANCIES")
        print(f"{'='*70}")
        for d in discrepancies:
            print(f"  {d['symbol']:<12} {d['side']:<6} stored={d['stored_mfe']:.2f}% actual={d['actual_mfe']:.2f}% diff={d['mfe_diff']:.2f}%")
    
    # Trades with stored MFE=0 but actual MFE>0
    zero_mfe_with_actual = [r for r in results if r['stored_mfe'] == 0 and r['actual_mfe'] > 0.5]
    if zero_mfe_with_actual:
        print(f"\n{'='*70}")
        print(f"STORED MFE=0 BUT ACTUAL MFE>0.5% (likely tracking bug)")
        print(f"{'='*70}")
        for d in zero_mfe_with_actual:
            print(f"  {d['symbol']:<12} {d['side']:<6} actual_mfe={d['actual_mfe']:.2f}%  exit={d['exit_reason']}")
    
    conn.close()


if __name__ == "__main__":
    main()
