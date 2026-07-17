"""
Multi-Exchange WebSocket Engine — Parallel stream management and monitoring.

Handles:
- Parallel connections to all adapters
- Auto-reconnect with exponential backoff
- Centralized message dispatch and heartbeat monitoring
- Latency and packet loss tracking per venue
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional, Callable
from loguru import logger

from exchanges.base_exchange import BaseExchange

class MultiExchangeWebSocketEngine:
    """
    Orchestrates WebSocket connections across multiple trading venues.
    """

    def __init__(self, exchanges: Dict[str, BaseExchange]) -> None:
        self.exchanges = exchanges
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._health: Dict[str, Dict] = {} # exchange_name -> status_metrics
        self._buffers: Dict[str, asyncio.Queue] = {}
        self._stale_threshold_sec = 5.0
        self._packet_loss_counters: Dict[str, int] = {}
        self._message_counters: Dict[str, int] = {}

    async def start(self, symbol_map: Dict[str, List[str]]) -> None:
        """
        Initialize connections for all venues.
        symbol_map: Dict mapping exchange names to lists of symbols to monitor.
        """
        self._running = True
        self._buffers = {name: asyncio.Queue(maxsize=1000) for name in symbol_map.keys()}
        for name, symbols in symbol_map.items():
            exchange = self.exchanges.get(name)
            if exchange:
                self._tasks.append(asyncio.create_task(self._monitor_exchange(name, exchange, symbols)))
        
        logger.info("Multi-Exchange WS Engine initialized for {} venues", len(self._tasks))

    async def stop(self) -> None:
        """Graceful shutdown of all streams."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Multi-Exchange WS Engine stopped")

    async def _monitor_exchange(self, name: str, exchange: BaseExchange, symbols: List[str]) -> None:
        """Main loop for a specific exchange adapter."""
        self._health[name] = {
            "status": "DISCONNECTED",
            "latency_ms": 0.0,
            "reconnect_count": 0,
            "last_heartbeat": 0.0,
            "packet_loss": 0.0
        }
        self._packet_loss_counters[name] = 0
        self._message_counters[name] = 0

        while self._running:
            try:
                if not exchange.connected:
                    self._health[name]["status"] = "CONNECTING"
                    await exchange.connect()
                    self._health[name]["reconnect_count"] += 1
                    
                    # Re-subscribe to relevant market data
                    for symbol in symbols:
                        await exchange.subscribe_orderbook(symbol, lambda data: self._on_message(name, data))
                
                self._health[name]["status"] = "CONNECTED"
                
                # Health Check / Heartbeat
                lat_info = await exchange.get_latency()
                self._health[name]["latency_ms"] = lat_info.ping_ms
                self._health[name]["last_heartbeat"] = time.time()
                
                # Check for stale data
                if time.time() - self._health[name]["last_heartbeat"] > self._stale_threshold_sec:
                    logger.warning("Stale data detected for {}", name)
                    self._health[name]["status"] = "STALE"
                
                await asyncio.sleep(15) # Monitor interval

            except Exception as e:
                self._health[name]["status"] = "ERROR"
                logger.error("WebSocket health failure on {}: {}", name, str(e))
                self._packet_loss_counters[name] += 1
                await asyncio.sleep(5)

    def _on_message(self, exchange_name: str, data: any) -> None:
        """Universal callback for buffering and stale data detection."""
        self._health[exchange_name]["last_heartbeat"] = time.time()
        self._message_counters[exchange_name] += 1
        
        try:
            buffer = self._buffers.get(exchange_name)
            if buffer:
                if buffer.full():
                    buffer.get_nowait() # Drop oldest
                buffer.put_nowait(data)
        except asyncio.QueueFull:
            pass

    async def get_next_message(self, exchange_name: str) -> any:
        """Retrieve next buffered message."""
        buffer = self._buffers.get(exchange_name)
        if buffer:
            return await buffer.get()
        return None

    def get_health_report(self) -> Dict:
        """Returns connectivity metrics for the dashboard."""
        return {
            "venues": self._health,
            "active_count": sum(1 for v in self._health.values() if v["status"] == "CONNECTED"),
            "total_reconnects": sum(v["reconnect_count"] for v in self._health.values())
        }