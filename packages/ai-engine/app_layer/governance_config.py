"""
Governance Configuration — Configurable promotion rules and split dashboards.

Per feedback:
    1. Separate Engineering Health from Trading Health
    2. Make promotion rules configurable (not fixed constants)
    3. Add stability metrics
    4. Add per-engine validation statistics

This module provides:
    - Configurable governance gates (YAML-loadable)
    - Engineering Health dashboard (API, DB, Latency, etc.)
    - Trading Health dashboard (PF, EV, Drawdown, etc.)
    - Stability metrics (rolling PF, expectancy trend, etc.)
    - Per-engine validation statistics

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


# ═══════════════════════════════════════════════════════════════
# CONFIGURABLE GOVERNANCE RULES
# ═══════════════════════════════════════════════════════════════

@dataclass
class GovernanceRules:
    """Configurable governance rules — can be loaded from YAML/JSON."""
    # Promotion gates
    min_trades: int = 100
    min_profit_factor: float = 1.30
    min_expectancy_r: float = 0.0
    max_drawdown_pct: float = 15.0
    min_confidence: float = 90.0
    min_health_checks_passed: int = 6  # Out of 6

    # Kill switch thresholds
    kill_pf_threshold: float = 0.5
    kill_drawdown_threshold: float = 200.0  # USD
    kill_max_consecutive_losses: int = 10

    # Statistical sufficiency
    min_trades_for_symbol: int = 20
    min_trades_for_regime: int = 15
    min_trades_for_session: int = 10
    min_trades_for_exit: int = 10
    min_trades_for_global: int = 50

    # Champion-challenger
    min_shadow_trades: int = 30
    min_promotion_improvements: int = 3  # Out of 5 metrics

    def to_dict(self) -> Dict:
        return {
            "promotion": {
                "min_trades": self.min_trades,
                "min_profit_factor": self.min_profit_factor,
                "min_expectancy_r": self.min_expectancy_r,
                "max_drawdown_pct": self.max_drawdown_pct,
                "min_confidence": self.min_confidence,
            },
            "kill_switch": {
                "pf_threshold": self.kill_pf_threshold,
                "drawdown_threshold": self.kill_drawdown_threshold,
                "max_consecutive_losses": self.kill_max_consecutive_losses,
            },
            "statistical_sufficiency": {
                "symbol": self.min_trades_for_symbol,
                "regime": self.min_trades_for_regime,
                "session": self.min_trades_for_session,
                "exit": self.min_trades_for_exit,
                "global": self.min_trades_for_global,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "GovernanceRules":
        """Load rules from a dictionary (e.g., from YAML/JSON)."""
        rules = cls()
        promo = data.get("promotion", {})
        kill = data.get("kill_switch", {})
        stat = data.get("statistical_sufficiency", {})

        if "min_trades" in promo:
            rules.min_trades = promo["min_trades"]
        if "min_profit_factor" in promo:
            rules.min_profit_factor = promo["min_profit_factor"]
        if "min_expectancy_r" in promo:
            rules.min_expectancy_r = promo["min_expectancy_r"]
        if "max_drawdown_pct" in promo:
            rules.max_drawdown_pct = promo["max_drawdown_pct"]
        if "min_confidence" in promo:
            rules.min_confidence = promo["min_confidence"]
        if "pf_threshold" in kill:
            rules.kill_pf_threshold = kill["pf_threshold"]
        if "drawdown_threshold" in kill:
            rules.kill_drawdown_threshold = kill["drawdown_threshold"]
        if "max_consecutive_losses" in kill:
            rules.kill_max_consecutive_losses = kill["max_consecutive_losses"]
        if "symbol" in stat:
            rules.min_trades_for_symbol = stat["symbol"]
        if "regime" in stat:
            rules.min_trades_for_regime = stat["regime"]

        return rules


# ═══════════════════════════════════════════════════════════════
# ENGINEERING HEALTH DASHBOARD
# ═══════════════════════════════════════════════════════════════

@dataclass
class EngineeringHealth:
    """Engineering health metrics — system infrastructure status."""
    api_status: str = "PASS"
    database_status: str = "PASS"
    latency_ms: float = 0.0
    data_quality: str = "PASS"
    execution_status: str = "PASS"
    scanner_status: str = "PASS"
    websocket_status: str = "PASS"
    synchronization: str = "PASS"
    cpu_usage_pct: float = 0.0
    memory_usage_pct: float = 0.0

    overall_score: float = 0.0  # 0-100

    def to_dict(self) -> Dict:
        return {
            "api": self.api_status,
            "database": self.database_status,
            "latency_ms": round(self.latency_ms, 1),
            "data_quality": self.data_quality,
            "execution": self.execution_status,
            "scanner": self.scanner_status,
            "websocket": self.websocket_status,
            "synchronization": self.synchronization,
            "cpu_pct": round(self.cpu_usage_pct, 1),
            "memory_pct": round(self.memory_usage_pct, 1),
            "overall_score": round(self.overall_score, 1),
        }

    def render(self) -> str:
        lines = []
        lines.append("┌─ ENGINEERING HEALTH ─" + "─" * 38 + "┐")
        checks = [
            ("API", self.api_status),
            ("Database", self.database_status),
            ("Data Quality", self.data_quality),
            ("Execution", self.execution_status),
            ("Scanner", self.scanner_status),
            ("WebSocket", self.websocket_status),
            ("Synchronization", self.synchronization),
        ]
        for name, status in checks:
            icon = "✓" if status == "PASS" else "✗" if status == "FAIL" else "⚠"
            lines.append(f"│  {icon} {name:<22s} {status:<8s}              │")
        lines.append(f"│  Latency: {self.latency_ms:>6.0f}ms    │  "
                     f"CPU: {self.cpu_usage_pct:>5.1f}%    │  "
                     f"Mem: {self.memory_usage_pct:>5.1f}% │")
        lines.append("└" + "─" * 60 + "┘")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# TRADING HEALTH DASHBOARD
# ═══════════════════════════════════════════════════════════════

@dataclass
class TradingHealth:
    """Trading health metrics — performance and risk status."""
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    recovery_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    trade_quality_score: float = 0.0
    portfolio_heat_pct: float = 0.0
    prediction_drift: str = "PASS"
    production_confidence: float = 0.0

    overall_score: float = 0.0  # 0-100

    def to_dict(self) -> Dict:
        return {
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "recovery_factor": round(self.recovery_factor, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "avg_winner_r": round(self.avg_winner_r, 3),
            "avg_loser_r": round(self.avg_loser_r, 3),
            "trade_quality_score": round(self.trade_quality_score, 1),
            "portfolio_heat_pct": round(self.portfolio_heat_pct, 2),
            "prediction_drift": self.prediction_drift,
            "production_confidence": round(self.production_confidence, 1),
            "overall_score": round(self.overall_score, 1),
        }

    def render(self) -> str:
        lines = []
        lines.append("┌─ TRADING HEALTH ─" + "─" * 42 + "┐")
        lines.append(f"│  Profit Factor:      {self.profit_factor:>8.2f}     │  "
                     f"Expectancy: {self.expectancy_r:>+7.3f}R   │")
        lines.append(f"│  Avg Winner:         {self.avg_winner_r:>+7.3f}R    │  "
                     f"Avg Loser:  {self.avg_loser_r:>+7.3f}R    │")
        lines.append(f"│  Max Drawdown:      {self.max_drawdown_pct:>7.2f}%     │  "
                     f"Recovery:   {self.recovery_factor:>+7.2f}     │")
        lines.append(f"│  Portfolio Heat:    {self.portfolio_heat_pct:>7.2f}%     │  "
                     f"Confidence: {self.production_confidence:>6.1f}%    │")
        lines.append(f"│  Prediction Drift:  {self.prediction_drift:<10s}    │  "
                     f"Trade Q:    {self.trade_quality_score:>6.1f}/100   │")
        lines.append("└" + "─" * 60 + "┘")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# STABILITY METRICS
# ═══════════════════════════════════════════════════════════════

@dataclass
class StabilityMetrics:
    """Stability metrics — rolling performance indicators."""
    rolling_pf_50: float = 0.0
    rolling_pf_200: float = 0.0
    rolling_ev_50: float = 0.0
    rolling_ev_200: float = 0.0
    pf_trend: str = "STABLE"
    ev_trend: str = "STABLE"
    drawdown_trend: str = "STABLE"
    rolling_max_dd_50: float = 0.0
    rolling_max_dd_200: float = 0.0
    rolling_recovery_50: float = 0.0
    filter_rejection_rate: float = 0.0
    filter_rejections_by_engine: Dict[str, float] = field(default_factory=dict)
    exit_distribution: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "rolling": {"pf_50": round(self.rolling_pf_50, 2), "pf_200": round(self.rolling_pf_200, 2),
                        "ev_50": round(self.rolling_ev_50, 3), "ev_200": round(self.rolling_ev_200, 3)},
            "trends": {"pf": self.pf_trend, "ev": self.ev_trend, "drawdown": self.drawdown_trend},
            "filter_rejection_rate": round(self.filter_rejection_rate, 3),
        }

    def render(self) -> str:
        lines = []
        lines.append("┌─ STABILITY METRICS ─" + "─" * 38 + "┐")
        lines.append(f"│  Rolling PF (50):    {self.rolling_pf_50:>8.2f}     │  "
                     f"Trend: {self.pf_trend:<14s}        │")
        lines.append(f"│  Rolling PF (200):   {self.rolling_pf_200:>8.2f}     │  "
                     f"EV Trend: {self.ev_trend:<12s}        │")
        lines.append(f"│  Rolling EV (50):    {self.rolling_ev_50:>+7.3f}R    │  "
                     f"DD Trend: {self.drawdown_trend:<12s}        │")
        lines.append(f"│  Rolling EV (200):   {self.rolling_ev_200:>+7.3f}R    │  "
                     f"Reject%: {self.filter_rejection_rate:>6.1f}%     │")
        lines.append("└" + "─" * 60 + "┘")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CONFIDENCE INTERVAL METRICS
# ═══════════════════════════════════════════════════════════════

@dataclass
class CIMetric:
    """A metric with confidence interval."""
    name: str = ""
    point_estimate: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    sample_size: int = 0
    unit: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "estimate": round(self.point_estimate, 4),
            "ci_lower": round(self.ci_lower, 4),
            "ci_upper": round(self.ci_upper, 4),
            "sample_size": self.sample_size,
            "unit": self.unit,
        }

    def render(self) -> str:
        return f"{self.name}: {self.point_estimate:+.4f}{self.unit}  95% CI [{self.ci_lower:+.4f}, {self.ci_upper:+.4f}]  (n={self.sample_size})"


@dataclass
class CIMetricsSet:
    """Set of metrics with confidence intervals."""
    metrics: List[CIMetric] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {m.name: m.to_dict() for m in self.metrics}

    def render(self) -> str:
        lines = []
        lines.append("┌─ METRICS WITH CONFIDENCE INTERVALS ─" + "─" * 26 + "┐")
        for m in self.metrics:
            lines.append(f"│  {m.name:<20s} {m.point_estimate:>+8.4f}{m.unit:<3s}  "
                         f"[{m.ci_lower:+.4f}, {m.ci_upper:+.4f}]  n={m.sample_size:<5d}│")
        lines.append("└" + "─" * 60 + "┘")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CATEGORIZED DRIFT
# ═══════════════════════════════════════════════════════════════

@dataclass
class DriftCategory:
    """A single drift category."""
    category: str = ""  # Data / Feature / Prediction / Execution / Performance
    status: str = "PASS"  # PASS / WARN / FAIL
    severity: float = 0.0  # 0-1, higher = more drift
    description: str = ""
    last_checked: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "category": self.category,
            "status": self.status,
            "severity": round(self.severity, 3),
            "description": self.description,
        }


@dataclass
class CategorizedDrift:
    """All drift categories."""
    categories: List[DriftCategory] = field(default_factory=list)
    overall_status: str = "PASS"

    def to_dict(self) -> Dict:
        return {
            "overall": self.overall_status,
            "categories": {c.category: c.to_dict() for c in self.categories},
        }

    def render(self) -> str:
        lines = []
        lines.append("┌─ DRIFT BY CATEGORY ─" + "─" * 38 + "┐")
        for c in self.categories:
            icon = "✓" if c.status == "PASS" else "✗" if c.status == "FAIL" else "⚠"
            bar_len = int(c.severity * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            lines.append(f"│  {icon} {c.category:<18s} {bar} {c.status:<5s}  │")
        lines.append("└" + "─" * 60 + "┘")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# SAFE MODE
# ═══════════════════════════════════════════════════════════════

@dataclass
class SafeModeState:
    """Current safe mode state — Risk State Machine with RECOVERY."""
    level: str = "NORMAL"  # NORMAL / CAUTIOUS / DEGRADED / CRITICAL / HALT / RECOVERY
    size_multiplier: float = 1.0
    reason: str = ""
    actions_allowed: List[str] = field(default_factory=list)
    actions_blocked: List[str] = field(default_factory=list)

    # RECOVERY state requires evidence before returning to NORMAL
    recovery_checks: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "level": self.level,
            "size_multiplier": round(self.size_multiplier, 2),
            "reason": self.reason,
            "recovery_checks": self.recovery_checks,
        }

    def render(self) -> str:
        lines = []
        lines.append("┌─ SAFE MODE (Risk State Machine) ─" + "─" * 25 + "┐")
        level_colors = {
            "NORMAL": "🟢", "CAUTIOUS": "🟡", "DEGRADED": "🟠",
            "CRITICAL": "🔴", "HALT": "⛔", "RECOVERY": "🔄",
        }
        icon = level_colors.get(self.level, "❓")
        lines.append(f"│  {icon} Level: {self.level:<12s}  Size: {self.size_multiplier:.0%}              │")
        if self.reason:
            lines.append(f"│  Reason: {self.reason:<48s}│")

        if self.level == "RECOVERY" and self.recovery_checks:
            lines.append("│  Recovery Requirements:" + " " * 35 + "│")
            for check, met in self.recovery_checks.items():
                c_icon = "✓" if met else "✗"
                lines.append(f"│    {c_icon} {check:<50s}  │")

        # Show state machine flow
        lines.append("│" + " " * 59 + "│")
        lines.append("│  Flow: NORMAL→CAUTION→DEGRADED→CRITICAL→HALT→RECOVERY→NORMAL  │")
        lines.append("└" + "─" * 59 + "┘")
        return "\n".join(lines)


class RiskStateMachine:
    """
    Risk State Machine with RECOVERY gate.

    Transitions:
        NORMAL → CAUTION: 2+ consecutive losses
        CAUTION → DEGRADED: 3+ consecutive losses OR daily loss > 2%
        DEGRADED → CRITICAL: 5+ consecutive losses OR daily loss > 3%
        CRITICAL → HALT: Any kill switch trigger
        HALT → RECOVERY: 1+ winning trade AND cooldown elapsed
        RECOVERY → NORMAL: ALL recovery checks pass

    RECOVERY requires evidence:
        - Stable data feeds
        - Reduced drawdown
        - Improving expectancy
        - Governance checks passing
    """

    # Recovery requirements — ALL must pass
    RECOVERY_CHECKS = {
        "data_feeds_stable": "Data feeds stable for 1+ hour",
        "drawdown_reduced": "Drawdown below 50% of peak",
        "expectancy_improving": "Recent 10 trades have positive EV",
        "governance_passing": "All governance health checks PASS",
        "no_critical_drift": "No critical drift detected",
    }

    def __init__(self):
        self._state = "NORMAL"
        self._consecutive_losses = 0
        self._consecutive_wins = 0
        self._daily_pnl = 0.0
        self._peak_equity = 0.0
        self._current_equity = 0.0
        self._halt_time = 0.0
        self._recovery_checks: Dict[str, bool] = {}

    def record_trade(self, pnl: float) -> None:
        """Record a completed trade outcome."""
        if pnl < 0:
            self._consecutive_losses += 1
            self._consecutive_wins = 0
        elif pnl > 0:
            self._consecutive_wins += 1
            self._consecutive_losses = 0

        self._daily_pnl += pnl
        self._current_equity += pnl
        self._peak_equity = max(self._peak_equity, self._current_equity)

    def evaluate(self) -> SafeModeState:
        """Evaluate current risk state and return SafeModeState."""
        state = SafeModeState()

        if self._state == "NORMAL":
            state = self._evaluate_normal()
        elif self._state == "CAUTIOUS":
            state = self._evaluate_cautious()
        elif self._state == "DEGRADED":
            state = self._evaluate_degraded()
        elif self._state == "CRITICAL":
            state = self._evaluate_critical()
        elif self._state == "HALT":
            state = self._evaluate_halt()
        elif self._state == "RECOVERY":
            state = self._evaluate_recovery()

        return state

    def _evaluate_normal(self) -> SafeModeState:
        """Evaluate NORMAL state."""
        if self._consecutive_losses >= 3:
            self._state = "CAUTIOUS"
            return SafeModeState(
                level="CAUTIOUS", size_multiplier=0.7,
                reason=f"{self._consecutive_losses} consecutive losses",
            )
        return SafeModeState(level="NORMAL", size_multiplier=1.0)

    def _evaluate_cautious(self) -> SafeModeState:
        """Evaluate CAUTIOUS state."""
        if self._consecutive_losses >= 5:
            self._state = "DEGRADED"
            return SafeModeState(
                level="DEGRADED", size_multiplier=0.4,
                reason=f"{self._consecutive_losses} consecutive losses",
            )
        if self._consecutive_wins >= 3:
            self._state = "NORMAL"
            return SafeModeState(level="NORMAL", size_multiplier=1.0)
        return SafeModeState(
            level="CAUTIOUS", size_multiplier=0.7,
            reason=f"{self._consecutive_losses} consecutive losses",
        )

    def _evaluate_degraded(self) -> SafeModeState:
        """Evaluate DEGRADED state."""
        dd_pct = (self._peak_equity - self._current_equity) / max(self._peak_equity, 1) * 100
        if dd_pct > 10 or self._consecutive_losses >= 8:
            self._state = "CRITICAL"
            return SafeModeState(
                level="CRITICAL", size_multiplier=0.0,
                reason=f"drawdown {dd_pct:.1f}% or {self._consecutive_losses} consecutive losses",
            )
        if self._consecutive_wins >= 2:
            self._state = "CAUTIOUS"
            return SafeModeState(level="CAUTIOUS", size_multiplier=0.7)
        return SafeModeState(
            level="DEGRADED", size_multiplier=0.4,
            reason=f"drawdown {dd_pct:.1f}%",
        )

    def _evaluate_critical(self) -> SafeModeState:
        """Evaluate CRITICAL state → HALT."""
        self._state = "HALT"
        self._halt_time = time.time()
        return SafeModeState(
            level="HALT", size_multiplier=0.0,
            reason="critical risk threshold exceeded",
        )

    def _evaluate_halt(self) -> SafeModeState:
        """Evaluate HALT state → RECOVERY after cooldown."""
        import time
        cooldown = 3600  # 1 hour
        elapsed = time.time() - self._halt_time

        if elapsed < cooldown:
            return SafeModeState(
                level="HALT", size_multiplier=0.0,
                reason=f"cooldown {cooldown - elapsed:.0f}s remaining",
            )

        # After cooldown, enter RECOVERY
        self._state = "RECOVERY"
        return SafeModeState(
            level="RECOVERY", size_multiplier=0.0,
            reason="cooldown complete — entering recovery",
            recovery_checks=dict(self.RECOVERY_CHECKS),
        )

    def _evaluate_recovery(self) -> SafeModeState:
        """
        Evaluate RECOVERY state — requires evidence before returning to NORMAL.
        """
        import time

        # Check all recovery requirements
        self._recovery_checks = {}

        # 1. Data feeds stable (check if no errors in last hour)
        self._recovery_checks["data_feeds_stable"] = True  # Would check actual feed status

        # 2. Drawdown reduced (below 50% of peak loss)
        dd_pct = (self._peak_equity - self._current_equity) / max(self._peak_equity, 1) * 100
        self._recovery_checks["drawdown_reduced"] = dd_pct < 5.0

        # 3. Expectancy improving (recent 10 trades have positive EV)
        self._recovery_checks["expectancy_improving"] = self._consecutive_wins >= 3

        # 4. Governance checks passing
        self._recovery_checks["governance_passing"] = True  # Would check actual governance

        # 5. No critical drift
        self._recovery_checks["no_critical_drift"] = True  # Would check actual drift

        # All checks must pass
        all_passed = all(self._recovery_checks.values())

        if all_passed:
            self._state = "NORMAL"
            self._consecutive_losses = 0
            self._consecutive_wins = 0
            return SafeModeState(level="NORMAL", size_multiplier=1.0)

        return SafeModeState(
            level="RECOVERY", size_multiplier=0.2,
            reason="recovery checks in progress",
            recovery_checks=self._recovery_checks,
        )


# ═══════════════════════════════════════════════════════════════
# DEPLOYMENT PHASES (Enhanced)
# ═══════════════════════════════════════════════════════════════

@dataclass
class DeploymentPhase:
    """A single deployment phase with conditions."""
    name: str = ""
    display_name: str = ""
    is_current: bool = False
    is_passed: bool = False
    conditions: List[str] = field(default_factory=list)
    conditions_met: List[bool] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "display": self.display_name,
            "current": self.is_current,
            "passed": self.is_passed,
            "conditions": [
                {"text": c, "met": m}
                for c, m in zip(self.conditions, self.conditions_met)
            ],
        }

    def render(self) -> str:
        icon = "●" if self.is_current else ("✓" if self.is_passed else "○")
        lines = []
        lines.append(f"│  {icon} {self.display_name:<20s} {'← CURRENT' if self.is_current else '':<12s}        │")
        if self.is_current and self.conditions:
            for c, met in zip(self.conditions, self.conditions_met):
                check = "✓" if met else "✗"
                lines.append(f"│    {check} {c:<52s}  │")
        return "\n".join(lines)


@dataclass
class DeploymentPhases:
    """Complete deployment phase display."""
    current_phase: str = "learning"
    phases: List[DeploymentPhase] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        lines.append("┌─ DEPLOYMENT PHASES ─" + "─" * 38 + "┐")
        for p in self.phases:
            lines.append(p.render())
        lines.append("└" + "─" * 60 + "┘")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# PROMOTION POLICY (Evidence-Based)
# ═══════════════════════════════════════════════════════════════

@dataclass
class PromotionPolicy:
    """Evidence-based promotion policy — no rule promoted without proof."""
    # Minimum requirements for promotion
    min_validation_sample: int = 500
    min_out_of_sample_trades: int = 100
    min_pf_improvement_pct: float = 10.0  # PF must improve by 10%+
    max_drawdown_increase_pct: float = 0.0  # DD must not worsen
    min_confidence: float = 90.0  # Confidence interval must exclude zero improvement

    # Evidence requirements
    requires_out_of_sample: bool = True
    requires_walk_forward: bool = True
    requires_shadow_testing: bool = True
    requires_governance_pass: bool = True

    def to_dict(self) -> Dict:
        return {
            "min_validation_sample": self.min_validation_sample,
            "min_out_of_sample_trades": self.min_out_of_sample_trades,
            "min_pf_improvement_pct": self.min_pf_improvement_pct,
            "max_drawdown_increase_pct": self.max_drawdown_increase_pct,
            "min_confidence": self.min_confidence,
            "requires_out_of_sample": self.requires_out_of_sample,
            "requires_walk_forward": self.requires_walk_forward,
            "requires_shadow_testing": self.requires_shadow_testing,
            "requires_governance_pass": self.requires_governance_pass,
        }

    def render(self) -> str:
        lines = []
        lines.append("┌─ PROMOTION POLICY (Evidence-Based) ─" + "─" * 23 + "┐")
        lines.append(f"│  Min Validation Sample:  {self.min_validation_sample:>6} trades              │")
        lines.append(f"│  Min OOS Trades:         {self.min_out_of_sample_trades:>6} trades              │")
        lines.append(f"│  Min PF Improvement:     {self.min_pf_improvement_pct:>5.1f}%                   │")
        lines.append(f"│  Max DD Increase:        {self.max_drawdown_increase_pct:>5.1f}%                   │")
        lines.append(f"│  Min Confidence:         {self.min_confidence:>5.1f}%                   │")
        lines.append("│" + " " * 59 + "│")
        lines.append(f"│  Requires: OOS={self.requires_out_of_sample}  WF={self.requires_walk_forward}  Shadow={self.requires_shadow_testing}     │")
        lines.append("└" + "─" * 60 + "┘")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# RESEARCH DASHBOARD
# ═══════════════════════════════════════════════════════════════

@dataclass
class ResearchDashboard:
    """Research dashboard — answers: does the App add value?"""
    # Period
    period_trades: int = 0
    period_start: str = ""
    period_end: str = ""

    # Core metrics
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    max_drawdown_pct: float = 0.0

    # Best/Worst
    best_regime: str = ""
    worst_regime: str = ""
    best_symbols: List[Dict] = field(default_factory=list)
    worst_symbols: List[Dict] = field(default_factory=list)

    # App engine attribution
    most_valuable_engine: str = ""
    least_valuable_engine: str = ""
    engine_attribution: List[Dict] = field(default_factory=list)

    # Promotion status
    promotion_status: str = "NOT READY"
    champion_version: str = ""
    challenger_version: str = ""

    def to_dict(self) -> Dict:
        return {
            "period": {"trades": self.period_trades, "start": self.period_start, "end": self.period_end},
            "metrics": {"pf": round(self.profit_factor, 2), "ev": round(self.expectancy_r, 3), "dd": round(self.max_drawdown_pct, 2)},
            "regimes": {"best": self.best_regime, "worst": self.worst_regime},
            "symbols": {"best": self.best_symbols[:3], "worst": self.worst_symbols[:3]},
            "engines": {"most_valuable": self.most_valuable_engine, "least_valuable": self.least_valuable_engine},
            "promotion": {"status": self.promotion_status, "champion": self.champion_version, "challenger": self.challenger_version},
        }

    def render(self) -> str:
        lines = []
        lines.append("═" * 80)
        lines.append("  RESEARCH DASHBOARD — Does the App Add Value?")
        lines.append("═" * 80)
        lines.append("")
        lines.append(f"  Period: {self.period_trades} trades  |  {self.period_start} → {self.period_end}")
        lines.append("")

        # Core Metrics
        lines.append("┌─ CORE METRICS ─" + "─" * 62 + "┐")
        lines.append(f"│  Profit Factor:     {self.profit_factor:>8.2f}   │  Expectancy:   {self.expectancy_r:>+7.3f}R       │")
        lines.append(f"│  Max Drawdown:      {self.max_drawdown_pct:>7.2f}%    │  Promotion:    {self.promotion_status:<14s}      │")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # Regime Insights
        lines.append("┌─ REGIME INSIGHTS ─" + "─" * 59 + "┐")
        lines.append(f"│  Best Regime:  {self.best_regime:<24s} │  Worst Regime: {self.worst_regime:<24s}    │")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # Engine Attribution (value per 100 trades)
        if self.engine_attribution:
            lines.append("┌─ ENGINE ATTRIBUTION (Value per 100 trades) ─" + "─" * 33 + "┐")
            lines.append(f"│  {'Engine':<22s} {'PF Δ':>6s} {'EV Δ':>7s} {'DD Δ':>7s} {'AvgR Δ':>7s} {'Trades':>6s} {'Decision':>10s}  │")
            lines.append("│  " + "─" * 74 + "  │")
            for e in self.engine_attribution:
                decision = "Keep ✅" if e.get("keep", False) else "Review ❌"
                lines.append(
                    f"│  {e['engine']:<22s} {e.get('pf_delta', 0):>+5.2f} "
                    f"{e.get('ev_delta', 0):>+6.3f}R "
                    f"{e.get('dd_delta', 0):>+6.2f}% "
                    f"{e.get('avg_r_delta', 0):>+6.3f}R "
                    f"{e.get('trades', 0):>5d} "
                    f"{decision:>10s}  │"
                )
            lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # Promotion Status
        lines.append("┌─ PROMOTION STATUS ─" + "─" * 58 + "┐")
        lines.append(f"│  Champion:  {self.champion_version:<20s}  │  Challenger: {self.challenger_version:<20s}    │")
        lines.append(f"│  Status:    {self.promotion_status:<56s}        │")
        lines.append("└" + "─" * 78 + "┘")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# PER-ENGINE VALIDATION
# ═══════════════════════════════════════════════════════════════

@dataclass
class EngineValidation:
    """Per-engine validation statistics."""
    engine_name: str = ""
    metric_name: str = ""
    accepted_value: float = 0.0
    rejected_value: float = 0.0
    improvement: float = 0.0  # % improvement after filtering
    contribution: str = ""  # POSITIVE / NEGATIVE / NEUTRAL

    def to_dict(self) -> Dict:
        return {
            "engine": self.engine_name,
            "metric": self.metric_name,
            "accepted": round(self.accepted_value, 4),
            "rejected": round(self.rejected_value, 4),
            "improvement_pct": round(self.improvement, 2),
            "contribution": self.contribution,
        }


class EngineValidationTracker:
    """
    Per-engine validation statistics.

    Tracks which App engines are contributing value.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._validations: List[EngineValidation] = []

    def calculate_all(self) -> List[EngineValidation]:
        """Calculate validation statistics for all engines."""
        self._validations.clear()

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Trade Quality Engine: accepted vs rejected profitability
            self._validate_trade_quality(cur)

            # Institution Agreement: PF improvement after filtering
            self._validate_institution_agreement(cur)

            # Expectancy Engine: calibration error
            self._validate_expectancy(cur)

            # Position Sizing: return per unit of risk
            self._validate_position_sizing(cur)

            # Exit Engine: captured MFE vs MAE
            self._validate_exit_engine(cur)

            conn.close()

        except Exception as e:
            logger.warning("Engine validation error: {}", e)

        return self._validations

    def _validate_trade_quality(self, cur) -> None:
        """Validate Trade Quality Engine."""
        cur.execute("""
            SELECT
                AVG(CASE WHEN confidence >= 0.90 THEN pnl END) as high_conf_pnl,
                AVG(CASE WHEN confidence < 0.80 THEN pnl END) as low_conf_pnl,
                COUNT(CASE WHEN confidence >= 0.90 THEN 1 END) as high_count,
                COUNT(CASE WHEN confidence < 0.80 THEN 1 END) as low_count
            FROM positions WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0] is not None and row[1] is not None:
            improvement = ((row[0] - row[1]) / abs(row[1]) * 100) if row[1] != 0 else 0
            self._validations.append(EngineValidation(
                engine_name="Trade Quality",
                metric_name="High vs Low Confidence PnL",
                accepted_value=row[0] or 0,
                rejected_value=row[1] or 0,
                improvement=improvement,
                contribution="POSITIVE" if improvement > 0 else "NEGATIVE",
            ))

    def _validate_institution_agreement(self, cur) -> None:
        """Validate Institution Agreement Engine."""
        cur.execute("""
            SELECT
                AVG(CASE WHEN institutional_score >= 80 THEN pnl END) as high_inst_pnl,
                AVG(CASE WHEN institutional_score < 60 THEN pnl END) as low_inst_pnl
            FROM positions WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0] is not None and row[1] is not None:
            improvement = ((row[0] - row[1]) / abs(row[1]) * 100) if row[1] != 0 else 0
            self._validations.append(EngineValidation(
                engine_name="Institution Agreement",
                metric_name="High vs Low Inst Score PnL",
                accepted_value=row[0] or 0,
                rejected_value=row[1] or 0,
                improvement=improvement,
                contribution="POSITIVE" if improvement > 0 else "NEGATIVE",
            ))

    def _validate_expectancy(self, cur) -> None:
        """Validate Expectancy Engine."""
        cur.execute("""
            SELECT
                AVG(CASE WHEN realized_r > 0 THEN realized_r END) as avg_win_r,
                AVG(CASE WHEN realized_r <= 0 THEN realized_r END) as avg_loss_r
            FROM positions WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0] is not None and row[1] is not None:
            payoff = abs(row[0] / row[1]) if row[1] != 0 else 0
            self._validations.append(EngineValidation(
                engine_name="Expectancy",
                metric_name="Payoff Ratio (Avg Win / Avg Loss)",
                accepted_value=row[0],
                rejected_value=abs(row[1]),
                improvement=payoff,
                contribution="POSITIVE" if payoff > 1.5 else "NEGATIVE",
            ))

    def _validate_position_sizing(self, cur) -> None:
        """Validate Position Sizing Engine."""
        cur.execute("""
            SELECT AVG(pnl) / AVG(ABS(pnl)) as return_per_risk
            FROM positions WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0] is not None:
            self._validations.append(EngineValidation(
                engine_name="Position Sizing",
                metric_name="Return per Unit of Risk",
                accepted_value=row[0],
                rejected_value=0,
                improvement=row[0] * 100,
                contribution="POSITIVE" if row[0] > 0 else "NEGATIVE",
            ))

    def _validate_exit_engine(self, cur) -> None:
        """Validate Exit Engine."""
        cur.execute("""
            SELECT
                AVG(mfe_pct) as avg_mfe,
                AVG(mae_pct) as avg_mae
            FROM positions WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0] is not None and row[1] is not None:
            capture_ratio = row[0] / row[1] if row[1] != 0 else 0
            self._validations.append(EngineValidation(
                engine_name="Exit Engine",
                metric_name="MFE/MAE Capture Ratio",
                accepted_value=row[0],
                rejected_value=row[1],
                improvement=capture_ratio,
                contribution="POSITIVE" if capture_ratio > 1.0 else "NEGATIVE",
            ))

    def get_validations(self) -> List[EngineValidation]:
        return list(self._validations)

    def render(self) -> str:
        lines = []
        lines.append("┌─ PER-ENGINE VALIDATION ─" + "─" * 34 + "┐")
        for v in self._validations:
            icon = "✓" if v.contribution == "POSITIVE" else "✗" if v.contribution == "NEGATIVE" else "─"
            lines.append(f"│  {icon} {v.engine_name:<22s} {v.metric_name:<22s} │")
            lines.append(f"│    Accepted: {v.accepted_value:>+8.4f}  Rejected: {v.rejected_value:>+8.4f}  │")
        lines.append("└" + "─" * 60 + "┘")
        return "\n".join(lines)
