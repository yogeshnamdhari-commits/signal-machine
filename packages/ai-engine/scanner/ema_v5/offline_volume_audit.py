"""
OFFLINE / HISTORICAL VOLUME OUTCOME AUDIT (research only — no live changes).

Replays the EXACT current live EMA_V5 pipeline over completed historical
5m candles and records every candle-qualified candidate (Volume PASS and
Volume REJECT), then simulates the forward exit to compute PnL / R / MFE / MAE.

Reuses the actual live sub-engines:
  - EMACache.update()       (exact indicator feed)
  - RegimeEngine.evaluate() (exact regime)
  - TrendEngine.evaluate()  (exact trend)
  - PullbackEngine.evaluate() (exact pullback)
  - CandleEngine.evaluate() (exact candle pattern)
  - VolumeEngine.evaluate() (exact volume gate)

No thresholds, weights, or logic are modified.

Usage:
    python offline_volume_audit.py [--symbols BTCUSDT,ETHUSDT] [--bars 1500] [--api-delay 0.2]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # packages/ai-engine

from scanner.ema_v5.cache import EMACache
from scanner.ema_v5.regime_engine import RegimeEngine
from scanner.ema_v5.trend_engine import TrendEngine
from scanner.ema_v5.pullback_engine import PullbackEngine
from scanner.ema_v5.candle_engine import CandleEngine
from scanner.ema_v5.volume_engine import VolumeEngine
from scanner.ema_v5.config import ema_v5_config

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
OUT_FILE = DATA_DIR / "historical_volume_audit.json"

# Default symbol list (liquid USDT-M perpetuals, varied volatility)
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "LINKUSDT", "AVAXUSDT", "LTCUSDT", "DOTUSDT", "NEARUSDT",
    "SUIUSDT", "ARBUSDT", "OPUSDT", "APTUSDT", "FILUSDT", "TIAUSDT",
    "SEIUSDT", "INJUSDT", "TAOUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT",
    "ORDIUSDT", "SATSUSDT", "ENAUSDT", "WUSDT", "BBUSDT", "ONDOUSDT",
]


def fetch_klines(symbol: str, interval: str = "5m", limit: int = 1500) -> list:
    """Fetch historical klines from Binance futures public REST."""
    url = (
        f"https://fapi.binance.com/fapi/v1/klines?"
        f"symbol={symbol}&interval={interval}&limit={limit}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return [
        {
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": int(k[6]),
        }
        for k in data
    ]


def simulate_exit(candles, entry_idx, side, entry, sl, tp1, max_hold_bars=48):
    """Simulate forward exit on completed candles.

    Checks in bar order: SL hit, TP1 hit. Returns outcome dict.
    MFE = best favorable excursion (in R), MAE = worst adverse (in R).
    """
    risk = abs(entry - sl) if sl else 0
    if risk <= 0:
        return None
    mfe_r = 0.0
    mae_r = 0.0
    exit_idx = None
    exit_reason = None
    for j in range(entry_idx + 1, min(entry_idx + 1 + max_hold_bars, len(candles))):
        c = candles[j]
        hi, lo, close = c["high"], c["low"], c["close"]
        if side == "LONG":
            r_hi = (hi - entry) / risk
            r_lo = (lo - entry) / risk
        else:
            r_hi = (entry - lo) / risk
            r_lo = (entry - hi) / risk
        mfe_r = max(mfe_r, r_hi)
        mae_r = min(mae_r, r_lo)
        if side == "LONG" and lo <= sl:
            exit_idx, exit_reason, exit_price = j, "stop_loss", sl
            break
        if side == "SHORT" and hi >= sl:
            exit_idx, exit_reason, exit_price = j, "stop_loss", sl
            break
        if side == "LONG" and hi >= tp1:
            exit_idx, exit_reason, exit_price = j, "take_profit_1", tp1
            break
        if side == "SHORT" and lo <= tp1:
            exit_idx, exit_reason, exit_price = j, "take_profit_1", tp1
            break
    if exit_idx is None:
        # Time exit at last bar close
        exit_idx = min(entry_idx + max_hold_bars, len(candles) - 1)
        exit_reason = "time_exit"
        exit_price = candles[exit_idx]["close"]
    pnl_r = (exit_price - entry) / risk if side == "LONG" else (entry - exit_price) / risk
    hold_bars = exit_idx - entry_idx
    return {
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_r": round(pnl_r, 3),
        "mfe_r": round(mfe_r, 3),
        "mae_r": round(mae_r, 3),
        "hold_bars": hold_bars,
    }


def run_symbol(symbol, bars, api_delay, records):
    klines = fetch_klines(symbol, "5m", limit=bars)
    if len(klines) < 300:
        print(f"  {symbol}: insufficient data ({len(klines)} bars)")
        return
    print(f"  {symbol}: {len(klines)} bars loaded")

    cache = EMACache()
    regime_engine = RegimeEngine()
    trend_engine = TrendEngine()
    pullback_engine = PullbackEngine()
    candle_engine = CandleEngine()
    volume_engine = VolumeEngine()

    # Seed cache with first window so EMA/ATR/vol_sma are stable
    seed = klines[:200]
    _ = cache.update(symbol, seed)

    for i in range(210, len(klines)):
        window = klines[: i + 1]
        ema_data = cache.update(symbol, window)
        if not ema_data:
            continue
        if ema_data.get("vol_sma20", 0) <= 0 or ema_data.get("last_volume", 0) <= 0:
            continue
        if ema_data.get("atr_14", 0) <= 0:
            continue

        # ── 1. Regime ──
        regime_eval = regime_engine.evaluate(ema_data, "unknown")
        regime = regime_eval.get("regime", "NO_TREND")
        if regime not in ("BUY_MODE", "SELL_MODE"):
            continue

        # ── 2. Trend ──
        trend_eval = trend_engine.evaluate(ema_data, regime)
        if not trend_eval.get("direction"):
            continue

        # ── 3. Pullback ──
        pb_eval = pullback_engine.evaluate(klines, ema_data, regime)
        if not pb_eval.get("pullback_detected"):
            continue

        # ── 4. Candle ──
        candle_eval = candle_engine.evaluate(klines, regime)
        if not candle_eval.get("pattern_found"):
            continue

        # ── 5. Volume ──
        vol_eval = volume_engine.evaluate(ema_data)
        last_candle = klines[i]

        side = "BUY" if regime == "BUY_MODE" else "SELL"
        vol_ratio = vol_eval.get("volume_ratio", 0)
        prev_vol = ema_data.get("prev_volume", 0)
        sma = ema_data.get("vol_sma20", 0)
        expand = vol_eval.get("volume_expanding", False)

        # Simulate entry/SL/TP exactly like SignalEngine
        entry = ema_data.get("last_close", 0)
        atr_val = ema_data.get("atr_14", 0)
        cfg = ema_v5_config.signal
        if side == "BUY":
            sl = entry - atr_val * cfg.sl_atr_mult
            tp1 = entry + abs(entry - sl) * cfg.tp1_rr
        else:
            sl = entry + atr_val * cfg.sl_atr_mult
            tp1 = entry - abs(sl - entry) * cfg.tp1_rr

        exit_info = simulate_exit(
            klines, i, "LONG" if side == "BUY" else "SHORT", entry, sl, tp1
        )

        record = {
            "symbol": symbol,
            "side": side,
            "timestamp": klines[i]["open_time"],
            "entry_price": entry,
            "volume_ratio": round(vol_ratio, 3),
            "pullback_vol_sma_ratio": round(prev_vol / sma, 3) if sma > 0 else 0,
            "confirm_vol_sma_ratio": round(vol_ratio, 3),
            "confirm_over_pullback": round((ema_data["last_volume"] / prev_vol), 3) if prev_vol > 0 else 0,
            "expansion": expand,
            "decision": "PASS" if vol_eval.get("volume_ok") else "REJECT",
            "rejected_by_ratio": round(vol_ratio, 3) < 0.4,
            "rejected_by_expansion": not expand,
            "candle_pattern": candle_eval.get("pattern_name", ""),
            "regime": regime,
            # Forward outcome
            "exit_price": exit_info["exit_price"] if exit_info else None,
            "exit_reason": exit_info["exit_reason"] if exit_info else None,
            "pnl_r": exit_info["pnl_r"] if exit_info else None,
            "mfe_r": exit_info["mfe_r"] if exit_info else None,
            "mae_r": exit_info["mae_r"] if exit_info else None,
            "hold_bars": exit_info["hold_bars"] if exit_info else None,
        }
        records.append(record)
        time.sleep(api_delay)  # be gentle to the API


def compute_stats(records):
    n = len(records)
    if n == 0:
        return {}
    wins = [r for r in records if (r.get("pnl_r") or 0) > 0]
    losses = [r for r in records if (r.get("pnl_r") or 0) <= 0]
    rs = [r.get("pnl_r") or 0 for r in records]
    gross_win = sum(r.get("pnl_r") or 0 for r in wins)
    gross_loss = abs(sum(r.get("pnl_r") or 0 for r in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else (999 if gross_win > 0 else 0)
    return {
        "n": n,
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "avg_r": round(statistics.mean(rs), 3) if rs else 0,
        "median_r": round(statistics.median(rs), 3) if rs else 0,
        "expectancy_r": round(statistics.mean(rs), 3) if rs else 0,
        "profit_factor": round(pf, 3),
        "avg_mfe_r": round(statistics.mean([r.get("mfe_r") or 0 for r in records]), 3),
        "avg_mae_r": round(statistics.mean([r.get("mae_r") or 0 for r in records]), 3),
    }


def print_group(title, stats):
    if not stats:
        print(f"  {title}: (no data)")
        return
    print(f"  {title}: n={stats['n']} win={stats['win_rate_pct']}% "
          f"avgR={stats['avg_r']} medR={stats['median_r']} "
          f"PF={stats['profit_factor']} MFE={stats['avg_mfe_r']}R MAE={stats['avg_mae_r']}R")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--bars", type=int, default=1500)
    parser.add_argument("--api-delay", type=float, default=0.15)
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    records = []

    for sym in symbols:
        try:
            run_symbol(sym, args.bars, args.api_delay, records)
        except Exception as e:
            print(f"  {sym}: ERROR {e}")

    # ── Persist ──
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(records, f, indent=2)
    print(f"\nSaved {len(records)} candidates → {OUT_FILE}")

    # ── A/B REPORT ──
    resolved = [r for r in records if r.get("pnl_r") is not None]
    print("\n" + "=" * 90)
    print("📊 HISTORICAL VOLUME OUTCOME AUDIT — A/B REPORT")
    print(f"Total candle-qualified candidates: {len(records)}  (resolved: {len(resolved)})")
    print("=" * 90)

    print("\n[A] Volume PASS vs Volume REJECT (forward outcome):")
    pass_recs = [r for r in resolved if r.get("decision") == "PASS"]
    reject_recs = [r for r in resolved if r.get("decision") == "REJECT"]
    print_group("Volume PASS", compute_stats(pass_recs))
    print_group("Volume REJECT", compute_stats(reject_recs))
    if pass_recs and reject_recs:
        diff = compute_stats(pass_recs)["expectancy_r"] - compute_stats(reject_recs)["expectancy_r"]
        print(f"  → Filter value (Expectancy PASS − REJECT) = {diff:+.3f}R")

    print("\n[B] Expansion TRUE vs Expansion FALSE (all candidates):")
    exp_t = [r for r in resolved if r.get("expansion")]
    exp_f = [r for r in resolved if not r.get("expansion")]
    print_group("Expansion TRUE", compute_stats(exp_t))
    print_group("Expansion FALSE", compute_stats(exp_f))

    print("\n[B2] Expansion × Decision matrix:")
    for dec in ("PASS", "REJECT"):
        for exp in (True, False):
            grp = [r for r in resolved if r.get("decision") == dec and r.get("expansion") == exp]
            print_group(f"{dec} + Expansion {exp}", compute_stats(grp))

    print("\n[C] Volume ratio buckets:")
    buckets = [
        ("<0.40", lambda r: r.get("volume_ratio", 0) < 0.40),
        ("0.40–0.80", lambda r: 0.40 <= r.get("volume_ratio", 0) < 0.80),
        ("0.80–1.00", lambda r: 0.80 <= r.get("volume_ratio", 0) < 1.00),
        ("1.00–1.50", lambda r: 1.00 <= r.get("volume_ratio", 0) < 1.50),
        ("1.50–2.00", lambda r: 1.50 <= r.get("volume_ratio", 0) < 2.00),
        (">2.00", lambda r: r.get("volume_ratio", 0) >= 2.00),
    ]
    for name, pred in buckets:
        grp = [r for r in resolved if pred(r)]
        print_group(f"ratio {name}", compute_stats(grp))

    print("\n[D] Side breakdown:")
    for side in ("BUY", "SELL"):
        grp = [r for r in resolved if r.get("side") == side]
        print_group(f"{side} (all candidates)", compute_stats(grp))
        pass_grp = [r for r in grp if r.get("decision") == "PASS"]
        rej_grp = [r for r in grp if r.get("decision") == "REJECT"]
        print_group(f"{side} Volume PASS", compute_stats(pass_grp))
        print_group(f"{side} Volume REJECT", compute_stats(rej_grp))

    print("\n[E] Symbol breakdown (n≥5 shown):")
    by_sym = {}
    for r in resolved:
        by_sym.setdefault(r.get("symbol"), []).append(r)
    for sym, grp in sorted(by_sym.items(), key=lambda kv: -len(kv[1])):
        if len(grp) < 5:
            continue
        print_group(f"{sym} (n={len(grp)})", compute_stats(grp))

    print("\n[F] Regime breakdown:")
    for regime in ("BUY_MODE", "SELL_MODE"):
        grp = [r for r in resolved if r.get("regime") == regime]
        print_group(f"{regime}", compute_stats(grp))
        pass_grp = [r for r in grp if r.get("decision") == "PASS"]
        rej_grp = [r for r in grp if r.get("decision") == "REJECT"]
        print_group(f"{regime} Volume PASS", compute_stats(pass_grp))
        print_group(f"{regime} Volume REJECT", compute_stats(rej_grp))

    print("\n" + "=" * 90)
    print("NOTE: research only — live strategy untouched.")


if __name__ == "__main__":
    main()
