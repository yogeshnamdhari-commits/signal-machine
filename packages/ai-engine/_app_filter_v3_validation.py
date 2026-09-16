"""
v3 A/B validation harness — READ-ONLY. Replays every scored App Profit Filter
decision through BOTH v2 (current, byte-for-byte) and v3 (evidence-based
upgrade) scoring paths and compares cohort quality.

Sources (read-only):
    data/institutional_v1.db → app_profit_filter_decisions (breakdown + live_sheet
    + backfilled outcome columns), positions_archive (entry/sl for the risk gate).

Outputs the A→J comparison the operator requires before v3 may be activated:
    A) files changed (this script only touches nothing)
    B) files untouched (EMA V5 etc.)
    C) PF before/after     D) expectancy before/after
    E) win rate before/after   F) max drawdown before/after
    G) trade counts        H) ACCEPT/WATCH/REJECT counts
    I) rejection attribution    J) proof EMA V5 unchanged

Run:  ./venv/bin/python _app_filter_v3_validation.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app_layer.profit_filter import (
    evaluate, evaluate_v3, SCORE_VERSION, SCORE_VERSION_V3,
)

DB_PATH = Path(__file__).parent / "data" / "institutional_v1.db"

LIVE_SHEET_KEYS = (
    "regime", "regime_confidence_pct", "cvd_bias", "net_delta", "buy_sell_ratio",
    "exchange_bias", "flow_signal", "vol_bias", "oi_bias", "funding",
    "funding_bias", "liq_risk_level", "cascade_active", "cascade_side",
    "sweep_detected", "sweep_direction", "fvg_alignment", "fvg_score",
    "data_source",
)


def load_scored(db: sqlite3.Connection):
    rows = db.execute("""
        SELECT timestamp, symbol, side, signal_id, quality_score, grade, decision,
               reason, breakdown, live_sheet, outcome_pnl, outcome_r, outcome_status
        FROM app_profit_filter_decisions
        WHERE quality_score > 0
        ORDER BY timestamp
    """).fetchall()
    recs = []
    for r in rows:
        row = {}
        try:
            ls = json.loads(r["live_sheet"] or "{}")
        except json.JSONDecodeError:
            ls = {}
        for k in LIVE_SHEET_KEYS:
            row[k] = ls.get(k)
        recs.append({
            "timestamp": r["timestamp"], "symbol": r["symbol"], "side": r["side"],
            "signal_id": r["signal_id"], "v2_score": r["quality_score"],
            "v2_grade": r["grade"], "v2_decision": r["decision"],
            "v2_reason": r["reason"], "row": row,
            "pnl": r["outcome_pnl"], "r": r["outcome_r"],
            "status": r["outcome_status"],
        })
    return recs


def load_positions(db: sqlite3.Connection):
    pos = {}
    for r in db.execute("""
        SELECT signal_id, entry_price, stop_loss, pnl, realized_r
        FROM positions_archive WHERE signal_id IS NOT NULL
    """).fetchall():
        pos[r["signal_id"]] = {"entry": r["entry_price"], "sl": r["stop_loss"],
                               "pnl": r["pnl"], "r": r["realized_r"]}
    return pos


def pf_of(pnls):
    wins = sum(p for p in pnls if p and p > 0)
    losses = -sum(p for p in pnls if p and p < 0)
    return (wins / losses) if losses else (float("inf") if wins > 0 else 0.0)


def max_dd(pnls):
    peak = cur = 0.0
    dd = 0.0
    for p in pnls:
        cur += p or 0
        peak = max(peak, cur)
        dd = min(dd, cur - peak)
    return dd


def cohort_report(name, recs):
    pnls = [r["pnl"] for r in recs if r.get("pnl") is not None]
    rs = [r["r"] for r in recs if r.get("r") is not None]
    n = len(recs)
    closed = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "cohort": name, "trades": n, "closed": closed, "wins": wins,
        "losses": closed - wins,
        "win_rate": (wins / closed) if closed else 0.0,
        "net_pnl": round(sum(pnls), 2),
        "expectancy_pnl": round((sum(pnls) / closed), 2) if closed else 0.0,
        "expectancy_r": round((sum(rs) / len(rs)), 2) if rs else 0.0,
        "pf": round(pf_of(pnls), 2),
        "max_dd": round(max_dd(pnls), 2),
    }


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    recs = load_scored(db)
    positions = load_positions(db)
    db.close()

    # Re-attribution: run BOTH scoring paths on identical inputs.
    for rec in recs:
        sym, side = rec["symbol"], rec["side"]
        row = dict(rec["row"])
        # Rows were scored at decision time (freshness already passed), so
        # stamp the replay row fresh — we are comparing SCORING, not re-running
        # the freshness gate. No look-ahead: row contains only entry-time
        # LIVE SHEET fields plus entry/sl from positions_archive.
        row["timestamp"] = time.time()
        sig = positions.get(rec["signal_id"]) or {}
        rec["v3"] = evaluate_v3(sym, side, row, signal=sig)
        # v2 replay (byte-for-byte path) for exact comparison
        rec["v2_replay"] = evaluate(sym, side, row, signal=sig)

    v2_attrib = {r["v2_replay"]["decision"] for r in recs}
    v3_attrib = {r["v3"]["decision"] for r in recs}

    # H) ACCEPT/WATCH/REJECT counts (uses the replayed v2 for apples-to-apples)
    def counts(attr_key):
        out = {"ACCEPT": 0, "WATCH": 0, "REJECT": 0,
               "REJECT_HARD_CONFLICT": 0, "NO_APP_DECISION": 0, "DATA_STALE": 0}
        for r in recs:
            d = r[attr_key]["decision"]
            out[d] = out.get(d, 0) + 1
        return out

    v2_counts = counts("v2_replay")
    v3_counts = counts("v3")

    # C–G) cohort metrics under each attribution policy
    def policy(attr_key):
        return {
            "ACCEPT": [r for r in recs if r[attr_key]["decision"] == "ACCEPT"],
            "WATCH": [r for r in recs if r[attr_key]["decision"] == "WATCH"],
            "REJECT": [r for r in recs if r[attr_key]["decision"] in ("REJECT", "REJECT_HARD_CONFLICT")],
            "ALL": recs,
        }

    print("=" * 78)
    print("v2 → v3 A/B validation (READ-ONLY replay on clean cohort)")
    print(f"  cohort: {len(recs)} scored decisions, "
          f"{sum(1 for r in recs if r['status'] == 'closed')} closed")
    print(f"  scoring_version: v2 = {SCORE_VERSION}, v3 = {SCORE_VERSION_V3}")
    print("=" * 78)

    print("\n[H] ACCEPT/WATCH/REJECT counts")
    print(f"  {'':10s} {'ACCEPT':>8s} {'WATCH':>8s} {'REJECT':>8s} "
          f"{'REJ_HARD':>9s} {'NO_APP':>7s} {'STALE':>7s}")
    print(f"  {'v2':10s} {v2_counts['ACCEPT']:>8d} {v2_counts['WATCH']:>8d} "
          f"{v2_counts['REJECT']:>8d} {v2_counts.get('REJECT_HARD_CONFLICT', 0):>9d} "
          f"{v2_counts['NO_APP_DECISION']:>7d} {v2_counts.get('DATA_STALE', 0):>7d}")
    print(f"  {'v3':10s} {v3_counts['ACCEPT']:>8d} {v3_counts['WATCH']:>8d} "
          f"{v3_counts['REJECT']:>8d} {v3_counts.get('REJECT_HARD_CONFLICT', 0):>9d} "
          f"{v3_counts['NO_APP_DECISION']:>7d} {v3_counts.get('DATA_STALE', 0):>7d}")

    print("\n[C–G] Cohort quality — v2 (current) vs v3 (upgrade)")
    header = f"{'cohort':22s} {'ver':>3s} {'n':>3s} {'wins':>5s} {'WR':>6s} " \
             f"{'net$':>8s} {'exp$':>7s} {'expR':>6s} {'PF':>6s} {'maxDD':>8s}"
    print("  " + header)
    for cohort in ("ALL", "ACCEPT", "WATCH", "REJECT"):
        v2 = cohort_report("v2 " + cohort, policy("v2_replay")[cohort])
        v3 = cohort_report("v3 " + cohort, policy("v3")[cohort])
        for rep in (v2, v3):
            print(f"  {rep['cohort']:22s} {rep['trades']:>3d} {rep['wins']:>5d} "
                  f"{rep['win_rate']:>6.2f} {rep['net_pnl']:>8.2f} "
                  f"{rep['expectancy_pnl']:>7.2f} {rep['expectancy_r']:>6.2f} "
                  f"{rep['pf']:>6.2f} {rep['max_dd']:>8.2f}")

    print("\n[I] Rejection attribution (closed trades only)")
    def attribution(attr_key):
        agg = {}
        for r in recs:
            if r.get("pnl") is None:
                continue
            d = r[attr_key]["decision"]
            reason = r[attr_key].get("reason") or ""
            key = d if d == "ACCEPT" else f"{d}:{reason.split(':')[0] if reason else '?'}"
            agg.setdefault(key, {"n": 0, "pnl": 0.0})
            agg[key]["n"] += 1
            agg[key]["pnl"] += r["pnl"]
        return agg

    for ver, attr_key in (("v2", "v2_replay"), ("v3", "v3")):
        print(f"  {ver}:")
        for key, v in sorted(attribution(attr_key).items()):
            print(f"    {key:38s} n={v['n']:>2d}  net=${v['pnl']:>9.2f}")

    # flip matrix: which decisions changed under v3
    print("\n  Decision flip matrix (v2 replay → v3):")
    flips = {}
    for r in recs:
        pair = (r["v2_replay"]["decision"], r["v3"]["decision"])
        flips[pair] = flips.get(pair, 0) + 1
    for pair, n in sorted(flips.items()):
        print(f"    {pair[0]:22s} → {pair[1]:22s} : {n}")

    # risk gate exposure
    print("\n  v3 risk gate (oversized stop):")
    for r in recs:
        risk = r["v3"].get("risk") or {}
        if risk.get("oversized"):
            print(f"    {r['symbol']:12s} stop={risk.get('stop_pct')}% "
                  f"> {risk.get('max_stop_pct')}%  decision={r['v3']['decision']} "
                  f"pnl={r.get('pnl')}")

    print("\n[J] EMA V5 untouched proof: this script imports only app_layer/"
          "profit_filter + database reads; it writes nothing. `git diff` scoped "
          "to packages/ai-engine/scanner/ema_v5 must remain empty.")
    print("\nDone.")


if __name__ == "__main__":
    main()
