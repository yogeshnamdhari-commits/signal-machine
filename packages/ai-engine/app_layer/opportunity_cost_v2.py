"""
Opportunity Cost Tracker v2 — Track ignored trades and their outcomes.

Per Executive Assessment v17:
    "Suppose 100 signals appeared. The engine traded 8. The remaining 92
     were ignored. How many of those 92 would actually have been profitable?
     Track:
         - Ignored winners
         - Ignored losers
     That tells you whether the filtering layer is too conservative
     or too permissive."

Key Innovation:
    v20 measured: Rejected trades only (after rejection)
    v23 measures: All ignored signals (before and after)

    This allows:
        - Measuring filter conservatism
        - Identifying missed opportunities
        - Balancing selectivity vs opportunity cost
        - Calibrating admission thresholds

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"
_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "opportunity_cost_v2.json"


@dataclass
class IgnoredSignal:
    """Record of an ignored signal and its eventual outcome."""
    signal_id: str = ""
    symbol: str = ""
    side: str = ""
    signal_time: float = 0.0
    signal_score: float = 0.0
    rejection_reason: str = ""

    # Outcome tracking
    outcome_tracked: bool = False
    actual_r: float = 0.0
    would_have_been_profitable: bool = False
    would_have_exceeded_3r: bool = False

    def to_dict(self) -> Dict:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side,
            "score": round(self.signal_score, 1),
            "reason": self.rejection_reason,
            "tracked": self.outcome_tracked,
            "actual_r": round(self.actual_r, 3),
            "profitable": self.would_have_been_profitable,
            "exceeded_3r": self.would_have_exceeded_3r,
        }


@dataclass
class OpportunityCostMetrics:
    """Aggregated opportunity cost metrics."""
    # Sample sizes
    total_ignored: int = 0
    tracked_ignored: int = 0

    # Outcomes
    ignored_winners: int = 0     # Would have been profitable
    ignored_losers: int = 0      # Would have lost
    ignored_3r_plus: int = 0     # Would have exceeded +3R

    # Rates
    false_rejection_rate: float = 0.0  # Ignored but would have won
    ignored_winner_rate: float = 0.0    # % of ignored that were profitable

    # Profit missed
    total_missed_r: float = 0.0         # Total R missed from ignored winners
    avg_missed_r: float = 0.0           # Average R of ignored winners
    opportunity_cost_per_signal: float = 0.0

    # Filter assessment
    filter_assessment: str = ""  # TOO_CONSERVATIVE / BALANCED / TOO_PERMISSIVE

    def to_dict(self) -> Dict:
        return {
            "sample_sizes": {
                "total_ignored": self.total_ignored,
                "tracked": self.tracked_ignored,
            },
            "outcomes": {
                "winners": self.ignored_winners,
                "losers": self.ignored_losers,
                "exceeded_3r": self.ignored_3r_plus,
            },
            "rates": {
                "false_rejection_rate": round(self.false_rejection_rate, 3),
                "ignored_winner_rate": round(self.ignored_winner_rate, 1),
            },
            "profit_missed": {
                "total_r": round(self.total_missed_r, 3),
                "avg_r": round(self.avg_missed_r, 3),
                "per_signal": round(self.opportunity_cost_per_signal, 3),
            },
            "filter_assessment": self.filter_assessment,
        }


@dataclass
class OpportunityCostReport:
    """Complete opportunity cost analysis."""
    timestamp: float = 0.0
    metrics: OpportunityCostMetrics = field(default_factory=OpportunityCostMetrics)

    # Diagnosis
    is_filter_too_conservative: bool = False
    diagnosis: str = ""
    recommendation: str = ""

    # Threshold adjustment
    suggested_threshold_change: float = 0.0  # Negative = lower threshold

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "metrics": self.metrics.to_dict(),
            "diagnosis": {
                "too_conservative": self.is_filter_too_conservative,
                "description": self.diagnosis,
                "recommendation": self.recommendation,
            },
            "threshold_adjustment": round(self.suggested_threshold_change, 1),
        }


class OpportunityCostTrackerV2:
    """
    Tracks all ignored signals and their outcomes.

    Per Executive Assessment v17:
        "That tells you whether the filtering layer is too conservative
         or too permissive."

    This engine:
        1. Logs all ignored signals
        2. Matches to actual outcomes
        3. Calculates false rejection rate
        4. Measures missed profit
        5. Recommends threshold adjustments

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None, log_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._log_path = log_path or _LOG_PATH
        self._records: List[IgnoredSignal] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load records from log file."""
        if time.time() - self._last_load < 300:
            return
        self._load_records()

    def _load_records(self) -> None:
        """Load ignored signal records from JSON log."""
        try:
            if self._log_path.exists():
                with open(self._log_path, "r") as f:
                    data = json.load(f)
                valid_fields = {f.name for f in IgnoredSignal.__dataclass_fields__.values()}
                filtered_data = [{k: v for k, v in item.items() if k in valid_fields} for item in data]
                self._records = [IgnoredSignal(**item) for item in filtered_data]
            else:
                self._records = []
            self._last_load = time.time()
        except Exception as e:
            logger.warning("Could not load opportunity cost tracker v2: {}", e)
            self._records = []

    def _save_records(self) -> None:
        """Save records to JSON log."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "w") as f:
                json.dump([r.to_dict() for r in self._records[-2000:]], f, indent=2)
        except Exception as e:
            logger.warning("Could not save opportunity cost records: {}", e)

    def log_ignored(
        self,
        symbol: str,
        side: str,
        score: float = 0.0,
        reason: str = "",
    ) -> None:
        """Log an ignored signal for later outcome tracking."""
        record = IgnoredSignal(
            signal_id=f"{symbol}_{side}_{int(time.time())}",
            symbol=symbol,
            side=side,
            signal_time=time.time(),
            signal_score=score,
            rejection_reason=reason,
        )
        self._records.append(record)
        self._save_records()

    def track_outcome(
        self,
        symbol: str,
        side: str,
        actual_r: float,
    ) -> None:
        """Track the outcome of a previously ignored signal."""
        cutoff = time.time() - 86400  # 24 hours
        for record in reversed(self._records):
            if (record.symbol == symbol
                and record.side == side
                and record.signal_time > cutoff
                and not record.outcome_tracked):

                record.outcome_tracked = True
                record.actual_r = actual_r
                record.would_have_been_profitable = actual_r > 0
                record.would_have_exceeded_3r = actual_r >= 3.0

                logger.debug(
                    "📊 OPPORTUNITY TRACKED: {} {} score={:.1f} actual={:.2f}R → {}",
                    symbol, side, record.signal_score, actual_r,
                    "WINNER" if record.would_have_been_profitable else "LOSER",
                )
                break

        self._save_records()

    def analyze(self) -> OpportunityCostReport:
        """Analyze opportunity cost and generate report."""
        self._ensure_loaded()

        report = OpportunityCostReport(timestamp=time.time())

        tracked = [r for r in self._records if r.outcome_tracked]
        report.metrics.total_ignored = len(self._records)
        report.metrics.tracked_ignored = len(tracked)

        if not tracked:
            report.diagnosis = "No tracked outcomes yet"
            report.recommendation = "Continue tracking ignored signals"
            return report

        # ── Outcomes ──
        report.metrics.ignored_winners = sum(1 for r in tracked if r.would_have_been_profitable)
        report.metrics.ignored_losers = sum(1 for r in tracked if not r.would_have_been_profitable)
        report.metrics.ignored_3r_plus = sum(1 for r in tracked if r.would_have_exceeded_3r)

        # ── Rates ──
        report.metrics.false_rejection_rate = report.metrics.ignored_winners / max(1, len(tracked))
        report.metrics.ignored_winner_rate = report.metrics.false_rejection_rate * 100

        # ── Profit missed ──
        winners = [r for r in tracked if r.would_have_been_profitable]
        report.metrics.total_missed_r = sum(r.actual_r for r in winners)
        report.metrics.avg_missed_r = report.metrics.total_missed_r / max(1, len(winners))
        report.metrics.opportunity_cost_per_signal = report.metrics.total_missed_r / max(1, len(tracked))

        # ── Filter assessment ──
        if report.metrics.false_rejection_rate > 0.3:
            report.metrics.filter_assessment = "TOO_CONSERVATIVE"
            report.is_filter_too_conservative = True
        elif report.metrics.false_rejection_rate < 0.1:
            report.metrics.filter_assessment = "TOO_PERMISSIVE"
        else:
            report.metrics.filter_assessment = "BALANCED"

        # ── Diagnosis ──
        if report.metrics.false_rejection_rate > 0.3:
            report.diagnosis = (
                f"Filter is too conservative — {report.metrics.false_rejection_rate:.0%} of "
                f"ignored trades would have been profitable"
            )
            report.recommendation = "Lower admission threshold to capture more opportunities"
            report.suggested_threshold_change = -5.0
        elif report.metrics.false_rejection_rate < 0.1:
            report.diagnosis = "Filter is well-calibrated — few profitable trades are being rejected"
            report.recommendation = "Maintain current threshold"
        else:
            report.diagnosis = "Filter is balanced — acceptable false rejection rate"
            report.recommendation = "Continue monitoring"

        return report
