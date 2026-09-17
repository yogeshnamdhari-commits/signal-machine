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
from math import isfinite
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"
_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "rejected_signals.json"

# ═══════════════════════════════════════════════════════════════
# REJECTION LEARNING CONFIGURATION
# ═══════════════════════════════════════════════════════════════

MIN_OUTCOMES_FOR_ANALYSIS = 20
FALSE_REJECTION_RATE_TARGET = 0.15
FALSE_ACCEPTANCE_RATE_TARGET = 0.30
ROLLING_WINDOW = 100


@dataclass
class RejectedSignal:
    """A signal that was rejected by the pipeline."""
    signal_id: str = ""
    symbol: str = ""
    side: str = ""
    timestamp: float = 0.0
    rejection_stage: str = ""
    rejection_reason: str = ""
    scores: Dict[str, float] = field(default_factory=dict)

    # Outcome (filled in later if we can track it)
    outcome_tracked: bool = False
    outcome_r: float = 0.0
    outcome_pnl: float = 0.0
    would_have_been_profitable: bool = False
    mfe_r: float = 0.0
    mae_r: float = 0.0
    holding_bars: int = 0
    exit_reason: str = ""
    tp_hit: bool = False
    sl_hit: bool = False
    outcome_source: str = ""

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
            "outcome_pnl": round(self.outcome_pnl, 2),
            "would_have_been_profitable": self.would_have_been_profitable,
            "mfe_r": round(self.mfe_r, 3),
            "mae_r": round(self.mae_r, 3),
            "holding_bars": self.holding_bars,
            "exit_reason": self.exit_reason,
            "tp_hit": self.tp_hit,
            "sl_hit": self.sl_hit,
            "outcome_source": self.outcome_source,
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
    false_rejections: int = 0
    good_rejections: int = 0
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
    """Learn from rejected signals without changing upstream trading data."""

    def __init__(self, db_path: Optional[Path] = None, log_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._log_path = log_path or _LOG_PATH
        self._rejected_signals: List[RejectedSignal] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        if time.time() - self._last_load < 300:
            return
        self._load_rejected_signals()

    def _load_rejected_signals(self) -> None:
        try:
            if self._log_path.exists():
                with open(self._log_path, "r") as f:
                    data = json.load(f)
                self._rejected_signals = [RejectedSignal(**item) for item in data]
            else:
                self._rejected_signals = []
            self._last_load = time.time()
            logger.info("📊 Rejection Learner loaded: {} rejected signals", len(self._rejected_signals))
        except Exception as e:
            logger.warning("Could not load rejection learner: {}", e)
            self._rejected_signals = []

    def _save_rejected_signals(self) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "w") as f:
                json.dump([s.to_dict() for s in self._rejected_signals], f, indent=2)
        except Exception as e:
            logger.warning("Could not save rejected signals: {}", e)

    def log_rejection(
        self,
        symbol: str,
        side: str,
        rejection_stage: str,
        rejection_reason: str,
        scores: Optional[Dict[str, float]] = None,
    ) -> None:
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
        if len(self._rejected_signals) > 1000:
            self._rejected_signals = self._rejected_signals[-1000:]
        self._save_rejected_signals()

    def track_outcome(self, symbol: str, side: str, realized_r: float, pnl: float) -> None:
        """Track an actual outcome when a previously rejected signal later trades."""
        cutoff = time.time() - 86400
        for signal in reversed(self._rejected_signals):
            if (signal.symbol == symbol and signal.side == side and signal.timestamp > cutoff
                    and not signal.outcome_tracked):
                signal.outcome_tracked = True
                signal.outcome_r = realized_r
                signal.outcome_pnl = pnl
                signal.would_have_been_profitable = realized_r > 0
                signal.outcome_source = "subsequent_executed_trade"
                logger.debug(
                    "📊 REJECTION TRACKED: {} {} rejected at {} → outcome={:.2f}R ({})",
                    symbol, side, signal.rejection_stage, realized_r,
                    "PROFITABLE" if signal.would_have_been_profitable else "LOSS",
                )
                break
        self._save_rejected_signals()

    def attribute_counterfactual_outcome(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        risk_per_unit: float,
        bars: List[Dict[str, float]],
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
    ) -> None:
        """Attribute a rejected signal using an explicitly supplied future market path.

        The caller must provide bars that occur after the rejection timestamp. No market
        data is generated or fetched here. MFE/MAE are measured from the supplied path.
        If TP and SL are both touched in the same bar, the path is ambiguous and no
        realized outcome is recorded, preventing an optimistic ordering assumption.
        """
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(float(v))
                   for v in (entry_price, risk_per_unit)) or risk_per_unit <= 0:
            raise ValueError("entry_price and risk_per_unit must be finite; risk_per_unit must be positive")
        if not bars:
            raise ValueError("bars must contain at least one future market observation")

        cutoff = time.time() - 86400
        signal = next((s for s in reversed(self._rejected_signals)
                       if s.symbol == symbol and s.side == side and s.timestamp > cutoff
                       and not s.outcome_tracked), None)
        if signal is None:
            return

        normalized = []
        previous_timestamp = None
        for bar in bars:
            try:
                timestamp = float(bar["timestamp"])
                high = float(bar["high"])
                low = float(bar["low"])
                close = float(bar["close"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid market-path bar: {exc}") from exc
            if not all(isfinite(v) for v in (timestamp, high, low, close)) or high < low:
                raise ValueError("market-path bars must contain finite values with high >= low")
            if previous_timestamp is not None and timestamp < previous_timestamp:
                raise ValueError("market-path timestamps must be nondecreasing")
            previous_timestamp = timestamp
            normalized.append((timestamp, high, low, close))

        side_upper = side.upper()
        if side_upper not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")

        if side_upper == "LONG":
            mfe_r = max((high - entry_price) / risk_per_unit for _, high, _, _ in normalized)
            mae_r = max((entry_price - low) / risk_per_unit for _, _, low, _ in normalized)
        else:
            mfe_r = max((entry_price - low) / risk_per_unit for _, _, low, _ in normalized)
            mae_r = max((high - entry_price) / risk_per_unit for _, high, _, _ in normalized)

        exit_price = normalized[-1][3]
        exit_reason = "HORIZON"
        tp_hit = False
        sl_hit = False
        holding_bars = len(normalized)

        for index, (_, high, low, close) in enumerate(normalized, start=1):
            tp_touched = target_price is not None and (
                high >= target_price if side_upper == "LONG" else low <= target_price
            )
            sl_touched = stop_price is not None and (
                low <= stop_price if side_upper == "LONG" else high >= stop_price
            )
            if tp_touched and sl_touched:
                exit_reason = "AMBIGUOUS_SAME_BAR"
                holding_bars = index
                self._save_rejected_signals()
                return
            if tp_touched:
                exit_price = float(target_price)
                exit_reason = "TP"
                tp_hit = True
                holding_bars = index
                break
            if sl_touched:
                exit_price = float(stop_price)
                exit_reason = "SL"
                sl_hit = True
                holding_bars = index
                break

        move = exit_price - entry_price if side_upper == "LONG" else entry_price - exit_price
        outcome_r = move / risk_per_unit
        signal.outcome_tracked = True
        signal.outcome_r = outcome_r
        signal.outcome_pnl = move
        signal.would_have_been_profitable = outcome_r > 0
        signal.mfe_r = mfe_r
        signal.mae_r = mae_r
        signal.holding_bars = holding_bars
        signal.exit_reason = exit_reason
        signal.tp_hit = tp_hit
        signal.sl_hit = sl_hit
        signal.outcome_source = "counterfactual_market_path"
        self._save_rejected_signals()

    def analyze(self) -> RejectionLearningResult:
        self._ensure_loaded()
        result = RejectionLearningResult(timestamp=time.time())
        result.total_rejected = len(self._rejected_signals)
        tracked = [s for s in self._rejected_signals if s.outcome_tracked]
        result.total_outcomes_tracked = len(tracked)
        if not tracked:
            return result

        result.false_rejections = sum(1 for s in tracked if s.would_have_been_profitable)
        result.good_rejections = len(tracked) - result.false_rejections
        result.false_rejection_rate = result.false_rejections / max(1, len(tracked))

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

        result.threshold_analysis = self._analyze_thresholds(tracked)
        result.recent_rejections = self._rejected_signals[-10:]
        return result

    def _analyze_thresholds(self, tracked: List[RejectedSignal]) -> ThresholdAnalysis:
        analysis = ThresholdAnalysis()
        if not tracked:
            return analysis

        eligibility_scores = []
        for s in tracked:
            score = s.scores.get("eligibility_score", s.scores.get("execution_score", 0))
            if score > 0:
                eligibility_scores.append((score, s.would_have_been_profitable, s.outcome_r))
        if not eligibility_scores:
            return analysis

        best_threshold = 90
        best_improvement = 0
        for test_threshold in range(70, 100, 5):
            rejected = [(score, prof, r) for score, prof, r in eligibility_scores if score < test_threshold]
            if not rejected:
                continue
            improvement = sum(r for _, prof, r in rejected if prof)
            if improvement > best_improvement:
                best_improvement = improvement
                best_threshold = test_threshold

        analysis.current_threshold = 90
        analysis.optimal_threshold = best_threshold
        analysis.potential_improvement_r = best_improvement
        current_rejected = [(score, prof, r) for score, prof, r in eligibility_scores if score < 90]
        if current_rejected:
            analysis.false_rejection_rate = sum(1 for _, prof, _ in current_rejected if prof) / len(current_rejected)

        if analysis.false_rejection_rate > FALSE_REJECTION_RATE_TARGET:
            analysis.recommendation = (
                f"Lower eligibility threshold from 90 to {best_threshold}. "
                f"False rejection rate {analysis.false_rejection_rate:.1%} exceeds "
                f"target {FALSE_REJECTION_RATE_TARGET:.1%}. Potential improvement: {best_improvement:.2f}R"
            )
        elif analysis.false_rejection_rate < 0.05:
            analysis.recommendation = "Thresholds are well-calibrated. False rejection rate is low."
        else:
            analysis.recommendation = (
                f"Thresholds are acceptable. False rejection rate {analysis.false_rejection_rate:.1%} is within target."
            )
        return analysis

    def get_false_rejection_rate(self) -> float:
        return self.analyze().false_rejection_rate

    def get_recommended_threshold(self) -> float:
        result = self.analyze()
        return result.threshold_analysis.optimal_threshold if result.threshold_analysis else 90

    def get_summary(self) -> Dict[str, Any]:
        return self.analyze().to_dict()
