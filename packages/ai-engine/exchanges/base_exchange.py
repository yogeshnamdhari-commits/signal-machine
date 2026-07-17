"""
Base Exchange — Abstract interface for all exchange adapters.

Defines the unified contract that SmartOrderRouter, PositionManager,
and ExecutionEngine depend on. Every exchange adapter must implement
all abstract methods to participate in routing and execution.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ── Exchange Identifiers ─────────────────────────────────────────

class ExchangeName(str, Enum):
    """Canonical exchange identifiers used across the system."""
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"
    DELTA = "delta"


# ── Normalized data types ────────────────────────────────────────

@dataclass
class ExchangeOrder:
    """Normalized order response across all exchanges."""
    order_id: str = ""
    client_order_id: str = ""
    symbol: str = ""
    side: str = ""              # BUY / SELL
    order_type: str = ""        # MARKET / LIMIT / STOP_MARKET / etc.
    status: str = ""            # NEW, FILLED, CANCELED, REJECTED, PARTIALLY_FILLED
    price: float = 0.0
    avg_price: float = 0.0
    quantity: float = 0.0
    executed_qty: float = 0.0
    cum_quote: float = 0.0
    time_in_force: str = "GTC"
    reduce_only: bool = False
    close_position: bool = False
    stop_price: float = 0.0
    working_type: str = "CONTRACT_PRICE"
    update_time: int = 0
    transact_time: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "status": self.status,
            "price": self.price,
            "avg_price": self.avg_price,
            "quantity": self.quantity,
            "executed_qty": self.executed_qty,
            "cum_quote": self.cum_quote,
            "time_in_force": self.time_in_force,
            "reduce_only": self.reduce_only,
            "stop_price": self.stop_price,
            "update_time": self.update_time,
        }


@dataclass
class ExchangePosition:
    """Normalized position across all exchanges."""
    symbol: str = ""
    side: str = ""              # LONG / SHORT
    quantity: float = 0.0
    entry_price: float = 0.0
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    leverage: int = 1
    margin_type: str = "cross"
    isolated_margin: float = 0.0
    position_side: str = "BOTH"
    notional: float = 0.0
    liquidation_price: float = 0.0
    update_time: int = 0


@dataclass
class AccountState:
    """Normalized account state across all exchanges."""
    total_balance: float = 0.0
    available_balance: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_margin: float = 0.0
    positions: List[ExchangePosition] = field(default_factory=list)
    update_time: int = 0


@dataclass
class OrderbookLevel:
    """Single orderbook level (price, quantity)."""
    price: float = 0.0
    quantity: float = 0.0


@dataclass
class OrderbookSnapshot:
    """Normalized orderbook snapshot."""
    symbol: str = ""
    bids: List[OrderbookLevel] = field(default_factory=list)
    asks: List[OrderbookLevel] = field(default_factory=list)
    timestamp: float = 0.0

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return 0.0

    @property
    def spread(self) -> float:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return 0.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid > 0:
            return (self.spread / mid) * 10_000
        return 0.0

    @property
    def bid_depth(self) -> float:
        return sum(l.price * l.quantity for l in self.bids)

    @property
    def ask_depth(self) -> float:
        return sum(l.price * l.quantity for l in self.asks)

    @property
    def total_depth(self) -> float:
        return self.bid_depth + self.ask_depth


# Alias for backward compatibility — ExchangeOrderBook == OrderbookSnapshot
ExchangeOrderBook = OrderbookSnapshot


@dataclass
class VenueHealth:
    """Health metrics for an exchange venue."""
    exchange_name: str = ""
    is_healthy: bool = True
    consecutive_failures: int = 0
    total_requests: int = 0
    total_failures: int = 0
    avg_latency_ms: float = 0.0
    last_success_time: float = 0.0
    last_failure_time: float = 0.0
    error_rate: float = 0.0
    uptime_pct: float = 100.0


@dataclass
class FundingInfo:
    """Normalized funding rate info across all exchanges."""
    exchange: str = ""
    symbol: str = ""
    funding_rate: float = 0.0
    next_funding_time: int = 0
    mark_price: float = 0.0
    index_price: float = 0.0
    predicted_rate: float = 0.0

    def __post_init__(self) -> None:
        self.fetched_at: float = time.time()


@dataclass
class ExchangeLatency:
    """Latency metrics for an exchange."""
    exchange: str = ""
    ping_ms: float = 0.0
    update_time: int = 0
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0


class BaseExchange(ABC):
    """
    Abstract base class for all exchange adapters.

    Every exchange must implement:
    - Connection lifecycle (connect / disconnect)
    - Order management (place / cancel / query)
    - Position and account queries
    - Market data (orderbook, funding rate)
    - Fee structure (maker / taker)
    - Latency tracking
    """

    def __init__(self, name: str, api_key: str = "", api_secret: str = "") -> None:
        self._name = name
        self._api_key = api_key
        self._api_secret = api_secret
        self._connected = False

        # Health tracking
        self._request_count = 0
        self._failure_count = 0
        self._consecutive_failures = 0
        self._latency_samples: List[float] = []
        self._max_latency_samples = 100
        self._last_success_time = 0.0
        self._last_failure_time = 0.0
        self._start_time = time.time()

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Lifecycle ────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the exchange."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the exchange."""
        ...

    # ── Order Management ─────────────────────────────────────────

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float = 0.0,
        stop_price: float = 0.0,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        close_position: bool = False,
        position_side: str = "BOTH",
        client_order_id: str = "",
        working_type: str = "CONTRACT_PRICE",
        callback_rate: float = 0.0,
    ) -> ExchangeOrder:
        """Place an order on the exchange."""
        ...

    @abstractmethod
    async def cancel_order(
        self,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> ExchangeOrder:
        """Cancel an open order."""
        ...

    @abstractmethod
    async def get_order(
        self,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> ExchangeOrder:
        """Query order status."""
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: str = "") -> List[ExchangeOrder]:
        """Get all open orders."""
        ...

    # ── Position & Account ───────────────────────────────────────

    @abstractmethod
    async def get_positions(self) -> List[ExchangePosition]:
        """Get all open positions."""
        ...

    @abstractmethod
    async def get_account(self) -> AccountState:
        """Get account state."""
        ...

    @abstractmethod
    async def get_balance(self) -> Dict[str, float]:
        """Get account balance."""
        ...

    # ── Market Data ──────────────────────────────────────────────

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderbookSnapshot:
        """Get orderbook snapshot for routing decisions."""
        ...

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> "FundingInfo":
        """Get current funding rate for the symbol."""
        ...

    @abstractmethod
    async def get_latency(self) -> "ExchangeLatency":
        """Get current latency metrics."""
        ...

    # ── Fee Structure ────────────────────────────────────────────

    @abstractmethod
    def get_maker_fee(self) -> float:
        """Return maker fee rate (e.g. 0.0002 for 0.02%)."""
        ...

    @abstractmethod
    def get_taker_fee(self) -> float:
        """Return taker fee rate (e.g. 0.0004 for 0.04%)."""
        ...

    # ── Latency ──────────────────────────────────────────────────

    def record_latency(self, latency_ms: float) -> None:
        """Record a latency sample for health tracking."""
        self._latency_samples.append(latency_ms)
        if len(self._latency_samples) > self._max_latency_samples:
            self._latency_samples.pop(0)

    def record_success(self) -> None:
        """Record a successful request."""
        self._request_count += 1
        self._consecutive_failures = 0
        self._last_success_time = time.time()

    def record_failure(self) -> None:
        """Record a failed request."""
        self._request_count += 1
        self._failure_count += 1
        self._consecutive_failures += 1
        self._last_failure_time = time.time()

    def get_latency_ms(self) -> float:
        """Get average latency in milliseconds."""
        if not self._latency_samples:
            return 500.0  # Default high latency for unknown
        return sum(self._latency_samples) / len(self._latency_samples)

    def get_p99_latency_ms(self) -> float:
        """Get P99 latency."""
        if not self._latency_samples:
            return 1000.0
        sorted_samples = sorted(self._latency_samples)
        idx = int(len(sorted_samples) * 0.99)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    # ── Health ───────────────────────────────────────────────────

    def get_health(self) -> VenueHealth:
        """Get current venue health metrics."""
        error_rate = (
            self._failure_count / self._request_count
            if self._request_count > 0
            else 0.0
        )
        uptime = (
            self._last_success_time / max(time.time() - self._start_time, 1)
            if self._last_success_time > 0
            else 0.0
        )
        return VenueHealth(
            exchange_name=self._name,
            is_healthy=self._consecutive_failures < 5,
            consecutive_failures=self._consecutive_failures,
            total_requests=self._request_count,
            total_failures=self._failure_count,
            avg_latency_ms=self.get_latency_ms(),
            last_success_time=self._last_success_time,
            last_failure_time=self._last_failure_time,
            error_rate=error_rate,
            uptime_pct=min(uptime * 100, 100.0),
        )

    def is_healthy(self) -> bool:
        """Check if venue is healthy enough for routing."""
        return self._consecutive_failures < 5 and self._connected

    # ── Stats ────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Get adapter statistics."""
        return {
            "name": self._name,
            "connected": self._connected,
            "request_count": self._request_count,
            "failure_count": self._failure_count,
            "consecutive_failures": self._consecutive_failures,
            "avg_latency_ms": round(self.get_latency_ms(), 2),
            "p99_latency_ms": round(self.get_p99_latency_ms(), 2),
            "error_rate": round(
                self._failure_count / max(1, self._request_count), 4
            ),
            "is_healthy": self.is_healthy(),
        }

    # ── Leverage & Margin ────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        """Set leverage — override if exchange supports it."""
        return {"msg": "not supported"}

    async def set_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> Dict:
        """Set margin type — override if exchange supports it."""
        return {"msg": "not supported"}
