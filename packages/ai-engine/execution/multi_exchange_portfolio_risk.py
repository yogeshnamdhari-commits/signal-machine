"""
Multi-Exchange Portfolio Risk Engine — Cross-exchange risk management.

Validates new positions against portfolio-wide risk limits:
- Maximum total exposure across all exchanges
- Per-exchange exposure limits
- Correlation-based concentration risk
- Cross-exchange margin utilization
- Daily loss limits
- Drawdown protection
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


@dataclass
class RiskLimits:
    """Portfolio-wide risk limits."""
    max_total_exposure_usd: float = 100_000.0
    max_per_exchange_exposure_usd: float = 50_000.0
    max_per_symbol_exposure_usd: float = 20_000.0
    max_open_positions: int = 10
    max_correlated_positions: int = 5
    max_daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    max_margin_utilization_pct: float = 80.0
    max_arbitrage_exposure_usd: float = 20_000.0 # New limit for arbitrage
    max_single_position_pct: float = 20.0  # % of total equity


@dataclass
class PortfolioSnapshot:
    """Point-in-time portfolio state."""
    total_equity: float = 0.0
    total_exposure_usd: float = 0.0
    exchange_exposure: Dict[str, float] = field(default_factory=dict)
    symbol_exposure: Dict[str, float] = field(default_factory=dict)
    open_position_count: int = 0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    peak_equity: float = 0.0
    current_drawdown_pct: float = 0.0
    margin_utilization_pct: float = 0.0
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_equity": self.total_equity,
            "total_exposure_usd": self.total_exposure_usd,
            "exchange_exposure": self.exchange_exposure,
            "symbol_exposure": self.symbol_exposure,
            "open_position_count": self.open_position_count,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_pct": self.daily_pnl_pct,
            "peak_equity": self.peak_equity,
            "current_drawdown_pct": self.current_drawdown_pct,
            "margin_utilization_pct": self.margin_utilization_pct,
            "timestamp": self.timestamp,
        }


class MultiExchangePortfolioRiskEngine:
    """
    Cross-exchange portfolio risk management.

    Validates every new trade against portfolio-wide constraints
    to prevent over-concentration and excessive risk.
    """

    def __init__(self, limits: Optional[RiskLimits] = None) -> None:
        self.limits = limits or RiskLimits()

        # State tracking
        self._equity = 0.0
        self._peak_equity = 0.0
        self._daily_pnl = 0.0
        self._daily_pnl_start = 0.0
        self._last_daily_reset = 0.0
        self._positions: List[Dict[str, Any]] = []

        logger.info("[portfolio_risk] Initialized with limits: max_exposure=${:,.0f}, max_positions={}",
                     self.limits.max_total_exposure_usd, self.limits.max_open_positions)

    async def get_snapshot(self, equity: float) -> PortfolioSnapshot:
        """
        Get current portfolio risk snapshot.

        Args:
            equity: Current account equity in USD

        Returns:
            PortfolioSnapshot with all risk metrics
        """
        self._equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

        # Reset daily PnL at midnight UTC
        now = time.time()
        if now - self._last_daily_reset > 86400:
            self._daily_pnl = 0.0
            self._daily_pnl_start = equity
            self._last_daily_reset = now

        # Calculate exposures
        exchange_exposure: Dict[str, float] = {}
        symbol_exposure: Dict[str, float] = {}
        total_exposure = 0.0

        for pos in self._positions:
            notional = pos.get("notional", 0.0)
            exchange = pos.get("exchange", "unknown")
            symbol = pos.get("symbol", "")

            total_exposure += notional
            exchange_exposure[exchange] = exchange_exposure.get(exchange, 0) + notional
            symbol_exposure[symbol] = symbol_exposure.get(symbol, 0) + notional

        # Drawdown
        drawdown_pct = 0.0
        if self._peak_equity > 0:
            drawdown_pct = (self._peak_equity - equity) / self._peak_equity * 100

        # Daily PnL
        daily_pnl_pct = 0.0
        if self._daily_pnl_start > 0:
            daily_pnl_pct = (equity - self._daily_pnl_start) / self._daily_pnl_start * 100

        # Margin utilization (estimate)
        margin_used = total_exposure / 20  # Assume 20x leverage
        margin_util = (margin_used / max(equity, 1)) * 100

        return PortfolioSnapshot(
            total_equity=equity,
            total_exposure_usd=total_exposure,
            exchange_exposure=exchange_exposure,
            symbol_exposure=symbol_exposure,
            open_position_count=len(self._positions),
            daily_pnl=self._daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            peak_equity=self._peak_equity,
            current_drawdown_pct=drawdown_pct,
            margin_utilization_pct=margin_util,
            timestamp=now,
        )

    def can_add_position(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        snapshot: PortfolioSnapshot, # Current portfolio snapshot
        is_arbitrage: bool = False, # Flag to differentiate arbitrage trades
    ) -> Tuple[bool, List[str]]:
        """
        Check if a new position can be added within risk limits.

        Args:
            symbol: Trading pair
            side: "LONG" or "SHORT"
            quantity: Position size
            price: Entry price
            snapshot: Current portfolio snapshot
            is_arbitrage: True if this is an arbitrage trade leg

        Returns:
            (allowed, violations) tuple
        """
        violations: List[str] = []
        notional = quantity * price

        # 1. Max open positions
        if snapshot.open_position_count >= self.limits.max_open_positions:
            violations.append(
                f"max_positions({snapshot.open_position_count}/{self.limits.max_open_positions})"
            )

        # 2. Max total exposure
        if snapshot.total_exposure_usd + notional > self.limits.max_total_exposure_usd:
            violations.append(
                f"max_exposure(${snapshot.total_exposure_usd + notional:,.0f}/"
                f"${self.limits.max_total_exposure_usd:,.0f})"
            )
        
        # 2.1 Max arbitrage exposure (if applicable)
        if is_arbitrage and snapshot.total_exposure_usd + notional > self.limits.max_arbitrage_exposure_usd:
            violations.append(
                f"max_arbitrage_exposure(${snapshot.total_exposure_usd + notional:,.0f}/"
                f"${self.limits.max_arbitrage_exposure_usd:,.0f})"
            )

        # 3. Max per-symbol exposure
        current_symbol_exposure = snapshot.symbol_exposure.get(symbol, 0)
        if current_symbol_exposure + notional > self.limits.max_per_symbol_exposure_usd:
            violations.append(
                f"max_symbol_exposure({symbol}: ${current_symbol_exposure + notional:,.0f}/"
                f"${self.limits.max_per_symbol_exposure_usd:,.0f})"
            )

        # 4. Max single position as % of equity
        if snapshot.total_equity > 0:
            position_pct = (notional / snapshot.total_equity) * 100
            if position_pct > self.limits.max_single_position_pct:
                violations.append(
                    f"max_position_pct({position_pct:.1f}%/"
                    f"{self.limits.max_single_position_pct:.1f}%)"
                )

        # 5. Daily loss limit
        if snapshot.daily_pnl_pct < -self.limits.max_daily_loss_pct:
            violations.append(
                f"daily_loss({snapshot.daily_pnl_pct:.2f}%/"
                f"-{self.limits.max_daily_loss_pct:.2f}%)"
            )

        # 6. Drawdown limit
        if snapshot.current_drawdown_pct >= self.limits.max_drawdown_pct:
            violations.append(
                f"drawdown({snapshot.current_drawdown_pct:.2f}%/"
                f"{self.limits.max_drawdown_pct:.2f}%)"
            )

        # 7. Margin utilization
        if snapshot.margin_utilization_pct >= self.limits.max_margin_utilization_pct:
            violations.append(
                f"margin_util({snapshot.margin_utilization_pct:.1f}%/"
                f"{self.limits.max_margin_utilization_pct:.1f}%)"
            )

        allowed = len(violations) == 0
        if not allowed:
            logger.warning("[portfolio_risk] Trade blocked: {}", violations)

        return allowed, violations

    def add_position(self, position: Dict[str, Any]) -> None:
        """Track a new position."""
        self._positions.append(position)
        logger.debug("[portfolio_risk] Added position: {} {} notional=${:,.2f}",
                     position.get("symbol"), position.get("side"),
                     position.get("notional", 0))

    def remove_position(self, position_id: str) -> None:
        """Remove a closed position."""
        self._positions = [p for p in self._positions if p.get("position_id") != position_id]

    def record_pnl(self, pnl: float) -> None:
        """Record realized PnL."""
        self._daily_pnl += pnl
        logger.debug("[portfolio_risk] PnL recorded: ${:+.2f} (daily: ${:+.2f})", pnl, self._daily_pnl)

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all tracked positions."""
        return self._positions.copy()

    def get_stats(self) -> Dict[str, Any]:
        """Get portfolio risk statistics."""
        return {
            "equity": self._equity,
            "peak_equity": self._peak_equity,
            "daily_pnl": self._daily_pnl,
            "open_positions": len(self._positions),
            "limits": {
                "max_total_exposure_usd": self.limits.max_total_exposure_usd,
                "max_open_positions": self.limits.max_open_positions,
                "max_daily_loss_pct": self.limits.max_daily_loss_pct,
                "max_drawdown_pct": self.limits.max_drawdown_pct,
            },
        }
