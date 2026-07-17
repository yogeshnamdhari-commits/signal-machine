"""
Pipeline Health Monitor — Tracks signal pipeline pass rates.

Monitors:
  - phase1_pass_rate
  - regime_pass_rate
  - signal_emit_rate
  - pipeline_throughput

Does NOT loosen any filters. Monitoring only.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Dict, Optional
from loguru import logger


class PipelineHealthMonitor:
    """
    Monitors pipeline health without modifying any filters.
    Tracks pass rates and throughput for operational visibility.
    """
    
    # Rolling window size
    WINDOW_SIZE = 1000
    
    def __init__(self):
        self._cycle_count = 0
        self._total_scanned = 0
        self._total_scored = 0
        self._total_phase1_pass = 0
        self._total_phase1_reject = 0
        self._total_regime_pass = 0
        self._total_regime_reject = 0
        self._total_session_reject = 0
        self._total_conf_reject = 0
        self._total_quiet_reject = 0
        self._total_rr_reject = 0
        self._total_checklist_reject = 0
        self._total_sweep_reject = 0
        self._total_signals_emitted = 0
        self._total_positions_opened = 0
        # Rolling windows
        self._emit_times: deque = deque(maxlen=100)
        self._cycle_times: deque = deque(maxlen=100)
        self._last_cycle_start: float = 0
    
    def start_cycle(self) -> None:
        """Mark start of a new scan cycle."""
        self._cycle_count += 1
        self._last_cycle_start = time.time()
    
    def end_cycle(self) -> None:
        """Mark end of a scan cycle."""
        if self._last_cycle_start > 0:
            elapsed = time.time() - self._last_cycle_start
            self._cycle_times.append(elapsed)
    
    def record_scan(self, symbol_count: int) -> None:
        """Record symbols scanned in current cycle."""
        self._total_scanned += symbol_count
    
    def record_scored(self, count: int = 1) -> None:
        """Record signals that reached scoring."""
        self._total_scored += count
    
    def record_phase1_pass(self) -> None:
        self._total_phase1_pass += 1
    
    def record_phase1_reject(self) -> None:
        self._total_phase1_reject += 1
    
    def record_regime_pass(self) -> None:
        self._total_regime_pass += 1
    
    def record_regime_reject(self) -> None:
        self._total_regime_reject += 1
    
    def record_session_reject(self) -> None:
        self._total_session_reject += 1
    
    def record_conf_reject(self) -> None:
        self._total_conf_reject += 1
    
    def record_quiet_reject(self) -> None:
        self._total_quiet_reject += 1
    
    def record_rr_reject(self) -> None:
        self._total_rr_reject += 1
    
    def record_checklist_reject(self) -> None:
        self._total_checklist_reject += 1
    
    def record_sweep_reject(self) -> None:
        self._total_sweep_reject += 1
    
    def record_signal_emitted(self) -> None:
        self._total_signals_emitted += 1
        self._emit_times.append(time.time())
    
    def record_position_opened(self) -> None:
        self._total_positions_opened += 1
    
    def get_metrics(self) -> Dict:
        """Return current pipeline health metrics."""
        total_scored = max(self._total_scored, 1)
        total_emitted = max(self._total_signals_emitted, 1)
        
        # Pass rates
        phase1_rate = round(self._total_phase1_pass / max(self._total_phase1_pass + self._total_phase1_reject, 1) * 100, 1)
        regime_rate = round(self._total_regime_pass / max(self._total_regime_pass + self._total_regime_reject, 1) * 100, 1)
        emit_rate = round(self._total_signals_emitted / max(self._total_scanned, 1) * 100, 2)
        
        # Rejection breakdown
        total_rejects = (self._total_phase1_reject + self._total_regime_reject +
                        self._total_session_reject + self._total_conf_reject +
                        self._total_quiet_reject + self._total_rr_reject +
                        self._total_checklist_reject + self._total_sweep_reject)
        
        # Signals per hour (rolling)
        now = time.time()
        recent_emits = [t for t in self._emit_times if now - t < 3600]
        signals_per_hour = len(recent_emits)
        
        # Average cycle time
        avg_cycle_time = round(sum(self._cycle_times) / len(self._cycle_times), 2) if self._cycle_times else 0
        
        return {
            "cycle_count": self._cycle_count,
            "total_scanned": self._total_scanned,
            "total_scored": self._total_scored,
            "phase1_pass_rate": phase1_rate,
            "regime_pass_rate": regime_rate,
            "signal_emit_rate": emit_rate,
            "signals_per_hour": signals_per_hour,
            "total_signals_emitted": self._total_signals_emitted,
            "total_positions_opened": self._total_positions_opened,
            "avg_cycle_time_s": avg_cycle_time,
            "rejection_breakdown": {
                "phase1": self._total_phase1_reject,
                "regime": self._total_regime_reject,
                "session": self._total_session_reject,
                "confidence": self._total_conf_reject,
                "quiet_market": self._total_quiet_reject,
                "rr_filter": self._total_rr_reject,
                "checklist": self._total_checklist_reject,
                "sweep": self._total_sweep_reject,
                "total": total_rejects,
            },
        }
    
    def log_health(self) -> None:
        """Log pipeline health summary."""
        m = self.get_metrics()
        logger.info(
            "📊 PIPELINE HEALTH: cycles={} scanned={} phase1={} regime={} emitted={} positions={} rate={}/hr",
            m["cycle_count"], m["total_scanned"], m["phase1_pass_rate"],
            m["regime_pass_rate"], m["total_signals_emitted"],
            m["total_positions_opened"], m["signals_per_hour"]
        )
        logger.info("   Rejections: phase1={} regime={} session={} conf={} quiet={} rr={} checklist={} sweep={}",
                     m["rejection_breakdown"]["phase1"],
                     m["rejection_breakdown"]["regime"],
                     m["rejection_breakdown"]["session"],
                     m["rejection_breakdown"]["confidence"],
                     m["rejection_breakdown"]["quiet_market"],
                     m["rejection_breakdown"]["rr_filter"],
                     m["rejection_breakdown"]["checklist"],
                     m["rejection_breakdown"]["sweep"])


# Global singleton
pipeline_monitor = PipelineHealthMonitor()
