"""
Pipeline Integrity Assertions — Automated Accounting Identity Verification.

Converts every accounting identity into a live assertion that runs after each
scan cycle. If any identity breaks, the dashboard immediately shows:

    ❌ Pipeline Integrity FAILED

Identities enforced:
    1. EMA Cross Up  ≥ BUY_MODE Created
    2. EMA Cross Down ≥ SELL_MODE Created
    3. BUY_MODE Created = Pullback Entered + Regime Lost + Expired
    4. Pullback Entered = Candle Evaluated + Regime Lost + Timeout
    5. Candle Evaluated = Candle Passed + Candle Rejected
    6. Candle Passed = Signal Emitted + Suppressed + Cancelled

This module is PURELY OBSERVATIONAL. It does not modify any trading logic.

Usage:
    from scanner.ema_v5.pipeline_integrity import PipelineIntegrity

    integrity = PipelineIntegrity()
    # After each scan cycle:
    result = integrity.evaluate(counters)
    if not result.all_passed:
        logger.error("❌ Pipeline Integrity FAILED\\n{}", result.summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger


# ══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class IdentityResult:
    """Result of a single accounting identity check."""
    name: str
    passed: bool
    left_label: str
    left_value: int
    right_label: str
    right_value: int
    operator: str  # ">=", "=="
    detail: str = ""
    severity: str = "critical"  # critical | high | medium | low
    expected: int = 0           # The expected count (left side for ==, left for >=)
    observed: int = 0           # The observed count (right side for ==, right for >=)
    missing: int = 0            # Difference when failed (expected - observed for leak, etc.)

    def __str__(self) -> str:
        icon = "✅" if self.passed else "❌"
        sev_tag = f" [{self.severity.upper()}]" if not self.passed else ""
        return (
            f"{icon} {self.name}: "
            f"{self.left_label}={self.left_value} {self.operator} "
            f"{self.right_label}={self.right_value}"
            + (f" ({self.detail})" if self.detail else "")
            + sev_tag
        )

    def failure_detail(self) -> str:
        """Detailed failure report with expected/observed/missing."""
        if self.passed:
            return f"✅ {self.name}: PASSED"
        diff = abs(self.expected - self.observed)
        rate = (diff / max(self.expected, 1)) * 100
        return (
            f"❌ {self.name} [{self.severity.upper()}]\n"
            f"   Expected: {self.expected}\n"
            f"   Observed: {self.observed}\n"
            f"   Missing:  {diff}\n"
            f"   Failure rate: {rate:.2f}%"
        )


@dataclass
class IntegrityReport:
    """Full pipeline integrity report for a scan cycle."""
    timestamp: float = field(default_factory=time.time)
    results: List[IdentityResult] = field(default_factory=list)
    score: int = 0
    total: int = 0
    weighted_score: float = 1.0  # 0.0 to 1.0, severity-weighted

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def integrity_pct(self) -> float:
        return (self.score / self.total * 100) if self.total > 0 else 100.0

    @property
    def weighted_pct(self) -> float:
        return round(self.weighted_score * 100, 1)

    def summary(self) -> str:
        lines = [
            "═══════════════════════════════════════════════════",
            "  PIPELINE INTEGRITY SCORE",
            f"  {self.score} / {self.total} ({self.integrity_pct:.0f}%)",
            f"  Weighted: {self.weighted_pct:.1f}%",
            "═══════════════════════════════════════════════════",
        ]
        for r in self.results:
            lines.append(f"  {r}")
        lines.append("")
        if self.all_passed:
            lines.append("  ✅ Pipeline Integrity: ALL IDENTITIES SATISFIED")
        else:
            lines.append(f"  ❌ Pipeline Integrity: FAILED — {self.fail_count} IDENTITY(S) BROKEN")
            # Detailed failure breakdown
            lines.append("")
            lines.append("  FAILURE DETAILS:")
            for r in self.results:
                if not r.passed:
                    lines.append(f"  {r.failure_detail()}")
        lines.append("═══════════════════════════════════════════════════")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        """Serialize for dashboard/bridge export."""
        return {
            "timestamp": self.timestamp,
            "score": self.score,
            "total": self.total,
            "integrity_pct": round(self.integrity_pct, 1),
            "weighted_score": round(self.weighted_score, 3),
            "weighted_pct": self.weighted_pct,
            "all_passed": self.all_passed,
            "fail_count": self.fail_count,
            "identities": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "severity": r.severity,
                    "left": {"label": r.left_label, "value": r.left_value},
                    "right": {"label": r.right_label, "value": r.right_value},
                    "operator": r.operator,
                    "detail": r.detail,
                    "expected": r.expected,
                    "observed": r.observed,
                    "missing": r.missing,
                    "failure_detail": r.failure_detail() if not r.passed else None,
                }
                for r in self.results
            ],
        }


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE INTEGRITY ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class PipelineIntegrity:
    """
    Evaluates accounting identities after each scan cycle.

    Expected counters dict keys (all integers, cumulative since engine start):

        EMA crossover events:
            cross_up              — EMA20 crossed above EMA50
            cross_down            — EMA20 crossed below EMA50

        State creation:
            buy_mode_created      — BUY_MODE regime classified
            sell_mode_created     — SELL_MODE regime classified

        BUY_MODE lifecycle (Identity 3):
            pullback_entered      — BUY_MODE candidates that reached WAITING_PULLBACK
            regime_lost_pre       — BUY_MODE candidates that lost regime before pullback
            expired_pre           — BUY_MODE candidates that expired before pullback (policy-driven)
            buy_mode_live         — BUY_MODE candidates still live (not yet terminal)

        Pullback lifecycle (Identity 4):
            candle_evaluated      — Pullback candidates that reached candle stage
            regime_lost_in_pull   — Pullback candidates that lost regime during wait
            timeout_in_pull       — Pullback candidates that timed out during wait (policy-driven)
            waiting_pullback_live — WAITING_PULLBACK candidates still live (not yet terminal)

        Candle lifecycle (Identity 5):
            candle_passed         — Candle pattern found
            candle_rejected       — Candle pattern not found

        Signal lifecycle (Identity 6):
            signal_emitted        — Signals successfully generated
            signal_suppressed     — Confidence passed but signal_engine rejected
            signal_cancelled      — Post-confidence gate rejections (volume, confidence)

        State manager consistency (Identity 7):
            buy_mode_live         — Live BUY_MODE count
            sell_mode_live        — Live SELL_MODE count
            waiting_pullback_live — Live WAITING_PULLBACK count
            state_violations      — Symbols with unknown states or count mismatches
            Note: Exclusivity is guaranteed by construction (dict keyed by symbol),
            so this identity verifies state validity and count consistency.

    Note: expired_pre and timeout_in_pull are policy-driven counters.
    They should only be incremented when an explicit expiration policy is defined
    (e.g., maximum candles, session end, ATR-based timeout).
    They are NOT incremented by arbitrary time-based timeouts.

    Architectural Contract:
        StateManager is the sole authority for lifecycle state transitions.
        Direct mutation of lifecycle state outside this component is prohibited.
        This ensures exclusivity is guaranteed by construction (dict keyed by symbol),
        and Identity 7 (State Manager Consistency) verifies internal coherence.
    """

    # Floating-point tolerance for integer comparisons (handles floating-point drift)
    _EPSILON = 0.01

    # ── Severity weights: critical=1.0, high=0.75, medium=0.5, low=0.25 ──
    # A critical failure drops weighted score to 0; low barely moves it.
    SEVERITY_WEIGHTS = {
        "critical": 1.0,
        "high": 0.75,
        "medium": 0.5,
        "low": 0.25,
    }

    # ── Identity severity classification ──
    IDENTITY_SEVERITY = {
        "cross_up_vs_buy": "high",       # Regime creation failure
        "cross_down_vs_sell": "high",    # Regime creation failure
        "buy_mode_accounting": "critical",  # Candidate leak from BUY_MODE
        "pullback_accounting": "high",   # Lost pullback candidates
        "candle_accounting": "critical",   # Missing candle outcomes
        "signal_accounting": "critical",   # Signals disappearing after confirmation
    }

    def __init__(self, max_history: int = 500) -> None:
        self._last_report: Optional[IntegrityReport] = None
        self._consecutive_failures: int = 0
        self._total_evaluations: int = 0
        self._total_failures: int = 0
        self._failure_history: List[Dict] = []
        self._max_history = 100
        # ── Score history: time-series of (timestamp, raw_score%, weighted_score%) ──
        self._score_history: List[Dict] = []
        self._max_score_history = max_history
        # ── Per-identity failure counts ──
        self._identity_fail_counts: Dict[str, int] = {}

    def evaluate(self, counters: Dict[str, int]) -> IntegrityReport:
        """
        Evaluate all accounting identities against the provided counters.

        Args:
            counters: Dict with all required counter keys (see class docstring).

        Returns:
            IntegrityReport with pass/fail for each identity.
        """
        self._total_evaluations += 1
        report = IntegrityReport()

        # ── Extract counters with defaults ──
        def _c(key: str) -> int:
            return int(counters.get(key, 0))

        cross_up = _c("cross_up")
        cross_down = _c("cross_down")
        buy_mode_created = _c("buy_mode_created")
        sell_mode_created = _c("sell_mode_created")
        buy_mode_restored = _c("buy_mode_restored")
        sell_mode_restored = _c("sell_mode_restored")
        waiting_pullback_restored = _c("waiting_pullback_restored")
        pullback_entered = _c("pullback_entered")
        regime_lost_pre = _c("regime_lost_pre")
        expired_pre = _c("expired_pre")
        candle_evaluated = _c("candle_evaluated")
        regime_lost_in_pull = _c("regime_lost_in_pull")
        timeout_in_pull = _c("timeout_in_pull")
        candle_passed = _c("candle_passed")
        candle_rejected = _c("candle_rejected")
        signal_emitted = _c("signal_emitted")
        signal_suppressed = _c("signal_suppressed")
        signal_cancelled = _c("signal_cancelled")
        # ── In-flight counters (live state snapshot, not accumulated) ──
        buy_mode_live = _c("buy_mode_live")
        sell_mode_live = _c("sell_mode_live")
        waiting_pullback_live = _c("waiting_pullback_live")

        # ══════════════════════════════════════════════════════════════════
        # IDENTITY 1: EMA Cross Up ≥ BUY_MODE Created
        #
        # Every BUY_MODE creation requires a prior EMA20>50 cross.
        # Cross_up may be > buy_mode_created because some crosses
        # may be invalidated by ranging market detection.
        # ══════════════════════════════════════════════════════════════════
        id1 = self._check_ge(
            "EMA Cross accounting",
            "cross_up", cross_up,
            "buy_mode_created", buy_mode_created,
            severity=self.IDENTITY_SEVERITY["cross_up_vs_buy"],
            identity_key="cross_up_vs_buy",
        )
        report.results.append(id1)

        # ══════════════════════════════════════════════════════════════════
        # IDENTITY 2: EMA Cross Down ≥ SELL_MODE Created
        # ══════════════════════════════════════════════════════════════════
        id2 = self._check_ge(
            "EMA Cross accounting",
            "cross_down", cross_down,
            "sell_mode_created", sell_mode_created,
            severity=self.IDENTITY_SEVERITY["cross_down_vs_sell"],
            identity_key="cross_down_vs_sell",
        )
        report.results.append(id2)

        # ══════════════════════════════════════════════════════════════════
        # IDENTITY 3: BUY_MODE Created = Pullback + RegimeLost + Expired + Live
        #
        # Every BUY_MODE candidate must have an explicit destination:
        #   - Entered pullback (WAITING_PULLBACK)
        #   - Lost regime before pullback (→ NO_TREND)
        #   - Expired before pullback (policy-driven, currently 0)
        #   - Still live in BUY_MODE (in-flight, not yet terminal)
        # ══════════════════════════════════════════════════════════════════
        buy_mode_accounted = (
            pullback_entered + regime_lost_pre + expired_pre + buy_mode_live
        )
        buy_mode_sources = buy_mode_created + buy_mode_restored
        id3 = self._check_eq(
            "BUY_MODE accounting",
            "buy_mode_created+restored", buy_mode_sources,
            "pullback+regime_lost+expired+live", buy_mode_accounted,
            severity=self.IDENTITY_SEVERITY["buy_mode_accounting"],
            identity_key="buy_mode_accounting",
        )
        report.results.append(id3)

        # ══════════════════════════════════════════════════════════════════
        # IDENTITY 4: Pullback Entered = Candle + RegimeLost + Timeout + Live
        #
        # Every pullback candidate must have an explicit destination:
        #   - Evaluated by candle engine
        #   - Lost regime during pullback wait (→ NO_TREND)
        #   - Timed out waiting for candle confirmation (policy-driven)
        #   - Still live in WAITING_PULLBACK (in-flight, not yet terminal)
        # ══════════════════════════════════════════════════════════════════
        pullback_accounted = (
            candle_evaluated + regime_lost_in_pull + timeout_in_pull + waiting_pullback_live
        )
        pullback_sources = pullback_entered + waiting_pullback_restored
        id4 = self._check_eq(
            "Pullback accounting",
            "pullback_entered+restored", pullback_sources,
            "candle+regime_lost+timeout+live", pullback_accounted,
            severity=self.IDENTITY_SEVERITY["pullback_accounting"],
            identity_key="pullback_accounting",
        )
        report.results.append(id4)

        # ══════════════════════════════════════════════════════════════════
        # IDENTITY 5: Candle Evaluated = Candle Passed + Candle Rejected
        #
        # Every candle evaluation must have exactly one outcome.
        # ══════════════════════════════════════════════════════════════════
        candle_accounted = candle_passed + candle_rejected
        id5 = self._check_eq(
            "Candle accounting",
            "candle_evaluated", candle_evaluated,
            "candle_passed+rejected", candle_accounted,
            severity=self.IDENTITY_SEVERITY["candle_accounting"],
            identity_key="candle_accounting",
        )
        report.results.append(id5)

        # ══════════════════════════════════════════════════════════════════
        # IDENTITY 6: Candle Passed = Signal Emitted + Suppressed + Cancelled
        #
        # Every candle that passes must have an explicit destination:
        #   - Signal emitted (signal_engine generated a signal)
        #   - Suppressed (confidence passed but signal_engine rejected)
        #   - Cancelled (post-confidence gate rejection)
        # ══════════════════════════════════════════════════════════════════
        signal_accounted = signal_emitted + signal_suppressed + signal_cancelled
        id6 = self._check_eq(
            "Signal accounting",
            "candle_passed", candle_passed,
            "signal_emitted+suppressed+cancelled", signal_accounted,
            severity=self.IDENTITY_SEVERITY["signal_accounting"],
            identity_key="signal_accounting",
        )
        report.results.append(id6)

        # ══════════════════════════════════════════════════════════════════
        # IDENTITY 7: State Manager Consistency
        #
        # Because StateManager stores exactly one state per symbol in a
        # dict keyed by symbol, exclusivity is guaranteed by construction.
        # This identity verifies that:
        #   1. Every stored state is a known valid state
        #   2. Aggregated counts from iteration match get_state_counts()
        # This catches: unknown states, missing symbols, count drift.
        # ══════════════════════════════════════════════════════════════════
        state_violations = _c("state_violations")
        id7 = IdentityResult(
            name="State manager consistency",
            passed=(state_violations == 0),
            left_label="state_violations",
            left_value=state_violations,
            right_label="expected",
            right_value=0,
            operator="==",
            detail=f"buy_mode={buy_mode_live} sell_mode={sell_mode_live} pullback={waiting_pullback_live}",
            severity="critical",
            expected=0,
            observed=state_violations,
            missing=state_violations,
        )
        report.results.append(id7)

        # ══════════════════════════════════════════════════════════════════
        # COMPUTE SCORE (raw + weighted)
        # ══════════════════════════════════════════════════════════════════
        report.total = len(report.results)
        report.score = sum(1 for r in report.results if r.passed)

        # ── Weighted score: each identity contributes proportional to its severity ──
        total_weight = sum(self.SEVERITY_WEIGHTS.get(r.severity, 0.5) for r in report.results)
        earned_weight = sum(
            self.SEVERITY_WEIGHTS.get(r.severity, 0.5)
            for r in report.results if r.passed
        )
        report.weighted_score = earned_weight / total_weight if total_weight > 0 else 1.0

        # ── Record score history ──
        self._score_history.append({
            "timestamp": report.timestamp,
            "raw_pct": round(report.integrity_pct, 1),
            "weighted_pct": report.weighted_pct,
            "score": report.score,
            "total": report.total,
            "all_passed": report.all_passed,
        })
        if len(self._score_history) > self._max_score_history:
            self._score_history = self._score_history[-self._max_score_history:]

        # ── Track per-identity failure counts ──
        for r in report.results:
            if not r.passed:
                _key = r.name
                self._identity_fail_counts[_key] = self._identity_fail_counts.get(_key, 0) + 1

        # Track failure streaks
        if report.all_passed:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            self._total_failures += 1
            self._failure_history.append({
                "timestamp": report.timestamp,
                "fail_count": report.fail_count,
                "weighted_pct": report.weighted_pct,
                "broken": [
                    {
                        "name": r.name,
                        "severity": r.severity,
                        "expected": r.expected,
                        "observed": r.observed,
                        "missing": r.missing,
                    }
                    for r in report.results if not r.passed
                ],
            })
            if len(self._failure_history) > self._max_history:
                self._failure_history = self._failure_history[-self._max_history:]

        self._last_report = report

        # Log result
        if report.all_passed:
            logger.debug(
                "✅ PIPELINE_INTEGRITY: {}/{} identities satisfied (weighted: {:.1f}%)",
                report.score, report.total, report.weighted_pct,
            )
        else:
            logger.error(
                "❌ PIPELINE_INTEGRITY FAILED: {}/{} raw, {:.1f}% weighted — {} broken identities:\n{}",
                report.score, report.total, report.weighted_pct,
                report.fail_count, report.summary(),
            )

        return report

    def _check_ge(self, name: str, left_label: str, left: int,
                  right_label: str, right: int,
                  severity: str = "high", identity_key: str = "") -> IdentityResult:
        """Check left >= right (with epsilon tolerance)."""
        passed = left >= right - self._EPSILON
        detail = ""
        missing = 0
        if not passed:
            missing = right - left
            detail = f"deficit={missing}"
        return IdentityResult(
            name=name,
            passed=passed,
            left_label=left_label,
            left_value=left,
            right_label=right_label,
            right_value=right,
            operator=">=",
            detail=detail,
            severity=severity,
            expected=left,
            observed=right,
            missing=missing,
        )

    def _check_eq(self, name: str, left_label: str, left: int,
                  right_label: str, right: int,
                  severity: str = "high", identity_key: str = "") -> IdentityResult:
        """Check left == right (with epsilon tolerance)."""
        diff = abs(left - right)
        passed = diff <= self._EPSILON
        detail = ""
        missing = 0
        if not passed:
            if left > right:
                detail = f"leak={left - right}"
                missing = left - right
            else:
                detail = f"phantom={right - left}"
                missing = right - left
        return IdentityResult(
            name=name,
            passed=passed,
            left_label=left_label,
            left_value=left,
            right_label=right_label,
            right_value=right,
            operator="==",
            detail=detail,
            severity=severity,
            expected=left,
            observed=right,
            missing=missing,
        )

    # ── PUBLIC ACCESSORS ──────────────────────────────────────────────────

    @property
    def last_report(self) -> Optional[IntegrityReport]:
        return self._last_report

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def is_healthy(self) -> bool:
        """True if the last evaluation passed and no consecutive failures."""
        return (
            self._last_report is not None
            and self._last_report.all_passed
            and self._consecutive_failures == 0
        )

    def get_health_summary(self) -> Dict:
        """Return a health summary for dashboard/bridge export."""
        last = self._last_report
        # Build failed identities list with expected/observed/missing
        _failed = []
        if last:
            for r in last.results:
                if not r.passed:
                    _failed.append({
                        "name": r.name,
                        "severity": r.severity,
                        "expected": r.expected,
                        "observed": r.observed,
                        "missing": r.missing,
                        "detail": r.detail,
                        "failure_detail": r.failure_detail(),
                    })
        return {
            "integrity_score": f"{last.score}/{last.total}" if last else "N/A",
            "integrity_pct": round(last.integrity_pct, 1) if last else 100.0,
            "weighted_score": round(last.weighted_score, 3) if last else 1.0,
            "weighted_pct": last.weighted_pct if last else 100.0,
            "all_passed": last.all_passed if last else True,
            "consecutive_failures": self._consecutive_failures,
            "total_evaluations": self._total_evaluations,
            "total_failures": self._total_failures,
            "status": "✅ HEALTHY" if self.is_healthy else "❌ DEGRADED",
            "identities": last.to_dict()["identities"] if last else [],
            "failed_identities": _failed,
            "failure_stats": self.get_failure_stats(),
        }

    def get_score_history(self, n: int = 50) -> List[Dict]:
        """Return the last N integrity score entries for time-series display."""
        return self._score_history[-n:]

    def get_failure_stats(self) -> Dict[str, Dict]:
        """Return per-identity failure statistics.

        Returns a dict like:
            {
                "BUY_MODE accounting": {
                    "total_failures": 4,
                    "failure_rate_pct": 0.77,
                    "last_severity": "critical",
                },
                ...
            }
        """
        stats = {}
        for _key, count in self._identity_fail_counts.items():
            # Find the severity for this identity from the last report
            sev = "unknown"
            if self._last_report:
                for r in self._last_report.results:
                    if r.name == _key:
                        sev = r.severity
                        break
            rate = (count / max(self._total_evaluations, 1)) * 100
            stats[_key] = {
                "total_failures": count,
                "failure_rate_pct": round(rate, 2),
                "last_severity": sev,
            }
        return stats

    def format_dashboard(self) -> str:
        """Format a single-line dashboard status with weighted score."""
        last = self._last_report
        if not last:
            return "⏳ Pipeline Integrity: awaiting first evaluation"

        if last.all_passed:
            return f"✅ Pipeline Integrity: {last.score}/{last.total} (100%) weighted={last.weighted_pct:.1f}%"
        else:
            broken = [f"{r.name}[{r.severity.upper()}]" for r in last.results if not r.passed]
            return (
                f"❌ Pipeline Integrity FAILED: {last.score}/{last.total} "
                f"({last.integrity_pct:.0f}%) weighted={last.weighted_pct:.1f}% — "
                f"broken: {', '.join(broken)}"
            )

    def format_history_table(self, n: int = 20) -> str:
        """Format a human-readable time-series table of integrity scores."""
        history = self.get_score_history(n)
        if not history:
            return "  No history yet"

        import datetime
        lines = [
            "  TIME            RAW    WEIGHTED   STATUS",
            "  ──────────────  ─────  ────────   ──────",
        ]
        for entry in history:
            ts = datetime.datetime.fromtimestamp(entry["timestamp"]).strftime("%H:%M:%S")
            raw = f"{entry['raw_pct']:.0f}%"
            wtd = f"{entry['weighted_pct']:.1f}%"
            status = "✅" if entry["all_passed"] else "❌"
            lines.append(f"  {ts:<16} {raw:<6} {wtd:<9} {status}")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_pipeline_integrity: Optional[PipelineIntegrity] = None


def get_pipeline_integrity() -> PipelineIntegrity:
    """Get or create the global pipeline integrity instance."""
    global _pipeline_integrity
    if _pipeline_integrity is None:
        _pipeline_integrity = PipelineIntegrity()
    return _pipeline_integrity
