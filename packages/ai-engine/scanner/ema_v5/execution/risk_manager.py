"""
EMA_V5 Risk Manager — Position sizing, drawdown limits, and risk controls.
Isolated from existing risk management.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class EMAv5RiskConfig:
    """Risk management configuration."""
    account_balance: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    max_positions: int = 3
    max_daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 15.0
    max_leverage: int = 5
    min_sl_distance_pct: float = 0.5
    max_sl_distance_pct: float = 5.0
    cooldown_after_loss_sec: int = 300  # 5 min


class EMAv5RiskManager:
    """Risk management for EMA_V5 positions."""

    def __init__(self, config: Optional[EMAv5RiskConfig] = None) -> None:
        self.config = config or EMAv5RiskConfig()
        self._daily_pnl = 0.0
        self._daily_reset_ts = 0.0
        self._peak_balance = self.config.account_balance
        self._current_balance = self.config.account_balance
        self._last_loss_ts = 0.0
        self._consecutive_losses = 0

    def compute_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        leverage: int = 1,
    ) -> float:
        """Compute position size based on risk per trade.
        
        Returns quantity to trade.
        """
        risk_amount = self._current_balance * (self.config.risk_per_trade_pct / 100)
        sl_distance = abs(entry_price - stop_loss)

        if sl_distance <= 0:
            logger.warning("EMAv5 risk: SL distance is zero")
            return 0.0

        # Quantity = risk_amount / sl_distance
        qty = risk_amount / sl_distance

        # Apply leverage limit
        notional = qty * entry_price
        max_notional = self._current_balance * self.config.max_leverage
        if notional > max_notional:
            qty = max_notional / entry_price
            logger.debug("EMAv5 risk: capped qty to leverage limit")

        return round(qty, 6)

    def can_open_trade(
        self,
        entry_price: float,
        stop_loss: float,
        open_position_count: int,
    ) -> tuple[bool, str]:
        """Check if a new trade can be opened.
        
        Returns (allowed, reason).
        """
        # Max positions check
        if open_position_count >= self.config.max_positions:
            return False, "max_positions"

        # Daily loss check
        self._check_daily_reset()
        daily_loss_pct = abs(self._daily_pnl) / self._current_balance * 100 if self._daily_pnl < 0 else 0
        if daily_loss_pct >= self.config.max_daily_loss_pct:
            return False, "daily_loss_limit"

        # Max drawdown check
        drawdown = (self._peak_balance - self._current_balance) / self._peak_balance * 100
        if drawdown >= self.config.max_drawdown_pct:
            return False, "max_drawdown"

        # SL distance check
        sl_distance_pct = abs(entry_price - stop_loss) / entry_price * 100
        if sl_distance_pct < self.config.min_sl_distance_pct:
            return False, "sl_too_tight"
        if sl_distance_pct > self.config.max_sl_distance_pct:
            return False, "sl_too_wide"

        # Cooldown after loss
        if time.time() - self._last_loss_ts < self.config.cooldown_after_loss_sec:
            return False, "cooldown"

        # Consecutive losses circuit breaker
        if self._consecutive_losses >= 3:
            return False, "consecutive_losses"

        return True, "ok"

    def record_trade_pnl(self, pnl: float) -> None:
        """Record trade PnL for daily tracking."""
        self._check_daily_reset()
        self._daily_pnl += pnl
        self._current_balance += pnl
        self._peak_balance = max(self._peak_balance, self._current_balance)

        if pnl < 0:
            self._last_loss_ts = time.time()
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def _check_daily_reset(self) -> None:
        """Reset daily PnL counter at midnight UTC."""
        now = time.time()
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).date()
        if self._daily_reset_ts != today.toordinal():
            self._daily_pnl = 0.0
            self._daily_reset_ts = today.toordinal()

    def get_status(self) -> Dict[str, Any]:
        """Get current risk status."""
        self._check_daily_reset()
        drawdown = (self._peak_balance - self._current_balance) / self._peak_balance * 100 if self._peak_balance > 0 else 0
        daily_loss_pct = abs(self._daily_pnl) / self._current_balance * 100 if self._daily_pnl < 0 else 0

        return {
            "balance": round(self._current_balance, 2),
            "peak_balance": round(self._peak_balance, 2),
            "drawdown_pct": round(drawdown, 2),
            "daily_pnl": round(self._daily_pnl, 2),
            "daily_loss_pct": round(daily_loss_pct, 2),
            "consecutive_losses": self._consecutive_losses,
            "max_positions": self.config.max_positions,
            "risk_per_trade_pct": self.config.risk_per_trade_pct,
            "max_daily_loss_pct": self.config.max_daily_loss_pct,
            "max_drawdown_pct": self.config.max_drawdown_pct,
        }

    def update_balance(self, balance: float) -> None:
        """Update account balance."""
        self._current_balance = balance
        self._peak_balance = max(self._peak_balance, balance)

    def reset_daily(self) -> None:
        """Manually reset daily counters."""
        self._daily_pnl = 0.0
        self._consecutive_losses = 0
