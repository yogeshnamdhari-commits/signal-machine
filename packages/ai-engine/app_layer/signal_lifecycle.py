"""
Signal Lifecycle — Track each signal through the entire pipeline.

Per Priority 9: Track each signal through:
    Generated → Validated → Ranked → Queued → Executed → Managed → Closed → Learned

This makes it easy to diagnose where profitable opportunities are being lost.

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class LifecycleStage:
    """A single stage in the signal lifecycle."""
    stage: str = ""
    timestamp: float = 0.0
    duration_ms: float = 0.0
    result: str = ""  # PASS / FAIL / PENDING
    details: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "stage": self.stage,
            "timestamp": self.timestamp,
            "duration_ms": round(self.duration_ms, 2),
            "result": self.result,
            "details": self.details,
        }


@dataclass
class SignalLifecycle:
    """Complete lifecycle of a signal."""
    signal_id: str = ""
    symbol: str = ""
    side: str = ""
    stages: List[LifecycleStage] = field(default_factory=list)
    current_stage: str = ""
    final_decision: str = ""
    final_priority: str = ""
    total_duration_ms: float = 0.0

    # Outcome (if trade was executed)
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    realized_r: float = 0.0
    exit_reason: str = ""
    hold_minutes: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side,
            "stages": [s.to_dict() for s in self.stages],
            "current_stage": self.current_stage,
            "final_decision": self.final_decision,
            "final_priority": self.final_priority,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": round(self.pnl, 4),
            "realized_r": round(self.realized_r, 3),
            "exit_reason": self.exit_reason,
            "hold_minutes": round(self.hold_minutes, 0),
        }

    def get_stage_summary(self) -> str:
        """Get human-readable stage summary."""
        lines = [f"Signal: {self.symbol} {self.side}"]
        for s in self.stages:
            icon = "✓" if s.result == "PASS" else "✗" if s.result == "FAIL" else "⏳"
            lines.append(f"  {icon} {s.stage}: {s.result} ({s.duration_ms:.1f}ms)")
        lines.append(f"  → Final: {self.final_decision} [{self.final_priority}]")
        return "\n".join(lines)


class SignalLifecycleTracker:
    """
    Tracks each signal through the entire pipeline.

    Per Priority 9: Easy diagnosis of where opportunities are lost.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._active: Dict[str, SignalLifecycle] = {}
        self._history: List[SignalLifecycle] = []

    def start_tracking(self, signal_id: str, symbol: str, side: str) -> SignalLifecycle:
        """Start tracking a new signal."""
        lifecycle = SignalLifecycle(
            signal_id=signal_id,
            symbol=symbol,
            side=side,
        )
        self._active[signal_id] = lifecycle
        return lifecycle

    def record_stage(
        self,
        signal_id: str,
        stage: str,
        result: str,
        duration_ms: float = 0.0,
        details: Optional[Dict] = None,
    ) -> None:
        """Record a pipeline stage completion."""
        lifecycle = self._active.get(signal_id)
        if not lifecycle:
            return

        lifecycle.stages.append(LifecycleStage(
            stage=stage,
            timestamp=time.time(),
            duration_ms=duration_ms,
            result=result,
            details=details or {},
        ))
        lifecycle.current_stage = stage

    def complete_tracking(
        self,
        signal_id: str,
        decision: str,
        priority: str,
        total_duration_ms: float = 0.0,
    ) -> Optional[SignalLifecycle]:
        """Complete tracking for a signal."""
        lifecycle = self._active.pop(signal_id, None)
        if not lifecycle:
            return None

        lifecycle.final_decision = decision
        lifecycle.final_priority = priority
        lifecycle.total_duration_ms = total_duration_ms

        self._history.append(lifecycle)

        # Store in database
        self._store_lifecycle(lifecycle)

        return lifecycle

    def record_trade_outcome(
        self,
        signal_id: str,
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        pnl: float = 0.0,
        realized_r: float = 0.0,
        exit_reason: str = "",
        hold_minutes: float = 0.0,
    ) -> None:
        """Record the outcome of an executed trade."""
        # Find in history
        for lifecycle in self._history:
            if lifecycle.signal_id == signal_id:
                lifecycle.entry_price = entry_price
                lifecycle.exit_price = exit_price
                lifecycle.pnl = pnl
                lifecycle.realized_r = realized_r
                lifecycle.exit_reason = exit_reason
                lifecycle.hold_minutes = hold_minutes
                break

    def get_lifecycle(self, signal_id: str) -> Optional[SignalLifecycle]:
        """Get lifecycle for a signal."""
        # Check active
        if signal_id in self._active:
            return self._active[signal_id]
        # Check history
        for lc in self._history:
            if lc.signal_id == signal_id:
                return lc
        return None

    def get_funnel_stats(self) -> Dict:
        """Get pipeline funnel statistics."""
        all_lifecycles = list(self._active.values()) + self._history

        funnel = {
            "generated": len(all_lifecycles),
            "validated": 0,
            "ranked": 0,
            "queued": 0,
            "executed": 0,
            "managed": 0,
            "closed": 0,
            "learned": 0,
        }

        for lc in all_lifecycles:
            stages_seen = {s.stage for s in lc.stages}
            if "institution" in stages_seen:
                funnel["validated"] += 1
            if "expectancy" in stages_seen:
                funnel["ranked"] += 1
            if "portfolio" in stages_seen:
                funnel["queued"] += 1
            if lc.final_decision == "EXECUTE":
                funnel["executed"] += 1
            if lc.current_stage == "managed":
                funnel["managed"] += 1
            if lc.exit_reason:
                funnel["closed"] += 1

        return funnel

    def _store_lifecycle(self, lifecycle: SignalLifecycle) -> None:
        """Store lifecycle in database."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Create table if not exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS signal_lifecycle (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    stages TEXT,
                    final_decision TEXT,
                    final_priority TEXT,
                    total_duration_ms REAL,
                    entry_price REAL,
                    exit_price REAL,
                    pnl REAL,
                    realized_r REAL,
                    exit_reason TEXT,
                    hold_minutes REAL,
                    timestamp REAL
                )
            """)

            import json
            cur.execute("""
                INSERT INTO signal_lifecycle (
                    signal_id, symbol, side, stages, final_decision,
                    final_priority, total_duration_ms, entry_price, exit_price,
                    pnl, realized_r, exit_reason, hold_minutes, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lifecycle.signal_id, lifecycle.symbol, lifecycle.side,
                json.dumps([s.to_dict() for s in lifecycle.stages]),
                lifecycle.final_decision, lifecycle.final_priority,
                lifecycle.total_duration_ms,
                lifecycle.entry_price, lifecycle.exit_price,
                lifecycle.pnl, lifecycle.realized_r,
                lifecycle.exit_reason, lifecycle.hold_minutes,
                time.time(),
            ))

            conn.commit()
            conn.close()

        except Exception as e:
            logger.warning("Lifecycle storage error: {}", e)
