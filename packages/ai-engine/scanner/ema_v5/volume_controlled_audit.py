"""
DIRECTION + REGIME CONTROLLED VOLUME OUTCOME AUDIT (research only — no live changes).

Reads the saved historical_volume_audit.json (produced by offline_volume_audit.py)
and splits candidates by direction (BUY/SELL) and regime (BUY_MODE/SELL_MODE),
comparing Volume PASS vs REJECT within each group.

For every group reports: N, win rate, avg R, median R, expectancy, profit factor,
MFE, MAE. Also computes Volume Filter Value = AvgR(PASS) - AvgR(REJECT) per group,
and flags symbols that contribute disproportionately to the aggregate.

Usage:
    python volume_controlled_audit.py [--input path/to/historical_volume_audit.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_INPUT = DATA_DIR / "historical_volume_audit.json"


def stats(records):
    n = len(records)
    if n == 0:
        return None
    wins = [r for r in records if (r.get("pnl_r") or 0) > 0]
    losses = [r for r in records if (r.get("pnl_r") or 0) <= 0]
    rs = [r.get("pnl_r") or 0 for r in records]
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = abs(sum(r for r in rs if r < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else (999 if gross_win > 0 else 0)
    return {
        "n": n,
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "avg_r": round(statistics.mean(rs), 3),
        "median_r": round(statistics.median(rs), 3),
        "expectancy_r": round(statistics.mean(rs), 3),
        "profit_factor": round(pf, 3),
        "avg_mfe_r": round(statistics.mean([r.get("mfe_r") or 0 for r in records]), 3),
        "avg_mae_r": round(statistics.mean([r.get("mae_r") or 0 for r in records]), 3),
    }


def print_group(title, s):
    if not s:
        print(f"  {title}: (no data)")
        return
    print(
        f"  {title}: n={s['n']} win={s['win_rate_pct']}% avgR={s['avg_r']} "
        f"medR={s['median_r']} exp={s['expectancy_r']} PF={s['profit_factor']} "
        f"MFE={s['avg_mfe_r']}R MAE={s['avg_mae_r']}R"
    )


def split_pass_reject(records):
    return (
        [r for r in records if r.get("decision") == "PASS"],
        [r for r in records if r.get("decision") == "REJECT"],
    )


def report_group(title, records):
    s = stats(records)
    print_group(title, s)
    return s


def filter_value(pass_recs, reject_recs):
    s_pass = stats(pass_recs)
    s_rej = stats(reject_recs)
    if not s_pass or not s_rej:
        return None
    return s_pass["expectancy_r"] - s_rej["expectancy_r"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT))
    args = parser.parse_args()

    recs = json.load(open(args.input))
    resolved = [r for r in recs if r.get("pnl_r") is not None]
    print("=" * 90)
    print("📊 DIRECTION + REGIME CONTROLLED VOLUME AUDIT")
    print(f"Input: {args.input}")
    print(f"Total candidates: {len(recs)}  (resolved: {len(resolved)})")
    print("=" * 90)

    # ── 0. Overall baseline ──
    print("\n[0] OVERALL BASELINE:")
    print_group("All candidates", stats(resolved))

    # ── 1. Direction-controlled ──
    print("\n[1] DIRECTION CONTROLLED — Volume PASS vs REJECT:")
    for side in ("BUY", "SELL"):
        grp = [r for r in resolved if r.get("side") == side]
        s_all = stats(grp)
        p, j = split_pass_reject(grp)
        fv = filter_value(p, j)
        print(f"\n  ── {side} (n={s_all['n']} win={s_all['win_rate_pct']}% avgR={s_all['avg_r']} PF={s_all['profit_factor']}) ──")
        print_group(f"{side} Volume PASS", stats(p))
        print_group(f"{side} Volume REJECT", stats(j))
        print(f"  → Volume Filter Value ({side}) = AvgR(PASS) − AvgR(REJECT) = "
              f"{fv:+.3f}R" if fv is not None else "  → Volume Filter Value: no data")

    # ── 2. Regime-controlled ──
    print("\n[2] REGIME CONTROLLED (EMA V5 entry regime) — Volume PASS vs REJECT:")
    for regime in ("BUY_MODE", "SELL_MODE"):
        grp = [r for r in resolved if r.get("regime") == regime]
        s_all = stats(grp)
        p, j = split_pass_reject(grp)
        fv = filter_value(p, j)
        print(f"\n  ── {regime} (n={s_all['n']} win={s_all['win_rate_pct']}% avgR={s_all['avg_r']} PF={s_all['profit_factor']}) ──")
        print_group(f"{regime} Volume PASS", stats(p))
        print_group(f"{regime} Volume REJECT", stats(j))
        print(f"  → Volume Filter Value ({regime}) = AvgR(PASS) − AvgR(REJECT) = "
              f"{fv:+.3f}R" if fv is not None else "  → Volume Filter Value: no data")

    # ── 3. Direction × regime cross ──
    print("\n[3] DIRECTION × REGIME CROSS:")
    for regime in ("BUY_MODE", "SELL_MODE"):
        for side in ("BUY", "SELL"):
            grp = [r for r in resolved if r.get("regime") == regime and r.get("side") == side]
            if not grp:
                print(f"\n  ── {regime}/{side}: (no data) ──")
                continue
            s_all = stats(grp)
            p, j = split_pass_reject(grp)
            fv = filter_value(p, j)
            print(f"\n  ── {regime}/{side} (n={s_all['n']} win={s_all['win_rate_pct']}% avgR={s_all['avg_r']} PF={s_all['profit_factor']}) ──")
            print_group("Volume PASS", stats(p))
            print_group("Volume REJECT", stats(j))
            if fv is not None:
                print(f"  → Volume Filter Value = {fv:+.3f}R")

    # ── 4. Symbol-level + disproportionate contribution ──
    print("\n[4] SYMBOL-LEVEL RESULTS:")
    by_sym = defaultdict(list)
    for r in resolved:
        by_sym[r.get("symbol")].append(r)

    n_total = len(resolved)
    print(f"\n  {'Symbol':<10} {'n':>4} {'%n':>6} {'win%':>6} {'avgR':>7} {'PF':>6} "
          f"{'PASS avgR':>9} {'REJ avgR':>9} {'FiltVal':>8}")
    print("  " + "-" * 76)
    flagged = []
    for sym in sorted(by_sym, key=lambda s: -len(by_sym[s])):
        grp = by_sym[sym]
        s_all = stats(grp)
        p, j = split_pass_reject(grp)
        s_pass, s_rej = stats(p), stats(j)
        fv = filter_value(p, j)
        share = len(grp) / n_total * 100
        fv_str = f"{fv:+.3f}" if fv is not None else "  n/a"
        p_avg = f"{s_pass['avg_r']:.3f}" if s_pass else "  n/a"
        r_avg = f"{s_rej['avg_r']:.3f}" if s_rej else "  n/a"
        print(f"  {sym:<10} {len(grp):>4} {share:>5.1f}% {s_all['win_rate_pct']:>5.1f}% "
              f"{s_all['avg_r']:>7.3f} {s_all['profit_factor']:>6.2f} {p_avg:>9} {r_avg:>9} {fv_str:>8}")
        if share >= 10:
            flagged.append((sym, share))

    if flagged:
        print("\n  ⚠️ Disproportionate contributors (≥10% of sample):")
        for sym, share in flagged:
            grp = by_sym[sym]
            s_all = stats(grp)
            p, j = split_pass_reject(grp)
            fv = filter_value(p, j)
            print(f"    - {sym}: {share:.1f}% of sample, avgR={s_all['avg_r']:.3f}, "
                  f"PASS={len(p)}/{len(j)} REJECT, filter value={fv if fv is None else f'{fv:+.3f}R'}")

    # ── 5. Regime composition of PASS vs REJECT ──
    print("\n[5] COMPOSITION CHECK — is PASS/REJECT compositionally different by side/regime?")
    for dec in ("PASS", "REJECT"):
        grp = [r for r in resolved if r.get("decision") == dec]
        n_buy = sum(1 for r in grp if r.get("side") == "BUY")
        n_sell = len(grp) - n_buy
        n_bm = sum(1 for r in grp if r.get("regime") == "BUY_MODE")
        print(f"  {dec}: n={len(grp)}  BUY={n_buy} ({n_buy/max(len(grp),1)*100:.0f}%)  "
              f"SELL={n_sell} ({n_sell/max(len(grp),1)*100:.0f}%)  "
              f"BUY_MODE={n_bm} ({n_bm/max(len(grp),1)*100:.0f}%)  "
              f"SELL_MODE={len(grp)-n_bm} ({(len(grp)-n_bm)/max(len(grp),1)*100:.0f}%)")

    print("\n" + "=" * 90)
    print("NOTE: research only — live strategy untouched. Do NOT change VolumeEngine yet.")
    print("Decision pending: this report determines whether the volume gate is the problem,")
    print("or whether it exposes a deeper BUY/SELL regime-selection problem.")


if __name__ == "__main__":
    main()
