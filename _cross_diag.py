"""
Cross Detection Diagnostic — Traces exactly why a crossover might be missed.

This script logs, for every symbol, the exact EMA values and cross detection
state so you can compare against TradingView.

Usage:
    python3 _cross_diag.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "packages" / "ai-engine"))

from scanner.ema_v5.cache import EMACache
from scanner.ema_v5.regime_engine import RegimeEngine
from scanner.ema_v5.config import ema_v5_config
from scanner.ema_v5.utils import ema_chain_aligned


def diagnose_symbol(cache: EMACache, regime_engine: RegimeEngine, symbol: str, klines: list):
    """Run full diagnostic for one symbol."""
    if not klines or len(klines) < ema_v5_config.ema.min_candles:
        print(f"  ❌ Insufficient candles: {len(klines) if klines else 0}/{ema_v5_config.ema.min_candles}")
        return

    ema_data = cache.update(symbol, klines)
    if not ema_data:
        print(f"  ❌ EMA computation failed")
        return

    e20 = ema_data.get("ema20", 0)
    e50 = ema_data.get("ema50", 0)
    e144 = ema_data.get("ema144", 0)
    e200 = ema_data.get("ema200", 0)
    e20p = ema_data.get("ema20_prev", 0)
    e50p = ema_data.get("ema50_prev", 0)
    close = ema_data.get("last_close", 0)
    e144_slope = ema_data.get("ema144_slope", 0)
    e200_slope = ema_data.get("ema200_slope", 0)

    # Chain alignment checks
    buy_chain_now = ema_chain_aligned(e20, e50, e144, e200, "BUY")
    buy_chain_prev = ema_chain_aligned(e20p, e50p, e144, e200, "BUY")
    sell_chain_now = ema_chain_aligned(e20, e50, e144, e200, "SELL")

    # Simple EMA20/EMA50 crossover detection
    cross_up_simple = e20p <= e50p and e20 > e50
    cross_down_simple = e20p >= e50p and e20 < e50

    # Regime evaluation
    regime_eval = regime_engine.evaluate(ema_data)
    regime = regime_eval.get("regime", "NO_TREND")
    reason = regime_eval.get("reason", "")

    # Print diagnostic
    print(f"  EMA20_prev={e20p:.4f}  EMA50_prev={e50p:.4f}")
    print(f"  EMA20_now ={e20:.4f}   EMA50_now ={e50:.4f}")
    print(f"  EMA144={e144:.4f}  EMA200={e200:.4f}")
    print(f"  Close={close:.4f}")
    print(f"  EMA144_slope={e144_slope:.6f}  EMA200_slope={e200_slope:.6f}")
    print()
    print(f"  Simple cross:  cross_up={cross_up_simple}  cross_down={cross_down_simple}")
    print(f"  Chain aligned: BUY={buy_chain_now}  SELL={sell_chain_now}")
    print(f"  Prev chain:    BUY_prev={buy_chain_prev}")
    print()
    print(f"  Regime: {regime}")
    print(f"  Reason: {reason}")
    print()

    # Explain why regime might be NO_TREND despite a simple cross
    if cross_up_simple and regime != "BUY_MODE":
        print(f"  ⚠️  SIMPLE CROSS DETECTED but regime={regime}")
        print(f"  Possible reasons:")
        if not buy_chain_now:
            print(f"    → Full chain NOT aligned (need ema20>ema50>ema144>ema200)")
            if e20 <= e50:
                print(f"      EMA20 ({e20:.4f}) <= EMA50 ({e50:.4f})")
            if e50 <= e144:
                print(f"      EMA50 ({e50:.4f}) <= EMA144 ({e144:.4f})")
            if e144 <= e200:
                print(f"      EMA144 ({e144:.4f}) <= EMA200 ({e200:.4f})")
        if e144_slope <= 0:
            print(f"    → EMA144 slope ({e144_slope:.6f}) <= 0")
        if e200_slope <= 0:
            print(f"    → EMA200 slope ({e200_slope:.6f}) <= 0")
        if close <= e144 or close <= e200:
            print(f"    → Price ({close:.4f}) not above EMA144 ({e144:.4f}) and EMA200 ({e200:.4f})")
        print()
        print(f"  The scanner requires ALL of these for BUY_MODE:")
        print(f"    1. ema20 > ema50 > ema144 > ema200  (full chain)")
        print(f"    2. ema144_slope > 0")
        print(f"    3. ema200_slope > 0")
        print(f"    4. close > ema144 AND close > ema200")
    elif not cross_up_simple and regime == "BUY_MODE":
        print(f"  ℹ️  No simple cross detected but already in BUY_MODE")
        print(f"  (Cross happened earlier, regime persists)")


def main():
    from binance_service import BinanceService

    binance = BinanceService()
    cache = EMACache()
    regime_engine = RegimeEngine()

    symbols = ["ETHUSDT", "BTCUSDT", "SOLUSDT"]

    for sym in symbols:
        print(f"\n{'='*60}")
        print(f"  {sym}")
        print(f"{'='*60}")
        try:
            klines = binance.get_klines(sym, "5m", ema_v5_config.ema.min_candles + 10)
            diagnose_symbol(cache, regime_engine, sym, klines)
        except Exception as e:
            print(f"  Error: {e}")


if __name__ == "__main__":
    main()
