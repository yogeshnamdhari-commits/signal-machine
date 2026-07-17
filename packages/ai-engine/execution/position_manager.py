"""
Position Manager — Position lifecycle, tracking, P&L, state persistence.

Responsible for:
- Position creation from filled orders
- Real-time P&L calculation (unrealized + realized)
- Stop loss / take profit management
- Position aging and timeout
- Margin and leverage tracking
- Position risk calculation
- State persistence for crash recovery
- Full position history
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

import sys
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from execution.exchange_adapter import (
    ExchangeAdapter, OrderType, OrderSide, PositionSide,
    TimeInForce, ExchangePosition,
)


class PositionStatus(str, Enum):
    """Position lifecycle states."""
    OPENING = "OPENING"         # Entry order placed, not yet filled
    OPEN = "OPEN"               # Fully entered
    CLOSING = "CLOSING"         # Exit order placed
    CLOSED = "CLOSED"           # Fully exited
    STOPPED_OUT = "STOPPED_OUT"
    TP_HIT = "TP_HIT"
    LIQUIDATED = "LIQUIDATED"


@dataclass
class Position:
    """Complete position record with full lifecycle tracking."""
    # Identity
    position_id: str = ""
    signal_id: str = ""
    entry_order_id: str = ""
    stop_order_id: str = ""
    tp_order_id: str = ""

    # Position details
    symbol: str = ""
    side: str = ""                  # LONG / SHORT
    entry_price: float = 0.0       # Average fill price
    current_price: float = 0.0
    quantity: float = 0.0
    leverage: int = 10
    margin: float = 0.0

    # Risk parameters
    stop_loss: float = 0.0
    take_profit: float = 0.0
    # Multi-target system
    take_profit_1: float = 0.0
    take_profit_2: float = 0.0
    take_profit_3: float = 0.0
    current_tp_index: int = 1
    tp1_exit_pct: float = 0.40
    tp2_exit_pct: float = 0.35
    tp3_exit_pct: float = 0.25
    trailing_stop_pct: float = 0.0
    risk_pct: float = 0.0          # % of account at risk

    # P&L
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    max_unrealized: float = 0.0    # Peak unrealized profit
    max_drawdown: float = 0.0      # Max adverse excursion

    # Signal metadata
    confidence: float = 0.0
    institutional_score: float = 0.0
    market_regime: str = ""
    risk_reward: float = 0.0

    # Status
    status: str = PositionStatus.OPEN.value
    close_reason: str = ""
    close_price: float = 0.0

    # Timestamps
    opened_at: float = 0.0
    closed_at: float = 0.0
    last_update: float = 0.0
    last_sl_check: float = 0.0

    def __post_init__(self):
        if not self.position_id:
            self.position_id = str(uuid.uuid4())
        if not self.opened_at:
            self.opened_at = time.time()
        if not self.last_update:
            self.last_update = self.opened_at

    def to_dict(self) -> Dict:
        return asdict(self)

    @property
    def age_sec(self) -> float:
        end = self.closed_at if self.closed_at else time.time()
        return end - self.opened_at

    @property
    def age_str(self) -> str:
        sec = self.age_sec
        if sec < 60:
            return f"{sec:.0f}s"
        elif sec < 3600:
            return f"{sec / 60:.1f}m"
        else:
            return f"{sec / 3600:.1f}h"

    @property
    def is_open(self) -> bool:
        return self.status in (
            PositionStatus.OPENING.value,
            PositionStatus.OPEN.value,
            PositionStatus.CLOSING.value,
        )


class PositionManager:
    """
    Manages position lifecycle with real-time P&L, risk tracking, and persistence.

    Key guarantees:
    - One signal → One position (enforced)
    - No duplicate positions per symbol
    - Real-time P&L with fees
    - Stop loss / take profit monitoring
    - Position reconciliation with exchange
    - Crash recovery from persistent state
    """

    STATE_FILE = _ai_root / "data" / "execution" / "position_state.json"
    MAX_POSITION_AGE_SEC = 86400 * 7  # 7 days max

    def __init__(self, exchange: ExchangeAdapter) -> None:
        self._exchange = exchange
        self._positions: Dict[str, Position] = {}      # position_id → Position
        self._by_signal: Dict[str, str] = {}            # signal_id → position_id
        self._by_symbol: Dict[str, List[str]] = {}      # symbol → [position_ids]
        self._closed_positions: List[Position] = []     # Historical closed positions
        self._lock = asyncio.Lock()
        self._on_close_callback: Optional[Callable] = None
        self._price_cache: Dict[str, float] = {}        # symbol → last price
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    def set_callbacks(self, on_close: Optional[Callable] = None) -> None:
        self._on_close_callback = on_close

    # ── Position Creation ────────────────────────────────────────

    async def open_position(
        self,
        signal_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        leverage: int = 10,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        confidence: float = 0.0,
        institutional_score: float = 0.0,
        market_regime: str = "",
        risk_reward: float = 0.0,
        entry_order_id: str = "",
        fees: float = 0.0,
    ) -> Optional[Position]:
        """
        Create a new position from a filled entry order.

        Enforces:
        - No duplicate positions per signal
        - No duplicate positions per symbol (unless configured otherwise)
        """
        async with self._lock:
            # Check for duplicate signal
            if signal_id in self._by_signal:
                existing_id = self._by_signal[signal_id]
                existing = self._positions.get(existing_id)
                if existing and existing.is_open:
                    logger.warning("Duplicate position blocked: signal={} existing={}",
                                   signal_id[:8], existing_id[:8])
                    return None

            # Check for existing open position on same symbol (same side)
            existing_on_symbol = self._by_symbol.get(symbol, [])
            for pid in existing_on_symbol:
                pos = self._positions.get(pid)
                if pos and pos.is_open and pos.side == side:
                    logger.warning("Position already exists: {} {} existing={}",
                                   symbol, side, pid[:8])
                    return None

            # Calculate margin
            position_value = entry_price * quantity
            margin = position_value / leverage if leverage > 0 else position_value

            # Calculate risk percentage
            balance = 10000.0  # Will be overridden by engine
            risk_pct = 0.0
            if stop_loss > 0 and entry_price > 0:
                risk_distance = abs(entry_price - stop_loss)
                risk_amount = risk_distance * quantity
                risk_pct = (risk_amount / balance) * 100 if balance > 0 else 0

            # Create position
            position = Position(
                signal_id=signal_id,
                entry_order_id=entry_order_id,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                current_price=entry_price,
                quantity=quantity,
                leverage=leverage,
                margin=round(margin, 2),
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                institutional_score=institutional_score,
                market_regime=market_regime,
                risk_reward=risk_reward,
                risk_pct=round(risk_pct, 2),
                fees=fees,
                status=PositionStatus.OPEN.value,
            )

            # Register
            self._positions[position.position_id] = position
            self._by_signal[signal_id] = position.position_id
            self._by_symbol.setdefault(symbol, []).append(position.position_id)

            logger.info("Position opened: {} {} {} qty={:.6f} entry={:.4f} SL={:.4f} TP={:.4f}",
                        position.position_id[:8], side, symbol,
                        quantity, entry_price, stop_loss, take_profit)

            return position

    # ── Position Updates ─────────────────────────────────────────

    async def update_price(self, symbol: str, price: float) -> List[Tuple[str, str]]:
        """
        Update current price and calculate P&L for all positions on a symbol.
        Returns list of (position_id, exit_reason) for positions that hit SL/TP.
        """
        self._price_cache[symbol] = price
        exits: List[Tuple[str, str]] = []

        position_ids = self._by_symbol.get(symbol, [])
        for pid in position_ids:
            pos = self._positions.get(pid)
            if not pos or not pos.is_open:
                continue

            pos.current_price = price
            pos.last_update = time.time()

            # Calculate unrealized P&L
            if pos.side == "LONG":
                pos.unrealized_pnl = (price - pos.entry_price) * pos.quantity * pos.leverage
            else:
                pos.unrealized_pnl = (pos.entry_price - price) * pos.quantity * pos.leverage

            pos.net_pnl = pos.unrealized_pnl - pos.fees
            pos.return_pct = (pos.net_pnl / pos.margin * 100) if pos.margin > 0 else 0

            # Track extremes
            pos.max_unrealized = max(pos.max_unrealized, pos.unrealized_pnl)
            if pos.unrealized_pnl < 0:
                pos.max_drawdown = min(pos.max_drawdown, pos.unrealized_pnl)

            # Check stop loss
            if pos.stop_loss > 0:
                if pos.side == "LONG" and price <= pos.stop_loss:
                    exits.append((pid, "stop_loss"))
                elif pos.side == "SHORT" and price >= pos.stop_loss:
                    exits.append((pid, "stop_loss"))

            # Check multi-target TPs sequentially
            tp1 = pos.take_profit_1 if pos.take_profit_1 > 0 else pos.take_profit
            tp2 = pos.take_profit_2
            tp3 = pos.take_profit_3

            if pos.current_tp_index == 1 and tp1 > 0:
                if (pos.side == "LONG" and price >= tp1) or (pos.side == "SHORT" and price <= tp1):
                    pos.current_tp_index = 2
                    exits.append((pid, "take_profit_1"))
            elif pos.current_tp_index == 2 and tp2 > 0:
                if (pos.side == "LONG" and price >= tp2) or (pos.side == "SHORT" and price <= tp2):
                    pos.current_tp_index = 3
                    exits.append((pid, "take_profit_2"))
            elif pos.current_tp_index == 3 and tp3 > 0:
                if (pos.side == "LONG" and price >= tp3) or (pos.side == "SHORT" and price <= tp3):
                    exits.append((pid, "take_profit_3"))
            elif pos.take_profit > 0 and tp1 == 0:
                # Legacy single TP fallback
                if (pos.side == "LONG" and price >= pos.take_profit) or (pos.side == "SHORT" and price <= pos.take_profit):
                    exits.append((pid, "take_profit"))

        return exits

    async def close_position(
        self,
        position_id: str,
        close_price: float,
        reason: str = "manual",
        fees: float = 0.0,
    ) -> Optional[Position]:
        """Close a position and calculate final P&L."""
        pos = self._positions.get(position_id)
        if not pos or not pos.is_open:
            return None

        pos.close_price = close_price
        pos.close_reason = reason
        pos.closed_at = time.time()
        pos.fees += fees

        # Final P&L
        if pos.side == "LONG":
            pos.realized_pnl = (close_price - pos.entry_price) * pos.quantity * pos.leverage
        else:
            pos.realized_pnl = (pos.entry_price - close_price) * pos.quantity * pos.leverage

        pos.net_pnl = pos.realized_pnl - pos.fees
        pos.return_pct = (pos.net_pnl / pos.margin * 100) if pos.margin > 0 else 0
        pos.unrealized_pnl = 0.0

        # Set status based on reason
        if reason == "stop_loss":
            pos.status = PositionStatus.STOPPED_OUT.value
        elif reason == "take_profit":
            pos.status = PositionStatus.TP_HIT.value
        elif reason == "liquidation":
            pos.status = PositionStatus.LIQUIDATED.value
        else:
            pos.status = PositionStatus.CLOSED.value

        # Move to history
        self._closed_positions.append(pos)

        # Keep only recent closed positions in memory
        if len(self._closed_positions) > 1000:
            self._closed_positions = self._closed_positions[-500:]

        logger.info("Position closed: {} {} {} reason={} PnL={:.2f} return={:.2f}%",
                    position_id[:8], pos.side, pos.symbol,
                    reason, pos.net_pnl, pos.return_pct)

        if self._on_close_callback:
            await self._on_close_callback(pos)

        return pos

    async def close_symbol_positions(self, symbol: str, reason: str = "manual") -> int:
        """Close all positions for a symbol."""
        closed = 0
        price = self._price_cache.get(symbol, 0)
        for pid in list(self._by_symbol.get(symbol, [])):
            pos = self._positions.get(pid)
            if pos and pos.is_open:
                close_price = price or pos.current_price
                if close_price > 0:
                    await self.close_position(pid, close_price, reason)
                    closed += 1
        return closed

    # ── Position Queries ─────────────────────────────────────────

    def get_position(self, position_id: str) -> Optional[Position]:
        return self._positions.get(position_id)

    def get_open_positions(self) -> List[Position]:
        return [p for p in self._positions.values() if p.is_open]

    def get_symbol_positions(self, symbol: str) -> List[Position]:
        pids = self._by_symbol.get(symbol, [])
        return [self._positions[pid] for pid in pids if pid in self._positions and self._positions[pid].is_open]

    def get_signal_position(self, signal_id: str) -> Optional[Position]:
        pid = self._by_signal.get(signal_id)
        return self._positions.get(pid) if pid else None

    def has_open_position(self, signal_id: str) -> bool:
        pos = self.get_signal_position(signal_id)
        return pos is not None and pos.is_open

    def has_symbol_position(self, symbol: str) -> bool:
        return any(p.is_open for p in self.get_symbol_positions(symbol))

    def get_closed_positions(self, limit: int = 100) -> List[Position]:
        return self._closed_positions[-limit:]

    def get_all_positions(self) -> List[Position]:
        """Get all positions (open + closed)."""
        open_pos = list(self._positions.values())
        return open_pos + self._closed_positions

    # ── Aggregate Metrics ────────────────────────────────────────

    def get_portfolio_metrics(self) -> Dict:
        """Get current portfolio metrics."""
        open_pos = self.get_open_positions()
        total_unrealized = sum(p.unrealized_pnl for p in open_pos)
        total_margin = sum(p.margin for p in open_pos)
        total_exposure = sum(p.entry_price * p.quantity for p in open_pos)
        total_risk = sum(
            abs(p.entry_price - p.stop_loss) * p.quantity
            for p in open_pos if p.stop_loss > 0
        )

        # Win/loss from closed positions
        wins = [p for p in self._closed_positions if p.net_pnl > 0]
        losses = [p for p in self._closed_positions if p.net_pnl <= 0]
        total_closed = len(self._closed_positions)

        return {
            "open_positions": len(open_pos),
            "total_unrealized_pnl": round(total_unrealized, 2),
            "total_margin": round(total_margin, 2),
            "total_exposure": round(total_exposure, 2),
            "total_risk_amount": round(total_risk, 2),
            "closed_trades": total_closed,
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": len(wins) / total_closed if total_closed > 0 else 0,
            "total_realized_pnl": round(sum(p.net_pnl for p in self._closed_positions), 2),
            "avg_trade_pnl": round(
                sum(p.net_pnl for p in self._closed_positions) / total_closed, 2
            ) if total_closed > 0 else 0,
        }

    # ── State Persistence ────────────────────────────────────────

    async def save_state(self) -> None:
        """Persist position state to disk."""
        try:
            state = {
                "positions": {pid: p.to_dict() for pid, p in self._positions.items()},
                "closed": [p.to_dict() for p in self._closed_positions[-200:]],
                "by_signal": self._by_signal,
                "saved_at": time.time(),
            }
            tmp = str(self.STATE_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            Path(tmp).rename(self.STATE_FILE)
            logger.debug("Position state saved: {} open, {} closed",
                        len([p for p in self._positions.values() if p.is_open]),
                        len(self._closed_positions))
        except Exception as exc:
            logger.error("Failed to save position state: {}", exc)

    async def load_state(self) -> int:
        """Restore position state from disk."""
        if not self.STATE_FILE.exists():
            return 0

        try:
            with open(self.STATE_FILE) as f:
                state = json.load(f)

            for pid, data in state.get("positions", {}).items():
                pos = Position(**data)
                self._positions[pid] = pos
                self._by_signal[pos.signal_id] = pid
                self._by_symbol.setdefault(pos.symbol, []).append(pid)

            for data in state.get("closed", []):
                pos = Position(**data)
                self._closed_positions.append(pos)

            self._by_signal.update(state.get("by_signal", {}))

            open_count = len([p for p in self._positions.values() if p.is_open])
            logger.info("Position state restored: {} open, {} closed",
                        open_count, len(self._closed_positions))
            return open_count

        except Exception as exc:
            logger.error("Failed to load position state: {}", exc)
            return 0

    # ── Reconciliation Support ───────────────────────────────────

    def get_expected_positions(self) -> Dict[str, Dict]:
        """Get all positions that should exist on exchange."""
        result = {}
        for pos in self._positions.values():
            if pos.is_open:
                result[pos.symbol] = {
                    "position_id": pos.position_id,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                }
        return result

    def reconcile_with_exchange(self, exchange_positions: List[ExchangePosition]) -> List[Dict]:
        """Compare internal state with exchange positions."""
        mismatches = []

        # Build exchange lookup
        exch_map: Dict[str, ExchangePosition] = {}
        for ep in exchange_positions:
            exch_map[ep.symbol] = ep

        # Check internal positions against exchange
        for pos in self._positions.values():
            if not pos.is_open:
                continue

            ep = exch_map.get(pos.symbol)
            if not ep:
                mismatches.append({
                    "type": "missing_on_exchange",
                    "position_id": pos.position_id,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "quantity": pos.quantity,
                })
            elif abs(ep.quantity - pos.quantity) > 0.0001:
                mismatches.append({
                    "type": "quantity_mismatch",
                    "position_id": pos.position_id,
                    "symbol": pos.symbol,
                    "internal_qty": pos.quantity,
                    "exchange_qty": ep.quantity,
                })

        # Check exchange positions not in internal state
        for ep in exchange_positions:
            if ep.symbol not in {p.symbol for p in self._positions.values() if p.is_open}:
                mismatches.append({
                    "type": "orphan_on_exchange",
                    "symbol": ep.symbol,
                    "side": ep.side,
                    "quantity": ep.quantity,
                })

        return mismatches

    # ── Statistics ───────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Get position manager statistics."""
        open_positions = self.get_open_positions()
        return {
            "open_count": len(open_positions),
            "closed_count": len(self._closed_positions),
            "total_positions": len(self._positions),
            "symbols_traded": len(self._by_symbol),
            "total_exposure": sum(p.entry_price * p.quantity for p in open_positions),
            "total_margin": sum(p.margin for p in open_positions),
            "total_unrealized_pnl": sum(p.unrealized_pnl for p in open_positions),
        }
