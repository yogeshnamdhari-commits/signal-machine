#!/usr/bin/env python3
"""
App Profit Filter v2 — READ-ONLY admission-BAND counterfactual (A/B/C/D).

Tests four score/admission-band policies against ALL 42 linked historical
outcomes (positions_archive joined by signal_id). Recomputes the v2 App
Quality Score from each decision's stored per-component breakdown x v2 weights,
so every policy is scored on the SAME basis.

Policies (user-specified; NONE activated, NONE production):
  A CURRENT          >=80 ACCEPT · 70-79 ACCEPT w/o hard conflict · 60-69 WATCH · <60 REJECT
  B CONSERVATIVE     >=75 ACCEPT · 70-74 ACCEPT w/o hard conflict · 60-69 WATCH · <60 REJECT
  C CONFLICT-FREE    >=70 ACCEPT w/o hard conflict · 60-69 ACCEPT iff
                       (no hard conflict AND fresh AND EMA-direction agrees
                        AND >=2 confirmation factors agree) · <60 REJECT
  D WATCH-CONVERSION >=80 ACCEPT · 70-79 ACCEPT w/o hard conflict · 60-69 ACCEPT iff
                       (fresh AND EMA-direction agrees AND no hard conflict AND
                        (liquidity/sweep confirmation OR CVD+Delta confirmation)) · <60 REJECT

Confirmation factors (for C): components whose component score AGREES with the
signal side among regime/cvd/delta/flow/oi_funding.
EMA direction agrees: LONG needs BUY_MODE regime, SHORT needs SELL_MODE (from
positions_archive.regime = EMA V5 state at open).
Missing/stale data -> NO_APP_DECISION under every policy (data-quality, never
a trade verdict), matching the live filter.

Optimization target: "how many additional PROFITABLE trades per additional
LOSING trade", and PF / expectancy over raw win rate.

Usage:
    python _app_filter_bands.py
    python _app_filter_bands.py --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict

DB = "data/institutional_v1.db"

V2_WEIGHTS = {
    "cvd": 0.20, "oi_funding": 0.15, "delta": 0.15, "flow": 0.15,
    "liquidity": 0.10, "sweep_fvg": 0.10, "regime": 0.10, "volume": 0.05,
}
assert abs(sum(V2_WEIGHTS.values()) - 1.0) < 1e-9

DIRECTIONAL = ("regime", "cvd", "delta", "flow", "oi_funding")
CONFIRMATION = ("regime", "cvd", "delta", "flow", "oi_funding")
CONFLICT_THRESHOLD = 45.0


def v2_score(bd: dict) -> float:
    return max(0.0, min(100.0, round(
        sum((bd.get(k) or {}).get("score", 0) * w for k, w in V2_WEIGHTS.items()), 1)))


def strong_conflicts(bd: dict) -> dict:
    return {k: (bd.get(k) or {}).get("detail", "")
            for k in DIRECTIONAL
            if (bd.get(k) or {}).get("conflict") and (bd.get(k) or {}).get("score", 100) < CONFLICT_THRESHOLD}


def liquidity_conflict(bd: dict) -> bool:
    liq = bd.get("liquidity") or {}
    return bool(liq.get("conflict") and liq.get("score", 100) < CONFLICT_THRESHOLD)


def hard_conflict(bd: dict) -> bool:
    return bool(strong_conflicts(bd)) or liquidity_conflict(bd)


def confirmations(bd: dict) -> int:
    return sum(1 for k in CONFIRMATION if (bd.get(k) or {}).get("agreed"))


def agreed(bd: dict, k: str) -> bool:
    return bool((bd.get(k) or {}).get("agreed"))


def policy_decide(name: str, score: float, bd: dict, ema_ok: bool) -> tuple[str, str]:
    hc = hard_conflict(bd)
    fresh = True  # scored decisions had LIVE SHEET data present
    if name == "A":
        if score >= 80:
            return "ACCEPT", ""
        if 70 <= score < 80:
            return ("REJECT", "HARD_CONFLICT") if hc else ("ACCEPT", "")
        if 60 <= score < 70:
            return "WATCH", "BAND:WATCH"
        return "REJECT", "BAND:LOW_SCORE"
    if name == "B":
        if score >= 75:
            return "ACCEPT", ""
        if 70 <= score < 75:
            return ("REJECT", "HARD_CONFLICT") if hc else ("ACCEPT", "")
        if 60 <= score < 70:
            return "WATCH", "BAND:WATCH"
        return "REJECT", "BAND:LOW_SCORE"
    if name == "C":
        if score >= 70:
            return ("REJECT", "HARD_CONFLICT") if hc else ("ACCEPT", "")
        if 60 <= score < 70:
            ok = (not hc) and fresh and ema_ok and confirmations(bd) >= 2
            return ("ACCEPT", "BAND:60_69_COND") if ok else ("WATCH", "BAND:WATCH")
        return "REJECT", "BAND:LOW_SCORE"
    # D
    if score >= 80:
        return "ACCEPT", ""
    if 70 <= score < 80:
        return ("REJECT", "HARD_CONFLICT") if hc else ("ACCEPT", "")
    if 60 <= score < 70:
        cond = ((agreed(bd, "liquidity") or agreed(bd, "sweep_fvg"))
                or (agreed(bd, "cvd") and agreed(bd, "delta")))
        ok = (not hc) and fresh and ema_ok and cond
        return ("ACCEPT", "BAND:60_69_WATCHCONV") if ok else ("WATCH", "BAND:WATCH")
    return "REJECT", "BAND:LOW_SCORE"


POLICIES = ("A", "B", "C", "D")


def load_linked(con: sqlite3.Connection) -> list[dict]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT d.id, d.timestamp, d.symbol, d.side, d.breakdown, d.signal_id,
               p.pnl, p.regime
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
        has = bool(bd)
        rec = {
            "id": r["id"], "timestamp": r["timestamp"], "symbol": r["symbol"],
            "side": r["side"], "pnl": r["pnl"] or 0.0,
            "regime_mode": r["regime"] or "?",
            "has_breakdown": has, "breakdown": bd,
            "v2_score": v2_score(bd) if has else None,
        }
        if has:
            rec["ema_ok"] = ((r["side"] == "LONG" and r["regime"] == "BUY_MODE")
                             or (r["side"] == "SHORT" and r["regime"] == "SELL_MODE"))
            for p in POLICIES:
                rec[p] = policy_decide(p, rec["v2_score"], bd, rec["ema_ok"])
        else:
            rec["ema_ok"] = False
            for p in POLICIES:
                rec[p] = ("NO_APP_DECISION", "NO_DATA_OR_STALE")
        out.append(rec)
    return out


def max_drawdown(pnls: list) -> float:
    peak = eq = mdd = 0.0
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
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    net = sum(pnls)
    pf = (gp / gl) if gl else (float("inf") if gp else 0.0)
    return {
        "trades": n, "wins": wins, "losses": losses,
        "win_rate": (100.0 * wins / n) if n else 0.0,
        "gross_profit": gp, "gross_loss": gl, "profit_factor": pf,
        "expectancy": (net / n) if n else 0.0, "net_pnl": net,
        "max_drawdown": max_drawdown(pnls),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Admission-band counterfactual (read-only)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(DB)
    recs = load_linked(con)
    con.close()

    scored = [r for r in recs if r["has_breakdown"]]
    no_data = [r for r in recs if not r["has_breakdown"]]
    print(f"LINKED TRADES: {len(recs)} | scored: {len(scored)} | "
          f"missing-data/stale (NO_APP_DECISION under all): {len(no_data)}")

    results = {}
    for p in POLICIES:
        acc = [r for r in recs if r[p][0] == "ACCEPT"]
        wat = [r for r in recs if r[p][0] == "WATCH"]
        rej = [r for r in recs if r[p][0] == "REJECT"]
        nod = [r for r in recs if r[p][0] == "NO_APP_DECISION"]
        m = metrics(acc)
        m.update(accepted=len(acc), watch=len(wat), rejected=len(rej),
                 no_decision=len(nod), acc_trades=acc)
        # Breakdowns of the accepted cohort
        m["LONG"] = metrics([t for t in acc if t["side"] == "LONG"])
        m["SHORT"] = metrics([t for t in acc if t["side"] == "SHORT"])
        m["BUY_MODE"] = metrics([t for t in acc if t["regime_mode"] == "BUY_MODE"])
        m["SELL_MODE"] = metrics([t for t in acc if t["regime_mode"] == "SELL_MODE"])
        m["b60_69"] = metrics([t for t in acc if t["v2_score"] is not None and 60 <= t["v2_score"] < 70])
        m["b70_79"] = metrics([t for t in acc if t["v2_score"] is not None and 70 <= t["v2_score"] < 80])
        m["b80p"] = metrics([t for t in acc if t["v2_score"] is not None and t["v2_score"] >= 80])
        results[p] = m

    hdr = f"{'metric':<22s}" + "".join(f"{p:>14s}" for p in POLICIES)
    print("\n=== POLICY x METRIC (ACCEPTED cohort, filter-on) ===")
    print(hdr)
    print("-" * len(hdr))
    def row(label, key):
        vals = []
        for p in POLICIES:
            v = results[p][key]
            vals.append(f"{v:12.2f}" if isinstance(v, float) else f"{v:>12d}")
        print(f"{label:<22s}" + "".join(f"{v:>14s}" for v in vals))
    row("trades (accepted)", "accepted")
    row("watch", "watch")
    row("rejected", "rejected")
    row("no_decision", "no_decision")
    row("profitable", "wins")
    row("losing", "losses")
    row("win rate %", "win_rate")
    row("gross profit $", "gross_profit")
    row("gross loss $", "gross_loss")
    row("profit factor", "profit_factor")
    row("expectancy/trade $", "expectancy")
    row("net PnL $", "net_pnl")
    row("max drawdown $", "max_drawdown")

    def sub(label, key, key2="net_pnl"):
        vals = []
        for p in POLICIES:
            m = results[p][key]
            vals.append(f"n={m['trades']:2d} ${m[key2]:8.2f} PF={m['profit_factor']:.2f} W%={m['win_rate']:4.1f}")
        print(f"{label:<22s}" + "".join(f"{v:>14s}" for v in vals))
    print("\n--- accepted-cohort breakdowns ---")
    sub("LONG", "LONG")
    sub("SHORT", "SHORT")
    sub("BUY_MODE", "BUY_MODE")
    sub("SELL_MODE", "SELL_MODE")
    sub("score 60-69", "b60_69")
    sub("score 70-79", "b70_79")
    sub("score >=80", "b80p")

    # ── Incremental vs A ──
    print("\n=== INCREMENTAL vs CURRENT_V2 (A) ===")
    base_ids = {t["id"] for t in results["A"]["acc_trades"]}
    incr = {}
    for p in ("B", "C", "D"):
        new = [t for t in results[p]["acc_trades"] if t["id"] not in base_ids]
        m = metrics(new)
        m["n_new"] = len(new)
        m["winners"] = sum(1 for t in new if t["pnl"] > 0)
        m["losers"] = sum(1 for t in new if t["pnl"] <= 0)
        m["incr_pnl"] = m["net_pnl"]
        m["incr_expectancy"] = (m["net_pnl"] / len(new)) if new else 0.0
        m["incr_pf"] = m["profit_factor"]
        incr[p] = m
        print(f"  Policy {p}: +{len(new)} additional trades | "
              f"+{m['winners']} winners / +{m['losers']} losers | "
              f"incremental PnL ${m['incr_pnl']:8.2f} | incr PF {m['incr_pf']:.2f} | "
              f"incr expectancy ${m['incr_expectancy']:.2f}/trade")
        for t in new:
            print(f"      NEW {t['symbol']:12s} {t['side']:5s} {t['regime_mode']:9s} "
                  f"v2score={t['v2_score']:5.1f} pnl=${t['pnl']:8.2f} "
                  f"({'PROFIT' if t['pnl'] > 0 else 'LOSS'}) reason={t[p][1] or 'ACCEPT'}")

    # ── Ranking ──
    print("\n=== RANKING (best -> worst) ===")
    def rank_key(p):
        m = results[p]
        return (1.0 if m["expectancy"] > 0 else 0.0,
                m["profit_factor"],
                m["expectancy"],
                m["net_pnl"],
                -m["max_drawdown"],
                m["accepted"])
    ordered = sorted(POLICIES, key=rank_key, reverse=True)
    for i, p in enumerate(ordered, 1):
        m = results[p]
        print(f"  {i}. {p}  exp=${m['expectancy']:+.2f}/t PF={m['profit_factor']:.2f} "
              f"net=${m['net_pnl']:+.2f} DD=${m['max_drawdown']:.2f} "
              f"accepted={m['accepted']} ({m['wins']}W/{m['losses']}L)")

    best = ordered[0]
    if results[best]["expectancy"] <= 0:
        print("\n>>> VERDICT: NO policy produces POSITIVE EXPECTANCY. Keep CURRENT_V2 (A) unchanged.")
    else:
        print(f"\n>>> VERDICT: best candidate = {best} (positive expectancy). Do NOT activate — diagnostics only.")

    if args.json:
        payload = {
            "policies": {p: {k: (v if not isinstance(v, dict) else v) for k, v in results[p].items()
                             if k != "acc_trades"} for p in POLICIES},
            "incremental": {p: {k: v for k, v in incr[p].items() if k != "acc_trades"} for p in ("B", "C", "D")},
        }
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
