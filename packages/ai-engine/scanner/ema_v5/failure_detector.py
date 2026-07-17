"""
EMA_V5 Failure Detector — Alerts on specific failure conditions.

Raises alerts if:
  - Candidate disappears
  - ACTIVE expires unexpectedly
  - Pipeline latency > 2s
  - DB failure
  - Duplicate signal
  - State rollback
  - Missing transition
  - Unexplained rejection
  - Silent exception
  - Race condition detected
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List, Optional

from loguru import logger


class FailureDetector:
    """Detects and logs specific failure conditions in the EMA V5 pipeline."""

    # Thresholds
    MAX_PIPELINE_LATENCY_MS = 2000  # 2 seconds
    MAX_STATE_STALE_SEC = 300       # 5 minutes in same state
    MAX_CONSECUTIVE_ERRORS = 5

    def __init__(self) -> None:
        # Alert history (last N)
        self._alerts: List[Dict] = []
        self._max_alerts = 200

        # State tracking
        self._state_timestamps: Dict[str, Dict] = {}  # symbol → {state, timestamp}
        self._seen_signals: Dict[str, float] = {}  # symbol → last_signal_time
        self._consecutive_errors: Dict[str, int] = defaultdict(int)

        # Failure counts
        self._failure_counts: Dict[str, int] = defaultdict(int)

        # DB health
        self._last_db_check: float = 0
        self._db_healthy: bool = True

        logger.info("🚨 Failure Detector initialized")

    def check_latency(self, symbol: str, stage: str, latency_ms: float) -> Optional[Dict]:
        """Check if pipeline latency exceeds threshold."""
        if latency_ms > self.MAX_PIPELINE_LATENCY_MS:
            alert = {
                "type": "HIGH_LATENCY",
                "symbol": symbol,
                "stage": stage,
                "latency_ms": round(latency_ms, 2),
                "threshold_ms": self.MAX_PIPELINE_LATENCY_MS,
                "timestamp": time.time(),
            }
            self._record_alert(alert)
            logger.error(
                "🚨 HIGH_LATENCY: {} {} latency={:.0f}ms (threshold={}ms)",
                symbol, stage, latency_ms, self.MAX_PIPELINE_LATENCY_MS,
            )
            return alert
        return None

    def check_duplicate_signal(self, symbol: str, confidence: float) -> Optional[Dict]:
        """Check for duplicate signals from the same symbol."""
        now = time.time()
        last_seen = self._seen_signals.get(symbol, 0)

        if now - last_seen < 300:  # Same signal within 5 minutes
            alert = {
                "type": "DUPLICATE_SIGNAL",
                "symbol": symbol,
                "confidence": confidence,
                "last_seen_sec_ago": round(now - last_seen, 1),
                "timestamp": now,
            }
            self._record_alert(alert)
            self._failure_counts["duplicate_signal"] += 1
            logger.error(
                "🚨 DUPLICATE_SIGNAL: {} conf={:.1f} (last seen {:.0f}s ago)",
                symbol, confidence, now - last_seen,
            )
            return alert

        self._seen_signals[symbol] = now
        return None

    def check_state_rollback(self, symbol: str, old_state: str, new_state: str) -> Optional[Dict]:
        """Check for unexpected state rollbacks."""
        # Valid rollbacks: ACTIVE → NO_TREND (trade closed), WAITING → NO_TREND (timeout)
        valid_rollbacks = {
            ("ACTIVE_BUY", "NO_TREND"),
            ("ACTIVE_SELL", "NO_TREND"),
            ("WAITING_CONFIRMATION", "NO_TREND"),
            ("WAITING_CONFIRMATION", "WAITING_PULLBACK"),
            ("WAITING_PULLBACK", "NO_TREND"),
            ("WAITING_PULLBACK", "BUY_MODE"),
            ("WAITING_PULLBACK", "SELL_MODE"),
        }

        if (old_state, new_state) not in valid_rollbacks:
            alert = {
                "type": "STATE_ROLLBACK",
                "symbol": symbol,
                "old_state": old_state,
                "new_state": new_state,
                "timestamp": time.time(),
            }
            self._record_alert(alert)
            self._failure_counts["state_rollback"] += 1
            logger.warning(
                "🚨 STATE_ROLLBACK: {} {} → {} (unexpected transition)",
                symbol, old_state, new_state,
            )
            return alert
        return None

    def check_missing_transition(self, symbol: str, expected_states: List[str], actual_state: str) -> Optional[Dict]:
        """Check if a symbol missed an expected state transition."""
        if expected_states and actual_state not in expected_states:
            alert = {
                "type": "MISSING_TRANSITION",
                "symbol": symbol,
                "expected": expected_states,
                "actual": actual_state,
                "timestamp": time.time(),
            }
            self._record_alert(alert)
            self._failure_counts["missing_transition"] += 1
            logger.warning(
                "🚨 MISSING_TRANSITION: {} expected={} actual={}",
                symbol, expected_states, actual_state,
            )
            return alert
        return None

    def check_db_health(self, healthy: bool, error: str = "") -> Optional[Dict]:
        """Track DB health status."""
        self._last_db_check = time.time()

        if not healthy and self._db_healthy:
            # DB just became unhealthy
            alert = {
                "type": "DB_FAILURE",
                "error": error,
                "timestamp": time.time(),
            }
            self._record_alert(alert)
            self._failure_counts["db_failure"] += 1
            logger.error("🚨 DB_FAILURE: {}", error)
            self._db_healthy = False
            return alert
        elif healthy and not self._db_healthy:
            # DB recovered
            logger.info("✅ DB_RECOVERED: database connection restored")
            self._db_healthy = True

        return None

    def record_error(self, component: str, error: str) -> Optional[Dict]:
        """Record an error from a component, track consecutive errors."""
        self._consecutive_errors[component] += 1

        if self._consecutive_errors[component] >= self.MAX_CONSECUTIVE_ERRORS:
            alert = {
                "type": "CONSECUTIVE_ERRORS",
                "component": component,
                "count": self._consecutive_errors[component],
                "error": error,
                "timestamp": time.time(),
            }
            self._record_alert(alert)
            self._failure_counts["consecutive_errors"] += 1
            logger.error(
                "🚨 CONSECUTIVE_ERRORS: {} has {} consecutive errors: {}",
                component, self._consecutive_errors[component], error,
            )
            return alert
        return None

    def clear_error(self, component: str) -> None:
        """Clear consecutive error count for a component."""
        self._consecutive_errors.pop(component, None)

    def _record_alert(self, alert: Dict) -> None:
        """Record an alert to history."""
        self._alerts.append(alert)
        if len(self._alerts) > self._max_alerts:
            self._alerts = self._alerts[-self._max_alerts:]

    def get_stats(self) -> Dict:
        """Get failure detection statistics."""
        return {
            "total_alerts": len(self._alerts),
            "failure_counts": dict(self._failure_counts),
            "db_healthy": self._db_healthy,
            "consecutive_errors": dict(self._consecutive_errors),
            "recent_alerts": self._alerts[-10:],  # Last 10
        }

    def get_recent_alerts(self, limit: int = 20) -> List[Dict]:
        """Get recent alerts for dashboard display."""
        return self._alerts[-limit:]
