"""
DeltaTerminal Event Bus — High-performance async pub-sub for modular communication.
"""
from __future__ import annotations
import asyncio
from typing import Any, Callable, Dict, List, TypeVar
from loguru import logger

T = TypeVar("T")

class EventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[[Any], asyncio.Task]]] = {}

    def subscribe(self, event_type: str, callback: Callable[[Any], Any]) -> None:
        """Subscribe to a specific event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
        logger.debug("Subscribed to event: {}", event_type)

    async def publish(self, event_type: str, data: Any) -> None:
        """Publish an event to all interested subscribers."""
        if event_type not in self._subscribers:
            return

        tasks = []
        for callback in self._subscribers[event_type]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    tasks.append(asyncio.create_task(callback(data)))
                else:
                    callback(data)
            except Exception as e:
                logger.error("Error in event subscriber {}: {}", callback.__name__, e)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

# Global Instance
bus = EventBus()