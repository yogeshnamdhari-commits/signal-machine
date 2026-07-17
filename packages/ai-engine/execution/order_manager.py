"""
Order Manager — Order lifecycle, state machine, idempotency.

Responsible for:
- Order creation with idempotency keys
- Order state machine (NEW → SUBMITTED → ACCEPTED → FILLED/CANCELLED/REJECTED)
- Duplicate detection and prevention
- Order timeout management
- Order cancellation logic
- Persistent state tracking
- Full audit trail per order
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from loguru import logger

import sys
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from execution.exchange_adapter import (
    ExchangeAdapter, ExchangeOrder, OrderType, OrderSide,
    TimeInForce, PositionSide, ExchangeError, RateLimitError,
)


class OrderState(str, Enum):
    """Order lifecycle states."""
    NEW = "NEW"                       # Created locally
    SUBMITTED = "SUBMITTED"           # Sent to exchange
    ACCEPTED = "ACCEPTED"             # Exchange confirmed
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"                 # Submission failed


class OrderPurpose(str, Enum):
    """Why an order was created."""
    ENTRY = "ENTRY"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"


@dataclass
class OrderRecord:
    """Complete order record with full lifecycle tracking."""
    # Identity
    order_id: str = ""                    # Internal UUID
    client_order_id: str = ""             # Sent to exchange
    exchange_order_id: int = 0            # From exchange
    signal_id: str = ""                   # Source signal

    # Order details
    symbol: str = ""
    side: str = ""                        # BUY / SELL
    order_type: str = ""                  # MARKET, LIMIT, etc.
    purpose: str = ""                     # ENTRY, STOP_LOSS, etc.
    quantity: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    time_in_force: str = "GTC"
    reduce_only: bool = False
    close_position: bool = False
    leverage: int = 1
    position_side: str = "BOTH"

    # State
    state: str = OrderState.NEW.value
    executed_qty: float = 0.0
    avg_price: float = 0.0
    cum_quote: float = 0.0
    fees: float = 0.0

    # Timestamps
    created_at: float = 0.0
    submitted_at: float = 0.0
    accepted_at: float = 0.0
    filled_at: float = 0.0
    cancelled_at: float = 0.0
    updated_at: float = 0.0

    # Metadata
    attempt: int = 0
    rejection_reason: str = ""
    failure_reason: str = ""
    timeout_sec: float = 0.0

    # Idempotency
    idempotency_key: str = ""

    # State history
    state_history: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.idempotency_key:
            self.idempotency_key = f"{self.signal_id}:{self.purpose}:{self.symbol}"

    def to_dict(self) -> Dict:
        return asdict(self)

    def transition(self, new_state: str, reason: str = "") -> None:
        """Record a state transition."""
        old_state = self.state
        self.state = new_state
        self.updated_at = time.time()
        self.state_history.append({
            "from": old_state,
            "to": new_state,
            "reason": reason,
            "time": self.updated_at,
        })
        if new_state == OrderState.SUBMITTED.value:
            self.submitted_at = self.updated_at
        elif new_state == OrderState.ACCEPTED.value:
            self.accepted_at = self.updated_at
        elif new_state == OrderState.FILLED.value:
            self.filled_at = self.updated_at
        elif new_state == OrderState.CANCELLED.value:
            self.cancelled_at = self.updated_at


class OrderManager:
    """
    Manages order lifecycle with idempotency, state tracking, and audit trail.

    Key guarantees:
    - One signal → One entry order (idempotent)
    - One position → One stop loss (idempotent)
    - One position → One take profit (idempotent)
    - No duplicate orders (idempotency key check)
    - Full state machine tracking per order
    - Persistent state for crash recovery
    """

    STATE_FILE = _ai_root / "data" / "execution" / "order_state.json"
    MAX_ORDER_AGE_SEC = 3600  # 1 hour
    ORDER_TIMEOUT_SEC = 30    # Cancel if not filled in 30s for market orders

    def __init__(self, exchange: ExchangeAdapter) -> None:
        self._exchange = exchange
        self._orders: Dict[str, OrderRecord] = {}       # order_id → OrderRecord
        self._by_exchange: Dict[int, str] = {}           # exchange_order_id → order_id
        self._by_signal: Dict[str, List[str]] = {}       # signal_id → [order_ids]
        self._idempotency: Set[str] = set()              # Active idempotency keys
        self._idempotency_map: Dict[str, str] = {}       # idempotency_key → order_id
        self._lock = asyncio.Lock()
        self._on_fill_callback: Optional[Callable] = None
        self._on_cancel_callback: Optional[Callable] = None
        self._on_reject_callback: Optional[Callable] = None

        # Ensure directory exists
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    def set_callbacks(
        self,
        on_fill: Optional[Callable] = None,
        on_cancel: Optional[Callable] = None,
        on_reject: Optional[Callable] = None,
    ) -> None:
        self._on_fill_callback = on_fill
        self._on_cancel_callback = on_cancel
        self._on_reject_callback = on_reject

    # ── Order Creation ───────────────────────────────────────────

    async def create_order(
        self,
        signal_id: str,
        symbol: str,
        side: str,
        order_type: OrderType,
        purpose: OrderPurpose,
        quantity: float,
        price: float = 0.0,
        stop_price: float = 0.0,
        time_in_force: TimeInForce = TimeInForce.GTC,
        reduce_only: bool = False,
        close_position: bool = False,
        leverage: int = 1,
        position_side: PositionSide = PositionSide.BOTH,
        timeout_sec: float = 0.0,
    ) -> Optional[OrderRecord]:
        """
        Create and submit an order with idempotency protection.

        Returns OrderRecord if created, None if duplicate.
        """
        idempotency_key = f"{signal_id}:{purpose.value}:{symbol}"

        async with self._lock:
            # Idempotency check
            if idempotency_key in self._idempotency:
                existing_id = self._idempotency_map.get(idempotency_key)
                if existing_id and existing_id in self._orders:
                    existing = self._orders[existing_id]
                    if existing.state not in (
                        OrderState.CANCELLED.value,
                        OrderState.REJECTED.value,
                        OrderState.FILLED.value,
                        OrderState.FAILED.value,
                        OrderState.EXPIRED.value,
                    ):
                        logger.info("Duplicate order blocked: key={} existing={}",
                                    idempotency_key, existing_id)
                        return None
                    # Previous order was terminal — allow new one
                    self._idempotency.discard(idempotency_key)
                    self._idempotency_map.pop(idempotency_key, None)
                else:
                    self._idempotency.discard(idempotency_key)

            # Create order record
            order_id = str(uuid.uuid4())
            client_order_id = f"DT-{order_id[:12]}-{int(time.time())}"

            order = OrderRecord(
                order_id=order_id,
                client_order_id=client_order_id,
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                order_type=order_type.value,
                purpose=purpose.value,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                time_in_force=time_in_force.value,
                reduce_only=reduce_only,
                close_position=close_position,
                leverage=leverage,
                position_side=position_side.value,
                timeout_sec=timeout_sec or self.ORDER_TIMEOUT_SEC,
                idempotency_key=idempotency_key,
            )

            # Register
            self._orders[order_id] = order
            self._idempotency.add(idempotency_key)
            self._idempotency_map[idempotency_key] = order_id
            self._by_signal.setdefault(signal_id, []).append(order_id)

        logger.info("Order created: {} {} {} {} qty={:.6f} price={:.4f}",
                     order_id[:8], purpose.value, side, symbol, quantity, price)

        # Submit to exchange
        await self._submit_order(order)
        return order

    async def _submit_order(self, order: OrderRecord) -> None:
        """Submit order to exchange."""
        try:
            order.transition(OrderState.SUBMITTED.value, "Sent to exchange")
            order.attempt += 1

            exchange_order = await self._exchange.place_order(
                symbol=order.symbol,
                side=OrderSide(order.side),
                order_type=OrderType(order.order_type),
                quantity=order.quantity,
                price=order.price,
                stop_price=order.stop_price,
                time_in_force=TimeInForce(order.time_in_force),
                reduce_only=order.reduce_only,
                close_position=order.close_position,
                position_side=PositionSide(order.position_side),
                client_order_id=order.client_order_id,
            )

            # Update with exchange response
            order.exchange_order_id = exchange_order.order_id
            self._by_exchange[exchange_order.order_id] = order.order_id

            # Determine state from exchange status
            status = exchange_order.status
            if status == "NEW":
                order.transition(OrderState.ACCEPTED.value, "Accepted by exchange")
            elif status == "PARTIALLY_FILLED":
                order.executed_qty = exchange_order.executed_qty
                order.avg_price = exchange_order.avg_price
                order.transition(OrderState.PARTIALLY_FILLED.value,
                                 f"Partial fill: {exchange_order.executed_qty}/{order.quantity}")
            elif status == "FILLED":
                order.executed_qty = exchange_order.executed_qty
                order.avg_price = exchange_order.avg_price
                order.cum_quote = exchange_order.cum_quote
                order.transition(OrderState.FILLED.value, "Fully filled")
                if self._on_fill_callback:
                    await self._on_fill_callback(order)
            elif status == "CANCELED":
                order.transition(OrderState.CANCELLED.value, "Cancelled by exchange")
            elif status == "REJECTED":
                order.rejection_reason = f"Exchange rejected: {status}"
                order.transition(OrderState.REJECTED.value, order.rejection_reason)
                if self._on_reject_callback:
                    await self._on_reject_callback(order)
            elif status == "EXPIRED":
                order.transition(OrderState.EXPIRED.value, "Order expired")

            logger.info("Order submitted: {} exchange_id={} status={}",
                        order.order_id[:8], exchange_order.order_id, status)

        except RateLimitError as exc:
            order.failure_reason = f"Rate limited: {exc}"
            order.transition(OrderState.FAILED.value, order.failure_reason)
            logger.warning("Order rate limited: {} — {}", order.order_id[:8], exc)

        except ExchangeError as exc:
            err_msg = str(exc)
            if "-2021" in err_msg or "Order would immediately trigger" in err_msg:
                order.rejection_reason = err_msg
                order.transition(OrderState.REJECTED.value, err_msg)
                if self._on_reject_callback:
                    await self._on_reject_callback(order)
            else:
                order.failure_reason = err_msg
                order.transition(OrderState.FAILED.value, err_msg)
            logger.error("Order exchange error: {} — {}", order.order_id[:8], exc)

        except Exception as exc:
            order.failure_reason = str(exc)
            order.transition(OrderState.FAILED.value, str(exc))
            logger.error("Order unexpected error: {} — {}", order.order_id[:8], exc)

    # ── Order Status Sync ────────────────────────────────────────

    async def sync_order(self, order_id: str) -> Optional[OrderRecord]:
        """Sync order status from exchange."""
        order = self._orders.get(order_id)
        if not order or not order.exchange_order_id:
            return order

        try:
            exchange_order = await self._exchange.get_order(
                symbol=order.symbol,
                order_id=order.exchange_order_id,
            )

            old_state = order.state
            status = exchange_order.status

            order.executed_qty = exchange_order.executed_qty
            order.avg_price = exchange_order.avg_price
            order.cum_quote = exchange_order.cum_quote

            if status == "FILLED" and old_state != OrderState.FILLED.value:
                order.transition(OrderState.FILLED.value, "Synced: filled")
                if self._on_fill_callback:
                    await self._on_fill_callback(order)
            elif status == "CANCELED" and old_state != OrderState.CANCELLED.value:
                order.transition(OrderState.CANCELLED.value, "Synced: cancelled")
            elif status == "REJECTED" and old_state != OrderState.REJECTED.value:
                order.rejection_reason = "Synced: rejected"
                order.transition(OrderState.REJECTED.value, order.rejection_reason)
            elif status == "EXPIRED" and old_state != OrderState.EXPIRED.value:
                order.transition(OrderState.EXPIRED.value, "Synced: expired")
            elif status == "PARTIALLY_FILLED":
                order.transition(OrderState.PARTIALLY_FILLED.value,
                                 f"Synced: partial {exchange_order.executed_qty}/{order.quantity}")

            return order

        except Exception as exc:
            logger.error("Order sync failed {}: {}", order_id[:8], exc)
            return order

    async def sync_all_active(self) -> int:
        """Sync all non-terminal orders."""
        active = [
            oid for oid, o in self._orders.items()
            if o.state not in (
                OrderState.FILLED.value,
                OrderState.CANCELLED.value,
                OrderState.REJECTED.value,
                OrderState.EXPIRED.value,
                OrderState.FAILED.value,
            )
        ]

        synced = 0
        for order_id in active:
            result = await self.sync_order(order_id)
            if result:
                synced += 1
        return synced

    # ── Order Cancellation ───────────────────────────────────────

    async def cancel_order(self, order_id: str, reason: str = "") -> bool:
        """Cancel an order."""
        order = self._orders.get(order_id)
        if not order:
            return False

        if order.state in (
            OrderState.FILLED.value,
            OrderState.CANCELLED.value,
            OrderState.REJECTED.value,
            OrderState.EXPIRED.value,
            OrderState.FAILED.value,
        ):
            logger.info("Cannot cancel terminal order: {} state={}", order_id[:8], order.state)
            return False

        try:
            if order.exchange_order_id:
                await self._exchange.cancel_order(
                    symbol=order.symbol,
                    order_id=order.exchange_order_id,
                )
            order.transition(OrderState.CANCELLED.value, reason or "Cancelled by system")

            # Release idempotency key
            self._idempotency.discard(order.idempotency_key)
            self._idempotency_map.pop(order.idempotency_key, None)

            if self._on_cancel_callback:
                await self._on_cancel_callback(order)

            logger.info("Order cancelled: {} reason={}", order_id[:8], reason)
            return True

        except Exception as exc:
            logger.error("Cancel order failed {}: {}", order_id[:8], exc)
            return False

    async def cancel_signal_orders(self, signal_id: str, reason: str = "") -> int:
        """Cancel all orders for a signal."""
        order_ids = self._by_signal.get(signal_id, [])
        cancelled = 0
        for order_id in order_ids:
            if await self.cancel_order(order_id, reason):
                cancelled += 1
        return cancelled

    async def cancel_symbol_orders(self, symbol: str, reason: str = "") -> int:
        """Cancel all orders for a symbol."""
        cancelled = 0
        for order in list(self._orders.values()):
            if (order.symbol == symbol and
                order.state not in (
                    OrderState.FILLED.value,
                    OrderState.CANCELLED.value,
                    OrderState.REJECTED.value,
                    OrderState.EXPIRED.value,
                    OrderState.FAILED.value,
                )):
                if await self.cancel_order(order.order_id, reason):
                    cancelled += 1
        return cancelled

    # ── Timeout Management ───────────────────────────────────────

    async def check_timeouts(self) -> int:
        """Cancel orders that have exceeded their timeout."""
        now = time.time()
        expired = 0

        for order in list(self._orders.values()):
            if order.state not in (
                OrderState.SUBMITTED.value,
                OrderState.ACCEPTED.value,
                OrderState.PARTIALLY_FILLED.value,
            ):
                continue

            age = now - order.created_at
            if age > order.timeout_sec > 0:
                logger.warning("Order timeout: {} age={:.0f}s timeout={:.0f}s",
                               order.order_id[:8], age, order.timeout_sec)
                if await self.cancel_order(order.order_id, f"Timeout after {age:.0f}s"):
                    expired += 1

        return expired

    # ── Order Queries ────────────────────────────────────────────

    def get_order(self, order_id: str) -> Optional[OrderRecord]:
        return self._orders.get(order_id)

    def get_order_by_exchange_id(self, exchange_order_id: int) -> Optional[OrderRecord]:
        order_id = self._by_exchange.get(exchange_order_id)
        return self._orders.get(order_id) if order_id else None

    def get_signal_orders(self, signal_id: str) -> List[OrderRecord]:
        order_ids = self._by_signal.get(signal_id, [])
        return [self._orders[oid] for oid in order_ids if oid in self._orders]

    def get_active_orders(self) -> List[OrderRecord]:
        return [
            o for o in self._orders.values()
            if o.state in (
                OrderState.NEW.value,
                OrderState.SUBMITTED.value,
                OrderState.ACCEPTED.value,
                OrderState.PARTIALLY_FILLED.value,
            )
        ]

    def get_symbol_orders(self, symbol: str) -> List[OrderRecord]:
        return [o for o in self._orders.values() if o.symbol == symbol]

    def has_entry_order(self, signal_id: str) -> bool:
        """Check if signal already has an entry order."""
        for order in self.get_signal_orders(signal_id):
            if (order.purpose == OrderPurpose.ENTRY.value and
                order.state not in (
                    OrderState.CANCELLED.value,
                    OrderState.REJECTED.value,
                    OrderState.FAILED.value,
                    OrderState.EXPIRED.value,
                )):
                return True
        return False

    def has_stop_order(self, signal_id: str) -> bool:
        """Check if signal already has a stop loss order."""
        for order in self.get_signal_orders(signal_id):
            if (order.purpose == OrderPurpose.STOP_LOSS.value and
                order.state not in (
                    OrderState.CANCELLED.value,
                    OrderState.REJECTED.value,
                    OrderState.FAILED.value,
                    OrderState.EXPIRED.value,
                )):
                return True
        return False

    def has_tp_order(self, signal_id: str) -> bool:
        """Check if signal already has a take profit order."""
        for order in self.get_signal_orders(signal_id):
            if (order.purpose == OrderPurpose.TAKE_PROFIT.value and
                order.state not in (
                    OrderState.CANCELLED.value,
                    OrderState.REJECTED.value,
                    OrderState.FAILED.value,
                    OrderState.EXPIRED.value,
                )):
                return True
        return False

    # ── State Persistence ────────────────────────────────────────

    async def save_state(self) -> None:
        """Persist order state to disk."""
        try:
            state = {
                "orders": {oid: o.to_dict() for oid, o in self._orders.items()},
                "idempotency": list(self._idempotency),
                "idempotency_map": self._idempotency_map,
                "saved_at": time.time(),
            }
            tmp = str(self.STATE_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            Path(tmp).rename(self.STATE_FILE)
            logger.debug("Order state saved: {} orders", len(self._orders))
        except Exception as exc:
            logger.error("Failed to save order state: {}", exc)

    async def load_state(self) -> int:
        """Restore order state from disk."""
        if not self.STATE_FILE.exists():
            return 0

        try:
            with open(self.STATE_FILE) as f:
                state = json.load(f)

            for oid, data in state.get("orders", {}).items():
                order = OrderRecord(**data)
                self._orders[oid] = order
                if order.exchange_order_id:
                    self._by_exchange[order.exchange_order_id] = oid
                self._by_signal.setdefault(order.signal_id, []).append(oid)

            self._idempotency = set(state.get("idempotency", []))
            self._idempotency_map = state.get("idempotency_map", {})

            logger.info("Order state restored: {} orders, {} active idempotency keys",
                        len(self._orders), len(self._idempotency))
            return len(self._orders)

        except Exception as exc:
            logger.error("Failed to load order state: {}", exc)
            return 0

    # ── Statistics ───────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Get order manager statistics."""
        states = {}
        for o in self._orders.values():
            states[o.state] = states.get(o.state, 0) + 1

        return {
            "total_orders": len(self._orders),
            "active_orders": len(self.get_active_orders()),
            "states": states,
            "idempotency_keys": len(self._idempotency),
            "signal_count": len(self._by_signal),
        }
