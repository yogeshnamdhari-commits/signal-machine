"""
Learning from Rejected Trades — Track rejected signals to optimize thresholds.

Per Executive Assessment v4:
    "The execution layer currently learns mostly from closed trades.
     It should also learn from rejected trades.
     Example:
         Rejected Score 88 Later moved +5R — That rejection was too strict.
         Rejected Score 82 Later -2R — Good rejection.
     Tracking both executed and rejected outcomes lets the eligibility
     threshold evolve automatically instead of remaining fixed."

Key Features:
    1. Signal Logging — record all signals with their scores
    2. Outcome Tracking — track what happened to rejected signals
    3. Threshold Analysis — which threshold settings would have been optimal
    4. False Rejection Detection — signals that were rejected but would have been profitable
    5. False Acceptance Detection — signals that were accepted but lost money
    6. Adaptive Threshold Recommendation — suggest optimal threshold adjustments

READ-ONLY: Never modifies upstream data. Logs to separate file.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"
_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "rejected_signals.json"

# ═══════════════════════════════════════════════════════════════
# REJECTION LEARNING CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Minimum outcomes needed for threshold analysis
MIN_OUTCOMES_FOR_ANALYSIS = 20

# Threshold adjustment recommendation thresholds
FALSE_REJECTION_RATE_TARGET = 0.15  # Target: < 15% of rejections were wrong
FALSE_ACCEPTANCE_RATE_TARGET = 0.30  # Target: < 30% of acceptances lost money

# Rolling window for recent analysis
ROLLING_WINDOW = 100


@dataclass
class RejectedSignal:
    """A signal that was rejected by the pipeline."""
    signal_id: str = ""
    symbol: str = ""
    side: str = ""
    timestamp: float = 0.0
    rejection_stage: str = ""     # Which stage rejected it
    rejection_reason: str = ""
    scores: Dict[str, float] = field(default_factory=dict)
    # Scores at rejection: quality, eligibility, validation, etc.

    # Outcome (filled in later if we can track it)
    outcome_tracked: bool = False
    outcome_r: float = 0.0        # What would have happened
    outcome_pnl: float = 0.0
    would_have_been_profitable: bool = False

    def to_dict(self) -> Dict:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side,
            "timestamp": self.timestamp,
            "rejection_stage": self.rejection_stage,
            "rejection_reason": self.rejection_reason,
            "scores": self.scores,
            "outcome_tracked": self.outcome_tracked,
            "outcome_r": round(self.outcome_r, 3),
            "would_have_been_profitable": self.would_have_been_profitable,
        }


@dataclass
class ThresholdAnalysis:
    """Analysis of how threshold changes would affect outcomes."""
    current_threshold: float = 0.0
    optimal_threshold: float = 0.0
    false_rejection_rate: float = 0.0
    false_acceptance_rate: float = 0.0
    potential_improvement_r: float = 0.0
    recommendation: str = ""

    def to_dict(self) -> Dict:
        return {
            "current_threshold": round(self.current_threshold, 1),
            "optimal_threshold": round(self.optimal_threshold, 1),
            "false_rejection_rate": round(self.false_rejection_rate, 3),
            "false_acceptance_rate": round(self.false_acceptance_rate, 3),
            "potential_improvement_r": round(self.potential_improvement_r, 3),
            "recommendation": self.recommendation,
        }


@dataclass
class RejectionLearningResult:
    """Complete result from rejection learning analysis."""
    timestamp: float = 0.0
    total_rejected: int = 0
    total_outcomes_tracked: int = 0
    false_rejections: int = 0     # Rejected but would have been profitable
    good_rejections: int = 0      # Rejected and would have lost money
    false_rejection_rate: float = 0.0
    threshold_analysis: Optional[ThresholdAnalysis] = None
    by_stage: Dict[str, Dict] = field(default_factory=dict)
    recent_rejections: List[RejectedSignal] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "total_rejected": self.total_rejected,
            "total_outcomes_tracked": self.total_outcomes_tracked,
            "false_rejections": self.false_rejections,
            "good_rejections": self.good_rejections,
            "false_rejection_rate": round(self.false_rejection_rate, 3),
            "threshold_analysis": self.threshold_analysis.to_dict() if self.threshold_analysis else {},
            "by_stage": self.by_stage,
        }


class RejectedTradeLearner:
    """
    Learns from rejected trades to optimize pipeline thresholds.

    Per Executive Assessment v4:
        "Tracking both executed and rejected outcomes lets the eligibility
         threshold evolve automatically instead of remaining fixed."

    This engine:
        1. Logs all rejected signals with their scores
        2. Attempts to track outcomes (did the rejected signal later appear?)
        3. Calculates false rejection rate
        4. Recommends threshold adjustments
        5. Identifies which rejection stages are too strict/loose

    READ-ONLY: Never modifies upstream data. Uses separate log file.
    """

    def __init__(self, db_path: Optional[Path] = None, log_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._log_path = log_path or _LOG_PATH
        self._rejected_signals: List[RejectedSignal] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load rejected signals from log file."""
        if time.time() - self._last_load < 300:
            return
        self._load_rejected_signals()

    def _load_rejected_signals(self) -> None:
        """Load rejected signals from JSON log."""
        try:
            if self._log_path.exists():
                with open(self._log_path, "r") as f:
                    data = json.load(f)
                self._rejected_signals = [
                    RejectedSignal(**item) for item in data
                ]
            else:
                self._rejected_signals = []

            self._last_load = time.time()
            logger.info(
                "📊 Rejection Learner loaded: {} rejected signals",
                len(self._rejected_signals),
            )

        except Exception as e:
            logger.warning("Could not load rejection learner: {}", e)
            self._rejected_signals = []

    def _save_rejected_signals(self) -> None:
        """Save rejected signals to JSON log."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "w") as f:
                json.dump([s.to_dict() for s in self._rejected_signals], f, indent=2)
        except Exception as e:
            logger.warning("Could not save rejected signals: {}", e)

    # ═══════════════════════════════════════════════════════════════
    # LOGGING
    # ═══════════════════════════════════════════════════════════════

    def log_rejection(
        self,
        symbol: str,
        side: str,
        rejection_stage: str,
        rejection_reason: str,
        scores: Optional[Dict[str, float]] = None,
    ) -> None:
        """
        Log a rejected signal for later analysis.

        Args:
            symbol: Symbol that was rejected
            side: LONG or SHORT
            rejection_stage: Which stage rejected it (validation, eligibility, etc.)
            rejection_reason: Why it was rejected
            scores: Optional dict of scores at rejection time
        """
        signal = RejectedSignal(
            signal_id=f"{symbol}_{side}_{int(time.time())}",
            symbol=symbol,
            side=side,
            timestamp=time.time(),
            rejection_stage=rejection_stage,
            rejection_reason=rejection_reason,
            scores=scores or {},
        )

        self._rejected_signals.append(signal)

        # Keep only recent signals (last 1000)
        if len(self._rejected_signals) > 1000:
            self._rejected_signals = self._rejected_signals[-1000:]

        self._save_rejected_signals()

    def track_outcome(
        self,
        symbol: str,
        side: str,
        realized_r: float,
        pnl: float,
    ) -> None:
        """
        Track the outcome of a previously rejected signal.

        This is called when a signal that was previously rejected
        later appears as a trade (from a different scan cycle).

        Args:
            symbol: Symbol that was rejected then traded
            side: LONG or SHORT
            realized_r: Actual R outcome
            pnl: Actual PnL
        """
        # Find matching rejected signals (same symbol+side in last 24h)
        cutoff = time.time() - 86400
        for signal in reversed(self._rejected_signals):
            if (signal.symbol == symbol
                and signal.side == side
                and signal.timestamp > cutoff
                and not signal.outcome_tracked):

                signal.outcome_tracked = True
                signal.outcome_r = realized_r
                signal.outcome_pnl = pnl
                signal.would_have_been_profitable = realized_r > 0

                logger.debug(
                    "📊 REJECTION TRACKED: {} {} rejected at {} → outcome={:.2f}R ({})",
                    symbol, side, signal.rejection_stage, realized_r,
                    "PROFITABLE" if signal.would_have_been_profitable else "LOSS",
                )
                break

        self._save_rejected_signals()

    # ═══════════════════════════════════════════════════════════════
    # ANALYSIS
    # ═══════════════════════════════════════════════════════════════

    def analyze(self) -> RejectionLearningResult:
        """
        Analyze rejected signals and recommend threshold adjustments.

        Returns:
            RejectionLearningResult with analysis and recommendations
        """
        self._ensure_loaded()

        result = RejectionLearningResult(timestamp=time.time())
        result.total_rejected = len(self._rejected_signals)

        # Filter to signals with tracked outcomes
        tracked = [s for s in self._rejected_signals if s.outcome_tracked]
        result.total_outcomes_tracked = len(tracked)

        if not tracked:
            return result

        # Count false rejections (rejected but would have been profitable)
        result.false_rejections = sum(1 for s in tracked if s.would_have_been_profitable)
        result.good_rejections = len(tracked) - result.false_rejections
        result.false_rejection_rate = result.false_rejections / max(1, len(tracked))

        # ── By Stage Analysis ──
        by_stage: Dict[str, List[RejectedSignal]] = defaultdict(list)
        for s in tracked:
            by_stage[s.rejection_stage].append(s)

        for stage, signals in by_stage.items():
            false_rej = sum(1 for s in signals if s.would_have_been_profitable)
            result.by_stage[stage] = {
                "total": len(signals),
                "false_rejections": false_rej,
                "false_rejection_rate": false_rej / max(1, len(signals)),
                "avg_outcome_r": sum(s.outcome_r for s in signals) / max(1, len(signals)),
            }

        # ── Threshold Analysis ──
        result.threshold_analysis = self._analyze_thresholds(tracked)

        # ── Recent Rejections ──
        result.recent_rejections = self._rejected_signals[-10:]

        return result

    def _analyze_thresholds(self, tracked: List[RejectedSignal]) -> ThresholdAnalysis:
        """Analyze what thresholds would have been optimal."""
        analysis = ThresholdAnalysis()

        if not tracked:
            return analysis

        # Calculate what different eligibility thresholds would have caught
        eligibility_scores = []
        for s in tracked:
            score = s.scores.get("eligibility_score", s.scores.get("execution_score", 0))
            if score > 0:
                eligibility_scores.append((score, s.would_have_been_profitable, s.outcome_r))

        if not eligibility_scores:
            return analysis

        # Find optimal threshold
        best_threshold = 90  # Current default
        best_improvement = 0

        for test_threshold in range(70, 100, 5):
            # Signals that would have been accepted at this threshold
            accepted = [(score, prof, r) for score, prof, r in eligibility_scores if score >= test_threshold]
            rejected = [(score, prof, r) for score, prof, r in eligibility_scores if score < test_threshold]

            if not rejected:
                continue

            # False rejections at this threshold
            false_rej = sum(1 for _, prof, _ in rejected if prof)
            false_rej_rate = false_rej / max(1, len(rejected))

            # Improvement from accepting these signals
            improvement = sum(r for _, prof, r in rejected if prof)

            if improvement > best_improvement:
                best_improvement = improvement
                best_threshold = test_threshold

        analysis.current_threshold = 90
        analysis.optimal_threshold = best_threshold
        analysis.potential_improvement_r = best_improvement

        # False rejection rate at current threshold
        current_rejected = [(score, prof, r) for score, prof, r in eligibility_scores if score < 90]
        if current_rejected:
            analysis.false_rejection_rate = sum(1 for _, prof, _ in current_rejected if prof) / len(current_rejected)

        # Recommendation
        if analysis.false_rejection_rate > FALSE_REJECTION_RATE_TARGET:
            analysis.recommendation = (
                f"Lower eligibility threshold from 90 to {best_threshold}. "
                f"False rejection rate {analysis.false_rejection_rate:.1%} exceeds "
                f"target {FALSE_REJECTION_RATE_TARGET:.1%}. "
                f"Potential improvement: {best_improvement:.2f}R"
            )
        elif analysis.false_rejection_rate < 0.05:
            analysis.recommendation = (
                "Thresholds are well-calibrated. False rejection rate is low."
            )
        else:
            analysis.recommendation = (
                f"Thresholds are acceptable. False rejection rate {analysis.false_rejection_rate:.1%} "
                f"is within target."
            )

        return analysis

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════

    def get_false_rejection_rate(self) -> float:
        """Get current false rejection rate."""
        result = self.analyze()
        return result.false_rejection_rate

    def get_recommended_threshold(self) -> float:
        """Get recommended eligibility threshold."""
        result = self.analyze()
        if result.threshold_analysis:
            return result.threshold_analysis.optimal_threshold
        return 90  # Default

    def get_summary(self) -> Dict[str, Any]:
        """Get complete rejection learning summary."""
        result = self.analyze()
        return result.to_dict()
