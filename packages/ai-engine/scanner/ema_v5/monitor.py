"""
EMA_V5 Pipeline Monitor — Permanent observability layer.

Provides:
1. Stage-by-stage lifecycle audit for every candidate
2. Runtime health watchdog (stall detection)
3. Transition timeout protection
4. Daily reconciliation reports

READ-ONLY: Never modifies trading logic.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# ══════════════════════════════════════════════════════════════════════════════
# 1. STAGE-BY-STAGE LIFECYCLE AUDIT
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class StageResult:
    """Result of a single pipeline stage evaluation."""
    stage: str
    timestamp: float
    passed: bool
    reason: str = ""
    score: float = 0.0
    detail: str = ""


@dataclass
class CandidateAudit:
    """Complete lifecycle audit for a single candidate."""
    symbol: str
    side: str
    entered_at: float = 0.0
    stages: List[StageResult] = field(default_factory=list)
    final_state: str = ""
    published: bool = False
    rejection_reason: str = ""

    def add_stage(self, stage: str, passed: bool, reason: str = "",
                  score: float = 0.0, detail: str = "") -> None:
        self.stages.append(StageResult(
            stage=stage,
            timestamp=time.time(),
            passed=passed,
            reason=reason,
            score=score,
            detail=detail,
        ))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entered_at": self.entered_at,
            "stages": [
                {
                    "stage": s.stage,
                    "timestamp": s.timestamp,
                    "passed": s.passed,
                    "reason": s.reason,
                    "score": s.score,
                    "detail": s.detail,
                }
                for s in self.stages
            ],
            "final_state": self.final_state,
            "published": self.published,
            "rejection_reason": self.rejection_reason,
        }


class LifecycleAudit:
    """Tracks per-candidate lifecycle through every pipeline stage."""

    def __init__(self) -> None:
        self._candidates: Dict[str, CandidateAudit] = {}
        self._completed: List[CandidateAudit] = []
        self._max_history = 500

    def start(self, symbol: str, side: str) -> CandidateAudit:
        """Begin tracking a new candidate."""
        audit = CandidateAudit(
            symbol=symbol,
            side=side,
            entered_at=time.time(),
        )
        self._candidates[symbol] = audit
        return audit

    def stage_pass(self, symbol: str, stage: str, reason: str = "",
                   score: float = 0.0, detail: str = "") -> None:
        """Record a stage pass."""
        audit = self._candidates.get(symbol)
        if audit:
            audit.add_stage(stage, passed=True, reason=reason, score=score, detail=detail)

    def stage_reject(self, symbol: str, stage: str, reason: str = "",
                     score: float = 0.0, detail: str = "") -> None:
        """Record a stage rejection and finalize the candidate."""
        audit = self._candidates.get(symbol)
        if audit:
            audit.add_stage(stage, passed=False, reason=reason, score=score, detail=detail)
            audit.final_state = f"REJECTED:{stage}"
            audit.rejection_reason = reason
            self._finalize(symbol)

    def publish(self, symbol: str, stage: str = "signal_engine") -> None:
        """Record successful publication."""
        audit = self._candidates.get(symbol)
        if audit:
            audit.add_stage(stage, passed=True, reason="published")
            audit.final_state = "PUBLISHED"
            audit.published = True
            self._finalize(symbol)

    def _finalize(self, symbol: str) -> None:
        """Move candidate from active to completed."""
        audit = self._candidates.pop(symbol, None)
        if audit:
            self._completed.append(audit)
            if len(self._completed) > self._max_history:
                self._completed = self._completed[-self._max_history:]

    def get_active(self) -> Dict[str, CandidateAudit]:
        return dict(self._candidates)

    def get_completed(self, n: int = 50) -> List[CandidateAudit]:
        return self._completed[-n:]

    def get_rejection_summary(self, n: int = 100) -> Dict[str, int]:
        """Count rejections by stage from recent completed candidates."""
        summary: Dict[str, int] = defaultdict(int)
        for audit in self._completed[-n:]:
            if not audit.published and audit.stages:
                last = audit.stages[-1]
                if not last.passed:
                    summary[last.stage] += 1
        return dict(summary)

    def to_bridge(self) -> Dict[str, Any]:
        """Export for dashboard consumption."""
        recent = self._completed[-20:]
        return {
            "active_candidates": len(self._candidates),
            "completed_audits": len(self._completed),
            "recent_rejections": self.get_rejection_summary(),
            "recent_audits": [a.to_dict() for a in recent],
        }


# ══════════════════════════════════════════════════════════════════════════════
# 2. STALL DETECTION WATCHDOG
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class WatchdogState:
    """Current watchdog observations."""
    last_signal_time: float = 0.0
    last_signal_symbol: str = ""
    stall_start: float = 0.0
    stall_detected: bool = False
    stall_duration_sec: float = 0.0
    stall_reason: str = ""
    consecutive_zero_signal_cycles: int = 0
    last_warning_time: float = 0.0


class StallDetector:
    """Detects when the pipeline has stopped producing signals."""

    # Thresholds
    STALL_THRESHOLD_HOURS = 6.0
    WARNING_COOLDOWN_SEC = 1800  # 30 minutes between warnings

    def __init__(self) -> None:
        self._state = WatchdogState()
        self._cycle_signals = 0
        self._cycle_candidates_by_state: Dict[str, int] = defaultdict(int)

    def record_signal(self, symbol: str) -> None:
        """Record a successful signal publication."""
        self._state.last_signal_time = time.time()
        self._state.last_signal_symbol = symbol
        self._state.stall_detected = False
        self._state.stall_start = 0.0
        self._state.consecutive_zero_signal_cycles = 0

    def record_cycle(self, signals_this_cycle: int,
                     state_counts: Dict[str, int]) -> None:
        """Record a scan cycle completion."""
        self._cycle_signals = signals_this_cycle
        self._cycle_candidates_by_state = dict(state_counts)

        if signals_this_cycle == 0:
            self._state.consecutive_zero_signal_cycles += 1
        else:
            self._state.consecutive_zero_signal_cycles = 0

    def check(self) -> Optional[Dict[str, Any]]:
        """Check for stall condition. Returns alert dict or None."""
        now = time.time()

        if self._state.last_signal_time == 0:
            # No signals ever recorded — check if this is initial startup
            return None

        time_since_signal = now - self._state.last_signal_time
        hours_since = time_since_signal / 3600

        if hours_since > self.STALL_THRESHOLD_HOURS:
            if not self._state.stall_detected:
                self._state.stall_detected = True
                self._state.stall_start = now

            self._state.stall_duration_sec = time_since_signal

            # Throttle warnings
            if now - self._state.last_warning_time > self.WARNING_COOLDOWN_SEC:
                self._state.last_warning_time = now
                alert = {
                    "type": "STALL_WARNING",
                    "severity": "critical",
                    "message": (
                        f"⚠️ SIGNAL PIPELINE STALLED\n"
                        f"No confirmed signals for {hours_since:.1f} hours.\n"
                        f"Last signal: {self._state.last_signal_symbol}"
                    ),
                    "hours_since_signal": round(hours_since, 1),
                    "state_counts": dict(self._cycle_candidates_by_state),
                    "consecutive_zero_cycles": self._state.consecutive_zero_signal_cycles,
                    "last_signal_symbol": self._state.last_signal_symbol,
                    "timestamp": now,
                }
                logger.warning(
                    "🚨 STALL: No signals for {:.1f}h | states={} | last={}",
                    hours_since,
                    dict(self._cycle_candidates_by_state),
                    self._state.last_signal_symbol,
                )
                return alert

        return None

    def to_bridge(self) -> Dict[str, Any]:
        """Export for dashboard."""
        now = time.time()
        hours_since = (now - self._state.last_signal_time) / 3600 if self._state.last_signal_time else 0
        return {
            "stall_detected": self._state.stall_detected,
            "hours_since_last_signal": round(hours_since, 1),
            "last_signal_symbol": self._state.last_signal_symbol,
            "consecutive_zero_cycles": self._state.consecutive_zero_signal_cycles,
            "state_counts": dict(self._cycle_candidates_by_state),
        }


# ══════════════════════════════════════════════════════════════════════════════
# 3. TRANSITION TIMEOUT PROTECTION
# ══════════════════════════════════════════════════════════════════════════════

# Maximum time (seconds) a symbol can remain in each state
STATE_TIMEOUTS = {
    "BUY_MODE": 8 * 3600,               # 8 hours
    "SELL_MODE": 8 * 3600,               # 8 hours
    "WAITING_PULLBACK": 12 * 300,        # ~12 candles at 5m
    "WAITING_CONFIRMATION": 6 * 300,     # ~6 candles at 5m
}


class TransitionTimeout:
    """Enforces maximum lifetime for each state."""

    def __init__(self) -> None:
        self._entry_times: Dict[str, Dict[str, float]] = {}  # symbol → {state, time}
        self._timeouts_triggered: Dict[str, int] = defaultdict(int)

    def enter_state(self, symbol: str, state: str) -> None:
        """Record when a symbol enters a state."""
        self._entry_times[symbol] = {"state": state, "time": time.time()}

    def exit_state(self, symbol: str) -> None:
        """Record when a symbol leaves a state."""
        self._entry_times.pop(symbol, None)

    def check_timeouts(self, state_manager) -> List[Dict[str, Any]]:
        """Check all symbols for state timeouts. Returns list of timeout events."""
        now = time.time()
        events = []

        for symbol, entry in list(self._entry_times.items()):
            state = entry["state"]
            duration = now - entry["time"]
            timeout = STATE_TIMEOUTS.get(state, 8 * 3600)

            if duration > timeout:
                self._timeouts_triggered[state] += 1
                events.append({
                    "symbol": symbol,
                    "state": state,
                    "duration_sec": round(duration),
                    "timeout_sec": timeout,
                    "action": "reset_to_NO_TREND",
                })
                logger.warning(
                    "⏰ TIMEOUT: {} in {} for {:.0f}s (max {:.0f}s) → resetting",
                    symbol, state, duration, timeout,
                )
                self._entry_times.pop(symbol, None)

        return events

    def to_bridge(self) -> Dict[str, Any]:
        """Export for dashboard."""
        now = time.time()
        active = {}
        for sym, entry in self._entry_times.items():
            state = entry["state"]
            duration = now - entry["time"]
            timeout = STATE_TIMEOUTS.get(state, 8 * 3600)
            active[sym] = {
                "state": state,
                "duration_sec": round(duration),
                "timeout_sec": timeout,
                "pct_used": round(duration / timeout * 100, 1) if timeout > 0 else 0,
            }
        return {
            "active_states": active,
            "timeouts_triggered": dict(self._timeouts_triggered),
        }


# ══════════════════════════════════════════════════════════════════════════════
# 4. DAILY RECONCILIATION
# ══════════════════════════════════════════════════════════════════════════════

class DailyReconciliation:
    """End-of-day funnel accounting."""

    def __init__(self) -> None:
        self._today_stats: Dict[str, int] = defaultdict(int)
        self._today_rejections: Dict[str, int] = defaultdict(int)
        self._today_date: str = ""
        self._log_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs"

    def _check_day(self) -> None:
        """Reset counters on new day."""
        today = time.strftime("%Y-%m-%d")
        if today != self._today_date:
            if self._today_date and any(v > 0 for v in self._today_stats.values()):
                self._write_report()
            self._today_stats = defaultdict(int)
            self._today_rejections = defaultdict(int)
            self._today_date = today

    def record_event(self, event_type: str) -> None:
        """Record a pipeline event."""
        self._check_day()
        self._today_stats[event_type] += 1

    def record_rejection(self, stage: str) -> None:
        """Record a rejection by stage."""
        self._check_day()
        self._today_rejections[stage] += 1

    def _write_report(self) -> None:
        """Write daily reconciliation report."""
        try:
            report = {
                "date": self._today_date,
                "events": dict(self._today_stats),
                "rejections": dict(self._today_rejections),
                "timestamp": time.time(),
            }
            path = self._log_path / f"ema_v5_reconciliation_{self._today_date}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(report, f, indent=2)
            logger.info(
                "📊 DAILY_RECONCILIATION {}: {} events, {} rejections",
                self._today_date,
                sum(self._today_stats.values()),
                sum(self._today_rejections.values()),
            )
        except Exception as e:
            logger.debug("Daily reconciliation write failed: {}", e)

    def get_current(self) -> Dict[str, Any]:
        """Get current day's stats."""
        self._check_day()
        return {
            "date": self._today_date,
            "events": dict(self._today_stats),
            "rejections": dict(self._today_rejections),
            "total_events": sum(self._today_stats.values()),
            "total_rejections": sum(self._today_rejections.values()),
        }

    def to_bridge(self) -> Dict[str, Any]:
        """Export for dashboard."""
        current = self.get_current()
        # Funnel reconciliation
        events = current["events"]
        rejections = current["rejections"]
        funnel = {
            "scanned": events.get("scanned", 0),
            "regime_pass": events.get("regime_pass", 0),
            "pullback_pass": events.get("pullback_pass", 0),
            "candle_pass": events.get("candle_pass", 0),
            "volume_pass": events.get("volume_pass", 0),
            "confidence_pass": events.get("confidence_pass", 0),
            "published": events.get("published", 0),
        }
        return {
            "date": current["date"],
            "funnel": funnel,
            "rejections": current["rejections"],
            "total_events": current["total_events"],
            "total_rejections": current["total_rejections"],
        }


# ══════════════════════════════════════════════════════════════════════════════
# 5. UNIFIED PIPELINE MONITOR
# ══════════════════════════════════════════════════════════════════════════════

class PipelineMonitor:
    """Unified monitoring interface for the EMA V5 pipeline."""

    def __init__(self) -> None:
        self.lifecycle = LifecycleAudit()
        self.stall_detector = StallDetector()
        self.transition_timeout = TransitionTimeout()
        self.daily_recon = DailyReconciliation()
        self._initialized = True
        logger.info("📊 PIPELINE_MONITOR initialized — lifecycle, stall, timeout, recon active")

    def check_all(self) -> List[Dict[str, Any]]:
        """Run all monitoring checks. Returns list of alerts."""
        alerts = []

        # Stall detection
        stall_alert = self.stall_detector.check()
        if stall_alert:
            alerts.append(stall_alert)

        return alerts

    def to_bridge(self) -> Dict[str, Any]:
        """Export all monitoring data for dashboard."""
        return {
            "lifecycle_audit": self.lifecycle.to_bridge(),
            "stall_detector": self.stall_detector.to_bridge(),
            "transition_timeout": self.transition_timeout.to_bridge(),
            "daily_reconciliation": self.daily_recon.to_bridge(),
            "check_time": time.time(),
        }


# Global instance
pipeline_monitor = PipelineMonitor()
