#!/usr/bin/env python3
"""
App Profit Filter — SELECTIVITY DIAGNOSTIC (READ-ONLY).

Requirement: improve WR/PF/expectancy/net-PnL/drawdown WITHOUT touching EMA V5,
without lowering the App threshold and without widening bands. The small win
(+$6.04) / loss (-$4.89) asymmetry means the payoff is acceptable — SELECTION
is the problem. This diagnostic finds which admitted trade populations are
demonstrably negative-expectancy, so a rule can remove them and nothing else.

Authoritative corpus: positions + positions_archive in data/institutional_v1.db
(149 trades = the exact current dashboard cohort:
 55 wins / 94 losses, WR 36.9%, PF 0.72, expectancy -$0.86, net -$127.45).

Evidence sources joined per trade (best-effort, coverage reported honestly):
  - positions/positions_archive ...... outcome + P&L + regime/session/confidence
  - signals (by signal_id) ............ confidence, cvd, delta, oi_delta,
                                       funding, exchange_flow, sweep/fvg/mss/mtf
  - signals.metadata .................. EMA V5 component block (volume ratio,
                                       volume confidence, candle, pullback)
  - app_profit_filter_decisions ....... stored App decision + v2 component
                                       breakdown + Live Sheet slice

This script NEVER writes, never activates, never touches EMA V5.

Usage: python _app_selectivity_diagnostic.py [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter

DB = "data/institutional_v1.db"

V2_WEIGHTS = {
    "cvd": 0.20, "oi_funding": 0.15, "delta": 0.15, "flow": 0.15,
    "liquidity": 0.10, "sweep_fvg": 0.10, "regime": 0.10, "volume": 0.05,
}
DIRECTIONAL = ("regime", "cvd", "delta", "flow", "oi_funding")


# ── UTC wall-clock hour -> session (mirrors Live Sheet session logic, info only) ──
def session_of(ts: float) -> str:
    import datetime
    h = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).hour
    if h >= 7 and h < 13:
        return "london"
    if h >= 13 and h < 21:
        return "new_york"
    if h >= 21 or h < 2:
        return "asia"
    return "overlap"


def normalize_regime(regime: str) -> str:
    r = str(regime or "").lower()
    if r in ("buy_mode", "trending_bull", "bullish", "bull"):
        return "BUY_BULL"
    if r in ("sell_mode", "trending_bear", "bearish", "bear"):
        return "SELL_BEAR"
    if r in ("range", "ranging", "no_trend"):
        return "RANGE"
    return "OTHER"


def parse_metadata(meta) -> dict:
    """Pull EMA V5 volume evidence from signals.metadata (read-only).

    signals.metadata is double-encoded (a JSON string holding a JSON string);
    decode twice and tolerate partial failure.
    """
    out = {}
    if not meta:
        return out
    d = None
    for _ in range(3):
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
                d = meta
            except (json.JSONDecodeError, AttributeError):
                break
        else:
            d = meta
            break
    comps = (d.get("components") or {}) if isinstance(d, dict) else {}
    vol = comps.get("volume") or ""
    m = re.search(r"ratio=([\d.]+)", str(vol))
    if m:
        out["volume_ratio"] = float(m.group(1))
    out["volume_surge"] = "surge=yes" in str(vol)
    out["volume_ok"] = "ratio=0" not in str(vol)
    conf = (comps.get("confidence") or {}) if isinstance(comps, dict) else {}
    if isinstance(conf, dict):
        vc = conf.get("volume")
        if isinstance(vc, (int, float)):
            out["volume_confidence"] = float(vc)
    return out


# ── Metrics ──────────────────────────────────────────────────────────────────

def max_drawdown(pnls: list) -> float:
    peak = eq = 0.0
    mdd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def metrics(trades: list) -> dict:
    n = len(trades)
    pn = [t["pnl"] for t in trades]
    wins = sum(1 for p in pn if p > 0)
    losses = sum(1 for p in pn if p <= 0)
    gp = sum(p for p in pn if p > 0)
    gl = abs(sum(p for p in pn if p <= 0))
    net = sum(pn)
    pf = (gp / gl) if gl else (float("inf") if gp else 0.0)
    return {
        "trades": n, "wins": wins, "losses": losses,
        "win_rate": round(100.0 * wins / n, 1) if n else 0.0,
        "gross_profit": round(gp, 2), "gross_loss": round(gl, 2),
        "profit_factor": round(pf, 2), "net_pnl": round(net, 2),
        "expectancy": round(net / n, 2) if n else 0.0,
        "max_drawdown": round(max_drawdown(pn), 2),
        "avg_win": round(gp / wins, 2) if wins else 0.0,
        "avg_loss": round(-gl / losses, 2) if losses else 0.0,
        "best": round(max(pn), 2) if pn else 0.0,
        "worst": round(min(pn), 2) if pn else 0.0,
    }


def fmt_m(m: dict) -> str:
    return (f"n={m['trades']:3d} W={m['wins']:2d}/L={m['losses']:2d} WR={m['win_rate']:4.1f}% "
            f"GP=${m['gross_profit']:8.2f} GL=${m['gross_loss']:8.2f} PF={m['profit_factor']:5.2f} "
            f"Exp=${m['expectancy']:7.2f} Net=${m['net_pnl']:9.2f} DD=${m['max_drawdown']:7.2f}")


def print_table(title: str, buckets: list, ref: dict) -> None:
    print(f"\n=== {title} ===")
    print(f"  {'bucket':<26s} {fmt_m(ref)}  <- BASELINE ALL")
    for name, sel in buckets:
        m = metrics(sel)
        # net-pnl delta vs baseline
        print(f"  {name:<26s} {fmt_m(m)}  dNet=${m['net_pnl'] - ref['net_pnl']:+.2f}")


# ── Data load ────────────────────────────────────────────────────────────────

def load_corpus() -> list:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = list(con.execute("SELECT * FROM positions")) + \
        list(con.execute("SELECT * FROM positions_archive"))
    sig = {r["id"]: r for r in con.execute("SELECT * FROM signals")}
    decs = {}
    for r in con.execute("SELECT * FROM app_profit_filter_decisions"):
        decs.setdefault(r["signal_id"] or "", []).append(r)
    out = []
    for t in rows:
        sid = t["signal_id"] or ""
        s = sig.get(sid)
        ds = decs.get(sid) or []
        meta = parse_metadata(s["metadata"] if s else None)
        bd = None
        fresh = None
        if ds:
            try:
                bd = json.loads(ds[-1]["breakdown"] or "{}") or None
            except json.JSONDecodeError:
                bd = None
            fresh = {
                "level": ds[-1]["data_fresh"] or "",
                "age_sec": ds[-1]["data_age_sec"] or 0,
                "stale": ds[-1]["stale"] or 0,
                "reason": ds[-1]["reason"] or "",
            }
        out.append(dict(
            sid=sid, sym=t["symbol"], side=t["side"], pnl=t["pnl"] or 0.0,
            regime=normalize_regime(t["regime"]),
            regime_raw=t["regime"] or "", at_open=str(t["at_open_regime"] or ""),
            session=t["session"] or session_of(t["opened_at"] or 0),
            conf=t["confidence"] or 0, inst=t["institutional_score"] or 0,
            mss=t["mss_score"] or 0, fvg=t["fvg_score"] or 0,
            volatility=t["volatility_score"] or 0,
            exit_reason=t["exit_reason"] or "", hold=t["hold_minutes"] or 0,
            has_sig=s is not None, has_dec=bool(ds),
            s_conf=(s["confidence"] or 0) if s else None,
            s_sweep=(s["sweep_score"] or 0) if s else None,
            s_fvg=(s["fvg_score"] or 0) if s else None,
            s_mtf=(s["mtf_alignment"] or 0) if s else None,
            cvd=(s["cvd"] or 0) if s else None,
            delta=(s["delta"] or 0) if s else None,
            oi=(s["oi_delta"] or 0) if s else None,
            funding=(s["funding_rate"] or 0) if s else None,
            flow=(s["exchange_flow"] or 0) if s else None,
            vol_ratio=meta.get("volume_ratio"), vol_conf=meta.get("volume_confidence"),
            app_decision=(ds[-1]["decision"] if ds else None),
            app_reason=(ds[-1]["reason"] if ds else None),
            app_qs=(ds[-1]["quality_score"] or 0) if ds else None,
            app_breakdown=bd, app_fresh=fresh,
        ))
    con.close()
    return out


# ── v2 recomputation from a stored v1 breakdown ──────────────────────────────

def v2_score(bd: dict):
    if not bd:
        return None
    return max(0.0, min(100.0, round(
        sum((bd.get(k) or {}).get("score", 0) * w for k, w in V2_WEIGHTS.items()), 1)))


def hard_conflicts(bd: dict) -> list:
    if not bd:
        return []
    return [k for k in DIRECTIONAL
            if (bd.get(k) or {}).get("conflict") and (bd.get(k) or {}).get("score", 100) < 45.0] + \
           (["liquidity"] if (bd.get("liquidity") or {}).get("conflict")
            and (bd.get("liquidity") or {}).get("score", 100) < 45.0 else [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    C = load_corpus()
    base = metrics(C)
    print(f"CORPUS: {len(C)} trades | signals joined: {sum(1 for c in C if c['has_sig'])} | "
          f"App decisions stored: {sum(1 for c in C if c['has_dec'])}")
    print(f"BASELINE: {fmt_m(base)}")

    # ── 1. PRIMARY BREAKDOWNS ───────────────────────────────────────────────
    print_table("LONG vs SHORT", [
        ("LONG", [c for c in C if c["side"] == "LONG"]),
        ("SHORT", [c for c in C if c["side"] == "SHORT"]),
    ], base)

    print_table("REGIME MODE (open)", [
        ("BUY_MODE/bull", [c for c in C if c["regime"] == "BUY_BULL"]),
        ("SELL_MODE/bear", [c for c in C if c["regime"] == "SELL_BEAR"]),
        ("RANGE", [c for c in C if c["regime"] == "RANGE"]),
        ("OTHER/unknown", [c for c in C if c["regime"] == "OTHER"]),
    ], base)

    print_table("SIDE x REGIME ALIGNMENT", [
        ("LONG in BUY/bull", [c for c in C if c["side"] == "LONG" and c["regime"] == "BUY_BULL"]),
        ("SHORT in SELL/bear", [c for c in C if c["side"] == "SHORT" and c["regime"] == "SELL_BEAR"]),
        ("LONG in SELL/bear (against)", [c for c in C if c["side"] == "LONG" and c["regime"] == "SELL_BEAR"]),
        ("SHORT in BUY/bull (against)", [c for c in C if c["side"] == "SHORT" and c["regime"] == "BUY_BULL"]),
    ], base)

    print_table("SESSION", [
        ("new_york", [c for c in C if c["session"] == "new_york"]),
        ("london", [c for c in C if c["session"] == "london"]),
        ("asia/overlap/other", [c for c in C if c["session"] not in ("new_york", "london")]),
    ], base)

    def conf_bucket(c):
        v = c["conf"] or 0
        return "<=0.60" if v <= 0.60 else ("0.60-0.75" if v < 0.75 else ("0.75-0.85" if v < 0.85 else ">=0.85"))
    print_table("CONFIDENCE BUCKET (positions)", [
        (b, [c for c in C if conf_bucket(c) == b])
        for b in ("<=0.60", "0.60-0.75", "0.75-0.85", ">=0.85")
    ], base)

    def vol_bucket(c):
        r = c["vol_ratio"]
        if r is None:
            return "vol-data-NA"
        return "vol<1.0" if r < 1.0 else ("vol 1.0-1.5" if r < 1.5 else "vol>1.5")
    print_table("VOLUME RATIO (from EMA V5 signal block)", [
        (b, [c for c in C if vol_bucket(c) == b])
        for b in ("vol<1.0", "vol 1.0-1.5", "vol>1.5", "vol-data-NA")
    ], base)

    print_table("EXIT REASON (outcome driver)", [
        (b, [c for c in C if (c["exit_reason"] or "?") == b])
        for b in ("take_profit_1", "trailing_stop", "stop_loss", "take_profit")
    ] + [("other", [c for c in C if (c["exit_reason"] or "") not in ("take_profit_1", "trailing_stop", "stop_loss", "take_profit")])], base)

    print_table("VOLATILITY SCORE (0 = unlabeled)", [
        ("vol==0", [c for c in C if (c["volatility"] or 0) == 0]),
        ("vol 1-40", [c for c in C if 0 < (c["volatility"] or 0) <= 40]),
        ("vol 40-70", [c for c in C if 40 < (c["volatility"] or 0) <= 70]),
        ("vol>70", [c for c in C if (c["volatility"] or 0) > 70]),
    ], base)

    print_table("SWEEP PRESENT (signals, s=29/113 non-zero)", [
        ("sweep>0", [c for c in C if c["has_sig"] and (c["s_sweep"] or 0) > 0]),
        ("sweep==0", [c for c in C if c["has_sig"] and not (c["s_sweep"] or 0) > 0]),
    ], base)

    # ── 2. APP DECISION COHORT (stored breakdowns, v2-recomputed) ───────────
    scored = [c for c in C if c["has_dec"] and c["app_breakdown"]]
    print("\n=== APP DECISION COHORT (stored breakdown, v2-recomputed; n=%d) ===" % len(scored))
    if scored:
        for c in scored:
            bd = c["app_breakdown"]
            print(f"  {c['sym']:12s} {c['side']:5s} pnl=${c['pnl']:8.2f} app={str(c['app_decision']):6s} "
                  f"reason={str(c['app_reason']):24s} v2={v2_score(bd)} qs={c['app_qs']} "
                  f"conflicts={hard_conflicts(bd)} fresh={c['app_fresh']}")
        # per-component outcome table
        print("\n  Per-component (v2 weight, stored score) -> outcome:")
        for comp, w in V2_WEIGHTS.items():
            hi = [c for c in scored if (c["app_breakdown"].get(comp) or {}).get("score", 0) >= 60]
            lo = [c for c in scored if (c["app_breakdown"].get(comp) or {}).get("score", 0) < 60]
            conf = [c for c in scored if comp in hard_conflicts(c["app_breakdown"])]
            mh, ml, mc = metrics(hi), metrics(lo), metrics(conf)
            print(f"    {comp:<10s} score>=60: {fmt_m(mh)}")
            print(f"    {comp:<10s} score< 60: {fmt_m(ml)}")
            print(f"    {comp:<10s} hard-conf : {fmt_m(mc)}" if mc["trades"] else f"    {comp:<10s} hard-conf : none")
    else:
        print("  none")

    # ── 3. LARGEST LOSERS / WINNERS ─────────────────────────────────────────
    print("\n=== LARGEST LOSERS (top 10) ===")
    for c in sorted(C, key=lambda c: c["pnl"])[:10]:
        print(f"  {c['sym']:12s} {c['side']:5s} pnl=${c['pnl']:8.2f} {c['regime_raw']:12s} "
              f"conf={c['conf']:.2f} vol_ratio={c['vol_ratio']} app={str(c['app_decision'])}/{str(c['app_reason'])}")
    print("\n=== LARGEST WINNERS (top 5) ===")
    for c in sorted(C, key=lambda c: c["pnl"])[-5:][::-1]:
        print(f"  {c['sym']:12s} {c['side']:5s} pnl=${c['pnl']:8.2f} {c['regime_raw']:12s} "
              f"conf={c['conf']:.2f} vol_ratio={c['vol_ratio']} app={str(c['app_decision'])}/{str(c['app_reason'])}")

    # ── 4. LEAVE-ONE-OUT OUTLIER SENSITIVITY ────────────────────────────────
    print("\n=== LEAVE-ONE-OUT: OUTLIER SENSITIVITY ===")
    for label, drops in [
        ("drop worst (BLUAIUSDT)", [c for c in C if c["sym"] != "BLUAIUSDT"]),
        ("drop top-2 losers", [c for c in C if c["sym"] not in ("BLUAIUSDT", "KAITOUSDT")]),
        ("drop top-3 losers", [c for c in C if c["sym"] not in ("BLUAIUSDT", "KAITOUSDT", "VELVETUSDT")]),
        ("drop top-5 losers", [c for c in C if c["sym"] not in ("BLUAIUSDT", "KAITOUSDT", "VELVETUSDT", "ARXUSDT", "SPACEUSDT")]),
        ("drop best (ALLOUSDT)", [c for c in C if c["sym"] != "ALLOUSDT"]),
        ("drop best+worst", [c for c in C if c["sym"] not in ("ALLOUSDT", "BLUAIUSDT")]),
    ]:
        m = metrics(drops)
        print(f"  {label:<24s} {fmt_m(m)}  dNet=${m['net_pnl'] - base['net_pnl']:+.2f}")

    # ── 5. COUNTERFACTUAL: drop-losing-population candidate rules ───────────
    print("\n=== COUNTERFACTUAL — CANDIDATE SELECTIVITY RULES (what-if NOT admitted) ===")
    rules = []
    # (from the evidence, these are the negative-expectancy groups to test)
    rules.append(("REJECT LONG in SELL/bear regime",
                  [c for c in C if not (c["side"] == "LONG" and c["regime"] == "SELL_BEAR")]))
    rules.append(("REJECT SHORT in BUY/bull regime",
                  [c for c in C if not (c["side"] == "SHORT" and c["regime"] == "BUY_BULL")]))
    rules.append(("REJECT any counter-regime trade",
                  [c for c in C if not ((c["side"] == "LONG" and c["regime"] == "SELL_BEAR")
                                       or (c["side"] == "SHORT" and c["regime"] == "BUY_BULL"))]))
    rules.append(("REJECT low confidence <=0.60",
                  [c for c in C if (c["conf"] or 0) > 0.60]))
    rules.append(("REJECT low-SL-no-take profit exits (SL losses only) [info]",
                  [c for c in C if c["exit_reason"] != "stop_loss"]))
    rules.append(("REJECT vol_ratio < 0.9 when known",
                  [c for c in C if (c["vol_ratio"] is None or c["vol_ratio"] >= 0.9)]))
    rules.append(("REJECT vol < 1.0 + LONG/bull",
                  [c for c in C if not (c["vol_ratio"] is not None and c["vol_ratio"] < 1.0
                                        and c["side"] == "LONG" and c["regime"] == "BUY_BULL")]))

    # LOO complement: also drop each rule's EXCLUDED set alone to prove the
    # EXCLUDED population itself is the losing population
    for name, keep in rules:
        m = metrics(keep)
        excluded = [c for c in C if c not in keep]
        me = metrics(excluded)
        print(f"  {name:<42s}")
        print(f"    KEEP      : {fmt_m(m)}  dNet=${m['net_pnl'] - base['net_pnl']:+.2f}")
        print(f"    EXCLUDED  : n={me['trades']:3d} {fmt_m(me)}" if me["trades"] else "    EXCLUDED  : none")

    if args.json:
        print(json.dumps({"baseline": base}, indent=2, default=str))


if __name__ == "__main__":
    main()