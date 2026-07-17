"""
Alert Service — Alert management and notification delivery.

Integrates with:
- ExecutionMonitor alerts
- RiskGuardian alerts
- Telegram notifications
- Discord notifications
"""
from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class AlertCategory(str, Enum):
    RISK = "risk"
    EXECUTION = "execution"
    EXCHANGE = "exchange"
    SYSTEM = "system"
    ARBITRAGE = "arbitrage"
    SIGNAL = "signal"


class AlertService:
    """
    Central alert management service.
    Handles alert creation, filtering, notification delivery.
    """

    MAX_ALERTS = 5000
    ALERT_PATH = Path("data/reports/alerts.json")

    def __init__(self) -> None:
        self._alerts: List[Dict[str, Any]] = []
        self._unread_count: int = 0
        self._callbacks: List[Callable] = []

        # Alert delivery configs
        self._telegram_enabled: bool = False
        self._discord_webhook: str = ""
        self._email_enabled: bool = False

        # Stats
        self._stats: Dict[str, int] = {
            "total": 0,
            "info": 0,
            "warning": 0,
            "critical": 0,
            "emergency": 0,
        }

        # Level thresholds for auto-notification
        self._notify_levels = {AlertLevel.CRITICAL, AlertLevel.EMERGENCY}

        self.ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)

    def create_alert(
        self,
        level: AlertLevel,
        category: AlertCategory,
        title: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new alert."""
        alert = {
            "id": f"alert-{uuid.uuid4().hex[:12]}",
            "level": level.value,
            "category": category.value,
            "title": title,
            "message": message,
            "data": data or {},
            "timestamp": time.time(),
            "read": False,
            "acknowledged": False,
        }

        self._alerts.append(alert)
        self._unread_count += 1
        self._stats["total"] += 1
        self._stats[level.value] = self._stats.get(level.value, 0) + 1

        if len(self._alerts) > self.MAX_ALERTS:
            self._alerts = self._alerts[-self.MAX_ALERTS // 2:]

        logger.log(
            level.value.upper(),
            "[ALERT] {}: {} — {}",
            category.value,
            title,
            message,
        )

        # Auto-notify for critical/emergency
        if level in self._notify_levels:
            self._dispatch_notification(alert)

        return alert

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge an alert."""
        for alert in self._alerts:
            if alert["id"] == alert_id:
                alert["acknowledged"] = True
                if not alert["read"]:
                    alert["read"] = True
                    self._unread_count = max(0, self._unread_count - 1)
                return True
        return False

    def mark_read(self, alert_id: str) -> bool:
        """Mark an alert as read."""
        for alert in self._alerts:
            if alert["id"] == alert_id and not alert["read"]:
                alert["read"] = True
                self._unread_count = max(0, self._unread_count - 1)
                return True
        return False

    def mark_all_read(self) -> int:
        """Mark all alerts as read."""
        count = 0
        for alert in self._alerts:
            if not alert["read"]:
                alert["read"] = True
                count += 1
        self._unread_count = 0
        return count

    def get_alerts(
        self,
        limit: int = 50,
        level_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
        unread_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get alerts with optional filtering."""
        filtered = self._alerts

        if level_filter and level_filter != "all":
            filtered = [a for a in filtered if a["level"] == level_filter]
        if category_filter and category_filter != "all":
            filtered = [a for a in filtered if a["category"] == category_filter]
        if unread_only:
            filtered = [a for a in filtered if not a["read"]]

        return filtered[-limit:]

    def get_alert_stats(self) -> Dict[str, Any]:
        """Get alert statistics."""
        return {
            "total_alerts": self._stats["total"],
            "unread_count": self._unread_count,
            "by_level": {
                "info": self._stats.get("info", 0),
                "warning": self._stats.get("warning", 0),
                "critical": self._stats.get("critical", 0),
                "emergency": self._stats.get("emergency", 0),
            },
            "by_category": self._count_by_category(),
            "timestamp": time.time(),
        }

    def _count_by_category(self) -> Dict[str, int]:
        """Count alerts by category."""
        counts: Dict[str, int] = {}
        for alert in self._alerts:
            cat = alert.get("category", "unknown")
            counts[cat] = counts.get(cat, 0) + 1
        return counts

    def _dispatch_notification(self, alert: Dict[str, Any]) -> None:
        """Dispatch notification to configured channels."""
        for callback in self._callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error("[AlertService] Notification dispatch error: {}", e)

        # Log notification
        logger.info(
            "[AlertService] Notification dispatched for {}: {}",
            alert["level"],
            alert["title"],
        )

    def register_callback(self, callback: Callable) -> None:
        """Register a notification callback."""
        self._callbacks.append(callback)

    def configure_telegram(self, enabled: bool, bot_token: str = "", chat_id: str = "") -> None:
        """Configure Telegram notifications."""
        self._telegram_enabled = enabled

    def configure_discord(self, webhook_url: str) -> None:
        """Configure Discord notifications."""
        self._discord_webhook = webhook_url

    def save_alerts(self) -> None:
        """Persist alerts to disk."""
        try:
            data = self._alerts[-1000:]
            tmp = self.ALERT_PATH.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            tmp.rename(self.ALERT_PATH)
        except Exception as e:
            logger.error("[AlertService] Failed to save alerts: {}", e)
