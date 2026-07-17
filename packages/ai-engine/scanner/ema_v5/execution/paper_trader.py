"""
EMA_V5 Paper Trader — Simulated execution for testing without real orders.
Isolated from existing paper trading.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from .order_manager import EMAv5OrderManager, EMAv5Order
from .position_manager import EMAv5PositionManager, EMAv5Position
from .risk_manager import EMAv5RiskManager, EMAv5RiskConfig


@dataclass
class EMAv5PaperConfig:
    """Paper trading configuration."""
    initial_balance: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    max_positions: int = 3
    max_daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 15.0
    leverage: int = 5
    taker_fee: float = 0.0004
    slippage_pct: float = 0.01  # 0.01% slippage


class EMAv5PaperTrader:
    """Simulated EMA_V5 execution for paper trading."""

    def __init__(self, config: Optional[EMAv5PaperConfig] = None) -> None:
        self.config = config or EMAv5PaperConfig()
        self._order_mgr = EMAv5OrderManager()
        self._pos_mgr = EMAv5PositionManager(max_positions=self.config.max_positions)
        self._risk_mgr = EMAv5RiskManager(EMAv5RiskConfig(
            account_balance=self.config.initial_balance,
            risk_per_trade_pct=self.config.risk_per_trade_pct,
            max_positions=self.config.max_positions,
            max_daily_loss_pct=self.config.max_daily_loss_pct,
            max_drawdown_pct=self.config.max_drawdown_pct,
            max_leverage=self.config.leverage,
        ))
        self._balance = self.config.initial_balance
        self._trade_log: List[Dict] = []
        logger.info("📊 EMA_V5 Paper Trader initialized: balance=${:.2f}", self._balance)

    def process_signal(self, signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a signal and execute paper trade.
        
        Returns execution result or None if rejected.
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        entry = signal.get("entry", 0)
        sl = signal.get("sl", 0)
        tp1 = signal.get("take_profit_1", 0)
        tp2 = signal.get("take_profit_2", 0)
        tp3 = signal.get("take_profit_3", 0)
        confidence = signal.get("confidence", 0)
        regime = signal.get("regime", "")

        if not all([symbol, side, entry > 0, sl > 0]):
            return None

        # Risk check
        can_open, reason = self._risk_mgr.can_open_trade(
            entry, sl, self._pos_mgr.get_position_count()
        )
        if not can_open:
            logger.info("📊 EMA_V5 PAPER: {} {} REJECTED: {}", side, symbol, reason)
            return {"status": "rejected", "reason": reason}

        # Position size
        qty = self._risk_mgr.compute_position_size(entry, sl, self.config.leverage)
        if qty <= 0:
            return {"status": "rejected", "reason": "zero_qty"}

        # Apply slippage
        slippage = entry * self.config.slippage_pct / 100
        fill_price = entry + slippage if side == "LONG" else entry - slippage

        # Fees
        fee = fill_price * qty * self.config.taker_fee

        # Open position
        pos = self._pos_mgr.open_position(
            symbol=symbol, side=side, entry_price=fill_price, quantity=qty,
            stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
            signal_uuid=signal.get("uuid", str(uuid.uuid4())),
            regime=regime, confidence=confidence, leverage=self.config.leverage,
        )
        pos.fees = fee

        # Create SL order
        self._order_mgr.create_stop_order(symbol, side, qty, sl, reason="stop_loss")

        # Create TP orders
        self._order_mgr.create_tp_order(symbol, side, qty * 0.35, tp1, reason="take_profit_1")
        self._order_mgr.create_tp_order(symbol, side, qty * 0.40, tp2, reason="take_profit_2")
        self._order_mgr.create_tp_order(symbol, side, qty * 0.25, tp3, reason="take_profit_3")

        result = {
            "status": "filled",
            "symbol": symbol,
            "side": side,
            "entry_price": fill_price,
            "quantity": qty,
            "stop_loss": sl,
            "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "fee": fee,
            "slippage": slippage,
        }

        self._trade_log.append({"type": "entry", "time": time.time(), **result})
        logger.info("📊 EMA_V5 PAPER ENTRY: {} {} @ {:.4f} qty={:.4f}",
                     side, symbol, fill_price, qty)
        return result

    def on_price_update(self, symbol: str, price: float) -> List[Dict[str, Any]]:
        """Process price update — check SL, TP, max hold.
        
        Returns list of exit actions taken.
        """
        actions = []
        pos = self._pos_mgr.get_position(symbol)
        if not pos:
            return actions

        # Update price
        self._pos_mgr.update_price(symbol, price)

        # Check stop loss
        if self._pos_mgr.check_sl_exit(symbol, price):
            result = self._close_position(symbol, price, "stop_loss")
            if result:
                actions.append(result)
            return actions

        # Check TP exits
        tp_actions = self._pos_mgr.check_tp_exits(symbol, price)
        for action in tp_actions:
            if action["type"] == "partial_close":
                qty_pct = action.get("qty_pct", 0.35)
                close_qty = pos.quantity * qty_pct
                pnl = self._pos_mgr.partial_close(symbol, close_qty, price, action["reason"])
                self._risk_mgr.record_trade_pnl(pnl)
                self._trade_log.append({
                    "type": "partial_close", "time": time.time(),
                    "symbol": symbol, "reason": action["reason"], "pnl": pnl,
                })
                actions.append({"action": "partial_close", "reason": action["reason"], "pnl": pnl})
            elif action["type"] == "full_close":
                result = self._close_position(symbol, price, action["reason"])
                if result:
                    actions.append(result)

        # Check max hold
        if self._pos_mgr.check_max_hold(symbol, 48.0):
            result = self._close_position(symbol, price, "max_hold")
            if result:
                actions.append(result)

        return actions

    def _close_position(self, symbol: str, price: float, reason: str) -> Optional[Dict[str, Any]]:
        """Close a position."""
        pos = self._pos_mgr.close_position(symbol, price, reason)
        if not pos:
            return None

        # Update balance
        self._balance += pos.realized_pnl
        self._risk_mgr.update_balance(self._balance)
        self._risk_mgr.record_trade_pnl(pos.realized_pnl)

        result = {
            "action": "close",
            "symbol": symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": price,
            "quantity": pos.remaining_qty,
            "pnl": pos.realized_pnl,
            "hold_minutes": pos.hold_minutes,
            "reason": reason,
        }

        self._trade_log.append({"type": "exit", "time": time.time(), **result})
        logger.info("📊 EMA_V5 PAPER EXIT: {} {} PnL={:.4f} reason={}",
                     pos.side, symbol, pos.realized_pnl, reason)
        return result

    def get_status(self) -> Dict[str, Any]:
        """Get paper trading status."""
        return {
            "balance": round(self._balance, 2),
            "initial_balance": self.config.initial_balance,
            "total_return_pct": round((self._balance - self.config.initial_balance) / self.config.initial_balance * 100, 2),
            "open_positions": self._pos_mgr.get_position_count(),
            "total_realized": round(self._pos_mgr.get_total_realized(), 2),
            "total_unrealized": round(self._pos_mgr.get_total_unrealized(), 2),
            "total_trades": len(self._trade_log),
            "risk": self._risk_mgr.get_status(),
        }

    def get_positions(self) -> List[Dict]:
        """Get all open positions."""
        return [
            {
                "symbol": p.symbol, "side": p.side, "entry": p.entry_price,
                "current": p.current_price, "pnl": round(p.unrealized_pnl, 2),
                "qty": p.remaining_qty, "hold_min": round(p.hold_minutes, 1),
            }
            for p in self._pos_mgr.get_all_positions()
        ]

    def get_trade_log(self) -> List[Dict]:
        """Get full trade log."""
        return list(self._trade_log)

    def reset(self) -> None:
        """Reset paper trader to initial state."""
        self._balance = self.config.initial_balance
        self._order_mgr = EMAv5OrderManager()
        self._pos_mgr = EMAv5PositionManager(max_positions=self.config.max_positions)
        self._risk_mgr = EMAv5RiskManager(EMAv5RiskConfig(
            account_balance=self.config.initial_balance,
            risk_per_trade_pct=self.config.risk_per_trade_pct,
        ))
        self._trade_log = []
        logger.info("📊 EMA_V5 Paper Trader reset")
