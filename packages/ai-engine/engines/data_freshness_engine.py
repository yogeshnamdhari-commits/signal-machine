"""
Data Freshness Engine — Tracks real-time data health per exchange and data source.

Monitors:
  - Per-exchange WebSocket tick age (time since last tick)
  - Per-exchange connection status (connected / disconnected / reconnecting)
  - Dropped / missed messages (heartbeat gaps)
  - Per-data-source age (OI polling, funding, exchange flow, trade stream)
  - Overall data freshness score (0-100)

Used by dashboard to display honest "LIVE" status with data provenance.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class ExchangeHealth:
    """Health status for a single exchange connection."""
    name: str
    connected: bool = False
    last_tick_ts: float = 0.0
    tick_count: int = 0
    dropped_count: int = 0
    reconnect_count: int = 0
    last_heartbeat_ts: float = 0.0
    error_count: int = 0
    avg_latency_ms: float = 0.0

    @property
    def tick_age_sec(self) -> float:
        """Seconds since last tick, or 999 if never received."""
        if self.last_tick_ts <= 0:
            return 999.0
        return time.time() - self.last_tick_ts

    @property
    def is_fresh(self) -> bool:
        """True if tick received within last 30 seconds."""
        return self.tick_age_sec < 30

    @property
    def status_label(self) -> str:
        if not self.connected:
            return "disconnected"
        age = self.tick_age_sec
        if age < 5:
            return "live"
        elif age < 30:
            return "stale"
        elif age < 120:
            return "slow"
        else:
            return "dead"

    @property
    def status_icon(self) -> str:
        label = self.status_label
        return {"live": "🟢", "stale": "🟡", "slow": "🟠", "dead": "🔴", "disconnected": "⚫"}.get(label, "❓")

    @property
    def age_display(self) -> str:
        age = self.tick_age_sec
        if age >= 999:
            return "never"
        elif age < 1:
            return f"{age*1000:.0f}ms"
        elif age < 60:
            return f"{age:.1f}s"
        elif age < 3600:
            return f"{age/60:.1f}m"
        else:
            return f"{age/3600:.1f}h"


@dataclass
class DataSourceHealth:
    """Health status for a logical data source (e.g., OI polling, funding)."""
    name: str
    last_update_ts: float = 0.0
    update_count: int = 0
    error_count: int = 0
    avg_interval_sec: float = 0.0
    source_detail: str = ""  # e.g. "Binance REST /fapi/v1/openInterest"

    @property
    def age_sec(self) -> float:
        if self.last_update_ts <= 0:
            return 999.0
        return time.time() - self.last_update_ts

    @property
    def age_display(self) -> str:
        age = self.age_sec
        if age >= 999:
            return "never"
        elif age < 60:
            return f"{age:.0f}s ago"
        elif age < 3600:
            return f"{age/60:.0f}m ago"
        else:
            return f"{age/3600:.1f}h ago"


class DataFreshnessEngine:
    """
    Tracks data freshness across all exchanges and data sources.

    Usage:
        freshness = DataFreshnessEngine()

        # On every WS tick per exchange:
        freshness.record_tick("binance")
        freshness.record_tick("bybit")

        # On data source updates:
        freshness.record_data_update("open_interest", "Binance WebSocket @openInterest (3s push)")
        freshness.record_data_update("funding", "Binance + Bybit + OKX WS")
        freshness.record_data_update("exchange_flow", "Binance Trade Stream")
        freshness.record_data_update("liquidation", "Binance Liq Stream")

        # On dropped/missed messages:
        freshness.record_drop("binance")
        freshness.record_error("okx")

        # Get snapshot for bridge:
        snapshot = freshness.get_snapshot()
    """

    EXCHANGES = ["binance", "bybit", "okx", "delta"]
    DATA_SOURCES = ["open_interest", "funding", "exchange_flow", "liquidation", "klines", "trades"]

    def __init__(self) -> None:
        self._exchanges: Dict[str, ExchangeHealth] = {
            name: ExchangeHealth(name=name) for name in self.EXCHANGES
        }
        self._sources: Dict[str, DataSourceHealth] = {
            name: DataSourceHealth(name=name) for name in self.DATA_SOURCES
        }
        self._source_details = {
            "open_interest": "Binance Futures REST API (60s poll)",
            "funding": "Binance + Bybit + OKX (volume-weighted avg)",
            "exchange_flow": "Binance Futures Taker Trade Stream",
            "liquidation": "Binance Futures Liquidation Stream",
            "klines": "Binance OHLCV (1m/5m/15m/1h/4h)",
            "trades": "Binance Futures WebSocket Trade Stream",
        }
        for name, detail in self._source_details.items():
            if name in self._sources:
                self._sources[name].source_detail = detail

    # ── Event recording ──

    def record_tick(self, exchange: str) -> None:
        """Record a WebSocket tick received from an exchange."""
        ex = self._exchanges.get(exchange.lower())
        if ex:
            now = time.time()
            ex.last_tick_ts = now
            ex.tick_count += 1
            ex.connected = True

    def record_heartbeat(self, exchange: str) -> None:
        """Record a heartbeat/ping response from an exchange."""
        ex = self._exchanges.get(exchange.lower())
        if ex:
            ex.last_heartbeat_ts = time.time()
            ex.connected = True

    def record_drop(self, exchange: str) -> None:
        """Record a dropped/missed message from an exchange."""
        ex = self._exchanges.get(exchange.lower())
        if ex:
            ex.dropped_count += 1

    def record_error(self, exchange: str) -> None:
        """Record an error on an exchange connection."""
        ex = self._exchanges.get(exchange.lower())
        if ex:
            ex.error_count += 1

    def record_reconnect(self, exchange: str) -> None:
        """Record a reconnection event for an exchange."""
        ex = self._exchanges.get(exchange.lower())
        if ex:
            ex.reconnect_count += 1
            ex.connected = True
            ex.last_tick_ts = time.time()  # Reset tick time on reconnect

    def set_disconnected(self, exchange: str) -> None:
        """Mark an exchange as disconnected."""
        ex = self._exchanges.get(exchange.lower())
        if ex:
            ex.connected = False

    def record_data_update(self, source: str, detail: str = "") -> None:
        """Record an update from a logical data source."""
        src = self._sources.get(source.lower())
        if src:
            now = time.time()
            if src.last_update_ts > 0 and src.update_count > 0:
                interval = now - src.last_update_ts
                # Running average
                src.avg_interval_sec = (src.avg_interval_sec * src.update_count + interval) / (src.update_count + 1)
            src.last_update_ts = now
            src.update_count += 1
            if detail:
                src.source_detail = detail

    def record_data_error(self, source: str) -> None:
        """Record an error from a data source."""
        src = self._sources.get(source.lower())
        if src:
            src.error_count += 1

    # ── Snapshot ──

    def get_exchange_snapshot(self, name: str) -> Optional[Dict[str, Any]]:
        """Get snapshot for a single exchange."""
        ex = self._exchanges.get(name.lower())
        if not ex:
            return None
        return {
            "name": ex.name,
            "connected": ex.connected,
            "status": ex.status_label,
            "status_icon": ex.status_icon,
            "age_display": ex.age_display,
            "tick_age_sec": round(ex.tick_age_sec, 1),
            "tick_count": ex.tick_count,
            "dropped_count": ex.dropped_count,
            "reconnect_count": ex.reconnect_count,
            "error_count": ex.error_count,
        }

    def get_source_snapshot(self, name: str) -> Optional[Dict[str, Any]]:
        """Get snapshot for a single data source."""
        src = self._sources.get(name.lower())
        if not src:
            return None
        return {
            "name": src.name,
            "age_display": src.age_display,
            "age_sec": round(src.age_sec, 1),
            "update_count": src.update_count,
            "error_count": src.error_count,
            "avg_interval_sec": round(src.avg_interval_sec, 1) if src.avg_interval_sec > 0 else 0,
            "source_detail": src.source_detail,
        }

    def get_snapshot(self) -> Dict[str, Any]:
        """Get full freshness snapshot for bridge / dashboard."""
        exchanges = {}
        connected_count = 0
        total_dropped = 0
        for name in self.EXCHANGES:
            snap = self.get_exchange_snapshot(name)
            exchanges[name] = snap
            if snap and snap["connected"]:
                connected_count += 1
                total_dropped += snap["dropped_count"]

        sources = {}
        for name in self.DATA_SOURCES:
            snap = self.get_source_snapshot(name)
            sources[name] = snap

        # Compute freshness score
        score = self._compute_freshness_score()

        return {
            "exchanges": exchanges,
            "sources": sources,
            "connected_exchanges": connected_count,
            "total_exchanges": len(self.EXCHANGES),
            "total_dropped": total_dropped,
            "freshness_score": score,
            "timestamp": time.time(),
        }

    def _compute_freshness_score(self) -> float:
        """
        Compute a 0-100 data freshness score.
        Penalties: disconnected exchanges, stale ticks, dropped messages.
        """
        score = 100.0

        for name, ex in self._exchanges.items():
            if not ex.connected:
                score -= 15  # Heavy penalty for disconnected
            elif ex.tick_age_sec > 60:
                score -= 10
            elif ex.tick_age_sec > 30:
                score -= 5
            elif ex.tick_age_sec > 10:
                score -= 2

            if ex.dropped_count > 0:
                score -= min(ex.dropped_count * 0.5, 10)
            if ex.error_count > 0:
                score -= min(ex.error_count * 1.0, 10)

        for name, src in self._sources.items():
            if src.age_sec > 300:
                score -= 5
            elif src.age_sec > 120:
                score -= 3

        return max(0.0, min(100.0, score))
