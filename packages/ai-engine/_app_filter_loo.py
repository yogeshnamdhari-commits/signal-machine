#!/usr/bin/env python3
"""
App Profit Filter v2 — LEAVE-ONE-OUT (LOO) component analysis (READ-ONLY).

Answers: which App-layer component is genuinely HELPING profitability, and
which is HURTING it — measured on the "30 clean authoritative closes" cohort
(linked decisions with a CLOSED positions_archive trade + real PnL, excluding
operational/data failures).

Method (strictly read-only; nothing activated, no DB writes, no config change):
    For each component X:
      1. remove X's weight from the v2 score and RENORMALIZE the remaining
         weights to 0-100 (so every scenario is scored on the same basis);
      2. remove X from the hard-conflict evaluation;
      3. re-derive the CURRENT_V2 (policy A) admission decision from the LOO
         score;
      4. compute the ACCEPTED-cohort metrics under that scenario;
      5. compare against the CURRENT_V2 baseline.

Outlier handling:
    BLUAIUSDT (-$34.34) dominated the earlier sample. The authoritative
    dataset NEVER deletes it, but every table is ALSO reported on the
    ex-outlier cohort so we can see whether a component truly improves the
    distribution or merely looks bad because of one extreme trade.

Metrics retained per scenario (operator spec):
    trades (accepted), win rate, PF, expectancy, net PnL, delta vs V2,
    largest loss, avg win, avg loss, LONG/SHORT, BUY_MODE/SELL_MODE,
    confidence band, NO_APP_DECISION / operational failures, outlier trades.

Usage:
    python _app_filter_loo.py
    python _app_filter_loo.py --json
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
COMPONENTS = ("cvd", "oi_funding", "delta", "flow", "liquidity", "sweep_fvg", "regime", "volume")
DIRECTIONAL = ("regime", "cvd", "delta", "flow", "oi_funding")
CONFLICT_THRESHOLD = 45.0

# Trades whose removal-from-scoring dominance we must inspect separately.
# The authoritative cohort NEVER drops them; these are only cross-checked.
OUTLIER_SYMBOLS = ("BLUAIUSDT", "LUNA2USDT")


def v2_score(bd: dict) -> float:
    return max(0.0, min(100.0, round(
        sum((bd.get(k) or {}).get("score", 0) * w for k, w in V2_WEIGHTS.items()), 1)))


def v2_score_loo(bd: dict, exclude: str) -> float:
    """v2 score with component `exclude` removed and remaining weights renormalized."""
    s = 0.0
    wsum = 0.0
    for k, w in V2_WEIGHTS.items():
        if k == exclude:
            continue
        s += (bd.get(k) or {}).get("score", 0) * w
        wsum += w
    if not wsum:
        return 0.0
    return max(0.0, min(100.0, round(s / wsum, 1)))


def hard_conflict_loo(bd: dict, exclude: str) -> bool:
    """Hard-conflict with `exclude` removed from the directional/liquidity set."""
    for k in DIRECTIONAL:
        if k == exclude:
            continue
        c = bd.get(k) or {}
        if c.get("conflict") and c.get("score", 100) < CONFLICT_THRESHOLD:
            return True
    if exclude != "liquidity":
        liq = bd.get("liquidity") or {}
        if liq.get("conflict") and liq.get("score", 100) < CONFLICT_THRESHOLD:
            return True
    return False


def decide_A(score: float, hc: bool) -> tuple[str, str]:
    """CURRENT_V2 policy A admission bands (replicated from _app_filter_bands)."""
    if score >= 80:
        return "ACCEPT", ""
    if 70 <= score < 80:
        return ("REJECT", "HARD_CONFLICT") if hc else ("ACCEPT", "")
    if 60 <= score < 70:
        return "WATCH", "BAND:WATCH"
    return "REJECT", "BAND:LOW_SCORE"


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
        "trades": n, "wins": wins, "losses": losses,
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


def load_clean(con) -> tuple[list, list]:
    """Authoritative cohort: linked decisions with closed pnl, minus operational failures.

    Returns (clean_trades, operational_failures).
    """
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT d.timestamp, d.symbol, d.side, d.decision, d.reason,
               d.signal_confidence, d.breakdown, d.scoring_version, d.signal_id,
               p.pnl, p.outcome, p.status, p.exit_reason, p.regime, p.realized_r
        FROM app_profit_filter_decisions d
        LEFT JOIN positions_archive p ON p.signal_id = d.signal_id
        ORDER BY d.timestamp
        """
    ).fetchall()

    clean, open_pos, no_trade, operational = [], [], [], []
    for r in rows:
        rec = {
            "timestamp": r["timestamp"], "symbol": r["symbol"], "side": r["side"],
            "decision": r["decision"], "reason": r["reason"] or "",
            "confidence": r["signal_confidence"] or 0.0,
            "regime_mode": r["regime"] or "?", "exit_reason": r["exit_reason"] or "",
            "r": r["realized_r"] or 0.0, "pnl": None,
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
        clean.append(rec)

    info = {"decision_rows": len(rows), "open": len(open_pos),
            "no_trade": len(no_trade), "operational": len(operational)}
    return clean, info


def scenario_admit(cohort, exclude):
    """Annotate each clean trade with its admission decision under LOO scenario.

    exclude=None is the CURRENT_V2 baseline.
    """
    out = []
    for t in cohort:
        if not t["scored"]:
            out.append((t, "REJECT", "UNSCORED"))
            continue
        if exclude is None:
            score = t["v2_score"]
            ema_ok = ((t["side"] == "LONG" and t["regime_mode"] == "BUY_MODE")
                      or (t["side"] == "SHORT" and t["regime_mode"] == "SELL_MODE"))
            hc = hard_conflict_loo(t["breakdown"], exclude)  # exclude=None → full set
            dec, reason = decide_A(score, hc)
            out.append((t, dec, reason))
        else:
            score = v2_score_loo(t["breakdown"], exclude)
            hc = hard_conflict_loo(t["breakdown"], exclude)
            dec, reason = decide_A(score, hc)
            out.append((t, dec, reason))
    return out


def run_scenario(cohort, exclude):
    admitted = []
    for t, dec, reason in scenario_admit(cohort, exclude):
        if dec == "ACCEPT":
            admitted.append(t)
    m = metrics(admitted)
    m["admitted"] = len(admitted)
    return admitted, m


def show_table(scenarios, cohort, cohort_label):
    """Print the operator-specified LOO table for a set of scenarios."""
    base = scenarios[None]
    base_m = base[1]
    hdr = (f"{'Component removed':<20s} {'Trades':>6s} {'WR%':>6s} {'PF':>6s} "
           f"{'Exp/t':>8s} {'Net PnL':>10s} {'Δ PnL':>9s} {'Δ exp':>8s}")
    print(f"\n=== LEAVE-ONE-OUT — {cohort_label} ===")
    print(f"  baseline CURRENT_V2: ACCEPTED n={base_m['admitted']} "
          f"{base_m['wins']}W/{base_m['losses']}L WR={base_m['win_rate']:.1f}% "
          f"PF={base_m['profit_factor']:.2f} exp=${base_m['expectancy']:+.2f} "
          f"net=${base_m['net_pnl']:+.2f}")
    print(hdr)
    print("-" * len(hdr))
    for label in (None, *COMPONENTS):
        admitted, m = scenarios[label]
        d_pnl = m["net_pnl"] - base_m["net_pnl"]
        d_exp = m["expectancy"] - base_m["expectancy"]
        name = label or "None — V2 baseline"
        pf_s = f"{m['profit_factor']:.2f}" if m["trades"] else "  — "
        print(f"{name:<20s} {m['admitted']:6d} {m['win_rate']:6.1f} {pf_s:>6s} "
              f"{m['expectancy']:8.2f} {m['net_pnl']:10.2f} {d_pnl:9.2f} {d_exp:8.2f}")

    print("\n--- retained metrics (accepted cohort, per scenario) ---")
    for label in (None, *COMPONENTS):
        admitted, m = scenarios[label]
        name = label or "None — V2 baseline"
        print(f"  {name:<20s} n={m['admitted']:2d} avgW=${m['avg_win']:7.2f} "
              f"avgL=${m['avg_loss']:7.2f} maxLoss=${m['largest_loss']:8.2f} "
              f"DD=${m['max_drawdown']:7.2f} lossStreak={m['max_loss_streak']}")

    print("\n--- admission flips vs CURRENT_V2 baseline (which trades moved?) ---")
    base_map = {id(t): dec for t, dec, _ in scenario_admit(cohort, None)}
    for label in COMPONENTS:
        flips = []
        for t, dec, reason in scenario_admit(cohort, label):
            base_dec = base_map[id(t)]
            if dec != base_dec:
                flips.append((t, base_dec, dec))
        if flips:
            print(f"  remove {label}: {len(flips)} flips")
            for t, bd, nd in flips:
                print(f"      {t['symbol']:12s} {t['side']:5s} v2={t['v2_score']} "
                      f"{bd}->{nd} pnl=${t['pnl']:8.2f}")

    return base_m


def build_scenarios(cohort):
    scenarios = {}
    for label in (None, *COMPONENTS):
        admitted, m = run_scenario(cohort, label)
        scenarios[label] = (admitted, m)
    return scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Leave-one-out component analysis (read-only)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(DB)
    clean, info = load_clean(con)
    con.close()

    print(f"DECISION ROWS: {info['decision_rows']}")
    print(f"  clean authoritative closes (linked+closed+pnl): {len(clean)}")
    print(f"  open positions: {info['open']} | decisions with no position: {info['no_trade']}")
    print(f"  OPERATIONAL / DATA FAILURES (NO_APP_DECISION): {info['operational']}")

    if not clean:
        print("No clean closes yet — nothing to analyze.")
        return

    outliers = [t for t in clean if t["symbol"] in OUTLIER_SYMBOLS]
    ex_outlier = [t for t in clean if t["symbol"] not in OUTLIER_SYMBOLS]

    scenarios = build_scenarios(clean)
    show_table(scenarios, clean, "AUTHORITATIVE cohort (includes outliers)")

    if ex_outlier:
        ex_scenarios = build_scenarios(ex_outlier)
        show_table(ex_scenarios, ex_outlier,
                   f"ex-outlier cohort (excludes {', '.join(OUTLIER_SYMBOLS)})")
        print("\n  NOTE: outliers remain in the authoritative dataset — this view is diagnostic only.")

    # ── Retained breakdowns on the AUTHORITATIVE baseline accepted cohort ──
    base_admitted = scenarios[None][0]
    print("\n=== LONG vs SHORT (V2 baseline ACCEPTED) ===")
    for side in ("LONG", "SHORT"):
        grp = [t for t in base_admitted if t["side"] == side]
        m = metrics(grp)
        print(f"  {side:6s} n={m['trades']:2d} {m['wins']}W/{m['losses']}L "
              f"WR={m['win_rate']:5.1f}% PF={m['profit_factor']:5.2f} "
              f"exp=${m['expectancy']:+.2f} net=${m['net_pnl']:+.2f}")

    print("\n=== BUY_MODE vs SELL_MODE (V2 baseline ACCEPTED) ===")
    for mode in ("BUY_MODE", "SELL_MODE"):
        grp = [t for t in base_admitted if t["regime_mode"] == mode]
        m = metrics(grp)
        print(f"  {mode:9s} n={m['trades']:2d} {m['wins']}W/{m['losses']}L "
              f"WR={m['win_rate']:5.1f}% PF={m['profit_factor']:5.2f} "
              f"exp=${m['expectancy']:+.2f} net=${m['net_pnl']:+.2f}")

    print("\n=== CONFIDENCE BANDS (V2 baseline ACCEPTED, decisions.signal_confidence) ===")
    conf_sets = defaultdict(list)
    for t in base_admitted:
        conf_sets[bucket_conf(t["confidence"])].append(t)
    for b in ("40-50", "50-60", "60-70", "70-80", "80+", "<40"):
        if conf_sets[b]:
            m = metrics(conf_sets[b])
            print(f"  conf {b:5s} n={m['trades']:2d} {m['wins']}W/{m['losses']}L "
                  f"WR={m['win_rate']:5.1f}% PF={m['profit_factor']:5.2f} "
                  f"exp=${m['expectancy']:+.2f} net=${m['net_pnl']:+.2f}")

    print("\n=== OPERATIONAL / DATA FAILURES (separate — never in the cohorts above) ===")
    print(f"  total NO_APP_DECISION: {info['operational']} (missing/stale LIVE SHEET coverage)")

    print("\n=== OUTLIER TRADES (kept in authoritative dataset) ===")
    for t in outliers:
        print(f"  {t['symbol']:12s} {t['side']:5s} v2score={t['v2_score']} "
              f"pnl=${t['pnl']:8.2f} exit={t['exit_reason'] or '?'} mode={t['regime_mode']}")

    # ── Verdict ──
    base_m = scenarios[None][1]
    best = None
    best_delta = 0.0
    for label in COMPONENTS:
        d = scenarios[label][1]["net_pnl"] - base_m["net_pnl"]
        if d > best_delta:
            best, best_delta = label, d
    worst = None
    worst_delta = 0.0
    for label in COMPONENTS:
        d = scenarios[label][1]["net_pnl"] - base_m["net_pnl"]
        if d < worst_delta:
            worst, worst_delta = label, d
    print("\n=== VERDICT (diagnostic only — NOTHING activated) ===")
    if worst is not None:
        print(f"  Removing '{worst}' HURT the most (net PnL {worst_delta:+.2f} vs baseline) "
              f"→ it is currently HELPING admission.")
    else:
        print("  No component removal reduced net PnL — all components are at worst neutral in this cohort.")
    if best is not None and best_delta > 0:
        print(f"  Removing '{best}' HELPED the most (net PnL {best_delta:+.2f} vs baseline) "
              f"→ it is currently HURTING admission (candidate for review).")
    else:
        print("  No component removal improved net PnL — no component currently HURTS admission.")
    print(f"  Baseline accepted expectancy: ${base_m['expectancy']:+.2f}/trade "
          f"({len(clean)} clean closes; target 30). Results are directional until 30.")

    if args.json:
        payload = {
            "clean_closes": len(clean),
            "operational_failures": info["operational"],
            "scenarios": {},
        }
        for label in (None, *COMPONENTS):
            admitted, m = scenarios[label]
            payload["scenarios"][str(label)] = {
                k: v for k, v in m.items()
            }
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
