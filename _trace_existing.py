"""
Trace existing BUY_MODE candidates through their lifecycle.

This script attaches to the 44 BUY_MODE and 40 SELL_MODE symbols
already present and traces their pipeline progression.

Usage:
    python3 _trace_existing.py
    
    # Trace a specific symbol
    python3 _trace_existing.py --symbol ETHUSDT
"""
import sys, json, time, requests
sys.path.insert(0, 'packages/ai-engine')

from scanner.ema_v5.state_manager import StateManager
from scanner.ema_v5.utils import is_bullish_engulfing, is_hammer, is_bullish_pin_bar, price_touches_ema
from scanner.ema_v5.config import ema_v5_config

def trace_symbol(sym: str) -> None:
    """Trace one symbol through the complete pipeline."""
    print(f"\n{'='*60}")
    print(f"  LIFECYCLE TRACE: {sym}")
    print(f"{'='*60}")
    
    # Get current state
    sm = StateManager()
    all_states = sm.get_all_states()
    sdata = all_states.get(sym, {})
    state = sdata.get("state", "NOT_TRACKED")
    prev = sdata.get("previous", "N/A")
    last_update = sdata.get("last_update", 0)
    age = time.time() - last_update if last_update else 0
    
    print(f"\n1. CURRENT STATE")
    print(f"   State: {state}")
    print(f"   Previous: {prev}")
    print(f"   Age: {age:.0f}s ({age/3600:.1f}h)")
    
    # Fetch klines
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": sym, "interval": "5m", "limit": 250}
    try:
        resp = requests.get(url, params=params, timeout=5)
        klines = resp.json()
    except Exception as e:
        print(f"   Error fetching klines: {e}")
        return
    
    if not klines or len(klines) < 220:
        print(f"   Insufficient data: {len(klines)} candles")
        return
    
    # Compute EMAs
    closes = [float(k[4]) for k in klines]
    def ema(data, period):
        result = []
        multiplier = 2 / (period + 1)
        prev_ema = sum(data[:period]) / period
        for i in range(len(data)):
            if i < period - 1: result.append(float("nan"))
            elif i == period - 1: result.append(prev_ema)
            else:
                ema_val = (data[i] - prev_ema) * multiplier + prev_ema
                result.append(ema_val)
                prev_ema = ema_val
        return result
    
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    
    cfg_pullback = ema_v5_config.pullback
    cfg_candle = ema_v5_config.candle
    
    # Current EMA values
    print(f"\n2. CURRENT EMA VALUES")
    print(f"   Close:  {closes[-1]:.4f}")
    print(f"   EMA20:  {e20[-1]:.4f}")
    print(f"   EMA50:  {e50[-1]:.4f}")
    
    # Pullback analysis
    print(f"\n3. PULLBACK ANALYSIS")
    
    # Check last 60 candles for pullback touches
    touches = []
    for i in range(-60, 0):
        if i < -len(klines) or i < -len(e20) or i < -2:
            continue
        recent = klines[max(0, i-2):i+1]
        touch = False
        for ci, candle in enumerate(reversed(recent)):
            low = float(candle[3])
            close = float(candle[4])
            candle_idx = i - ci
            if candle_idx < -len(e20) or not e20[candle_idx] or e20[candle_idx] <= 0:
                continue
            ema_val = e20[candle_idx]
            if low <= ema_val and close >= ema_val:
                if price_touches_ema(low, ema_val, cfg_pullback.touch_tolerance_pct):
                    touch = True
                    touches.append(i)
                    break
    
    print(f"   Pullback touches (last 60 candles): {len(touches)}")
    if touches:
        print(f"   At scans: {touches}")
    
    # Check for candle patterns at each pullback touch
    print(f"\n4. CANDLE PATTERN ANALYSIS")
    patterns_found = []
    for i in touches:
        if i < -len(klines) or i < -2:
            continue
        o1 = float(klines[i-1][1])
        c1 = float(klines[i-1][4])
        o2 = float(klines[i][1])
        h2 = float(klines[i][2])
        l2 = float(klines[i][3])
        cl2 = float(klines[i][4])
        
        body2 = abs(cl2 - o2)
        range2 = h2 - l2 if h2 > l2 else 0.0001
        body_ratio = body2 / range2
        lower_wick = min(o2, cl2) - l2
        wick_ratio = lower_wick / body2 if body2 > 0 else 0
        
        engulfing = is_bullish_engulfing(o1, c1, o2, cl2, cfg_candle.body_ratio_min)
        hammer = is_hammer(o2, h2, l2, cl2, cfg_candle.wick_ratio_min)
        pin_bar = is_bullish_pin_bar(o2, h2, l2, cl2, cfg_candle.wick_ratio_min)
        pattern = engulfing or hammer or pin_bar
        
        if pattern:
            patterns_found.append(i)
            print(f"   Scan {i}: PATTERN FOUND (engulf={engulfing} hammer={hammer} pin={pin_bar})")
        else:
            print(f"   Scan {i}: no pattern (body={body_ratio:.3f} wick={wick_ratio:.2f})")
    
    if not patterns_found:
        print(f"   No candle patterns found at pullback touches")
    
    # Summary
    print(f"\n5. SUMMARY")
    print(f"   Pullback touches: {len(touches)}")
    print(f"   Candle patterns: {len(patterns_found)}")
    print(f"   Both on same scan: {len(patterns_found)}")
    
    if state == "WAITING_PULLBACK":
        if touches and patterns_found:
            print(f"   → Pipeline SHOULD transition (both conditions met)")
            print(f"   → But transition may not be executing")
        elif touches:
            print(f"   → Pullback touches exist but candle patterns missing")
            print(f"   → Candidate waiting for candle pattern")
        else:
            print(f"   → No pullback touches detected")
            print(f"   → Candidate waiting for price to reach EMA")
    
    print(f"\n{'='*60}")


def main():
    symbol = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--symbol" and i + 1 < len(sys.argv) - 1:
            symbol = sys.argv[i + 2]
    
    sm = StateManager()
    all_states = sm.get_all_states()
    
    if symbol:
        trace_symbol(symbol)
    else:
        # Get all BUY_MODE and SELL_MODE symbols
        buy_syms = [sym for sym, sdata in all_states.items() 
                    if isinstance(sdata, dict) and sdata.get("state") == "BUY_MODE"]
        sell_syms = [sym for sym, sdata in all_states.items() 
                     if isinstance(sdata, dict) and sdata.get("state") == "SELL_MODE"]
        
        print(f"Tracing {len(buy_syms)} BUY_MODE and {len(sell_syms)} SELL_MODE symbols")
        
        # Trace first 3 of each
        for sym in buy_syms[:3]:
            trace_symbol(sym)
        
        for sym in sell_syms[:3]:
            trace_symbol(sym)


if __name__ == "__main__":
    main()
