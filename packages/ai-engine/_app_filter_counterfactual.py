#!/usr/bin/env python3
"""
App Profit Filter v2 — READ-ONLY counterfactual: CURRENT_V2 vs RELAXED_V2.

Simulates both admission policies against ALL linked historical outcomes
(positions_archive joined by signal_id). It NEVER modifies EMA V5, Smart Money,
the execution engine, or the LIVE SHEET. It never activates anything — Phase A
diagnostic only.

Policy A — CURRENT_V2 (as implemented in app_layer/profit_filter.py):
    score >= 80                -> ACCEPT   (score carries even over a conflict)
    ANY single strong directional conflict (regime/cvd/delta/flow/oi_funding)
        or liquidity cascade   -> REJECT (HARD_CONFLICT)
    70 <= score < 80           -> ACCEPT
    60 <= score < 70           -> WATCH
    score < 60                 -> REJECT
    stale / missing / unverified data -> NO_APP_DECISION (never a trade verdict)

Policy B — RELAXED_V2 (candidate, NOT activated):
    regime directly opposite   -> REJECT (HARD_REJECT:REGIME)     [always]
    liquidity cascade          -> REJECT (HARD_REJECT:LIQUIDITY)  [always]
    >=2 independent strong conflicts among cvd/delta/flow/oi_funding -> HARD_REJECT
    ONE conflicting factor     -> score penalty only (bands decide)
    70 <= score               -> ACCEPT ; 60-69 -> WATCH ; <60 -> REJECT
    volume conflict           -> soft confirmation only, never a veto
    stale / missing / unverified data -> NO_APP_DECISION

Both policies are scored on the SAME basis: the v2 App Quality Score recomputed
from each decision's stored per-component breakdown x v2 weights.

Usage:
    python _app_filter_counterfactual.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict

DB = "data/institutional_v1.db"

# ── v2 weights (must sum to 100). Kept identical for BOTH policies. ──────────
V2_WEIGHTS = {
    "cvd": 0.20,
    "oi_funding": 0.15,
    "delta": 0.15,
    "flow": 0.15,
    "liquidity": 0.10,
    "sweep_fvg": 0.10,
    "regime": 0.10,
    "volume": 0.05,
}
assert abs(sum(V2_WEIGHTS.values()) - 1.0) < 1e-9, "v2 weights must sum to 1.0"

# v2 directional components that can form hard conflicts. VOLUME is excluded
# (soft confirmation modifier). SWEEP/FVG and plain high-liq-risk are scored
# but never hard-reject.
DIRECTIONAL = ("regime", "cvd", "delta", "flow", "oi_funding")
CONFLICT_THRESHOLD = 45.0  # component score below this = STRONG contradiction


def _conflicts(bd: dict) -> dict:
    """{component: detail} for STRONG directional conflicts in a breakdown."""
    return {
        k: (bd.get(k) or {}).get("detail", "")
        for k in DIRECTIONAL
        if (bd.get(k) or {}).get("conflict") and (bd.get(k) or {}).get("score", 100) < CONFLICT_THRESHOLD
    }


def _liquidity_conflict(bd: dict) -> bool:
    liq = bd.get("liquidity") or {}
    return bool(liq.get("conflict") and liq.get("score", 100) < CONFLICT_THRESHOLD)


def v2_score(bd: dict) -> float:
    s = sum((bd.get(k) or {}).get("score", 0) * w for k, w in V2_WEIGHTS.items())
    return max(0.0, min(100.0, round(s, 1)))


def decide_current_v2(score: float, bd: dict) -> tuple[str, str]:
    """CURRENT_V2 admission -> (decision, reason). Mirrors evaluate()."""
    conflicts = _conflicts(bd)
    liq = _liquidity_conflict(bd)
    if score >= 80:
        return "ACCEPT", ""
    if conflicts or liq:
        worst = max(conflicts.keys(), key=lambda k: 50 - (bd.get(k) or {}).get("score", 0)) if conflicts else "LIQUIDITY"
        return "REJECT", f"HARD_CONFLICT:{worst.upper()}"
    if score >= 70:
        return "ACCEPT", ""
    if score >= 60:
        return "WATCH", "BAND:WATCH"
    return "REJECT", "BAND:LOW_SCORE"


def decide_relaxed_v2(score: float, bd: dict) -> tuple[str, str]:
    """RELAXED_V2 admission -> (decision, reason). Candidate policy."""
    conflicts = _conflicts(bd)
    regime = conflicts.pop("regime", None)
    if regime is not None:
        return "REJECT", "HARD_REJECT:REGIME"
    if _liquidity_conflict(bd):
        return "REJECT", "HARD_REJECT:LIQUIDITY"
    if len(conflicts) >= 2:
        worst = max(conflicts, key=lambda k: 50 - (bd.get(k) or {}).get("score", 0))
        return "REJECT", f"HARD_REJECT:MULTI:{worst.upper()}"
    # single conflict => penalty only (already embedded in score), bands decide
    if score >= 70:
        return "ACCEPT", ""
    if score >= 60:
        return "WATCH", "BAND:WATCH"
    return "REJECT", "BAND:LOW_SCORE"


# ── Data loading ─────────────────────────────────────────────────────────────

def load_linked(con: sqlite3.Connection) -> list[dict]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT d.id, d.timestamp, d.symbol, d.side, d.decision, d.reason,
               d.quality_score, d.breakdown, d.signal_id,
               p.pnl, p.realized_r, p.regime, p.outcome
        FROM app_profit_filter_decisions d
        JOIN positions_archive p ON p.signal_id = d.signal_id
        ORDER BY d.timestamp
        """
    ).fetchall()
    out = []
    for r in rows:
        try:
            bd = json.loads(r["breakdown"] or "{}")
        except json.JSONDecodeError:
            bd = {}
        out.append({
            "id": r["id"], "timestamp": r["timestamp"], "symbol": r["symbol"],
            "side": r["side"], "quality_score": r["quality_score"],
            "has_breakdown": bool(bd),
            "breakdown": bd,
            "v2_score": v2_score(bd) if bd else None,
            "pnl": r["pnl"] or 0.0, "r": r["realized_r"] or 0.0,
            "regime_mode": r["regime"] or "?", "outcome": r["outcome"] or "",
            "cv2": None, "rv2": None,
        })
    return out


def classify(rec: dict) -> None:
    """Classify each decision under both policies (NO_APP_DECISION handled)."""
    if not rec["has_breakdown"]:
        rec["cv2"] = ("NO_APP_DECISION", "NO_DATA_OR_STALE")
        rec["rv2"] = ("NO_APP_DECISION", "NO_DATA_OR_STALE")
        return
    score = rec["v2_score"]
    rec["cv2"] = decide_current_v2(score, rec["breakdown"])
    rec["rv2"] = decide_relaxed_v2(score, rec["breakdown"])


# ── Metrics ──────────────────────────────────────────────────────────────────

def max_drawdown(pnls: list[float]) -> float:
    peak = 0.0
    mdd = 0.0
    eq = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def metrics(trades: list[dict]) -> dict:
    n = len(trades)
    pnls = [t["pnl"] for t in trades]
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] <= 0)
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    net = sum(pnls)
    pf = (gross_profit / gross_loss) if gross_loss else (float("inf") if gross_profit else 0.0)
    return {
        "trades": n, "wins": wins, "losses": losses,
        "win_rate": (100.0 * wins / n) if n else 0.0,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "profit_factor": pf, "net_pnl": net,
        "expectancy": (net / n) if n else 0.0,
        "max_drawdown": max_drawdown(pnls),
    }


def count_accepted(trades: list[dict], key: str) -> int:
    return sum(1 for t in trades if t[key][0] == "ACCEPT")


def print_table(title: str, cur: dict, rel: dict, all42: dict) -> None:
    hdr = f"{'metric':<28s} {'CURRENT_V2':>14s} {'RELAXED_V2':>14s}"
    print(f"\n=== {title} ===")
    print(hdr)
    print("-" * len(hdr))
    rows = [
        ("trades", cur["trades"], rel["trades"], all42["trades"]),
        ("accepted", cur["accepted"], rel["accepted"], all42["trades"]),
        ("rejected", cur["rejected"], rel["rejected"], 0),
        ("watch", cur["watch"], rel["watch"], 0),
        ("no_decision(data-quality)", cur["no_decision"], rel["no_decision"], 0),
        ("profitable trades", cur["wins"], rel["wins"], all42["wins"]),
        ("losing trades", cur["losses"], rel["losses"], all42["losses"]),
        ("win rate %", cur["win_rate"], rel["win_rate"], all42["win_rate"]),
        ("gross profit $", cur["gross_profit"], rel["gross_profit"], all42["gross_profit"]),
        ("gross loss $", cur["gross_loss"], rel["gross_loss"], all42["gross_loss"]),
        ("profit factor", cur["profit_factor"], rel["profit_factor"], all42["profit_factor"]),
        ("expectancy/trade $", cur["expectancy"], rel["expectancy"], all42["expectancy"]),
        ("net PnL $", cur["net_pnl"], rel["net_pnl"], all42["net_pnl"]),
        ("max drawdown $", cur["max_drawdown"], rel["max_drawdown"], all42["max_drawdown"]),
    ]
    for label, c, r_, a in rows:
        cf = "%.2f" % c if isinstance(c, float) else str(c)
        rf = "%.2f" % r_ if isinstance(r_, float) else str(r_)
        af = "%.2f" % a if isinstance(a, float) else str(a)
        print(f"{label:<28s} {cf:>14s} {rf:>14s}  | no-filter(ALL 42): {af}")
    print()


def print_breakdown(label: str, cur_sel: list, rel_sel: list) -> None:
    cm = metrics(cur_sel)
    rm = metrics(rel_sel)
    print(f"  {label:<22s} current n={cm['trades']:2d} net=${cm['net_pnl']:8.2f} W%={cm['win_rate']:4.1f} PF={cm['profit_factor']:.2f} | "
          f"relaxed n={rm['trades']:2d} net=${rm['net_pnl']:8.2f} W%={rm['win_rate']:4.1f} PF={rm['profit_factor']:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="App Filter v2 counterfactual (read-only)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON at end")
    args = parser.parse_args()

    con = sqlite3.connect(DB)
    recs = load_linked(con)
    con.close()
    for r in recs:
        classify(r)

    scored = [r for r in recs if r["has_breakdown"]]

    def split(recs_: list, key: str) -> tuple:
        acc = [r for r in recs_ if r[key][0] == "ACCEPT"]
        rej = [r for r in recs_ if r[key][0] == "REJECT"]
        wat = [r for r in recs_ if r[key][0] == "WATCH"]
        nod = [r for r in recs_ if r[key][0] == "NO_APP_DECISION"]
        return acc, rej, wat, nod

    cur_acc, cur_rej, cur_wat, cur_nod = split(recs, "cv2")
    rel_acc, rel_rej, rel_wat, rel_nod = split(recs, "rv2")
    all42 = metrics(recs)
    cur_m = {**metrics(cur_acc), "accepted": len(cur_acc), "rejected": len(cur_rej),
             "watch": len(cur_wat), "no_decision": len(cur_nod)}
    rel_m = {**metrics(rel_acc), "accepted": len(rel_acc), "rejected": len(rel_rej),
             "watch": len(rel_wat), "no_decision": len(rel_nod)}

    print(f"LINKED TRADES: {len(recs)} | scored (breakdown present): {len(scored)} | "
          f"missing-data/stale (NO_APP_DECISION under both): {len(recs) - len(scored)}")
    print(f"NOTE: v2 score recomputed from stored v1 breakdown x v2 weights for every scored decision. "
          f"No live data was changed.")

    print_table("COUNTERFACTUAL — ACCEPTED-COHORT P&L (filter-on)", cur_m, rel_m, all42)

    # Score bands (accepted cohort)
    def bands(sel: list) -> dict:
        out = {"60-69": [], "70-79": [], ">=80": []}
        for t in sel:
            s = t["v2_score"]
            if s is None:
                continue
            if s >= 80:
                out[">=80"].append(t)
            elif s >= 70:
                out["70-79"].append(t)
            elif s >= 60:
                out["60-69"].append(t)
        return out

    print("=== SCORE BANDS (accepted cohort) ===")
    for band in ("60-69", "70-79", ">=80"):
        print_breakdown(f"score {band}", bands(cur_acc)[band], bands(rel_acc)[band])

    # LONG / SHORT and BUY_MODE / SELL_MODE
    print("=== LONG vs SHORT (accepted cohort) ===")
    for side in ("LONG", "SHORT"):
        print_breakdown(f"side {side}",
                        [t for t in cur_acc if t["side"] == side],
                        [t for t in rel_acc if t["side"] == side])
    print("=== BUY_MODE vs SELL_MODE (accepted cohort, EMA V5 regime at open) ===")
    for mode in ("BUY_MODE", "SELL_MODE"):
        print_breakdown(f"regime {mode}",
                        [t for t in cur_acc if t["regime_mode"] == mode],
                        [t for t in rel_acc if t["regime_mode"] == mode])

    # Per-conflict outcomes: trades carrying each strong conflict (what the veto
    # would discard vs what relaxing rescues)
    print("=== CONFLICT OUTCOMES (scored trades carrying each STRONG conflict) ===")
    for comp in ("oi_funding", "cvd", "delta", "flow", "liquidity"):
        sel_c = [t for t in scored
                 if (t["breakdown"].get(comp) or {}).get("conflict")
                 and (t["breakdown"].get(comp) or {}).get("score", 100) < CONFLICT_THRESHOLD]
        m = metrics(sel_c)
        print(f"  {comp:10s} n={m['trades']:2d} net=${m['net_pnl']:8.2f} "
              f"W/L={m['wins']}/{m['losses']} W%={m['win_rate']:4.1f} PF={m['profit_factor']:.2f}")

    # Rescued trades
    cur_rej_ids = {t["id"] for t in cur_rej}
    cur_wat_ids = {t["id"] for t in cur_wat}
    rescued = [t for t in rel_acc if t["id"] in cur_rej_ids]
    rescued_p = [t for t in rescued if t["pnl"] > 0]
    rescued_l = [t for t in rescued if t["pnl"] <= 0]
    # REJECT -> WATCH upgrades (partial rescue: single-conflict veto removed)
    upgraded = [t for t in rel_wat if t["id"] in cur_rej_ids]
    print("\n=== 1. COUNTERFACTUAL TRADES RESCUED BY RELAXED_V2 (REJECT -> ACCEPT) ===")
    for t in rescued:
        print(f"  {t['symbol']:12s} {t['side']:5s} {t['regime_mode']:9s} v2score={t['v2_score']:5.1f} "
              f"cv2='{t['cv2'][1]}' -> relaxed=ACCEPT  pnl=${t['pnl']:8.2f} "
              f"({t['outcome'] or '?'})")
    rm_rescued = metrics(rescued)
    print(f"  TOTAL RESCUED: n={len(rescued)} net=${rm_rescued['net_pnl']:8.2f} "
          f"PF={rm_rescued['profit_factor']:.2f} W%={rm_rescued['win_rate']:.1f} "
          f"W/L={rm_rescued['wins']}/{rm_rescued['losses']}")
    print(f"  2. PROFITABLE TRADES RESCUED: {len(rescued_p)} "
          f"(+${sum(t['pnl'] for t in rescued_p):.2f})")
    print(f"  3. LOSING TRADES RESCUED: {len(rescued_l)} "
          f"(${sum(t['pnl'] for t in rescued_l):.2f})")

    print("\n=== 1b. REJECT -> WATCH UPGRADES under RELAXED_V2 (partial rescue) ===")
    for t in upgraded:
        print(f"  {t['symbol']:12s} {t['side']:5s} {t['regime_mode']:9s} v2score={t['v2_score']:5.1f} "
              f"cv2='{t['cv2'][1]}' -> relaxed=WATCH  pnl=${t['pnl']:8.2f} ({t['outcome'] or '?'})")
    um = metrics(upgraded)
    print(f"  TOTAL UPGRADED: n={len(upgraded)} net=${um['net_pnl']:8.2f} "
          f"W/L={um['wins']}/{um['losses']} PF={um['profit_factor']:.2f} "
          f"-> if these were traded, the extra drawdown would be ${um['max_drawdown']:.2f}")

    cur_rej_profitable = [t for t in cur_rej if t["pnl"] > 0]
    print(f"  4. PROFITABLE TRADES REJECTED BY CURRENT_V2: {len(cur_rej_profitable)} "
          f"(+${sum(t['pnl'] for t in cur_rej_profitable):.2f})")

    all_sorted = sorted(scored, key=lambda t: t["pnl"])
    print("  5. LARGEST NEGATIVE OUTLIERS (scored, all policies):")
    for t in all_sorted[:3]:
        print(f"     {t['symbol']:12s} pnl=${t['pnl']:8.2f} cv2={t['cv2'][0]:6s} "
              f"relaxed={t['rv2'][0]:6s} v2score={t['v2_score']}")
    print("  6. LARGEST POSITIVE OUTLIERS (scored, all policies):")
    for t in all_sorted[-3:][::-1]:
        print(f"     {t['symbol']:12s} pnl=${t['pnl']:8.2f} cv2={t['cv2'][0]:6s} "
              f"relaxed={t['rv2'][0]:6s} v2score={t['v2_score']}")

    # ── Decision rule ──
    print("\n=== DECISION RULE (user-specified) ===")
    pf_cur, pf_rel = cur_m["profit_factor"], rel_m["profit_factor"]
    exp_cur, exp_rel = cur_m["expectancy"], rel_m["expectancy"]
    n_cur, n_rel = cur_m["accepted"], rel_m["accepted"]
    mdd_cur, mdd_rel = cur_m["max_drawdown"], rel_m["max_drawdown"]
    pf_ok = pf_rel > pf_cur
    exp_ok = exp_rel > exp_cur
    count_ok = n_rel >= n_cur and n_rel >= 1
    mdd_ok = mdd_rel <= mdd_cur * 1.2 + 1.0  # not materially worse (<=20% +$1 cushion)
    print(f"  PF        : current={pf_cur:.3f}  relaxed={pf_rel:.3f}  improved={pf_ok}")
    print(f"  Expectancy: current=${exp_cur:.3f}  relaxed=${exp_rel:.3f}  improved={exp_ok}")
    print(f"  Trade cnt : current={n_cur}  relaxed={n_rel}  adequate={count_ok}")
    print(f"  Max DD    : current=${mdd_cur:.2f}  relaxed=${mdd_rel:.2f}  not-much-worse={mdd_ok}")
    verdict = "RELAXED_V2" if (pf_ok and exp_ok and count_ok and mdd_ok) else "KEEP CURRENT_V2"
    print(f"  >>> VERDICT: {verdict}")

    if args.json:
        out = {
            "current_v2": cur_m, "relaxed_v2": rel_m, "no_filter_all": all42,
            "rescued": [{"symbol": t["symbol"], "side": t["side"], "pnl": t["pnl"],
                         "v2_score": t["v2_score"], "reason": t["rv2"][1]}
                        for t in rescued],
            "verdict": verdict,
        }
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
