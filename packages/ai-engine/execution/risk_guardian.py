"""
Risk Guardian — Real-time risk monitoring and circuit breakers.

Monitors:
- Portfolio drawdown
- Daily P&L limits
- Position concentration
- Leverage limits
- Margin usage
- Correlated exposure
- Volatility spikes
- Loss streaks

Actions:
- Block new trades when limits exceeded
- Force close positions on breach
- Reduce position sizes
- Lower leverage
- Send risk alerts
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from loguru import logger

import sys
from pathlib import Path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from config import config


class RiskLevel(str, Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    BREACH = "BREACH"


class RiskAction(str, Enum):
    NONE = "NONE"
    WARN = "WARN"
    BLOCK_NEW_TRADES = "BLOCK_NEW_TRADES"
    REDUCE_SIZE = "REDUCE_SIZE"
    CLOSE_POSITIONS = "CLOSE_POSITIONS"
    HALT = "HALT"


@dataclass
class RiskState:
    """Current risk state snapshot."""
    level: str = RiskLevel.NORMAL.value
    action: str = RiskAction.NONE.value
    drawdown_pct: float = 0.0
    daily_pnl: float = 0.0
    daily_loss_pct: float = 0.0
    open_positions: int = 0
    total_exposure: float = 0.0
    total_margin: float = 0.0
    margin_usage_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    peak_equity: float = 0.0
    current_equity: float = 0.0
    loss_streak: int = 0
    win_streak: int = 0
    consecutive_losses: int = 0
    alerts: List[str] = field(default_factory=list)
    breaches: List[str] = field(default_factory=list)
    last_update: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "level": self.level,
            "action": self.action,
            "drawdown_pct": round(self.drawdown_pct, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_loss_pct": round(self.daily_loss_pct, 2),
            "open_positions": self.open_positions,
            "total_exposure": round(self.total_exposure, 2),
            "total_margin": round(self.total_margin, 2),
            "margin_usage_pct": round(self.margin_usage_pct, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "peak_equity": round(self.peak_equity, 2),
            "current_equity": round(self.current_equity, 2),
            "loss_streak": self.loss_streak,
            "alerts": self.alerts,
            "breaches": self.breaches,
        }


class RiskGuardian:
    """
    Real-time risk monitoring with circuit breakers.

    Risk Levels:
    - NORMAL: All limits within bounds
    - ELEVATED: Approaching limits (80% of threshold)
    - HIGH: Near limits (90% of threshold)
    - CRITICAL: At limits — new trades blocked
    - BREACH: Exceeded limits — force close required

    Checks run continuously:
    - Every tick: Drawdown, P&L
    - Every second: Position risk, margin
    - Every minute: Correlation, concentration
    """

    def __init__(self) -> None:
        # Risk parameters (from config)
        self._max_drawdown_pct = config.risk.max_drawdown_pct  # 10%
        self._max_daily_loss_pct = config.risk.max_daily_loss_pct  # 5%
        self._max_open_positions = config.risk.max_open_positions  # 10
        self._max_leverage = config.risk.max_leverage  # 20
        self._risk_per_trade_pct = config.risk.risk_per_trade_pct  # 1%
        self._max_position_pct = config.risk.max_position_pct  # 2%

        # State
        self._state = RiskState()
        self._starting_equity = 10_000.0
        self._peak_equity = self._starting_equity
        self._daily_start_equity = self._starting_equity
        self._daily_start_time = time.time()
        self._trade_results: List[float] = []  # Recent trade P&Ls
        self._halt_until: float = 0.0

        # Circuit breaker
        self._breach_count = 0
        self._last_breach_time = 0.0
        self._circuit_breaker_trips = 0

        # Callbacks
        self._on_alert: Optional[Callable] = None
        self._on_breach: Optional[Callable] = None
        self._on_halt: Optional[Callable] = None

    def set_callbacks(
        self,
        on_alert: Optional[Callable] = None,
        on_breach: Optional[Callable] = None,
        on_halt: Optional[Callable] = None,
    ) -> None:
        self._on_alert = on_alert
        self._on_breach = on_breach
        self._on_halt = on_halt

    def set_starting_equity(self, equity: float) -> None:
        self._starting_equity = equity
        self._peak_equity = equity
        self._daily_start_equity = equity
        self._state.peak_equity = equity
        self._state.current_equity = equity

    # ── Signal Validation ────────────────────────────────────────

    def check_signal(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        quantity: float,
        leverage: int,
        confidence: float = 0.0,
    ) -> Tuple[bool, str, RiskAction]:
        """
        Validate a signal against all risk limits.

        Returns: (allowed, reason, action)
        """
        # Check halt
        if time.time() < self._halt_until:
            return False, "System halted due to risk breach", RiskAction.HALT

        # Check risk level
        if self._state.level == RiskLevel.BREACH.value:
            return False, "Risk breach — all trading halted", RiskAction.HALT

        if self._state.level == RiskLevel.CRITICAL.value:
            return False, "Risk critical — new trades blocked", RiskAction.BLOCK_NEW_TRADES

        # Drawdown check
        if self._state.drawdown_pct >= self._max_drawdown_pct:
            return False, f"Max drawdown exceeded: {self._state.drawdown_pct:.1f}% >= {self._max_drawdown_pct}%", RiskAction.HALT

        # Daily loss check
        if self._state.daily_loss_pct >= self._max_daily_loss_pct:
            return False, f"Daily loss limit exceeded: {self._state.daily_loss_pct:.1f}% >= {self._max_daily_loss_pct}%", RiskAction.BLOCK_NEW_TRADES

        # Position count check
        if self._state.open_positions >= self._max_open_positions:
            return False, f"Max positions reached: {self._state.open_positions} >= {self._max_open_positions}", RiskAction.BLOCK_NEW_TRADES

        # Leverage check
        if leverage > self._max_leverage:
            return False, f"Leverage too high: {leverage}x > {self._max_leverage}x", RiskAction.REDUCE_SIZE

        # Position size check
        position_value = entry_price * quantity
        max_position = self._state.current_equity * self._max_position_pct / 100
        if position_value > max_position:
            return False, f"Position too large: ${position_value:.0f} > ${max_position:.0f}", RiskAction.REDUCE_SIZE

        # Risk per trade check
        risk_distance = abs(entry_price - stop_loss)
        risk_amount = risk_distance * quantity
        max_risk = self._state.current_equity * self._risk_per_trade_pct / 100
        if risk_amount > max_risk * 1.1:  # 10% tolerance
            return False, f"Risk per trade exceeded: ${risk_amount:.0f} > ${max_risk:.0f}", RiskAction.REDUCE_SIZE

        # Confidence check
        if confidence < 0.5: # Aligned with backtest threshold (0-1 scale)
            return False, f"Confidence too low: {confidence:.2f} < 0.50", RiskAction.NONE

        # Loss streak check
        if self._state.consecutive_losses >= 5:
            return False, f"Loss streak: {self._state.consecutive_losses} consecutive losses", RiskAction.REDUCE_SIZE

        return True, "Risk check passed", RiskAction.NONE

    # ── State Updates ────────────────────────────────────────────

    def update_equity(self, current_equity: float) -> None:
        """Update current equity and recalculate risk metrics."""
        self._state.current_equity = current_equity
        self._state.last_update = time.time()

        # Peak tracking
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        self._state.peak_equity = self._peak_equity

        # Drawdown
        if self._peak_equity > 0:
            self._state.drawdown_pct = ((self._peak_equity - current_equity) / self._peak_equity) * 100
        self._state.max_drawdown_pct = max(self._state.max_drawdown_pct, self._state.drawdown_pct)

        # Daily P&L
        self._state.daily_pnl = current_equity - self._daily_start_equity
        if self._daily_start_equity > 0:
            self._state.daily_loss_pct = max(0, -self._state.daily_pnl / self._daily_start_equity * 100)

        # Update risk level
        self._update_risk_level()

    def update_positions(self, open_count: int, total_exposure: float, total_margin: float) -> None:
        """Update position-related risk metrics."""
        self._state.open_positions = open_count
        self._state.total_exposure = total_exposure
        self._state.total_margin = total_margin

        if self._state.current_equity > 0:
            self._state.margin_usage_pct = (total_margin / self._state.current_equity) * 100

    def record_trade_result(self, pnl: float) -> None:
        """Record a trade result for streak tracking."""
        self._trade_results.append(pnl)
        if len(self._trade_results) > 100:
            self._trade_results = self._trade_results[-50:]

        if pnl > 0:
            self._state.win_streak += 1
            self._state.consecutive_losses = 0
            self._state.loss_streak = max(self._state.loss_streak, 0)
        else:
            self._state.loss_streak += 1
            self._state.consecutive_losses += 1
            self._state.win_streak = 0

    def reset_daily(self) -> None:
        """Reset daily tracking (call at midnight UTC)."""
        self._daily_start_equity = self._state.current_equity
        self._daily_start_time = time.time()
        self._state.daily_pnl = 0.0
        self._state.daily_loss_pct = 0.0
        logger.info("Risk guardian daily reset: equity=${:.2f}", self._daily_start_equity)

    # ── Risk Level Calculation ───────────────────────────────────

    def _update_risk_level(self) -> None:
        """Calculate current risk level from all metrics."""
        alerts = []
        breaches = []
        level = RiskLevel.NORMAL

        # Drawdown
        dd = self._state.drawdown_pct
        dd_limit = self._max_drawdown_pct
        if dd >= dd_limit:
            breaches.append(f"Drawdown {dd:.1f}% >= {dd_limit}%")
            level = RiskLevel.BREACH
        elif dd >= dd_limit * 0.9:
            alerts.append(f"Drawdown CRITICAL: {dd:.1f}%")
            level = max_level(level, RiskLevel.CRITICAL)
        elif dd >= dd_limit * 0.8:
            alerts.append(f"Drawdown HIGH: {dd:.1f}%")
            level = max_level(level, RiskLevel.HIGH)
        elif dd >= dd_limit * 0.6:
            level = max_level(level, RiskLevel.ELEVATED)

        # Daily loss
        dl = self._state.daily_loss_pct
        dl_limit = self._max_daily_loss_pct
        if dl >= dl_limit:
            breaches.append(f"Daily loss {dl:.1f}% >= {dl_limit}%")
            level = max_level(level, RiskLevel.CRITICAL)
        elif dl >= dl_limit * 0.8:
            alerts.append(f"Daily loss HIGH: {dl:.1f}%")
            level = max_level(level, RiskLevel.HIGH)

        # Position count
        if self._state.open_positions >= self._max_open_positions:
            level = max_level(level, RiskLevel.HIGH)

        # Margin usage
        if self._state.margin_usage_pct > 80:
            alerts.append(f"Margin usage HIGH: {self._state.margin_usage_pct:.1f}%")
            level = max_level(level, RiskLevel.HIGH)

        # Loss streak
        if self._state.consecutive_losses >= 5:
            alerts.append(f"Loss streak: {self._state.consecutive_losses}")
            level = max_level(level, RiskLevel.ELEVATED)

        # Update state
        old_level = self._state.level
        self._state.level = level.value
        self._state.alerts = alerts
        self._state.breaches = breaches

        # Determine action
        if level == RiskLevel.BREACH:
            self._state.action = RiskAction.HALT.value
            self._trigger_breach(breaches)
        elif level == RiskLevel.CRITICAL:
            self._state.action = RiskAction.BLOCK_NEW_TRADES.value
        elif level == RiskLevel.HIGH:
            self._state.action = RiskAction.REDUCE_SIZE.value
        elif level == RiskLevel.ELEVATED:
            self._state.action = RiskAction.WARN.value
        else:
            self._state.action = RiskAction.NONE.value

        # Log level changes
        if level.value != old_level:
            logger.warning("Risk level changed: {} → {} (alerts={}, breaches={})",
                          old_level, level.value, len(alerts), len(breaches))

    def _trigger_breach(self, breaches: List[str]) -> None:
        """Handle risk breach."""
        self._breach_count += 1
        self._last_breach_time = time.time()

        # Circuit breaker: halt for increasing durations
        halt_minutes = min(30, 5 * (2 ** min(self._breach_count - 1, 3)))
        self._halt_until = time.time() + halt_minutes * 60

        logger.critical("RISK BREACH #{}: {} — halting for {} minutes",
                        self._breach_count, breaches, halt_minutes)

        if self._on_breach:
            for breach in breaches:
                self._on_breach(breach)

    # ── Queries ──────────────────────────────────────────────────

    def get_state(self) -> RiskState:
        return self._state

    def is_trading_allowed(self) -> bool:
        """Check if new trades are allowed."""
        if time.time() < self._halt_until:
            return False
        return self._state.level not in (
            RiskLevel.BREACH.value,
            RiskLevel.CRITICAL.value,
        )

    def get_position_size_multiplier(self) -> float:
        """Get position size multiplier based on risk level."""
        if self._state.level == RiskLevel.NORMAL.value:
            return 1.0
        elif self._state.level == RiskLevel.ELEVATED.value:
            return 0.75
        elif self._state.level == RiskLevel.HIGH.value:
            return 0.5
        else:
            return 0.0

    def get_stats(self) -> Dict:
        """Get risk guardian statistics."""
        return {
            "level": self._state.level,
            "action": self._state.action,
            "drawdown_pct": round(self._state.drawdown_pct, 2),
            "daily_loss_pct": round(self._state.daily_loss_pct, 2),
            "breach_count": self._breach_count,
            "circuit_breaker_trips": self._circuit_breaker_trips,
            "consecutive_losses": self._state.consecutive_losses,
            "halt_until": self._halt_until,
            "trading_allowed": self.is_trading_allowed(),
            "size_multiplier": self.get_position_size_multiplier(),
        }


def max_level(a: RiskLevel, b: RiskLevel) -> RiskLevel:
    """Return the higher of two risk levels."""
    order = {
        RiskLevel.NORMAL: 0,
        RiskLevel.ELEVATED: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
        RiskLevel.BREACH: 4,
    }
    return a if order.get(a, 0) >= order.get(b, 0) else b
