"""
READ-ONLY final classification: STRATEGY-CLEAN vs OPERATIONAL-FAILURES.

Classification evidence per trade:
  - NO_DATA(0,0,False,False) snapshots during hold  -> scanner/WS feed down
  - IN_OUTAGE_WINDOW (global 18:51-19:04 outage)     -> risk loop on stale prices
  - exit at/after data recovery with SL missed       -> delayed protective exit
  - MAE >> intended SL distance during outage        -> SL masked by stale data
  - anomalous exit (below SL) with MAE 0             -> exit integrity suspect

Tiers:
  CONFIRMED  : protective stop missed/delayed due to data failure (exclude)
  SUSPECT    : SL likely masked during outage / anomalous exit (exclude from strategy)
  EXPOSED    : stale feed during hold, but protective level not breached,
               clean exit on fresh data (exclude from strategy, list reason)
  CLEAN      : no data-integrity impact
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

DB = "data/institutional_v1.db"
REBASELINE_AT = 1786441147.773142

# (pos_id, tier, reason)
CLASSIFICATION = {
    1992: ("CLEAN", "data OK; MAE 0.68% close to SL 0.47%; no outage overlap"),
    1993: ("CLEAN", "data OK; TP hit; no outage"),
    1994: ("CLEAN", "data OK (pre-outage); MAE 1.04% vs SL 0.54% fast gap, fresh feed"),
    1995: ("CLEAN", "data OK; TP hit; no outage"),
    1996: ("CLEAN", "data OK (pre-outage); MAE 1.48% vs SL 1.19% fast move, fresh feed"),
    1997: ("CLEAN", "data OK; TP hit; no outage"),
    1998: ("CLEAN", "data OK (pre-outage); trailing win; MAE 0.62% sampled"),
    1999: ("CLEAN", "data OK (pre-outage); trailing win"),
    2000: ("ANOMALOUS", "exit 0.3615 BELOW SL 0.3629 but MAE=0.00%/MFE=0.00%; held 3.9h "
                        "through outage; exit integrity unverified — exclude from strategy"),
    2001: ("CLEAN", "data OK (pre-outage); tight SL fast gap, fresh feed"),
    2002: ("CLEAN", "data OK (pre-outage 15:44-15:48); MAE 1.60% vs SL 0.24% genuine fast gap"),
    2003: ("CLEAN", "data OK (pre-outage); MAE 0.78% vs SL 0.40% fast gap, fresh feed"),
    2004: ("CLEAN", "data OK; pre-outage window; tight SL 0.13%"),
    2005: ("EXPOSED", "57 NO_DATA(0,0,F,F) snapshots 18:52-19:04 while SHORT held; "
                      "SL 1.39% barely not crossed (MAE 1.38%); stale-price evaluation during hold; "
                      "clean exit on fresh data +$5.22"),
    2006: ("SUSPECT", "held through outage; MAE 1.25% > SL 0.73% (SL likely crossed during outage, "
                      "masked by stale data); survived to +$4.54 — stop not honored in outage"),
    2007: ("EXPOSED", "12 NO_DATA snapshots during hold; stale evaluation; SL 0.14% not breached "
                      "(MAE 0.00%); clean exit +$0.55 on fresh data"),
    2008: ("CONFIRMED", "56 NO_DATA snapshots during hold; SL 0.26509 CROSSED (MAE 0.56% > SL 0.34%) "
                        "but masked by stale data; survived to +$1.39 — protective stop missed during outage"),
    2009: ("CLEAN", "LOW_DATA(trades=0 x7, 5m present); MAE 0.21% ~ SL 0.19%; trailing win on fresh data"),
    2010: ("CONFIRMED", "31 NO_DATA snapshots; held entire outage 18:40-19:04; exited 19:04:06 AT data "
                        "recovery; exit 0.055495 vs SL 0.055199 (missed by 0.30%); MAE 0.82% vs SL 0.36% "
                        "— delayed protective exit, same class as BLUAI"),
    2011: ("CLEAN", "data OK through outage (trades present); TP win on fresh data; SL not threatened"),
    2012: ("CONFIRMED", "37 NO_DATA snapshots; stale price ~0.0284 masked crash; SL 0.028223 never "
                        "honored; exit at data recovery 0.024437; MAE 13.78% vs SL 0.43% (x32) — "
                        "catastrophic delayed protective exit"),
    2013: ("SUSPECT", "opened 19:01:53 DURING outage tail; SL 0.15% missed (MAE 0.50% = 3.3x SL); "
                      "possible stale entry/monitoring; stop_loss -$1.44"),
    2014: ("EXPOSED", "opened 19:03:13 in outage tail; 9 NO_DATA snapshots at 19:09-19:10 secondary "
                      "outage; SL 0.11% not breached (MAE 0.05%); clean exit +$0.34"),
    2015: ("CLEAN", "data OK; late session; TP win; no outage overlap"),
    2017: ("CLEAN", "data OK; 3s stop; fresh feed"),
    2018: ("CLEAN", "duplicate signal (kept as dup); data OK; trailing win"),
    2019: ("CLEAN", "data OK; tight SL 0.58% fast gap on fresh feed"),
    2021: ("CLEAN", "data OK; 1-min stop; tight SL 0.12% fast gap"),
    2022: ("CLEAN", "duplicate signal; same as 2021"),
    2023: ("CLEAN", "data OK; MAE 0.94% vs SL 0.40% fast move, fresh feed"),
    2025: ("CLEAN", "LOW_DATA(trades=0 x11, 5m present); MAE 0.51% vs SL 0.17% tight gap; fresh feed"),
    2026: ("CLEAN", "data OK; TP win; tight SL 0.17% not breached"),
    2028: ("CLEAN", "data OK; 5-min stop; tiny +$0.14; fresh feed"),
}


@dataclass
class T:
    id: int
    symbol: str
    side: str
    pnl: float
    closed_at: float
    conf: float
    regime: str


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _stats(trades: List[T]) -> Dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0, "gp": 0, "gl": 0, "pf": None,
                "exp": 0, "aw": 0, "al": 0, "maxcl": 0, "maxdd": 0, "net": 0}
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)
    net = gp - gl
    maxcl = cur = 0
    running = peak = maxdd = 0.0
    for t in sorted(trades, key=lambda x: x.closed_at):
        if t.pnl <= 0:
            cur += 1
            maxcl = max(maxcl, cur)
        else:
            cur = 0
        running += t.pnl
        peak = max(peak, running)
        maxdd = max(maxdd, peak - running)
    return {"n": len(trades), "wins": len(wins), "losses": len(losses),
            "wr": len(wins) / len(trades) * 100, "gp": gp, "gl": gl, "pf": pf,
            "exp": net / len(trades) if trades else 0,
            "aw": gp / len(wins) if wins else 0, "al": -gl / len(losses) if losses else 0,
            "maxcl": maxcl, "maxdd": maxdd, "net": net}


def _bucket(conf: float) -> str:
    return "conf_40_50" if conf < 0.50 else ("conf_50_60" if conf < 0.60 else ("conf_60_70" if conf < 0.70 else "conf_70_plus"))


def main() -> None:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT pa.id, pa.symbol, pa.side, pa.pnl, pa.closed_at, pa.confidence, pa.regime
           FROM positions_archive pa WHERE pa.opened_at >= ? ORDER BY pa.closed_at""",
        (REBASELINE_AT,),
    ).fetchall()

    trades = [T(r["id"], r["symbol"], r["side"], r["pnl"] or 0, r["closed_at"] or 0,
                r["confidence"] or 0, r["regime"] or "") for r in rows]

    # duplicates by signal signature
    sigs = db.execute("""SELECT pa.id, s.symbol, s.side, s.entry, s.stop_loss, s.take_profit, s.confidence
                         FROM positions_archive pa JOIN signals s ON s.id=pa.signal_id
                         WHERE pa.opened_at>=?""", (REBASELINE_AT,)).fetchall()
    seen, dup = {}, set()
    for r in sigs:
        key = (r["symbol"], r["side"], round(r["entry"] or 0, 8), round(r["stop_loss"] or 0, 8),
               round(r["take_profit"] or 0, 8), round(r["confidence"] or 0, 4))
        if key in seen:
            dup.add(r["id"])
        else:
            seen[key] = r["id"]

    clean = [t for t in trades if t.id not in dup]
    print("=" * 78)
    print("FINAL CLASSIFICATION — STRATEGY-CLEAN vs OPERATIONAL-FAILURES")
    print("=" * 78)
    print(f"post-rebaseline rows     : {len(trades)}")
    print(f"duplicate emissions      : {len(dup)}")
    print(f"unique trades            : {len(clean)}")

    tiers = {"CONFIRMED": [], "SUSPECT": [], "EXPOSED": [], "ANOMALOUS": [], "CLEAN": []}
    for t in clean:
        tier, reason = CLASSIFICATION.get(t.id, ("CLEAN", "no evidence"))
        tiers[tier].append(t)

    op = tiers["CONFIRMED"] + tiers["SUSPECT"] + tiers["EXPOSED"] + tiers["ANOMALOUS"]
    strat = tiers["CLEAN"]

    print("\n--- OPERATIONAL-FAILURES (excluded from strategy) ---")
    for tier in ("CONFIRMED", "SUSPECT", "EXPOSED", "ANOMALOUS"):
        for t in sorted(tiers[tier], key=lambda x: x.closed_at):
            reason = CLASSIFICATION[t.id][1]
            print(f"  [{tier:<9}] pos {t.id:>4} {t.symbol:<11} {t.side:<5} pnl=${t.pnl:>7.2f} "
                  f"close={_fmt(t.closed_at)} | {reason[:100]}")

    print("\n--- STRATEGY-CLEAN SET ---")
    for t in sorted(strat, key=lambda x: x.closed_at):
        print(f"  pos {t.id:>4} {t.symbol:<11} {t.side:<5} pnl=${t.pnl:>7.2f} close={_fmt(t.closed_at)}")

    def report(label, lst):
        s = _stats(lst)
        print(f"\n  {label}: n={s['n']} W={s['wins']} L={s['losses']} WR={s['wr']:.1f}% "
              f"GP=${s['gp']:.2f} GL=${s['gl']:.2f} PF={s['pf'] if s['pf']!=float('inf') else 'INF'}"
              f" net=${s['net']:.2f} exp=${s['exp']:.3f} avgW=${s['aw']:.2f} avgL=${s['al']:.2f} "
              f"maxCL={s['maxcl']} maxDD=${s['maxdd']:.2f}")
        return s

    print("\n--- STRATEGY-CLEAN METRICS ---")
    s_all = report("ALL STRATEGY-CLEAN", strat)
    print("\n  BY SIDE:")
    for side in ("LONG", "SHORT"):
        report(f"  {side}", [t for t in strat if t.side == side])
    print("\n  BY CONFIDENCE:")
    for b in ("conf_40_50", "conf_50_60", "conf_60_70", "conf_70_plus"):
        report(b, [t for t in strat if _bucket(t.conf) == b])
    print("\n  BY REGIME:")
    regs = {}
    for t in strat:
        regs.setdefault(t.regime or "unknown", []).append(t)
    for reg, grp in sorted(regs.items()):
        report(reg, grp)

    # Comparison: strict clean vs strict+confirmed-removed-only
    print("\n--- SENSITIVITY: remove only CONFIRMED (keep SUSPECT/EXPOSED/ANOMALOUS) ---")
    strat2 = strat + tiers["SUSPECT"] + tiers["EXPOSED"] + tiers["ANOMALOUS"]
    report("CLEAN + non-CONFIRMED", strat2)

    print("\n--- OPERATIONAL-FAILURES SUMMARY ---")
    for tier in ("CONFIRMED", "SUSPECT", "EXPOSED", "ANOMALOUS"):
        lst = tiers[tier]
        if lst:
            report(tier, lst)

    print("\nNOTE: archive `pnl` accumulates partial slices (authoritative per-position PnL).")
    print("BLUAI and ELSA are CONFIRMED delayed-protective-exit failures (same outage).")


if __name__ == "__main__":
    main()
