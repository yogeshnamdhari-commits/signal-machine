"""
EMA_V5 Order Manager — Handles order creation, submission, and tracking.
Isolated from existing order management. Uses exchange adapter interface.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class EMAv5Order:
    """EMA_V5 order record."""
    order_id: str = ""
    client_order_id: str = ""
    symbol: str = ""
    side: str = ""          # BUY / SELL
    order_type: str = ""    # MARKET / LIMIT / STOP_MARKET
    quantity: float = 0.0
    price: float = 0.0
    stop_price: float = 0.0
    status: str = "PENDING"  # PENDING, SUBMITTED, FILLED, PARTIALLY_FILLED, CANCELED, REJECTED
    filled_qty: float = 0.0
    avg_price: float = 0.0
    reduce_only: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    signal_uuid: str = ""
    reason: str = ""        # entry, stop_loss, take_profit_1, take_profit_2, take_profit_3, max_hold
    metadata: Dict[str, Any] = field(default_factory=dict)


class EMAv5OrderManager:
    """Manages EMA_V5 order lifecycle."""

    def __init__(self) -> None:
        self._orders: Dict[str, EMAv5Order] = {}  # order_id → order
        self._pending: List[str] = []  # order_ids pending fill
        self._exchange = None  # Set via set_exchange()

    def set_exchange(self, exchange: Any) -> None:
        """Set the exchange adapter for order submission."""
        self._exchange = exchange

    def create_entry_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        signal_uuid: str = "",
        order_type: str = "LIMIT",
    ) -> EMAv5Order:
        """Create an entry order."""
        client_id = f"ev5_{uuid.uuid4().hex[:12]}"
        order = EMAv5Order(
            order_id="",
            client_order_id=client_id,
            symbol=symbol,
            side="BUY" if side == "LONG" else "SELL",
            order_type=order_type,
            quantity=quantity,
            price=price,
            status="PENDING",
            created_at=time.time(),
            updated_at=time.time(),
            signal_uuid=signal_uuid,
            reason="entry",
        )
        self._orders[client_id] = order
        logger.info("📊 EMA_V5 ORDER CREATED: {} {} {} @ {:.4f} qty={:.4f}",
                     order_type, order.side, symbol, price, quantity)
        return order

    def create_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        signal_uuid: str = "",
        reason: str = "stop_loss",
    ) -> EMAv5Order:
        """Create a stop market order."""
        client_id = f"ev5_{uuid.uuid4().hex[:12]}"
        order = EMAv5Order(
            order_id="",
            client_order_id=client_id,
            symbol=symbol,
            side="SELL" if side == "LONG" else "BUY",
            order_type="STOP_MARKET",
            quantity=quantity,
            stop_price=stop_price,
            status="PENDING",
            created_at=time.time(),
            updated_at=time.time(),
            signal_uuid=signal_uuid,
            reason=reason,
            reduce_only=True,
        )
        self._orders[client_id] = order
        logger.info("📊 EMA_V5 STOP ORDER: {} {} @ {:.4f} qty={:.4f}",
                     order.side, symbol, stop_price, quantity)
        return order

    def create_tp_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        signal_uuid: str = "",
        reason: str = "take_profit_1",
    ) -> EMAv5Order:
        """Create a take-profit limit order."""
        client_id = f"ev5_{uuid.uuid4().hex[:12]}"
        order = EMAv5Order(
            order_id="",
            client_order_id=client_id,
            symbol=symbol,
            side="SELL" if side == "LONG" else "BUY",
            order_type="LIMIT",
            quantity=quantity,
            price=price,
            status="PENDING",
            created_at=time.time(),
            updated_at=time.time(),
            signal_uuid=signal_uuid,
            reason=reason,
            reduce_only=True,
        )
        self._orders[client_id] = order
        logger.info("📊 EMA_V5 TP ORDER: {} {} @ {:.4f} qty={:.4f} ({})",
                     order.side, symbol, price, quantity, reason)
        return order

    async def submit_order(self, order: EMAv5Order) -> bool:
        """Submit order to exchange."""
        if not self._exchange:
            logger.warning("EMAv5: No exchange set, order stays PENDING")
            return False

        try:
            result = await self._exchange.place_order(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                price=order.price,
                stop_price=order.stop_price,
                reduce_only=order.reduce_only,
                client_order_id=order.client_order_id,
            )
            order.order_id = result.get("order_id", "")
            order.status = "SUBMITTED"
            order.updated_at = time.time()
            self._pending.append(order.client_order_id)
            logger.info("📊 EMA_V5 ORDER SUBMITTED: {} id={}", order.symbol, order.order_id)
            return True
        except Exception as e:
            order.status = "REJECTED"
            order.updated_at = time.time()
            logger.error("EMAv5 order submit failed: {}", e)
            return False

    def update_fill(self, client_order_id: str, filled_qty: float,
                    avg_price: float, status: str = "FILLED") -> Optional[EMAv5Order]:
        """Update order fill status."""
        order = self._orders.get(client_order_id)
        if not order:
            return None
        order.filled_qty = filled_qty
        order.avg_price = avg_price
        order.status = status
        order.updated_at = time.time()
        if status in ("FILLED", "CANCELED", "REJECTED"):
            if client_order_id in self._pending:
                self._pending.remove(client_order_id)
        return order

    def cancel_order(self, client_order_id: str) -> bool:
        """Cancel an order."""
        order = self._orders.get(client_order_id)
        if not order:
            return False
        order.status = "CANCELED"
        order.updated_at = time.time()
        if client_order_id in self._pending:
            self._pending.remove(client_order_id)
        logger.info("📊 EMA_V5 ORDER CANCELED: {} {}", order.symbol, order.reason)
        return True

    def cancel_all(self, symbol: Optional[str] = None) -> int:
        """Cancel all pending orders, optionally filtered by symbol."""
        count = 0
        for cid in list(self._pending):
            order = self._orders.get(cid)
            if order and (symbol is None or order.symbol == symbol):
                self.cancel_order(cid)
                count += 1
        return count

    def get_order(self, client_order_id: str) -> Optional[EMAv5Order]:
        """Get order by client ID."""
        return self._orders.get(client_order_id)

    def get_pending(self, symbol: Optional[str] = None) -> List[EMAv5Order]:
        """Get all pending orders."""
        return [
            self._orders[cid] for cid in self._pending
            if cid in self._orders and (symbol is None or self._orders[cid].symbol == symbol)
        ]

    def get_all_orders(self) -> List[EMAv5Order]:
        """Get all orders."""
        return list(self._orders.values())

    def get_order_count(self) -> int:
        """Total order count."""
        return len(self._orders)

    def get_pending_count(self) -> int:
        """Pending order count."""
        return len(self._pending)
