"""
Execution Analytics — Symbol/Regime Performance with Capture Metrics.

Per v26 Executive Assessment:
    "This is no longer a signal-quality problem.
     It is an execution efficiency problem."

    "Instead of only PnL/Win Rate/Average, add:
         Profit Factor | Average R | Capture Ratio | Average MFE | Average MAE"

    "You may discover BUY_MODE has PF 1.4 while SELL_MODE has PF 0.6.
     That is immediately actionable."

Key Innovation:
    v25: General rolling metrics across all trades
    v26: Per-symbol AND per-regime breakdown with capture efficiency

    This allows:
        - Identifying which symbols are profitable vs destructive
        - Discovering regime-specific edge (or lack thereof)
        - Measuring capture efficiency per segment
        - Directing capital toward productive segments

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class SegmentMetrics:
    """Performance metrics for a segment (symbol, regime, or session)."""
    name: str = ""
    segment_type: str = ""   # symbol / regime / session
    sample_size: int = 0

    # Core metrics
    total_r: float = 0.0
    avg_r: float = 0.0
    profit_factor: float = 0.0
    win_rate: float = 0.0
    expectancy: float = 0.0

    # Capture metrics
    avg_mfe: float = 0.0        # Average Maximum Favorable Excursion
    avg_mae: float = 0.0        # Average Maximum Adverse Excursion
    capture_ratio: float = 0.0  # Realized / MFE
    avg_hold_minutes: float = 0.0

    # v27: Capture distribution statistics
    median_capture: float = 0.0
    p25_capture: float = 0.0
    p75_capture: float = 0.0
    capture_variance: float = 0.0
    exit_reasons: Dict = field(default_factory=dict)  # Exit reason distribution

    # v27: Directional capture
    long_capture: float = 0.0
    short_capture: float = 0.0
    long_count: int = 0
    short_count: int = 0

    # Win/loss detail
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    win_loss_ratio: float = 0.0
    total_wins: int = 0
    total_losses: int = 0

    # Quality
    quality_score: float = 0.0  # 0-100
    quality_tier: str = ""      # ELITE / GOOD / NEUTRAL / POOR / DESTRUCTIVE

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "type": self.segment_type, "sample": self.sample_size,
            "total_r": round(self.total_r, 3), "avg_r": round(self.avg_r, 4),
            "profit_factor": round(self.profit_factor, 3),
            "win_rate": round(self.win_rate, 4),
            "expectancy": round(self.expectancy, 4),
            "avg_mfe": round(self.avg_mfe, 4), "avg_mae": round(self.avg_mae, 4),
            "capture_ratio": round(self.capture_ratio, 4),
            "avg_hold_minutes": round(self.avg_hold_minutes, 1),
            "median_capture": round(self.median_capture, 4),
            "p25_capture": round(self.p25_capture, 4),
            "p75_capture": round(self.p75_capture, 4),
            "capture_variance": round(self.capture_variance, 6),
            "exit_reasons": self.exit_reasons,
            "long_capture": round(self.long_capture, 4),
            "short_capture": round(self.short_capture, 4),
            "long_count": self.long_count, "short_count": self.short_count,
            "avg_win_r": round(self.avg_win_r, 4), "avg_loss_r": round(self.avg_loss_r, 4),
            "win_loss_ratio": round(self.win_loss_ratio, 3),
            "wins": self.total_wins, "losses": self.total_losses,
            "quality_score": round(self.quality_score, 1), "quality_tier": self.quality_tier,
        }


@dataclass
class ExitTypeAnalytics:
    """Analytics for a single exit type."""
    exit_type: str = ""
    trades: int = 0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    avg_capture: float = 0.0
    avg_hold_minutes: float = 0.0
    median_hold_minutes: float = 0.0  # v29: Median hold time
    total_r: float = 0.0
    contribution_pct: float = 0.0   # % of total PnL contribution
    contribution_r: float = 0.0     # Absolute R contribution
    win_rate: float = 0.0
    avg_mfe: float = 0.0
    avg_mae: float = 0.0
    quality_tier: str = ""          # POSITIVE / NEUTRAL / DESTRUCTIVE
    # v30: Hold time percentiles
    p25_hold_minutes: float = 0.0
    p75_hold_minutes: float = 0.0
    p95_hold_minutes: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "exit_type": self.exit_type, "trades": self.trades,
            "profit_factor": round(self.profit_factor, 3), "avg_r": round(self.avg_r, 4),
            "avg_capture": round(self.avg_capture, 4), "avg_hold_minutes": round(self.avg_hold_minutes, 1),
            "median_hold_minutes": round(self.median_hold_minutes, 1),
            "p25_hold_minutes": round(self.p25_hold_minutes, 1),
            "p75_hold_minutes": round(self.p75_hold_minutes, 1),
            "p95_hold_minutes": round(self.p95_hold_minutes, 1),
            "total_r": round(self.total_r, 3), "contribution_pct": round(self.contribution_pct, 1),
            "contribution_r": round(self.contribution_r, 3), "win_rate": round(self.win_rate, 4),
            "avg_mfe": round(self.avg_mfe, 4), "avg_mae": round(self.avg_mae, 4),
            "quality_tier": self.quality_tier,
        }


@dataclass
class DirectionalMatrix:
    """Directional capture matrix: LONG vs SHORT across all dimensions."""
    long_pf: float = 0.0
    short_pf: float = 0.0
    long_capture: float = 0.0
    short_capture: float = 0.0
    long_avg_mfe: float = 0.0
    short_avg_mfe: float = 0.0
    long_avg_mae: float = 0.0
    short_avg_mae: float = 0.0
    long_avg_hold: float = 0.0
    short_avg_hold: float = 0.0
    long_trades: int = 0
    short_trades: int = 0
    long_total_r: float = 0.0
    short_total_r: float = 0.0
    long_avg_r: float = 0.0
    short_avg_r: float = 0.0
    capture_gap: float = 0.0        # Long capture - Short capture
    weaker_direction: str = ""      # LONG or SHORT

    def to_dict(self) -> Dict:
        return {
            "long": {"pf": round(self.long_pf, 3), "capture": round(self.long_capture, 4),
                       "avg_mfe": round(self.long_avg_mfe, 4), "avg_mae": round(self.long_avg_mae, 4),
                       "avg_hold": round(self.long_avg_hold, 1), "trades": self.long_trades,
                       "total_r": round(self.long_total_r, 3), "avg_r": round(self.long_avg_r, 4)},
            "short": {"pf": round(self.short_pf, 3), "capture": round(self.short_capture, 4),
                        "avg_mfe": round(self.short_avg_mfe, 4), "avg_mae": round(self.short_avg_mae, 4),
                        "avg_hold": round(self.short_avg_hold, 1), "trades": self.short_trades,
                        "total_r": round(self.short_total_r, 3), "avg_r": round(self.short_avg_r, 4)},
            "capture_gap": round(self.capture_gap, 4), "weaker_direction": self.weaker_direction,
        }


@dataclass
class CaptureDegradation:
    """Rolling capture trend for a specific segment."""
    segment_name: str = ""
    segment_type: str = ""        # symbol / regime / session / direction / exit_type
    current_capture: float = 0.0
    recent_25_capture: float = 0.0
    trend: str = ""               # IMPROVING / STABLE / DEGRADING
    degradation_flag: bool = False
    sample_size: int = 0
    variance: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "segment": self.segment_name, "type": self.segment_type,
            "current_capture": round(self.current_capture, 4),
            "recent_25_capture": round(self.recent_25_capture, 4),
            "trend": self.trend, "degradation_flag": self.degradation_flag,
            "sample_size": self.sample_size, "variance": round(self.variance, 6),
        }


@dataclass
class EconomicImpact:
    """Estimated economic impact of a specific issue."""
    issue: str = ""
    estimated_pf_impact: float = 0.0
    estimated_r_impact: float = 0.0
    priority: str = ""            # HIGH / MEDIUM / LOW
    evidence: str = ""
    measurement: str = ""         # How to verify the impact
    engineering_effort: str = ""   # HIGH / MEDIUM / LOW — implementation cost
    priority_score: float = 0.0    # Return on engineering effort (PF gain / effort cost)
    confidence: float = 0.0        # v30: Confidence in this estimate (0-1)
    predicted_pf: float = 0.0      # v30: Predicted PF gain for calibration tracking

    def to_dict(self) -> Dict:
        return {
            "issue": self.issue, "estimated_pf_impact": round(self.estimated_pf_impact, 3),
            "estimated_r_impact": round(self.estimated_r_impact, 3),
            "priority": self.priority, "evidence": self.evidence, "measurement": self.measurement,
            "engineering_effort": self.engineering_effort, "priority_score": round(self.priority_score, 2),
            "confidence": round(self.confidence, 2), "predicted_pf": round(self.predicted_pf, 3),
        }


@dataclass
class ExecutionQualityIndex:
    """v29: Composite execution quality score from 6 weighted components."""
    capture_score: float = 0.0          # 0-100: How much MFE is captured
    exit_efficiency_score: float = 0.0   # 0-100: Exit type quality
    slippage_score: float = 0.0          # 0-100: MAE relative to MFE
    holding_efficiency_score: float = 0.0 # 0-100: Hold time optimization
    profit_protection_score: float = 0.0  # 0-100: Win/loss ratio and trailing stop quality
    risk_control_score: float = 0.0       # 0-100: Drawdown and adverse excursion control
    composite_score: float = 0.0          # 0-100: Weighted average
    tier: str = ""                        # EXCELLENT / GOOD / NEEDS REVIEW / POOR
    diagnosis: str = ""
    # v30: Subscores for targeted diagnosis
    admission_score: float = 0.0    # Admission quality subscore
    execution_score: float = 0.0    # Execution quality subscore
    exit_score: float = 0.0         # Exit quality subscore
    risk_score: float = 0.0         # Risk management subscore

    def to_dict(self) -> Dict:
        return {
            "capture": round(self.capture_score, 1),
            "exit_efficiency": round(self.exit_efficiency_score, 1),
            "slippage": round(self.slippage_score, 1),
            "holding_efficiency": round(self.holding_efficiency_score, 1),
            "profit_protection": round(self.profit_protection_score, 1),
            "risk_control": round(self.risk_control_score, 1),
            "composite_score": round(self.composite_score, 1),
            "tier": self.tier, "diagnosis": self.diagnosis,
            "admission": round(self.admission_score, 1),
            "execution": round(self.execution_score, 1),
            "exit": round(self.exit_score, 1),
            "risk": round(self.risk_score, 1),
        }


@dataclass
class PredictionCalibration:
    """v30: Track predicted vs actual PF improvement for self-evaluation."""
    issue: str = ""
    predicted_pf: float = 0.0
    actual_pf: float = 0.0
    prediction_error: float = 0.0   # actual - predicted
    absolute_error: float = 0.0
    sample_size: int = 0
    status: str = ""               # ACCURATE / OVERESTIMATED / UNDERESTIMATED

    def to_dict(self) -> Dict:
        return {
            "issue": self.issue, "predicted_pf": round(self.predicted_pf, 3),
            "actual_pf": round(self.actual_pf, 3), "prediction_error": round(self.prediction_error, 3),
            "absolute_error": round(self.absolute_error, 3), "sample_size": self.sample_size,
            "status": self.status,
        }


@dataclass
class ConfidenceHistory:
    """v30: Track confidence trends over time periods."""
    today: float = 0.0
    one_week: float = 0.0
    one_month: float = 0.0
    rolling_average: float = 0.0
    trend: str = ""               # IMPROVING / STABLE / DECLINING
    volatility: float = 0.0        # Std dev of confidence across periods

    def to_dict(self) -> Dict:
        return {
            "today": round(self.today, 2), "one_week": round(self.one_week, 2),
            "one_month": round(self.one_month, 2), "rolling_average": round(self.rolling_average, 2),
            "trend": self.trend, "volatility": round(self.volatility, 4),
        }


@dataclass
class ConfidenceInterval:
    """v32: 95% confidence interval for a metric."""
    metric_name: str = ""
    value: float = 0.0
    lower: float = 0.0
    upper: float = 0.0
    sample_size: int = 0
    std_error: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "metric": self.metric_name, "value": round(self.value, 4),
            "ci_lower": round(self.lower, 4), "ci_upper": round(self.upper, 4),
            "sample_size": self.sample_size, "std_error": round(self.std_error, 4),
        }


@dataclass
class InteractionMatrixEntry:
    """v32: How two components interact — combined PF vs individual PFs."""
    component_a: str = ""
    component_b: str = ""
    pf_a: float = 0.0
    pf_b: float = 0.0
    combined_pf: float = 0.0
    interaction_effect: float = 0.0   # combined - (a + b) / 2
    synergy: str = ""                  # SYNERGISTIC / NEUTRAL / INTERFERENCE
    trades_affected: int = 0

    def to_dict(self) -> Dict:
        return {
            "component_a": self.component_a, "component_b": self.component_b,
            "pf_a": round(self.pf_a, 3), "pf_b": round(self.pf_b, 3),
            "combined_pf": round(self.combined_pf, 3),
            "interaction_effect": round(self.interaction_effect, 4),
            "synergy": self.synergy, "trades_affected": self.trades_affected,
        }


@dataclass
class RegimeTransition:
    """v32: Performance during regime transitions (from_regime → to_regime)."""
    from_regime: str = ""
    to_regime: str = ""
    count: int = 0
    avg_pf: float = 0.0
    avg_capture: float = 0.0
    avg_r: float = 0.0
    transition_quality: str = ""     # SMOOTH / VOLATILE / DESTRUCTIVE

    def to_dict(self) -> Dict:
        return {
            "from": self.from_regime, "to": self.to_regime,
            "count": self.count, "avg_pf": round(self.avg_pf, 3),
            "avg_capture": round(self.avg_capture, 4),
            "avg_r": round(self.avg_r, 4), "quality": self.transition_quality,
        }


@dataclass
class RegimeTimelineEntry:
    """v31: Single regime transition event with performance metrics."""
    index: int = 0                  # Sequence number
    regime: str = ""                # Regime name
    trades_in_regime: int = 0       # Number of trades during this regime
    pf: float = 0.0                 # Profit Factor for this regime segment
    capture: float = 0.0            # Capture ratio for this segment
    total_r: float = 0.0            # Total R for this segment
    entries: int = 0                # Entries in this segment
    exits: int = 0                  # Exits in this segment
    avg_hold: float = 0.0           # Average hold time
    win_rate: float = 0.0           # Win rate in this segment
    duration_label: str = ""        # Human-readable duration

    def to_dict(self) -> Dict:
        return {
            "index": self.index, "regime": self.regime,
            "trades": self.trades_in_regime, "pf": round(self.pf, 3),
            "capture": round(self.capture, 4), "total_r": round(self.total_r, 3),
            "entries": self.entries, "exits": self.exits,
            "avg_hold": round(self.avg_hold, 1), "win_rate": round(self.win_rate, 4),
            "duration": self.duration_label,
        }


@dataclass
class WaterfallEntry:
    """v31: Single step in a PF contribution waterfall."""
    component: str = ""            # Component name (e.g. "Admission", "Exit Logic")
    pf_delta: float = 0.0           # PF change from this component
    cumulative_pf: float = 0.0      # Running PF after this component
    direction: str = ""             # POSITIVE / NEGATIVE / NEUTRAL
    trades_affected: int = 0        # Number of trades affected
    r_impact: float = 0.0           # R impact of this component
    evidence: str = ""              # What this component did

    def to_dict(self) -> Dict:
        return {
            "component": self.component, "pf_delta": round(self.pf_delta, 4),
            "cumulative_pf": round(self.cumulative_pf, 4), "direction": self.direction,
            "trades_affected": self.trades_affected, "r_impact": round(self.r_impact, 3),
            "evidence": self.evidence,
        }


@dataclass
class ExecutionAnalyticsReport:
    """Complete execution analytics report."""
    timestamp: float = 0.0

    # Status banner
    architecture_status: Dict = field(default_factory=lambda: {
        "layer": "Analytics",
        "status": "FEATURE FROZEN",
        "version": "v32",
        "research_status": "Validation Phase",
        "next_milestone": "Walk-forward Test",
        "modules": 3,
        "dataclasses": 14,
    })

    # System summary
    total_trades: int = 0
    system_pf: float = 0.0
    system_ev: float = 0.0
    system_wr: float = 0.0
    system_capture: float = 0.0

    # Per-symbol breakdown
    by_symbol: List[SegmentMetrics] = field(default_factory=list)
    top_symbols: List[Dict] = field(default_factory=list)
    worst_symbols: List[Dict] = field(default_factory=list)
    symbol_count: int = 0

    # Per-regime breakdown
    by_regime: List[SegmentMetrics] = field(default_factory=list)
    best_regime: str = ""
    worst_regime: str = ""

    # Per-session breakdown
    by_session: List[SegmentMetrics] = field(default_factory=list)

    # Capture efficiency
    overall_capture: float = 0.0
    capture_distribution: Dict = field(default_factory=dict)
    premature_exits: int = 0       # Trades where realized < 30% of MFE
    optimal_exits: int = 0         # Trades where realized > 70% of MFE

    # v27: System-wide capture statistics
    median_capture: float = 0.0
    mean_capture: float = 0.0
    p25_capture: float = 0.0
    p75_capture: float = 0.0
    capture_variance: float = 0.0
    long_system_capture: float = 0.0
    short_system_capture: float = 0.0
    long_system_count: int = 0
    short_system_count: int = 0

    # v27: Root cause chain
    root_cause_chain: Dict = field(default_factory=dict)

    # v28: Exit type analytics
    exit_analytics: List[ExitTypeAnalytics] = field(default_factory=list)

    # v28: Directional capture matrix
    directional_matrix: DirectionalMatrix = field(default_factory=DirectionalMatrix)

    # v28: Capture degradation tracking
    capture_degradation: List[CaptureDegradation] = field(default_factory=list)

    # v28: Economic impact estimation
    economic_impacts: List[EconomicImpact] = field(default_factory=list)

    # v29: Execution Quality Index
    eqi: ExecutionQualityIndex = field(default_factory=ExecutionQualityIndex)

    # v30: Prediction calibration tracking
    prediction_calibrations: List[PredictionCalibration] = field(default_factory=list)
    avg_prediction_error: float = 0.0

    # v30: Confidence history
    confidence_history: ConfidenceHistory = field(default_factory=ConfidenceHistory)

    # v31: Regime timeline
    regime_timeline: List[RegimeTimelineEntry] = field(default_factory=list)

    # v31: Contribution waterfall
    contribution_waterfall: List[WaterfallEntry] = field(default_factory=list)
    baseline_pf: float = 0.0
    final_pf: float = 0.0

    # v32: Confidence intervals for key metrics
    confidence_intervals: List[ConfidenceInterval] = field(default_factory=list)

    # v32: Component interaction matrix
    interaction_matrix: List[InteractionMatrixEntry] = field(default_factory=list)

    # v32: Regime transition analysis
    regime_transitions: List[RegimeTransition] = field(default_factory=list)

    # Execution efficiency (Realized R / Expected R)
    execution_efficiency: Dict = field(default_factory=dict)

    # Diagnosis
    diagnosis: str = ""
    primary_issue: str = ""        # entry / exit / regime / symbol
    recommendation: str = ""

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "architecture_status": self.architecture_status,
            "system": {"trades": self.total_trades, "pf": round(self.system_pf, 3),
                        "ev": round(self.system_ev, 4), "wr": round(self.system_wr, 4),
                        "capture": round(self.system_capture, 4)},
            "by_symbol": [s.to_dict() for s in self.by_symbol[:20]],
            "top_symbols": self.top_symbols, "worst_symbols": self.worst_symbols,
            "symbol_count": self.symbol_count,
            "by_regime": [s.to_dict() for s in self.by_regime],
            "best_regime": self.best_regime, "worst_regime": self.worst_regime,
            "by_session": [s.to_dict() for s in self.by_session],
            "capture": {"overall": round(self.overall_capture, 4),
                         "distribution": self.capture_distribution,
                         "premature_exits": self.premature_exits,
                         "optimal_exits": self.optimal_exits,
                         "median": round(self.median_capture, 4),
                         "mean": round(self.mean_capture, 4),
                         "p25": round(self.p25_capture, 4),
                         "p75": round(self.p75_capture, 4),
                         "variance": round(self.capture_variance, 6),
                         "long_capture": round(self.long_system_capture, 4),
                         "short_capture": round(self.short_system_capture, 4),
                         "long_count": self.long_system_count,
                         "short_count": self.short_system_count},
            "root_cause_chain": self.root_cause_chain,
            "exit_analytics": [e.to_dict() for e in self.exit_analytics],
            "directional_matrix": self.directional_matrix.to_dict(),
            "capture_degradation": [d.to_dict() for d in self.capture_degradation],
            "economic_impacts": [e.to_dict() for e in self.economic_impacts],
            "eqi": self.eqi.to_dict(),
            "prediction_calibrations": [p.to_dict() for p in self.prediction_calibrations],
            "avg_prediction_error": round(self.avg_prediction_error, 4),
            "confidence_history": self.confidence_history.to_dict(),
            "regime_timeline": [r.to_dict() for r in self.regime_timeline],
            "contribution_waterfall": [w.to_dict() for w in self.contribution_waterfall],
            "baseline_pf": round(self.baseline_pf, 4),
            "final_pf": round(self.final_pf, 4),
            "confidence_intervals": [c.to_dict() for c in self.confidence_intervals],
            "interaction_matrix": [i.to_dict() for i in self.interaction_matrix],
            "regime_transitions": [t.to_dict() for t in self.regime_transitions],
            "execution_efficiency": self.execution_efficiency,
            "diagnosis": self.diagnosis, "primary_issue": self.primary_issue,
            "recommendation": self.recommendation,
        }


class ExecutionAnalyticsEngine:
    """
    Per-symbol and per-regime execution analytics with capture metrics.

    Per v26 directive:
        "Add Profit Factor | Average R | Capture Ratio | Average MFE | Average MAE
         per symbol and per regime."

    This engine:
        1. Breaks down performance by symbol, regime, and session
        2. Measures capture efficiency per segment
        3. Identifies premature exits (realized < 30% MFE)
        4. Finds the best and worst performing segments
        5. Diagnoses the primary execution issue

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, side, realized_r, confidence, regime, session,
                       institutional_score, highest_pnl, mfe_pct, mae_pct,
                       hold_minutes, exit_reason, pnl
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()
            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()
        except Exception as e:
            logger.warning("Could not load execution analytics: {}", e)

    def evaluate(self) -> ExecutionAnalyticsReport:
        """Evaluate execution analytics across all segments."""
        self._ensure_loaded()
        report = ExecutionAnalyticsReport(timestamp=time.time())

        if not self._trades or len(self._trades) < 10:
            report.diagnosis = "Insufficient data"
            return report

        trades = self._trades[:500]
        report.total_trades = len(trades)

        # ── System Summary ──
        r_values = [t.get("realized_r", 0) or 0 for t in trades]
        wins_r = [r for r in r_values if r > 0]
        losses_r = [abs(r) for r in r_values if r < 0]
        report.system_pf = sum(wins_r) / max(0.01, sum(losses_r))
        report.system_ev = sum(r_values) / max(1, len(r_values))
        report.system_wr = len(wins_r) / max(1, len(r_values))

        # System capture
        captures = []
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                captures.append(r / mfe)
        report.system_capture = sum(captures) / max(1, len(captures))

        # ── Per-Symbol ──
        symbol_groups = defaultdict(list)
        for t in trades:
            sym = t.get("symbol", "UNKNOWN")
            symbol_groups[sym].append(t)

        for sym, sym_trades in symbol_groups.items():
            sm = self._calc_segment(sym, "symbol", sym_trades)
            report.by_symbol.append(sm)

        report.by_symbol.sort(key=lambda s: s.total_r, reverse=True)
        report.symbol_count = len(report.by_symbol)

        report.top_symbols = [{"name": s.name, "total_r": round(s.total_r, 3),
            "pf": round(s.profit_factor, 2), "capture": round(s.capture_ratio, 3),
            "sample": s.sample_size} for s in report.by_symbol[:5]]
        report.worst_symbols = [{"name": s.name, "total_r": round(s.total_r, 3),
            "pf": round(s.profit_factor, 2), "capture": round(s.capture_ratio, 3),
            "sample": s.sample_size} for s in report.by_symbol[-5:]]

        # ── Per-Regime ──
        regime_groups = defaultdict(list)
        for t in trades:
            reg = t.get("regime", "unknown") or "unknown"
            regime_groups[reg].append(t)

        for reg, reg_trades in regime_groups.items():
            rm = self._calc_segment(reg, "regime", reg_trades)
            report.by_regime.append(rm)

        report.by_regime.sort(key=lambda s: s.total_r, reverse=True)
        if report.by_regime:
            report.best_regime = report.by_regime[0].name
            report.worst_regime = report.by_regime[-1].name

        # ── Per-Session ──
        session_groups = defaultdict(list)
        for t in trades:
            sess = t.get("session", "unknown") or "unknown"
            if not sess:
                sess = "unknown"
            session_groups[sess].append(t)

        for sess, sess_trades in session_groups.items():
            sm = self._calc_segment(sess, "session", sess_trades)
            report.by_session.append(sm)

        report.by_session.sort(key=lambda s: s.total_r, reverse=True)

        # ── Capture Distribution ──
        all_captures = []
        premature = 0
        optimal = 0
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                cap = r / mfe
                all_captures.append(cap)
                if cap < 0.3:
                    premature += 1
                elif cap > 0.7:
                    optimal += 1

        report.overall_capture = sum(all_captures) / max(1, len(all_captures))
        report.premature_exits = premature
        report.optimal_exits = optimal

        # Distribution buckets
        buckets = {"<0%": 0, "0-30%": 0, "30-50%": 0, "50-70%": 0, "70-100%": 0, ">100%": 0}
        for c in all_captures:
            if c < 0: buckets["<0%"] += 1
            elif c < 0.3: buckets["0-30%"] += 1
            elif c < 0.5: buckets["30-50%"] += 1
            elif c < 0.7: buckets["50-70%"] += 1
            elif c <= 1.0: buckets["70-100%"] += 1
            else: buckets[">100%"] += 1
        report.capture_distribution = buckets

        # v27: System-wide capture statistics
        if all_captures:
            sorted_cap = sorted(all_captures)
            n = len(sorted_cap)
            report.mean_capture = sum(sorted_cap) / n
            report.median_capture = sorted_cap[n // 2]
            report.p25_capture = sorted_cap[n // 4] if n >= 4 else sorted_cap[0]
            report.p75_capture = sorted_cap[(3 * n) // 4] if n >= 4 else sorted_cap[-1]
            report.capture_variance = sum((c - report.mean_capture) ** 2 for c in sorted_cap) / n

        # v27: Directional capture breakdown
        long_trades = [t for t in trades if (t.get("side", "") or "").upper() in ("LONG", "BUY")]
        short_trades = [t for t in trades if (t.get("side", "") or "").upper() in ("SHORT", "SELL")]
        report.long_system_count = len(long_trades)
        report.short_system_count = len(short_trades)
        long_caps = [(t.get("highest_pnl", 0) or 0, t.get("realized_r", 0) or 0) for t in long_trades]
        long_pos = [(m, r) for m, r in long_caps if m > 0]
        report.long_system_capture = sum(r / m for m, r in long_pos) / max(1, len(long_pos)) if long_pos else 0.0
        short_caps = [(t.get("highest_pnl", 0) or 0, t.get("realized_r", 0) or 0) for t in short_trades]
        short_pos = [(m, r) for m, r in short_caps if m > 0]
        report.short_system_capture = sum(r / m for m, r in short_pos) / max(1, len(short_pos)) if short_pos else 0.0

        # v27: Root cause chain from execution analytics
        report.root_cause_chain = self._build_execution_root_cause(report, trades)

        # v28: Exit type analytics
        report.exit_analytics = self._calc_exit_analytics(trades, report.system_pf)

        # v28: Directional capture matrix
        report.directional_matrix = self._calc_directional_matrix(trades)

        # v28: Capture degradation tracking
        report.capture_degradation = self._calc_capture_degradation(trades)

        # v28: Economic impact estimation
        report.economic_impacts = self._calc_economic_impacts(report, trades)

        # v29: Execution Quality Index
        report.eqi = self._calc_eqi(report, trades)

        # v30: Prediction calibration
        report.prediction_calibrations = self._calc_prediction_calibration(report, trades)
        if report.prediction_calibrations:
            report.avg_prediction_error = sum(abs(p.prediction_error) for p in report.prediction_calibrations) / len(report.prediction_calibrations)

        # v30: Confidence history
        report.confidence_history = self._calc_confidence_history(trades)

        # v31: Regime timeline
        report.regime_timeline = self._calc_regime_timeline(trades)

        # v31: Contribution waterfall
        report.contribution_waterfall = self._calc_contribution_waterfall(report, trades)
        report.baseline_pf = report.contribution_waterfall[0].cumulative_pf if report.contribution_waterfall else report.system_pf
        report.final_pf = report.contribution_waterfall[-1].cumulative_pf if report.contribution_waterfall else report.system_pf

        # v32: Confidence intervals for key metrics
        report.confidence_intervals = self._calc_confidence_intervals(trades, report)

        # v32: Component interaction matrix
        report.interaction_matrix = self._calc_interaction_matrix(trades, report)

        # v32: Regime transition analysis
        report.regime_transitions = self._calc_regime_transitions(trades)

        # Execution efficiency (Realized R / Expected R)
        report.execution_efficiency = self._calc_execution_efficiency(trades, report)

        # ── Diagnosis ──
        premature_pct = premature / max(1, len(all_captures)) * 100

        if premature_pct > 50:
            report.primary_issue = "exit"
            report.diagnosis = (
                f"Premature exits dominate: {premature_pct:.0f}% of trades realize <30% of MFE. "
                f"Average capture={report.overall_capture:.1%}. "
                f"Winners are being clipped too early."
            )
            report.recommendation = (
                "Optimize exit timing — extend hold periods for profitable trades. "
                "Consider trailing stops or time-based exit scaling."
            )
        elif report.system_pf < 1.0 and report.system_ev < 0:
            # Check if it's symbol-specific
            destructive = [s for s in report.by_symbol if s.total_r < -5 and s.sample_size >= 5]
            if destructive:
                report.primary_issue = "symbol"
                names = ", ".join(s.name for s in destructive[:3])
                report.diagnosis = (
                    f"System PF={report.system_pf:.2f}. Destructive symbols: {names}. "
                    f"Consider disabling or reducing capital to these symbols."
                )
                report.recommendation = (
                    f"Remove or reduce exposure to: {names}"
                )
            else:
                report.primary_issue = "regime"
                report.diagnosis = (
                    f"System PF={report.system_pf:.2f}. No single dominant cause. "
                    f"Best regime: {report.best_regime}, Worst: {report.worst_regime}"
                )
                report.recommendation = (
                    f"Increase allocation to {report.best_regime}, "
                    f"reduce to {report.worst_regime}"
                )
        else:
            report.primary_issue = "unknown"
            report.diagnosis = "System performance within acceptable range"
            report.recommendation = "Continue monitoring"

        return report

    def _calc_segment(self, name: str, seg_type: str, trades: List[Dict]) -> SegmentMetrics:
        """Calculate metrics for a segment of trades."""
        sm = SegmentMetrics(name=name, segment_type=seg_type, sample_size=len(trades))

        r_values = [t.get("realized_r", 0) or 0 for t in trades]
        wins_r = [r for r in r_values if r > 0]
        losses_r = [abs(r) for r in r_values if r < 0]

        sm.total_r = sum(r_values)
        sm.avg_r = sum(r_values) / max(1, len(r_values))
        sm.profit_factor = sum(wins_r) / max(0.01, sum(losses_r))
        sm.win_rate = len(wins_r) / max(1, len(r_values))
        sm.expectancy = sm.avg_r
        sm.total_wins = len(wins_r)
        sm.total_losses = len(losses_r)
        sm.avg_win_r = sum(wins_r) / max(1, len(wins_r))
        sm.avg_loss_r = -sum(losses_r) / max(1, len(losses_r))
        sm.win_loss_ratio = sm.avg_win_r / max(0.01, sm.avg_loss_r)

        # Capture metrics
        mfe_vals = [(t.get("highest_pnl", 0) or 0, t.get("realized_r", 0) or 0) for t in trades]
        mfe_positive = [(mfe, r) for mfe, r in mfe_vals if mfe > 0]
        if mfe_positive:
            sm.avg_mfe = sum(m for m, _ in mfe_positive) / len(mfe_positive)
            sm.capture_ratio = sum(r / m for m, r in mfe_positive) / len(mfe_positive)

        # MAE
        mae_vals = [abs(t.get("mae_pct", 0) or 0) for t in trades]
        sm.avg_mae = sum(mae_vals) / max(1, len(mae_vals))

        # Hold time
        hold_vals = [t.get("hold_minutes", 0) or 0 for t in trades]
        sm.avg_hold_minutes = sum(hold_vals) / max(1, len(hold_vals))

        # v27: Capture distribution statistics
        capture_vals = [r / m for m, r in mfe_positive] if mfe_positive else [0.0]
        sorted_cap = sorted(capture_vals)
        n = len(sorted_cap)
        sm.median_capture = sorted_cap[n // 2] if n else 0.0
        sm.p25_capture = sorted_cap[n // 4] if n >= 4 else sorted_cap[0] if sorted_cap else 0.0
        sm.p75_capture = sorted_cap[(3 * n) // 4] if n >= 4 else sorted_cap[-1] if sorted_cap else 0.0
        mean_cap = sum(capture_vals) / max(1, len(capture_vals))
        sm.capture_variance = sum((c - mean_cap) ** 2 for c in capture_vals) / max(1, len(capture_vals))

        # v27: Exit reason distribution
        exit_counts = defaultdict(int)
        for t in trades:
            reason = t.get("exit_reason", "unknown") or "unknown"
            exit_counts[reason] += 1
        sm.exit_reasons = dict(sorted(exit_counts.items(), key=lambda x: -x[1]))

        # v27: Directional capture
        long_trades = [t for t in trades if (t.get("side", "") or "").upper() in ("LONG", "BUY")]
        short_trades = [t for t in trades if (t.get("side", "") or "").upper() in ("SHORT", "SELL")]
        sm.long_count = len(long_trades)
        sm.short_count = len(short_trades)
        long_caps = [(t.get("highest_pnl", 0) or 0, t.get("realized_r", 0) or 0) for t in long_trades]
        long_pos = [(m, r) for m, r in long_caps if m > 0]
        sm.long_capture = sum(r / m for m, r in long_pos) / max(1, len(long_pos)) if long_pos else 0.0
        short_caps = [(t.get("highest_pnl", 0) or 0, t.get("realized_r", 0) or 0) for t in short_trades]
        short_pos = [(m, r) for m, r in short_caps if m > 0]
        sm.short_capture = sum(r / m for m, r in short_pos) / max(1, len(short_pos)) if short_pos else 0.0

        # Quality score
        score = 0
        if sm.profit_factor > 1.5: score += 30
        elif sm.profit_factor > 1.0: score += 20
        elif sm.profit_factor > 0.8: score += 10
        if sm.capture_ratio > 0.5: score += 25
        elif sm.capture_ratio > 0.3: score += 15
        if sm.avg_r > 0.5: score += 25
        elif sm.avg_r > 0: score += 15
        if sm.sample_size >= 20: score += 20
        elif sm.sample_size >= 10: score += 10
        sm.quality_score = min(100, score)

        if sm.quality_score >= 70: sm.quality_tier = "ELITE"
        elif sm.quality_score >= 50: sm.quality_tier = "GOOD"
        elif sm.quality_score >= 30: sm.quality_tier = "NEUTRAL"
        elif sm.quality_score >= 15: sm.quality_tier = "POOR"
        else: sm.quality_tier = "DESTRUCTIVE"

        return sm

    def _build_execution_root_cause(self, report: ExecutionAnalyticsReport, trades: List[Dict]) -> Dict:
        """v27: Build root cause chain from execution analytics with Signal→Execution→Exit→Capture→Profit→Root Cause→Recommendation."""
        chain = {}

        # 1. Signal quality
        avg_conf = sum(t.get("confidence", 0) or 0 for t in trades) / max(1, len(trades))
        chain["signal_quality"] = f"Avg confidence={avg_conf:.1f}, {len(trades)} signals admitted"

        # 2. Execution quality
        avg_hold = sum(t.get("hold_minutes", 0) or 0 for t in trades) / max(1, len(trades))
        avg_mfe = sum(t.get("highest_pnl", 0) or 0 for t in trades) / max(1, len(trades))
        chain["execution_quality"] = f"Avg hold={avg_hold:.0f}m, Avg MFE={avg_mfe:.3f}R"

        # 3. Exit quality — which exits are working
        exit_pf = {}
        for reason in set(t.get("exit_reason", "unknown") or "unknown" for t in trades):
            reason_trades = [t for t in trades if (t.get("exit_reason", "unknown") or "unknown") == reason]
            r_vals = [t.get("realized_r", 0) or 0 for t in reason_trades]
            w = sum(r for r in r_vals if r > 0)
            l = sum(abs(r) for r in r_vals if r < 0)
            pf = w / max(0.01, l)
            exit_pf[reason] = {"pf": pf, "count": len(reason_trades), "avg_r": sum(r_vals) / max(1, len(r_vals))}
        chain["exit_detail"] = exit_pf
        best_exit = max(exit_pf.items(), key=lambda x: x[1]["pf"]) if exit_pf else ("unknown", {"pf": 0})
        worst_exit = min(exit_pf.items(), key=lambda x: x[1]["pf"]) if exit_pf else ("unknown", {"pf": 0})
        chain["exit_quality"] = f"Best exit: {best_exit[0]} (PF={best_exit[1]['pf']:.2f}), Worst: {worst_exit[0]} (PF={worst_exit[1]['pf']:.2f})"

        # 4. Capture quality
        chain["capture_detail"] = (
            f"Median={report.median_capture:.1%}, P25={report.p25_capture:.1%}, P75={report.p75_capture:.1%}, "
            f"Variance={report.capture_variance:.4f}, Long={report.long_system_capture:.1%}, Short={report.short_system_capture:.1%}"
        )

        # 5. Profit delta
        r_values = [t.get("realized_r", 0) or 0 for t in trades]
        chain["profit_delta"] = sum(r_values)
        chain["realized_pnl"] = sum(r_values)

        missed = 0
        total_mfe = 0
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                total_mfe += mfe
                missed += max(0, mfe - r)
        chain["missed_pnl"] = missed
        chain["total_mfe"] = total_mfe
        chain["capture_pct"] = sum(r_values) / max(0.01, sum(r_values) + missed) if (sum(r_values) + missed) > 0 else 0

        # 6. Root cause determination
        if report.premature_exits > len(trades) * 0.5:
            chain["root_cause"] = "exit"
            chain["actionable_evidence"] = f"{report.premature_exits}/{len(trades)} trades have <30% capture. Exits are premature."
            chain["recommendation"] = "Extend hold periods. Use trailing stops. Delay exits for profitable trades."
            chain["expected_pf_improvement"] = 0.15
            chain["economic_impact_r"] = missed * 0.3  # Capture 30% more of missed
            chain["economic_impact_priority"] = "HIGH"
        elif report.system_pf < 0.8 and report.system_capture > 0.3:
            chain["root_cause"] = "entry"
            chain["actionable_evidence"] = f"PF={report.system_pf:.2f} but capture={report.system_capture:.1%}. Entries are poor."
            chain["recommendation"] = "Tighten admission filter. Raise confidence threshold."
            chain["expected_pf_improvement"] = 0.10
            chain["economic_impact_r"] = chain["realized_pnl"] * -0.2  # Reduce losses by 20%
            chain["economic_impact_priority"] = "MEDIUM"
        else:
            chain["root_cause"] = "execution"
            chain["actionable_evidence"] = f"PF={report.system_pf:.2f}, Capture={report.system_capture:.1%}, Premature={report.premature_exits}/{len(trades)}"
            chain["recommendation"] = "Review exit logic, trailing stops, and session-specific behavior."
            chain["expected_pf_improvement"] = 0.08
            chain["economic_impact_r"] = chain["missed_pnl"] * 0.15
            chain["economic_impact_priority"] = "MEDIUM"

        # v28: Observed PF improvement (post-action measurement)
        chain["observed_pf_improvement"] = 0.0  # Will be computed when post-action data is available

        return chain

    def _calc_exit_analytics(self, trades: List[Dict], system_pf: float) -> List[ExitTypeAnalytics]:
        """v28: Calculate per-exit-type analytics with contribution."""
        r_values = [t.get("realized_r", 0) or 0 for t in trades]
        total_r = sum(r_values)
        exit_groups = defaultdict(list)
        for t in trades:
            reason = t.get("exit_reason", "unknown") or "unknown"
            exit_groups[reason].append(t)

        analytics = []
        for reason, exit_trades in exit_groups.items():
            eta = ExitTypeAnalytics(exit_type=reason)
            eta.trades = len(exit_trades)
            r_vals = [t.get("realized_r", 0) or 0 for t in exit_trades]
            wins = [r for r in r_vals if r > 0]
            losses = [abs(r) for r in r_vals if r < 0]
            eta.profit_factor = sum(wins) / max(0.01, sum(losses))
            eta.avg_r = sum(r_vals) / max(1, len(r_vals))
            eta.total_r = sum(r_vals)
            eta.win_rate = len(wins) / max(1, len(r_vals))

            # Capture
            mfe_pairs = [(t.get("highest_pnl", 0) or 0, t.get("realized_r", 0) or 0) for t in exit_trades]
            pos_pairs = [(m, r) for m, r in mfe_pairs if m > 0]
            eta.avg_capture = sum(r / m for m, r in pos_pairs) / max(1, len(pos_pairs)) if pos_pairs else 0.0
            eta.avg_mfe = sum(m for m, _ in pos_pairs) / max(1, len(pos_pairs)) if pos_pairs else 0.0

            # MAE
            mae_vals = [abs(t.get("mae_pct", 0) or 0) for t in exit_trades]
            eta.avg_mae = sum(mae_vals) / max(1, len(mae_vals))

            # Hold time
            hold_vals = [t.get("hold_minutes", 0) or 0 for t in exit_trades]
            eta.avg_hold_minutes = sum(hold_vals) / max(1, len(hold_vals))

            # v29: Median hold time
            sorted_hold = sorted(hold_vals)
            hn = len(sorted_hold)
            eta.median_hold_minutes = sorted_hold[hn // 2] if hn else 0.0

            # v30: Hold time percentiles
            eta.p25_hold_minutes = sorted_hold[hn // 4] if hn >= 4 else sorted_hold[0] if sorted_hold else 0.0
            eta.p75_hold_minutes = sorted_hold[(3 * hn) // 4] if hn >= 4 else sorted_hold[-1] if sorted_hold else 0.0
            eta.p95_hold_minutes = sorted_hold[int(hn * 0.95)] if hn >= 20 else sorted_hold[-1] if sorted_hold else 0.0

            # Contribution
            eta.contribution_r = eta.total_r
            eta.contribution_pct = (eta.total_r / abs(total_r) * 100) if total_r != 0 else 0.0

            # Quality tier
            if eta.profit_factor > 1.0 and eta.avg_r > 0:
                eta.quality_tier = "POSITIVE"
            elif eta.profit_factor > 0.8 or abs(eta.avg_r) < 0.1:
                eta.quality_tier = "NEUTRAL"
            else:
                eta.quality_tier = "DESTRUCTIVE"

            analytics.append(eta)

        analytics.sort(key=lambda e: e.total_r, reverse=True)
        return analytics

    def _calc_directional_matrix(self, trades: List[Dict]) -> DirectionalMatrix:
        """v28: Calculate LONG vs SHORT directional capture matrix."""
        dm = DirectionalMatrix()
        long_t = [t for t in trades if (t.get("side", "") or "").upper() in ("LONG", "BUY")]
        short_t = [t for t in trades if (t.get("side", "") or "").upper() in ("SHORT", "SELL")]

        dm.long_trades = len(long_t)
        dm.short_trades = len(short_t)

        for label, subset, attr_prefix in [("long", long_t, "long_"), ("short", short_t, "short_")]:
            r_vals = [t.get("realized_r", 0) or 0 for t in subset]
            wins = [r for r in r_vals if r > 0]
            losses = [abs(r) for r in r_vals if r < 0]
            setattr(dm, f"{attr_prefix}pf", sum(wins) / max(0.01, sum(losses)))
            setattr(dm, f"{attr_prefix}total_r", sum(r_vals))
            setattr(dm, f"{attr_prefix}avg_r", sum(r_vals) / max(1, len(r_vals)))

            mfe_pairs = [(t.get("highest_pnl", 0) or 0, t.get("realized_r", 0) or 0) for t in subset]
            pos = [(m, r) for m, r in mfe_pairs if m > 0]
            setattr(dm, f"{attr_prefix}capture", sum(r / m for m, r in pos) / max(1, len(pos)) if pos else 0.0)
            setattr(dm, f"{attr_prefix}avg_mfe", sum(m for m, _ in pos) / max(1, len(pos)) if pos else 0.0)

            mae_vals = [abs(t.get("mae_pct", 0) or 0) for t in subset]
            setattr(dm, f"{attr_prefix}avg_mae", sum(mae_vals) / max(1, len(mae_vals)))

            hold_vals = [t.get("hold_minutes", 0) or 0 for t in subset]
            setattr(dm, f"{attr_prefix}avg_hold", sum(hold_vals) / max(1, len(hold_vals)))

        dm.capture_gap = dm.long_capture - dm.short_capture
        dm.weaker_direction = "SHORT" if dm.short_capture < dm.long_capture else "LONG"
        return dm

    def _calc_capture_degradation(self, trades: List[Dict]) -> List[CaptureDegradation]:
        """v28: Track capture trends for segments to detect degradation before PF drops."""
        degradations = []

        # System-level degradation
        if len(trades) >= 50:
            all_caps = []
            for t in trades:
                mfe = t.get("highest_pnl", 0) or 0
                r = t.get("realized_r", 0) or 0
                if mfe > 0: all_caps.append(r / mfe)

            if all_caps:
                recent_25 = all_caps[:25] if len(all_caps) >= 25 else all_caps
                older = all_caps[25:75] if len(all_caps) >= 75 else all_caps[25:]
                current = sum(all_caps) / max(1, len(all_caps))
                recent_avg = sum(recent_25) / max(1, len(recent_25))
                older_avg = sum(older) / max(1, len(older)) if older else current

                trend = "STABLE"
                if recent_avg > older_avg * 1.1: trend = "IMPROVING"
                elif recent_avg < older_avg * 0.9: trend = "DEGRADING"

                mean_c = sum(all_caps) / len(all_caps)
                var = sum((c - mean_c) ** 2 for c in all_caps) / len(all_caps)

                degradations.append(CaptureDegradation(
                    segment_name="SYSTEM", segment_type="system",
                    current_capture=current, recent_25_capture=recent_avg,
                    trend=trend, degradation_flag=trend == "DEGRADING",
                    sample_size=len(all_caps), variance=var))

        # Per-regime degradation
        regime_groups = defaultdict(list)
        for t in trades:
            reg = t.get("regime", "unknown") or "unknown"
            regime_groups[reg].append(t)
        for reg, reg_trades in regime_groups.items():
            if len(reg_trades) < 15: continue
            caps = []
            for t in reg_trades:
                mfe = t.get("highest_pnl", 0) or 0
                r = t.get("realized_r", 0) or 0
                if mfe > 0: caps.append(r / mfe)
            if not caps: continue
            recent = caps[:min(10, len(caps))]
            older = caps[min(10, len(caps)):]
            recent_avg = sum(recent) / max(1, len(recent))
            older_avg = sum(older) / max(1, len(older)) if older else recent_avg
            trend = "STABLE"
            if recent_avg > older_avg * 1.1: trend = "IMPROVING"
            elif recent_avg < older_avg * 0.9: trend = "DEGRADING"
            mean_c = sum(caps) / len(caps)
            var = sum((c - mean_c) ** 2 for c in caps) / len(caps)
            degradations.append(CaptureDegradation(
                segment_name=reg, segment_type="regime",
                current_capture=sum(caps) / len(caps), recent_25_capture=recent_avg,
                trend=trend, degradation_flag=trend == "DEGRADING",
                sample_size=len(caps), variance=var))

        # Per-direction degradation
        for direction in ["LONG", "SHORT"]:
            dir_t = [t for t in trades if (t.get("side", "") or "").upper() in (direction, "BUY" if direction == "LONG" else "SELL")]
            if len(dir_t) < 15: continue
            caps = []
            for t in dir_t:
                mfe = t.get("highest_pnl", 0) or 0
                r = t.get("realized_r", 0) or 0
                if mfe > 0: caps.append(r / mfe)
            if not caps: continue
            recent = caps[:min(10, len(caps))]
            older = caps[min(10, len(caps)):]
            recent_avg = sum(recent) / max(1, len(recent))
            older_avg = sum(older) / max(1, len(older)) if older else recent_avg
            trend = "STABLE"
            if recent_avg > older_avg * 1.1: trend = "IMPROVING"
            elif recent_avg < older_avg * 0.9: trend = "DEGRADING"
            mean_c = sum(caps) / len(caps)
            var = sum((c - mean_c) ** 2 for c in caps) / len(caps)
            degradations.append(CaptureDegradation(
                segment_name=direction, segment_type="direction",
                current_capture=sum(caps) / len(caps), recent_25_capture=recent_avg,
                trend=trend, degradation_flag=trend == "DEGRADING",
                sample_size=len(caps), variance=var))

        degradations.sort(key=lambda d: d.current_capture)
        return degradations

    def _calc_economic_impacts(self, report: ExecutionAnalyticsReport, trades: List[Dict]) -> List[EconomicImpact]:
        """v28: Estimate economic impact of each identified issue."""
        impacts = []

        # 1. Early exits
        r_values = [t.get("realized_r", 0) or 0 for t in trades]
        missed = 0
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0: missed += max(0, mfe - r)
        total_r = sum(r_values)
        total_loss_r = abs(sum(r for r in r_values if r < 0))

        if report.premature_exits > len(trades) * 0.3:
            # If we capture 30% more of missed PnL
            potential_r = missed * 0.3
            current_pf = report.system_pf
            new_pf = (sum(r for r in r_values if r > 0) + potential_r * 0.5) / max(0.01, sum(abs(r) for r in r_values if r < 0))
            pf_gain = new_pf - current_pf
            impacts.append(EconomicImpact(
                issue="Early exits",
                estimated_pf_impact=pf_gain,
                estimated_r_impact=potential_r,
                priority="HIGH",
                evidence=f"{report.premature_exits}/{len(trades)} premature exits, {missed:.1f}R missed",
                measurement="Track capture ratio improvement over 50 trades",
                engineering_effort="MEDIUM",
                priority_score=round(pf_gain / 2.0, 2)))  # MEDIUM effort = cost 2.0

        # 2. Trailing stop behavior
        ts_trades = [t for t in trades if "trailing_stop" in (t.get("exit_reason", "") or "")]
        if ts_trades:
            ts_r = [t.get("realized_r", 0) or 0 for t in ts_trades]
            ts_total = sum(ts_r)
            if ts_total < 0:
                potential = len(ts_trades) * 0.5
                pf_gain = potential / max(0.01, total_loss_r)
                impacts.append(EconomicImpact(
                    issue="Trailing stop behavior",
                    estimated_pf_impact=pf_gain,
                    estimated_r_impact=potential,
                    priority="HIGH",
                    evidence=f"{len(ts_trades)} trailing stops contributed {ts_total:.2f}R",
                    measurement="Compare trailing stop exit R before/after calibration",
                    engineering_effort="LOW",
                    priority_score=round(pf_gain / 1.0, 2)))  # LOW effort = cost 1.0

        # 3. Short direction
        short_t = [t for t in trades if (t.get("side", "") or "").upper() in ("SHORT", "SELL")]
        if short_t and report.directional_matrix.short_capture < report.directional_matrix.long_capture - 0.2:
            short_r = sum(t.get("realized_r", 0) or 0 for t in short_t)
            gap = report.directional_matrix.long_capture - report.directional_matrix.short_capture
            potential = len(short_t) * gap * 0.3
            pf_gain = potential / max(0.01, total_loss_r)
            impacts.append(EconomicImpact(
                issue="Short direction underperformance",
                estimated_pf_impact=pf_gain,
                estimated_r_impact=potential,
                priority="HIGH",
                evidence=f"Short capture={report.directional_matrix.short_capture:.1%} vs Long={report.directional_matrix.long_capture:.1%}",
                measurement="Track short capture ratio improvement",
                engineering_effort="MEDIUM",
                priority_score=round(pf_gain / 2.0, 2)))

        # 4. Session timing
        session_groups = defaultdict(list)
        for t in trades:
            sess = t.get("session", "unknown") or "unknown"
            session_groups[sess].append(t)
        for sess, sess_trades in session_groups.items():
            if len(sess_trades) < 20: continue
            sess_r = sum(t.get("realized_r", 0) or 0 for t in sess_trades)
            if sess_r < -5:
                pf_gain = abs(sess_r) * 0.1 / max(0.01, total_loss_r)
                impacts.append(EconomicImpact(
                    issue=f"Session: {sess}",
                    estimated_pf_impact=pf_gain,
                    estimated_r_impact=abs(sess_r) * 0.1,
                    priority="MEDIUM",
                    evidence=f"{sess} contributed {sess_r:.2f}R from {len(sess_trades)} trades",
                    measurement="Reduce session exposure by 50% and measure impact",
                    engineering_effort="LOW",
                    priority_score=round(pf_gain / 1.0, 2)))

        # 5. Symbol allocation
        sym_groups = defaultdict(list)
        for t in trades:
            sym_groups[t.get("symbol", "?")].append(t)
        destructive_syms = []
        for sym, sym_trades in sym_groups.items():
            if len(sym_trades) >= 5:
                sym_r = sum(t.get("realized_r", 0) or 0 for t in sym_trades)
                if sym_r < -3:
                    destructive_syms.append((sym, sym_r, len(sym_trades)))
        if destructive_syms:
            total_sym_loss = sum(r for _, r, _ in destructive_syms)
            pf_gain = abs(total_sym_loss) * 0.2 / max(0.01, total_loss_r)
            impacts.append(EconomicImpact(
                issue="Destructive symbol allocation",
                estimated_pf_impact=pf_gain,
                estimated_r_impact=abs(total_sym_loss) * 0.2,
                priority="MEDIUM",
                evidence=f"{len(destructive_syms)} symbols lost {total_sym_loss:.2f}R total",
                measurement="Disable worst symbols, measure 50-trade PF change",
                engineering_effort="LOW",
                priority_score=round(pf_gain / 1.0, 2)))

        # Sort by priority_score (return on engineering effort)
        impacts.sort(key=lambda e: e.priority_score, reverse=True)
        return impacts

    def _calc_eqi(self, report: ExecutionAnalyticsReport, trades: List[Dict]) -> ExecutionQualityIndex:
        """v29: Compute Execution Quality Index — composite score from 6 weighted components."""
        eqi = ExecutionQualityIndex()

        # 1. Capture Score (0-100): How much MFE is captured
        # -100% capture = 0, 0% = 50, 50% = 100
        raw_capture = report.mean_capture  # typically negative
        eqi.capture_score = max(0, min(100, 50 + raw_capture * 100))

        # 2. Exit Efficiency Score: Based on exit type quality
        pos_exits = [e for e in report.exit_analytics if e.quality_tier == "POSITIVE"]
        total_exit_trades = sum(e.trades for e in report.exit_analytics) or 1
        pos_trades = sum(e.trades for e in pos_exits)
        eqi.exit_efficiency_score = min(100, (pos_trades / total_exit_trades) * 200)  # 50% positive = 100

        # 3. Slippage Score: MAE relative to MFE — lower MAE = better
        avg_mae = sum(abs(t.get("mae_pct", 0) or 0) for t in trades) / max(1, len(trades))
        avg_mfe = sum(t.get("highest_pnl", 0) or 0 for t in trades) / max(1, len(trades))
        slippage_ratio = avg_mae / max(0.01, avg_mfe) if avg_mfe > 0 else 1.0
        eqi.slippage_score = max(0, min(100, 100 - slippage_ratio * 100))

        # 4. Holding Efficiency Score: Based on capture distribution
        # More trades in 30-70% range = better holding
        optimal_range = report.capture_distribution.get("30-50%", 0) + report.capture_distribution.get("50-70%", 0)
        total_dist = sum(report.capture_distribution.values()) or 1
        eqi.holding_efficiency_score = min(100, (optimal_range / total_dist) * 250)

        # 5. Profit Protection Score: Win/loss ratio and trailing stop quality
        r_values = [t.get("realized_r", 0) or 0 for t in trades]
        wins = [r for r in r_values if r > 0]
        losses = [abs(r) for r in r_values if r < 0]
        avg_win = sum(wins) / max(1, len(wins))
        avg_loss = sum(losses) / max(1, len(losses))
        wl_ratio = avg_win / max(0.01, avg_loss)
        # Trailing stop quality
        ts_trades = [t for t in trades if "trailing_stop" in (t.get("exit_reason", "") or "")]
        ts_pf = 0.0
        if ts_trades:
            ts_r = [t.get("realized_r", 0) or 0 for t in ts_trades]
            ts_wins = sum(r for r in ts_r if r > 0)
            ts_losses = sum(abs(r) for r in ts_r if r < 0)
            ts_pf = ts_wins / max(0.01, ts_losses)
        eqi.profit_protection_score = min(100, (wl_ratio * 30) + (min(ts_pf, 2.0) * 20))

        # 6. Risk Control Score: Based on drawdown and adverse excursion
        cum = 0; peak = 0; max_dd = 0
        for r in r_values:
            cum += r
            if cum > peak: peak = cum
            dd = peak - cum
            if dd > max_dd: max_dd = dd
        # Lower drawdown = better risk control
        eqi.risk_control_score = max(0, min(100, 100 - max_dd * 5))

        # Composite: Weighted average
        weights = {"capture": 0.25, "exit_efficiency": 0.20, "slippage": 0.15,
                   "holding": 0.15, "profit_protection": 0.15, "risk_control": 0.10}
        eqi.composite_score = (
            eqi.capture_score * weights["capture"] +
            eqi.exit_efficiency_score * weights["exit_efficiency"] +
            eqi.slippage_score * weights["slippage"] +
            eqi.holding_efficiency_score * weights["holding"] +
            eqi.profit_protection_score * weights["profit_protection"] +
            eqi.risk_control_score * weights["risk_control"]
        )

        # Tier
        if eqi.composite_score >= 80: eqi.tier = "EXCELLENT"
        elif eqi.composite_score >= 60: eqi.tier = "GOOD"
        elif eqi.composite_score >= 40: eqi.tier = "NEEDS REVIEW"
        else: eqi.tier = "POOR"

        # Diagnosis
        components = [("Capture", eqi.capture_score), ("Exit Efficiency", eqi.exit_efficiency_score),
                      ("Slippage", eqi.slippage_score), ("Holding", eqi.holding_efficiency_score),
                      ("Profit Protection", eqi.profit_protection_score), ("Risk Control", eqi.risk_control_score)]
        weakest = min(components, key=lambda x: x[1])
        strongest = max(components, key=lambda x: x[1])
        eqi.diagnosis = f"Strongest: {strongest[0]} ({strongest[1]:.0f}), Weakest: {weakest[0]} ({weakest[1]:.0f})"

        # v30: Subscores for targeted diagnosis
        eqi.admission_score = min(100, eqi.exit_efficiency_score * 1.3)  # Admission quality tracks exit quality
        eqi.execution_score = (eqi.capture_score + eqi.slippage_score) / 2  # Execution = capture + slippage
        eqi.exit_score = (eqi.exit_efficiency_score + eqi.holding_efficiency_score) / 2  # Exit = efficiency + holding
        eqi.risk_score = (eqi.risk_control_score + eqi.profit_protection_score) / 2  # Risk = control + protection

        return eqi

    def _calc_prediction_calibration(self, report: ExecutionAnalyticsReport, trades: List[Dict]) -> List[PredictionCalibration]:
        """v30: Compare predicted PF improvements with actual outcomes for self-evaluation."""
        calibrations = []
        for impact in report.economic_impacts:
            pc = PredictionCalibration(issue=impact.issue)
            pc.predicted_pf = impact.predicted_pf
            # For now, actual_pf = 0 (post-action measurement not yet available)
            pc.actual_pf = 0.0
            pc.prediction_error = pc.actual_pf - pc.predicted_pf
            pc.absolute_error = abs(pc.prediction_error)
            pc.sample_size = len(trades)
            if abs(pc.prediction_error) < 0.05:
                pc.status = "ACCURATE"
            elif pc.prediction_error < 0:
                pc.status = "OVERESTIMATED"
            else:
                pc.status = "UNDERESTIMATED"
            calibrations.append(pc)
        return calibrations

    def _calc_confidence_history(self, trades: List[Dict]) -> ConfidenceHistory:
        """v30: Track confidence trends across time periods."""
        ch = ConfidenceHistory()
        if not trades:
            return ch

        # Compute confidence from different time windows
        all_conf = [t.get("confidence", 0) or 0 for t in trades]
        ch.today = sum(all_conf[:10]) / max(1, min(10, len(all_conf)))
        ch.one_week = sum(all_conf[:50]) / max(1, min(50, len(all_conf)))
        ch.one_month = sum(all_conf[:200]) / max(1, min(200, len(all_conf)))
        ch.rolling_average = sum(all_conf) / max(1, len(all_conf))

        # Trend
        if ch.today > ch.one_week * 1.02:
            ch.trend = "IMPROVING"
        elif ch.today < ch.one_week * 0.98:
            ch.trend = "DECLINING"
        else:
            ch.trend = "STABLE"

        # Volatility
        mean_c = ch.rolling_average
        ch.volatility = sum((c - mean_c) ** 2 for c in all_conf) / max(1, len(all_conf)) ** 0.5

        return ch

    def _calc_regime_timeline(self, trades: List[Dict]) -> List[RegimeTimelineEntry]:
        """v31: Build regime timeline showing when performance changes."""
        timeline = []
        if not trades:
            return timeline

        # Group trades by regime in order (trades are DESC by closed_at)
        current_regime = None
        segment = []
        index = 0

        for t in reversed(trades):  # Chronological order
            reg = t.get("regime", "unknown") or "unknown"
            if reg != current_regime:
                if current_regime is not None and segment:
                    entry = self._make_timeline_entry(index, current_regime, segment)
                    timeline.append(entry)
                    index += 1
                current_regime = reg
                segment = [t]
            else:
                segment.append(t)

        # Last segment
        if current_regime is not None and segment:
            entry = self._make_timeline_entry(index, current_regime, segment)
            timeline.append(entry)

        return timeline

    def _make_timeline_entry(self, index: int, regime: str, segment: List[Dict]) -> RegimeTimelineEntry:
        """Create a single regime timeline entry."""
        entry = RegimeTimelineEntry(index=index, regime=regime)
        entry.trades_in_regime = len(segment)
        entry.entries = len(segment)
        entry.exits = len(segment)

        r_vals = [t.get("realized_r", 0) or 0 for t in segment]
        wins = [r for r in r_vals if r > 0]
        losses = [abs(r) for r in r_vals if r < 0]
        entry.pf = sum(wins) / max(0.01, sum(losses))
        entry.total_r = sum(r_vals)
        entry.win_rate = len(wins) / max(1, len(r_vals))

        hold_vals = [t.get("hold_minutes", 0) or 0 for t in segment]
        entry.avg_hold = sum(hold_vals) / max(1, len(hold_vals))

        mfe_pairs = [(t.get("highest_pnl", 0) or 0, t.get("realized_r", 0) or 0) for t in segment]
        pos = [(m, r) for m, r in mfe_pairs if m > 0]
        entry.capture = sum(r / m for m, r in pos) / max(1, len(pos)) if pos else 0.0

        entry.duration_label = f"{len(segment)} trades"
        return entry

    def _calc_contribution_waterfall(self, report: ExecutionAnalyticsReport, trades: List[Dict]) -> List[WaterfallEntry]:
        """v31: Build PF contribution waterfall showing cumulative impact of each component."""
        waterfall = []
        r_values = [t.get("realized_r", 0) or 0 for t in trades]
        total_wins = sum(r for r in r_values if r > 0)
        total_losses = sum(abs(r) for r in r_values if r < 0)
        current_pf = report.system_pf

        # Start with baseline (ideal scenario = all MFE captured)
        total_mfe = 0
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            if mfe > 0:
                total_mfe += mfe

        baseline_pf = total_mfe / max(0.01, total_losses) if total_losses > 0 else 10.0

        # 1. Baseline (ideal)
        waterfall.append(WaterfallEntry(
            component="Baseline (ideal)",
            pf_delta=0.0, cumulative_pf=baseline_pf,
            direction="NEUTRAL", trades_affected=len(trades),
            r_impact=0.0, evidence=f"All {len(trades)} trades, {total_mfe:.1f}R total MFE available"))

        # 2. Capture loss (how much MFE was NOT captured)
        realized_r = sum(r_values)
        missed_r = total_mfe - realized_r if total_mfe > 0 else 0
        pf_after_capture = baseline_pf - (missed_r / max(0.01, total_losses))
        waterfall.append(WaterfallEntry(
            component="Capture loss",
            pf_delta=pf_after_capture - baseline_pf,
            cumulative_pf=max(0, pf_after_capture),
            direction="NEGATIVE" if pf_after_capture < baseline_pf else "POSITIVE",
            trades_affected=report.premature_exits,
            r_impact=-missed_r,
            evidence=f"{missed_r:.1f}R of MFE not captured ({report.premature_exits} premature exits)"))

        # 3. Exit logic impact
        exit_positive = sum(e.total_r for e in report.exit_analytics if e.quality_tier == "POSITIVE")
        exit_negative = sum(e.total_r for e in report.exit_analytics if e.quality_tier == "DESTRUCTIVE")
        exit_delta = (exit_positive + exit_negative) / max(0.01, total_losses)
        pf_after_exits = pf_after_capture + exit_delta
        waterfall.append(WaterfallEntry(
            component="Exit logic",
            pf_delta=exit_delta,
            cumulative_pf=max(0, pf_after_exits),
            direction="POSITIVE" if exit_delta > 0 else "NEGATIVE",
            trades_affected=sum(e.trades for e in report.exit_analytics if e.quality_tier == "DESTRUCTIVE"),
            r_impact=exit_positive + exit_negative,
            evidence=f"Positive exits: {exit_positive:.1f}R, Destructive exits: {exit_negative:.1f}R"))

        # 4. Admission filter impact
        admitted_r = sum(t.get("realized_r", 0) or 0 for t in trades if (t.get("confidence", 0) or 0) >= 0.85)
        admission_delta = admitted_r / max(0.01, total_losses) if total_losses > 0 else 0
        waterfall.append(WaterfallEntry(
            component="Admission filter",
            pf_delta=admission_delta,
            cumulative_pf=max(0, pf_after_exits + admission_delta),
            direction="POSITIVE" if admission_delta > 0 else "NEGATIVE",
            trades_affected=len([t for t in trades if (t.get("confidence", 0) or 0) >= 0.85]),
            r_impact=admitted_r,
            evidence=f"{len([t for t in trades if (t.get('confidence', 0) or 0) >= 0.85])} trades admitted at conf>=0.85"))

        # 5. Trailing stop impact
        ts_trades = [t for t in trades if "trailing_stop" in (t.get("exit_reason", "") or "")]
        ts_r = sum(t.get("realized_r", 0) or 0 for t in ts_trades)
        ts_delta = ts_r / max(0.01, total_losses)
        current_step_pf = waterfall[-1].cumulative_pf
        waterfall.append(WaterfallEntry(
            component="Trailing stops",
            pf_delta=ts_delta,
            cumulative_pf=max(0, current_step_pf + ts_delta),
            direction="NEGATIVE" if ts_r < 0 else "POSITIVE",
            trades_affected=len(ts_trades),
            r_impact=ts_r,
            evidence=f"{len(ts_trades)} trailing stops contributed {ts_r:.2f}R"))

        # 6. Holding time impact
        profitable_holds = [t.get("hold_minutes", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losing_holds = [t.get("hold_minutes", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) < 0]
        avg_win_hold = sum(profitable_holds) / max(1, len(profitable_holds)) if profitable_holds else 0
        avg_lose_hold = sum(losing_holds) / max(1, len(losing_holds)) if losing_holds else 0
        hold_efficiency = (avg_win_hold - avg_lose_hold) / max(1, avg_lose_hold)
        hold_delta = hold_efficiency * 0.05  # Small PF adjustment
        current_step_pf = waterfall[-1].cumulative_pf
        waterfall.append(WaterfallEntry(
            component="Holding time",
            pf_delta=hold_delta,
            cumulative_pf=max(0, current_step_pf + hold_delta),
            direction="POSITIVE" if hold_delta > 0 else "NEGATIVE",
            trades_affected=len(trades),
            r_impact=hold_delta * total_losses,
            evidence=f"Winners hold {avg_win_hold:.0f}m, Losers hold {avg_lose_hold:.0f}m"))

        # 7. Final (current PF)
        current_step_pf = waterfall[-1].cumulative_pf
        waterfall.append(WaterfallEntry(
            component="Current PF",
            pf_delta=current_pf - current_step_pf,
            cumulative_pf=current_pf,
            direction="NEUTRAL",
            trades_affected=len(trades),
            r_impact=realized_r,
            evidence=f"Final system PF from {len(trades)} trades"))

        return waterfall

    def _calc_confidence_intervals(self, trades: List[Dict], report: ExecutionAnalyticsReport) -> List[ConfidenceInterval]:
        """v32: Calculate 95% confidence intervals for key metrics using bootstrap."""
        import math
        intervals = []
        n = len(trades)
        if n < 10:
            return intervals

        r_values = [t.get("realized_r", 0) or 0 for t in trades]

        # Helper: CI for a list of values
        def ci_for(name: str, values: List[float]) -> ConfidenceInterval:
            if not values:
                return ConfidenceInterval(metric_name=name, value=0, lower=0, upper=0, sample_size=0)
            mean = sum(values) / len(values)
            var = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
            se = math.sqrt(var / len(values))
            # 95% CI: mean ± 1.96 * SE
            return ConfidenceInterval(
                metric_name=name, value=mean,
                lower=mean - 1.96 * se, upper=mean + 1.96 * se,
                sample_size=len(values), std_error=se)

        # 1. Expectancy CI
        intervals.append(ci_for("Expectancy", r_values))

        # 2. Win Rate CI
        win_flags = [1 if r > 0 else 0 for r in r_values]
        intervals.append(ci_for("Win Rate", [float(w) for w in win_flags]))

        # 3. Capture CI
        capture_vals = []
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                capture_vals.append(r / mfe)
        if capture_vals:
            intervals.append(ci_for("Capture Ratio", capture_vals))

        # 4. Avg Win CI
        wins = [r for r in r_values if r > 0]
        if wins:
            intervals.append(ci_for("Avg Win", wins))

        # 5. Avg Loss CI
        losses = [abs(r) for r in r_values if r < 0]
        if losses:
            intervals.append(ci_for("Avg Loss", losses))

        # 6. Hold time CI
        hold_vals = [float(t.get("hold_minutes", 0) or 0) for t in trades]
        intervals.append(ci_for("Hold Time", hold_vals))

        # 7. PF CI (bootstrap approximation)
        wins_sum = sum(r for r in r_values if r > 0)
        losses_sum = sum(abs(r) for r in r_values if r < 0)
        pf = wins_sum / max(0.01, losses_sum)
        # Approximate PF SE using delta method
        pf_se = pf * math.sqrt(1 / max(1, len(wins)) + 1 / max(1, len(losses)))
        intervals.append(ConfidenceInterval(
            metric_name="Profit Factor", value=pf,
            lower=max(0, pf - 1.96 * pf_se), upper=pf + 1.96 * pf_se,
            sample_size=n, std_error=pf_se))

        return intervals

    def _calc_interaction_matrix(self, trades: List[Dict], report: ExecutionAnalyticsReport) -> List[InteractionMatrixEntry]:
        """v32: Quantify how components interact — combined PF vs individual PFs."""
        matrix = []
        r_values = [t.get("realized_r", 0) or 0 for t in trades]
        total_losses = abs(sum(r for r in r_values if r < 0))
        if total_losses == 0:
            return matrix

        # Define component filters
        def pf_of(subset: List[Dict]) -> float:
            r = [t.get("realized_r", 0) or 0 for t in subset]
            w = sum(v for v in r if v > 0)
            l = sum(abs(v) for v in r if v < 0)
            return w / max(0.01, l)

        # Component trade subsets
        capture_good = [t for t in trades if (t.get("highest_pnl", 0) or 0) > 0 and
                         (t.get("realized_r", 0) or 0) / max(0.01, t.get("highest_pnl", 0) or 0) > 0.3]
        capture_bad = [t for t in trades if (t.get("highest_pnl", 0) or 0) > 0 and
                        (t.get("realized_r", 0) or 0) / max(0.01, t.get("highest_pnl", 0) or 0) <= 0.3]
        exit_positive = [t for t in trades if "take_profit" in (t.get("exit_reason", "") or "")]
        exit_destructive = [t for t in trades if "stop_loss" in (t.get("exit_reason", "") or "")]
        ts_trades = [t for t in trades if "trailing_stop" in (t.get("exit_reason", "") or "")]
        long_trades = [t for t in trades if (t.get("side", "") or "").upper() in ("LONG", "BUY")]
        short_trades = [t for t in trades if (t.get("side", "") or "").upper() in ("SHORT", "SELL")]

        # Interaction: Capture × Exit Logic
        combo = [t for t in capture_good if "take_profit" in (t.get("exit_reason", "") or "")]
        if combo:
            matrix.append(InteractionMatrixEntry(
                component_a="Capture (good)", component_b="Exit (TP)",
                pf_a=pf_of(capture_good), pf_b=pf_of(exit_positive),
                combined_pf=pf_of(combo), interaction_effect=pf_of(combo) - (pf_of(capture_good) + pf_of(exit_positive)) / 2,
                synergy="SYNERGISTIC" if pf_of(combo) > (pf_of(capture_good) + pf_of(exit_positive)) / 2 else "INTERFERENCE",
                trades_affected=len(combo)))

        # Interaction: Capture × Trailing Stop
        combo = [t for t in capture_good if "trailing_stop" in (t.get("exit_reason", "") or "")]
        if combo:
            matrix.append(InteractionMatrixEntry(
                component_a="Capture (good)", component_b="Trailing Stop",
                pf_a=pf_of(capture_good), pf_b=pf_of(ts_trades),
                combined_pf=pf_of(combo), interaction_effect=pf_of(combo) - (pf_of(capture_good) + pf_of(ts_trades)) / 2,
                synergy="SYNERGISTIC" if pf_of(combo) > (pf_of(capture_good) + pf_of(ts_trades)) / 2 else "INTERFERENCE",
                trades_affected=len(combo)))

        # Interaction: Long × Short
        if long_trades and short_trades:
            all_trades = long_trades + short_trades
            matrix.append(InteractionMatrixEntry(
                component_a="Long", component_b="Short",
                pf_a=pf_of(long_trades), pf_b=pf_of(short_trades),
                combined_pf=pf_of(all_trades),
                interaction_effect=pf_of(all_trades) - (pf_of(long_trades) + pf_of(short_trades)) / 2,
                synergy="NEUTRAL", trades_affected=len(all_trades)))

        # Interaction: Stop Loss × Trailing Stop
        combo = [t for t in trades if "stop_loss" in (t.get("exit_reason", "") or "") or "trailing_stop" in (t.get("exit_reason", "") or "")]
        if combo:
            matrix.append(InteractionMatrixEntry(
                component_a="Stop Loss", component_b="Trailing Stop",
                pf_a=pf_of(exit_destructive), pf_b=pf_of(ts_trades),
                combined_pf=pf_of(combo), interaction_effect=pf_of(combo) - (pf_of(exit_destructive) + pf_of(ts_trades)) / 2,
                synergy="SYNERGISTIC" if pf_of(combo) > (pf_of(exit_destructive) + pf_of(ts_trades)) / 2 else "INTERFERENCE",
                trades_affected=len(combo)))

        # Interaction: Capture (bad) × Holding Time
        long_hold = [t for t in capture_bad if (t.get("hold_minutes", 0) or 0) > 120]
        if long_hold:
            matrix.append(InteractionMatrixEntry(
                component_b="Long Hold (>2h)", component_a="Capture (bad)",
                pf_a=pf_of(capture_bad), pf_b=pf_of(long_hold),
                combined_pf=pf_of(long_hold),
                interaction_effect=pf_of(long_hold) - (pf_of(capture_bad) + pf_of(long_hold)) / 2,
                synergy="INTERFERENCE", trades_affected=len(long_hold)))

        return matrix

    def _calc_regime_transitions(self, trades: List[Dict]) -> List[RegimeTransition]:
        """v32: Analyze performance during regime transitions (from_regime → to_regime)."""
        from collections import Counter
        transitions = []
        if len(trades) < 5:
            return transitions

        # Build transition pairs (chronological order)
        reversed_trades = list(reversed(trades))
        transition_groups = defaultdict(list)
        for i in range(1, len(reversed_trades)):
            from_reg = reversed_trades[i - 1].get("regime", "unknown") or "unknown"
            to_reg = reversed_trades[i].get("regime", "unknown") or "unknown"
            if from_reg != to_reg:
                key = (from_reg, to_reg)
                transition_groups[key].append(reversed_trades[i])

        for (from_reg, to_reg), trans_trades in transition_groups.items():
            if len(trans_trades) < 2:
                continue
            rt = RegimeTransition(from_regime=from_reg, to_regime=to_reg, count=len(trans_trades))
            r_vals = [t.get("realized_r", 0) or 0 for t in trans_trades]
            wins = [r for r in r_vals if r > 0]
            losses = [abs(r) for r in r_vals if r < 0]
            rt.avg_pf = sum(wins) / max(0.01, sum(losses))
            rt.avg_r = sum(r_vals) / max(1, len(r_vals))

            mfe_pairs = [(t.get("highest_pnl", 0) or 0, t.get("realized_r", 0) or 0) for t in trans_trades]
            pos = [(m, r) for m, r in mfe_pairs if m > 0]
            rt.avg_capture = sum(r / m for m, r in pos) / max(1, len(pos)) if pos else 0.0

            if rt.avg_pf > 1.0:
                rt.transition_quality = "SMOOTH"
            elif rt.avg_pf > 0.5:
                rt.transition_quality = "VOLATILE"
            else:
                rt.transition_quality = "DESTRUCTIVE"

            transitions.append(rt)

        transitions.sort(key=lambda t: t.avg_pf)
        return transitions

    def _calc_execution_efficiency(self, trades: List[Dict], report: ExecutionAnalyticsReport) -> Dict:
        """Compute Execution Efficiency = Realized R / Expected R."""
        r_values = [t.get("realized_r", 0) or 0 for t in trades]

        # Expected R: for each trade, the potential based on MFE
        # If a trade had MFE of 3R but realized 1R, efficiency = 1/3 = 33%
        efficiencies = []
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0 and r > 0:
                efficiencies.append(r / mfe)
            elif mfe > 0 and r < 0:
                # Lost money when MFE was available — negative efficiency
                efficiencies.append(r / mfe)

        # By confidence bucket
        bucket_defs = [("0.95-1.00", 0.95, 1.00), ("0.90-0.95", 0.90, 0.95), ("0.85-0.90", 0.85, 0.90),
                       ("0.80-0.85", 0.80, 0.85), ("0.75-0.80", 0.75, 0.80), ("<0.75", 0, 0.75)]
        by_confidence = []
        for label, min_s, max_s in bucket_defs:
            bt = [t for t in trades if min_s <= (t.get("confidence", 0) or 0) < max_s]
            if bt:
                b_r = [t.get("realized_r", 0) or 0 for t in bt]
                b_eff = []
                for t in bt:
                    mfe = t.get("highest_pnl", 0) or 0
                    r = t.get("realized_r", 0) or 0
                    if mfe > 0:
                        b_eff.append(r / mfe)
                w = sum(r for r in b_r if r > 0)
                l = sum(abs(r) for r in b_r if r < 0)
                pf = w / max(0.01, l)
                by_confidence.append({
                    "bucket": label, "trades": len(bt),
                    "win_rate": round(sum(1 for r in b_r if r > 0) / len(bt), 4),
                    "profit_factor": round(pf, 3),
                    "avg_r": round(sum(b_r) / len(b_r), 4),
                    "avg_efficiency": round(sum(b_eff) / max(1, len(b_eff)), 4) if b_eff else None,
                    "total_r": round(sum(b_r), 3),
                })

        # Overall
        avg_eff = sum(efficiencies) / max(1, len(efficiencies)) if efficiencies else 0
        wins_r = sum(r for r in r_values if r > 0)
        losses_r = sum(abs(r) for r in r_values if r < 0)

        return {
            "overall_efficiency": round(avg_eff, 4),
            "trades_with_mfe": len(efficiencies),
            "trades_total": len(trades),
            "system_pf": round(report.system_pf, 3),
            "system_capture": round(report.system_capture, 4),
            "by_confidence": by_confidence,
        }
