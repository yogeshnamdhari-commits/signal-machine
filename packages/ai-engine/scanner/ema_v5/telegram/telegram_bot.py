"""
EMA_V5 Telegram Bot — Sends notifications via Telegram API.
Isolated from existing telegram bot. Uses httpx for async HTTP.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    logger.warning("httpx not installed — Telegram bot will use mock mode")


@dataclass
class EMAv5TelegramConfig:
    """Telegram bot configuration."""
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    # Rate limiting
    min_interval_sec: int = 5
    max_per_minute: int = 20
    max_per_hour: int = 100
    # Features
    send_signals: bool = True
    send_exits: bool = True
    send_errors: bool = True
    send_daily_summary: bool = True
    send_weekly_summary: bool = True


class EMAv5TelegramBot:
    """Sends EMA_V5 notifications via Telegram."""

    API_BASE = "https://api.telegram.org"

    def __init__(self, config: Optional[EMAv5TelegramConfig] = None) -> None:
        self.config = config or EMAv5TelegramConfig()
        self._send_times: List[float] = []
        self._minute_times: List[float] = []
        self._hour_times: List[float] = []
        self._message_count = 0
        self._client: Optional[Any] = None

    async def initialize(self) -> None:
        """Initialize the bot client."""
        if not self.config.enabled:
            logger.info("EMAv5 Telegram: disabled (mock mode)")
            return

        if not HAS_HTTPX:
            logger.warning("EMAv5 Telegram: httpx not available, using mock mode")
            return

        if not self.config.bot_token or not self.config.chat_id:
            logger.warning("EMAv5 Telegram: missing bot_token or chat_id")
            return

        self._client = httpx.AsyncClient(timeout=30.0)
        logger.info("EMAv5 Telegram: initialized, chat_id={}", self.config.chat_id)

    async def close(self) -> None:
        """Close the bot client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_preview: bool = True,
    ) -> bool:
        """Send a text message to Telegram."""
        if not self.config.enabled:
            logger.debug("EMAv5 Telegram (mock): {}", text[:100])
            return True

        if not self._client:
            logger.warning("EMAv5 Telegram: not initialized")
            return False

        # Rate limiting
        if not self._check_rate_limit():
            logger.debug("EMAv5 Telegram: rate limited, message queued")
            return False

        try:
            url = f"{self.API_BASE}/bot{self.config.bot_token}/sendMessage"
            payload = {
                "chat_id": self.config.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_preview,
            }

            response = await self._client.post(url, json=payload)
            self._record_send()

            if response.status_code == 200:
                self._message_count += 1
                logger.debug("EMAv5 Telegram: message sent (#{})", self._message_count)
                return True
            else:
                logger.error("EMAv5 Telegram: send failed status={}", response.status_code)
                return False

        except Exception as e:
            logger.error("EMAv5 Telegram: send error: {}", e)
            return False

    async def send_signal(self, signal: Dict[str, Any]) -> bool:
        """Send a signal notification."""
        if not self.config.send_signals:
            return False

        from .message_formatter import EMAv5MessageFormatter
        text = EMAv5MessageFormatter.signal_message(signal)
        return await self.send_message(text)

    async def send_exit(self, exit_data: Dict[str, Any]) -> bool:
        """Send an exit notification."""
        if not self.config.send_exits:
            return False

        from .message_formatter import EMAv5MessageFormatter
        text = EMAv5MessageFormatter.exit_message(exit_data)
        return await self.send_message(text)

    async def send_error(self, error: str, context: str = "") -> bool:
        """Send an error notification."""
        if not self.config.send_errors:
            return False

        from .message_formatter import EMAv5MessageFormatter
        text = EMAv5MessageFormatter.error_message(error, context)
        return await self.send_message(text)

    async def send_daily_summary(self, summary: Dict[str, Any]) -> bool:
        """Send daily summary."""
        if not self.config.send_daily_summary:
            return False

        from .message_formatter import EMAv5MessageFormatter
        text = EMAv5MessageFormatter.daily_summary(summary)
        return await self.send_message(text)

    async def send_weekly_summary(self, summary: Dict[str, Any]) -> bool:
        """Send weekly summary."""
        if not self.config.send_weekly_summary:
            return False

        from .message_formatter import EMAv5MessageFormatter
        text = EMAv5MessageFormatter.weekly_summary(summary)
        return await self.send_message(text)

    def _check_rate_limit(self) -> bool:
        """Check if we can send (rate limiting)."""
        now = time.time()

        # Min interval
        if self._send_times and now - self._send_times[-1] < self.config.min_interval_sec:
            return False

        # Per minute
        self._minute_times = [t for t in self._minute_times if now - t < 60]
        if len(self._minute_times) >= self.config.max_per_minute:
            return False

        # Per hour
        self._hour_times = [t for t in self._hour_times if now - t < 3600]
        if len(self._hour_times) >= self.config.max_per_hour:
            return False

        return True

    def _record_send(self) -> None:
        """Record a send event for rate limiting."""
        now = time.time()
        self._send_times.append(now)
        self._minute_times.append(now)
        self._hour_times.append(now)

        # Trim old entries
        if len(self._send_times) > 1000:
            self._send_times = self._send_times[-500:]

    def get_status(self) -> Dict[str, Any]:
        """Get bot status."""
        return {
            "enabled": self.config.enabled,
            "initialized": self._client is not None,
            "messages_sent": self._message_count,
            "rate_limit_ok": self._check_rate_limit(),
        }
