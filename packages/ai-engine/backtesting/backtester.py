"""
Backtesting Engine — event-driven strategy simulation with realistic execution.
Simulates trades using historical data with slippage, fees, and position management.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


@dataclass
class Position:
    symbol: str
    side: Side
    entry_price: float
    size: float  # in base asset
    entry_time: datetime
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: float = 1.0
    pnl: float = 0
    fees_paid: float = 0
    slippage_paid: float = 0
    funding_paid: float = 0
    bars_held: int = 0
    partial_fills: int = 0
    trailing_stop_price: Optional[float] = None

    @property
    def notional(self) -> float:
        return self.entry_price * self.size

    @property
    def margin(self) -> float:
        return self.notional / self.leverage


@dataclass
class Trade:
    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    size: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    fees: float
    slippage: float
    funding: float
    exit_reason: str
    hold_time_minutes: float
    bars_held: int = 0
    partial_fills: int = 0
    r_multiple: float = 0

    @property
    def net_pnl(self) -> float:
        return self.pnl - self.fees - self.slippage

    @property
    def return_pct(self) -> float:
        notional = self.entry_price * self.size
        return (self.net_pnl / notional) * 100 if notional > 0 else 0


@dataclass
class BacktestConfig:
    initial_capital: float = 10_000
    leverage: float = 10.0
    maker_fee: float = 0.0002  # 0.02%
    taker_fee: float = 0.0004  # 0.04%
    slippage_pct: float = 0.0001  # 0.01%
    max_position_pct: float = 0.10  # 10% of capital per trade
    max_positions: int = 5
    risk_per_trade_pct: float = 0.02  # 2% risk per trade
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 3.0
    trailing_stop: bool = False
    trailing_stop_pct: float = 0.02
    # Realistic execution modeling
    spread_pct: float = 0.0003  # 0.03% avg spread
    latency_bars: int = 1  # 1-bar execution delay (simulates network + exchange)
    partial_fill_rate: float = 0.0  # 0% partial fills by default (0.0–0.3 realistic)
    adverse_fill_prob: float = 0.1  # 10% chance of adverse fill (slippage worse)
    adverse_fill_mult: float = 2.0  # adverse fill uses 2x slippage
    funding_rate_pct: float = 0.0001  # 0.01% per 8h funding interval
    min_slippage_bps: float = 0.5  # minimum 0.5 bps slippage


@dataclass
class BacktestState:
    capital: float
    equity_curve: List[float]
    positions: List[Position]
    closed_trades: List[Trade]
    peak_equity: float
    max_drawdown: float
    current_drawdown: float
    win_count: int = 0
    loss_count: int = 0
    total_fees: float = 0
    total_slippage: float = 0


class BacktestEngine:
    """
    Event-driven backtesting engine with realistic execution simulation.
    
    Features:
    - Multi-position management
    - Slippage and fee modeling
    - Stop-loss and take-profit orders
    - Trailing stops
    - ATR-based position sizing
    - Equity curve tracking
    - Drawdown monitoring
    """

    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        self.config = config or BacktestConfig()
        self._state: Optional[BacktestState] = None
        self._signal_callback: Optional[Callable] = None
        self._indicators: Dict[str, pd.DataFrame] = {}

    async def initialize(self) -> None:
        """Initialize the backtesting engine."""
        self._reset_state()
        logger.info("BacktestEngine ready — capital: ${:,.0f}, leverage: {}x",
                     self.config.initial_capital, self.config.leverage)

    def _reset_state(self) -> None:
        """Reset engine state for a new backtest."""
        self._state = BacktestState(
            capital=self.config.initial_capital,
            equity_curve=[self.config.initial_capital],
            positions=[],
            closed_trades=[],
            peak_equity=self.config.initial_capital,
            max_drawdown=0,
            current_drawdown=0,
        )

    # ── Core Backtest Loop ───────────────────────────────────────

    async def run(
        self,
        symbol: str,
        data: pd.DataFrame,
        signal_func: Callable[[pd.DataFrame, int], Optional[Dict]],
        precompute: bool = True,
    ) -> BacktestResult:
        """
        Run a backtest on historical data.
        
        Args:
            symbol: Trading pair symbol
            data: OHLCV DataFrame
            signal_func: Function that returns signal dict or None
                         Signal: {side, confidence, entry, sl, tp, ...}
            precompute: Whether to precompute indicators
        
        Returns:
            BacktestResult with all metrics
        """
        self._reset_state()
        state = self._state

        if data.empty or len(data) < 50:
            logger.warning("Insufficient data for backtest: {} bars", len(data))
            return self._build_result(symbol, data)

        # Precompute indicators
        if precompute:
            data = self._precompute_indicators(data)

        logger.info("Running backtest: {} — {} bars from {} to {}",
                     symbol, len(data), data["open_time"].iloc[0], data["open_time"].iloc[-1])

        # Main loop
        for i in range(50, len(data)):
            row = data.iloc[i]
            bar_time = row["open_time"]
            price = row["close"]
            high = row["high"]
            low = row["low"]

            # Increment bars held for all open positions
            for pos in state.positions:
                pos.bars_held += 1

            # Check existing positions for stop/take-profit/trailing
            await self._check_exits(symbol, high, low, price, bar_time)

            # Update trailing stops
            if self.config.trailing_stop:
                for pos in list(state.positions):
                    if pos.symbol != symbol:
                        continue
                    await self._update_trailing_stop(pos, high, low)

            # Check drawdown limit
            equity = self._calculate_equity(price)
            state.equity_curve.append(equity)
            self._update_drawdown(equity)

            if state.current_drawdown > 0.20:  # 20% max drawdown limit
                logger.warning("Max drawdown breached — stopping backtest")
                break

            # Generate signal
            try:
                signal = signal_func(data, i)
            except Exception as e:
                logger.debug("Signal error at bar {}: {}", i, e)
                signal = None

            # Process signal
            if signal and self._can_open_position(symbol):
                await self._open_position(symbol, signal, price, bar_time, row)

        # Close remaining positions at last price
        last_price = data["close"].iloc[-1]
        last_time = data["open_time"].iloc[-1]
        for pos in list(state.positions):
            await self._close_position(pos, last_price, last_time, "backtest_end")

        result = self._build_result(symbol, data)
        logger.info("Backtest complete: {} — trades={}, win_rate={:.1f}%, pnl=${:,.2f}",
                     symbol, result.total_trades, result.win_rate, result.net_pnl)
        return result

    # ── Position Management ──────────────────────────────────────

    async def _open_position(
        self,
        symbol: str,
        signal: Dict,
        price: float,
        bar_time: datetime,
        row: pd.Series,
    ) -> None:
        """Open a new position with realistic execution modeling."""
        state = self._state
        side_str = signal.get("side", "").upper()
        if side_str not in ("LONG", "SHORT"):
            return

        side = Side.LONG if side_str == "LONG" else Side.SHORT
        confidence = signal.get("confidence", 0.5)
        sl = signal.get("stop_loss")
        tp = signal.get("take_profit")

        # Position sizing based on risk
        size = self._calculate_size(price, sl, side)
        if size <= 0:
            return

        # Simulate spread: bid-ask cost
        spread_cost = price * self.config.spread_pct

        # Simulate slippage with adverse fill probability
        base_slippage = max(price * self.config.slippage_pct, price * self.config.min_slippage_bps / 10_000)
        is_adverse = np.random.random() < self.config.adverse_fill_prob
        slippage = base_slippage * (self.config.adverse_fill_mult if is_adverse else 1.0)

        # Apply spread + slippage to entry
        if side == Side.LONG:
            entry_price = price + spread_cost / 2 + slippage
        else:
            entry_price = price - spread_cost / 2 - slippage

        # Partial fill simulation
        actual_size = size
        partial_fills = 0
        if self.config.partial_fill_rate > 0 and np.random.random() < self.config.partial_fill_rate:
            fill_pct = np.random.uniform(0.7, 0.95)  # 70-95% filled
            actual_size = size * fill_pct
            partial_fills = 1

        # Calculate fees
        notional = entry_price * actual_size
        fee = notional * self.config.taker_fee

        # Trailing stop initialization
        trailing_stop = None
        if self.config.trailing_stop:
            if side == Side.LONG:
                trailing_stop = entry_price * (1 - self.config.trailing_stop_pct)
            else:
                trailing_stop = entry_price * (1 + self.config.trailing_stop_pct)

        position = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            size=actual_size,
            entry_time=bar_time,
            stop_loss=sl,
            take_profit=tp,
            leverage=self.config.leverage,
            fees_paid=fee,
            slippage_paid=(spread_cost / 2 + slippage) * actual_size,
            trailing_stop_price=trailing_stop,
        )

        state.positions.append(position)
        state.capital -= position.margin + fee
        state.total_fees += fee
        state.total_slippage += (spread_cost / 2 + slippage) * actual_size

    async def _close_position(
        self,
        pos: Position,
        exit_price: float,
        exit_time: datetime,
        reason: str,
    ) -> None:
        """Close a position with realistic execution: spread, slippage, funding."""
        state = self._state

        # Apply spread + slippage on exit
        spread_cost = exit_price * self.config.spread_pct
        base_slippage = max(exit_price * self.config.slippage_pct,
                            exit_price * self.config.min_slippage_bps / 10_000)
        is_adverse = np.random.random() < self.config.adverse_fill_prob
        slippage = base_slippage * (self.config.adverse_fill_mult if is_adverse else 1.0)

        if pos.side == Side.LONG:
            actual_exit = exit_price - spread_cost / 2 - slippage
            pnl = (actual_exit - pos.entry_price) * pos.size
        else:
            actual_exit = exit_price + spread_cost / 2 + slippage
            pnl = (pos.entry_price - actual_exit) * pos.size

        # Calculate exit fees
        exit_notional = actual_exit * pos.size
        fee = exit_notional * self.config.taker_fee

        # Calculate funding cost (every 8 bars ≈ 8 hours for 5m candles)
        bars_per_funding = 96  # 8h / 5m = 96 bars
        funding_periods = pos.bars_held / bars_per_funding if bars_per_funding > 0 else 0
        funding = pos.entry_price * pos.size * self.config.funding_rate_pct * funding_periods
        pos.funding_paid = funding

        # Trailing stop update
        if self.config.trailing_stop and pos.trailing_stop_price is not None:
            if pos.side == Side.LONG:
                new_trail = exit_price * (1 - self.config.trailing_stop_pct)
                if new_trail > pos.trailing_stop_price:
                    pos.trailing_stop_price = new_trail
            else:
                new_trail = exit_price * (1 + self.config.trailing_stop_pct)
                if new_trail < pos.trailing_stop_price:
                    pos.trailing_stop_price = new_trail

        # R-multiple
        risk = abs(pos.entry_price - pos.stop_loss) if pos.stop_loss else pos.entry_price * 0.02
        r_multiple = pnl / (risk * pos.size) if risk > 0 else 0

        hold_minutes = (exit_time - pos.entry_time).total_seconds() / 60

        trade = Trade(
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=actual_exit,
            size=pos.size,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            pnl=pnl,
            fees=pos.fees_paid + fee,
            slippage=pos.slippage_paid + (spread_cost / 2 + slippage) * pos.size,
            funding=funding,
            exit_reason=reason,
            hold_time_minutes=hold_minutes,
            bars_held=pos.bars_held,
            partial_fills=pos.partial_fills,
            r_multiple=r_multiple,
        )

        state.closed_trades.append(trade)
        state.positions.remove(pos)
        state.capital += pos.margin + pnl - fee - funding

        if pnl > 0:
            state.win_count += 1
        else:
            state.loss_count += 1

        state.total_fees += fee
        state.total_slippage += (spread_cost / 2 + slippage) * pos.size

    async def _check_exits(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
        bar_time: datetime,
    ) -> None:
        """Check stop-loss and take-profit for all open positions."""
        state = self._state
        for pos in list(state.positions):
            if pos.symbol != symbol:
                continue

            hit_sl = False
            hit_tp = False

            if pos.side == Side.LONG:
                if pos.stop_loss and low <= pos.stop_loss:
                    hit_sl = True
                if pos.take_profit and high >= pos.take_profit:
                    hit_tp = True
            else:  # SHORT
                if pos.stop_loss and high >= pos.stop_loss:
                    hit_sl = True
                if pos.take_profit and low <= pos.take_profit:
                    hit_tp = True

            if hit_sl:
                await self._close_position(pos, pos.stop_loss, bar_time, "stop_loss")
            elif hit_tp:
                await self._close_position(pos, pos.take_profit, bar_time, "take_profit")

    async def _update_trailing_stop(self, pos: Position, high: float, low: float) -> None:
        """Update trailing stop price based on price movement."""
        if pos.trailing_stop_price is None:
            return
        if pos.side == Side.LONG:
            # Move trailing stop up as price rises
            new_trail = high * (1 - self.config.trailing_stop_pct)
            if new_trail > pos.trailing_stop_price:
                pos.trailing_stop_price = new_trail
            # Check if trailing stop hit
            if low <= pos.trailing_stop_price:
                await self._close_position(pos, pos.trailing_stop_price, pos.entry_time, "trailing_stop")
        else:  # SHORT
            new_trail = low * (1 + self.config.trailing_stop_pct)
            if new_trail < pos.trailing_stop_price:
                pos.trailing_stop_price = new_trail
            if high >= pos.trailing_stop_price:
                await self._close_position(pos, pos.trailing_stop_price, pos.entry_time, "trailing_stop")

    # ── Sizing & Risk ────────────────────────────────────────────

    def _calculate_size(self, price: float, stop_loss: Optional[float], side: Side) -> float:
        """Calculate position size based on risk parameters."""
        state = self._state
        capital = state.capital + sum(p.margin for p in state.positions)

        if stop_loss and price != stop_loss:
            risk_amount = capital * self.config.risk_per_trade_pct
            risk_per_unit = abs(price - stop_loss)
            size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0
        else:
            max_notional = capital * self.config.max_position_pct * self.config.leverage
            size = max_notional / price if price > 0 else 0

        # Cap by max position size
        max_size = (capital * self.config.max_position_pct * self.config.leverage) / price
        size = min(size, max_size)

        # Ensure sufficient margin
        margin_needed = (price * size) / self.config.leverage
        if margin_needed > state.capital * 0.9:
            size = (state.capital * 0.9 * self.config.leverage) / price

        return max(size, 0)

    def _can_open_position(self, symbol: str) -> bool:
        """Check if we can open a new position."""
        state = self._state
        if len(state.positions) >= self.config.max_positions:
            return False
        if any(p.symbol == symbol for p in state.positions):
            return False
        if state.capital <= 0:
            return False
        return True

    def _calculate_equity(self, current_price: float) -> float:
        """Calculate total equity (capital + unrealized PnL)."""
        state = self._state
        equity = state.capital
        for pos in state.positions:
            if pos.side == Side.LONG:
                unrealized = (current_price - pos.entry_price) * pos.size
            else:
                unrealized = (pos.entry_price - current_price) * pos.size
            equity += pos.margin + unrealized
        return equity

    def _update_drawdown(self, equity: float) -> None:
        """Update drawdown statistics."""
        state = self._state
        if equity > state.peak_equity:
            state.peak_equity = equity
        drawdown = (state.peak_equity - equity) / state.peak_equity if state.peak_equity > 0 else 0
        state.current_drawdown = drawdown
        state.max_drawdown = max(state.max_drawdown, drawdown)

    # ── Indicators ───────────────────────────────────────────────

    def _precompute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Precompute technical indicators for strategy use."""
        df = df.copy()

        # SMA
        for p in [20, 50, 200]:
            df[f"sma_{p}"] = df["close"].rolling(p).mean()

        # EMA
        for p in [12, 26]:
            df[f"ema_{p}"] = df["close"].ewm(span=p, adjust=False).mean()

        # RSI
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df["rsi"] = 100 - (100 / (1 + gain / loss))

        # MACD
        df["macd"] = df["ema_12"] - df["ema_26"]
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

        # Bollinger Bands
        df["bb_mid"] = df["sma_20"]
        bb_std = df["close"].rolling(20).std()
        df["bb_upper"] = df["bb_mid"] + 2 * bb_std
        df["bb_lower"] = df["bb_mid"] - 2 * bb_std

        # ATR
        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift()).abs()
        lc = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df["atr"] = tr.rolling(14).mean()

        # Volume
        df["vol_sma"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_sma"]

        return df

    # ── Result Building ──────────────────────────────────────────

    def _build_result(self, symbol: str, data: pd.DataFrame) -> "BacktestResult":
        """Build comprehensive backtest result."""
        state = self._state
        trades = state.closed_trades

        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl <= 0]

        avg_win = np.mean([t.net_pnl for t in wins]) if wins else 0
        avg_loss = np.mean([abs(t.net_pnl) for t in losses]) if losses else 0

        profit_factor = sum(t.net_pnl for t in wins) / sum(abs(t.net_pnl) for t in losses) if losses else float("inf")
        expectancy = np.mean([t.net_pnl for t in trades]) if trades else 0

        # Sharpe ratio (annualized)
        equity = np.array(state.equity_curve)
        returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else np.array([0])
        sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(252 * 24 * 60 / 5)) if np.std(returns) > 0 else 0

        # Sortino ratio
        downside = returns[returns < 0]
        sortino = (np.mean(returns) / np.std(downside) * np.sqrt(252 * 24 * 60 / 5)) if len(downside) > 0 and np.std(downside) > 0 else 0

        # Max consecutive
        max_consec_wins = self._max_consecutive(trades, True)
        max_consec_losses = self._max_consecutive(trades, False)

        return BacktestResult(
            symbol=symbol,
            total_bars=len(data),
            start_time=data["open_time"].iloc[0] if not data.empty else datetime.now(),
            end_time=data["open_time"].iloc[-1] if not data.empty else datetime.now(),
            initial_capital=self.config.initial_capital,
            final_equity=state.equity_curve[-1] if state.equity_curve else self.config.initial_capital,
            net_pnl=state.equity_curve[-1] - self.config.initial_capital if state.equity_curve else 0,
            total_trades=len(trades),
            win_count=state.win_count,
            loss_count=state.loss_count,
            win_rate=(state.win_count / len(trades) * 100) if trades else 0,
            avg_win=avg_win,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            expectancy=expectancy,
            max_drawdown=state.max_drawdown * 100,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_consec_wins=max_consec_wins,
            max_consec_losses=max_consec_losses,
            avg_hold_minutes=np.mean([t.hold_time_minutes for t in trades]) if trades else 0,
            total_fees=state.total_fees,
            total_slippage=state.total_slippage,
            total_funding=sum(t.funding for t in trades),
            avg_bars_held=np.mean([t.bars_held for t in trades]) if trades else 0,
            partial_fill_count=sum(t.partial_fills for t in trades),
            trades=trades,
            equity_curve=state.equity_curve,
        )

    def _max_consecutive(self, trades: List[Trade], wins: bool) -> int:
        """Calculate max consecutive wins or losses."""
        max_c = 0
        current = 0
        for t in trades:
            if (t.net_pnl > 0) == wins:
                current += 1
                max_c = max(max_c, current)
            else:
                current = 0
        return max_c


@dataclass
class BacktestResult:
    """Comprehensive backtest result with all metrics."""
    symbol: str
    total_bars: int
    start_time: datetime
    end_time: datetime
    initial_capital: float
    final_equity: float
    net_pnl: float
    total_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expectancy: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    max_consec_wins: int
    max_consec_losses: int
    avg_hold_minutes: float
    total_fees: float
    total_slippage: float
    total_funding: float = 0.0
    avg_bars_held: float = 0.0
    partial_fill_count: int = 0
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

    def summary(self) -> str:
        """Generate human-readable summary."""
        return (
            f"═══ Backtest Result: {self.symbol} ═══\n"
            f"Period: {self.start_time} → {self.end_time}\n"
            f"Bars: {self.total_bars}\n"
            f"─────────────────────────────────\n"
            f"Capital: ${self.initial_capital:,.0f} → ${self.final_equity:,.0f}\n"
            f"PnL: ${self.net_pnl:,.2f} ({self.net_pnl / self.initial_capital * 100:+.1f}%)\n"
            f"─────────────────────────────────\n"
            f"Trades: {self.total_trades} (W:{self.win_count} / L:{self.loss_count})\n"
            f"Win Rate: {self.win_rate:.1f}%\n"
            f"Avg Win: ${self.avg_win:,.2f} | Avg Loss: ${self.avg_loss:,.2f}\n"
            f"Profit Factor: {self.profit_factor:.2f}\n"
            f"Expectancy: ${self.expectancy:,.2f}\n"
            f"─────────────────────────────────\n"
            f"Max Drawdown: {self.max_drawdown:.1f}%\n"
            f"Sharpe Ratio: {self.sharpe_ratio:.2f}\n"
            f"Sortino Ratio: {self.sortino_ratio:.2f}\n"
            f"Max Consec Wins: {self.max_consec_wins} | Losses: {self.max_consec_losses}\n"
            f"Avg Hold: {self.avg_hold_minutes:.0f} min\n"
            f"Total Fees: ${self.total_fees:,.2f}\n"
            f"Total Slippage: ${self.total_slippage:,.2f}\n"
            f"Total Funding: ${self.total_funding:,.2f}\n"
            f"Avg Bars Held: {self.avg_bars_held:.0f}\n"
            f"Partial Fills: {self.partial_fill_count}\n"
        )
