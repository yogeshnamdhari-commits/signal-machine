"""
Production Gate Checker — Validate system readiness for live trading.

Per Executive Assessment v7:
    "Before going live, I would require these validation gates:
        Gate                              Target
        Profit Factor                     > 1.10 sustained
        Expectancy                        Positive over rolling windows
        Walk-forward stability            Similar PF across multiple windows
        Symbol stability                  No dependence on a few symbols
        Regime stability                  No collapse in one market regime
        Out-of-sample validation          Comparable performance to in-sample

     Passing these would give much stronger evidence that the improvements
     generalize rather than fit the current dataset."

Key Features:
    1. Gate Definitions — clear pass/fail criteria
    2. Automated Checking — run all gates against current performance
    3. Detailed Reporting — which gates pass/fail and why
    4. Readiness Score — overall production readiness (0-100)
    5. Recommendation — GO / NO-GO / CONDITIONAL

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# ═══════════════════════════════════════════════════════════════
# PRODUCTION GATE THRESHOLDS
# ═══════════════════════════════════════════════════════════════

GATE_THRESHOLDS = {
    "profit_factor": {
        "target": 1.10,
        "weight": 0.25,
        "description": "Sustained Profit Factor > 1.10",
    },
    "expectancy": {
        "target": 0.0,
        "weight": 0.20,
        "description": "Positive expectancy over rolling windows",
    },
    "walk_forward_stability": {
        "target": 0.3,  # Max std dev of PF across windows
        "weight": 0.15,
        "description": "PF stability across walk-forward windows",
    },
    "symbol_stability": {
        "target": 0.3,  # Max % of PnL from single symbol
        "weight": 0.15,
        "description": "No excessive dependence on few symbols",
    },
    "regime_stability": {
        "target": 0.5,  # Min PF in worst regime
        "weight": 0.15,
        "description": "No collapse in any market regime",
    },
    "trade_count": {
        "target": 100,
        "weight": 0.10,
        "description": "Sufficient sample size for reliability",
    },
}


@dataclass
class GateResult:
    """Result of a single production gate check."""
    gate_name: str = ""
    description: str = ""
    passed: bool = False
    actual_value: float = 0.0
    target_value: float = 0.0
    weight: float = 0.0
    score: float = 0.0  # 0-100, how well this gate is met
    detail: str = ""

    def to_dict(self) -> Dict:
        return {
            "gate": self.gate_name,
            "description": self.description,
            "passed": self.passed,
            "actual": round(self.actual_value, 3),
            "target": round(self.target_value, 3),
            "score": round(self.score, 1),
            "detail": self.detail,
        }


@dataclass
class ProductionReadinessReport:
    """Complete production readiness assessment."""
    timestamp: float = 0.0
    overall_ready: bool = False
    readiness_score: float = 0.0  # 0-100
    recommendation: str = ""      # GO / NO-GO / CONDITIONAL

    # Gate results
    gates: List[GateResult] = field(default_factory=list)
    passed_count: int = 0
    failed_count: int = 0

    # Summary
    summary: str = ""

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "overall_ready": self.overall_ready,
            "readiness_score": round(self.readiness_score, 1),
            "recommendation": self.recommendation,
            "gates": [g.to_dict() for g in self.gates],
            "passed": self.passed_count,
            "failed": self.failed_count,
            "summary": self.summary,
        }


class ProductionGateChecker:
    """
    Validates system readiness for live trading.

    Per Executive Assessment v7:
        "Passing these would give much stronger evidence that the
         improvements generalize rather than fit the current dataset."

    This engine:
        1. Defines clear pass/fail criteria for production
        2. Runs all gates against current performance
        3. Provides readiness score (0-100)
        4. Makes GO/NO-GO recommendation

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, realized_r, pnl, exit_reason, regime,
                       closed_at, hold_minutes
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load production gate checker: {}", e)

    def check(self) -> ProductionReadinessReport:
        """
        Run all production gates and generate readiness report.

        Returns:
            ProductionReadinessReport with pass/fail for each gate
        """
        self._ensure_loaded()

        report = ProductionReadinessReport(timestamp=time.time())

        if not self._trades:
            report.recommendation = "NO-GO"
            report.summary = "No trade data available"
            return report

        # ── Gate 1: Profit Factor > 1.10 ──
        pf = self._calc_profit_factor()
        pf_target = GATE_THRESHOLDS["profit_factor"]["target"]
        pf_score = min(100, (pf / pf_target) * 100) if pf_target > 0 else 0
        report.gates.append(GateResult(
            gate_name="profit_factor",
            description=GATE_THRESHOLDS["profit_factor"]["description"],
            passed=pf >= pf_target,
            actual_value=pf,
            target_value=pf_target,
            weight=GATE_THRESHOLDS["profit_factor"]["weight"],
            score=pf_score,
            detail=f"PF = {pf:.3f} (target: {pf_target:.2f})",
        ))

        # ── Gate 2: Positive Expectancy ──
        ev = self._calc_expectancy()
        ev_target = GATE_THRESHOLDS["expectancy"]["target"]
        ev_score = min(100, max(0, 50 + ev * 100)) if ev >= 0 else max(0, 50 + ev * 50)
        report.gates.append(GateResult(
            gate_name="expectancy",
            description=GATE_THRESHOLDS["expectancy"]["description"],
            passed=ev >= ev_target,
            actual_value=ev,
            target_value=ev_target,
            weight=GATE_THRESHOLDS["expectancy"]["weight"],
            score=ev_score,
            detail=f"EV = {ev:.3f}R (target: >= {ev_target:.2f}R)",
        ))

        # ── Gate 3: Walk-Forward Stability ──
        wf_stability = self._calc_walk_forward_stability()
        wf_target = GATE_THRESHOLDS["walk_forward_stability"]["target"]
        wf_score = min(100, max(0, (1 - wf_stability / wf_target) * 100)) if wf_target > 0 else 50
        report.gates.append(GateResult(
            gate_name="walk_forward_stability",
            description=GATE_THRESHOLDS["walk_forward_stability"]["description"],
            passed=wf_stability <= wf_target,
            actual_value=wf_stability,
            target_value=wf_target,
            weight=GATE_THRESHOLDS["walk_forward_stability"]["weight"],
            score=wf_score,
            detail=f"PF std dev = {wf_stability:.3f} (target: <= {wf_target:.2f})",
        ))

        # ── Gate 4: Symbol Stability ──
        sym_stability = self._calc_symbol_stability()
        sym_target = GATE_THRESHOLDS["symbol_stability"]["target"]
        sym_score = min(100, max(0, (1 - sym_stability / sym_target) * 100)) if sym_target > 0 else 50
        report.gates.append(GateResult(
            gate_name="symbol_stability",
            description=GATE_THRESHOLDS["symbol_stability"]["description"],
            passed=sym_stability <= sym_target,
            actual_value=sym_stability,
            target_value=sym_target,
            weight=GATE_THRESHOLDS["symbol_stability"]["weight"],
            score=sym_score,
            detail=f"Max symbol contribution = {sym_stability:.1%} (target: <= {sym_target:.0%})",
        ))

        # ── Gate 5: Regime Stability ──
        regime_stability = self._calc_regime_stability()
        regime_target = GATE_THRESHOLDS["regime_stability"]["target"]
        regime_score = min(100, (regime_stability / regime_target) * 100) if regime_target > 0 else 50
        report.gates.append(GateResult(
            gate_name="regime_stability",
            description=GATE_THRESHOLDS["regime_stability"]["description"],
            passed=regime_stability >= regime_target,
            actual_value=regime_stability,
            target_value=regime_target,
            weight=GATE_THRESHOLDS["regime_stability"]["weight"],
            score=regime_score,
            detail=f"Worst regime PF = {regime_stability:.3f} (target: >= {regime_target:.2f})",
        ))

        # ── Gate 6: Trade Count ──
        trade_count = len(self._trades)
        tc_target = GATE_THRESHOLDS["trade_count"]["target"]
        tc_score = min(100, (trade_count / tc_target) * 100)
        report.gates.append(GateResult(
            gate_name="trade_count",
            description=GATE_THRESHOLDS["trade_count"]["description"],
            passed=trade_count >= tc_target,
            actual_value=trade_count,
            target_value=tc_target,
            weight=GATE_THRESHOLDS["trade_count"]["weight"],
            score=tc_score,
            detail=f"Trade count = {trade_count} (target: >= {tc_target})",
        ))

        # ── Calculate Readiness Score ──
        report.readiness_score = sum(
            g.score * g.weight for g in report.gates
        )
        report.passed_count = sum(1 for g in report.gates if g.passed)
        report.failed_count = sum(1 for g in report.gates if not g.passed)

        # ── Recommendation ──
        if report.readiness_score >= 80 and report.failed_count == 0:
            report.recommendation = "GO"
            report.overall_ready = True
        elif report.readiness_score >= 60:
            report.recommendation = "CONDITIONAL"
            report.overall_ready = False
        else:
            report.recommendation = "NO-GO"
            report.overall_ready = False

        # ── Summary ──
        failed_gates = [g for g in report.gates if not g.passed]
        if failed_gates:
            report.summary = (
                f"Readiness: {report.readiness_score:.0f}/100. "
                f"{report.passed_count}/{len(report.gates)} gates passed. "
                f"Failed: {', '.join(g.gate_name for g in failed_gates)}"
            )
        else:
            report.summary = (
                f"Readiness: {report.readiness_score:.0f}/100. "
                f"All {len(report.gates)} gates passed."
            )

        return report

    def _calc_profit_factor(self) -> float:
        """Calculate overall profit factor."""
        wins = [t.get("realized_r", 0) or 0 for t in self._trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in self._trades if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        return gross_profit / max(0.01, gross_loss)

    def _calc_expectancy(self) -> float:
        """Calculate expectancy in R."""
        all_r = [t.get("realized_r", 0) or 0 for t in self._trades]
        return sum(all_r) / max(1, len(all_r))

    def _calc_walk_forward_stability(self) -> float:
        """Calculate PF stability across walk-forward windows."""
        window_size = 50
        windows = []

        for i in range(0, len(self._trades), window_size):
            window_trades = self._trades[i:i + window_size]
            if len(window_trades) < 10:
                continue

            wins = [t.get("realized_r", 0) or 0 for t in window_trades if (t.get("realized_r", 0) or 0) > 0]
            losses = [abs(t.get("realized_r", 0) or 0) for t in window_trades if (t.get("realized_r", 0) or 0) < 0]

            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(losses) if losses else 0.01
            pf = gross_profit / max(0.01, gross_loss)
            windows.append(pf)

        if len(windows) < 2:
            return 0.5  # Not enough data

        mean_pf = sum(windows) / len(windows)
        variance = sum((pf - mean_pf) ** 2 for pf in windows) / len(windows)
        return math.sqrt(variance)

    def _calc_symbol_stability(self) -> float:
        """Calculate symbol concentration (max % of PnL from single symbol)."""
        by_symbol: Dict[str, float] = defaultdict(float)
        for t in self._trades:
            by_symbol[t.get("symbol", "")] += t.get("pnl", 0) or 0

        total_pnl = sum(abs(v) for v in by_symbol.values())
        if total_pnl <= 0:
            return 0.0

        max_contribution = max(abs(v) for v in by_symbol.values())
        return max_contribution / max(0.01, total_pnl)

    def _calc_regime_stability(self) -> float:
        """Calculate worst regime PF (minimum across regimes)."""
        by_regime: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_regime[t.get("regime", "unknown")].append(t)

        min_pf = float("inf")
        for regime, trades in by_regime.items():
            if len(trades) < 5:
                continue

            wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
            losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(losses) if losses else 0.01
            pf = gross_profit / max(0.01, gross_loss)
            min_pf = min(min_pf, pf)

        return min_pf if min_pf != float("inf") else 0.5
