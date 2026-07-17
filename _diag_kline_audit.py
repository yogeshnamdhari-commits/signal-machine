#!/usr/bin/env python3
"""
DIAGNOSTIC: Kline Data Quality Audit
====================================
Read-only script. Checks 4 things:
  1. Kline count (does the engine have enough for EMA200 warmup?)
  2. Duplicate timestamps (are WS duplicates inflating the list?)
  3. History continuity (are there gaps in the kline sequence?)
  4. EMA verification (do scanner EMAs match fresh computation?)

Usage:
  python3 _diag_kline_audit.py
"""
from __future__ import annotations
import json
import time
import requests
from pathlib import Path

BRIDGE_DIR = Path("packages/ai-engine/data/bridge")
SYMBOLS_TO_CHECK = ["CELOUSDT", "AVAXUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
REST_URL = "https://fapi.binance.com/fapi/v1/klines"

# ── Scanner's EMA function (exact copy from utils.py) ──
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


def fetch_klines(symbol: str, limit: int = 300) -> list:
    """Fetch klines from Binance REST API."""
    try:
        resp = requests.get(REST_URL, params={
            "symbol": symbol, "interval": "5m", "limit": limit
        }, timeout=10)
        return resp.json()
    except Exception as e:
        print(f"  ❌ REST API error for {symbol}: {e}")
        return []


def check_kline_quality(closes, open_times, symbol):
    """Check for duplicates and gaps in kline data."""
    dups = 0
    gaps = 0
    for i in range(1, len(open_times)):
        if open_times[i] == open_times[i - 1]:
            dups += 1
        elif open_times[i] - open_times[i - 1] > 300_000:
            gaps += 1
    return dups, gaps


def main():
    print("=" * 70)
    print("KLINE DATA QUALITY AUDIT")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 70)

    # ── CHECK 1: Read EMA_V5 bridge for current scanner state ──
    print("\n── CHECK 1: Scanner Bridge State ──")
    try:
        with open(BRIDGE_DIR / "ema_v5.json") as f:
            ema_bridge = json.load(f)
        signals = ema_bridge.get("active_signals", [])
        print(f"  Active EMA_V5 signals: {len(signals)}")
        for s in signals:
            print(f"    {s.get('symbol')} {s.get('side')} conf={s.get('confidence')} entry={s.get('entry')}")
            ema_d = s.get("ema_data", {})
            if ema_d:
                print(f"      EMA20={ema_d.get('ema20', 'N/A')} EMA50={ema_d.get('ema50', 'N/A')} "
                      f"EMA144={ema_d.get('ema144', 'N/A')} EMA200={ema_d.get('ema200', 'N/A')}")
    except FileNotFoundError:
        print("  ⚠️  ema_v5.json not found")
    except Exception as e:
        print(f"  ❌ Error: {e}")

    # ── CHECK 2: REST API kline quality for each symbol ──
    print("\n── CHECK 2: REST API Kline Quality (300 candles) ──")
    for sym in SYMBOLS_TO_CHECK:
        klines = fetch_klines(sym, 300)
        if not klines:
            continue

        closes = [float(k[4]) for k in klines]
        open_times = [int(k[0]) for k in klines]
        dups, gaps = check_kline_quality(closes, open_times, sym)

        first_time = time.strftime('%H:%M', time.gmtime(open_times[0] / 1000))
        last_time = time.strftime('%H:%M', time.gmtime(open_times[-1] / 1000))

        print(f"\n  {sym}: {len(klines)} candles ({first_time} - {last_time} UTC)")
        print(f"    Duplicates: {dups} {'✅' if dups == 0 else '❌'}")
        print(f"    Gaps (>5min): {gaps} {'✅' if gaps == 0 else '❌'}")

        # ── CHECK 3: EMA verification ──
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

        print(f"    EMA20:  {e20[idx]:.8f}")
        print(f"    EMA50:  {e50[idx]:.8f}")
        print(f"    EMA144: {e144[idx]:.8f}")
        print(f"    EMA200: {e200[idx]:.8f}")
        print(f"    Chain:  {chain}")
        print(f"    Close:  {closes[idx]:.8f}")

        # Compare EMA144 vs EMA200 spread (should be meaningful in a trend)
        spread = abs(e144[idx] - e200[idx]) / e200[idx] * 100
        print(f"    EMA144-200 spread: {spread:.3f}% {'✅' if spread > 0.1 else '⚠️ very narrow'}")

        # Check for collapse pattern (EMA144 ≈ EMA200 ≈ close)
        e144_close_delta = abs(e144[idx] - closes[idx]) / closes[idx] * 100
        e200_close_delta = abs(e200[idx] - closes[idx]) / closes[idx] * 100
        collapsed = e144_close_delta < 0.5 and e200_close_delta < 0.5
        if collapsed:
            print(f"    ⚠️  EMA144/200 COLLAPSE PATTERN: EMA144-Δ={e144_close_delta:.3f}% EMA200-Δ={e200_close_delta:.3f}%")
        else:
            print(f"    ✅ Normal EMA spread: EMA144-Δ={e144_close_delta:.3f}% EMA200-Δ={e200_close_delta:.3f}%")

    # ── CHECK 4: Sensitivity analysis — how much do EMAs change with candle count? ──
    print(f"\n── CHECK 3: EMA Sensitivity to Candle Count (CELOUSDT) ──")
    klines = fetch_klines("CELOUSDT", 300)
    if klines:
        closes = [float(k[4]) for k in klines]
        print(f"  Total candles available: {len(closes)}")
        print(f"  Signal EMA144: 0.05835170 (from scanner record)")
        print()

        for n in [300, 250, 220, 200, 150, 100, 75]:
            subset = closes[-n:] if n <= len(closes) else closes
            idx = len(subset) - 1
            e144 = ema(subset, 144)
            e200 = ema(subset, 200) if n >= 200 else [0]
            e144_v = e144[idx] if len(e144) > idx else 0
            e200_v = e200[idx] if n >= 200 else None

            d144 = abs(e144_v - 0.05835170) / 0.05835170 * 100
            d200 = abs(e200_v - 0.05833882) / 0.05833882 * 100 if e200_v else None

            e200_str = f"Δ{d200:.3f}%" if d200 is not None else "N/A"
            e200_val_str = f"{e200_v:.8f}" if e200_v is not None else "N/A"
            match = "✅" if d144 < 0.1 and (d200 is not None and d200 < 0.1) else ""

            print(f"  n={n:>3}: EMA144={e144_v:.8f} Δ{d144:.3f}% | EMA200={e200_val_str:>12} {e200_str} {match}")

    print(f"\n{'='*70}")
    print("DIAGNOSTIC COMPLETE — Review results above")
    print("=" * 70)


if __name__ == "__main__":
    main()
