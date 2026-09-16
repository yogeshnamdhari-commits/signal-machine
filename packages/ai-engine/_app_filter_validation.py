#!/usr/bin/env python3
"""
App Profit Filter v2 — COHORT VALIDATION framework (READ-ONLY).

Accumulates the "30 clean authoritative closes" evaluation the operator
specified. Run any time; it reports how close the cohort is to 30 clean closes
and computes the FULL metric set on whatever exists. EMA V5 / Smart Money /
LIVE SHEET / execution are never touched.

Clean authoritative closes = linked decisions with a CLOSED positions_archive
trade and a real PnL, EXCLUDING operational/data failures (NO_APP_DECISION).

Metric set (operator-specified, measured at 30 closes):
    PF, expectancy/trade, win rate, net PnL, max drawdown,
    LONG vs SHORT, BUY_MODE vs SELL_MODE,
    confidence 40-50 / 50-60 / 60-70 / 70-80 / 80+,
    OI/Funding, CVD, Delta, Flow, Liquidity/Sweep/FVG, Volume contributions,
    TP1 vs trailing-stop, avg win/loss, largest loss, consecutive losses,
    duplicate signals, operational/data failures (separate).

Thresholds (operator):
    PF > 1 + positive expectancy            -> keep filter, continue validation
    PF 0.9-1.1                               -> diagnose side/regime/confidence buckets
    PF < 0.8 + negative expectancy           -> investigate App-layer admission rules
    operational contamination               -> fix infrastructure/data first

Usage:
    python _app_filter_validation.py
    python _app_filter_validation.py --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict

DB = "data/institutional_v1.db"
TARGET_CLEAN_CLOSES = 30

try:
    from _app_filter_bands import policy_decide, v2_score, hard_conflict
except ImportError:  # fallback single-source-of-truth guard
    from _app_filter_bands import policy_decide, v2_score, hard_conflict

V2_WEIGHTS = {
    "cvd": 0.20, "oi_funding": 0.15, "delta": 0.15, "flow": 0.15,
    "liquidity": 0.10, "sweep_fvg": 0.10, "regime": 0.10, "volume": 0.05,
}
COMPONENTS = ("cvd", "oi_funding", "delta", "flow", "volume", "liquidity", "sweep_fvg", "regime")


def max_drawdown(pnls):
    peak = eq = mdd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def metrics(trades):
    n = len(trades)
    pnls = [t["pnl"] for t in trades]
    wins = sum(1 for t in trades if t["pnl"] > 0)
    losses = sum(1 for t in trades if t["pnl"] <= 0)
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    net = sum(pnls)
    pf = (gp / gl) if gl else (float("inf") if gp else 0.0)
    avg_win = (gp / wins) if wins else 0.0
    avg_loss = (gl / losses) if losses else 0.0
    largest_loss = min(pnls, default=0.0)
    streak = cur = 0
    for t in sorted(trades, key=lambda x: x["timestamp"]):
        if t["pnl"] <= 0:
            cur += 1
            streak = max(streak, cur)
        else:
            cur = 0
    return {
        "n": n, "wins": wins, "losses": losses,
        "win_rate": (100.0 * wins / n) if n else 0.0,
        "gross_profit": gp, "gross_loss": gl, "profit_factor": pf,
        "expectancy": (net / n) if n else 0.0, "net_pnl": net,
        "max_drawdown": max_drawdown(pnls),
        "avg_win": avg_win, "avg_loss": avg_loss, "largest_loss": largest_loss,
        "max_loss_streak": streak,
    }


def bucket_conf(c):
    if c < 40: return "<40"
    if c < 50: return "40-50"
    if c < 60: return "50-60"
    if c < 70: return "60-70"
    if c < 80: return "70-80"
    return "80+"


def main() -> None:
    parser = argparse.ArgumentParser(description="App Filter v2 cohort validation (read-only)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT d.timestamp, d.symbol, d.side, d.decision, d.reason,
               d.signal_confidence, d.breakdown, d.scoring_version, d.signal_id,
               p.pnl, p.outcome, p.status, p.exit_reason, p.current_tp_index,
               p.regime, p.realized_r
        FROM app_profit_filter_decisions d
        LEFT JOIN positions_archive p ON p.signal_id = d.signal_id
        ORDER BY d.timestamp
        """
    ).fetchall()

    linked = []
    open_pos = []
    no_trade = []
    operational = []
    for r in rows:
        rec = {
            "timestamp": r["timestamp"], "symbol": r["symbol"], "side": r["side"],
            "decision": r["decision"], "reason": r["reason"] or "",
            "confidence": r["signal_confidence"] or 0.0,
            "regime_mode": r["regime"] or "?", "exit_reason": r["exit_reason"] or "",
            "tp_index": r["current_tp_index"] or 0, "r": r["realized_r"] or 0.0,
        }
        try:
            bd = json.loads(r["breakdown"] or "{}")
        except json.JSONDecodeError:
            bd = {}
        rec["breakdown"] = bd
        rec["scored"] = bool(bd)
        rec["v2_score"] = v2_score(bd) if bd else None
        if r["decision"] == "NO_APP_DECISION" or r["reason"] in (
                "NO_LIVE_SHEET_DATA", "DATA_STALE", "NO_FRESHNESS_STAMP"):
            operational.append(rec)
            continue
        if r["pnl"] is None:
            if r["status"] == "open":
                open_pos.append(rec)
            else:
                no_trade.append(rec)
            continue
        rec["pnl"] = r["pnl"] or 0.0
        linked.append(rec)

    clean = [t for t in linked]
    # classification under CURRENT_V2 (policy A) for admitted/cohort split
    for t in clean:
        if t["scored"]:
            ema_ok = ((t["side"] == "LONG" and t["regime_mode"] == "BUY_MODE")
                      or (t["side"] == "SHORT" and t["regime_mode"] == "SELL_MODE"))
            t["v2_admit"] = policy_decide("A", t["v2_score"], t["breakdown"], ema_ok)[0]
        else:
            t["v2_admit"] = "UNSCORED"

    print(f"DECISION ROWS: {len(rows)}")
    print(f"  clean authoritative closes (linked+closed+pnl): {len(clean)}")
    print(f"  open positions: {len(open_pos)} | decisions with no position: {len(no_trade)}")
    print(f"  OPERATIONAL/DATA FAILURES (NO_APP_DECISION): {len(operational)}")
    op_by = defaultdict(int)
    for t in operational:
        op_by[t["reason"] or t["decision"]] += 1
    for k, v in sorted(op_by.items(), key=lambda x: -x[1]):
        print(f"      {k}: {v}")
    print(f"\n{'*'*70}\n  TARGET: {TARGET_CLEAN_CLOSES} clean closes — currently {len(clean)}. "
          f"{'REVIEW READY' if len(clean) >= TARGET_CLEAN_CLOSES else 'STILL ACCUMULATING — results directional'}\n{'*'*70}")

    if not clean:
        print("No clean closes yet.")
        return

    # ── Cohort splits ──
    admitted = [t for t in clean if t["v2_admit"] == "ACCEPT"]
    watch = [t for t in clean if t["v2_admit"] == "WATCH"]
    rejected = [t for t in clean if t["v2_admit"] in ("REJECT", "UNSCORED")]

    def show(name, trades, extra=""):
        m = metrics(trades)
        print(f"{name:26s} n={m['n']:3d} {m['wins']:2d}W/{m['losses']:2d}L "
              f"WR={m['win_rate']:5.1f}% PF={m['profit_factor']:5.2f} "
              f"exp=${m['expectancy']:7.2f} net=${m['net_pnl']:9.2f} "
              f"DD=${m['max_drawdown']:7.2f} avgW=${m['avg_win']:.2f} "
              f"avgL=${m['avg_loss']:.2f} maxLoss=${m['largest_loss']:.2f} "
              f"lossStreak={m['max_loss_streak']} {extra}")
        return m

    print("\n=== COHORTS ===")
    m_all = show("ALL clean closes", clean)
    m_acc = show("  ACCEPTED (v2 A)", admitted)
    show("  WATCH", watch)
    show("  REJECTED", rejected)

    print("\n=== LONG vs SHORT (ALL clean) ===")
    show("  LONG", [t for t in clean if t["side"] == "LONG"])
    show("  SHORT", [t for t in clean if t["side"] == "SHORT"])

    print("\n=== BUY_MODE vs SELL_MODE (ALL clean) ===")
    show("  BUY_MODE", [t for t in clean if t["regime_mode"] == "BUY_MODE"])
    show("  SELL_MODE", [t for t in clean if t["regime_mode"] == "SELL_MODE"])

    print("\n=== CONFIDENCE BUCKETS (ALL clean, decisions.signal_confidence) ===")
    conf_sets = defaultdict(list)
    for t in clean:
        conf_sets[bucket_conf(t["confidence"])].append(t)
    for b in ("40-50", "50-60", "60-70", "70-80", "80+", "<40"):
        if conf_sets[b]:
            show(f"  conf {b}", conf_sets[b])

    print("\n=== PER-COMPONENT CONTRIBUTION (scored clean closes: agreed vs conflicted) ===")
    scored_clean = [t for t in clean if t["scored"]]
    if scored_clean:
        for comp in COMPONENTS:
            agree = [t for t in scored_clean if (t["breakdown"].get(comp) or {}).get("agreed")]
            conf = [t for t in scored_clean if (t["breakdown"].get(comp) or {}).get("conflict")]
            a, c = metrics(agree), metrics(conf)
            print(f"  {comp:10s} AGREED   n={a['n']:2d} net=${a['net_pnl']:8.2f} WR={a['win_rate']:4.1f}% PF={a['profit_factor']:5.2f}")
            print(f"  {'':10s} CONFLICT n={c['n']:2d} net=${c['net_pnl']:8.2f} WR={c['win_rate']:4.1f}% PF={c['profit_factor']:5.2f}")

    print("\n=== VOLUME CONFLICT DRILL-DOWN (the identified negative outlier driver) ===")
    vol_conf = [t for t in scored_clean if (t["breakdown"].get("volume") or {}).get("conflict")]
    if vol_conf:
        for t in vol_conf:
            print(f"  {t['symbol']:12s} {t['side']:5s} v2score={t['v2_score']} "
                  f"vol_detail='{(t['breakdown'].get('volume') or {}).get('detail')}' "
                  f"pnl=${t['pnl']:8.2f} ({'WIN' if t['pnl']>0 else 'LOSS'}) exit={t['exit_reason']}")
        vm = metrics(vol_conf)
        print(f"  VOLUME-CONFLICT TOTAL: n={vm['n']} net=${vm['net_pnl']:8.2f} WR={vm['win_rate']:.1f}% PF={vm['profit_factor']:.2f}")
    else:
        print("  no volume-conflict trades in scored clean closes")

    print("\n=== TP1 vs TRAILING vs SL (ALL clean, by exit_reason) ===")
    for key, label in (("take_profit_1", "TP1"), ("trailing_stop", "TRAILING"),
                       ("stop_loss", "SL"), ("take_profit", "TP-other"),
                       ("max_hold_24h", "MAX_HOLD"), ("no_progress_6h", "NO_PROGRESS")):
        grp = [t for t in clean if t["exit_reason"].startswith(key)]
        if grp:
            show(f"  {label}", grp)

    print("\n=== DUPLICATE SIGNALS (same symbol+side within 24h) ===")
    dup = defaultdict(list)
    for t in clean:
        dup[(t["symbol"], t["side"])].append(t)
    dup_count = 0
    for (sym, side), items in dup.items():
        items = sorted(items, key=lambda x: x["timestamp"])
        for a, b in zip(items, items[1:]):
            if b["timestamp"] - a["timestamp"] < 86400:
                dup_count += 1
                print(f"  DUP {sym} {side}: {a['timestamp']:.0f}->{b['timestamp']:.0f} "
                      f"pnls ${a['pnl']:.2f} / ${b['pnl']:.2f}")
    if dup_count == 0:
        print("  no duplicates within 24h")

    print("\n=== OPERATIONAL / DATA FAILURES (separate — excluded from cohorts above) ===")
    print(f"  total NO_APP_DECISION: {len(operational)} (missing/stale LIVE SHEET coverage)")

    # ── Threshold verdict on the ALL cohort ──
    print("\n=== OPERATOR THRESHOLDS (applied to ALL clean closes) ===")
    pf, exp = m_all["profit_factor"], m_all["expectancy"]
    if pf > 1.0 and exp > 0:
        verdict = "KEEP FILTER — continue validation toward 30 clean closes"
    elif 0.9 <= pf <= 1.1:
        verdict = "DIAGNOSE side/regime/confidence buckets (PF 0.9-1.1 band)"
    elif pf < 0.8 and exp < 0:
        verdict = "INVESTIGATE App-layer admission rules (PF<0.8 + negative expectancy)"
    else:
        verdict = "BORDERLINE — keep accumulating; re-evaluate at 30 clean closes"
    print(f"  PF={pf:.3f} expectancy=${exp:.3f} -> {verdict}")
    if len(clean) < TARGET_CLEAN_CLOSES:
        print(f"  NOTE: only {len(clean)} clean closes < {TARGET_CLEAN_CLOSES} — treat all results as directional, not conclusive.")

    if args.json:
        out = {
            "clean_closes": len(clean), "target": TARGET_CLEAN_CLOSES,
            "all": m_all, "accepted": m_acc,
            "operational_failures": dict(op_by),
            "volume_conflict": metrics(vol_conf) if vol_conf else None,
            "verdict": verdict,
        }
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
