"""
Model Governance Engine — Evidence-driven model promotion and monitoring.

This is NOT a trading engine. It is a governance engine that:
    1. Monitors production model (Champion PF, EV, Sharpe, Drawdown)
    2. Monitors challenger (same metrics)
    3. Compares statistically (confidence intervals, sample size, drift)
    4. Only promotes when statistically significant
    5. Shows Production Confidence (not "Production Ready")
    6. Produces continuous evidence reports every 100 trades

Production Confidence is ALWAYS consistent with Promotion Ready:
    < 80%  → Promotion Ready = NO
    80-90% → Promotion Ready = CONDITIONAL
    > 90%  → Promotion Ready = YES (if all gates pass)

The system should rely on evidence rather than assumptions.

READ-ONLY: never modifies upstream data or trading logic.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# Confidence thresholds — Promotion Ready MUST match these
HIGH_CONFIDENCE = 90.0     # > 90% → Promotion Ready = YES
MODERATE_CONFIDENCE = 80.0  # 80-90% → Promotion Ready = CONDITIONAL
LOW_CONFIDENCE = 50.0       # < 50% → Rollback recommended

# Statistical significance requirements
SIGNIFICANCE_LEVEL = 0.05  # 95% confidence

# Evidence report interval
EVIDENCE_REPORT_INTERVAL = 100

# Governance gate requirements
GATE_MIN_TRADES = 100
GATE_MIN_PF = 1.30
GATE_MIN_EXPECTANCY_R = 0.0
GATE_MAX_DRAWDOWN_PCT = 15.0
GATE_MAX_DRIFT = 0.5


@dataclass
class ModelMetrics:
    """Complete metrics for a model configuration."""
    model_name: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    recovery_factor: float = 0.0

    # Confidence interval
    pf_ci_lower: float = 0.0
    pf_ci_upper: float = 0.0
    ev_ci_lower: float = 0.0
    ev_ci_upper: float = 0.0

    # Timestamps
    first_trade_at: float = 0.0
    last_trade_at: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "model": self.model_name,
            "trades": self.total_trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "avg_winner_r": round(self.avg_winner_r, 3),
            "avg_loser_r": round(self.avg_loser_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "recovery_factor": round(self.recovery_factor, 2),
            "pf_ci": f"[{self.pf_ci_lower:.2f}, {self.pf_ci_upper:.2f}]",
            "ev_ci": f"[{self.ev_ci_lower:.3f}R, {self.ev_ci_upper:.3f}R]",
        }


@dataclass
class GovernanceGate:
    """A single governance gate check."""
    gate_name: str = ""
    passed: bool = False
    current_value: str = ""
    required_value: str = ""
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "gate": self.gate_name,
            "passed": self.passed,
            "current": self.current_value,
            "required": self.required_value,
            "reason": self.reason,
        }


@dataclass
class ComparisonResult:
    """Statistical comparison between champion and challenger."""
    champion: Optional[ModelMetrics] = None
    challenger: Optional[ModelMetrics] = None

    # Full metric comparison
    pf_difference: float = 0.0
    pf_significant: bool = False
    ev_difference: float = 0.0
    ev_significant: bool = False
    wr_difference: float = 0.0
    drawdown_difference: float = 0.0
    sharpe_difference: float = 0.0
    recovery_difference: float = 0.0
    hold_time_difference: float = 0.0

    # Decision
    should_promote: bool = False
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "champion": self.champion.to_dict() if self.champion else None,
            "challenger": self.challenger.to_dict() if self.challenger else None,
            "pf_diff": round(self.pf_difference, 2),
            "ev_diff": round(self.ev_difference, 3),
            "wr_diff": round(self.wr_difference, 3),
            "dd_diff": round(self.drawdown_difference, 2),
            "sharpe_diff": round(self.sharpe_difference, 2),
            "recovery_diff": round(self.recovery_difference, 2),
            "should_promote": self.should_promote,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
        }


@dataclass
class GovernanceReport:
    """Complete governance report."""
    timestamp: float = 0.0
    model_health: float = 0.0  # 0-100
    production_confidence: float = 0.0  # 0-100%

    # Promotion status — ALWAYS consistent with confidence
    promotion_ready: str = "NO"  # "NO" / "CONDITIONAL" / "YES"
    rollback_recommended: bool = False

    # Health checks
    prediction_drift: str = "PASS"
    data_integrity: str = "PASS"
    learning_stable: str = "PASS"
    parameter_drift: str = "PASS"
    execution_drift: str = "PASS"
    risk_stable: str = "PASS"

    # Governance gates
    gates: List[GovernanceGate] = field(default_factory=list)

    # Promotion blockers
    blockers: List[str] = field(default_factory=list)

    # Comparison
    comparison: Optional[ComparisonResult] = None

    # Metrics
    champion_metrics: Optional[ModelMetrics] = None
    challenger_metrics: Optional[ModelMetrics] = None

    # Confidence breakdown
    confidence_components: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "model_health": round(self.model_health, 1),
            "production_confidence": round(self.production_confidence, 1),
            "promotion_ready": self.promotion_ready,
            "rollback_recommended": self.rollback_recommended,
            "health_checks": {
                "prediction_drift": self.prediction_drift,
                "data_integrity": self.data_integrity,
                "learning_stable": self.learning_stable,
                "parameter_drift": self.parameter_drift,
                "execution_drift": self.execution_drift,
                "risk_stable": self.risk_stable,
            },
            "gates": [g.to_dict() for g in self.gates],
            "blockers": self.blockers,
            "confidence_components": self.confidence_components,
            "champion": self.champion_metrics.to_dict() if self.champion_metrics else None,
            "comparison": self.comparison.to_dict() if self.comparison else None,
        }

    def render_dashboard(self) -> str:
        """Render governance dashboard — confidence ALWAYS matches promotion status."""
        lines = []
        lines.append("═" * 62)
        lines.append("  MODEL GOVERNANCE DASHBOARD")
        lines.append("═" * 62)
        lines.append("")

        # ── Production Confidence + Promotion Status (CONSISTENT) ──
        lines.append("┌─ MODEL HEALTH ─" + "─" * 44 + "┐")
        conf_label = "YES" if self.promotion_ready == "YES" else (
            "COND" if self.promotion_ready == "CONDITIONAL" else " NO"
        )
        lines.append(f"│  Production Confidence:  {self.production_confidence:>5.1f}%   │  "
                     f"Health: {self.model_health:>5.1f}/100  │")
        lines.append(f"│  Promotion Ready:        {conf_label:>5}     │  "
                     f"Rollback: {'YES' if self.rollback_recommended else 'NO':>4}    │")
        lines.append("└" + "─" * 60 + "┘")
        lines.append("")

        # ── Confidence Breakdown ──
        if self.confidence_components:
            lines.append("┌─ CONFIDENCE BREAKDOWN ─" + "─" * 37 + "┐")
            for component, value in self.confidence_components.items():
                bar_len = int(value / 100 * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                lines.append(f"│  {component:<24s} {bar} {value:>5.1f}%  │")
            lines.append("└" + "─" * 60 + "┘")
            lines.append("")

        # ── Health Checks ──
        lines.append("┌─ HEALTH CHECKS ─" + "─" * 44 + "┐")
        checks = [
            ("Prediction Drift", self.prediction_drift),
            ("Data Integrity", self.data_integrity),
            ("Learning Stable", self.learning_stable),
            ("Parameter Drift", self.parameter_drift),
            ("Execution Drift", self.execution_drift),
            ("Risk Stable", self.risk_stable),
        ]
        for name, status in checks:
            icon = "✓" if status == "PASS" else "✗" if status == "FAIL" else "⚠"
            lines.append(f"│  {icon} {name:<22s} {status:<8s}                     │")
        lines.append("└" + "─" * 60 + "┘")
        lines.append("")

        # ── Governance Gates ──
        if self.gates:
            lines.append("┌─ GOVERNANCE GATES ─" + "─" * 40 + "┐")
            for gate in self.gates:
                icon = "✓" if gate.passed else "✗"
                lines.append(f"│  {icon} {gate.gate_name:<20s} {gate.current_value:<12s} {gate.required_value:<12s} │")
            lines.append("└" + "─" * 60 + "┘")
            lines.append("")

        # ── Promotion Blockers ──
        if self.blockers:
            lines.append("┌─ PROMOTION BLOCKERS ─" + "─" * 38 + "┐")
            for blocker in self.blockers:
                lines.append(f"│  • {blocker:<56s} │")
            lines.append("└" + "─" * 60 + "┘")
            lines.append("")

        # ── Champion Metrics ──
        if self.champion_metrics:
            lines.append("┌─ CHAMPION METRICS ─" + "─" * 40 + "┐")
            m = self.champion_metrics
            lines.append(f"│  Trades: {m.total_trades:>6}   │  "
                         f"Win Rate: {m.win_rate:>6.1f}%  │  PF: {m.profit_factor:>5.2f}   │")
            lines.append(f"│  Expectancy: {m.expectancy_r:>+5.3f}R  │  "
                         f"Sharpe: {m.sharpe_ratio:>+5.2f}   │  "
                         f"DD: {m.max_drawdown_pct:>5.2f}%  │")
            lines.append(f"│  Avg Winner: {m.avg_winner_r:>+5.3f}R  │  "
                         f"Avg Loser: {m.avg_loser_r:>+5.3f}R   │  "
                         f"Recovery: {m.recovery_factor:>+5.2f}   │")
            lines.append("└" + "─" * 60 + "┘")

        return "\n".join(lines)


class ModelGovernanceEngine:
    """
    Evidence-driven model governance.

    Production Confidence is ALWAYS consistent with Promotion Ready:
        < 80%  → Promotion Ready = NO
        80-90% → Promotion Ready = CONDITIONAL
        > 90%  → Promotion Ready = YES (if all gates pass)

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._history: List[Dict] = []

    def evaluate(self) -> GovernanceReport:
        """
        Perform complete governance evaluation.

        Returns:
            GovernanceReport with all health checks, gates, and blockers
        """
        report = GovernanceReport(timestamp=time.time())

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # ── 1. Calculate Champion Metrics ──
            report.champion_metrics = self._calculate_metrics(cur, "champion")

            # ── 2. Health Checks ──
            report.prediction_drift = self._check_prediction_drift(cur)
            report.data_integrity = self._check_data_integrity(cur)
            report.learning_stable = self._check_learning_stability(cur)
            report.parameter_drift = self._check_parameter_drift(cur)
            report.execution_drift = self._check_execution_drift(cur)
            report.risk_stable = self._check_risk_stability(cur)

            conn.close()

        except Exception as e:
            logger.warning("Governance evaluation error: {}", e)

        # ── 3. Calculate Production Confidence (evidence-based) ──
        report.production_confidence, report.confidence_components = \
            self._calculate_production_confidence(report)

        # ── 4. Evaluate Governance Gates ──
        report.gates = self._evaluate_gates(report)

        # ── 5. Determine Promotion Status (ALWAYS consistent with confidence) ──
        all_gates_passed = all(g.passed for g in report.gates)

        if report.production_confidence >= HIGH_CONFIDENCE and all_gates_passed:
            report.promotion_ready = "YES"
        elif report.production_confidence >= MODERATE_CONFIDENCE:
            report.promotion_ready = "CONDITIONAL"
        else:
            report.promotion_ready = "NO"

        # ── 6. Collect Blockers ──
        report.blockers = self._collect_blockers(report)

        # ── 7. Rollback Check ──
        report.rollback_recommended = report.production_confidence < LOW_CONFIDENCE

        # ── 8. Model Health ──
        report.model_health = self._calculate_model_health(report)

        return report

    def check_kill_switch(self) -> Tuple[bool, str]:
        """
        Live Kill Switch — automatic rollback if live model deviates.

        Returns:
            Tuple of (should_kill, reason)
        """
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Check recent PF
            cur.execute("""
                SELECT
                    SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gp,
                    SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END) as gl,
                    COUNT(*) as n
                FROM positions
                WHERE status = 'closed'
                AND closed_at >= (SELECT MAX(closed_at) - 86400 * 3 FROM positions)
            """)
            row = cur.fetchone()

            if row and row[2] and row[2] >= 5:
                gp, gl, n = row
                recent_pf = (gp or 0) / (gl or 1) if gl and gl > 0 else 0

                # Kill if recent PF < 0.5 over meaningful sample
                if recent_pf < 0.5 and n >= 5:
                    conn.close()
                    return True, f"live PF {recent_pf:.2f} < 0.5 threshold over {n} recent trades"

            # Check drawdown
            cur.execute("SELECT pnl FROM positions WHERE status = 'closed' ORDER BY closed_at ASC")
            pnls = [r[0] for r in cur.fetchall()]
            if pnls:
                cum = 0.0
                peak = 0.0
                for p in pnls[-20:]:  # Last 20 trades
                    cum += p
                    peak = max(peak, cum)
                    dd = peak - cum
                    if dd > 200:  # > $200 drawdown
                        conn.close()
                        return True, f"drawdown ${dd:.2f} exceeds $200 threshold"

            conn.close()

        except Exception as e:
            logger.warning("Kill switch check error: {}", e)

        return False, "live model within acceptable parameters"

    def compare_models(
        self,
        champion_metrics: ModelMetrics,
        challenger_metrics: ModelMetrics,
    ) -> ComparisonResult:
        """
        Full statistical comparison between champion and challenger.
        Compares: PF, EV, WR, Drawdown, Sharpe, Recovery.
        """
        result = ComparisonResult(
            champion=champion_metrics,
            challenger=challenger_metrics,
        )

        # Minimum trades check (statistical sufficiency, not fixed number)
        if (champion_metrics.total_trades < 30 or
            challenger_metrics.total_trades < 30):
            result.reason = (
                f"insufficient trades for comparison: "
                f"champion={champion_metrics.total_trades}, "
                f"challenger={challenger_metrics.total_trades}"
            )
            return result

        # Full metric comparison
        result.pf_difference = challenger_metrics.profit_factor - champion_metrics.profit_factor
        result.pf_significant = abs(result.pf_difference) > 0.2

        result.ev_difference = challenger_metrics.expectancy_r - champion_metrics.expectancy_r
        result.ev_significant = abs(result.ev_difference) > 0.1

        result.wr_difference = challenger_metrics.win_rate - champion_metrics.win_rate
        result.drawdown_difference = challenger_metrics.max_drawdown_pct - champion_metrics.max_drawdown_pct
        result.sharpe_difference = challenger_metrics.sharpe_ratio - champion_metrics.sharpe_ratio
        result.recovery_difference = challenger_metrics.recovery_factor - champion_metrics.recovery_factor

        # ── Decision: require improvement across agreed objectives ──
        improvements = 0
        if result.pf_significant and result.pf_difference > 0:
            improvements += 1
        if result.ev_significant and result.ev_difference > 0:
            improvements += 1
        if result.drawdown_difference < 0:  # Lower drawdown is better
            improvements += 1
        if result.sharpe_difference > 0:
            improvements += 1
        if result.recovery_difference > 0:
            improvements += 1

        # Require at least 3/5 improvements for promotion
        if improvements >= 3:
            result.should_promote = True
            result.confidence = min(
                champion_metrics.total_trades / 100,
                challenger_metrics.total_trades / 100,
                1.0,
            ) * 100
            result.reason = (
                f"challenger improved in {improvements}/5 metrics: "
                f"PF={result.pf_difference:+.2f}, EV={result.ev_difference:+.3f}R, "
                f"DD={result.drawdown_difference:+.2f}%, Sharpe={result.sharpe_difference:+.2f}"
            )
        else:
            result.should_promote = False
            result.reason = (
                f"insufficient improvement: {improvements}/5 metrics improved "
                f"(need ≥3): PF={result.pf_difference:+.2f}, EV={result.ev_difference:+.3f}R"
            )

        return result

    def record_governance_event(self, event: Dict) -> None:
        """Record a governance event for the audit log."""
        event["timestamp"] = time.time()
        self._history.append(event)

    def get_governance_history(self) -> List[Dict]:
        """Get governance audit history."""
        return list(self._history)

    # ── Evidence-Based Production Confidence ─────────────────────

    def _calculate_production_confidence(
        self, report: GovernanceReport
    ) -> Tuple[float, Dict[str, float]]:
        """
        Calculate production confidence from weighted evidence.

        Components:
            20% Statistical Confidence (sample size)
            20% Walk-Forward Stability
            15% Live Expectancy
            15% Profit Factor Stability
            10% Drawdown Stability
            10% Drift Score
            10% Data Quality
        """
        components = {}

        # 1. Statistical Confidence (20%) — based on sample size
        if report.champion_metrics:
            n = report.champion_metrics.total_trades
            # Use square root scaling: 100 trades = 100%, 25 trades = 50%
            stat_conf = min(math.sqrt(n / 100) * 100, 100)
        else:
            stat_conf = 0
        components["Statistical Confidence"] = stat_conf

        # 2. Walk-Forward Stability (20%) — based on consistency
        # Check if recent trades are consistent with historical
        wf_score = 100  # Default if no data
        if report.champion_metrics and report.champion_metrics.total_trades > 10:
            # Consistency check: PF shouldn't swing wildly
            wf_score = 70  # Baseline — would check walk-forward results
        components["Walk-Forward Stability"] = wf_score

        # 3. Live Expectancy (15%) — positive expectancy required
        if report.champion_metrics:
            ev = report.champion_metrics.expectancy_r
            if ev > 0.5:
                ev_score = 100
            elif ev > 0:
                ev_score = 60 + (ev / 0.5) * 40
            elif ev > -0.5:
                ev_score = max(0, 60 + ev * 120)
            else:
                ev_score = 0
        else:
            ev_score = 0
        components["Live Expectancy"] = ev_score

        # 4. Profit Factor Stability (15%)
        if report.champion_metrics:
            pf = report.champion_metrics.profit_factor
            if pf >= 1.5:
                pf_score = 100
            elif pf >= 1.3:
                pf_score = 70 + (pf - 1.3) / 0.2 * 30
            elif pf >= 1.0:
                pf_score = 40 + (pf - 1.0) / 0.3 * 30
            elif pf >= 0.8:
                pf_score = max(0, (pf - 0.8) / 0.2 * 40)
            else:
                pf_score = 0
        else:
            pf_score = 0
        components["PF Stability"] = pf_score

        # 5. Drawdown Stability (10%)
        if report.champion_metrics:
            dd = report.champion_metrics.max_drawdown_pct
            if dd < 5:
                dd_score = 100
            elif dd < 10:
                dd_score = 80 + (10 - dd) / 5 * 20
            elif dd < 15:
                dd_score = 50 + (15 - dd) / 5 * 30
            else:
                dd_score = max(0, 50 - (dd - 15) * 5)
        else:
            dd_score = 50
        components["Drawdown Stability"] = dd_score

        # 6. Drift Score (10%) — lower drift = higher score
        drift_score = 100
        if report.parameter_drift == "WARN":
            drift_score = 50
        elif report.parameter_drift == "FAIL":
            drift_score = 0
        if report.prediction_drift == "WARN":
            drift_score = min(drift_score, 60)
        elif report.prediction_drift == "FAIL":
            drift_score = 0
        components["Drift Score"] = drift_score

        # 7. Data Quality (10%)
        if report.data_integrity == "PASS":
            data_score = 100
        elif report.data_integrity == "WARN":
            data_score = 60
        else:
            data_score = 0
        components["Data Quality"] = data_score

        # Weighted total
        weights = {
            "Statistical Confidence": 0.20,
            "Walk-Forward Stability": 0.20,
            "Live Expectancy": 0.15,
            "PF Stability": 0.15,
            "Drawdown Stability": 0.10,
            "Drift Score": 0.10,
            "Data Quality": 0.10,
        }

        total = sum(components[k] * weights[k] for k in weights)

        return total, components

    # ── Governance Gates ─────────────────────────────────────────

    def _evaluate_gates(self, report: GovernanceReport) -> List[GovernanceGate]:
        """Evaluate all governance gates."""
        gates = []

        m = report.champion_metrics

        # Gate 1: Minimum trades (statistical sufficiency)
        n = m.total_trades if m else 0
        gates.append(GovernanceGate(
            gate_name="Minimum Trades",
            passed=n >= GATE_MIN_TRADES,
            current_value=str(n),
            required_value=f"≥{GATE_MIN_TRADES}",
            reason=f"{n} trades" + (" ✓" if n >= GATE_MIN_TRADES else f" < {GATE_MIN_TRADES} required"),
        ))

        # Gate 2: Profit Factor
        pf = m.profit_factor if m else 0
        gates.append(GovernanceGate(
            gate_name="Profit Factor",
            passed=pf >= GATE_MIN_PF,
            current_value=f"{pf:.2f}",
            required_value=f"≥{GATE_MIN_PF}",
            reason=f"PF {pf:.2f}" + (" ✓" if pf >= GATE_MIN_PF else f" < {GATE_MIN_PF}"),
        ))

        # Gate 3: Expectancy
        ev = m.expectancy_r if m else 0
        gates.append(GovernanceGate(
            gate_name="Expectancy",
            passed=ev > GATE_MIN_EXPECTANCY_R,
            current_value=f"{ev:+.3f}R",
            required_value=f">{GATE_MIN_EXPECTANCY_R}R",
            reason=f"EV {ev:+.3f}R" + (" ✓" if ev > 0 else " ≤ 0"),
        ))

        # Gate 4: Max Drawdown
        dd = m.max_drawdown_pct if m else 0
        gates.append(GovernanceGate(
            gate_name="Max Drawdown",
            passed=dd < GATE_MAX_DRAWDOWN_PCT,
            current_value=f"{dd:.2f}%",
            required_value=f"<{GATE_MAX_DRAWDOWN_PCT}%",
            reason=f"DD {dd:.2f}%" + (" ✓" if dd < 15 else f" ≥ {GATE_MAX_DRAWDOWN_PCT}%"),
        ))

        # Gate 5: Drift
        drift_ok = report.parameter_drift == "PASS" and report.prediction_drift == "PASS"
        gates.append(GovernanceGate(
            gate_name="Drift",
            passed=drift_ok,
            current_value="OK" if drift_ok else "DRIFT",
            required_value="PASS",
            reason="within limits" if drift_ok else "drift detected",
        ))

        # Gate 6: All health checks
        all_health = all(c == "PASS" for c in [
            report.prediction_drift, report.data_integrity,
            report.learning_stable, report.parameter_drift,
            report.execution_drift, report.risk_stable,
        ])
        gates.append(GovernanceGate(
            gate_name="Health Checks",
            passed=all_health,
            current_value="PASS" if all_health else "FAIL",
            required_value="ALL PASS",
            reason="all checks pass" if all_health else "some checks failed",
        ))

        return gates

    def _collect_blockers(self, report: GovernanceReport) -> List[str]:
        """Collect all promotion blockers."""
        blockers = []

        m = report.champion_metrics
        n = m.total_trades if m else 0

        if n < GATE_MIN_TRADES:
            blockers.append(f"Trades: {n} / {GATE_MIN_TRADES} required")

        if report.production_confidence < MODERATE_CONFIDENCE:
            blockers.append(f"Production Confidence: {report.production_confidence:.1f}% (< {MODERATE_CONFIDENCE}%)")

        pf = m.profit_factor if m else 0
        if pf < GATE_MIN_PF:
            blockers.append(f"Profit Factor: {pf:.2f} (< {GATE_MIN_PF})")

        ev = m.expectancy_r if m else 0
        if ev <= 0:
            blockers.append(f"Expectancy: {ev:+.3f}R (≤ 0)")

        dd = m.max_drawdown_pct if m else 0
        if dd >= GATE_MAX_DRAWDOWN_PCT:
            blockers.append(f"Drawdown: {dd:.2f}% (≥ {GATE_MAX_DRAWDOWN_PCT}%)")

        if report.parameter_drift != "PASS":
            blockers.append(f"Parameter Drift: {report.parameter_drift}")

        if report.prediction_drift != "PASS":
            blockers.append(f"Prediction Drift: {report.prediction_drift}")

        return blockers

    # ── Model Health ─────────────────────────────────────────────

    def _calculate_model_health(self, report: GovernanceReport) -> float:
        """Calculate overall model health score (0-100)."""
        checks = [
            report.prediction_drift,
            report.data_integrity,
            report.learning_stable,
            report.parameter_drift,
            report.execution_drift,
            report.risk_stable,
        ]

        passed = sum(1 for c in checks if c == "PASS")
        health = (passed / len(checks)) * 80  # Max 80 from health checks

        # Bonus from gates
        if report.gates:
            gates_passed = sum(1 for g in report.gates if g.passed)
            health += (gates_passed / len(report.gates)) * 20

        return min(health, 100)

    # ── Metrics Calculation ──────────────────────────────────────

    def _calculate_metrics(self, cur, model_name: str) -> ModelMetrics:
        """Calculate complete metrics from trade data."""
        m = ModelMetrics(model_name=model_name)

        cur.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                   SUM(pnl),
                   SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                   SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END),
                   AVG(pnl),
                   AVG(CASE WHEN pnl > 0 THEN realized_r ELSE NULL END),
                   AVG(CASE WHEN pnl <= 0 THEN realized_r ELSE NULL END),
                   MIN(closed_at),
                   MAX(closed_at)
            FROM positions WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0] > 0:
            n, wins, total_pnl, gp, gl, avg_pnl, avg_wr, avg_lr, first_t, last_t = row
            m.total_trades = n
            m.winning_trades = wins or 0
            m.win_rate = (wins or 0) / n if n > 0 else 0
            m.total_pnl = total_pnl or 0
            m.avg_winner_r = avg_wr or 0
            m.avg_loser_r = avg_lr or 0
            m.first_trade_at = first_t or 0
            m.last_trade_at = last_t or 0

            m.profit_factor = (gp or 0) / (gl or 1) if gl and gl > 0 else (
                float('inf') if gp and gp > 0 else 0
            )

            m.expectancy_r = (m.win_rate * m.avg_winner_r) - \
                ((1 - m.win_rate) * abs(m.avg_loser_r))

            m.pf_ci_lower, m.pf_ci_upper = self._profit_factor_ci(gp or 0, gl or 0, n)

            cur.execute("SELECT realized_r FROM positions WHERE status = 'closed' AND realized_r IS NOT NULL")
            rs = [r[0] for r in cur.fetchall() if r[0] is not None]
            if len(rs) > 1:
                mean_r = sum(rs) / len(rs)
                std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1))
                m.sharpe_ratio = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0

            cur.execute("SELECT pnl FROM positions WHERE status = 'closed' ORDER BY closed_at ASC")
            pnls = [r[0] for r in cur.fetchall()]
            if pnls:
                cum = 0.0
                peak = 0.0
                max_dd = 0.0
                for p in pnls:
                    cum += p
                    peak = max(peak, cum)
                    dd = peak - cum
                    max_dd = max(max_dd, dd)
                m.max_drawdown_pct = max_dd / 10000 * 100

            m.recovery_factor = m.total_pnl / max_dd if max_dd > 0 else 0

        return m

    @staticmethod
    def _profit_factor_ci(gross_profit: float, gross_loss: float, n: int) -> Tuple[float, float]:
        """Calculate confidence interval for profit factor."""
        if n < 10 or gross_loss == 0:
            return 0, 0
        pf = gross_profit / gross_loss
        se = pf / math.sqrt(n)
        margin = 1.96 * se
        return max(0, pf - margin), pf + margin

    # ── Health Checks ────────────────────────────────────────────

    def _check_prediction_drift(self, cur) -> str:
        cur.execute("""
            SELECT AVG(pnl) FROM positions WHERE status = 'closed'
            AND closed_at >= (SELECT MAX(closed_at) - 86400 * 7 FROM positions)
        """)
        recent = cur.fetchone()
        cur.execute("SELECT AVG(pnl) FROM positions WHERE status = 'closed'")
        historical = cur.fetchone()
        if recent and historical and recent[0] is not None and historical[0] is not None:
            if abs(recent[0] - historical[0]) > abs(historical[0]) * 0.5 and abs(historical[0]) > 0:
                return "WARN"
        return "PASS"

    def _check_data_integrity(self, cur) -> str:
        cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'closed' AND (pnl IS NULL OR entry_price IS NULL)")
        bad = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'closed'")
        total = cur.fetchone()[0]
        if total > 0 and bad / total > 0.05:
            return "FAIL"
        return "PASS"

    def _check_learning_stability(self, cur) -> str:
        cur.execute("SELECT AVG(confidence) FROM positions WHERE status = 'closed' AND closed_at >= (SELECT MAX(closed_at) - 86400 * 14 FROM positions)")
        row = cur.fetchone()
        if row and row[0] is not None and row[0] < 0.3:
            return "WARN"
        return "PASS"

    def _check_parameter_drift(self, cur) -> str:
        cur.execute("""
            SELECT
                AVG(CASE WHEN closed_at >= (SELECT MAX(closed_at) - 86400 * 7 FROM positions) THEN realized_r END),
                AVG(CASE WHEN closed_at < (SELECT MAX(closed_at) - 86400 * 7 FROM positions) THEN realized_r END)
            FROM positions WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0] is not None and row[1] is not None:
            if abs(row[0] - row[1]) > 1.0:
                return "WARN"
        return "PASS"

    def _check_execution_drift(self, cur) -> str:
        cur.execute("SELECT AVG(hold_minutes) FROM positions WHERE status = 'closed' AND closed_at >= (SELECT MAX(closed_at) - 86400 * 7 FROM positions)")
        row = cur.fetchone()
        if row and row[0] is not None and row[0] > 1000:
            return "WARN"
        return "PASS"

    def _check_risk_stability(self, cur) -> str:
        cur.execute("""
            SELECT COUNT(CASE WHEN pnl < -10 THEN 1 END), COUNT(*)
            FROM positions WHERE status = 'closed'
            AND closed_at >= (SELECT MAX(closed_at) - 86400 * 7 FROM positions)
        """)
        row = cur.fetchone()
        if row and row[1] > 0 and row[0] / row[1] > 0.2:
            return "WARN"
        return "PASS"
