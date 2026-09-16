#!/usr/bin/env python3
"""
CLEAN FORWARD-TEST REPORT — read-only.

AUDIT B: after the loss-streak re-baseline and the rolling-PF contamination
cleanup, only trades closed at or after the re-baseline timestamp are treated
as production forward-test evidence. Historical/bug-era trades are excluded.

Data source: positions_archive (authoritative closed trades) in
institutional_v1.db. Partial scale-out exits of a single position are merged
into their parent trade so they never inflate trade count / win rate.

This script makes NO changes to the engine, strategy, or any database.
Run:  python3 scripts/clean_forward_test_report.py
"""
import json
import sqlite3
import sys
import statistics
from pathlib import Path
from datetime import datetime, timezone

PKG = Path(__file__).resolve().parent.parent
INST_DB = PKG / "data" / "institutional_v1.db"
REGIME_STATE = PKG / "data" / "regime_state.json"
MIN_TRADES = 30


def load_cutoff() -> float:
    """Read the re-baseline cutoff timestamp from regime_state.json."""
    try:
        with open(REGIME_STATE) as f:
            state = json.load(f)
        return float(state.get("rebaseline_at") or 0)
    except Exception as e:
        print(f"⚠️  Could not read re-baseline cutoff: {e}")
        return 0.0


def fetch_clean_trades(cutoff: float):
    """Return authoritative closed trades whose ENTIRE lifecycle is post-rebaseline.

    A trade is clean evidence only if it was OPENED at or after the re-baseline
    cutoff. Positions opened before the cutoff (even if closed after it) carry
    accumulated PnL from pre-cutoff partial exits and pre-cleanup state, so they
    are excluded.
    """
    con = sqlite3.connect(str(INST_DB), timeout=10)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(
        """SELECT symbol, side, entry_price, pnl, fees, opened_at, closed_at,
                  exit_reason, gross_pnl, funding, realized_pnl_raw,
                  realized_pnl_rounded, loss_classification,
                  consecutive_losses_before, consecutive_losses_after,
                  realized_r, mae_pct, mfe_pct, confidence,
                  strategy_version, signal_id
           FROM positions_archive
           WHERE status='closed' AND opened_at >= ?
           ORDER BY closed_at ASC""",
        (cutoff,),
    )
    trades = [dict(r) for r in cur.fetchall()]

    # Enrich each clean trade with the strategy metadata captured at signal
    # time (EMAs, volume result, pullback result, candle confirmation) so the
    # report can show direction/volume/EMA breakdowns.
    sig_rows = cur.execute(
        "SELECT id, metadata FROM signals WHERE id IN ("
        + ",".join(f"'{t['signal_id']}'" for t in trades if t.get("signal_id"))
        + ")"
    ).fetchall()
    sig_map = {}
    for s in sig_rows:
        sig_map[s["id"]] = s["metadata"]
    for t in trades:
        meta_raw = sig_map.get(t.get("signal_id"))
        meta = {}
        if meta_raw:
            try:
                inner = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                if isinstance(inner, str):
                    inner = json.loads(inner)
                meta = inner if isinstance(inner, dict) else {}
            except Exception:
                pass
        t["ema_data"] = meta.get("ema_data", {})
        comp = meta.get("components", {})
        t["volume_result"] = comp.get("volume", "")
        t["pullback_result"] = comp.get("pullback", "")
        t["candle_confirmation"] = comp.get("candle", "")
    con.close()
    return trades


def summarize(trades):
    print()
    print("=" * 72)
    print("  CLEAN FORWARD-TEST REPORT (post-rebaseline, authoritative closes)")
    print("=" * 72)
    n = len(trades)
    print(f"  Completed trades: {n}")
    if n < MIN_TRADES:
        print(f"  ⏳ Collecting clean data… {n}/{MIN_TRADES} trades "
              f"({MIN_TRADES - n} to go) — report is preliminary.")
    print()

    if n == 0:
        print("  No post-rebaseline closed trades yet.")
        return

    wins = [t for t in trades if (t["realized_pnl_rounded"] or t["pnl"] or 0) > 0]
    losses = [t for t in trades if (t["realized_pnl_rounded"] or t["pnl"] or 0) <= 0]
    n_wins, n_losses = len(wins), len(losses)
    gross = sum(t.get("gross_pnl") or t.get("pnl") or 0 for t in trades)
    fees = sum(t.get("fees") or 0 for t in trades)
    funding = sum(t.get("funding") or 0 for t in trades)
    net = sum(t.get("realized_pnl_rounded") or t.get("pnl") or 0 for t in trades)
    gw = sum(t.get("realized_pnl_rounded") or t.get("pnl") or 0 for t in wins)
    gl = abs(sum(t.get("realized_pnl_rounded") or t.get("pnl") or 0 for t in losses))
    pf = (gw / gl) if gl > 0 else float("inf")
    r_vals = [t.get("realized_r") or 0 for t in trades]
    expectancy = (net / n) if n else 0.0
    avg_r = statistics.mean(r_vals) if r_vals else 0.0
    med_r = statistics.median(r_vals) if r_vals else 0.0

    # Max drawdown from running realized-PnL equity (per-trade)
    equity, peak, mdd = 0.0, 0.0, 0.0
    for t in trades:
        equity += (t.get("realized_pnl_rounded") or t.get("pnl") or 0)
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)

    # Max consecutive losses
    cur_streak = max_streak = 0
    for t in trades:
        if (t.get("realized_pnl_rounded") or t.get("pnl") or 0) <= 0:
            cur_streak += 1
            max_streak = max(max_streak, cur_streak)
        else:
            cur_streak = 0

    print(f"  Trades:              {n}")
    print(f"  Wins:                {n_wins}")
    print(f"  Losses:              {n_losses}")
    print(f"  Win rate:            {(n_wins / n * 100 if n else 0):.1f}%")
    print(f"  Gross PnL:           ${gross:,.2f}")
    print(f"  Fees:                ${fees:,.2f}")
    print(f"  Funding:             ${funding:,.2f}")
    print(f"  Net PnL:             ${net:,.2f}")
    print(f"  Profit Factor:       {pf if pf != float('inf') else '∞'}")
    print(f"  Expectancy:          ${expectancy:,.3f} / trade")
    print(f"  Average R:           {avg_r:.2f}R")
    print(f"  Median R:            {med_r:.2f}R")
    print(f"  Max Drawdown:        ${mdd:,.2f}")
    print(f"  Max Consec Losses:   {max_streak}")
    print()

    # ── Breakdowns ──
    def bucket(rows):
        return [t.get("realized_pnl_rounded") or t.get("pnl") or 0 for t in rows]

    def perf(label, rows):
        if not rows:
            print(f"    {label}: n=0")
            return
        vals = bucket(rows)
        w = sum(1 for v in vals if v > 0)
        netv = sum(vals)
        gw = sum(v for v in vals if v > 0)
        gl = abs(sum(v for v in vals if v < 0))
        pf = (gw / gl) if gl > 0 else float("inf")
        r_vals = [t.get("realized_r") or 0 for t in rows]
        avg_r = statistics.mean(r_vals) if r_vals else 0.0
        _pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
        print(f"    {label}: n={len(rows):>3d} wins={w:>3d} "
              f"win_rate={w/len(rows)*100:>5.1f}% PF={_pf_str:<4s} "
              f"avg_R={avg_r:>6.2f} net=${netv:>9,.2f}")

    print("  ── BUY vs SELL ──")
    perf("BUY ", [t for t in trades if t.get("side", "").upper() == "LONG"])
    perf("SELL", [t for t in trades if t.get("side", "").upper() == "SHORT"])
    print()
    print("  ── Confidence buckets ──")
    confs = sorted({int((t.get("confidence") or 0) * 100 // 10) for t in trades if t.get("confidence")})
    for lo in confs:
        hi = lo + 1
        perf(f"conf {lo*10}-{hi*10}",
             [t for t in trades if lo * 10 <= (t.get("confidence") or 0) * 100 < hi * 10])
    print()

    # ── Volume result buckets ──
    print("  ── Volume result buckets ──")
    vol_keys = sorted({t.get("volume_result") or "unknown" for t in trades})
    for vk in vol_keys:
        perf(f"vol[{vk[:38]:<38}]",
             [t for t in trades if (t.get("volume_result") or "unknown") == vk])
    print()

    # ── EMA chain pattern buckets ──
    print("  ── EMA confirmation performance ──")
    ema_pats = sorted({(t.get("ema_data") or {}).get("ema_chain_pattern") or "?" for t in trades})
    for pat in ema_pats:
        perf(f"ema[{pat:<12}]",
             [t for t in trades if ((t.get("ema_data") or {}).get("ema_chain_pattern") or "?") == pat])
    print()

    # ── MAE / MFE ──
    mae = [t.get("mae_pct") or 0 for t in trades]
    mfe = [t.get("mfe_pct") or 0 for t in trades]
    print(f"  ── MAE / MFE ──")
    print(f"    avg MAE: {statistics.mean(mae):.2f}%   max MAE: {max(mae):.2f}%")
    print(f"    avg MFE: {statistics.mean(mfe):.2f}%   max MFE: {max(mfe):.2f}%")
    print()

    print("  ── Per-trade audit trail (last 30) ──")
    for t in trades[-30:]:
        _ema = t.get("ema_data") or {}
        print(
            f"    {t['symbol']:<12s} {t.get('side',''):<5s} "
            f"raw={t.get('realized_pnl_raw') or 0:>9.4f} "
            f"rnd={t.get('realized_pnl_rounded') or t.get('pnl') or 0:>9.2f} "
            f"cls={t.get('loss_classification') or '?':<9s} "
            f"cl_before={t.get('consecutive_losses_before') or 0} "
            f"cl_after={t.get('consecutive_losses_after') or 0} "
            f"R={t.get('realized_r') or 0:>6.2f} "
            f"MAE={t.get('mae_pct') or 0:>6.2f}% MFE={t.get('mfe_pct') or 0:>6.2f}% "
            f"reason={t.get('exit_reason') or ''}  "
            f"ema={_ema.get('ema_chain_pattern') or '?'} vol=[{t.get('volume_result') or '?'}]"
        )
    print()


def main():
    cutoff = load_cutoff()
    if cutoff:
        print(f"Re-baseline cutoff: {cutoff} "
              f"({datetime.fromtimestamp(cutoff, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')})")
    else:
        print("⚠️  No re-baseline cutoff found — refusing to mix historical trades.")
        sys.exit(1)
    trades = fetch_clean_trades(cutoff)
    summarize(trades)


if __name__ == "__main__":
    main()
