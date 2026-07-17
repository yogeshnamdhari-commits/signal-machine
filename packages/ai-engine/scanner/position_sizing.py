"""
Position Sizing Engine — Kelly criterion, fixed fractional, volatility-based sizing.
Integrates with Adaptive Alpha Ranking for tier-based position multipliers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
from loguru import logger
from config import config


@dataclass
class SizeResult:
    quantity: float
    position_value: float
    margin_required: float
    risk_amount: float
    method: str
    kelly_fraction: float = 0
    adjusted_for_drawdown: bool = False
    alpha_multiplier: float = 1.0
    alpha_tier: str = ""


class PositionSizingEngine:
    """
    Multiple position sizing methods:
    - Fixed fractional (% of balance per trade)
    - Kelly criterion (optimal sizing from win rate)
    - Volatility-based (ATR-normalized)
    - Risk-parity adjusted
    - Drawdown-adjusted (reduce size when losing)
    """

    def __init__(self) -> None:
        self.balance = 10_000.0
        self.peak_balance = self.balance
        self.win_rate = 0.5
        self.avg_win = 0.02
        self.avg_loss = 0.01
        self.method = "fixed_fractional"
        self._alpha_ranking = None  # Set by engine for tier-based sizing

    def set_alpha_ranking(self, alpha_ranking) -> None:
        """Set the alpha ranking engine for tier-based position sizing."""
        self._alpha_ranking = alpha_ranking

    async def initialize(self) -> None:
        logger.info("PositionSizing engine ready")

    async def calculate_size(
        self, symbol: str, direction: str, entry_price: float,
        stop_loss: float, atr: float = 0,
        win_rate: Optional[float] = None, avg_win: Optional[float] = None,
        avg_loss: Optional[float] = None,
    ) -> SizeResult:
        risk_usd = self.balance * config.risk.risk_per_trade_pct / 100
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance == 0 or entry_price == 0:
            return SizeResult(0, 0, 0, 0, "invalid")

        # Fixed fractional
        fixed_qty = risk_usd / risk_distance

        # Kelly criterion
        wr = win_rate or self.win_rate
        aw = avg_win or self.avg_win
        al = avg_loss or self.avg_loss
        kelly = (wr * aw - (1 - wr) * al) / aw if aw > 0 else 0
        kelly = max(0, min(kelly, 0.25))  # Cap at 25%
        kelly_usd = self.balance * kelly
        kelly_qty = kelly_usd / entry_price

        # Volatility-based (ATR normalized)
        if atr > 0:
            vol_risk = self.balance * 0.01  # 1% vol target
            vol_qty = vol_risk / (atr * config.risk.sl_atr_mult)
        else:
            vol_qty = fixed_qty

        # Choose method
        if self.method == "kelly":
            qty = kelly_qty
            method = "kelly"
        elif self.method == "volatility" and atr > 0:
            qty = vol_qty
            method = "volatility"
        else:
            qty = fixed_qty
            method = "fixed_fractional"

        # Drawdown adjustment
        dd = (self.peak_balance - self.balance) / self.peak_balance if self.peak_balance > 0 else 0
        adjusted = False
        if dd > 0.05:  # > 5% drawdown
            adj_factor = max(0.5, 1 - dd)
            qty *= adj_factor
            adjusted = True

        # Cap at max position
        pos_val = qty * entry_price
        max_pos = self.balance * config.risk.max_position_pct / 100
        if pos_val > max_pos:
            qty = max_pos / entry_price
            pos_val = max_pos

        margin = pos_val / config.risk.max_leverage

        # ── Alpha Ranking Integration ──
        alpha_multiplier = 1.0
        alpha_tier = ""
        if self._alpha_ranking:
            alpha_multiplier = self._alpha_ranking.get_position_multiplier(symbol)
            alpha_tier = self._alpha_ranking.get_tier(symbol)
            # Apply tier multiplier to final quantity
            qty *= alpha_multiplier
            pos_val = qty * entry_price

            # Re-cap at max position after multiplier
            if pos_val > max_pos:
                qty = max_pos / entry_price
                pos_val = qty * entry_price

            margin = pos_val / config.risk.max_leverage

        return SizeResult(
            quantity=round(qty, 6),
            position_value=round(pos_val, 2),
            margin_required=round(margin, 2),
            risk_amount=round(risk_usd, 2),
            method=method,
            kelly_fraction=kelly,
            adjusted_for_drawdown=adjusted,
            alpha_multiplier=alpha_multiplier,
            alpha_tier=alpha_tier,
        )

    def update_stats(self, balance: float, win_rate: float, avg_win: float, avg_loss: float) -> None:
        self.balance = balance
        self.peak_balance = max(self.peak_balance, balance)
        self.win_rate = win_rate
        self.avg_win = avg_win
        self.avg_loss = avg_loss
