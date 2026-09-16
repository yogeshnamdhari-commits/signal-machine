"""
Reconciliation Assertions — Automatic PASS/FAIL Audit Trail

Runs after each scan cycle (or on-demand) to verify operational correctness.
Any FAIL flags the scan immediately to prevent collecting corrupted statistics.

Three layers remain separate:
  1. Operational correctness — universe, state accounting, dashboard-DB alignment
  2. Behavioral diagnostics — pipeline conversion, candidate journey, rejection reasons
  3. Performance validation — PF, expectancy, win rate, MAE/MFE, drawdown, regime

This module covers Layer 1 ONLY. Layers 2 and 3 are separate modules.

Usage:
    from scanner.reconciliation_assertions import run_reconciliation
    results = run_reconciliation(db_path, scan_stats)
    for r in results:
        print(r)
"""

import sqlite3
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AssertionResult:
    """Single reconciliation assertion result."""
    name: str
    passed: bool
    detail: str
    severity: str = "FAIL"  # FAIL = must block, WARN = investigate

    def __str__(self) -> str:
        icon = "✓" if self.passed else "✗"
        label = "PASS" if self.passed else self.severity
        return f"[{label}] {icon} {self.name}: {self.detail}"


@dataclass
class ReconciliationReport:
    """Full reconciliation report for a scan cycle."""
    timestamp: float = field(default_factory=time.time)
    results: list = field(default_factory=list)
    scan_cycle_id: Optional[str] = None

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def warn_count(self) -> int:
        return sum(1 for r in self.results if not r.passed and r.severity == "WARN")

    def summary(self) -> str:
        lines = [
            f"═══ RECONCILIATION REPORT ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.timestamp))}) ═══",
            f"Total assertions: {len(self.results)} | Passed: {len(self.results) - self.fail_count} | Failed: {self.fail_count} | Warnings: {self.warn_count}",
            ""
        ]
        for r in self.results:
            lines.append(f"  {r}")
        lines.append("")
        if self.all_passed:
            lines.append("  RESULT: ALL CHECKS PASSED ✓")
        else:
            lines.append(f"  RESULT: {self.fail_count} CHECK(S) FAILED ✗ — SCAN DATA MAY BE CORRUPTED")
        return "\n".join(lines)


def run_reconciliation(
    db_path: str = "data/institutional_v1.db",
    scan_stats: Optional[dict] = None,
    max_symbols: int = 250,
    min_volume_24h: float = 2_000_000,
) -> ReconciliationReport:
    """
    Run all reconciliation assertions against the database.
    
    Args:
        db_path: Path to institutional_v1.db
        scan_stats: Optional dict from scanner with pipeline counts
                    Expected keys: candidates, rejected_regime, rejected_fast,
                    rejected_pullback, rejected_candle, rejected_volume,
                    rejected_active, signals_emitted
        max_symbols: Configured max symbols from settings
        min_volume_24h: Configured minimum volume from settings
    
    Returns:
        ReconciliationReport with PASS/FAIL for each assertion
    """
    report = ReconciliationReport()
    
    try:
        db = sqlite3.connect(str(Path(db_path)), timeout=10)
        db.row_factory = sqlite3.Row
    except Exception as e:
        report.results.append(AssertionResult(
            name="Database Connection",
            passed=False,
            detail=f"Cannot connect to {db_path}: {e}",
            severity="FAIL"
        ))
        return report

    try:
        # ════════════════════════════════════════════════════════════════
        # ASSERTION 1: Universe Consistency
        # Configured universe should match symbols in DB
        # ════════════════════════════════════════════════════════════════
        try:
            total_symbols = db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            active_symbols = db.execute("SELECT COUNT(*) FROM symbols WHERE is_active = 1").fetchone()[0]
            # Allow some tolerance — universe may not be exactly max_symbols
            # but should be within a reasonable range
            if active_symbols == 0:
                report.results.append(AssertionResult(
                    name="Universe Consistency",
                    passed=False,
                    detail=f"Active symbols=0 — no symbols to scan",
                    severity="FAIL"
                ))
            elif active_symbols < max_symbols * 0.5:
                report.results.append(AssertionResult(
                    name="Universe Consistency",
                    passed=False,
                    detail=f"Active symbols={active_symbols} (expected ~{max_symbols}) — universe critically small",
                    severity="FAIL"
                ))
            else:
                report.results.append(AssertionResult(
                    name="Universe Consistency",
                    passed=True,
                    detail=f"Active symbols={active_symbols}, total={total_symbols}"
                ))
        except Exception as e:
            report.results.append(AssertionResult(
                name="Universe Consistency",
                passed=False,
                detail=f"Error checking universe: {e}",
                severity="FAIL"
            ))

        # ════════════════════════════════════════════════════════════════
        # ASSERTION 2: Signal State Completeness
        # Every signal must be in exactly one terminal state
        # ════════════════════════════════════════════════════════════════
        try:
            total_signals = db.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            active_signals = db.execute("SELECT COUNT(*) FROM signals WHERE status = 'active'").fetchone()[0]
            expired_signals = db.execute("SELECT COUNT(*) FROM signals WHERE status = 'expired'").fetchone()[0]
            states_sum = active_signals + expired_signals
            
            if states_sum != total_signals:
                other = total_signals - states_sum
                report.results.append(AssertionResult(
                    name="Signal State Completeness",
                    passed=False,
                    detail=f"Sum of states ({states_sum}) ≠ total ({total_signals}) — {other} signals in unknown state",
                    severity="FAIL"
                ))
            else:
                report.results.append(AssertionResult(
                    name="Signal State Completeness",
                    passed=True,
                    detail=f"active={active_signals}, expired={expired_signals}, total={total_signals}"
                ))
        except Exception as e:
            report.results.append(AssertionResult(
                name="Signal State Completeness",
                passed=False,
                detail=f"Error: {e}",
                severity="FAIL"
            ))

        # ════════════════════════════════════════════════════════════════
        # ASSERTION 3: Outcome Integrity
        # No expired signal should have outcome='pending'
        # This is the lifecycle bug check
        # ════════════════════════════════════════════════════════════════
        try:
            orphaned = db.execute(
                "SELECT COUNT(*) FROM signals WHERE status = 'expired' AND outcome = 'pending'"
            ).fetchone()[0]
            
            if orphaned > 0:
                report.results.append(AssertionResult(
                    name="Outcome Integrity",
                    passed=False,
                    detail=f"{orphaned} expired signals still have outcome='pending' — lifecycle bug",
                    severity="FAIL"
                ))
            else:
                report.results.append(AssertionResult(
                    name="Outcome Integrity",
                    passed=True,
                    detail="Zero expired signals with outcome='pending'"
                ))
        except Exception as e:
            report.results.append(AssertionResult(
                name="Outcome Integrity",
                passed=False,
                detail=f"Error: {e}",
                severity="FAIL"
            ))

        # ════════════════════════════════════════════════════════════════
        # ASSERTION 4: Position-Signal Linkage
        # Every open position must have a corresponding active signal
        # ════════════════════════════════════════════════════════════════
        try:
            open_positions = db.execute(
                "SELECT p.id, p.symbol, p.signal_id FROM positions p WHERE p.status = 'open'"
            ).fetchall()
            
            orphaned_positions = 0
            details = []
            for pos in open_positions:
                if pos["signal_id"]:
                    sig_active = db.execute(
                        "SELECT COUNT(*) FROM signals WHERE id = ? AND status = 'active'",
                        (pos["signal_id"],)
                    ).fetchone()[0]
                    if sig_active == 0:
                        orphaned_positions += 1
                        details.append(f"{pos['symbol']}(sig={pos['signal_id']})")
                else:
                    orphaned_positions += 1
                    details.append(f"{pos['symbol']}(no signal_id)")
            
            if orphaned_positions > 0:
                report.results.append(AssertionResult(
                    name="Position-Signal Linkage",
                    passed=False,
                    detail=f"{orphaned_positions} open position(s) without active signal: {', '.join(details)}",
                    severity="WARN"
                ))
            else:
                report.results.append(AssertionResult(
                    name="Position-Signal Linkage",
                    passed=True,
                    detail=f"All {len(open_positions)} open positions linked to active signals"
                ))
        except Exception as e:
            report.results.append(AssertionResult(
                name="Position-Signal Linkage",
                passed=False,
                detail=f"Error: {e}",
                severity="FAIL"
            ))

        # ════════════════════════════════════════════════════════════════
        # ASSERTION 5: Active Signal-Position Consistency
        # Every active signal should have a corresponding open position
        # ════════════════════════════════════════════════════════════════
        try:
            active_sigs = db.execute(
                "SELECT id, symbol FROM signals WHERE status = 'active'"
            ).fetchall()
            
            orphaned_signals = 0
            for sig in active_sigs:
                pos_count = db.execute(
                    "SELECT COUNT(*) FROM positions WHERE signal_id = ? AND status = 'open'",
                    (sig["id"],)
                ).fetchone()[0]
                if pos_count == 0:
                    orphaned_signals += 1
            
            if orphaned_signals > 0:
                report.results.append(AssertionResult(
                    name="Active Signal-Position Consistency",
                    passed=False,
                    detail=f"{orphaned_signals} active signals without open position — may be pre-execution or zombie",
                    severity="WARN"
                ))
            else:
                report.results.append(AssertionResult(
                    name="Active Signal-Position Consistency",
                    passed=True,
                    detail=f"All {len(active_sigs)} active signals have open positions"
                ))
        except Exception as e:
            report.results.append(AssertionResult(
                name="Active Signal-Position Consistency",
                passed=False,
                detail=f"Error: {e}",
                severity="FAIL"
            ))

        # ════════════════════════════════════════════════════════════════
        # ASSERTION 6: Confidence Scale Consistency
        # All confidence values should be in [0.0, 1.0] range
        # ════════════════════════════════════════════════════════════════
        try:
            min_conf = db.execute("SELECT MIN(confidence) FROM signals").fetchone()[0]
            max_conf = db.execute("SELECT MAX(confidence) FROM signals").fetchone()[0]
            
            if min_conf is not None and max_conf is not None:
                if min_conf < 0 or max_conf > 1.0:
                    report.results.append(AssertionResult(
                        name="Confidence Scale Consistency",
                        passed=False,
                        detail=f"Confidence range [{min_conf}, {max_conf}] outside [0, 1] — scale mismatch",
                        severity="FAIL"
                    ))
                else:
                    report.results.append(AssertionResult(
                        name="Confidence Scale Consistency",
                        passed=True,
                        detail=f"Confidence range [{min_conf:.4f}, {max_conf:.4f}] within [0, 1]"
                    ))
            else:
                report.results.append(AssertionResult(
                    name="Confidence Scale Consistency",
                    passed=True,
                    detail="No signals to check"
                ))
        except Exception as e:
            report.results.append(AssertionResult(
                name="Confidence Scale Consistency",
                passed=False,
                detail=f"Error: {e}",
                severity="FAIL"
            ))

        # ════════════════════════════════════════════════════════════════
        # ASSERTION 7: Duplicate Signal Check
        # No two active signals should reference the same symbol+side
        # ════════════════════════════════════════════════════════════════
        try:
            dupes = db.execute(
                "SELECT symbol, side, COUNT(*) as cnt FROM signals "
                "WHERE status = 'active' GROUP BY symbol, side HAVING cnt > 1"
            ).fetchall()
            
            if dupes:
                dupe_detail = ", ".join([f"{d['symbol']} {d['side']}×{d['cnt']}" for d in dupes])
                report.results.append(AssertionResult(
                    name="Duplicate Signal Check",
                    passed=False,
                    detail=f"Duplicate active signals: {dupe_detail}",
                    severity="FAIL"
                ))
            else:
                report.results.append(AssertionResult(
                    name="Duplicate Signal Check",
                    passed=True,
                    detail="No duplicate active signals"
                ))
        except Exception as e:
            report.results.append(AssertionResult(
                name="Duplicate Signal Check",
                passed=False,
                detail=f"Error: {e}",
                severity="FAIL"
            ))

        # ════════════════════════════════════════════════════════════════
        # ASSERTION 8: Pipeline Reconciliation (if scan_stats provided)
        # Candidates created = Accepted + Rejected
        # ════════════════════════════════════════════════════════════════
        if scan_stats:
            try:
                candidates = scan_stats.get("candidates", 0)
                rejected_sum = (
                    scan_stats.get("rejected_regime", 0) +
                    scan_stats.get("rejected_fast", 0) +
                    scan_stats.get("rejected_pullback", 0) +
                    scan_stats.get("rejected_candle", 0) +
                    scan_stats.get("rejected_volume", 0) +
                    scan_stats.get("rejected_active", 0) +
                    scan_stats.get("rejected_dup", 0) +
                    scan_stats.get("rejected_cooldown", 0) +
                    scan_stats.get("rejected_entry_atr", 0) +
                    scan_stats.get("rejected_rr", 0)
                )
                signals_emitted = scan_stats.get("signals_emitted", 0)
                total_accounted = rejected_sum + signals_emitted
                
                if candidates > 0 and total_accounted != candidates:
                    diff = candidates - total_accounted
                    report.results.append(AssertionResult(
                        name="Pipeline Reconciliation",
                        passed=False,
                        detail=f"Candidates={candidates} but accounted={total_accounted} (diff={diff}) — {diff} candidates unaccounted",
                        severity="FAIL"
                    ))
                elif candidates > 0:
                    report.results.append(AssertionResult(
                        name="Pipeline Reconciliation",
                        passed=True,
                        detail=f"Candidates={candidates} = rejected({rejected_sum}) + signals({signals_emitted})"
                    ))
                else:
                    report.results.append(AssertionResult(
                        name="Pipeline Reconciliation",
                        passed=True,
                        detail="No scan stats to reconcile"
                    ))
            except Exception as e:
                report.results.append(AssertionResult(
                    name="Pipeline Reconciliation",
                    passed=False,
                    detail=f"Error: {e}",
                    severity="FAIL"
                ))
        else:
            report.results.append(AssertionResult(
                name="Pipeline Reconciliation",
                passed=True,
                detail="Skipped — no scan_stats provided"
            ))

        # ════════════════════════════════════════════════════════════════
        # ASSERTION 9: Stale Signal Check
        # No active signal should be older than signal_cooldown (default 24h)
        # ════════════════════════════════════════════════════════════════
        try:
            cutoff_24h = time.time() - 86400
            stale = db.execute(
                "SELECT COUNT(*) FROM signals WHERE status = 'active' AND timestamp < ?",
                (cutoff_24h,)
            ).fetchone()[0]
            
            if stale > 0:
                report.results.append(AssertionResult(
                    name="Stale Signal Check",
                    passed=False,
                    detail=f"{stale} active signal(s) older than 24h — potential zombie",
                    severity="WARN"
                ))
            else:
                report.results.append(AssertionResult(
                    name="Stale Signal Check",
                    passed=True,
                    detail="No stale active signals (>24h)"
                ))
        except Exception as e:
            report.results.append(AssertionResult(
                name="Stale Signal Check",
                passed=False,
                detail=f"Error: {e}",
                severity="FAIL"
            ))

        # ════════════════════════════════════════════════════════════════
        # ASSERTION 10: Outcome Field Completeness
        # Closed positions should have outcome, realized_r, mae_pct, mfe_pct
        # ════════════════════════════════════════════════════════════════
        try:
            # Check positions table — closed trades should have outcomes
            closed_no_outcome = db.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'closed' "
                "AND (outcome IS NULL OR outcome = '')"
            ).fetchone()[0]
            
            total_closed = db.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'closed'"
            ).fetchone()[0]
            
            if closed_no_outcome > 0:
                report.results.append(AssertionResult(
                    name="Outcome Field Completeness",
                    passed=False,
                    detail=f"{closed_no_outcome}/{total_closed} closed positions missing outcome",
                    severity="WARN"
                ))
            else:
                report.results.append(AssertionResult(
                    name="Outcome Field Completeness",
                    passed=True,
                    detail=f"All {total_closed} closed positions have outcome"
                ))
        except Exception as e:
            report.results.append(AssertionResult(
                name="Outcome Field Completeness",
                passed=False,
                detail=f"Error: {e}",
                severity="FAIL"
            ))

    except Exception as e:
        report.results.append(AssertionResult(
            name="Reconciliation Engine",
            passed=False,
            detail=f"Unexpected error during reconciliation: {e}",
            severity="FAIL"
        ))
    finally:
        db.close()

    return report


def run_quick_check(db_path: str = "data/institutional_v1.db") -> bool:
    """
    Quick boolean check — does the database pass all critical assertions?
    Use this for gate checks before writing scan data.
    """
    report = run_reconciliation(db_path)
    return report.all_passed


if __name__ == "__main__":
    import sys
    
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/institutional_v1.db"
    report = run_reconciliation(db_path)
    print(report.summary())
    
    if not report.all_passed:
        sys.exit(1)
