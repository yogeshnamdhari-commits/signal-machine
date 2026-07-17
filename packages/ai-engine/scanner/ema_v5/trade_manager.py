"""
EMA_V5 Trade Manager — Trade lifecycle management.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from loguru import logger

from .config import ema_v5_config


class TradeManager:
    """Manages EMA_V5 trade lifecycle (open, monitor, close)."""

    def __init__(self) -> None:
        self._open_trades: Dict[str, Dict] = {}  # symbol → trade

    def open_trade(self, signal: Dict) -> Dict:
        """Record a new trade from signal."""
        sym = signal.get("symbol", "")
        trade = {
            "symbol": sym,
            "side": signal.get("side", ""),
            "entry": signal.get("entry", 0),
            "sl": signal.get("sl", 0),
            "tp1": signal.get("take_profit_1", 0),
            "tp2": signal.get("take_profit_2", 0),
            "tp3": signal.get("take_profit_3", 0),
            "tp_idx": 1,
            "opened_at": time.time(),
            "confidence": signal.get("confidence", 0),
            "regime": signal.get("regime", ""),
            "status": "active",
        }
        self._open_trades[sym] = trade
        logger.info("📈 EMA_V5 TRADE OPENED: {} {} @ {}", signal.get("side"), sym, signal.get("entry"))
        return trade

    def check_exit(self, symbol: str, price: float) -> Optional[Dict]:
        """Check if trade should be closed.

        Returns close dict or None.
        """
        trade = self._open_trades.get(symbol)
        if not trade:
            return None

        side = trade["side"]
        entry = trade["entry"]
        sl = trade["sl"]
        hold_hours = (time.time() - trade["opened_at"]) / 3600

        # Max hold check
        if hold_hours >= ema_v5_config.trade.max_hold_hours:
            return {"symbol": symbol, "reason": "max_hold", "price": price}

        # SL check
        if side == "LONG" and price <= sl:
            return {"symbol": symbol, "reason": "stop_loss", "price": price}
        if side == "SHORT" and price >= sl:
            return {"symbol": symbol, "reason": "stop_loss", "price": price}

        return None

    def close_trade(self, symbol: str) -> Optional[Dict]:
        """Close trade and return record."""
        return self._open_trades.pop(symbol, None)

    @property
    def open_count(self) -> int:
        return len(self._open_trades)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._open_trades
