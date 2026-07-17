"""
Opportunity Cost Tracker — What did rejected trades do later?

Per Executive Assessment v5:
    "For every rejected trade:
         Rejected ↓ Later +5R
         Rejected ↓ Later -2R

     Without this, it's difficult to know whether increased selectivity
     is improving profitability or simply reducing activity."

Key Features:
    1. Signal Logging — record all signals with rejection reason
    2. Outcome Tracking — track what happened to rejected signals
    3. Opportunity Cost Calculation — missed profit from rejections
    4. Threshold Calibration — are we rejecting too many good trades?
    5. Cost-Benefit Analysis — is selectivity worth the opportunity cost?

READ-ONLY: Never modifies upstream data. Uses separate log file.
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
_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "opportunity_cost.json"


@dataclass
class OpportunityRecord:
    """Record of a rejected signal and its eventual outcome."""
    signal_id: str = ""
    symbol: str = ""
    side: str = ""
    rejection_time: float = 0.0
    rejection_stage: str = ""
    rejection_reason: str = ""
    scores: Dict[str, float] = field(default_factory=dict)

    # Outcome tracking
    outcome_tracked: bool = False
    outcome_time: float = 0.0
    outcome_r: float = 0.0
    outcome_pnl: float = 0.0
    would_have_been_profitable: bool = False
    would_have_exceeded_3r: bool = False
    would_have_exceeded_5r: bool = False

    def to_dict(self) -> Dict:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side,
            "rejection_time": self.rejection_time,
            "rejection_stage": self.rejection_stage,
            "rejection_reason": self.rejection_reason,
            "scores": self.scores,
            "outcome_tracked": self.outcome_tracked,
            "outcome_r": round(self.outcome_r, 3),
            "would_have_been_profitable": self.would_have_been_profitable,
            "would_have_exceeded_3r": self.would_have_exceeded_3r,
            "would_have_exceeded_5r": self.would_have_exceeded_5r,
        }


@dataclass
class OpportunityCostDashboard:
    """Complete opportunity cost analysis."""
    timestamp: float = 0.0

    # Overall metrics
    total_rejected: int = 0
    total_tracked: int = 0
    false_rejection_rate: float = 0.0  # Rejected but would have been profitable
    missed_profit_r: float = 0.0       # Total R missed from false rejections
    missed_3r_trades: int = 0          # Trades that would have exceeded +3R
    missed_5r_trades: int = 0          # Trades that would have exceeded +5R

    # Cost-benefit
    avg_rejected_r: float = 0.0        # Average R of rejected trades
    avg_accepted_r: float = 0.0        # Average R of accepted trades
    selectivity_ratio: float = 0.0     # avg accepted / avg rejected
    opportunity_cost_per_rejection: float = 0.0

    # By stage
    by_stage: Dict[str, Dict] = field(default_factory=dict)

    # By symbol
    by_symbol: Dict[str, Dict] = field(default_factory=dict)

    # Recent
    recent_rejections: List[OpportunityRecord] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "overall": {
                "total_rejected": self.total_rejected,
                "total_tracked": self.total_tracked,
                "false_rejection_rate": round(self.false_rejection_rate, 3),
                "missed_profit_r": round(self.missed_profit_r, 3),
                "missed_3r_trades": self.missed_3r_trades,
                "missed_5r_trades": self.missed_5r_trades,
                "avg_rejected_r": round(self.avg_rejected_r, 3),
                "selectivity_ratio": round(self.selectivity_ratio, 3),
                "opportunity_cost_per_rejection": round(self.opportunity_cost_per_rejection, 3),
            },
            "by_stage": self.by_stage,
            "by_symbol": self.by_symbol,
            "recent_rejections": [r.to_dict() for r in self.recent_rejections],
        }


class OpportunityCostTracker:
    """
    Tracks opportunity cost of rejected trades.

    Per Executive Assessment v5:
        "Without this, it's difficult to know whether increased selectivity
         is improving profitability or simply reducing activity."

    This engine:
        1. Logs all rejected signals
        2. Attempts to track outcomes (did the rejected signal later appear?)
        3. Calculates missed profit from false rejections
        4. Provides cost-benefit analysis of selectivity
        5. Recommends threshold adjustments

    READ-ONLY: Never modifies upstream data. Uses separate log file.
    """

    def __init__(self, db_path: Optional[Path] = None, log_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._log_path = log_path or _LOG_PATH
        self._records: List[OpportunityRecord] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load records from log file."""
        if time.time() - self._last_load < 300:
            return
        self._load_records()

    def _load_records(self) -> None:
        """Load records from JSON log."""
        try:
            if self._log_path.exists():
                with open(self._log_path, "r") as f:
                    data = json.load(f)
                self._records = [OpportunityRecord(**item) for item in data]
            else:
                self._records = []
            self._last_load = time.time()
        except Exception as e:
            logger.warning("Could not load opportunity cost tracker: {}", e)
            self._records = []

    def _save_records(self) -> None:
        """Save records to JSON log."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "w") as f:
                json.dump([r.to_dict() for r in self._records], f, indent=2)
        except Exception as e:
            logger.warning("Could not save opportunity cost records: {}", e)

    def log_rejection(
        self,
        symbol: str,
        side: str,
        rejection_stage: str,
        rejection_reason: str,
        scores: Optional[Dict[str, float]] = None,
    ) -> None:
        """Log a rejected signal for later outcome tracking."""
        record = OpportunityRecord(
            signal_id=f"{symbol}_{side}_{int(time.time())}",
            symbol=symbol,
            side=side,
            rejection_time=time.time(),
            rejection_stage=rejection_stage,
            rejection_reason=rejection_reason,
            scores=scores or {},
        )
        self._records.append(record)

        # Keep only recent records (last 2000)
        if len(self._records) > 2000:
            self._records = self._records[-2000:]

        self._save_records()

    def track_outcome(
        self,
        symbol: str,
        side: str,
        realized_r: float,
        pnl: float,
    ) -> None:
        """Track the outcome of a previously rejected signal."""
        cutoff = time.time() - 86400  # 24 hours
        for record in reversed(self._records):
            if (record.symbol == symbol
                and record.side == side
                and record.rejection_time > cutoff
                and not record.outcome_tracked):

                record.outcome_tracked = True
                record.outcome_time = time.time()
                record.outcome_r = realized_r
                record.outcome_pnl = pnl
                record.would_have_been_profitable = realized_r > 0
                record.would_have_exceeded_3r = realized_r >= 3.0
                record.would_have_exceeded_5r = realized_r >= 5.0

                logger.debug(
                    "📊 OPPORTUNITY TRACKED: {} {} rejected at {} → outcome={:.2f}R",
                    symbol, side, record.rejection_stage, realized_r,
                )
                break

        self._save_records()

    def get_dashboard(self) -> OpportunityCostDashboard:
        """Get complete opportunity cost analysis."""
        self._ensure_loaded()

        dash = OpportunityCostDashboard(timestamp=time.time())
        dash.total_rejected = len(self._records)

        tracked = [r for r in self._records if r.outcome_tracked]
        dash.total_tracked = len(tracked)

        if not tracked:
            return dash

        # ── Overall metrics ──
        profitable = [r for r in tracked if r.would_have_been_profitable]
        dash.false_rejection_rate = len(profitable) / max(1, len(tracked))
        dash.missed_profit_r = sum(r.outcome_r for r in profitable)
        dash.missed_3r_trades = sum(1 for r in tracked if r.would_have_exceeded_3r)
        dash.missed_5r_trades = sum(1 for r in tracked if r.would_have_exceeded_5r)
        dash.avg_rejected_r = sum(r.outcome_r for r in tracked) / max(1, len(tracked))
        dash.opportunity_cost_per_rejection = dash.missed_profit_r / max(1, len(tracked))

        # ── By Stage ──
        by_stage: Dict[str, List[OpportunityRecord]] = defaultdict(list)
        for r in tracked:
            by_stage[r.rejection_stage].append(r)

        for stage, records in by_stage.items():
            profitable_in_stage = [r for r in records if r.would_have_been_profitable]
            dash.by_stage[stage] = {
                "total": len(records),
                "false_rejections": len(profitable_in_stage),
                "false_rejection_rate": len(profitable_in_stage) / max(1, len(records)),
                "missed_profit_r": sum(r.outcome_r for r in profitable_in_stage),
                "avg_outcome_r": sum(r.outcome_r for r in records) / max(1, len(records)),
            }

        # ── By Symbol ──
        by_symbol: Dict[str, List[OpportunityRecord]] = defaultdict(list)
        for r in tracked:
            by_symbol[r.symbol].append(r)

        for sym, records in by_symbol.items():
            profitable_in_sym = [r for r in records if r.would_have_been_profitable]
            if len(records) >= 3:  # Only show symbols with enough data
                dash.by_symbol[sym] = {
                    "total": len(records),
                    "false_rejections": len(profitable_in_sym),
                    "missed_profit_r": sum(r.outcome_r for r in profitable_in_sym),
                }

        # ── Recent ──
        dash.recent_rejections = self._records[-10:]

        return dash

    def get_false_rejection_rate(self) -> float:
        """Get current false rejection rate."""
        dash = self.get_dashboard()
        return dash.false_rejection_rate

    def get_summary(self) -> Dict[str, Any]:
        """Get opportunity cost summary."""
        dash = self.get_dashboard()
        return dash.to_dict()
