"""
Telegram Alert Engine — enhanced alerting with formatting, rate limiting, and rich messages.
Extends base TelegramAlerts with structured alerts, batching, and notification preferences.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

import httpx
from loguru import logger


class AlertLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertCategory(Enum):
    SIGNAL = "signal"
    POSITION = "position"
    RISK = "risk"
    SYSTEM = "system"
    MARKET = "market"
    PERFORMANCE = "performance"


@dataclass
class Alert:
    id: str
    category: AlertCategory
    level: AlertLevel
    title: str
    message: str
    timestamp: datetime
    symbol: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    sent: bool = False
    acknowledged: bool = False


@dataclass
class AlertConfig:
    enabled: bool = True
    bot_token: str = ""
    chat_id: str = ""
    # Rate limiting
    min_interval_sec: int = 5
    max_alerts_per_hour: int = 60
    max_alerts_per_day: int = 500
    # Level filtering
    min_level: AlertLevel = AlertLevel.LOW
    enabled_categories: Set[AlertCategory] = field(default_factory=lambda: set(AlertCategory))
    # Quiet hours (UTC)
    quiet_hours_start: int = 0  # 0 = midnight
    quiet_hours_end: int = 6    # 6 AM
    quiet_hours_enabled: bool = False


class TelegramAlertEngine:
    """
    Enhanced Telegram alert engine with:
    - Structured alert formatting (signals, positions, risk, system)
    - Rate limiting (per-minute, per-hour, per-day)
    - Alert batching for high-frequency events
    - Category and level filtering
    - Quiet hours support
    - Alert acknowledgment tracking
    - Rich markdown formatting with emojis
    """

    def __init__(self, config: Optional[AlertConfig] = None) -> None:
        self.config = config or AlertConfig()
        self._alert_history: List[Alert] = []
        self._send_times: List[float] = []
        self._daily_count = 0
        self._daily_reset_date = datetime.now().date()
        self._pending_batch: List[Alert] = []
        self._batch_timer: Optional[asyncio.Task] = None
        self._batch_delay = 2.0  # seconds

    async def initialize(self) -> None:
        """Initialize the alert engine."""
        if self.config.enabled and self.config.bot_token:
            logger.info("TelegramAlertEngine ready — chat_id={}", self.config.chat_id)
        else:
            logger.info("TelegramAlertEngine ready — alerts will be logged only")

    # ── Alert Sending ────────────────────────────────────────────

    async def send_alert(self, alert: Alert) -> bool:
        """Send an alert with rate limiting and filtering."""
        # Reset daily counter
        today = datetime.now().date()
        if today != self._daily_reset_date:
            self._daily_count = 0
            self._daily_reset_date = today

        # Check filters
        if not self._should_send(alert):
            return False

        # Rate limit
        if not self._check_rate_limit():
            logger.debug("Rate limited: {}", alert.title)
            return False

        # Format message
        message = self._format_alert(alert)

        # Send
        success = await self._send_telegram(message)
        if success:
            alert.sent = True
            self._send_times.append(time.time())
            self._daily_count += 1

        self._alert_history.append(alert)
        if len(self._alert_history) > 1000:
            self._alert_history = self._alert_history[-500:]

        return success

    async def send_signal_alert(self, signal: Dict) -> bool:
        """Send a structured signal alert."""
        sig_type = signal.get("type", "LONG")
        icon = "🟢" if sig_type == "LONG" else "🔴"
        confidence = signal.get("confidence", 0)
        bar = "█" * int(confidence * 10) + "░" * (10 - int(confidence * 10))

        # Risk/Reward
        entry = signal.get("entry_price", 0)
        sl = signal.get("stop_loss", 0)
        tp = signal.get("take_profit", 0)
        rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

        risk_adjusted = signal.get("risk_adjusted", {})
        qty = risk_adjusted.get("quantity", 0)
        pos_val = risk_adjusted.get("position_value", 0)

        alert = Alert(
            id=f"sig_{signal.get('id', int(time.time()))}",
            category=AlertCategory.SIGNAL,
            level=AlertLevel.HIGH if confidence >= 0.8 else AlertLevel.MEDIUM,
            title=f"{icon} {sig_type} {signal.get('symbol', '?')}",
            message=(
                f"{icon} *SIGNAL: {sig_type} {signal.get('symbol', '?')}*\n\n"
                f"📍 *Entry:* `${entry:,.4f}`\n"
                f"🛑 *Stop Loss:* `${sl:,.4f}` ({abs(entry-sl)/entry*100:.2f}%)\n"
                f"🎯 *Take Profit:* `${tp:,.4f}` ({abs(tp-entry)/entry*100:.2f}%)\n"
                f"📊 *R:R:* `{rr:.2f}`\n\n"
                f"🤖 *Confidence:* `{confidence:.0%}` `{bar}`\n"
                f"🌀 *Regime:* `{signal.get('regime', 'N/A')}`\n"
            ),
            timestamp=datetime.now(),
            symbol=signal.get("symbol"),
            data=signal,
        )

        if qty:
            alert.message += (
                f"\n💰 *Position:*\n"
                f"  Qty: `{qty}` | Value: `${pos_val:,.2f}`\n"
                f"  Risk: `${risk_adjusted.get('margin_required', 0):,.2f}`"
            )

        return await self.send_alert(alert)

    async def send_position_alert(
        self, position: Dict, event: str, pnl: float = 0, price: float = 0
    ) -> bool:
        """Send a position update alert."""
        sym = position.get("symbol", "?")
        side = position.get("side", "LONG")
        icon = "🟢" if side == "LONG" else "🔴"

        if event == "opened":
            level = AlertLevel.MEDIUM
            title = f"📍 OPENED {side} {sym}"
            msg = (
                f"📍 *POSITION OPENED*\n\n"
                f"{icon} *{side} {sym}*\n"
                f"Entry: `${price:,.4f}`\n"
                f"SL: `${position.get('stop_loss', 0):,.4f}`\n"
                f"TP: `${position.get('take_profit', 0):,.4f}`"
            )
        elif event == "closed":
            level = AlertLevel.HIGH if pnl < 0 else AlertLevel.MEDIUM
            result_icon = "✅" if pnl > 0 else "❌"
            title = f"{result_icon} CLOSED {sym} — PnL: ${pnl:+,.2f}"
            msg = (
                f"{result_icon} *POSITION CLOSED*\n\n"
                f"*{sym}* | {side}\n"
                f"Entry: `${position.get('entry_price', 0):,.4f}` → Exit: `${price:,.4f}`\n"
                f"PnL: *`${pnl:+,.2f}`* ({pnl/position.get('entry_price', 1)*100:+.1f}%)\n"
                f"Reason: `{position.get('exit_reason', 'N/A')}`"
            )
        else:
            return False

        alert = Alert(
            id=f"pos_{sym}_{event}_{int(time.time())}",
            category=AlertCategory.POSITION,
            level=level,
            title=title,
            message=msg,
            timestamp=datetime.now(),
            symbol=sym,
            data={"position": position, "event": event, "pnl": pnl, "price": price},
        )
        return await self.send_alert(alert)

    async def send_risk_alert(self, title: str, details: str, level: AlertLevel = AlertLevel.HIGH) -> bool:
        """Send a risk management alert."""
        icons = {
            AlertLevel.LOW: "ℹ️",
            AlertLevel.MEDIUM: "⚠️",
            AlertLevel.HIGH: "🔶",
            AlertLevel.CRITICAL: "🚨",
        }
        icon = icons.get(level, "📢")

        alert = Alert(
            id=f"risk_{int(time.time())}",
            category=AlertCategory.RISK,
            level=level,
            title=f"{icon} {title}",
            message=f"{icon} *RISK ALERT*\n\n*{title}*\n{details}",
            timestamp=datetime.now(),
        )
        return await self.send_alert(alert)

    async def send_system_alert(self, title: str, message: str, level: AlertLevel = AlertLevel.LOW) -> bool:
        """Send a system notification."""
        icons = {
            AlertLevel.LOW: "ℹ️",
            AlertLevel.MEDIUM: "⚙️",
            AlertLevel.HIGH: "⚠️",
            AlertLevel.CRITICAL: "🚨",
        }
        icon = icons.get(level, "📢")

        alert = Alert(
            id=f"sys_{int(time.time())}",
            category=AlertCategory.SYSTEM,
            level=level,
            title=f"{icon} {title}",
            message=f"{icon} *{title}*\n{message}",
            timestamp=datetime.now(),
        )
        return await self.send_alert(alert)

    async def send_performance_summary(self, metrics: Dict) -> bool:
        """Send daily performance summary."""
        alert = Alert(
            id=f"perf_{int(time.time())}",
            category=AlertCategory.PERFORMANCE,
            level=AlertLevel.LOW,
            title="📊 Daily Performance Summary",
            message=self._format_performance(metrics),
            timestamp=datetime.now(),
            data=metrics,
        )
        return await self.send_alert(alert)

    # ── Formatting ───────────────────────────────────────────────

    def _format_alert(self, alert: Alert) -> str:
        """Format alert for Telegram."""
        return alert.message

    def _format_performance(self, metrics: Dict) -> str:
        """Format performance metrics for Telegram."""
        return (
            f"📊 *DAILY PERFORMANCE SUMMARY*\n"
            f"{'─' * 28}\n"
            f"💰 *Portfolio:* `${metrics.get('equity', 0):,.2f}`\n"
            f"📈 *PnL:* `${metrics.get('daily_pnl', 0):+,.2f}`\n"
            f"🎯 *Win Rate:* `{metrics.get('win_rate', 0):.1f}%`\n"
            f"📊 *Trades:* `{metrics.get('trades_today', 0)}`\n"
            f"⚡ *Sharpe:* `{metrics.get('sharpe', 0):.2f}`\n"
            f"📉 *Drawdown:* `{metrics.get('drawdown', 0):.1f}%`\n"
            f"{'─' * 28}\n"
            f"⏰ {datetime.now().strftime('%H:%M UTC')}"
        )

    # ── Rate Limiting ────────────────────────────────────────────

    def _should_send(self, alert: Alert) -> bool:
        """Check if alert should be sent based on filters."""
        if not self.config.enabled:
            return False

        # Category filter
        if self.config.enabled_categories and alert.category not in self.config.enabled_categories:
            return False

        # Level filter
        levels = [AlertLevel.LOW, AlertLevel.MEDIUM, AlertLevel.HIGH, AlertLevel.CRITICAL]
        if levels.index(alert.level) < levels.index(self.config.min_level):
            return False

        # Quiet hours
        if self.config.quiet_hours_enabled:
            hour = datetime.now().hour
            if self.config.quiet_hours_start <= hour < self.config.quiet_hours_end:
                if alert.level.value not in ("high", "critical"):
                    return False

        return True

    def _check_rate_limit(self) -> bool:
        """Check rate limits."""
        now = time.time()

        # Per-minute limit
        recent = [t for t in self._send_times if now - t < 60]
        if len(recent) >= self.config.max_alerts_per_hour // 60:
            return False

        # Per-hour limit
        hourly = [t for t in self._send_times if now - t < 3600]
        if len(hourly) >= self.config.max_alerts_per_hour:
            return False

        # Per-day limit
        if self._daily_count >= self.config.max_alerts_per_day:
            return False

        # Min interval
        if self._send_times and now - self._send_times[-1] < self.config.min_interval_sec:
            return False

        return True

    # ── Telegram API ─────────────────────────────────────────────

    async def _send_telegram(self, text: str) -> bool:
        """Send message via Telegram API."""
        if not self.config.bot_token or not self.config.chat_id:
            logger.info("TG Alert: {}", text[:200])
            return True

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage",
                    json={
                        "chat_id": self.config.chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code == 200:
                    return True
                else:
                    logger.error("Telegram API error {}: {}", resp.status_code, resp.text)
                    return False
        except Exception as exc:
            logger.error("Telegram send failed: {}", exc)
            return False

    # ── Alert History ────────────────────────────────────────────

    def get_recent_alerts(self, count: int = 50, category: Optional[AlertCategory] = None) -> List[Alert]:
        """Get recent alerts, optionally filtered by category."""
        alerts = self._alert_history
        if category:
            alerts = [a for a in alerts if a.category == category]
        return alerts[-count:]

    def get_unacknowledged(self) -> List[Alert]:
        """Get all unacknowledged alerts."""
        return [a for a in self._alert_history if not a.acknowledged]

    def acknowledge(self, alert_id: str) -> bool:
        """Acknowledge an alert."""
        for alert in self._alert_history:
            if alert.id == alert_id:
                alert.acknowledged = True
                return True
        return False

    def get_stats(self) -> Dict:
        """Get alert statistics."""
        now = time.time()
        return {
            "total": len(self._alert_history),
            "sent": sum(1 for a in self._alert_history if a.sent),
            "unacknowledged": len(self.get_unacknowledged()),
            "last_hour": sum(1 for a in self._alert_history if (now - a.timestamp.timestamp()) < 3600),
            "by_category": {
                cat.value: sum(1 for a in self._alert_history if a.category == cat)
                for cat in AlertCategory
            },
            "by_level": {
                level.value: sum(1 for a in self._alert_history if a.level == level)
                for level in AlertLevel
            },
        }
