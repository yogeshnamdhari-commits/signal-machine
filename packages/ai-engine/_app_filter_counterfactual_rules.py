#!/usr/bin/env python3
"""
App Profit Filter — RULE COUNTERFACTUAL SWEEP (READ-ONLY).

Every candidate App-layer selectivity rule is tested on the FULL authoritative
149-trade corpus as a strict counterfactual: KEEP = what the rule would have
admitted, EXCLUDED = what it would have removed. A rule is only worth
activating if the EXCLUDED population is demonstrably negative-expectancy AND
removing it genuinely lifts PF/expectancy/net-PnL WITHOUT cutting the winner
distribution.

Only pre-admission evidence is used (live-sheet/signals at entry time) so the
rule is enforceable in app_layer/profit_filter.py. Nothing is activated here.

Usage: python _app_filter_counterfactual_rules.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-excluded", action="store_true",
                    help="print the EXCLUDED symbols per rule")
    args = ap.parse_args()

    con = sqlite3.connect("data/institutional_v1.db")
    con.row_factory = sqlite3.Row
    rows = list(con.execute("SELECT * FROM positions")) + \
        list(con.execute("SELECT * FROM positions_archive"))
    sig = {r["id"]: r for r in con.execute("SELECT * FROM signals")}

    C = []
    for t in rows:
        sid = t["signal_id"] or ""
        s = sig.get(sid)
        C.append(dict(
            sid=sid, sym=t["symbol"], side=t["side"], pnl=t["pnl"] or 0.0,
            conf=t["confidence"] or 0,
            regime_raw=str(t["regime"] or ""),
            at_open=str(t["at_open_regime"] or ""),
            has_sig=s is not None,
            cvd=(s["cvd"]) if s else None,
            delta=(s["delta"]) if s else None,
            oi=(s["oi_delta"]) if s else None,
            funding=(s["funding_rate"]) if s else None,
            flow=(s["exchange_flow"]) if s else None,
            sweep=(s["sweep_score"]) if s else None,
            fvg=(s["fvg_score"]) if s else None,
            market_regime=(s["market_regime"]) if s else "",
            mtf=(s["mtf_alignment"]) if s else None,
        ))

    def metrics(xs):
        n = len(xs)
        pn = [x["pnl"] for x in xs]
        w = sum(1 for p in pn if p > 0); l = n - w
        gp = sum(p for p in pn if p > 0); gl = abs(sum(p for p in pn if p <= 0))
        net = sum(pn)
        pf = (gp / gl) if gl else (float("inf") if gp else 0)
        eq = peak = dd = 0.0
        for p in pn:
            eq += p; peak = max(peak, eq); dd = max(dd, peak - eq)
        return dict(n=n, w=w, l=l, wr=round(100.0*w/n, 1) if n else 0.0,
                    pf=round(pf, 2), exp=round(net/n, 2) if n else 0.0,
                    net=round(net, 2), dd=round(dd, 2))

    base = metrics(C)
    print(f"CORPUS n={base['n']}  BASELINE: {base}")

    def rule(name, keep, excl_note=""):
        keep_ids = {id(x) for x in keep}
        excl = [x for x in C if id(x) not in keep_ids]
        km = metrics(keep)
        em = metrics(excl)
        verdict = []
        if km["pf"] > 1.0 and km["exp"] > 0:
            verdict.append("PF>1 & Exp>0")
        if km["net"] > base["net"] + 20 and km["wr"] >= base["wr"]:
            verdict.append("net+ & WR+")
        if km["net"] > base["net"] + 20 and em["exp"] < -0.5:
            verdict.append("excluded-Exp")
        print(f"\nRULE: {name}")
        print(f"  KEEP    : {km}")
        print(f"  EXCLUDED: n={em['n']} {em}" + (f"  {excl_note}" if excl_note else ""))
        if args.show_excluded and em["n"] <= 60:
            print("  excluded:", ", ".join(f"{x['sym']}({x['pnl']:+.1f})" for x in excl))
        if verdict:
            print(f"  >>> STRONG: {'; '.join(verdict)}")

    # Coarse data-completeness rule: scored inputs MISSING/zero at admission
    # (the user's own data-quality principle: missing != neutral).
    def has_evidence(x, fields):
        if not x["has_sig"]:
            return False
        return all((x[f] is not None and x[f] != 0) for f in fields)

    # Rule 1: CVD evidence present AND aligned (bullish LONG / bearish SHORT)
    rule("1. Require CVD evidence; reject anti-CVD (LONG+cvd<0 | SHORT+cvd>0)",
         [x for x in C if not (x["cvd"] is not None and x["cvd"] != 0 and
                               ((x["side"] == "LONG" and x["cvd"] < 0) or
                                (x["side"] == "SHORT" and x["cvd"] > 0)))],
         "note: only rejects when CVD conflict is provable (not data absence)")

    # Rule 2: data-completeness — require cvd evidence (present, non-zero) OR
    # no-decision. Missing CVD = UNKNOWN, never neutral.
    rule("2. Missing CVD evidence -> NO_APP_DECISION (not neutral score)",
         [x for x in C if has_evidence(x, ("cvd",))])

    # Rule 3: require evidence on the full directional set
    rule("3. Require cvd+delta+oi+funding+flow evidence -> NO_APP_DECISION",
         [x for x in C if has_evidence(x, ("cvd", "delta", "oi", "funding", "flow"))])

    # Rule 4: OI evidence required
    rule("4. Require OI evidence (oi_delta present) -> NO_APP_DECISION",
         [x for x in C if has_evidence(x, ("oi",))])

    # Rule 5: low-confidence (<0.6) rejected
    rule("5. Reject confidence <= 0.60", [x for x in C if x["conf"] > 0.60])

    # Rule 6: low-confidence OR unknown regime rejected
    rule("6. Reject conf<=0.60 OR unknown regime",
         [x for x in C if not (x["conf"] <= 0.60 or not x["market_regime"])])

    # Rule 7: any anti-CVD asked strictly (no requirement of evidence presence)
    rule("7. Reject anti-CVD (LONG+cvd<0 | SHORT+cvd>0) ONLY (no completeness)",
         [x for x in C if not ((x["cvd"] or 0) < 0 and x["side"] == "LONG") and
                          not ((x["cvd"] or 0) > 0 and x["side"] == "SHORT")])

    # Rule 8: anti-CVD OR anti-delta (LONG+delta<0 | SHORT+delta>0)
    rule("8. Reject anti-CVD OR anti-delta (sign conflicts)",
         [x for x in C if not (((x["cvd"] or 0) < 0 and x["side"] == "LONG") or
                               ((x["cvd"] or 0) > 0 and x["side"] == "SHORT") or
                               ((x["delta"] or 0) < 0 and x["side"] == "LONG") or
                               ((x["delta"] or 0) > 0 and x["side"] == "SHORT"))])

    # Rule 9: combined completeness on directional set but tolerant per-field:
    # only reject when a field is PRESENT and ALIGNED against the side.
    rule("9. Reject any present-field conflict among cvd/delta/oi/flow",
         [x for x in C if not
             (x["cvd"] not in (None, 0) and ((x["cvd"] < 0) == (x["side"] == "LONG")) or
              x["delta"] not in (None, 0) and ((x["delta"] < 0) == (x["side"] == "LONG")) or
              x["oi"] not in (None, 0) and ((x["oi"] < 0) == (x["side"] == "LONG")) or
              x["flow"] not in (None, 0) and ((x["flow"] < 0) == (x["side"] == "LONG")))])

    # LOO for the strongest rule so far (rule 9): does it survive dropping each
    # of the top-5 losers and the top winner?
    print("\n=== LOO STABILITY — Rule 9 (top-5 losers + top winner removed) ===")
    import heapq
    top_losers = heapq.nsmallest(5, C, key=lambda x: x["pnl"])
    top_winner = heapq.nlargest(1, C, key=lambda x: x["pnl"])
    for tag, drop in [("none", []), ("worst", top_losers[:1]),
                      ("top2", top_losers[:2]), ("top5", top_losers[:5]),
                      ("best", top_winner)]:
        rem = {id(x) for x in drop}
        keep_ids = set()
        for x in C:
            if id(x) in rem:
                continue
            if not (x["cvd"] not in (None, 0) and ((x["cvd"] < 0) == (x["side"] == "LONG")) or
                    x["delta"] not in (None, 0) and ((x["delta"] < 0) == (x["side"] == "LONG")) or
                    x["oi"] not in (None, 0) and ((x["oi"] < 0) == (x["side"] == "LONG")) or
                    x["flow"] not in (None, 0) and ((x["flow"] < 0) == (x["side"] == "LONG"))):
                keep_ids.add(id(x))
        keeps = [x for x in C if id(x) in keep_ids]
        m = metrics(keeps)
        print(f"  rule9 + drop {tag:5s}: {m}")

if __name__ == "__main__":
    main()