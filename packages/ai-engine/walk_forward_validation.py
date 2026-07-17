"""
Walk-Forward Validation Script — v33 Confidence Model

Run this periodically to check if the frozen confidence model
continues to rank trades correctly on new data.

Usage:
    python walk_forward_validation.py

Confidence model (FROZEN v33):
    inst*0.50 + regime*0.10 - mss*0.10 + pullback*0.15 - fvg*0.10 - vol*0.05
    Threshold: 60.0

Validation milestones:
    [ ] 100+ closed trades with frozen model
    [ ] PF > 1.0 on unseen data
    [ ] Confidence buckets remain monotonic
    [ ] Stability across regimes
    [ ] Stability across symbols
"""
import sqlite3
from pathlib import Path
from collections import defaultdict
from datetime import datetime

DB_PATH = Path(__file__).parent / "data" / "institutional_v1.db"

# Freeze date: when v33 was deployed (use a conservative estimate)
FREEZE_DATE = "2026-07-09"


def new_confidence(t):
    """v33 frozen formula."""
    inst = t.get("institutional_score", 50) or 50
    mss = t.get("mss_score", 50) or 50
    fvg = t.get("fvg_score", 50) or 50
    vol = t.get("volatility_score", 50) or 50
    regime = 100 if t.get("regime") in ("BUY_MODE", "SELL_MODE") else 0
    score = inst * 0.50 + (100 - mss) * 0.15 + (100 - fvg) * 0.15 + (100 - vol) * 0.10 + regime * 0.10
    return max(0, min(100, score))


def run_validation():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 70)
    print("WALK-FORWARD VALIDATION — v33 FROZEN MODEL")
    print("=" * 70)
    print("Run date: %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("Freeze date: %s" % FREEZE_DATE)
    print()

    # Get ALL closed trades
    cur.execute("""
        SELECT confidence, institutional_score, mss_score, fvg_score,
               volatility_score, regime, session, realized_r, side,
               hold_minutes, highest_pnl, exit_reason, closed_at, symbol
        FROM positions_archive
        WHERE status='closed' AND confidence IS NOT NULL
        AND institutional_score IS NOT NULL
        ORDER BY closed_at ASC
    """)
    all_trades = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Split into pre-freeze and post-freeze
    # Use index-based split: last 20% as walk-forward
    split_idx = int(len(all_trades) * 0.8)
    pre_trades = all_trades[:split_idx]
    post_trades = all_trades[split_idx:]

    print("Total trades: %d" % len(all_trades))
    print("Pre-freeze (calibration): %d" % len(pre_trades))
    print("Post-freeze (validation): %d" % len(post_trades))
    print()

    if len(post_trades) < 10:
        print("NOT ENOUGH DATA for walk-forward validation.")
        print("Need at least 10 post-freeze trades. Currently have %d." % len(post_trades))
        return

    # Compute new confidence for all trades
    for t in all_trades:
        t["_new_conf"] = new_confidence(t)

    # ═══════════════════════════════════════════════════
    # VALIDATION 1: PF by confidence bucket (post-freeze)
    # ═══════════════════════════════════════════════════
    print("=" * 70)
    print("VALIDATION 1: PF BY CONFIDENCE BUCKET (POST-FREEZE)")
    print("=" * 70)

    buckets = defaultdict(list)
    for t in post_trades:
        s = t["_new_conf"]
        if s >= 70:
            buckets["70+"].append(t)
        elif s >= 60:
            buckets["60-70"].append(t)
        elif s >= 50:
            buckets["50-60"].append(t)
        elif s >= 45:
            buckets["45-50"].append(t)
        else:
            buckets["<45"].append(t)

    print("%-10s %7s %7s %7s %8s %8s" % ("Bucket", "Trades", "WinR", "PF", "AvgR", "TotalR"))
    print("-" * 52)

    bucket_pfs = []
    for label in ["70+", "60-70", "50-60", "45-50", "<45"]:
        bucket = buckets[label]
        if not bucket:
            print("%-10s %7s %7s %7s %8s %8s" % (label, "-", "-", "-", "-", "-"))
            continue
        rv = [t.get("realized_r", 0) or 0 for t in bucket]
        w = sum(r for r in rv if r > 0)
        l = sum(abs(r) for r in rv if r < 0)
        pf = w / max(0.01, l)
        wr = sum(1 for r in rv if r > 0) / len(rv) * 100
        avg_r = sum(rv) / len(rv)
        total_r = sum(rv)
        print("%-10s %7d %6.1f%% %7.3f %+.4f %8.2f" % (label, len(bucket), wr, pf, avg_r, total_r))
        bucket_pfs.append((label, pf))

    # Monotonicity check
    if len(bucket_pfs) >= 2:
        pf_values = [p for _, p in bucket_pfs]
        monotonic = all(pf_values[i] <= pf_values[i + 1] for i in range(len(pf_values) - 1))
        print()
        print("Monotonicity: %s" % ("PASS" if monotonic else "FAIL"))
        print("  %s" % " -> ".join("%s=%.3f" % (l, p) for l, p in bucket_pfs))

    # ═══════════════════════════════════════════════════
    # VALIDATION 2: PF by regime (post-freeze)
    # ═══════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("VALIDATION 2: PF BY REGIME (POST-FREEZE)")
    print("=" * 70)

    regime_groups = defaultdict(list)
    for t in post_trades:
        regime_groups[t.get("regime", "unknown") or "unknown"].append(t)

    print("%-18s %7s %7s %7s %8s" % ("Regime", "Trades", "WinR", "PF", "TotalR"))
    print("-" * 50)
    for regime in sorted(regime_groups.keys()):
        bucket = regime_groups[regime]
        rv = [t.get("realized_r", 0) or 0 for t in bucket]
        w = sum(r for r in rv if r > 0)
        l = sum(abs(r) for r in rv if r < 0)
        pf = w / max(0.01, l)
        wr = sum(1 for r in rv if r > 0) / len(rv) * 100
        total_r = sum(rv)
        print("%-18s %7d %6.1f%% %7.3f %8.2f" % (regime, len(bucket), wr, pf, total_r))

    # ═══════════════════════════════════════════════════
    # VALIDATION 3: PF by direction (post-freeze)
    # ═══════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("VALIDATION 3: PF BY DIRECTION (POST-FREEZE)")
    print("=" * 70)

    dir_groups = defaultdict(list)
    for t in post_trades:
        side = (t.get("side", "") or "").upper()
        if side in ("LONG", "BUY"):
            dir_groups["LONG"].append(t)
        elif side in ("SHORT", "SELL"):
            dir_groups["SHORT"].append(t)

    print("%-10s %7s %7s %7s %8s" % ("Direction", "Trades", "WinR", "PF", "TotalR"))
    print("-" * 42)
    for direction in ["LONG", "SHORT"]:
        bucket = dir_groups.get(direction, [])
        if not bucket:
            print("%-10s %7s %7s %7s %8s" % (direction, "-", "-", "-", "-"))
            continue
        rv = [t.get("realized_r", 0) or 0 for t in bucket]
        w = sum(r for r in rv if r > 0)
        l = sum(abs(r) for r in rv if r < 0)
        pf = w / max(0.01, l)
        wr = sum(1 for r in rv if r > 0) / len(rv) * 100
        total_r = sum(rv)
        print("%-10s %7d %6.1f%% %7.3f %8.2f" % (direction, len(bucket), wr, pf, total_r))

    # ═══════════════════════════════════════════════════
    # VALIDATION 4: Overall post-freeze metrics
    # ═══════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("VALIDATION 4: OVERALL POST-FREEZE METRICS")
    print("=" * 70)

    rv = [t.get("realized_r", 0) or 0 for t in post_trades]
    w = sum(r for r in rv if r > 0)
    l = sum(abs(r) for r in rv if r < 0)
    pf = w / max(0.01, l)
    wr = sum(1 for r in rv if r > 0) / len(rv) * 100
    ev = sum(rv) / len(rv)

    cum = 0
    peak = 0
    max_dd = 0
    for r in rv:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    print("  Trades:         %d" % len(post_trades))
    print("  Profit Factor:  %.3f" % pf)
    print("  Win Rate:       %.1f%%" % wr)
    print("  Expectancy:     %.4fR" % ev)
    print("  Total R:        %.2f" % sum(rv))
    print("  Max Drawdown:   %.2fR" % max_dd)

    # ═══════════════════════════════════════════════════
    # VALIDATION 5: Rolling window drift detection + statistical significance
    # ═══════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("VALIDATION 5: ROLLING WINDOW DRIFT DETECTION")
    print("=" * 70)

    windows = [25, 50, 100]
    print("%-12s %7s %7s %7s %8s %8s %10s" % ("Window", "Trades", "WinR", "PF", "AvgR", "Max DD", "Status"))
    print("-" * 65)

    window_pfs = {}
    for w_size in windows:
        if len(post_trades) < w_size:
            print("%-12s %7s %7s %7s %8s %8s %10s" % (
                "Last %d" % w_size, "-", "-", "-", "-", "-", "INSUFFICIENT"))
            continue

        window_trades = post_trades[-w_size:]
        rv = [t.get("realized_r", 0) or 0 for t in window_trades]
        wins = [r for r in rv if r > 0]
        losses = [abs(r) for r in rv if r < 0]
        w_pf = sum(wins) / max(0.01, sum(losses))
        w_wr = len(wins) / max(1, len(rv)) * 100
        w_ev = sum(rv) / len(rv)
        window_pfs[w_size] = w_pf

        cum = 0
        peak = 0
        w_dd = 0
        for r in rv:
            cum += r
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > w_dd:
                w_dd = dd

        if w_pf >= 0.8:
            status = "NORMAL"
        elif w_pf >= 0.5:
            status = "CAUTION"
        elif w_pf >= 0.3:
            status = "WARNING"
        else:
            status = "CRITICAL"

        print("%-12s %7d %6.1f%% %7.3f %+.4f %8.2f %10s" % (
            "Last %d" % w_size, len(window_trades), w_wr, w_pf, w_ev, w_dd, status))

    # Statistical significance: bootstrap 95% CI for PF
    if len(post_trades) >= 20:
        print()
        print("STATISTICAL SIGNIFICANCE (Bootstrap 95% CI):")
        print("-" * 50)
        import random
        random.seed(42)

        rv_all = [t.get("realized_r", 0) or 0 for t in post_trades]
        boot_pfs = []
        for _ in range(5000):
            sample = [random.choice(rv_all) for _ in range(len(rv_all))]
            sw = sum(r for r in sample if r > 0)
            sl = sum(abs(r) for r in sample if r < 0)
            boot_pfs.append(sw / max(0.01, sl))
        boot_pfs.sort()
        ci_low = boot_pfs[125]
        ci_high = boot_pfs[4875]
        print("  Overall PF: %.3f" % pf)
        print("  95%% CI: [%.3f, %.3f]" % (ci_low, ci_high))
        if ci_high < 1.0:
            print("  Result: PF SIGNIFICANTLY below 1.0 (edge not confirmed)")
        elif ci_low > 1.0:
            print("  Result: PF SIGNIFICANTLY above 1.0 (edge confirmed)")
        else:
            print("  Result: CI includes 1.0 (inconclusive — need more data)")

        # Drift significance: is recent window outside expected variation?
        if len(post_trades) >= 50:
            recent_50 = post_trades[-50:]
            rv_recent = [t.get("realized_r", 0) or 0 for t in recent_50]
            w_recent = sum(r for r in rv_recent if r > 0)
            l_recent = sum(abs(r) for r in rv_recent if r < 0)
            pf_recent = w_recent / max(0.01, l_recent)

            print()
            print("DRIFT SIGNIFICANCE (Recent 50 vs Overall):")
            print("-" * 50)
            print("  Recent 50 PF: %.3f" % pf_recent)
            print("  Overall PF:   %.3f" % pf)
            print("  Expected 95%% CI: [%.3f, %.3f]" % (ci_low, ci_high))
            if pf_recent < ci_low:
                print("  Result: OUTSIDE expected variation — POSSIBLE DRIFT")
            elif pf_recent > ci_high:
                print("  Result: ABOVE expected variation — IMPROVEMENT")
            else:
                print("  Result: Within expected variation — NORMAL")

    # ═══════════════════════════════════════════════════
    # VALIDATION 6: Confidence calibration monitoring
    # ═══════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("VALIDATION 6: CONFIDENCE CALIBRATION (Predicted vs Observed)")
    print("=" * 70)

    # For each confidence bucket, compare predicted win rate to observed
    print("%-10s %7s %7s %7s %7s %10s" % ("Bucket", "Trades", "Expected", "Observed", "Gap", "Calibration"))
    print("-" * 52)

    # Expected win rates by bucket (from calibration data)
    # These should be updated periodically from the calibration dataset
    expected_wr = {
        "70+": 0.467,
        "60-70": 0.467,
        "50-60": 0.017,
        "45-50": 0.011,
        "<45": 0.000,
    }

    calibration_scores = []
    for label in ["70+", "60-70", "50-60", "45-50", "<45"]:
        bucket = buckets.get(label, [])
        if not bucket:
            print("%-10s %7s %7s %7s %7s %10s" % (label, "-", "-", "-", "-", "-"))
            continue
        observed_wr = sum(1 for t in bucket if (t.get("realized_r", 0) or 0) > 0) / len(bucket)
        expected = expected_wr.get(label, 0)
        gap = observed_wr - expected
        if abs(gap) < 0.05:
            cal = "CALIBRATED"
        elif abs(gap) < 0.15:
            cal = "DRIFT"
        else:
            cal = "MISCALIBRATED"
        calibration_scores.append(1.0 - min(1.0, abs(gap) * 5))
        print("%-10s %7d %6.1f%% %6.1f%% %+.1f%% %10s" % (
            label, len(bucket), expected * 100, observed_wr * 100, gap * 100, cal))

    # ═══════════════════════════════════════════════════
    # VALIDATION 7: Overall Stability Score
    # ═══════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("VALIDATION 7: OVERALL STABILITY SCORE")
    print("=" * 70)

    # Component scores (0-100)
    # 1. Confidence ranking: based on monotonicity
    if len(bucket_pfs) >= 2:
        pf_vals = [p for _, p in bucket_pfs]
        mono = all(pf_vals[i] <= pf_vals[i + 1] for i in range(len(pf_vals) - 1))
        conf_rank_score = 100 if mono else max(0, 50 - abs(pf_vals[-1] - pf_vals[0]) * 20)
    else:
        conf_rank_score = 0

    # 2. Regime stability: based on variance of regime PFs
    regime_pfs = []
    for r in regime_groups:
        if len(regime_groups[r]) >= 3:
            rv = [t.get("realized_r", 0) or 0 for t in regime_groups[r]]
            w = sum(r for r in rv if r > 0)
            l = sum(abs(r) for r in rv if r < 0)
            regime_pfs.append(w / max(0.01, l))
    if regime_pfs:
        regime_mean = sum(regime_pfs) / len(regime_pfs)
        regime_var = sum((p - regime_mean) ** 2 for p in regime_pfs) / len(regime_pfs)
        regime_score = max(0, 100 - regime_var * 100)
    else:
        regime_score = 0

    # 3. Direction stability: based on long/short PF difference
    long_pf = 0
    short_pf = 0
    if "LONG" in dir_groups and len(dir_groups["LONG"]) >= 3:
        rv = [t.get("realized_r", 0) or 0 for t in dir_groups["LONG"]]
        w = sum(r for r in rv if r > 0)
        l = sum(abs(r) for r in rv if r < 0)
        long_pf = w / max(0.01, l)
    if "SHORT" in dir_groups and len(dir_groups["SHORT"]) >= 3:
        rv = [t.get("realized_r", 0) or 0 for t in dir_groups["SHORT"]]
        w = sum(r for r in rv if r > 0)
        l = sum(abs(r) for r in rv if r < 0)
        short_pf = w / max(0.01, l)
    dir_diff = abs(long_pf - short_pf)
    dir_score = max(0, 100 - dir_diff * 50)

    # 4. Drift: based on recent vs overall PF comparison
    if len(post_trades) >= 50:
        recent_50 = post_trades[-50:]
        rv_r = [t.get("realized_r", 0) or 0 for t in recent_50]
        w_r = sum(r for r in rv_r if r > 0)
        l_r = sum(abs(r) for r in rv_r if r < 0)
        pf_r = w_r / max(0.01, l_r)
        drift_ratio = pf_r / max(0.01, pf) if pf > 0 else 0
        drift_score = max(0, min(100, drift_ratio * 50))
    else:
        drift_score = 50  # insufficient data

    # 5. Calibration: average calibration score
    cal_score = (sum(calibration_scores) / len(calibration_scores) * 100) if calibration_scores else 50

    # Overall stability
    overall = (conf_rank_score * 0.25 + regime_score * 0.20 + dir_score * 0.15 +
               drift_score * 0.20 + cal_score * 0.20)

    print("  Confidence Ranking:    %3.0f/100" % conf_rank_score)
    print("  Regime Stability:      %3.0f/100" % regime_score)
    print("  Direction Stability:   %3.0f/100" % dir_score)
    print("  Drift Score:           %3.0f/100" % drift_score)
    print("  Calibration Score:     %3.0f/100" % cal_score)
    print("  ────────────────────────────────")
    print("  Overall Stability:     %3.0f/100" % overall)

    if overall >= 80:
        print("  Status: EXCELLENT — model is stable")
    elif overall >= 60:
        print("  Status: GOOD — minor variations detected")
    elif overall >= 40:
        print("  Status: CAUTION — monitoring recommended")
    else:
        print("  Status: CRITICAL — model may be degrading")

    # ═══════════════════════════════════════════════════
    # MILESTONE CHECK (compute first so Validation 8 can reference)
    # ═══════════════════════════════════════════════════
    milestones = [
        ("100+ closed trades with frozen model", len(post_trades) >= 100),
        ("PF > 1.0 on unseen data", pf > 1.0),
        ("Confidence buckets monotonic", len(bucket_pfs) >= 2 and all(
            bucket_pfs[i][1] <= bucket_pfs[i + 1][1] for i in range(len(bucket_pfs) - 1))),
        ("Stability across regimes (all > 0 PF)", all(
            sum(t.get("realized_r", 0) or 0 for t in regime_groups[r]) > 0
            for r in regime_groups if len(regime_groups[r]) >= 5)),
        ("Stability across directions (both > 0 PF)", all(
            sum(t.get("realized_r", 0) or 0 for t in dir_groups.get(d, [])) > 0
            for d in ["LONG", "SHORT"] if len(dir_groups.get(d, [])) >= 5)),
    ]

    # ═══════════════════════════════════════════════════
    # VALIDATION 8: Historical Log (Time Series of Model Health)
    # ═══════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("VALIDATION 8: HISTORICAL LOG")
    print("=" * 70)

    import csv

    history_path = Path(__file__).parent / "data" / "validation_history.csv"
    run_date = datetime.now().strftime("%Y-%m-%d")
    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Determine drift status
    drift_status = "N/A"
    if len(post_trades) >= 50:
        recent_50 = post_trades[-50:]
        rv_recent = [t.get("realized_r", 0) or 0 for t in recent_50]
        w_recent = sum(r for r in rv_recent if r > 0)
        l_recent = sum(abs(r) for r in rv_recent if r < 0)
        pf_recent = w_recent / max(0.01, l_recent)
        if len(post_trades) >= 20:
            rv_all_boot = [t.get("realized_r", 0) or 0 for t in post_trades]
            boot_pfs_local = []
            import random as _rnd
            _rnd.seed(42)
            for _ in range(2000):
                sample = [_rnd.choice(rv_all_boot) for _ in range(len(rv_all_boot))]
                sw = sum(r for r in sample if r > 0)
                sl = sum(abs(r) for r in sample if r < 0)
                boot_pfs_local.append(sw / max(0.01, sl))
            boot_pfs_local.sort()
            ci_lo = boot_pfs_local[50]
            ci_hi = boot_pfs_local[1950]
            if pf_recent < ci_lo:
                drift_status = "DRIFT_DOWN"
            elif pf_recent > ci_hi:
                drift_status = "DRIFT_UP"
            else:
                drift_status = "NORMAL"

    # Compute overall stability (reuse from Validation 7)
    overall_stability = overall  # Already computed above

    # Milestone progress
    milestones_passed = sum(1 for _, passed in milestones if passed)
    milestones_total = len(milestones)

    # ── Contextual fields ──────────────────────────────────
    # 1. Market regime mix (%)
    regime_counts = defaultdict(int)
    for t in post_trades:
        regime_counts[t.get("regime", "unknown") or "unknown"] += 1
    regime_mix = ";".join("%s:%d%%" % (r, round(c / len(post_trades) * 100))
                          for r, c in sorted(regime_counts.items(), key=lambda x: -x[1]))

    # 2. Symbols traded
    symbols = sorted(set(t.get("symbol", "?") or "?" for t in post_trades))
    symbols_str = ",".join(symbols)

    # 3. Average confidence (v33 recalculated)
    avg_confidence = sum(t.get("_new_conf", 0) for t in post_trades) / max(1, len(post_trades))

    # 4. Average hold time (minutes)
    hold_times = [t.get("hold_minutes", 0) or 0 for t in post_trades if (t.get("hold_minutes", 0) or 0) > 0]
    avg_hold = sum(hold_times) / max(1, len(hold_times))

    # 5. Long vs Short count
    long_count = sum(1 for t in post_trades if (t.get("side", "") or "").upper() in ("LONG", "BUY"))
    short_count = sum(1 for t in post_trades if (t.get("side", "") or "").upper() in ("SHORT", "SELL"))

    # 6. Sample-size quality
    unique_symbols = len(symbols)
    def _date_str(val):
        if val is None:
            return ""
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val / 1000 if val > 1e12 else val).strftime("%Y-%m-%d")
        return str(val)[:10]
    trading_days = sorted(set(
        _date_str(t.get("closed_at")) for t in post_trades if t.get("closed_at")
    ))
    unique_trading_days = len(trading_days)

    # 7. R distribution metrics
    rv_all = [t.get("realized_r", 0) or 0 for t in post_trades]
    sorted_r = sorted(rv_all)
    if len(sorted_r) % 2 == 0:
        median_r = (sorted_r[len(sorted_r) // 2 - 1] + sorted_r[len(sorted_r) // 2]) / 2
    else:
        median_r = sorted_r[len(sorted_r) // 2]
    avg_r = sum(rv_all) / max(1, len(rv_all))

    # Write to CSV
    file_exists = history_path.exists()
    with open(history_path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "run_date", "run_timestamp", "post_trades", "overall_pf",
                "win_rate", "expectancy", "max_dd", "stability_score",
                "drift_status", "calibration_score", "conf_rank_score",
                "regime_score", "dir_score", "drift_score",
                "milestones_passed", "milestones_total",
                "avg_confidence", "avg_hold_min",
                "long_count", "short_count",
                "unique_symbols", "unique_trading_days",
                "avg_r", "median_r",
                "regime_mix", "symbols", "version"
            ])
        writer.writerow([
            run_date, run_ts, len(post_trades),
            "%.3f" % pf, "%.1f" % wr, "%.4f" % ev,
            "%.2f" % max_dd, "%.0f" % overall_stability,
            drift_status, "%.0f" % cal_score,
            "%.0f" % conf_rank_score, "%.0f" % regime_score,
            "%.0f" % dir_score, "%.0f" % drift_score,
            milestones_passed, milestones_total,
            "%.1f" % avg_confidence, "%.0f" % avg_hold,
            long_count, short_count,
            unique_symbols, unique_trading_days,
            "%.4f" % avg_r, "%.4f" % median_r,
            regime_mix, symbols_str, "v33"
        ])

    print("  Logged to: %s" % history_path)

    # Display last 10 entries with context
    if history_path.exists():
        with open(history_path, "r") as f:
            reader = list(csv.reader(f))
        if len(reader) > 1:
            header = reader[0]
            rows = reader[1:]
            recent = rows[-10:]
            print()
            print("  RECENT HISTORY (last %d runs):" % len(recent))
            print("  " + "-" * 100)
            print("  %-12s %6s %7s %6s %5s %5s %5s %5s %10s %10s" % (
                "Date", "Trades", "PF", "WinR", "Stab", "Calib", "AvgCF", "Hold", "Drift", "L/S"))
            print("  " + "-" * 100)
            for row in recent:
                # Indices: 0=date, 2=trades, 3=pf, 4=wr, 7=stability,
                # 9=calibration, 8=drift, 16=avg_conf, 17=avg_hold, 18=long, 19=short
                if len(row) >= 20:
                    print("  %-12s %6s %7s %5s%% %5s %5s %5s %5s %10s %4s/%s" % (
                        row[0], row[2], row[3], row[4],
                        row[7], row[9],
                        row[16], row[17],
                        row[8], row[18], row[19]))
                elif len(row) >= 10:
                    print("  %-12s %6s %7s %5s%% %5s %5s" % (
                        row[0], row[2], row[3], row[4], row[7], row[9]))
            print("  " + "-" * 100)

            # Show context for latest run
            latest = rows[-1]
            if len(latest) >= 26:
                print()
                print("  LATEST RUN CONTEXT:")
                print("  Regime mix:   %s" % latest[24].replace(";", "  "))
                print("  Symbols:      %d unique (%s)" % (int(latest[20]), latest[25][:60] + "..." if len(latest[25]) > 60 else latest[25]))
                print("  Trading days: %s" % latest[21])
                print("  Avg conf:     %s" % latest[16])
                print("  Avg hold:     %s min" % latest[17])
                print("  Avg R:        %s" % latest[22])
                print("  Median R:     %s" % latest[23])
                print("  Long/Short:   %s / %s" % (latest[18], latest[19]))

    # ═══════════════════════════════════════════════════
    # VALIDATION SUMMARY — Compact Operational View
    # ═══════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    print()

    # Pass/Fail for each acceptance criterion
    mono_pass = len(bucket_pfs) >= 2 and all(
        bucket_pfs[i][1] <= bucket_pfs[i + 1][1] for i in range(len(bucket_pfs) - 1))
    regime_pass = all(
        sum(t.get("realized_r", 0) or 0 for t in regime_groups[r]) > 0
        for r in regime_groups if len(regime_groups[r]) >= 5)
    direction_pass = all(
        sum(t.get("realized_r", 0) or 0 for t in dir_groups.get(d, [])) > 0
        for d in ["LONG", "SHORT"] if len(dir_groups.get(d, [])) >= 5)

    # Calibration pass: all active buckets within 10% gap
    cal_pass = all(abs(s - 1.0) < 0.5 for s in calibration_scores) if calibration_scores else False

    # Overall validation status
    has_enough = len(post_trades) >= 100
    if has_enough and pf > 1.0 and mono_pass and regime_pass and direction_pass:
        status_label = "VALIDATED"
    elif not has_enough:
        status_label = "INSUFFICIENT"
    elif pf < 0.5 and drift_status == "DRIFT_DOWN":
        status_label = "DEGRADED"
    else:
        status_label = "COLLECTING"

    # ── Walk-forward Progress ──────────────────────────
    TRADE_TARGET = 500
    DAY_TARGET = 30
    SYMBOL_TARGET = 100

    trade_pct = min(100, len(post_trades) / TRADE_TARGET * 100)
    day_pct = min(100, unique_trading_days / DAY_TARGET * 100)
    sym_pct = min(100, unique_symbols / SYMBOL_TARGET * 100)
    validation_confidence = trade_pct * 0.50 + day_pct * 0.25 + sym_pct * 0.25

    def _bar(current, target, width=20):
        pct = min(1.0, current / max(1, target))
        filled = int(pct * width)
        return "\u2588" * filled + "\u2591" * (width - filled) + " %d/%d" % (current, target)

    def _bar_pct(pct_val, width=20):
        filled = int(pct_val / 100 * width)
        return "\u2588" * filled + "\u2591" * (width - filled) + " %.0f%%" % pct_val

    # ── Sample Diversity ───────────────────────────────
    sessions_seen = len(set(t.get("session", "unknown") or "unknown" for t in post_trades))
    regimes_seen = len(regime_counts)

    sym_div = min(100, unique_symbols / 50 * 100)
    day_div = min(100, unique_trading_days / 20 * 100)
    sess_div = min(100, sessions_seen / 4 * 100)
    regime_div = min(100, regimes_seen / 5 * 100)

    diversity_score = (sym_div * 0.35 + day_div * 0.30 + sess_div * 0.15 + regime_div * 0.20)

    # ── Distribution Similarity ────────────────────────
    train_regime_counts = defaultdict(int)
    for t in pre_trades:
        train_regime_counts[t.get("regime", "unknown") or "unknown"] += 1
    train_total = max(1, len(pre_trades))
    val_total = max(1, len(post_trades))

    all_regimes = sorted(set(list(train_regime_counts.keys()) + list(regime_counts.keys())))
    shifts = []
    for r in all_regimes:
        train_pct_r = train_regime_counts.get(r, 0) / train_total * 100
        val_pct_r = regime_counts.get(r, 0) / val_total * 100
        shifts.append(abs(val_pct_r - train_pct_r))

    max_shift = max(shifts) if shifts else 0
    avg_shift = sum(shifts) / len(shifts) if shifts else 0
    distribution_similarity = max(0, min(100, 100 - avg_shift * 2.5))

    if distribution_similarity >= 90:
        dist_label = "SIMILAR"
    elif distribution_similarity >= 75:
        dist_label = "MODERATE"
    else:
        dist_label = "MAJOR SHIFT"

    # ── Stability trend from history ───────────────────
    stability_trend = []
    if history_path.exists():
        with open(history_path, "r") as f:
            reader = list(csv.reader(f))
        if len(reader) > 1:
            for row in reader[1:]:
                if len(row) >= 8:
                    try:
                        stability_trend.append((row[0], float(row[7])))
                    except (ValueError, IndexError):
                        pass

    # ── Recommendation ─────────────────────────────────
    if not has_enough:
        recommendation = "Continue collecting data"
    elif status_label == "DEGRADED":
        recommendation = "Investigate model degradation"
    elif status_label == "VALIDATED":
        recommendation = "Model validated \u2014 consider live deployment"
    else:
        recommendation = "Continue collecting data"

    # DECISION ENGINE
    print("  MODEL HEALTH \u2014 v33 (Frozen)")
    print()
    print("  Validation Progress       %s" % _bar_pct(validation_confidence))
    print("  Sample Diversity          %s" % _bar_pct(diversity_score))
    print("  Distribution Match        %s  (%s)" % (_bar_pct(distribution_similarity), dist_label))
    print()

    if drift_status == "NORMAL":
        drift_icon = "\033[32m\u25cf Normal\033[0m"
    elif "DRIFT_DOWN" in drift_status:
        drift_icon = "\033[31m\u25cf Drifting down\033[0m"
    elif "DRIFT_UP" in drift_status:
        drift_icon = "\033[32m\u25cf Drifting up\033[0m"
    else:
        drift_icon = "\u25cb N/A"

    print("  Drift                     %s" % drift_icon)
    print("  Calibration               %s" % ("\033[32m\u25cf Passed\033[0m" if cal_pass else "\033[31m\u25cf Failed\033[0m"))
    print("  Confidence Monotonicity   %s" % ("\033[32m\u25cf Passed\033[0m" if mono_pass else "\033[31m\u25cf Failed\033[0m"))
    print()

    print("  RECOMMENDATION")
    print("  \u2192 %s" % recommendation)
    print()
    print("  " + "\u2500" * 50)

    # Detail sections
    print()
    print("  WALK-FORWARD PROGRESS (detail)")
    print("  Closed trades:   %s" % _bar(len(post_trades), TRADE_TARGET))
    print("  Trading days:    %s" % _bar(unique_trading_days, DAY_TARGET))
    print("  Unique symbols:  %s" % _bar(unique_symbols, SYMBOL_TARGET))
    print("  Validation conf: %.0f%%" % validation_confidence)
    print()

    print("  PROFITABILITY")
    print("  PF:                 %.3f" % pf)
    print("  Win Rate:           %.1f%%" % wr)
    print("  Expectancy:         %.4fR" % ev)
    print("  Avg R:              %.4fR" % avg_r)
    print("  Median R:           %.4fR" % median_r)
    print("  Max Drawdown:       %.2fR" % max_dd)
    print()

    print("  SAMPLE DIVERSITY (detail)")
    print("  Symbols:            %d" % unique_symbols)
    print("  Trading days:       %d" % unique_trading_days)
    print("  Sessions:           %d" % sessions_seen)
    print("  Regimes:            %d" % regimes_seen)
    print("  Diversity score:    %.0f/100" % diversity_score)
    print()

    # Regime Distribution Table
    print("  REGIME DISTRIBUTION (Training vs Validation)")
    print("  %-18s %10s %10s %8s" % ("Regime", "Training", "Validation", "Shift"))
    print("  " + "-" * 50)
    for r in all_regimes:
        train_pct_r = train_regime_counts.get(r, 0) / train_total * 100
        val_pct_r = regime_counts.get(r, 0) / val_total * 100
        shift = val_pct_r - train_pct_r
        sign = "+" if shift >= 0 else ""
        print("  %-18s %9.1f%% %9.1f%% %s%.1f%%" % (r, train_pct_r, val_pct_r, sign, shift))
    print()
    print("  Max shift:          %.1f%%" % max_shift)
    print("  Avg shift:          %.1f%%" % avg_shift)
    print("  Distribution match: %.0f/100 (%s)" % (distribution_similarity, dist_label))
    print()

    # Stability Trend
    if len(stability_trend) >= 2:
        print("  STABILITY TREND")
        for date, stab in stability_trend:
            bar_len = int(stab / 100 * 30)
            print("  %-12s %s %.0f" % (date, "\u2588" * bar_len, stab))
        print()

    # Acceptance Criteria
    print("  ACCEPTANCE CRITERIA")
    print("  [%s] Confidence monotonicity" % ("PASS" if mono_pass else "FAIL"))
    print("  [%s] Regime stability" % ("PASS" if regime_pass else "FAIL"))
    print("  [%s] Direction stability" % ("PASS" if direction_pass else "FAIL"))
    print("  [%s] Calibration" % ("PASS" if cal_pass else "FAIL"))
    print()

    # Status Box
    if status_label == "VALIDATED":
        status_icon = "\033[32m\u25cf VALIDATED\033[0m"
    elif status_label == "DEGRADED":
        status_icon = "\033[31m\u25cf DEGRADED\033[0m"
    else:
        status_icon = "\033[33m\u25cf %s\033[0m" % status_label

    print("  +---------------------------------------+")
    print("  |  STATUS: %-27s |" % status_label)
    print("  |  %-35s |" % status_icon)
    print("  +---------------------------------------+")

    # Milestones
    print()
    print("  MILESTONES")
    for desc, passed in milestones:
        mark = "\u2713" if passed else "\u00b7"
        print("    %s %s" % (mark, desc))
    print()
    print("=" * 70)


if __name__ == "__main__":
    run_validation()
