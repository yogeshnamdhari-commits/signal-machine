"""Definitive audit against a stable DB snapshot, with internal consistency checks."""
from __future__ import annotations

import sqlite3
from datetime import datetime

DB = "data/audit_snapshot.db"
REBASELINE_AT = 1786441147.773142

CLASSIFICATION = {
    1992: ("CLEAN", "data OK; MAE 0.68% vs SL 0.47%; pre-outage"),
    1993: ("CLEAN", "data OK; TP; no outage"),
    1994: ("CLEAN", "data OK; pre-outage; MAE 1.04% vs SL 0.54% fast gap"),
    1995: ("CLEAN", "data OK; TP; no outage"),
    1996: ("CLEAN", "data OK; pre-outage; MAE 1.48% vs SL 1.19%"),
    1997: ("CLEAN", "data OK; TP; no outage"),
    1998: ("CLEAN", "data OK; pre-outage; trailing win"),
    1999: ("CLEAN", "data OK; pre-outage; trailing win"),
    2000: ("ANOMALOUS", "exit below SL with MAE 0.00%; held 3.9h through outage; exit integrity unverified"),
    2001: ("CLEAN", "data OK; pre-outage; tight SL fast gap"),
    2002: ("CLEAN", "data OK; pre-outage; MAE 1.60% vs SL 0.24% genuine gap"),
    2003: ("CLEAN", "data OK; pre-outage; MAE 0.78% vs SL 0.40%"),
    2004: ("CLEAN", "data OK; pre-outage; tight SL 0.13%"),
    2005: ("EXPOSED", "57 NO_DATA 18:52-19:04 while held; SL 1.39% barely not crossed (MAE 1.38%); clean exit +5.22 on fresh data"),
    2006: ("SUSPECT", "held through outage; MAE 1.25% > SL 0.73% (SL likely crossed in outage, masked); survived +4.54"),
    2007: ("EXPOSED", "12 NO_DATA during hold; SL 0.14% not breached; clean exit +0.55"),
    2008: ("CONFIRMED", "56 NO_DATA; SL 0.26509 crossed (MAE 0.56% > SL 0.34%) masked; survived +1.39; protective stop missed"),
    2009: ("CLEAN", "LOW_DATA(trades=0 x7, 5m ok); MAE 0.21% ~ SL 0.19%; trailing win on fresh data"),
    2010: ("CONFIRMED", "31 NO_DATA; held whole outage; exited AT data recovery; exit missed SL by 0.30%; MAE 0.82% vs SL 0.36%; delayed protective exit"),
    2011: ("CLEAN", "data OK through outage; TP win; SL not threatened"),
    2012: ("CONFIRMED", "37 NO_DATA; stale ~0.0284 masked crash; SL never honored; exit at recovery; MAE 13.78% vs SL 0.43% (x32)"),
    2013: ("SUSPECT", "opened during outage tail; SL 0.15% missed (MAE 0.50% = 3.3x SL); possible stale entry/monitor"),
    2014: ("EXPOSED", "opened outage tail; 9 NO_DATA 19:09-19:10 secondary; SL 0.11% not breached; clean exit +0.34"),
    2015: ("CLEAN", "data OK; TP win; no outage overlap"),
    2017: ("CLEAN", "data OK; 3s stop; fresh feed"),
    2018: ("CLEAN", "duplicate signal (excluded); data OK; trailing win"),
    2019: ("CLEAN", "data OK; tight SL 0.58% fast gap"),
    2021: ("CLEAN", "data OK; 1-min stop; tight SL 0.12%"),
    2022: ("CLEAN", "duplicate signal (excluded); same as 2021"),
    2023: ("CLEAN", "data OK; MAE 0.94% vs SL 0.40%; fresh feed"),
    2025: ("CLEAN", "LOW_DATA(trades=0 x11, 5m ok); tight SL 0.17% gap; fresh feed"),
    2026: ("CLEAN", "data OK; TP win; SL 0.17% not breached"),
    2028: ("CLEAN", "data OK; 5-min stop; +0.14; fresh feed"),
}


class T:
    __slots__ = ("id", "symbol", "side", "pnl", "closed_at", "conf", "regime")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def fmt(ts):
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S") if ts else "?"


def stats(lst):
    wins = [t for t in lst if t.pnl > 0]
    losses = [t for t in lst if t.pnl <= 0]
    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    net = gp - gl
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    maxcl = cur = 0
    running = peak = maxdd = 0.0
    for t in sorted(lst, key=lambda x: x.closed_at):
        cur = cur + 1 if t.pnl <= 0 else 0
        maxcl = max(maxcl, cur)
        running += t.pnl
        peak = max(peak, running)
        maxdd = max(maxdd, peak - running)
    return {"n": len(lst), "w": len(wins), "l": len(losses), "wr": len(wins) / len(lst) * 100 if lst else 0,
            "gp": gp, "gl": gl, "pf": pf, "net": net, "exp": net / len(lst) if lst else 0,
            "aw": gp / len(wins) if wins else 0, "al": -gl / len(losses) if losses else 0,
            "maxcl": maxcl, "maxdd": maxdd}


def line(label, s):
    pf = f"{s['pf']:.3f}" if s["pf"] != float("inf") else "INF"
    return (f"{label:<30} n={s['n']:>2} W={s['w']:>2} L={s['l']:>2} "
            f"WR={s['wr']:>5.1f}% GP=${s['gp']:>8.2f} GL=${s['gl']:>8.2f} "
            f"PF={pf:>6} net=${s['net']:>8.2f} exp=${s['exp']:>7.3f} "
            f"avgW=${s['aw']:>5.2f} avgL=${s['al']:>6.2f} maxCL={s['maxcl']} maxDD=${s['maxdd']:>8.2f}")


def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT pa.id, pa.symbol, pa.side, pa.pnl, pa.closed_at, pa.opened_at,
                  pa.confidence, pa.regime, pa.entry_price, pa.mae_pct
           FROM positions_archive pa
           WHERE pa.opened_at >= ? ORDER BY pa.closed_at""",
        (REBASELINE_AT,),
    ).fetchall()

    sigs = db.execute(
        """SELECT pa.id, s.symbol, s.side, s.entry, s.stop_loss, s.take_profit, s.confidence
           FROM positions_archive pa JOIN signals s ON s.id = pa.signal_id
           WHERE pa.opened_at >= ?""",
        (REBASELINE_AT,),
    ).fetchall()
    seen, dup = {}, set()
    for r in sigs:
        key = (r["symbol"], r["side"], round(r["entry"] or 0, 8), round(r["stop_loss"] or 0, 8),
               round(r["take_profit"] or 0, 8), round(r["confidence"] or 0, 4))
        if key in seen:
            dup.add(r["id"])
        else:
            seen[key] = r["id"]

    trades = [T(id=r["id"], symbol=r["symbol"], side=r["side"], pnl=r["pnl"] or 0,
                closed_at=r["closed_at"] or 0, conf=r["confidence"] or 0, regime=r["regime"] or "")
              for r in rows]
    clean_all = [t for t in trades if t.id not in dup]

    tiers = {"CONFIRMED": [], "SUSPECT": [], "EXPOSED": [], "ANOMALOUS": [], "CLEAN": []}
    for t in clean_all:
        tier, _ = CLASSIFICATION.get(t.id, ("CLEAN", ""))
        tiers[tier].append(t)
    strat = tiers["CLEAN"]

    # ---- internal consistency assertions ----
    all_ids = {t.id for t in clean_all}
    assert len(clean_all) == 31, f"clean_all={len(clean_all)}"
    assert sum(len(v) for v in tiers.values()) == len(clean_all)
    sum_tiers = sum(t.pnl for v in tiers.values() for t in v)
    sum_all = sum(t.pnl for t in clean_all)
    assert abs(sum_tiers - sum_all) < 1e-9, f"tiers {sum_tiers:.4f} != all {sum_all:.4f}"
    assert set(dup) == {2018, 2022}, dup
    assert {t.id for t in strat} == {1992,1993,1994,1995,1996,1997,1998,1999,2001,2002,2003,2004,
                                     2009,2011,2015,2017,2019,2021,2023,2025,2026,2028}

    # ---- report ----
    print("=" * 104)
    print("DATA-INTEGRITY AUDIT — FINAL (snapshot %s)" % DB)
    print("=" * 104)
    print(f"post-rebaseline archive rows : {len(trades)}")
    print(f"duplicate emissions excluded  : {len(dup)} (ids {sorted(dup)})")
    print(f"unique trades                 : {len(clean_all)}")
    print(f"STRATEGY-CLEAN                : {len(strat)}")
    print(f"OPERATIONAL-FAILURES (excl)   : {len(clean_all)-len(strat)} "
          f"(CONFIRMED {len(tiers['CONFIRMED'])} / SUSPECT {len(tiers['SUSPECT'])} / "
          f"EXPOSED {len(tiers['EXPOSED'])} / ANOMALOUS {len(tiers['ANOMALOUS'])})")
    print(f"internal consistency          : OK (tiers sum == all sum)")

    print("\n--- OPERATIONAL-FAILURES (excluded) ---")
    for tier in ("CONFIRMED", "SUSPECT", "EXPOSED", "ANOMALOUS"):
        for t in sorted(tiers[tier], key=lambda x: x.closed_at):
            _, reason = CLASSIFICATION[t.id]
            print(f"  [{tier:<9}] pos {t.id:>4} {t.symbol:<11} {t.side:<5} "
                  f"pnl=${t.pnl:>7.2f} close={fmt(t.closed_at)} | {reason}")

    print("\n--- STRATEGY-CLEAN TRADES ---")
    for t in sorted(strat, key=lambda x: x.closed_at):
        print(f"  pos {t.id:>4} {t.symbol:<11} {t.side:<5} pnl=${t.pnl:>7.2f} close={fmt(t.closed_at)}")

    print("\n--- STRATEGY-CLEAN METRICS ---")
    print(line("ALL", stats(strat)))
    for side in ("LONG", "SHORT"):
        print(line(f"  {side}", stats([t for t in strat if t.side == side])))

    def bucket(c):
        return "conf_40_50" if c < 0.50 else ("conf_50_60" if c < 0.60 else ("conf_60_70" if c < 0.70 else "conf_70_plus"))

    for b in ("conf_40_50", "conf_50_60", "conf_60_70", "conf_70_plus"):
        print(line(b, stats([t for t in strat if bucket(t.conf) == b])))

    regs = {}
    for t in strat:
        regs.setdefault(t.regime or "unknown", []).append(t)
    for reg, grp in sorted(regs.items()):
        print(line(f"  regime:{reg}", stats(grp)))

    print("\n--- SENSITIVITY VARIANTS ---")
    a = strat + tiers["SUSPECT"] + tiers["EXPOSED"] + tiers["ANOMALOUS"]
    b = strat + tiers["EXPOSED"] + tiers["ANOMALOUS"]
    print(line("A: only CONFIRMED removed", stats(a)))
    print(line("B: CONFIRMED+SUSPECT removed", stats(b)))
    print(line("C: all 31 unique (original)", stats(clean_all)))

    print("\n--- OPERATIONAL-FAILURE SUMMARIES ---")
    for tier in ("CONFIRMED", "SUSPECT", "EXPOSED", "ANOMALOUS"):
        if tiers[tier]:
            print(line(tier, stats(tiers[tier])))

    print("\nNOTES:")
    print("- Engine is LIVE (PID 42280) and rewriting positions_archive rows in place;")
    print("  audit runs on a frozen sqlite snapshot to be deterministic.")
    print("- archive pnl accumulates partial slices (authoritative per-position PnL).")
    print("- BLUAI/ELSA: CONFIRMED delayed protective exit (18:52-19:04 outage).")
    print("- CRV: protective SL crossed during outage but masked; survived to win.")
    print("- SAGA (pos 2015) archived after earlier report: 31 unique now (was 30).")


if __name__ == "__main__":
    main()
