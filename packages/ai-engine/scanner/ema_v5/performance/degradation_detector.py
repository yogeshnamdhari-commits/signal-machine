"""
EMA_V5 Degradation Detector — Detects performance degradation early.
Alerts when performance drops below thresholds.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database


@dataclass
class DegradationConfig:
    """Degradation detection configuration."""
    # Win rate thresholds
    min_win_rate_50: float = 40.0     # Min WR over last 50 trades
    min_win_rate_100: float = 42.0    # Min WR over last 100 trades

    # PnL thresholds
    max_consecutive_losses: int = 5   # Max consecutive losses before alert
    max_daily_loss_pct: float = 5.0   # Max daily loss % of balance

    # Drawdown thresholds
    warning_drawdown_pct: float = 5.0
    critical_drawdown_pct: float = 10.0

    # Confidence thresholds
    min_avg_confidence: float = 0.88

    # Time thresholds
    min_trades_for_alert: int = 20


@dataclass
class DegradationAlert:
    """Single degradation alert."""
    alert_type: str = ""  # win_rate, consecutive_loss, drawdown, confidence
    severity: str = ""    # warning, critical
    message: str = ""
    value: float = 0.0
    threshold: float = 0.0
    timestamp: float = 0.0


class EMAv5DegradationDetector:
    """Detects performance degradation in EMA_V5."""

    def __init__(self, config: Optional[DegradationConfig] = None,
                 db: Optional[EMAv5Database] = None) -> None:
        self.config = config or DegradationConfig()
        self._db = db or EMAv5Database()
        self._alerts: List[DegradationAlert] = []
        self._alert_counter = 0

    def check(self, signals: Optional[List[Dict]] = None) -> List[DegradationAlert]:
        """Check for degradation. Returns list of alerts."""
        if signals is None:
            signals = self._db.get_all_signals()

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        new_alerts = []

        if len(closed) < self.config.min_trades_for_alert:
            return new_alerts

        # Check win rate
        alert = self._check_win_rate(closed)
        if alert:
            new_alerts.append(alert)

        # Check consecutive losses
        alert = self._check_consecutive_losses(closed)
        if alert:
            new_alerts.append(alert)

        # Check drawdown
        alert = self._check_drawdown(closed)
        if alert:
            new_alerts.append(alert)

        # Check confidence
        alert = self._check_confidence(closed)
        if alert:
            new_alerts.append(alert)

        # Record alerts
        self._alerts.extend(new_alerts)

        if new_alerts:
            logger.warning("EMAv5 degradation: {} alerts detected", len(new_alerts))

        return new_alerts

    def _check_win_rate(self, trades: List[Dict]) -> Optional[DegradationAlert]:
        """Check if win rate is below threshold."""
        # Last 50 trades
        recent_50 = trades[-50:]
        wins_50 = sum(1 for t in recent_50 if t.get("result") == "win")
        wr_50 = wins_50 / len(recent_50) * 100 if recent_50 else 0

        if wr_50 < self.config.min_win_rate_50:
            return DegradationAlert(
                alert_type="win_rate",
                severity="critical" if wr_50 < self.config.min_win_rate_50 - 10 else "warning",
                message=f"Win rate {wr_50:.1f}% below threshold {self.config.min_win_rate_50}%",
                value=wr_50,
                threshold=self.config.min_win_rate_50,
                timestamp=time.time(),
            )

        # Last 100 trades
        recent_100 = trades[-100:]
        wins_100 = sum(1 for t in recent_100 if t.get("result") == "win")
        wr_100 = wins_100 / len(recent_100) * 100 if recent_100 else 0

        if wr_100 < self.config.min_win_rate_100:
            return DegradationAlert(
                alert_type="win_rate_extended",
                severity="warning",
                message=f"Extended win rate {wr_100:.1f}% below threshold {self.config.min_win_rate_100}%",
                value=wr_100,
                threshold=self.config.min_win_rate_100,
                timestamp=time.time(),
            )

        return None

    def _check_consecutive_losses(self, trades: List[Dict]) -> Optional[DegradationAlert]:
        """Check for excessive consecutive losses."""
        consecutive = 0
        for t in reversed(trades):
            if t.get("result") == "loss":
                consecutive += 1
            else:
                break

        if consecutive >= self.config.max_consecutive_losses:
            return DegradationAlert(
                alert_type="consecutive_loss",
                severity="critical" if consecutive >= self.config.max_consecutive_losses + 2 else "warning",
                message=f"{consecutive} consecutive losses (max={self.config.max_consecutive_losses})",
                value=consecutive,
                threshold=self.config.max_consecutive_losses,
                timestamp=time.time(),
            )

        return None

    def _check_drawdown(self, trades: List[Dict]) -> Optional[DegradationAlert]:
        """Check for excessive drawdown."""
        cumulative = 0
        peak = 0
        max_dd = 0

        for t in trades:
            cumulative += t.get("pnl", 0)
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / max(peak, 1) * 100
            max_dd = max(max_dd, dd)

        if max_dd >= self.config.critical_drawdown_pct:
            return DegradationAlert(
                alert_type="drawdown",
                severity="critical",
                message=f"Drawdown {max_dd:.1f}% exceeds critical threshold {self.config.critical_drawdown_pct}%",
                value=max_dd,
                threshold=self.config.critical_drawdown_pct,
                timestamp=time.time(),
            )
        elif max_dd >= self.config.warning_drawdown_pct:
            return DegradationAlert(
                alert_type="drawdown",
                severity="warning",
                message=f"Drawdown {max_dd:.1f}% exceeds warning threshold {self.config.warning_drawdown_pct}%",
                value=max_dd,
                threshold=self.config.warning_drawdown_pct,
                timestamp=time.time(),
            )

        return None

    def _check_confidence(self, trades: List[Dict]) -> Optional[DegradationAlert]:
        """Check if average confidence is below threshold."""
        confidences = [t.get("confidence", 0) for t in trades if t.get("confidence")]
        if not confidences:
            return None

        avg_conf = sum(confidences) / len(confidences)
        if avg_conf < self.config.min_avg_confidence:
            return DegradationAlert(
                alert_type="confidence",
                severity="warning",
                message=f"Average confidence {avg_conf:.2f} below threshold {self.config.min_avg_confidence}",
                value=avg_conf,
                threshold=self.config.min_avg_confidence,
                timestamp=time.time(),
            )

        return None

    def get_alerts(self, n: int = 50) -> List[DegradationAlert]:
        """Get last N alerts."""
        return self._alerts[-n:]

    def get_status(self) -> Dict[str, Any]:
        """Get detector status."""
        recent = self._alerts[-10:]
        return {
            "total_alerts": len(self._alerts),
            "recent_alerts": [
                {"type": a.alert_type, "severity": a.severity, "message": a.message}
                for a in recent
            ],
            "has_critical": any(a.severity == "critical" for a in recent),
        }
