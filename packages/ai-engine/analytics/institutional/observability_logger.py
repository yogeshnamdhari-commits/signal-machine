"""
Observability Logger
====================
Structured logging for all important decisions.

Logs:
- Signal accepted/rejected
- RR rejected
- Promotion blocked
- Validation failed
- Configuration changed
- Deployment blocked
- Drift detected
- Optimization started/completed

READ-ONLY — Never modifies trading logic.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


class ObservabilityLogger:
    """
    Structured observability logger.
    
    Logs all important decisions for audit and debugging.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "observability"
    
    def __init__(self):
        self._log_dir = self.LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
    
    def _write_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Write a structured event to the log."""
        timestamp = datetime.now(timezone.utc).isoformat()
        
        event = {
            "timestamp": timestamp,
            "event_type": event_type,
            "data": data,
        }
        
        # Log to loguru
        logger.info("📊 OBSERVABILITY: {} | {}", event_type, json.dumps(data, default=str)[:200])
        
        # Write to daily log file
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = self._log_dir / f"events_{date_str}.jsonl"
        
        try:
            with open(log_file, "a") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except Exception as e:
            logger.debug("OBSERVABILITY: Failed to write event: {}", e)
    
    def log_signal_accepted(self, symbol: str, side: str, confidence: float, rr: float) -> None:
        """Log when a signal is accepted."""
        self._write_event("signal_accepted", {
            "symbol": symbol,
            "side": side,
            "confidence": confidence,
            "rr": rr,
        })
    
    def log_signal_rejected(self, symbol: str, side: str, reason: str) -> None:
        """Log when a signal is rejected."""
        self._write_event("signal_rejected", {
            "symbol": symbol,
            "side": side,
            "reason": reason,
        })
    
    def log_rr_rejected(self, symbol: str, side: str, rr: float, required: float) -> None:
        """Log when a signal is rejected for RR."""
        self._write_event("rr_rejected", {
            "symbol": symbol,
            "side": side,
            "rr": rr,
            "required": required,
        })
    
    def log_promotion_blocked(self, config: str, level: str, reason: str) -> None:
        """Log when promotion is blocked."""
        self._write_event("promotion_blocked", {
            "config": config,
            "level": level,
            "reason": reason,
        })
    
    def log_validation_failed(self, config: str, gate: str, required: str, actual: str) -> None:
        """Log when validation fails."""
        self._write_event("validation_failed", {
            "config": config,
            "gate": gate,
            "required": required,
            "actual": actual,
        })
    
    def log_configuration_changed(self, old_config: str, new_config: str, reason: str) -> None:
        """Log when configuration changes."""
        self._write_event("configuration_changed", {
            "old_config": old_config,
            "new_config": new_config,
            "reason": reason,
        })
    
    def log_deployment_blocked(self, config: str, reason: str) -> None:
        """Log when deployment is blocked."""
        self._write_event("deployment_blocked", {
            "config": config,
            "reason": reason,
        })
    
    def log_drift_detected(self, config: str, metric: str, drift: float) -> None:
        """Log when drift is detected."""
        self._write_event("drift_detected", {
            "config": config,
            "metric": metric,
            "drift": drift,
        })
    
    def log_optimization_started(self, configs: int) -> None:
        """Log when optimization starts."""
        self._write_event("optimization_started", {
            "configs": configs,
        })
    
    def log_optimization_completed(self, best_config: str, score: float) -> None:
        """Log when optimization completes."""
        self._write_event("optimization_completed", {
            "best_config": best_config,
            "score": score,
        })
    
    def log_trade_completed(self, symbol: str, side: str, pnl: float, exit_reason: str) -> None:
        """Log when a trade completes."""
        self._write_event("trade_completed", {
            "symbol": symbol,
            "side": side,
            "pnl": pnl,
            "exit_reason": exit_reason,
        })
    
    def log_regime_change(self, old_regime: str, new_regime: str) -> None:
        """Log when regime changes."""
        self._write_event("regime_change", {
            "old_regime": old_regime,
            "new_regime": new_regime,
        })
    
    def log_session_start(self, session: str) -> None:
        """Log when a trading session starts."""
        self._write_event("session_start", {
            "session": session,
        })
    
    def log_health_check(self, status: str, components: Dict[str, str]) -> None:
        """Log health check results."""
        self._write_event("health_check", {
            "status": status,
            "components": components,
        })
    
    def get_recent_events(self, count: int = 50) -> list:
        """Get recent events from the log."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = self._log_dir / f"events_{date_str}.jsonl"
        
        if not log_file.exists():
            return []
        
        events = []
        try:
            with open(log_file, "r") as f:
                for line in f:
                    try:
                        events.append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        
        return events[-count:]
    
    def get_event_summary(self) -> Dict[str, int]:
        """Get summary of event types."""
        events = self.get_recent_events(1000)
        
        summary = defaultdict(int)
        for event in events:
            event_type = event.get("event_type", "unknown")
            summary[event_type] += 1
        
        return dict(summary)


# Global singleton
_logger: Optional[ObservabilityLogger] = None

def get_observability_logger() -> ObservabilityLogger:
    """Get or create the global observability logger."""
    global _logger
    if _logger is None:
        _logger = ObservabilityLogger()
    return _logger
