"""
READ-ONLY TRANSITION AUDIT — REGIME → PULLBACK (no live changes, no strategy edits).

Replays the exact live EMA V5 chain (REGIME → TREND → PULLBACK) for the current
BUY_MODE/SELL_MODE candidates using fresh live klines, and reports for every symbol
why it does / does not transition to WAITING_PULLBACK.

Reuses the actual live sub-engines verbatim:
  - EMACache.update()        (exact indicator feed)
  - RegimeEngine.evaluate()  (exact regime)
  - TrendEngine.evaluate()   (exact trend)
  - PullbackEngine.evaluate()(exact pullback)

Read-only: no thresholds, weights, state, or strategy logic are touched.

Usage:
    python -m scanner.ema_v5.pullback_transition_audit [--symbols SYM,SYM] [--bars 300]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # packages/ai-engine

from scanner.ema_v5.cache import EMACache
from scanner.ema_v5.regime_engine import RegimeEngine
from scanner.ema_v5.trend_engine import TrendEngine
from scanner.ema_v5.pullback_engine import PullbackEngine
from scanner.ema_v5.config import ema_v5_config

BRIDGE = Path(__file__).resolve().parent.parent.parent / "data" / "bridge" / "ema_v5.json"


def fetch_klines(symbol: str, interval: str = "5m", limit: int = 300) -> list:
    url = (
        f"https://fapi.binance.com/fapi/v1/klines?"
        f"symbol={symbol}&interval={interval}&limit={limit}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
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


def get_live_regime_symbols():
    """Pull the current BUY_MODE/SELL_MODE symbols from the live engine bridge."""
    if not BRIDGE.exists():
        print(f"Bridge file not found: {BRIDGE}")
        return []
    data = json.load(open(BRIDGE))
    ev = data.get("ema_v5", {})
    states = ev.get("states", {})
    syms = [
        s for s, st in states.items()
        if st.get("state") in ("BUY_MODE", "SELL_MODE")
    ]
    return syms


def audit_symbol(symbol, bars, cache, regime_engine, trend_engine, pb_engine):
    row = {"symbol": symbol}
    try:
        klines = fetch_klines(symbol, "5m", limit=bars)
    except Exception as e:
        row.update({"fetch_error": str(e), "state_transition_attempted": False})
        return row
    if len(klines) < ema_v5_config.ema.min_candles:
        row.update({"reason": "insufficient_data", "state_transition_attempted": False})
        return row

    ema_data = cache.update(symbol, klines)
    if not ema_data:
        row.update({"reason": "ema_computation_failed", "state_transition_attempted": False})
        return row

    # ── Regime ──
    regime_eval = regime_engine.evaluate(ema_data, "unknown")
    regime = regime_eval.get("regime", "NO_TREND")
    row.update({
        "side": "BUY" if regime == "BUY_MODE" else ("SELL" if regime == "SELL_MODE" else "NONE"),
        "current_state": regime,
        "previous_state": "NO_TREND",
        "ema20": ema_data.get("ema20", 0),
        "ema50": ema_data.get("ema50", 0),
        "ema144": ema_data.get("ema144", 0),
        "ema200": ema_data.get("ema200", 0),
        "price": ema_data.get("last_close", 0),
        "atr": ema_data.get("atr_14", 0),
        "regime_reason": regime_eval.get("reason", ""),
    })

    if regime not in ("BUY_MODE", "SELL_MODE"):
        row.update({"pullback_rejection_reason": "regime_lost",
                    "state_transition_attempted": False})
        return row

    # ── Trend ──
    trend_eval = trend_engine.evaluate(ema_data, regime)
    row["trend_direction"] = trend_eval.get("direction", "")
    row["trend_score"] = trend_eval.get("trend_score", 0)

    # ── Pullback ──
    pb_eval = pb_engine.evaluate(klines, ema_data, regime)
    diag = pb_eval.get("diagnostics", {})
    touch = pb_eval.get("touch_level")
    row.update({
        "ema20_dist_atr": round(diag.get("ema20_dist_atr", 0), 3),
        "ema50_dist_atr": round(diag.get("ema50_dist_atr", 0), 3),
        "touch_ema20": diag.get("near_ema20", False),
        "touch_ema50": diag.get("near_ema50", False),
        "structure_valid": pb_eval.get("structure_intact", True),
        "pullback_accepted": pb_eval.get("pullback_detected", False),
        "pullback_rejection_reason": pb_eval.get("reason", "none"),
        "pullback_touch_level": touch,
        "pullback_mode": diag.get("mode", "unknown"),
        "atr_threshold": diag.get("atr_threshold", 0),
        "state_transition_attempted": pb_eval.get("pullback_detected", False),
        "state_transition_result": "WAITING_PULLBACK" if pb_eval.get("pullback_detected", False) else "STAY_NO_TREND",
    })
    row["cooldown_active"] = False
    row["dedup_active"] = False
    return row


def classify_reason(row):
    r = row.get("pullback_rejection_reason", "")
    if not row.get("pullback_accepted"):
        if "not_near_ema" in r or "outside_tolerance" in r:
            return "not near EMA20/50 (ATR distance > threshold)"
        if "pullback_too_deep" in r:
            return "ATR distance too large (pullback too deep)"
        if "structure_broken" in r:
            return "structure invalid"
        if "no_ema_touch" in r or "no_ema_proximity" in r:
            return "no EMA touch in last candles"
        if "missing_data" in r:
            return "missing data"
        if "no_trend" in r or "regime_lost" in r:
            return "wrong side of EMA / regime lost"
        return f"other: {r}"
    return "ACCEPTED"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=str, default="")
    parser.add_argument("--bars", type=int, default=300)
    parser.add_argument("--api-delay", type=float, default=0.1)
    args = parser.parse_args()

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = get_live_regime_symbols()
    if not symbols:
        print("No regime candidates found in bridge; pass --symbols explicitly.")
        return

    print(f"Auditing {len(symbols)} live regime candidates (bars={args.bars})...")

    cache = EMACache()
    regime_engine = RegimeEngine()
    trend_engine = TrendEngine()
    pb_engine = PullbackEngine()

    rows = []
    for sym in symbols:
        row = audit_symbol(sym, args.bars, cache, regime_engine, trend_engine, pb_engine)
        rows.append(row)
        time.sleep(args.api_delay)

    # ── Per-candidate report ──
    print("\n" + "=" * 100)
    print("📋 PER-CANDIDATE TRANSITION REPORT")
    print("=" * 100)
    hdr = (f"{'SYMBOL':<14} {'SIDE':<4} {'STATE':<10} {'PRICE':>10} {'ATR':>8} "
           f"{'d20ATR':>7} {'d50ATR':>7} {'t20':>4} {'t50':>4} {'str':>4} {'ACC':>4} REASON")
    print(hdr)
    print("-" * 100)
    for r in rows:
        if r.get("fetch_error"):
            print(f"{r['symbol']:<14} FETCH ERROR: {r['fetch_error']}")
            continue
        if r.get("pullback_rejection_reason") == "regime_lost" or not r.get("state_transition_attempted", False):
            print(f"{r['symbol']:<14} regime={r.get('current_state')} "
                  f"NO-TRANSITION reason={r.get('pullback_rejection_reason','n/a')}")
            continue
        print(f"{r['symbol']:<14} {r.get('side','?'):<4} {r.get('current_state','?'):<10} "
              f"{r.get('price',0):>10.6f} {r.get('atr',0):>8.4f} "
              f"{r.get('ema20_dist_atr',0):>7.2f} {r.get('ema50_dist_atr',0):>7.2f} "
              f"{str(r.get('touch_ema20')):>4} {str(r.get('touch_ema50')):>4} "
              f"{str(r.get('structure_valid')):>4} {str(r.get('pullback_accepted')):>4} "
              f"{r.get('pullback_rejection_reason','')}")

    # ── Rejection summary ──
    print("\n" + "=" * 100)
    print("📊 REGIME → PULLBACK REJECTION SUMMARY")
    print("=" * 100)
    accepted = [r for r in rows if r.get("pullback_accepted")]
    rejected = [r for r in rows if not r.get("pullback_accepted")]
    print(f"Total regime candidates: {len(rows)}")
    print(f"Accepted (→ WAITING_PULLBACK): {len(accepted)}")
    print(f"Rejected: {len(rejected)}")

    if rejected:
        reasons = Counter(classify_reason(r) for r in rejected)
        print("\nRejected because:")
        for reason, cnt in reasons.most_common():
            pct = cnt / len(rejected) * 100
            print(f"  {cnt:>3} ({pct:>5.1f}%)  {reason}")
        most_common = reasons.most_common(1)
        if most_common:
            print(f"\n★ SINGLE MOST COMMON REJECTION REASON: {most_common[0][1]} "
                  f"({most_common[0][0]} occurrences)")

    # ── Full details dump ──
    print("\n" + "=" * 100)
    print("🔬 FULL DETAIL (all fields)")
    print("=" * 100)
    for r in rows:
        print(json.dumps(r, default=str))

    print("\nNOTE: read-only audit — no strategy logic or thresholds modified.")


if __name__ == "__main__":
    main()
