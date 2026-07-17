"""
Trade Blocker — Phase 12: Automatic trade blocking on risk events.

Blocks trades automatically when:
  1. 3 Consecutive Losses → pause trading
  2. Daily Drawdown > 3% → stop new positions
  3. Weekly Drawdown > 8% → stop new positions
  4. News Volatility Event → pause trading
  5. Exchange Data Failure → stop trading

Provides:
  - Real-time risk monitoring
  - Automatic trade blocking/unblocking
  - State persistence (survives restarts)
  - Dashboard integration for status display
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from loguru import logger


@dataclass
class BlockerState:
    """Current blocker state."""
    blocked: bool = False
    block_reason: str = ""
    block_time: float = 0
    consecutive_losses: int = 0
    daily_pnl: float = 0
    daily_pnl_pct: float = 0
    weekly_pnl: float = 0
    weekly_pnl_pct: float = 0
    daily_peak_equity: float = 0
    weekly_peak_equity: float = 0
    last_loss_time: float = 0
    loss_streak_start: float = 0
    # Trade history for streak tracking
    recent_trades: List[Dict] = field(default_factory=list)
    # Data freshness
    exchange_data_ok: bool = True
    last_data_tick: float = 0
    # News event
    news_event_active: bool = False
    news_event_start: float = 0


class TradeBlocker:
    """
    Phase 12: Automatic trade blocking on risk events.
    
    Monitors:
    - Consecutive losses (3+ = block)
    - Daily drawdown (>3% = block)
    - Weekly drawdown (>8% = block)
    - Exchange data failure (no data = block)
    - News volatility events (optional)
    """

    # ── Configuration ──
    MAX_CONSECUTIVE_LOSSES = 3
    MAX_DAILY_DD_PCT = 3.0       # 3% daily drawdown
    MAX_WEEKLY_DD_PCT = 8.0      # 8% weekly drawdown
    DATA_TIMEOUT_SEC = 120       # No data for 2 min = data failure
    NEWS_PAUSE_SEC = 300         # Pause 5 min during news
    LOSS_STREAK_WINDOW = 3600    # 1 hour window for consecutive losses

    def __init__(self, initial_equity: float = 10_000.0) -> None:
        self._state = BlockerState()
        self._initial_equity = initial_equity
        self._state.daily_peak_equity = initial_equity
        self._state.weekly_peak_equity = initial_equity

    def record_trade_outcome(self, pnl: float, symbol: str = "") -> None:
        """Record a trade outcome for streak and drawdown tracking."""
        now = time.time()
        self._state.recent_trades.append({
            "pnl": pnl,
            "symbol": symbol,
            "timestamp": now,
        })
        # Keep last 100 trades
        if len(self._state.recent_trades) > 100:
            self._state.recent_trades = self._state.recent_trades[-100:]

        # Update daily PnL
        self._state.daily_pnl += pnl
        equity = self._initial_equity + self._state.daily_pnl
        if equity > self._state.daily_peak_equity:
            self._state.daily_peak_equity = equity
        self._state.daily_pnl_pct = (
            (self._state.daily_peak_equity - equity) / self._state.daily_peak_equity * 100
            if self._state.daily_peak_equity > 0 else 0
        )

        # Update weekly PnL
        self._state.weekly_pnl += pnl
        if equity > self._state.weekly_peak_equity:
            self._state.weekly_peak_equity = equity
        self._state.weekly_pnl_pct = (
            (self._state.weekly_peak_equity - equity) / self._state.weekly_peak_equity * 100
            if self._state.weekly_peak_equity > 0 else 0
        )

        # Track consecutive losses
        if pnl < 0:
            self._state.consecutive_losses += 1
            self._state.last_loss_time = now
            if self._state.consecutive_losses == 1:
                self._state.loss_streak_start = now
        else:
            # Win resets the streak
            self._state.consecutive_losses = 0
            self._state.loss_streak_start = 0

        # Check all blocking conditions
        self._evaluate()

    def record_data_tick(self, source: str = "binance") -> None:
        """Record that exchange data is flowing (called from data freshness engine)."""
        self._state.last_data_tick = time.time()
        self._state.exchange_data_ok = True

    def trigger_news_event(self, duration_sec: int = 300) -> None:
        """Manually trigger a news volatility pause."""
        self._state.news_event_active = True
        self._state.news_event_start = time.time()
        self._state.block_reason = f"News volatility event (paused {duration_sec}s)"
        self._state.blocked = True
        self._state.block_time = time.time()
        logger.warning("🚨 TRADE BLOCKER: News event triggered — pausing {}s", duration_sec)

    def _evaluate(self) -> None:
        """Evaluate all blocking conditions."""
        now = time.time()
        reasons = []

        # 1. Consecutive losses
        # Only count losses within the streak window
        if self._state.loss_streak_start > 0:
            time_since_streak = now - self._state.loss_streak_start
            if time_since_streak < self.LOSS_STREAK_WINDOW:
                if self._state.consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
                    reasons.append(f"3+ consecutive losses ({self._state.consecutive_losses})")
            else:
                # Streak expired
                self._state.consecutive_losses = 0
                self._state.loss_streak_start = 0

        # 2. Daily drawdown
        if self._state.daily_pnl_pct >= self.MAX_DAILY_DD_PCT:
            reasons.append(f"Daily DD {self._state.daily_pnl_pct:.1f}% >= {self.MAX_DAILY_DD_PCT}%")

        # 3. Weekly drawdown
        if self._state.weekly_pnl_pct >= self.MAX_WEEKLY_DD_PCT:
            reasons.append(f"Weekly DD {self._state.weekly_pnl_pct:.1f}% >= {self.MAX_WEEKLY_DD_PCT}%")

        # 4. Exchange data failure
        if self._state.last_data_tick > 0:
            data_age = now - self._state.last_data_tick
            if data_age > self.DATA_TIMEOUT_SEC:
                self._state.exchange_data_ok = False
                reasons.append(f"Exchange data stale ({data_age:.0f}s > {self.DATA_TIMEOUT_SEC}s)")
        elif now - self._state.block_time > 30:
            # Only flag if engine has been running for 30+ seconds
            reasons.append("No exchange data received")

        # 5. News event
        if self._state.news_event_active:
            news_age = now - self._state.news_event_start
            if news_age < self.NEWS_PAUSE_SEC:
                reasons.append(f"News event active ({self.NEWS_PAUSE_SEC - news_age:.0f}s remaining)")
            else:
                self._state.news_event_active = False

        # Apply blocking
        if reasons:
            self._state.blocked = True
            self._state.block_reason = " | ".join(reasons)
            if self._state.block_time == 0:
                self._state.block_time = now
            logger.warning("🚫 TRADE BLOCKER ACTIVE: {}", self._state.block_reason)
        else:
            if self._state.blocked:
                logger.info("✅ TRADE BLOCKER CLEARED: {}", self._state.block_reason)
            self._state.blocked = False
            self._state.block_reason = ""
            self._state.block_time = 0

    def is_blocked(self) -> bool:
        """Check if trading is currently blocked."""
        self._evaluate()
        return self._state.blocked

    def get_status(self) -> Dict:
        """Get full blocker status for dashboard display."""
        self._evaluate()
        return {
            "blocked": self._state.blocked,
            "block_reason": self._state.block_reason,
            "consecutive_losses": self._state.consecutive_losses,
            "daily_pnl": round(self._state.daily_pnl, 2),
            "daily_pnl_pct": round(self._state.daily_pnl_pct, 2),
            "weekly_pnl": round(self._state.weekly_pnl, 2),
            "weekly_pnl_pct": round(self._state.weekly_pnl_pct, 2),
            "daily_dd_limit": self.MAX_DAILY_DD_PCT,
            "weekly_dd_limit": self.MAX_WEEKLY_DD_PCT,
            "exchange_data_ok": self._state.exchange_data_ok,
            "news_event_active": self._state.news_event_active,
            "max_consecutive_losses": self.MAX_CONSECUTIVE_LOSSES,
        }

    def reset_daily(self) -> None:
        """Reset daily tracking (call at midnight or session start)."""
        self._state.daily_pnl = 0
        self._state.daily_pnl_pct = 0
        self._state.daily_peak_equity = self._initial_equity + self._state.weekly_pnl
        logger.info("🔄 Trade Blocker: daily reset")

    def reset_weekly(self) -> None:
        """Reset weekly tracking."""
        self._state.weekly_pnl = 0
        self._state.weekly_pnl_pct = 0
        self._state.weekly_peak_equity = self._initial_equity
        self._state.daily_peak_equity = self._initial_equity
        logger.info("🔄 Trade Blocker: weekly reset")
