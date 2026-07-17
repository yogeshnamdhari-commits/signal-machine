"""
EMA_V5 Alert Manager — Manages alert types, priorities, and routing.
Isolated from existing alert management.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from .telegram_bot import EMAv5TelegramBot, EMAv5TelegramConfig


class EMAv5AlertLevel(Enum):
    """Alert priority levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EMAv5AlertType(Enum):
    """Alert types for EMA_V5."""
    SIGNAL = "signal"
    EXIT = "exit"
    VERIFICATION = "verification"
    ERROR = "error"
    DAILY_SUMMARY = "daily_summary"
    WEEKLY_SUMMARY = "weekly_summary"
    SYSTEM = "system"


@dataclass
class EMAv5Alert:
    """Single alert record."""
    alert_id: str = ""
    alert_type: EMAv5AlertType = EMAv5AlertType.SYSTEM
    level: EMAv5AlertLevel = EMAv5AlertLevel.LOW
    title: str = ""
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    sent: bool = False
    sent_at: float = 0.0


class EMAv5AlertManager:
    """Manages EMA_V5 alerts and routing to Telegram."""

    def __init__(self, bot: Optional[EMAv5TelegramBot] = None) -> None:
        self._bot = bot or EMAv5TelegramBot()
        self._alerts: List[EMAv5Alert] = []
        self._alert_counter = 0

        # Alert type → level mapping
        self._type_levels = {
            EMAv5AlertType.SIGNAL: EMAv5AlertLevel.HIGH,
            EMAv5AlertType.EXIT: EMAv5AlertLevel.HIGH,
            EMAv5AlertType.VERIFICATION: EMAv5AlertLevel.MEDIUM,
            EMAv5AlertType.ERROR: EMAv5AlertLevel.CRITICAL,
            EMAv5AlertType.DAILY_SUMMARY: EMAv5AlertLevel.LOW,
            EMAv5AlertType.WEEKLY_SUMMARY: EMAv5AlertLevel.LOW,
            EMAv5AlertType.SYSTEM: EMAv5AlertLevel.MEDIUM,
        }

        # Enabled alert types
        self._enabled_types: Set[EMAv5AlertType] = set(EMAv5AlertType)

    def set_enabled_types(self, types: Set[EMAv5AlertType]) -> None:
        """Set which alert types are enabled."""
        self._enabled_types = types

    def create_alert(
        self,
        alert_type: EMAv5AlertType,
        title: str = "",
        message: str = "",
        data: Optional[Dict] = None,
        level: Optional[EMAv5AlertLevel] = None,
    ) -> EMAv5Alert:
        """Create a new alert."""
        self._alert_counter += 1
        alert = EMAv5Alert(
            alert_id=f"ev5_{self._alert_counter:06d}",
            alert_type=alert_type,
            level=level or self._type_levels.get(alert_type, EMAv5AlertLevel.LOW),
            title=title,
            message=message,
            data=data or {},
            timestamp=time.time(),
        )
        self._alerts.append(alert)
        return alert

    async def send_alert(self, alert: EMAv5Alert) -> bool:
        """Send an alert via Telegram."""
        if alert.alert_type not in self._enabled_types:
            logger.debug("EMAv5 alert skipped: {} (disabled)", alert.alert_type.value)
            return False

        from .message_formatter import EMAv5MessageFormatter

        # Format message based on type
        if alert.alert_type == EMAv5AlertType.SIGNAL:
            text = EMAv5MessageFormatter.signal_message(alert.data)
        elif alert.alert_type == EMAv5AlertType.EXIT:
            text = EMAv5MessageFormatter.exit_message(alert.data)
        elif alert.alert_type == EMAv5AlertType.ERROR:
            text = EMAv5MessageFormatter.error_message(alert.message, alert.title)
        elif alert.alert_type == EMAv5AlertType.DAILY_SUMMARY:
            text = EMAv5MessageFormatter.daily_summary(alert.data)
        elif alert.alert_type == EMAv5AlertType.WEEKLY_SUMMARY:
            text = EMAv5MessageFormatter.weekly_summary(alert.data)
        elif alert.alert_type == EMAv5AlertType.VERIFICATION:
            text = EMAv5MessageFormatter.verification_alert(alert.data)
        else:
            text = f"<b>{alert.title}</b>\n\n{alert.message}"

        sent = await self._bot.send_message(text)
        alert.sent = sent
        alert.sent_at = time.time() if sent else 0

        if sent:
            logger.info("EMAv5 alert sent: {} {}", alert.alert_type.value, alert.alert_id)
        else:
            logger.warning("EMAv5 alert failed: {} {}", alert.alert_type.value, alert.alert_id)

        return sent

    async def send_signal_alert(self, signal: Dict[str, Any]) -> bool:
        """Convenience: send a signal alert."""
        alert = self.create_alert(EMAv5AlertType.SIGNAL, data=signal)
        return await self.send_alert(alert)

    async def send_exit_alert(self, exit_data: Dict[str, Any]) -> bool:
        """Convenience: send an exit alert."""
        alert = self.create_alert(EMAv5AlertType.EXIT, data=exit_data)
        return await self.send_alert(alert)

    async def send_error_alert(self, error: str, context: str = "") -> bool:
        """Convenience: send an error alert."""
        alert = self.create_alert(EMAv5AlertType.ERROR, title=context, message=error)
        return await self.send_alert(alert)

    async def send_daily_summary(self, summary: Dict[str, Any]) -> bool:
        """Convenience: send daily summary."""
        alert = self.create_alert(EMAv5AlertType.DAILY_SUMMARY, data=summary)
        return await self.send_alert(alert)

    def get_alerts(self, n: int = 50) -> List[EMAv5Alert]:
        """Get last N alerts."""
        return self._alerts[-n:]

    def get_sent_count(self) -> int:
        """Count sent alerts."""
        return sum(1 for a in self._alerts if a.sent)

    def get_failed_count(self) -> int:
        """Count failed alerts."""
        return sum(1 for a in self._alerts if not a.sent and a.timestamp > 0)

    def get_status(self) -> Dict[str, Any]:
        """Get alert manager status."""
        return {
            "total_alerts": len(self._alerts),
            "sent": self.get_sent_count(),
            "failed": self.get_failed_count(),
            "enabled_types": [t.value for t in self._enabled_types],
            "bot_status": self._bot.get_status(),
        }
