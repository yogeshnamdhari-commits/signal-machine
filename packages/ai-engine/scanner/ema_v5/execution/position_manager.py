"""
EMA_V5 Position Manager — Tracks open positions, PnL, and lifecycle.
Isolated from existing position management.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class EMAv5Position:
    """EMA_V5 position record."""
    symbol: str = ""
    side: str = ""          # LONG / SHORT
    entry_price: float = 0.0
    quantity: float = 0.0
    leverage: int = 1
    stop_loss: float = 0.0
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    take_profit_3: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0
    status: str = "OPEN"    # OPEN, PARTIAL_CLOSED, CLOSED
    opened_at: float = 0.0
    closed_at: float = 0.0
    signal_uuid: str = ""
    regime: str = ""
    confidence: float = 0.0
    # TP tracking
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    remaining_qty: float = 0.0
    # SL tracking
    current_sl: float = 0.0
    breakeven_hit: bool = False
    # Performance
    peak_pnl: float = 0.0
    max_drawdown: float = 0.0
    hold_minutes: float = 0.0


class EMAv5PositionManager:
    """Manages EMA_V5 open positions."""

    def __init__(self, max_positions: int = 3) -> None:
        self._positions: Dict[str, EMAv5Position] = {}  # symbol → position
        self._closed: List[EMAv5Position] = []
        self._max_positions = max_positions

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        tp1: float,
        tp2: float,
        tp3: float,
        signal_uuid: str = "",
        regime: str = "",
        confidence: float = 0.0,
        leverage: int = 1,
    ) -> EMAv5Position:
        """Open a new position."""
        pos = EMAv5Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            current_sl=stop_loss,
            remaining_qty=quantity,
            status="OPEN",
            opened_at=time.time(),
            signal_uuid=signal_uuid,
            regime=regime,
            confidence=confidence,
        )
        self._positions[symbol] = pos
        logger.info("📊 EMA_V5 POSITION OPENED: {} {} @ {:.4f} qty={:.4f}",
                     side, symbol, entry_price, quantity)
        return pos

    def close_position(self, symbol: str, exit_price: float,
                       reason: str = "") -> Optional[EMAv5Position]:
        """Close a position fully."""
        pos = self._positions.pop(symbol, None)
        if not pos:
            return None

        pos.status = "CLOSED"
        pos.closed_at = time.time()
        pos.hold_minutes = (pos.closed_at - pos.opened_at) / 60

        # Compute realized PnL
        if pos.side == "LONG":
            pnl = (exit_price - pos.entry_price) * pos.remaining_qty
        else:
            pnl = (pos.entry_price - exit_price) * pos.remaining_qty

        pos.realized_pnl = pnl - pos.fees
        self._closed.append(pos)

        logger.info("📊 EMA_V5 POSITION CLOSED: {} {} PnL={:.4f} reason={}",
                     pos.side, symbol, pos.realized_pnl, reason)
        return pos

    def partial_close(self, symbol: str, quantity: float, exit_price: float,
                      reason: str = "") -> float:
        """Partial close. Returns realized PnL from this partial close."""
        pos = self._positions.get(symbol)
        if not pos:
            return 0.0

        close_qty = min(quantity, pos.remaining_qty)
        if pos.side == "LONG":
            pnl = (exit_price - pos.entry_price) * close_qty
        else:
            pnl = (pos.entry_price - exit_price) * close_qty

        pos.remaining_qty -= close_qty
        pos.realized_pnl += pnl
        pos.fees += exit_price * close_qty * 0.0004  # taker fee

        if pos.remaining_qty <= 0:
            pos.status = "CLOSED"
            pos.closed_at = time.time()
            pos.hold_minutes = (pos.closed_at - pos.opened_at) / 60
            self._positions.pop(symbol, None)
            self._closed.append(pos)

        logger.debug("📊 EMA_V5 PARTIAL CLOSE: {} {} qty={:.4f} PnL={:.4f}",
                     pos.side, symbol, close_qty, pnl)
        return pnl

    def update_price(self, symbol: str, price: float) -> Optional[EMAv5Position]:
        """Update current price and compute unrealized PnL."""
        pos = self._positions.get(symbol)
        if not pos:
            return None

        pos.current_price = price

        if pos.side == "LONG":
            pos.unrealized_pnl = (price - pos.entry_price) * pos.remaining_qty
        else:
            pos.unrealized_pnl = (pos.entry_price - price) * pos.remaining_qty

        pos.unrealized_pnl -= pos.fees

        # Track peak
        pos.peak_pnl = max(pos.peak_pnl, pos.unrealized_pnl)
        pos.max_drawdown = max(pos.max_drawdown, pos.peak_pnl - pos.unrealized_pnl)

        # Breakeven check
        risk = abs(pos.entry_price - pos.stop_loss)
        if risk > 0:
            current_r = pos.unrealized_pnl / (risk * pos.remaining_qty) if pos.remaining_qty > 0 else 0
            if current_r >= 1.0 and not pos.breakeven_hit:
                pos.current_sl = pos.entry_price
                pos.breakeven_hit = True
                logger.debug("📊 EMA_V5 BREAKEVEN: {} {}", pos.side, symbol)

        return pos

    def check_tp_exits(self, symbol: str, price: float) -> List[Dict[str, Any]]:
        """Check if any TP levels are hit. Returns list of exit actions."""
        pos = self._positions.get(symbol)
        if not pos:
            return []

        actions = []

        if pos.side == "LONG":
            if not pos.tp1_hit and price >= pos.take_profit_1:
                pos.tp1_hit = True
                actions.append({"type": "partial_close", "qty_pct": 0.35, "reason": "take_profit_1"})
            if pos.tp1_hit and not pos.tp2_hit and price >= pos.take_profit_2:
                pos.tp2_hit = True
                actions.append({"type": "partial_close", "qty_pct": 0.40, "reason": "take_profit_2"})
            if pos.tp2_hit and not pos.tp3_hit and price >= pos.take_profit_3:
                pos.tp3_hit = True
                actions.append({"type": "full_close", "reason": "take_profit_3"})
        else:
            if not pos.tp1_hit and price <= pos.take_profit_1:
                pos.tp1_hit = True
                actions.append({"type": "partial_close", "qty_pct": 0.35, "reason": "take_profit_1"})
            if pos.tp1_hit and not pos.tp2_hit and price <= pos.take_profit_2:
                pos.tp2_hit = True
                actions.append({"type": "partial_close", "qty_pct": 0.40, "reason": "take_profit_2"})
            if pos.tp2_hit and not pos.tp3_hit and price <= pos.take_profit_3:
                pos.tp3_hit = True
                actions.append({"type": "full_close", "reason": "take_profit_3"})

        return actions

    def check_sl_exit(self, symbol: str, price: float) -> bool:
        """Check if stop loss is hit."""
        pos = self._positions.get(symbol)
        if not pos:
            return False

        if pos.side == "LONG" and price <= pos.current_sl:
            return True
        if pos.side == "SHORT" and price >= pos.current_sl:
            return True
        return False

    def check_max_hold(self, symbol: str, max_hours: float = 48.0) -> bool:
        """Check if max hold time exceeded."""
        pos = self._positions.get(symbol)
        if not pos:
            return False
        hold_hours = (time.time() - pos.opened_at) / 3600
        return hold_hours >= max_hours

    def get_position(self, symbol: str) -> Optional[EMAv5Position]:
        """Get position by symbol."""
        return self._positions.get(symbol)

    def get_all_positions(self) -> List[EMAv5Position]:
        """Get all open positions."""
        return list(self._positions.values())

    def get_closed(self) -> List[EMAv5Position]:
        """Get all closed positions."""
        return list(self._closed)

    def get_position_count(self) -> int:
        """Open position count."""
        return len(self._positions)

    def can_open(self) -> bool:
        """Check if a new position can be opened."""
        return len(self._positions) < self._max_positions

    def has_position(self, symbol: str) -> bool:
        """Check if symbol has an open position."""
        return symbol in self._positions

    def get_total_unrealized(self) -> float:
        """Total unrealized PnL across all positions."""
        return sum(p.unrealized_pnl for p in self._positions.values())

    def get_total_realized(self) -> float:
        """Total realized PnL across all closed positions."""
        return sum(p.realized_pnl for p in self._closed)
