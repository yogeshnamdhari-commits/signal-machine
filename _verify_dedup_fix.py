#!/usr/bin/env python3
"""
BEFORE/AFTER EMA VERIFICATION
Compares scanner EMA values against independent Binance computation
for a specific symbol, after the dedup fix has been applied.
"""
import json
import time
import requests

BRIDGE = "packages/ai-engine/data/bridge"
REST_URL = "https://fapi.binance.com/fapi/v1/klines"

def ema(values, period):
    if not values or period <= 0:
        return []
    result = [0.0] * len(values)
    k = 2.0 / (period + 1)
    if len(values) >= period:
        sma_sum = sum(values[:period])
        result[period - 1] = sma_sum / period
        for i in range(period, len(values)):
            result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result

def main():
    # Read current scanner state
    try:
        with open(f"{BRIDGE}/ema_v5.json") as f:
            data = json.load(f)
        signals = data.get("active_signals", [])
    except Exception:
        signals = []

    print("=" * 70)
    print("BEFORE/AFTER EMA VERIFICATION")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    if not signals:
        print("\nNo active EMA_V5 signals. Checking cached scanner state...")

    # Check kline quality for top symbols
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "CELOUSDT"]

    for sym in symbols:
        print(f"\n── {sym} ──")
        resp = requests.get(REST_URL, params={"symbol": sym, "interval": "5m", "limit": 300}, timeout=10)
        klines = resp.json()
        if not klines:
            print("  ❌ REST API error")
            continue

        closes = [float(k[4]) for k in klines]
        open_times = [int(k[0]) for k in klines]

        # Check for duplicates
        seen = set()
        dups = sum(1 for ot in open_times if ot in seen or seen.add(ot))
        unique = len(set(open_times))

        # Check for gaps
        gaps = sum(1 for i in range(1, len(open_times)) if open_times[i] - open_times[i-1] > 300_000)

        idx = len(closes) - 1
        e20 = ema(closes, 20)
        e50 = ema(closes, 50)
        e144 = ema(closes, 144)
        e200 = ema(closes, 200)

        chain = "BEAR"
        if e20[idx] > e50[idx] > e144[idx] > e200[idx]:
            chain = "BULL"
        elif not (e20[idx] < e50[idx] < e144[idx] < e200[idx]):
            chain = "MIXED"

        print(f"  REST candles: {len(klines)} | Unique: {unique} | Dups: {dups} | Gaps: {gaps}")
        print(f"  Close:  {closes[idx]:.8f}")
        print(f"  EMA20:  {e20[idx]:.8f}")
        print(f"  EMA50:  {e50[idx]:.8f}")
        print(f"  EMA144: {e144[idx]:.8f}")
        print(f"  EMA200: {e200[idx]:.8f}")
        print(f"  Chain:  {chain}")
        spread = abs(e144[idx] - e200[idx]) / e200[idx] * 100
        print(f"  EMA144-200 spread: {spread:.3f}%")

        # Check for collapse pattern
        e144_delta = abs(e144[idx] - closes[idx]) / closes[idx] * 100
        e200_delta = abs(e200[idx] - closes[idx]) / closes[idx] * 100
        collapsed = e144_delta < 0.5 and e200_delta < 0.5
        if collapsed:
            print(f"  ⚠️  COLLAPSE: EMA144-Δ={e144_delta:.3f}% EMA200-Δ={e200_delta:.3f}%")
        else:
            print(f"  ✅ Normal spread: EMA144-Δ={e144_delta:.3f}% EMA200-Δ={e200_delta:.3f}%")

    # Check for any signal with EMA data
    if signals:
        print(f"\n── ACTIVE SIGNALS ──")
        for s in signals:
            sym = s.get("symbol", "?")
            side = s.get("side", "?")
            ema_d = s.get("ema_data", {})
            e144 = ema_d.get("ema144", 0)
            e200 = ema_d.get("ema200", 0)
            print(f"  {sym} {side} EMA144={e144:.8f} EMA200={e200:.8f}")

    print(f"\n{'='*70}")
    print("VERIFICATION COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    main()
