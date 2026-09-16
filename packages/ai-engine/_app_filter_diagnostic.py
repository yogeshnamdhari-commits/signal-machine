#!/usr/bin/env python3
"""
App Profit Filter v2 — Phase-A Diagnostic with OUTLIER_FLAG + Leave-One-Out Robustness.

READ-ONLY. Links scored app_profit_filter_decisions to actual closed trades
(positions_archive by signal_id), then for every rejection reason computes:
  - P/L with all trades
  - P/L excluding largest winner   (leave-one-out: drop max)
  - P/L excluding largest loser    (leave-one-out: drop min)
  - OUTLIER_FLAG per trade
  - Cohort comparison: ALL vs ACCEPTED vs REJECTED vs WATCH vs NO_APP_DECISION

Outlier rule (z-score on the |P&L| within the reason group):
  flag = |P&L - mean| > 2.0 * std   (a trade whose P/L dominates the group)

Usage:
    python _app_filter_diagnostic.py
    python _app_filter_diagnostic.py --backfill   # write outcomes back into decisions
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
from collections import defaultdict

DB = "data/institutional_v1.db"
REJECTION_REASONS = {
    "HARD_CONFLICT:REGIME": "HARD_CONFLICT:REGIME",
    "HARD_CONFLICT:OI_FUNDING": "HARD_CONFLICT:OI_FUNDING",
    "HARD_CONFLICT:CVD": "HARD_CONFLICT:CVD",
    "HARD_CONFLICT:DELTA": "HARD_CONFLICT:DELTA",
    "HARD_CONFLICT:FLOW": "HARD_CONFLICT:FLOW",
    "HARD_CONFLICT:LIQUIDITY": "HARD_CONFLICT:LIQUIDITY",
    "HARD_CONFLICT": "HARD_CONFLICT",
    "BAND:WATCH": "BAND:WATCH",
    "BAND:LOW_SCORE": "BAND:LOW_SCORE",
    "DATA_STALE": "DATA_STALE",
    "NO_LIVE_SHEET_DATA": "NO_LIVE_SHEET_DATA",
    "REGIME_CONFIDENCE": "REGIME_CONFIDENCE",
}


def reason_key(reason: str) -> str:
    return REJECTION_REASONS.get(reason, "ACCEPT")


def link_decisions(con: sqlite3.Connection) -> list[dict]:
    decisions = con.execute(
        "SELECT symbol, side, decision, quality_score, reason, signal_id "
        "FROM app_profit_filter_decisions WHERE quality_score > 0 ORDER BY timestamp"
    ).fetchall()
    positions = {p[0]: p for p in con.execute(
        "SELECT signal_id, pnl, realized_r, outcome, exit_reason "
        "FROM positions_archive").fetchall()}
    linked = []
    for sym, side, decision, score, reason, sid in decisions:
        p = positions.get(sid)
        record = dict(
            sym=sym, side=side, decision=decision, score=score,
            reason=reason_key(reason), sid=sid,
            pnl=(p[1] if p else None), r=(p[2] if p else None),
            outcome=(p[3] if p else None), exit_reason=(p[4] if p else None),
            has_trade=p is not None,
        )
        linked.append(record)
    return linked


def leave_one_out(pnls: list[float]) -> dict:
    """Robustness: full-set P/L vs excluding largest winner / largest loser."""
    if not pnls:
        return {"all": 0.0, "drop_max_winner": 0.0, "drop_max_loser": 0.0, "n": 0}
    if len(pnls) == 1:
        return {"all": pnls[0], "drop_max_winner": 0.0, "drop_max_loser": 0.0, "n": 1}
    full = sum(pnls)
    drop_winner = full - max(pnls)
    drop_loser = full - min(pnls)
    return {"all": full, "drop_max_winner": drop_winner, "drop_max_loser": drop_loser, "n": len(pnls)}


def flag_outlier(grp_pnls: list[float], pnl: float, r: float | None) -> bool:
    """OUTLIER_FLAG: |R| >= 3.0 (anomalous excursion, SL-sized) OR z-score > 2.0 in-group."""
    if r is not None and abs(r) >= 3.0:
        return True
    if len(grp_pnls) < 2:
        return False
    mean = statistics.mean(grp_pnls)
    stdev = statistics.stdev(grp_pnls) if len(grp_pnls) > 1 else 0.0
    if stdev == 0:
        return False
    return abs(pnl - mean) > 2.0 * stdev


def print_reason_table(linked: list[dict]) -> None:
    by_reason: dict[str, list[dict]] = defaultdict(list)
    for r in linked:
        by_reason[r["reason"]].append(r)

    print("=== PER-REASON: OUTLIER_FLAG + LEAVE-ONE-OUT (USD P/L) ===")
    head = f"{'REASON':20s} {'n':>2s} {'W':>2s} {'L':>2s} {'ALL':>9s} {'EXCL+WIN':>9s} {'EXCL-LOSER':>10s}   OUTLIERS"
    print(head)
    print("-" * len(head))
    for k in sorted(by_reason, key=lambda x: -len(by_reason[x])):
        v = by_reason[k]
        traded = [r for r in v if r.get("has_trade")]
        pnls = [r["pnl"] for r in traded]
        wins = sum(1 for r in traded if r.get("outcome") == "win")
        losses = sum(1 for r in traded if r.get("outcome") == "loss")
        loo = leave_one_out(pnls)
        outlier_flags = [r["sym"] for r in traded if flag_outlier(pnls, r["pnl"], r.get("r"))]
        outliers = ",".join(outlier_flags) if outlier_flags else "-"
        flagged_any = len(traded) != len(loo)  # placeholder no-op
        print(
            f"{k:20s} {len(v):2d} {wins:2d} {losses:2d} "
            f"{loo['all']:9.2f} {loo['drop_max_winner']:9.2f} {loo['drop_max_loser']:10.2f}   {outliers}"
        )
    print()


def print_score_bands(linked: list[dict]) -> None:
    print("=== SCORE BANDS x OUTCOME ===")
    bands = [(90, 100), (80, 89), (70, 79), (60, 69), (0, 59)]
    for lo, hi in bands:
        v = [r for r in linked if lo <= r["score"] <= hi and r.get("has_trade")]
        if not v:
            print(f"  {lo:2d}-{hi:3d}: n=0")
            continue
        pnls = [r["pnl"] for r in v]
        wins = sum(1 for r in v if r.get("outcome") == "win")
        losses = sum(1 for r in v if r.get("outcome") == "loss")
        execs = sum(1 for r in v if r["decision"] in ("ACCEPT", "EXECUTE"))
        loo = leave_one_out(pnls)
        print(
            f"  {lo:2d}-{hi:3d}: n={len(v):2d} accept={execs} {wins}W/{losses}L "
            f"net=${loo['all']:8.2f} | excl+win ${loo['drop_max_winner']:8.2f} | excl-loser ${loo['drop_max_loser']:8.2f}"
        )
    print()


def print_cohort_table(linked: list[dict]) -> None:
    """ALL vs ACCEPTED vs REJECTED vs WATCH (v2 #8 comparison)."""
    print("=== COHORTS: ALL vs ACCEPTED vs REJECTED vs WATCH (USD P/L) ===")
    head = f"{'COHORT':16s} {'n':>3s} {'W':>2s} {'L':>2s} {'WIN%':>5s} {'NET':>10s} {'EXCL+WIN':>10s} {'EXCL-LOSER':>11s}"
    print(head)
    print("-" * len(head))
    cohorts = {"ALL": linked, "ACCEPTED": [], "REJECTED": [], "WATCH": []}
    for r in linked:
        if r["decision"] in ("ACCEPT", "EXECUTE"):
            cohorts["ACCEPTED"].append(r)
        elif r["decision"] == "REJECT":
            cohorts["REJECTED"].append(r)
        elif r["decision"] == "WATCH":
            cohorts["WATCH"].append(r)
    for k, v in cohorts.items():
        traded = [r for r in v if r.get("has_trade")]
        pnls = [r["pnl"] for r in traded]
        wins = sum(1 for r in traded if r.get("outcome") == "win")
        losses = sum(1 for r in traded if r.get("outcome") == "loss")
        loo = leave_one_out(pnls)
        winrate = 100.0 * wins / (wins + losses) if (wins + losses) else 0.0
        print(
            f"{k:16s} {len(v):3d} {wins:2d} {losses:2d} {winrate:5.1f} "
            f"{loo['all']:10.2f} {loo['drop_max_winner']:10.2f} {loo['drop_max_loser']:11.2f}"
        )
    print()


def print_side_analysis(linked: list[dict]) -> None:
    print("=== LONG vs SHORT x OUTCOME ===")
    for side in ("LONG", "SHORT"):
        v = [r for r in linked if r["side"] == side and r.get("has_trade")]
        if not v:
            print(f"  {side}: no traded records")
            continue
        pnls = [r["pnl"] for r in v]
        wins = sum(1 for r in v if r.get("outcome") == "win")
        losses = sum(1 for r in v if r.get("outcome") == "loss")
        loo = leave_one_out(pnls)
        flagged = [r["sym"] for r in v if flag_outlier(pnls, r["pnl"], r.get("r"))]
        print(
            f"  {side:5s}: n={len(v):2d} {wins}W/{losses}L net=${loo['all']:8.2f} "
            f"| excl+win ${loo['drop_max_winner']:8.2f} | excl-loser ${loo['drop_max_loser']:8.2f} "
            f"| OUTLIER_FLAG={','.join(flagged) or '-'}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="App Profit Filter v2 diagnostic")
    parser.add_argument("--backfill", action="store_true",
                        help="write backfilled outcomes onto decision rows")
    args = parser.parse_args()

    con = sqlite3.connect(DB)
    if args.backfill:
        con.execute(
            "UPDATE app_profit_filter_decisions SET outcome_pnl=NULL, outcome_r=NULL, "
            "outcome_status='', exit_reason='' WHERE outcome_status != ''"
        )
        con.commit()
        con.execute("""
            UPDATE app_profit_filter_decisions AS d SET
              outcome_pnl = (SELECT pnl FROM positions_archive p WHERE p.signal_id = d.signal_id ORDER BY p.closed_at DESC LIMIT 1),
              outcome_r = (SELECT realized_r FROM positions_archive p WHERE p.signal_id = d.signal_id ORDER BY p.closed_at DESC LIMIT 1),
              outcome_status = 'closed',
              exit_reason = (SELECT exit_reason FROM positions_archive p WHERE p.signal_id = d.signal_id ORDER BY p.closed_at DESC LIMIT 1)
            WHERE EXISTS (SELECT 1 FROM positions_archive p WHERE p.signal_id = d.signal_id)
        """)
        con.commit()
        print("Outcomes backfilled onto decision rows.\n")
    linked = link_decisions(con)
    con.close()

    ex = [r for r in linked if r["decision"] in ("ACCEPT", "EXECUTE")]
    rj = [r for r in linked if r["decision"] not in ("ACCEPT", "EXECUTE")]

    print(f"TOTAL SCORED: {len(linked)} | ACCEPT {len(ex)} | REJECT/WATCH {len(rj)}\n")

    print_reason_table(linked)
    print_cohort_table(linked)
    print_score_bands(linked)
    print_side_analysis(linked)

    # Counterfactual
    rj_pnls = [r["pnl"] for r in rj if r.get("has_trade")]
    ex_pnls = [r["pnl"] for r in ex if r.get("has_trade")]
    rj_loo = leave_one_out(rj_pnls)
    eng_off = sum(rj_loo.values())
    print("=== COUNTERFACTUAL (filter-on: block all rejects) ===")
    print(f"  Filters OFF net (rejects actually traded):  ${rj_loo['all']:8.2f}  ({len(rj_pnls)} trades)")
    print(f"    excl largest winner: ${rj_loo['drop_max_winner']:8.2f}")
    print(f"    excl largest loser:  ${rj_loo['drop_max_loser']:8.2f}")
    print(f"  Filters ON net (only Accepted trades kept):  ${sum(ex_pnls):8.2f}  ({len(ex_pnls)} trades)")
    print(f"  Net improvement (damage saved):             ${rj_loo['all'] - sum(ex_pnls):8.2f}")
    print()
    no_trade = [r for r in linked if not r.get("has_trade")]
    if no_trade:
        print(f"NOTE: {len(no_trade)} scored decisions had no matching closed trade: "
              f"{', '.join(r['sym'] for r in no_trade)}")


if __name__ == "__main__":
    main()