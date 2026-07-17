"""
Alert Dispatcher — Multi-Channel Alerting
==========================================
Channels: Telegram, Discord, Email, Push Notifications
Alerts:   Risk Breach, Exchange Disconnect, Position Mismatch,
          Drawdown Threshold, Kill Switch Activation
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "capital"


# ─── Enums ───────────────────────────────────────────────────────────────────
class AlertSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


class AlertChannel(Enum):
    TELEGRAM = "telegram"
    DISCORD = "discord"
    EMAIL = "email"
    PUSH = "push"
    LOG = "log"
    WEBHOOK = "webhook"


class AlertType(Enum):
    RISK_BREACH = "risk_breach"
    EXCHANGE_DISCONNECT = "exchange_disconnect"
    POSITION_MISMATCH = "position_mismatch"
    DRAWDOWN_THRESHOLD = "drawdown_threshold"
    KILL_SWITCH = "kill_switch"
    MARGIN_CALL = "margin_call"
    API_ERROR = "api_error"
    PERFORMANCE_DRIFT = "performance_drift"
    SYSTEM_HEALTH = "system_health"
    TIER_PROMOTION = "tier_promotion"
    TIER_DEMOTION = "tier_demotion"
    SLIPPAGE_HIGH = "slippage_high"
    CUSTOM = "custom"


# ─── Data Classes ────────────────────────────────────────────────────────────
@dataclass
class Alert:
    """An alert message."""
    alert_id: str
    alert_type: str
    severity: str
    title: str
    message: str
    details: dict
    channels: list
    timestamp: str
    delivered: bool = False
    delivery_status: dict = field(default_factory=dict)
    acknowledged: bool = False


@dataclass
class AlertRule:
    """Rule for automatic alert triggering."""
    rule_id: str
    name: str
    alert_type: str
    severity: str
    channels: list
    condition: str  # Human-readable condition
    cooldown_seconds: int
    enabled: bool = True
    last_triggered: str = ""


# ─── Alert Dispatcher ────────────────────────────────────────────────────────
class AlertDispatcher:
    """
    Multi-channel alert dispatcher.

    Usage:
        dispatcher = AlertDispatcher()
        dispatcher.add_channel(AlertChannel.TELEGRAM, telegram_send_fn)
        dispatcher.send_alert(
            alert_type=AlertType.RISK_BREACH,
            severity=AlertSeverity.CRITICAL,
            title="Risk Limit Breached",
            message="Portfolio risk at 6.2% (limit 5%)",
        )
    """

    def __init__(self):
        self._channels: dict[str, dict] = {}
        self._alerts: list[Alert] = []
        self._rules: dict[str, AlertRule] = {}
        self._cooldowns: dict[str, float] = {}
        self._rate_limits: dict[str, list[float]] = {}

        # Default rate limits (per channel)
        self._rate_limit_config = {
            "telegram": {"max_per_minute": 10, "max_per_hour": 50},
            "discord": {"max_per_minute": 10, "max_per_hour": 50},
            "email": {"max_per_minute": 3, "max_per_hour": 20},
            "push": {"max_per_minute": 10, "max_per_hour": 50},
        }

        self._register_default_rules()
        self._load_state()
        logger.info("AlertDispatcher initialized: %d channels, %d rules",
                     len(self._channels), len(self._rules))

    # ── Channel Management ────────────────────────────────────────────────────
    def add_channel(
        self,
        channel: AlertChannel,
        send_fn: Callable[[str, str, dict], bool],
        min_severity: AlertSeverity = AlertSeverity.INFO,
    ):
        """Register an alert channel."""
        self._channels[channel.value] = {
            "send_fn": send_fn,
            "min_severity": min_severity,
            "enabled": True,
        }
        self._rate_limits[channel.value] = []
        logger.info("Added alert channel: %s (min_severity=%s)", channel.value, min_severity.value)

    def remove_channel(self, channel: AlertChannel):
        """Remove an alert channel."""
        self._channels.pop(channel.value, None)

    # ── Send Alert ────────────────────────────────────────────────────────────
    def send_alert(
        self,
        alert_type: AlertType,
        severity: AlertSeverity,
        title: str,
        message: str,
        details: Optional[dict] = None,
        channels: Optional[list[AlertChannel]] = None,
    ) -> Alert:
        """Send an alert through configured channels."""
        alert_id = f"ALERT-{int(time.time() * 1000)}"

        # Determine target channels
        if channels:
            target_channels = [c.value for c in channels]
        else:
            target_channels = self._get_channels_for_severity(severity)

        # Check rate limits
        allowed_channels = []
        for ch in target_channels:
            if self._check_rate_limit(ch):
                allowed_channels.append(ch)

        if not allowed_channels:
            logger.warning("All channels rate-limited for alert: %s", title)
            allowed_channels = ["log"]  # Always allow logging

        # Create alert
        alert = Alert(
            alert_id=alert_id,
            alert_type=alert_type.value,
            severity=severity.value,
            title=title,
            message=message,
            details=details or {},
            channels=allowed_channels,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Deliver
        delivery_status = {}
        for channel in allowed_channels:
            try:
                success = self._deliver(channel, alert)
                delivery_status[channel] = "delivered" if success else "failed"
            except Exception as e:
                delivery_status[channel] = f"error: {str(e)}"
                logger.error("Alert delivery failed [%s]: %s", channel, e)

        alert.delivered = any(v == "delivered" for v in delivery_status.values())
        alert.delivery_status = delivery_status

        self._alerts.append(alert)
        self._save_state()

        # Always log
        log_fn = logger.critical if severity in (AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY) else logger.warning
        log_fn("🚨 [%s] %s: %s", severity.value, title, message)

        return alert

    # ── Convenience Methods ───────────────────────────────────────────────────
    def alert_risk_breach(self, details: dict):
        """Send risk breach alert."""
        self.send_alert(
            AlertType.RISK_BREACH, AlertSeverity.CRITICAL,
            "⚠️ Risk Limit Breached",
            f"Portfolio risk has exceeded limits: {json.dumps(details, indent=2)}",
            details,
        )

    def alert_exchange_disconnect(self, exchange: str, error: str):
        """Send exchange disconnect alert."""
        self.send_alert(
            AlertType.EXCHANGE_DISCONNECT, AlertSeverity.HIGH,
            f"🔌 Exchange Disconnected: {exchange}",
            f"Connection lost: {error}",
            {"exchange": exchange, "error": error},
        )

    def alert_position_mismatch(self, details: dict):
        """Send position mismatch alert."""
        self.send_alert(
            AlertType.POSITION_MISMATCH, AlertSeverity.HIGH,
            "📊 Position Mismatch Detected",
            "Internal positions do not match exchange",
            details,
        )

    def alert_drawdown(self, current_dd: float, limit: float):
        """Send drawdown threshold alert."""
        severity = AlertSeverity.CRITICAL if current_dd > limit else AlertSeverity.HIGH
        self.send_alert(
            AlertType.DRAWDOWN_THRESHOLD, severity,
            f"📉 Drawdown Alert: {current_dd:.1%}",
            f"Current drawdown {current_dd:.1%} approaching/exceeding limit {limit:.1%}",
            {"current": current_dd, "limit": limit},
        )

    def alert_kill_switch(self, trigger: str, reason: str):
        """Send kill switch activation alert."""
        self.send_alert(
            AlertType.KILL_SWITCH, AlertSeverity.EMERGENCY,
            "🚨 KILL SWITCH ACTIVATED",
            f"Emergency halt triggered: {trigger} — {reason}",
            {"trigger": trigger, "reason": reason},
        )

    def alert_tier_change(self, from_tier: int, to_tier: int, direction: str):
        """Send tier change alert."""
        alert_type = AlertType.TIER_PROMOTION if direction == "up" else AlertType.TIER_DEMOTION
        severity = AlertSeverity.INFO if direction == "up" else AlertSeverity.WARNING
        emoji = "⬆️" if direction == "up" else "⬇️"
        self.send_alert(
            alert_type, severity,
            f"{emoji} Tier {direction.title()}: {from_tier} → {to_tier}",
            f"Capital deployment tier changed from {from_tier} to {to_tier}",
            {"from": from_tier, "to": to_tier, "direction": direction},
        )

    # ── Delivery ──────────────────────────────────────────────────────────────
    def _deliver(self, channel: str, alert: Alert) -> bool:
        """Deliver alert to a channel."""
        config = self._channels.get(channel)
        if not config or not config.get("enabled"):
            return False

        # Check severity filter
        min_sev = config.get("min_severity", AlertSeverity.INFO)
        sev_order = {"INFO": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3, "EMERGENCY": 4}
        if sev_order.get(alert.severity, 0) < sev_order.get(min_sev.value, 0):
            return False

        send_fn = config.get("send_fn")
        if send_fn:
            return send_fn(alert.title, alert.message, asdict(alert))

        # Default: log delivery
        return True

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    def _check_rate_limit(self, channel: str) -> bool:
        """Check if channel is within rate limits."""
        config = self._rate_limit_config.get(channel, {})
        max_per_minute = config.get("max_per_minute", 10)
        max_per_hour = config.get("max_per_hour", 50)

        now = time.time()
        history = self._rate_limits.get(channel, [])

        # Clean old entries
        history = [t for t in history if now - t < 3600]
        self._rate_limits[channel] = history

        # Check limits
        recent_minute = sum(1 for t in history if now - t < 60)
        if recent_minute >= max_per_minute:
            return False
        if len(history) >= max_per_hour:
            return False

        history.append(now)
        return True

    # ── Channel Selection ─────────────────────────────────────────────────────
    def _get_channels_for_severity(self, severity: AlertSeverity) -> list[str]:
        """Get appropriate channels for severity level."""
        channels = ["log"]  # Always log

        if severity in (AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY):
            # All channels
            for ch in self._channels:
                if self._channels[ch].get("enabled"):
                    channels.append(ch)
        elif severity == AlertSeverity.HIGH:
            # Telegram + Discord
            for ch in ["telegram", "discord"]:
                if ch in self._channels and self._channels[ch].get("enabled"):
                    channels.append(ch)
        elif severity == AlertSeverity.WARNING:
            # Telegram only
            if "telegram" in self._channels and self._channels["telegram"].get("enabled"):
                channels.append("telegram")

        return list(set(channels))

    # ── Default Rules ─────────────────────────────────────────────────────────
    def _register_default_rules(self):
        """Register default alert rules."""
        defaults = [
            AlertRule(
                rule_id="risk_breach", name="Risk Breach",
                alert_type="risk_breach", severity="CRITICAL",
                channels=["telegram", "discord", "email"],
                condition="Portfolio risk exceeds limit",
                cooldown_seconds=300,
            ),
            AlertRule(
                rule_id="exchange_down", name="Exchange Disconnect",
                alert_type="exchange_disconnect", severity="HIGH",
                channels=["telegram", "discord"],
                condition="Exchange API/WebSocket disconnected",
                cooldown_seconds=120,
            ),
            AlertRule(
                rule_id="position_desync", name="Position Mismatch",
                alert_type="position_mismatch", severity="HIGH",
                channels=["telegram", "discord"],
                condition="Internal positions != exchange positions",
                cooldown_seconds=300,
            ),
            AlertRule(
                rule_id="drawdown_warn", name="Drawdown Warning",
                alert_type="drawdown_threshold", severity="WARNING",
                channels=["telegram"],
                condition="Drawdown > 5%",
                cooldown_seconds=600,
            ),
            AlertRule(
                rule_id="drawdown_crit", name="Drawdown Critical",
                alert_type="drawdown_threshold", severity="CRITICAL",
                channels=["telegram", "discord", "email"],
                condition="Drawdown > 8%",
                cooldown_seconds=300,
            ),
            AlertRule(
                rule_id="kill_switch", name="Kill Switch",
                alert_type="kill_switch", severity="EMERGENCY",
                channels=["telegram", "discord", "email", "push"],
                condition="Kill switch activated",
                cooldown_seconds=60,
            ),
        ]

        for rule in defaults:
            self._rules[rule.rule_id] = rule

    # ── Acknowledgment ────────────────────────────────────────────────────────
    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge an alert."""
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                self._save_state()
                return True
        return False

    # ── Statistics ────────────────────────────────────────────────────────────
    def get_stats(self) -> dict:
        """Get alert statistics."""
        total = len(self._alerts)
        delivered = sum(1 for a in self._alerts if a.delivered)
        acknowledged = sum(1 for a in self._alerts if a.acknowledged)

        by_severity = {}
        by_type = {}
        for a in self._alerts:
            by_severity[a.severity] = by_severity.get(a.severity, 0) + 1
            by_type[a.alert_type] = by_type.get(a.alert_type, 0) + 1

        return {
            "total_alerts": total,
            "delivered": delivered,
            "acknowledged": acknowledged,
            "channels_configured": len(self._channels),
            "rules_configured": len(self._rules),
            "by_severity": by_severity,
            "by_type": by_type,
        }

    def get_recent_alerts(self, count: int = 20) -> list[dict]:
        """Get recent alerts."""
        return [asdict(a) for a in self._alerts[-count:]]

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save dispatcher state."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "total_alerts": len(self._alerts),
            "alerts": [asdict(a) for a in self._alerts[-500:]],
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "alert_state.json").write_text(json.dumps(state, indent=2, default=str))

    def _load_state(self):
        """Load persisted state."""
        path = DATA_DIR / "alert_state.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            for a in state.get("alerts", []):
                self._alerts.append(Alert(**a))
        except Exception as e:
            logger.error("Failed to load alert state: %s", e)
