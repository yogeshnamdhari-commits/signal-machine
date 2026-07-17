"""
EMA_V5 Notification Queue — Async queue for buffered notification delivery.
Isolated from existing queue systems.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from .telegram_bot import EMAv5TelegramBot
from .alert_manager import EMAv5AlertManager, EMAv5Alert, EMAv5AlertType


@dataclass
class QueueItem:
    """Single queue item."""
    alert: EMAv5Alert
    priority: int = 0  # higher = more urgent
    created_at: float = 0.0
    attempts: int = 0
    max_attempts: int = 3


class EMAv5NotificationQueue:
    """Async notification queue with priority and retry."""

    def __init__(self, bot: Optional[EMAv5TelegramBot] = None,
                 alert_manager: Optional[EMAv5AlertManager] = None) -> None:
        self._bot = bot or EMAv5TelegramBot()
        self._alert_mgr = alert_manager or EMAv5AlertManager(self._bot)
        self._queue: List[QueueItem] = []
        self._processing = False
        self._process_task: Optional[asyncio.Task] = None
        self._batch_size = 5
        self._batch_delay = 1.0  # seconds between batches
        self._stats = {"queued": 0, "sent": 0, "failed": 0, "retry": 0}

    def enqueue(
        self,
        alert_type: EMAv5AlertType,
        data: Optional[Dict] = None,
        title: str = "",
        message: str = "",
        priority: int = 0,
    ) -> str:
        """Add an alert to the queue. Returns alert ID."""
        alert = self._alert_mgr.create_alert(
            alert_type=alert_type, title=title, message=message, data=data,
        )
        item = QueueItem(
            alert=alert,
            priority=priority,
            created_at=time.time(),
        )
        self._queue.append(item)
        self._stats["queued"] += 1

        # Sort by priority (descending)
        self._queue.sort(key=lambda x: -x.priority)

        logger.debug("EMAv5 queue: +1 item (total={})", len(self._queue))
        return alert.alert_id

    async def process(self) -> int:
        """Process queued items. Returns number sent."""
        if self._processing:
            return 0

        self._processing = True
        sent_count = 0

        try:
            # Process in batches
            while self._queue:
                batch = self._queue[:self._batch_size]
                self._queue = self._queue[self._batch_size:]

                for item in batch:
                    try:
                        success = await self._alert_mgr.send_alert(item.alert)
                        if success:
                            sent_count += 1
                            self._stats["sent"] += 1
                        else:
                            item.attempts += 1
                            if item.attempts < item.max_attempts:
                                self._queue.append(item)
                                self._stats["retry"] += 1
                            else:
                                self._stats["failed"] += 1
                    except Exception as e:
                        logger.error("EMAv5 queue: send error: {}", e)
                        item.attempts += 1
                        if item.attempts < item.max_attempts:
                            self._queue.append(item)
                        else:
                            self._stats["failed"] += 1

                # Delay between batches
                if self._queue:
                    await asyncio.sleep(self._batch_delay)

        finally:
            self._processing = False

        return sent_count

    async def start_processing(self) -> None:
        """Start background queue processing."""
        if self._process_task and not self._process_task.done():
            return

        async def _loop():
            while True:
                try:
                    await self.process()
                except Exception as e:
                    logger.error("EMAv5 queue loop error: {}", e)
                await asyncio.sleep(5)  # Check every 5 seconds

        self._process_task = asyncio.create_task(_loop())
        logger.info("EMAv5 notification queue: processing started")

    async def stop_processing(self) -> None:
        """Stop background queue processing."""
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
            self._process_task = None
            logger.info("EMAv5 notification queue: processing stopped")

    def get_queue_size(self) -> int:
        """Get current queue size."""
        return len(self._queue)

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        return {
            **self._stats,
            "queue_size": len(self._queue),
            "is_processing": self._processing,
        }

    def clear(self) -> int:
        """Clear the queue. Returns number of items cleared."""
        count = len(self._queue)
        self._queue.clear()
        return count
