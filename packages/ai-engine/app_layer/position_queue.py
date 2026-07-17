"""
Position Queue — Ranks and queues simultaneous high-quality trades.

READ-ONLY with respect to upstream data. Never modifies signals or positions.

Per Master Directive:
    "If ten high-quality trades appear simultaneously:
     Rank → Top 3 → Execute → Keep remainder on watch.
     This improves capital efficiency."

Queue Logic:
    1. Score all pending signals
    2. Rank by composite score (EV + TQ + Execution Quality)
    3. Execute top N (configurable, default 3)
    4. Keep remainder on watch list
    5. Monitor watch list for upgrades
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# QUEUE CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Maximum simultaneous executions per cycle
MAX_EXECUTIONS_PER_CYCLE = 3

# Maximum watch list size
MAX_WATCH_LIST = 10

# Minimum composite score to be queued
MIN_QUEUE_SCORE = 60.0

# Watch list upgrade threshold (score must improve by this much)
UPGRADE_THRESHOLD = 5.0


@dataclass
class QueuedSignal:
    """A signal in the queue."""
    symbol: str = ""
    side: str = ""
    composite_score: float = 0.0
    expected_value_r: float = 0.0
    trade_quality_score: float = 0.0
    execution_quality: float = 0.0
    priority: str = "REJECT"
    status: str = "QUEUED"  # QUEUED / EXECUTING / WATCHING / REJECTED / EXECUTED
    queued_at: float = 0.0
    signal_data: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "composite_score": round(self.composite_score, 2),
            "expected_value_r": round(self.expected_value_r, 3),
            "trade_quality_score": round(self.trade_quality_score, 1),
            "execution_quality": round(self.execution_quality, 1),
            "priority": self.priority,
            "status": self.status,
            "age_seconds": round(time.time() - self.queued_at, 0) if self.queued_at else 0,
        }


@dataclass
class QueueDecision:
    """Decision from the position queue."""
    executing: List[QueuedSignal] = field(default_factory=list)
    watching: List[QueuedSignal] = field(default_factory=list)
    rejected: List[QueuedSignal] = field(default_factory=list)
    total_queued: int = 0
    total_executing: int = 0
    total_watching: int = 0

    def to_dict(self) -> Dict:
        return {
            "executing": [s.to_dict() for s in self.executing],
            "watching": [s.to_dict() for s in self.watching],
            "rejected": [s.to_dict() for s in self.rejected],
            "total_queued": self.total_queued,
            "total_executing": self.total_executing,
            "total_watching": self.total_watching,
        }


class PositionQueue:
    """
    Ranks and queues simultaneous high-quality trades.

    Per Master Directive: Execute top 3, keep remainder on watch.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self._queue: List[QueuedSignal] = []
        self._watch_list: List[QueuedSignal] = []
        self._history: List[QueuedSignal] = []

    def add_signals(
        self,
        signals: List[Dict[str, Any]],
        expected_values: Optional[Dict[str, float]] = None,
        trade_quality_scores: Optional[Dict[str, float]] = None,
        execution_qualities: Optional[Dict[str, float]] = None,
    ) -> QueueDecision:
        """
        Add signals to queue and make execution decisions.

        Args:
            signals: List of signal dicts
            expected_values: Dict of symbol → EV in R-multiples
            trade_quality_scores: Dict of symbol → TQ score
            execution_qualities: Dict of symbol → execution quality score

        Returns:
            QueueDecision with execution and watch lists
        """
        evs = expected_values or {}
        tqs = trade_quality_scores or {}
        eqs = execution_qualities or {}

        # ── Score and queue signals ──
        for sig in signals:
            symbol = sig.get("symbol", "")
            side = sig.get("side", "")
            key = f"{symbol}_{side}"

            # Composite score: 40% EV + 30% TQ + 30% Execution Quality
            ev = evs.get(key, 0)
            tq = tqs.get(key, 50)
            eq = eqs.get(key, 50)

            composite = ev * 0.4 + tq * 0.3 + eq * 0.3

            if composite < MIN_QUEUE_SCORE:
                continue

            queued = QueuedSignal(
                symbol=symbol,
                side=side,
                composite_score=composite,
                expected_value_r=ev,
                trade_quality_score=tq,
                execution_quality=eq,
                priority=self._classify_priority(composite),
                status="QUEUED",
                queued_at=time.time(),
                signal_data=sig,
            )
            self._queue.append(queued)

        # ── Sort by composite score ──
        self._queue.sort(key=lambda s: s.composite_score, reverse=True)

        # ── Make decisions ──
        decision = QueueDecision(total_queued=len(self._queue))

        executing = []
        watching = []
        rejected = []

        for queued in self._queue:
            if len(executing) < MAX_EXECUTIONS_PER_CYCLE:
                queued.status = "EXECUTING"
                executing.append(queued)
            elif len(watching) < MAX_WATCH_LIST:
                queued.status = "WATCHING"
                watching.append(queued)
            else:
                queued.status = "REJECTED"
                rejected.append(queued)

        decision.executing = executing
        decision.watching = watching
        decision.rejected = rejected
        decision.total_executing = len(executing)
        decision.total_watching = len(watching)

        # Move to watch list and history
        self._watch_list = watching
        self._history.extend(executing)
        self._history.extend(rejected)
        self._queue.clear()

        logger.info(
            "QUEUE: {} signals → {} executing, {} watching, {} rejected",
            decision.total_queued, decision.total_executing,
            decision.total_watching, len(rejected),
        )

        return decision

    def check_watch_list(self) -> List[QueuedSignal]:
        """
        Check watch list for signals that should be promoted.

        Returns:
            List of signals upgraded from watching to executing
        """
        promoted = []
        remaining = []

        for queued in self._watch_list:
            # Check if signal is still fresh (< 5 minutes)
            age = time.time() - queued.queued_at
            if age > 300:  # 5 minutes
                queued.status = "REJECTED"
                self._history.append(queued)
                continue

            # Check for score improvement (would need re-evaluation)
            # For now, just keep on watch
            remaining.append(queued)

        self._watch_list = remaining
        return promoted

    def get_watch_list(self) -> List[QueuedSignal]:
        """Get current watch list."""
        return list(self._watch_list)

    def get_stats(self) -> Dict:
        """Get queue statistics."""
        return {
            "queue_size": len(self._queue),
            "watch_list_size": len(self._watch_list),
            "history_size": len(self._history),
        }

    @staticmethod
    def _classify_priority(composite: float) -> str:
        """Classify composite score into priority bucket."""
        if composite >= 85:
            return "ELITE"
        elif composite >= 75:
            return "HIGH"
        elif composite >= 60:
            return "MEDIUM"
        else:
            return "LOW"
