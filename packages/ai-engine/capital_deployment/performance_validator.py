"""
Live Performance Validator — Strategy Drift Detection
======================================================
Compares: Backtest PF, Walk-Forward PF, Paper PF, Live PF
Calculates: Performance Drift, Execution Drift, Slippage Drift
"""

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "capital"


# ─── Data Classes ────────────────────────────────────────────────────────────
@dataclass
class PerformancePhase:
    """Performance metrics for a single phase."""
    phase: str  # backtest, walk_forward, paper, live
    profit_factor: float
    win_rate: float
    sharpe_ratio: float
    max_drawdown: float
    avg_trade_pnl: float
    total_trades: int
    avg_slippage_bps: float
    avg_holding_time_hours: float
    expectancy: float
    calmar_ratio: float
    recovery_factor: float
    timestamp: str = ""


@dataclass
class DriftAnalysis:
    """Drift between two performance phases."""
    from_phase: str
    to_phase: str
    pf_drift: float          # Profit factor change
    wr_drift: float          # Win rate change
    sharpe_drift: float      # Sharpe change
    dd_drift: float          # Drawdown change
    execution_drift: float   # Execution quality change
    slippage_drift: float    # Slippage change
    overall_drift: float     # Composite drift score
    drift_severity: str      # NONE, MINOR, MODERATE, SEVERE, CRITICAL
    alerts: list


@dataclass
class ValidationReport:
    """Complete validation report."""
    timestamp: str
    phases: dict
    drift_analysis: dict
    overall_health: str
    health_score: float
    recommendations: list
    alerts: list


# ─── Performance Validator ───────────────────────────────────────────────────
class PerformanceValidator:
    """
    Validates live performance against backtest expectations.

    Usage:
        validator = PerformanceValidator()
        validator.set_phase("backtest", pf=2.1, wr=0.55, sharpe=1.8, ...)
        validator.set_phase("live", pf=1.7, wr=0.52, sharpe=1.4, ...)
        report = validator.validate()
    """

    # Drift thresholds
    PF_DRIFT_WARNING = 0.15     # 15% PF drop
    PF_DRIFT_CRITICAL = 0.30    # 30% PF drop
    WR_DRIFT_WARNING = 0.05     # 5% WR drop
    WR_DRIFT_CRITICAL = 0.10    # 10% WR drop
    SHARPE_DRIFT_WARNING = 0.25
    SHARPE_DRIFT_CRITICAL = 0.50

    def __init__(self):
        self._phases: dict[str, PerformancePhase] = {}
        self._drift_history: list[dict] = []
        self._load_state()
        logger.info("PerformanceValidator initialized")

    # ── Set Phase Performance ─────────────────────────────────────────────────
    def set_phase(
        self,
        phase: str,
        profit_factor: float,
        win_rate: float,
        sharpe_ratio: float,
        max_drawdown: float,
        avg_trade_pnl: float = 0.0,
        total_trades: int = 0,
        avg_slippage_bps: float = 0.0,
        avg_holding_time_hours: float = 0.0,
        expectancy: float = 0.0,
        calmar_ratio: float = 0.0,
        recovery_factor: float = 0.0,
    ):
        """Set performance metrics for a phase."""
        self._phases[phase] = PerformancePhase(
            phase=phase,
            profit_factor=profit_factor,
            win_rate=win_rate,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            avg_trade_pnl=avg_trade_pnl,
            total_trades=total_trades,
            avg_slippage_bps=avg_slippage_bps,
            avg_holding_time_hours=avg_holding_time_hours,
            expectancy=expectancy,
            calmar_ratio=calmar_ratio,
            recovery_factor=recovery_factor,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._save_state()
        logger.info("Set %s performance: PF=%.2f, WR=%.1f%%, Sharpe=%.2f",
                     phase, profit_factor, win_rate * 100, sharpe_ratio)

    # ── Validate ──────────────────────────────────────────────────────────────
    def validate(self) -> ValidationReport:
        """Run complete validation across all phases."""
        alerts = []
        drifts = {}

        # Phase order for comparison
        phase_order = ["backtest", "walk_forward", "paper", "live"]
        available = [p for p in phase_order if p in self._phases]

        # Calculate drifts between consecutive phases
        for i in range(len(available) - 1):
            from_phase = available[i]
            to_phase = available[i + 1]
            drift = self._calculate_drift(from_phase, to_phase)
            drifts[f"{from_phase}_to_{to_phase}"] = asdict(drift)

            if drift.drift_severity in ("SEVERE", "CRITICAL"):
                alerts.append({
                    "severity": drift.drift_severity,
                    "message": f"Significant drift from {from_phase} to {to_phase}: {drift.overall_drift:.1%}",
                    "details": drift.alerts,
                })

        # Calculate overall health
        health_score = self._calculate_health_score(drifts)
        health = self._assess_health(health_score)

        # Generate recommendations
        recs = self._generate_recommendations(drifts, alerts)

        report = ValidationReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            phases={k: asdict(v) for k, v in self._phases.items()},
            drift_analysis=drifts,
            overall_health=health,
            health_score=round(health_score, 2),
            recommendations=recs,
            alerts=alerts,
        )

        self._drift_history.append(asdict(report))
        self._save_state()
        return report

    # ── Drift Calculation ─────────────────────────────────────────────────────
    def _calculate_drift(self, from_phase: str, to_phase: str) -> DriftAnalysis:
        """Calculate drift between two phases."""
        fp = self._phases[from_phase]
        tp = self._phases[to_phase]

        # PF drift (percentage change)
        pf_drift = (tp.profit_factor - fp.profit_factor) / fp.profit_factor if fp.profit_factor > 0 else 0

        # WR drift (absolute change)
        wr_drift = tp.win_rate - fp.win_rate

        # Sharpe drift
        sharpe_drift = (tp.sharpe_ratio - fp.sharpe_ratio) / fp.sharpe_ratio if fp.sharpe_ratio > 0 else 0

        # DD drift (worse = higher)
        dd_drift = tp.max_drawdown - fp.max_drawdown

        # Execution drift (slippage change)
        execution_drift = tp.avg_slippage_bps - fp.avg_slippage_bps

        # Slippage drift
        slippage_drift = execution_drift  # Same metric

        # Overall drift (weighted composite)
        overall_drift = (
            abs(pf_drift) * 0.30 +
            abs(wr_drift) * 0.20 +
            abs(sharpe_drift) * 0.25 +
            abs(dd_drift) * 0.15 +
            abs(execution_drift / 100) * 0.10  # Normalize bps
        )

        # Severity
        severity = self._classify_drift(overall_drift, pf_drift, wr_drift, sharpe_drift)

        # Alerts
        drift_alerts = []
        if pf_drift < -self.PF_DRIFT_WARNING:
            drift_alerts.append(f"PF dropped {abs(pf_drift):.1%}")
        if wr_drift < -self.WR_DRIFT_WARNING:
            drift_alerts.append(f"WR dropped {abs(wr_drift):.1%}")
        if sharpe_drift < -self.SHARPE_DRIFT_WARNING:
            drift_alerts.append(f"Sharpe dropped {abs(sharpe_drift):.1%}")
        if dd_drift > 0.05:
            drift_alerts.append(f"Max DD increased by {dd_drift:.1%}")

        return DriftAnalysis(
            from_phase=from_phase,
            to_phase=to_phase,
            pf_drift=round(pf_drift, 4),
            wr_drift=round(wr_drift, 4),
            sharpe_drift=round(sharpe_drift, 4),
            dd_drift=round(dd_drift, 4),
            execution_drift=round(execution_drift, 2),
            slippage_drift=round(slippage_drift, 2),
            overall_drift=round(overall_drift, 4),
            drift_severity=severity,
            alerts=drift_alerts,
        )

    def _classify_drift(
        self, overall: float, pf: float, wr: float, sharpe: float
    ) -> str:
        """Classify drift severity."""
        if pf < -self.PF_DRIFT_CRITICAL or wr < -self.WR_DRIFT_CRITICAL:
            return "CRITICAL"
        if overall > 0.30:
            return "SEVERE"
        if overall > 0.15:
            return "MODERATE"
        if overall > 0.05:
            return "MINOR"
        return "NONE"

    # ── Health Scoring ────────────────────────────────────────────────────────
    def _calculate_health_score(self, drifts: dict) -> float:
        """Calculate overall health score (0-100)."""
        if not drifts:
            return 100.0

        scores = []
        for drift_data in drifts.values():
            severity = drift_data.get("drift_severity", "NONE")
            severity_scores = {
                "NONE": 100, "MINOR": 85, "MODERATE": 60, "SEVERE": 30, "CRITICAL": 0
            }
            scores.append(severity_scores.get(severity, 50))

        return sum(scores) / len(scores)

    def _assess_health(self, score: float) -> str:
        """Assess overall health from score."""
        if score >= 90:
            return "EXCELLENT"
        elif score >= 75:
            return "GOOD"
        elif score >= 50:
            return "FAIR"
        elif score >= 25:
            return "POOR"
        return "CRITICAL"

    # ── Recommendations ──────────────────────────────────────────────────────
    def _generate_recommendations(self, drifts: dict, alerts: list) -> list[str]:
        """Generate actionable recommendations."""
        recs = []

        if not drifts:
            return ["Insufficient data for recommendations"]

        # Check for consistent degradation
        degrading = sum(
            1 for d in drifts.values() if d.get("drift_severity") in ("MODERATE", "SEVERE", "CRITICAL")
        )

        if degrading >= 2:
            recs.append("⚠️ Consistent performance degradation detected — consider reducing position sizes")
            recs.append("Review market regime changes that may be affecting strategy")
        elif degrading == 1:
            recs.append("Monitor performance closely — one phase shows significant drift")

        # PF-specific
        for key, d in drifts.items():
            if d.get("pf_drift", 0) < -0.20:
                recs.append(f"PF drift of {d['pf_drift']:.1%} in {key} — review entry/exit logic")

        # Slippage
        for key, d in drifts.items():
            if d.get("slippage_drift", 0) > 10:
                recs.append(f"Slippage increased by {d['slippage_drift']:.1f} bps — consider limit orders")

        if not recs:
            recs.append("✅ Performance is within expected parameters")

        return recs

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save validator state."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "phases": {k: asdict(v) for k, v in self._phases.items()},
            "drift_history": self._drift_history[-100:],
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "performance_validation.json").write_text(json.dumps(state, indent=2, default=str))

    def _load_state(self):
        """Load persisted state."""
        path = DATA_DIR / "performance_validation.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            for k, v in state.get("phases", {}).items():
                self._phases[k] = PerformancePhase(**v)
            self._drift_history = state.get("drift_history", [])
        except Exception as e:
            logger.error("Failed to load validation state: %s", e)

    def get_stats(self) -> dict:
        """Get validator statistics."""
        report = self.validate() if self._phases else None
        return {
            "phases_configured": list(self._phases.keys()),
            "overall_health": report.overall_health if report else "N/A",
            "health_score": report.health_score if report else 0,
            "alerts": len(report.alerts) if report else 0,
        }
