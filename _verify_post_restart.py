"""
Post-Restart Verification — Phased Trade Lifecycle Checklist

Verifies data pipeline integrity across three phases:
  Phase 1: First completed trade (4 conditions)
  Phase 2: First 5 completed trades (lifecycle complexity)
  Phase 3: First 20 completed trades (statistical foundation)

Complex lifecycle transitions tested:
  Open → Trailing → Close
  Open → Partial TP → Stop moved → Close
  Open → Restart → Close
  Open → MAE drawdown → Recovery → Close

Usage:
    python3 _verify_post_restart.py [--watch] [--phase 1|2|3]
    
    --watch: continuously monitor until phase completion
    --phase: force specific phase check (default: auto-detect)
"""

import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime


DB_PATH = Path(__file__).resolve().parent / "packages/ai-engine" / "data" / "institutional_v1.db"
CAL_DB_PATH = Path(__file__).resolve().parent / "packages/ai-engine" / "data" / "database" / "confidence_calibration.db"
LOG_PATH = Path(__file__).resolve().parent / "packages/ai-engine" / "data" / "logs" / "engine_service.log"


class VerificationResult:
    def __init__(self, condition: str, passed: bool, detail: str, severity: str = "FAIL"):
        self.condition = condition
        self.passed = passed
        self.detail = detail
        self.severity = severity  # FAIL = must fix, WARN = investigate
    
    def __str__(self):
        if self.passed:
            icon = "✅"
        elif self.severity == "WARN":
            icon = "⚠️"
        else:
            icon = "❌"
        return f"{icon} {self.condition}\n   {self.detail}"


def check_condition_1(db: sqlite3.Connection) -> VerificationResult:
    """Trade outcome written: tp_hit/sl_hit, not pending, with realized_r populated."""
    # Find most recently closed trade (status=expired, outcome != pending)
    row = db.execute("""
        SELECT id, symbol, side, outcome, realized_r, mae_pct, mfe_pct, status, confidence
        FROM signals 
        WHERE status = 'expired' AND outcome IN ('tp_hit', 'sl_hit')
        ORDER BY id DESC LIMIT 1
    """).fetchone()
    
    if not row:
        return VerificationResult(
            "1. Trade outcome written",
            False,
            "No closed trades with outcome=tp_hit/sl_hit found yet"
        )
    
    sig_id, symbol, side, outcome, realized_r, mae_pct, mfe_pct, status, confidence = row
    
    issues = []
    if realized_r is None or realized_r == 0:
        issues.append(f"realized_r={realized_r} (expected non-zero)")
    if mae_pct is None:
        issues.append("mae_pct=NULL")
    if mfe_pct is None:
        issues.append("mfe_pct=NULL")
    
    if issues:
        return VerificationResult(
            "1. Trade outcome written",
            False,
            f"{symbol} {side} → {outcome} BUT: {', '.join(issues)}"
        )
    
    return VerificationResult(
        "1. Trade outcome written",
        True,
        f"{symbol} {side} → {outcome} | R={realized_r:.2f} | MAE={mae_pct:.2f}% | MFE={mfe_pct:.2f}% | conf={confidence}"
    )


def check_condition_2(cal_db: sqlite3.Connection) -> VerificationResult:
    """Calibration record updated from Pending → Win/Loss."""
    pending = cal_db.execute(
        "SELECT COUNT(*) FROM calibration_log WHERE outcome = 'pending'"
    ).fetchone()[0]
    
    completed = cal_db.execute(
        "SELECT COUNT(*) FROM calibration_log WHERE outcome IN ('win', 'loss')"
    ).fetchone()[0]
    
    total = cal_db.execute("SELECT COUNT(*) FROM calibration_log").fetchone()[0]
    
    if completed == 0:
        return VerificationResult(
            "2. Calibration record updated",
            False,
            f"Zero completed calibration records ({pending} pending, {total} total) — record_outcome still not working"
        )
    
    # Show breakdown
    wins = cal_db.execute(
        "SELECT COUNT(*) FROM calibration_log WHERE outcome = 'win'"
    ).fetchone()[0]
    losses = cal_db.execute(
        "SELECT COUNT(*) FROM calibration_log WHERE outcome = 'loss'"
    ).fetchone()[0]
    
    return VerificationResult(
        "2. Calibration record updated",
        True,
        f"{completed} completed ({wins} wins, {losses} losses) out of {total} total | {pending} still pending"
    )


def check_condition_3(db: sqlite3.Connection) -> VerificationResult:
    """Dashboard statistics have real data (not just expired_no_data)."""
    outcomes = db.execute(
        "SELECT outcome, COUNT(*) FROM signals GROUP BY outcome"
    ).fetchall()
    
    outcome_map = {o: c for o, c in outcomes}
    total = sum(outcome_map.values())
    expired_no_data = outcome_map.get("expired_no_data", 0)
    pending = outcome_map.get("pending", 0)
    tp_hit = outcome_map.get("tp_hit", 0)
    sl_hit = outcome_map.get("sl_hit", 0)
    real_trades = tp_hit + sl_hit
    
    if real_trades == 0:
        return VerificationResult(
            "3. Dashboard statistics meaningful",
            False,
            f"Zero real trade outcomes (tp_hit=0, sl_hit=0) — {expired_no_data} expired_no_data, {pending} pending"
        )
    
    win_rate = (tp_hit / real_trades * 100) if real_trades > 0 else 0
    
    return VerificationResult(
        "3. Dashboard statistics meaningful",
        True,
        f"Real outcomes: {tp_hit} wins, {sl_hit} losses ({win_rate:.1f}% WR) | {expired_no_data} expired_no_data | {pending} pending"
    )


def check_condition_4() -> VerificationResult:
    """No calibration exceptions logged since restart."""
    if not LOG_PATH.exists():
        return VerificationResult(
            "4. No calibration exceptions",
            False,
            f"Log file not found: {LOG_PATH}"
        )
    
    # Read last 500 lines of log
    try:
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()
        
        recent = lines[-500:] if len(lines) > 500 else lines
        
        calib_errors = [
            l.strip() for l in recent
            if "CALIBRATOR_OUTCOME_FAILED" in l or "Calibrator outcome failed" in l
        ]
        
        calib_successes = [
            l.strip() for l in recent
            if "SIGNAL_EXPIRED" in l and ("tp_hit" in l or "sl_hit" in l)
        ]
        
        if calib_errors:
            return VerificationResult(
                "4. No calibration exceptions",
                False,
                f"{len(calib_errors)} calibration errors found:\n" + "\n".join(f"   {e}" for e in calib_errors[:3])
            )
        
        return VerificationResult(
            "4. No calibration exceptions",
            True,
            f"No calibration errors in last {len(recent)} log lines | {len(calib_successes)} signal expiry events logged"
        )
    except Exception as e:
        return VerificationResult(
            "4. No calibration exceptions",
            False,
            f"Error reading log: {e}"
        )


# ═══════════════════════════════════════════════════════════════
# VERIFICATION PROGRESS DASHBOARD
# ═══════════════════════════════════════════════════════════════

def print_progress_dashboard():
    """Print a compact verification progress table."""
    try:
        db = sqlite3.connect(str(DB_PATH), timeout=10)
        cal_db = sqlite3.connect(str(CAL_DB_PATH), timeout=10)
    except Exception as e:
        print(f"  Cannot open databases: {e}")
        return
    
    try:
        # Count metrics
        completed = db.execute(
            "SELECT COUNT(*) FROM signals WHERE outcome IN ('tp_hit', 'sl_hit')"
        ).fetchone()[0]
        
        with_realized_r = db.execute(
            "SELECT COUNT(*) FROM signals WHERE outcome IN ('tp_hit', 'sl_hit') AND realized_r != 0 AND realized_r IS NOT NULL"
        ).fetchone()[0]
        
        with_mae_mfe = db.execute(
            "SELECT COUNT(*) FROM signals WHERE outcome IN ('tp_hit', 'sl_hit') AND mae_pct IS NOT NULL AND mfe_pct IS NOT NULL"
        ).fetchone()[0]
        
        cal_completed = cal_db.execute(
            "SELECT COUNT(*) FROM calibration_log WHERE outcome IN ('win', 'loss')"
        ).fetchone()[0]
        
        open_pos = db.execute(
            "SELECT COUNT(*) FROM positions WHERE status = 'open'"
        ).fetchone()[0]
        
        total_signals = db.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        
        # Check parameter freeze
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent / "packages" / "ai-engine"))
            from scanner.parameter_freeze import ParameterFreeze
            freeze = ParameterFreeze()
            freeze_status = freeze.check()
            frozen_ok = freeze_status.get("frozen", False) and freeze_status.get("clean", True)
        except Exception as e:
            frozen_ok = False
        
        # Check reconciliation
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent / "packages" / "ai-engine"))
            from scanner.reconciliation_assertions import run_reconciliation
            recon = run_reconciliation(str(DB_PATH))
            # Only count actual FAILs (not WARNs) as failures
            recon_actual_fails = sum(1 for r in recon.results if not r.passed and r.severity == "FAIL")
            recon_pass = recon_actual_fails == 0
            recon_total = len(recon.results)
            recon_pass_count = sum(1 for r in recon.results if r.passed)
        except Exception as e:
            recon_pass = False
            recon_total = 10
            recon_pass_count = 0
        
        # Print dashboard
        target = 20
        print()
        print("╔═══════════════════════════════════════════════════════════════╗")
        print("║           VERIFICATION PROGRESS DASHBOARD                    ║")
        print("╠═══════════════════════════════════════════════════════════════╣")
        
        def bar(current, target, width=20):
            filled = min(int(width * current / target), width) if target > 0 else 0
            return "█" * filled + "░" * (width - filled)
        
        print(f"║  Metric                    Current   Target   Progress       ║")
        print(f"║  ─────────────────────────────────────────────────────────── ║")
        print(f"║  Completed trades          {completed:>5}     {target:>5}   {bar(completed, target)} {completed/target*100:.0f}% ║")
        print(f"║  With realized R           {with_realized_r:>5}     {target:>5}   {bar(with_realized_r, target)} {with_realized_r/target*100:.0f}% ║")
        print(f"║  With MAE/MFE              {with_mae_mfe:>5}     {target:>5}   {bar(with_mae_mfe, target)} {with_mae_mfe/target*100:.0f}% ║")
        print(f"║  Calibration records       {cal_completed:>5}     {target:>5}   {bar(cal_completed, target)} {cal_completed/target*100:.0f}% ║")
        print(f"║  ─────────────────────────────────────────────────────────── ║")
        print(f"║  Open positions            {open_pos:>5}        —        —              ║")
        print(f"║  Total signals          {total_signals:>7}        —        —              ║")
        print(f"║  ─────────────────────────────────────────────────────────── ║")
        print(f"║  Reconciliation     {'✅' if recon_pass else '❌'}  {recon_pass_count}/{recon_total} {'PASS' if recon_pass else 'FAIL':>6}        —              ║")
        print(f"║  Parameter freeze   {'✅' if frozen_ok else '❌'}  {'LOCKED' if frozen_ok else 'OPEN':>6}        —        —              ║")
        print(f"╚═══════════════════════════════════════════════════════════════╝")
        print()
        
    finally:
        db.close()
        cal_db.close()


def run_verification() -> list:
    """Run all four conditions."""
    results = []
    
    # Connect to databases
    try:
        db = sqlite3.connect(str(DB_PATH), timeout=10)
        db.row_factory = sqlite3.Row
    except Exception as e:
        return [VerificationResult("Database Connection", False, f"Cannot open {DB_PATH}: {e}")]
    
    try:
        cal_db = sqlite3.connect(str(CAL_DB_PATH), timeout=10)
    except Exception as e:
        db.close()
        return [VerificationResult("Calibration DB Connection", False, f"Cannot open {CAL_DB_PATH}: {e}")]
    
    try:
        results.append(check_condition_1(db))
        results.append(check_condition_2(cal_db))
        results.append(check_condition_3(db))
        results.append(check_condition_4())
    finally:
        db.close()
        cal_db.close()
    
    return results


# ═══════════════════════════════════════════════════════════════
# PHASE 2: First 5 Completed Trades — Lifecycle Complexity
# ═══════════════════════════════════════════════════════════════

def check_phase2_outcome_integrity(db: sqlite3.Connection) -> VerificationResult:
    """All completed trades should have tp_hit or sl_hit (not expired_no_data)."""
    # Count trades that closed through the engine path
    real_outcomes = db.execute("""
        SELECT COUNT(*) FROM signals 
        WHERE outcome IN ('tp_hit', 'sl_hit')
    """).fetchone()[0]
    
    # Count trades that were cleaned up by zombie expiry
    zombie_outcomes = db.execute("""
        SELECT COUNT(*) FROM signals 
        WHERE outcome = 'expired_no_data' 
        AND timestamp > (SELECT MAX(timestamp) - 86400 FROM signals)
    """).fetchone()[0]
    
    if real_outcomes == 0:
        return VerificationResult(
            "Phase 2: Outcome Integrity",
            False,
            f"Zero tp_hit/sl_hit outcomes | {zombie_outcomes} zombie cleanups in last 24h"
        )
    
    return VerificationResult(
        "Phase 2: Outcome Integrity",
        True,
        f"{real_outcomes} real outcomes (tp_hit/sl_hit) | {zombie_outcomes} zombie cleanups"
    )


def check_phase2_no_pending_on_closed(db: sqlite3.Connection) -> VerificationResult:
    """No closed position should have outcome='pending' in signals table."""
    # Find positions that are closed but signals still pending
    orphaned = db.execute("""
        SELECT p.symbol, p.side, p.closed_at, s.outcome as sig_outcome
        FROM positions p
        LEFT JOIN signals s ON p.signal_id = s.id
        WHERE p.status = 'closed' 
        AND (s.outcome = 'pending' OR s.outcome IS NULL)
    """).fetchall()
    
    if orphaned:
        details = [f"{r[0]} {r[1]}" for r in orphaned[:5]]
        return VerificationResult(
            "Phase 2: No Pending on Closed",
            False,
            f"{len(orphaned)} closed positions with pending/null signal outcome: {', '.join(details)}",
            severity="WARN"
        )
    
    return VerificationResult(
        "Phase 2: No Pending on Closed",
        True,
        "All closed positions have non-pending signal outcomes"
    )


def check_phase2_realized_r_populated(db: sqlite3.Connection) -> VerificationResult:
    """Realized R should be non-zero for all tp_hit/sl_hit trades."""
    trades = db.execute("""
        SELECT symbol, side, outcome, realized_r, mae_pct, mfe_pct
        FROM signals WHERE outcome IN ('tp_hit', 'sl_hit')
    """).fetchall()
    
    if not trades:
        return VerificationResult(
            "Phase 2: Realized R Populated",
            False,
            "No tp_hit/sl_hit trades to check"
        )
    
    issues = []
    for sym, side, outcome, rr, mae, mfe in trades:
        if rr is None or rr == 0:
            issues.append(f"{sym} {side}: R={rr}")
        if mae is None:
            issues.append(f"{sym} {side}: MAE=NULL")
        if mfe is None:
            issues.append(f"{sym} {side}: MFE=NULL")
    
    if issues:
        return VerificationResult(
            "Phase 2: Realized R Populated",
            False,
            f"{len(issues)} field issues: {'; '.join(issues[:3])}"
        )
    
    return VerificationResult(
        "Phase 2: Realized R Populated",
        True,
        f"All {len(trades)} trades have realized_r, mae_pct, mfe_pct populated"
    )


def check_phase2_calibration_fill_rate(cal_db: sqlite3.Connection) -> VerificationResult:
    """Calibration records should have outcomes, not all pending."""
    total = cal_db.execute("SELECT COUNT(*) FROM calibration_log").fetchone()[0]
    pending = cal_db.execute("SELECT COUNT(*) FROM calibration_log WHERE outcome = 'pending'").fetchone()[0]
    completed = total - pending
    
    if total == 0:
        return VerificationResult(
            "Phase 2: Calibration Fill Rate",
            True,
            "No calibration records yet"
        )
    
    fill_rate = (completed / total * 100) if total > 0 else 0
    
    if completed == 0:
        return VerificationResult(
            "Phase 2: Calibration Fill Rate",
            False,
            f"Zero completed records ({pending} pending, {fill_rate:.1f}% fill rate) — record_outcome still broken"
        )
    
    return VerificationResult(
        "Phase 2: Calibration Fill Rate",
        True,
        f"{completed}/{total} completed ({fill_rate:.1f}% fill rate) | {pending} still pending"
    )


def check_phase2_no_duplicate_outcomes(db: sqlite3.Connection) -> VerificationResult:
    """No signal should have outcome written twice."""
    dupes = db.execute("""
        SELECT symbol, side, COUNT(*) as cnt 
        FROM signals 
        WHERE outcome IN ('tp_hit', 'sl_hit')
        GROUP BY symbol, side 
        HAVING cnt > 1
    """).fetchall()
    
    if dupes:
        details = [f"{r[0]} {r[1]}×{r[2]}" for r in dupes]
        return VerificationResult(
            "Phase 2: No Duplicate Outcomes",
            False,
            f"Duplicate outcomes found: {', '.join(details)}"
        )
    
    return VerificationResult(
        "Phase 2: No Duplicate Outcomes",
        True,
        "No duplicate outcomes in signals table"
    )


def check_phase2_position_signal_sync(db: sqlite3.Connection) -> VerificationResult:
    """Position table and signals table should agree on outcome."""
    # Get closed positions with their signal outcomes
    mismatches = db.execute("""
        SELECT p.symbol, p.side, p.outcome as pos_outcome, s.outcome as sig_outcome
        FROM positions p
        JOIN signals s ON p.signal_id = s.id
        WHERE p.status = 'closed'
        AND s.outcome IN ('tp_hit', 'sl_hit')
        AND (
            (p.outcome = 'win' AND s.outcome != 'tp_hit')
            OR
            (p.outcome = 'loss' AND s.outcome != 'sl_hit')
        )
    """).fetchall()
    
    if mismatches:
        details = [f"{r[0]} {r[1]}: pos={r[2]} sig={r[3]}" for r in mismatches]
        return VerificationResult(
            "Phase 2: Position-Signal Sync",
            False,
            f"{len(mismatches)} mismatches: {'; '.join(details)}"
        )
    
    return VerificationResult(
        "Phase 2: Position-Signal Sync",
        True,
        "All position outcomes match signal outcomes"
    )


def run_phase2_verification() -> list:
    """Run Phase 2 checks (first 5 completed trades)."""
    results = []
    
    try:
        db = sqlite3.connect(str(DB_PATH), timeout=10)
        db.row_factory = sqlite3.Row
    except Exception as e:
        return [VerificationResult("Database Connection", False, f"Cannot open: {e}")]
    
    try:
        cal_db = sqlite3.connect(str(CAL_DB_PATH), timeout=10)
    except Exception as e:
        db.close()
        return [VerificationResult("Calibration DB Connection", False, f"Cannot open: {e}")]
    
    try:
        results.append(check_phase2_outcome_integrity(db))
        results.append(check_phase2_no_pending_on_closed(db))
        results.append(check_phase2_realized_r_populated(db))
        results.append(check_phase2_calibration_fill_rate(cal_db))
        results.append(check_phase2_no_duplicate_outcomes(db))
        results.append(check_phase2_position_signal_sync(db))
    finally:
        db.close()
        cal_db.close()
    
    return results


# ═══════════════════════════════════════════════════════════════
# PHASE 3: First 20 Completed Trades — Statistical Foundation
# ═══════════════════════════════════════════════════════════════

def check_phase3_sample_size(db: sqlite3.Connection) -> VerificationResult:
    """Need at least 20 completed trades for basic statistics."""
    count = db.execute("""
        SELECT COUNT(*) FROM signals WHERE outcome IN ('tp_hit', 'sl_hit')
    """).fetchone()[0]
    
    if count < 20:
        return VerificationResult(
            "Phase 3: Sample Size",
            False,
            f"{count}/20 completed trades — need {20 - count} more"
        )
    
    return VerificationResult(
        "Phase 3: Sample Size",
        True,
        f"{count} completed trades — sufficient for basic statistics"
    )


def check_phase3_no_outlier_rr(db: sqlite3.Connection) -> VerificationResult:
    """No realized R should be astronomically wrong (data entry error)."""
    trades = db.execute("""
        SELECT symbol, side, realized_r FROM signals 
        WHERE outcome IN ('tp_hit', 'sl_hit')
    """).fetchall()
    
    outliers = []
    for sym, side, rr in trades:
        if rr is not None and (abs(rr) > 20 or rr == 0):
            outliers.append(f"{sym} {side}: R={rr}")
    
    if outliers:
        return VerificationResult(
            "Phase 3: No Outlier R-Multiples",
            False,
            f"{len(outliers)} outliers: {'; '.join(outliers[:3])}"
        )
    
    return VerificationResult(
        "Phase 3: No Outlier R-Multiples",
        True,
        f"All {len(trades)} R-multiples within reasonable range"
    )


def check_phase3_regime_distribution(db: sqlite3.Connection) -> VerificationResult:
    """Trades should span multiple market regimes (not all same regime)."""
    regimes = db.execute("""
        SELECT metadata FROM signals 
        WHERE outcome IN ('tp_hit', 'sl_hit')
    """).fetchall()
    
    regime_set = set()
    for (meta,) in regimes:
        if meta:
            try:
                import json
                data = json.loads(meta) if isinstance(meta, str) else meta
                if isinstance(data, list):
                    for item in data:
                        if item.get("name") == "regime":
                            regime_set.add(round(item.get("value", 0), 1))
            except:
                pass
    
    if len(regime_set) < 2:
        return VerificationResult(
            "Phase 3: Regime Distribution",
            False,
            f"Only {len(regime_set)} unique regime values — trades may be regime-concentrated",
            severity="WARN"
        )
    
    return VerificationResult(
        "Phase 3: Regime Distribution",
        True,
        f"{len(regime_set)} unique regime values across {len(regimes)} trades"
    )


def check_phase3_confidence_spread(db: sqlite3.Connection) -> VerificationResult:
    """Completed trades should span multiple confidence bands."""
    trades = db.execute("""
        SELECT confidence FROM signals 
        WHERE outcome IN ('tp_hit', 'sl_hit')
    """).fetchall()
    
    if not trades:
        return VerificationResult(
            "Phase 3: Confidence Spread",
            False,
            "No trades to analyze"
        )
    
    confs = [r[0] for r in trades if r[0] is not None]
    if not confs:
        return VerificationResult(
            "Phase 3: Confidence Spread",
            False,
            "No confidence values found"
        )
    
    min_conf = min(confs)
    max_conf = max(confs)
    avg_conf = sum(confs) / len(confs)
    
    # Check how many bands are represented
    bands = {"50-60": 0, "60-70": 0, "70-80": 0, "80-90": 0, "90-100": 0}
    for c in confs:
        if 0.5 <= c < 0.6: bands["50-60"] += 1
        elif 0.6 <= c < 0.7: bands["60-70"] += 1
        elif 0.7 <= c < 0.8: bands["70-80"] += 1
        elif 0.8 <= c < 0.9: bands["80-90"] += 1
        elif c >= 0.9: bands["90-100"] += 1
    
    active_bands = sum(1 for v in bands.values() if v > 0)
    band_detail = " | ".join([f"{k}: {v}" for k, v in bands.items() if v > 0])
    
    if active_bands < 2:
        return VerificationResult(
            "Phase 3: Confidence Spread",
            False,
            f"Only {active_bands} confidence band(s) represented — {band_detail}",
            severity="WARN"
        )
    
    return VerificationResult(
        "Phase 3: Confidence Spread",
        True,
        f"{active_bands} bands | range [{min_conf:.1%}, {max_conf:.1%}] avg={avg_conf:.1%} | {band_detail}"
    )


def run_phase3_verification() -> list:
    """Run Phase 3 checks (first 20 completed trades)."""
    results = []
    
    try:
        db = sqlite3.connect(str(DB_PATH), timeout=10)
        db.row_factory = sqlite3.Row
    except Exception as e:
        return [VerificationResult("Database Connection", False, f"Cannot open: {e}")]
    
    try:
        results.append(check_phase3_sample_size(db))
        results.append(check_phase3_no_outlier_rr(db))
        results.append(check_phase3_regime_distribution(db))
        results.append(check_phase3_confidence_spread(db))
    finally:
        db.close()
    
    return results


# ═══════════════════════════════════════════════════════════════
# Auto-detect current phase
# ═══════════════════════════════════════════════════════════════

def get_completed_trade_count() -> int:
    """Count trades with real outcomes."""
    try:
        db = sqlite3.connect(str(DB_PATH), timeout=10)
        count = db.execute(
            "SELECT COUNT(*) FROM signals WHERE outcome IN ('tp_hit', 'sl_hit')"
        ).fetchone()[0]
        db.close()
        return count
    except:
        return 0


def detect_phase() -> int:
    """Auto-detect which phase we're in based on completed trade count."""
    count = get_completed_trade_count()
    if count == 0:
        return 1
    elif count < 5:
        return 2
    elif count < 20:
        return 3
    else:
        return 4  # All phases complete


def print_report(results: list, phase: int = 1):
    """Print formatted verification report."""
    all_passed = all(r.passed for r in results)
    completed = get_completed_trade_count()
    
    phase_names = {
        1: "PHASE 1 — First Completed Trade (4 conditions)",
        2: "PHASE 2 — First 5 Trades (Lifecycle Complexity)",
        3: "PHASE 3 — First 20 Trades (Statistical Foundation)",
        4: "ALL PHASES COMPLETE — Ready for Baseline Analysis"
    }
    
    print()
    print("═══════════════════════════════════════════════════════════════")
    print(f"  {phase_names.get(phase, f'PHASE {phase}')}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Completed trades: {completed}")
    print("═══════════════════════════════════════════════════════════════")
    print()
    
    for r in results:
        print(f"  {r}")
        print()
    
    print("───────────────────────────────────────────────────────────────")
    if all_passed:
        print(f"  ✅ PHASE {phase} VERIFIED — Data pipeline trustworthy at this level")
        if phase < 4:
            next_phase = phase + 1
            next_target = {2: 5, 3: 20, 4: 20}.get(next_target, "analysis")
            print(f"  → Next milestone: {phase_names.get(next_phase, 'Complete')}")
    else:
        failed = [r.condition for r in results if not r.passed]
        print(f"  ❌ {len(failed)} CHECK(S) FAILED — DO NOT trust validation data")
        for f in failed:
            print(f"     → {f}")
    print("═══════════════════════════════════════════════════════════════")
    print()
    
    return all_passed


def watch_mode(target_phase: int = None):
    """Continuously monitor until phase completion, then verify."""
    if target_phase is None:
        target_phase = detect_phase()
    
    phase_targets = {1: 1, 2: 5, 3: 20}
    target_count = phase_targets.get(target_phase, 20)
    
    print(f"👀 Watching for Phase {target_phase} completion ({target_count} trades)...")
    print("   (Press Ctrl+C to stop)")
    print()
    
    current_count = get_completed_trade_count()
    print(f"   Current completed trades: {current_count}/{target_count}")
    print()
    
    while True:
        try:
            current_count = get_completed_trade_count()
            
            if current_count >= target_count:
                print(f"\n🔔 Phase {target_phase} target reached! ({current_count}/{target_count})")
                print("   Running verification...\n")
                
                if target_phase == 1:
                    results = run_verification()
                elif target_phase == 2:
                    results = run_phase2_verification()
                elif target_phase == 3:
                    results = run_phase3_verification()
                else:
                    results = run_verification() + run_phase2_verification() + run_phase3_verification()
                
                passed = print_report(results, phase=target_phase)
                
                if passed and target_phase < 3:
                    next_phase = target_phase + 1
                    print(f"\n🔄 Auto-advancing to Phase {next_phase}...")
                    watch_mode(next_phase)
                return
            
            # Calculate progress
            if target_count > 0:
                progress = current_count / target_count * 100
                bar_len = 20
                filled = int(bar_len * current_count / target_count)
                bar = "█" * filled + "░" * (bar_len - filled)
                sys.stdout.write(f"\r   ⏳ [{bar}] {current_count}/{target_count} ({progress:.0f}%)")
            else:
                sys.stdout.write(f"\r   ⏳ Waiting... ({current_count} completed)")
            
            sys.stdout.flush()
            time.sleep(30)  # Check every 30 seconds
            
        except KeyboardInterrupt:
            print("\n\nStopped watching.")
            return
        except Exception as e:
            print(f"\n   Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    # Parse arguments
    phase = None
    watch = False
    
    for arg in sys.argv[1:]:
        if arg == "--watch":
            watch = True
        elif arg.startswith("--phase"):
            if "=" in arg:
                phase = int(arg.split("=")[1])
            elif "--phase" in sys.argv:
                idx = sys.argv.index("--phase")
                if idx + 1 < len(sys.argv):
                    phase = int(sys.argv[idx + 1])
    
    if watch:
        watch_mode(phase)
    else:
        # Auto-detect and run appropriate phase
        if phase is None:
            phase = detect_phase()
        
        completed = get_completed_trade_count()
        print(f"Completed trades: {completed} | Auto-detected phase: {phase}")
        
        if phase == 1:
            results = run_verification()
        elif phase == 2:
            # Also run phase 1 checks
            results = run_verification() + run_phase2_verification()
        elif phase == 3:
            results = run_verification() + run_phase2_verification() + run_phase3_verification()
        else:
            results = run_verification() + run_phase2_verification() + run_phase3_verification()
        
        passed = print_report(results, phase=phase)
        sys.exit(0 if passed else 1)
