#!/usr/bin/env python3
"""
EMA_V5 Institutional-Grade Backtest Engine
==========================================
Runs full-strategy backtests on all perpetual futures symbols using real historical data.
Calculates all institutional metrics: Net Profit, PF, CAGR, MDD, Sharpe, Sortino, Calmar, etc.
Tests multiple position sizing schemes. Produces portfolio-level analysis.
"""

import sys, os, json, time, math, statistics, random
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'packages', 'ai-engine'))
os.environ['LOGURU_LEVEL'] = 'ERROR'

import numpy as np
import sqlite3

# ══════════════════════════════════════════════════════════════════════
# EMA CALCULATION
# ══════════════════════════════════════════════════════════════════════

def ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average with SMA seed."""
    result = np.full(len(data), np.nan)
    if len(data) < period:
        return result
    # SMA seed
    result[period - 1] = np.mean(data[:period])
    # EMA
    alpha = 2.0 / (period + 1)
    for i in range(period, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result

def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range."""
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    result = np.full(len(tr), np.nan)
    if len(tr) < period:
        return result
    result[period - 1] = np.mean(tr[:period])
    alpha = 2.0 / (period + 1)
    for i in range(period, len(tr)):
        result[i] = alpha * tr[i] + (1 - alpha) * result[i - 1]
    return result

# ══════════════════════════════════════════════════════════════════════
# STRATEGY ENGINE (EMA_V5 faithful implementation)
# ══════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    entry_time: int  # bar index
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    size: float  # in base asset
    risk_amount: float  # in USD
    regime: str = ""
    confidence: float = 0.0
    # Exit tracking
    exit_price: float = 0.0
    exit_time: int = 0
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    r_multiple: float = 0.0
    # Partial close tracking
    remaining_size: float = 0.0
    partial_closed: bool = False
    tp1_hit: bool = False
    tp2_hit: bool = False
    be_moved: bool = False

@dataclass
class BacktestResult:
    symbol: str
    risk_level: float
    trades: List[Trade]
    equity_curve: List[float]
    initial_capital: float
    final_capital: float
    # Metrics
    net_profit: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    avg_rr: float = 0.0
    avg_hold_bars: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    recovery_factor: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    mar_ratio: float = 0.0
    ulcer_index: float = 0.0
    equity_slope: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_streak: int = 0
    losing_streak: int = 0
    exposure_pct: float = 0.0
    avg_hold_hours: float = 0.0
    # Market regime breakdown
    bull_trades: int = 0
    bear_trades: int = 0
    sideways_trades: int = 0
    buy_trades: int = 0
    sell_trades: int = 0


class EMAv5Backtester:
    """Institutional backtest engine faithful to EMA_V5 strategy logic."""

    def __init__(self, capital: float = 10000.0, commission_bps: float = 4.0,
                 slippage_bps: float = 2.0, funding_rate_annual: float = 0.01):
        self.initial_capital = capital
        self.commission_bps = commission_bps
        self.slippage_bps = slippage_bps
        self.funding_rate_annual = funding_rate_annual

        # Strategy parameters (from config)
        self.ema_periods = [20, 50, 144, 200]
        self.slope_lookback = 5
        self.touch_tolerance_pct = 0.3
        self.max_pullback_pct = 2.0
        self.body_ratio_min = 0.5
        self.wick_ratio_min = 2.0
        self.volume_sma_period = 20
        self.min_volume_ratio = 1.0
        self.min_confidence = 90.0
        self.sl_atr_mult = 1.5
        self.tp1_rr = 1.5
        self.tp2_rr = 3.0
        self.tp3_rr = 5.0
        self.tp1_exit_pct = 0.35
        self.tp2_exit_pct = 0.40
        self.tp3_exit_pct = 0.25
        self.max_hold_bars = 48  # 48 hours at 1h
        self.breakeven_at_r = 1.0
        self.trailing_atr_mult = 1.0
        self.cooldown_bars = 6  # 1 hour at 1h

    def run_backtest(self, symbol: str, klines: np.ndarray,
                     risk_pct: float = 1.0) -> BacktestResult:
        """
        Run full backtest on one symbol.
        klines: shape (N, 6) with columns [open_time, open, high, low, close, volume]
        """
        n = len(klines)
        if n < 220:
            return BacktestResult(symbol=symbol, risk_level=risk_pct, trades=[],
                                  equity_curve=[], initial_capital=self.initial_capital,
                                  final_capital=self.initial_capital)

        opens = klines[:, 1].astype(float)
        highs = klines[:, 2].astype(float)
        lows = klines[:, 3].astype(float)
        closes = klines[:, 4].astype(float)
        volumes = klines[:, 5].astype(float)

        # Compute indicators
        ema20 = ema(closes, 20)
        ema50 = ema(closes, 50)
        ema144 = ema(closes, 144)
        ema200 = ema(closes, 200)
        atr14 = atr(highs, lows, closes, 14)
        vol_sma = ema(volumes, 20)  # Using EMA as smoothed SMA approximation

        # Equity tracking
        capital = self.initial_capital
        equity_curve = [capital]
        trades: List[Trade] = []
        open_trade: Optional[Trade] = None
        last_signal_bar = -999

        # Start from bar 220 (EMA200 warmup)
        for i in range(220, n):
            # ── Check open trade management first ──
            if open_trade is not None:
                open_trade = self._manage_trade(
                    open_trade, i, highs[i], lows[i], closes[i], atr14[i]
                )
                if open_trade is not None and open_trade.exit_price > 0:
                    # Trade closed
                    self._close_trade(open_trade, closes[i], i)
                    capital += open_trade.pnl
                    trades.append(open_trade)
                    open_trade = None
                elif open_trade is not None:
                    # Check max hold
                    if i - open_trade.entry_time >= self.max_hold_bars:
                        open_trade.exit_price = closes[i]
                        open_trade.exit_time = i
                        open_trade.exit_reason = "MAX_HOLD"
                        self._close_trade(open_trade, closes[i], i)
                        capital += open_trade.pnl
                        trades.append(open_trade)
                        open_trade = None
                    else:
                        equity_curve.append(capital + self._unrealized_pnl(open_trade, closes[i]))
                        continue

            if open_trade is not None:
                equity_curve.append(capital + self._unrealized_pnl(open_trade, closes[i]))
                continue

            # ── Cooldown check ──
            if i - last_signal_bar < self.cooldown_bars:
                equity_curve.append(capital)
                continue

            # ── Check for valid data ──
            if any(np.isnan(x) for x in [ema20[i], ema50[i], ema144[i], ema200[i], atr14[i]]):
                equity_curve.append(capital)
                continue

            # ── Stage 1: Regime Classification ──
            regime = self._classify_regime(closes[i], ema20[i], ema50[i], ema144[i], ema200[i])
            if regime == "NO_TREND":
                equity_curve.append(capital)
                continue

            # ── Stage 2: Trend Analysis ──
            trend_ok, direction = self._check_trend(
                closes[i], ema20[i], ema50[i], ema144[i], ema200[i],
                ema20[max(0,i-self.slope_lookback):i+1] if i >= self.slope_lookback else None,
                ema50[max(0,i-self.slope_lookback):i+1] if i >= self.slope_lookback else None,
            )
            if not trend_ok:
                equity_curve.append(capital)
                continue

            # ── Stage 3: Pullback Detection ──
            pullback_ok = self._check_pullback(
                closes[i], lows[i], highs[i], ema20[i], ema50[i], regime
            )
            if not pullback_ok:
                equity_curve.append(capital)
                continue

            # ── Stage 4: Candlestick Pattern ──
            candle_ok = self._check_candle(opens[i], highs[i], lows[i], closes[i], regime)
            if not candle_ok:
                equity_curve.append(capital)
                continue

            # ── Stage 5: Volume Confirmation ──
            volume_ok = self._check_volume(volumes[i], vol_sma[i]) if not np.isnan(vol_sma[i]) else False
            if not volume_ok:
                equity_curve.append(capital)
                continue

            # ── Stage 6: Confidence Scoring ──
            confidence = self._compute_confidence(
                closes[i], ema20[i], ema50[i], ema144[i], ema200[i],
                volumes[i], vol_sma[i] if not np.isnan(vol_sma[i]) else 0,
                regime, trend_ok, pullback_ok, candle_ok, volume_ok
            )

            if confidence < self.min_confidence:
                equity_curve.append(capital)
                continue

            # ── Generate Signal ──
            entry_price = closes[i]
            current_atr = atr14[i]
            if current_atr <= 0:
                equity_curve.append(capital)
                continue

            # Position sizing
            risk_amount = capital * (risk_pct / 100.0)
            sl_distance = current_atr * self.sl_atr_mult
            size = risk_amount / sl_distance  # base asset units

            # Apply slippage
            slippage = entry_price * (self.slippage_bps / 10000)

            if direction == "LONG":
                actual_entry = entry_price + slippage
                stop_loss = entry_price - sl_distance
                tp1 = entry_price + sl_distance * self.tp1_rr
                tp2 = entry_price + sl_distance * self.tp2_rr
                tp3 = entry_price + sl_distance * self.tp3_rr
                side = "LONG"
            else:  # SHORT
                actual_entry = entry_price - slippage
                stop_loss = entry_price + sl_distance
                tp1 = entry_price - sl_distance * self.tp1_rr
                tp2 = entry_price - sl_distance * self.tp2_rr
                tp3 = entry_price - sl_distance * self.tp3_rr
                side = "SHORT"

            # Commission
            commission = actual_entry * size * (self.commission_bps / 10000)
            capital -= commission

            open_trade = Trade(
                symbol=symbol,
                side=side,
                entry_price=actual_entry,
                entry_time=i,
                stop_loss=stop_loss,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                size=size,
                risk_amount=risk_amount,
                regime=regime,
                confidence=confidence,
                remaining_size=size,
            )
            last_signal_bar = i

            equity_curve.append(capital + self._unrealized_pnl(open_trade, closes[i]))

        # Close any remaining open trade at last price
        if open_trade is not None and open_trade.exit_price == 0:
            open_trade.exit_price = closes[-1]
            open_trade.exit_time = n - 1
            open_trade.exit_reason = "END_OF_DATA"
            self._close_trade(open_trade, closes[-1], n - 1)
            capital += open_trade.pnl
            trades.append(open_trade)

        result = BacktestResult(
            symbol=symbol,
            risk_level=risk_pct,
            trades=trades,
            equity_curve=equity_curve,
            initial_capital=self.initial_capital,
            final_capital=capital,
        )
        self._compute_metrics(result, n)
        return result

    def _classify_regime(self, close, ema20, ema50, ema144, ema200) -> str:
        """Classify market regime based on EMA chain."""
        if ema20 > ema50 > ema144 > ema200:
            return "BUY_MODE"
        elif ema20 < ema50 < ema144 < ema200:
            return "SELL_MODE"
        elif ema144 > ema200 and close > ema144:
            return "BUY_MODE"  # partial bullish
        elif ema144 < ema200 and close < ema144:
            return "SELL_MODE"  # partial bearish
        return "NO_TREND"

    def _check_trend(self, close, ema20, ema50, ema144, ema200,
                     ema20_window=None, ema50_window=None) -> Tuple[bool, str]:
        """Check trend direction and strength."""
        if ema20 > ema50 and close > ema144:
            direction = "LONG"
        elif ema20 < ema50 and close < ema144:
            direction = "SHORT"
        else:
            return False, ""

        # Slope check
        if ema20_window is not None and len(ema20_window) >= 2:
            slope = ema20_window[-1] - ema20_window[0]
            if direction == "LONG" and slope <= 0:
                return False, ""
            if direction == "SHORT" and slope >= 0:
                return False, ""

        return True, direction

    def _check_pullback(self, close, low, high, ema20, ema50, regime) -> bool:
        """Check for pullback to EMA zone."""
        # Price should have been near EMA zone recently
        tolerance = close * (self.touch_tolerance_pct / 100)
        ema_zone = (ema20 + ema50) / 2

        if regime == "BUY_MODE":
            # Pullback means price dipped toward EMA20/50
            dist_to_zone = close - ema_zone
            if 0 <= dist_to_zone <= tolerance * 3:
                return True
            # Also accept if low touched EMA zone
            if abs(low - ema_zone) < tolerance * 2:
                return True
        elif regime == "SELL_MODE":
            dist_to_zone = ema_zone - close
            if 0 <= dist_to_zone <= tolerance * 3:
                return True
            if abs(high - ema_zone) < tolerance * 2:
                return True

        return False

    def _check_candle(self, open_p, high, low, close, regime) -> bool:
        """Check for candlestick confirmation pattern."""
        candle_range = high - low
        if candle_range <= 0:
            return False

        body = abs(close - open_p)
        body_ratio = body / candle_range

        if regime == "BUY_MODE":
            # Bullish engulfing or strong bullish candle
            if close > open_p and body_ratio >= self.body_ratio_min:
                return True
            # Hammer / pin bar at support
            lower_wick = min(open_p, close) - low
            if lower_wick > body * self.wick_ratio_min and close > open_p:
                return True
        elif regime == "SELL_MODE":
            # Bearish engulfing or strong bearish candle
            if close < open_p and body_ratio >= self.body_ratio_min:
                return True
            # Shooting star / inverted pin bar at resistance
            upper_wick = high - max(open_p, close)
            if upper_wick > body * self.wick_ratio_min and close < open_p:
                return True

        return False

    def _check_volume(self, volume, vol_sma) -> bool:
        """Volume above average."""
        if vol_sma <= 0:
            return False
        return volume >= vol_sma * self.min_volume_ratio

    def _compute_confidence(self, close, ema20, ema50, ema144, ema200,
                           vol, vol_sma, regime, trend_ok, pullback_ok, candle_ok, volume_ok) -> float:
        """Compute confidence score (0-100)."""
        score = 0.0

        # Trend score (0-25)
        if trend_ok:
            alignment = 0
            if ema20 > ema50: alignment += 1
            if ema50 > ema144: alignment += 1
            if ema144 > ema200: alignment += 1
            score += (alignment / 3) * 25

        # Pullback score (0-25)
        if pullback_ok:
            score += 25

        # Candle score (0-20)
        if candle_ok:
            score += 20

        # Volume score (0-15)
        if volume_ok and vol_sma > 0:
            vol_ratio = vol / vol_sma
            score += min(15, (vol_ratio - 1.0) * 30)

        # Regime score (0-15)
        if regime in ("BUY_MODE", "SELL_MODE"):
            score += 15

        return score

    def _manage_trade(self, trade: Trade, bar_idx: int, high: float, low: float,
                      close: float, current_atr: float) -> Optional[Trade]:
        """Manage open trade: check TP/SL/BE/Trailing."""
        if trade.exit_price > 0:
            return trade  # Already closed

        side = trade.side
        entry = trade.entry_price
        sl = trade.stop_loss

        if side == "LONG":
            # Check SL
            if low <= sl:
                trade.exit_price = sl
                trade.exit_time = bar_idx
                trade.exit_reason = "STOP_LOSS"
                return trade

            # Check TP1
            if not trade.tp1_hit and high >= trade.tp1:
                trade.tp1_hit = True
                # Partial close at TP1
                partial_size = trade.remaining_size * self.tp1_exit_pct
                partial_pnl = (trade.tp1 - entry) * partial_size
                trade.pnl += partial_pnl
                trade.remaining_size -= partial_size

            # Check TP2
            if trade.tp1_hit and not trade.tp2_hit and high >= trade.tp2:
                trade.tp2_hit = True
                partial_size = trade.remaining_size * (self.tp2_exit_pct / (1 - self.tp1_exit_pct))
                partial_pnl = (trade.tp2 - entry) * partial_size
                trade.pnl += partial_pnl
                trade.remaining_size -= partial_size

            # Move SL to breakeven
            if not trade.be_moved and trade.tp1_hit:
                r_distance = entry - sl
                if high >= entry + r_distance * self.breakeven_at_r:
                    trade.stop_loss = entry
                    trade.be_moved = True

            # Trailing stop
            if trade.tp1_hit and current_atr > 0:
                trail_level = high - current_atr * self.trailing_atr_mult
                if trail_level > trade.stop_loss:
                    trade.stop_loss = trail_level

            # Check TP3 (full close)
            if trade.tp2_hit and high >= trade.tp3:
                partial_pnl = (trade.tp3 - entry) * trade.remaining_size
                trade.pnl += partial_pnl
                trade.remaining_size = 0
                trade.exit_price = trade.tp3
                trade.exit_time = bar_idx
                trade.exit_reason = "TP3"
                return trade

        else:  # SHORT
            # Check SL
            if high >= sl:
                trade.exit_price = sl
                trade.exit_time = bar_idx
                trade.exit_reason = "STOP_LOSS"
                return trade

            # Check TP1
            if not trade.tp1_hit and low <= trade.tp1:
                trade.tp1_hit = True
                partial_size = trade.remaining_size * self.tp1_exit_pct
                partial_pnl = (entry - trade.tp1) * partial_size
                trade.pnl += partial_pnl
                trade.remaining_size -= partial_size

            # Check TP2
            if trade.tp1_hit and not trade.tp2_hit and low <= trade.tp2:
                trade.tp2_hit = True
                partial_size = trade.remaining_size * (self.tp2_exit_pct / (1 - self.tp1_exit_pct))
                partial_pnl = (entry - trade.tp2) * partial_size
                trade.pnl += partial_pnl
                trade.remaining_size -= partial_size

            # Move SL to breakeven
            if not trade.be_moved and trade.tp1_hit:
                r_distance = sl - entry
                if low <= entry - r_distance * self.breakeven_at_r:
                    trade.stop_loss = entry
                    trade.be_moved = True

            # Trailing stop
            if trade.tp1_hit and current_atr > 0:
                trail_level = low + current_atr * self.trailing_atr_mult
                if trail_level < trade.stop_loss:
                    trade.stop_loss = trail_level

            # Check TP3
            if trade.tp2_hit and low <= trade.tp3:
                partial_pnl = (entry - trade.tp3) * trade.remaining_size
                trade.pnl += partial_pnl
                trade.remaining_size = 0
                trade.exit_price = trade.tp3
                trade.exit_time = bar_idx
                trade.exit_reason = "TP3"
                return trade

        return trade

    def _unrealized_pnl(self, trade: Trade, close: float) -> float:
        """Calculate unrealized PnL for equity curve."""
        if trade.exit_price > 0:
            return 0
        if trade.side == "LONG":
            return (close - trade.entry_price) * trade.remaining_size
        else:
            return (trade.entry_price - close) * trade.remaining_size

    def _close_trade(self, trade: Trade, final_price: float, bar_idx: int):
        """Finalize trade PnL."""
        if trade.exit_price == 0:
            trade.exit_price = final_price
            trade.exit_time = bar_idx

        if trade.side == "LONG":
            remaining_pnl = (trade.exit_price - trade.entry_price) * trade.remaining_size
        else:
            remaining_pnl = (trade.entry_price - trade.exit_price) * trade.remaining_size

        trade.pnl += remaining_pnl
        trade.pnl_pct = trade.pnl / trade.risk_amount * 100
        sl_dist = abs(trade.entry_price - trade.stop_loss)
        if sl_dist > 0:
            trade.r_multiple = trade.pnl / (sl_dist * trade.size)
        trade.avg_hold_bars = bar_idx - trade.entry_time

    def _compute_metrics(self, result: BacktestResult, total_bars: int):
        """Compute all institutional metrics."""
        trades = result.trades
        n = len(trades)
        result.total_trades = n

        if n == 0:
            return

        pnls = [t.pnl for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        result.net_profit = sum(pnls)
        result.gross_profit = sum(winners) if winners else 0
        result.gross_loss = abs(sum(losers)) if losers else 0
        result.profit_factor = result.gross_profit / result.gross_loss if result.gross_loss > 0 else float('inf')
        result.expectancy = statistics.mean(pnls) if pnls else 0
        result.avg_winner = statistics.mean(winners) if winners else 0
        result.avg_loser = statistics.mean(losers) if losers else 0
        result.win_rate = len(winners) / n * 100

        # R-multiples
        r_multiples = [t.r_multiple for t in trades if t.r_multiple != 0]
        result.avg_rr = statistics.mean(r_multiples) if r_multiples else 0

        # Hold time
        hold_bars = [t.avg_hold_bars for t in trades]
        result.avg_hold_hours = statistics.mean(hold_bars) if hold_bars else 0

        # Equity curve metrics
        ec = result.equity_curve
        if len(ec) > 1:
            ec_arr = np.array(ec)
            # Max drawdown
            peak = np.maximum.accumulate(ec_arr)
            drawdown = (peak - ec_arr)
            dd_pct = drawdown / np.where(peak > 0, peak, 1) * 100
            result.max_drawdown = float(np.max(drawdown))
            result.max_drawdown_pct = float(np.max(dd_pct))

            # Recovery factor
            result.recovery_factor = result.net_profit / result.max_drawdown if result.max_drawdown > 0 else float('inf')

            # CAGR
            years = total_bars / (365.25 * 24)  # 1h bars
            if years > 0 and result.final_capital > 0:
                cagr = (result.final_capital / result.initial_capital) ** (1 / years) - 1
                result.calmar = cagr / (result.max_drawdown_pct / 100) if result.max_drawdown_pct > 0 else float('inf')
                result.mar_ratio = cagr  # simplified MAR = CAGR / MaxDD
            else:
                cagr = 0

            # Sharpe (annualized)
            returns = np.diff(ec_arr) / ec_arr[:-1]
            returns = returns[~np.isnan(returns) & ~np.isinf(returns)]
            if len(returns) > 1:
                mean_r = np.mean(returns)
                std_r = np.std(returns)
                result.sharpe = float(mean_r / std_r * np.sqrt(365.25 * 24)) if std_r > 0 else 0

                # Sortino
                neg_returns = returns[returns < 0]
                if len(neg_returns) > 1:
                    down_std = np.std(neg_returns)
                    result.sortino = float(mean_r / down_std * np.sqrt(365.25 * 24)) if down_std > 0 else 0

            # Ulcer Index
            if len(dd_pct) > 1:
                result.ulcer_index = float(np.sqrt(np.mean(dd_pct ** 2)))

            # Equity curve slope (linear regression)
            if len(ec_arr) > 1:
                x = np.arange(len(ec_arr))
                slope = np.polyfit(x, ec_arr, 1)[0]
                result.equity_slope = float(slope)

        # Winning/losing streaks
        streak = 0
        max_win_streak = 0
        max_lose_streak = 0
        for p in pnls:
            if p > 0:
                if streak >= 0:
                    streak += 1
                else:
                    streak = 1
                max_win_streak = max(max_win_streak, streak)
            else:
                if streak <= 0:
                    streak -= 1
                else:
                    streak = -1
                max_lose_streak = max(max_lose_streak, abs(streak))
        result.winning_streak = max_win_streak
        result.losing_streak = max_lose_streak

        # Exposure
        total_hold = sum(t.avg_hold_bars for t in trades)
        result.exposure_pct = total_hold / total_bars * 100 if total_bars > 0 else 0

        # Buy/Sell breakdown
        result.buy_trades = sum(1 for t in trades if t.side == "LONG")
        result.sell_trades = sum(1 for t in trades if t.side == "SHORT")


# ══════════════════════════════════════════════════════════════════════
# ROBUSTNESS TESTS
# ══════════════════════════════════════════════════════════════════════

def monte_carlo_simulation(trades: List[Trade], n_simulations: int = 1000,
                           initial_capital: float = 10000.0) -> Dict:
    """Monte Carlo simulation by shuffling trade order."""
    pnls = [t.pnl for t in trades]
    if len(pnls) < 5:
        return {"error": "insufficient trades"}

    final_capitals = []
    max_dds = []
    for _ in range(n_simulations):
        shuffled = random.sample(pnls, len(pnls))
        equity = [initial_capital]
        for p in shuffled:
            equity.append(equity[-1] + p)
        final_capitals.append(equity[-1])
        peak = max(equity)
        dd = max(peak - e for e in equity)
        max_dds.append(dd / peak * 100 if peak > 0 else 0)

    return {
        "simulations": n_simulations,
        "median_final": float(np.median(final_capitals)),
        "p5_final": float(np.percentile(final_capitals, 5)),
        "p95_final": float(np.percentile(final_capitals, 95)),
        "median_max_dd": float(np.median(max_dds)),
        "p95_max_dd": float(np.percentile(max_dds, 95)),
        "profit_probability": float(sum(1 for c in final_capitals if c > initial_capital) / n_simulations),
    }


def walk_forward_analysis(symbol: str, klines: np.ndarray, n_folds: int = 5,
                         risk_pct: float = 1.0) -> Dict:
    """Simple walk-forward: train on first portion, test on remaining."""
    n = len(klines)
    fold_size = n // n_folds
    results = []

    for fold in range(1, n_folds):
        # Out-of-sample test period
        test_start = fold * fold_size
        test_end = min((fold + 1) * fold_size, n)
        test_data = klines[test_start:test_end]

        if len(test_data) < 250:
            continue

        bt = EMAv5Backtester(capital=10000)
        result = bt.run_backtest(f"{symbol}_WF{fold}", test_data, risk_pct)
        results.append({
            "fold": fold,
            "trades": result.total_trades,
            "net_profit": result.net_profit,
            "profit_factor": result.profit_factor,
            "max_drawdown_pct": result.max_drawdown_pct,
            "expectancy": result.expectancy,
        })

    # Consistency
    profitable_folds = sum(1 for r in results if r["net_profit"] > 0)
    avg_pf = statistics.mean([r["profit_factor"] for r in results if r["profit_factor"] < float('inf')]) if results else 0

    return {
        "folds": results,
        "profitable_folds": profitable_folds,
        "total_folds": len(results),
        "consistency": profitable_folds / len(results) * 100 if results else 0,
        "avg_profit_factor": avg_pf,
    }


def parameter_sensitivity(symbol: str, klines: np.ndarray, risk_pct: float = 1.0) -> Dict:
    """Test strategy sensitivity to key parameter changes."""
    base = EMAv5Backtester(capital=10000)
    base_result = base.run_backtest(symbol, klines, risk_pct)
    base_pf = base_result.profit_factor
    base_profit = base_result.net_profit

    variations = {}

    # SL multiplier variations
    for mult in [1.0, 1.25, 1.5, 1.75, 2.0]:
        bt = EMAv5Backtester(capital=10000)
        bt.sl_atr_mult = mult
        r = bt.run_backtest(f"{symbol}_SL{mult}", klines, risk_pct)
        variations[f"sl_atr_{mult}"] = {"pf": r.profit_factor, "profit": r.net_profit, "trades": r.total_trades}

    # TP1 RR variations
    for rr in [1.0, 1.5, 2.0, 2.5, 3.0]:
        bt = EMAv5Backtester(capital=10000)
        bt.tp1_rr = rr
        r = bt.run_backtest(f"{symbol}_TP1_{rr}", klines, risk_pct)
        variations[f"tp1_rr_{rr}"] = {"pf": r.profit_factor, "profit": r.net_profit, "trades": r.total_trades}

    # Confidence threshold variations
    for conf in [80, 85, 90, 95]:
        bt = EMAv5Backtester(capital=10000)
        bt.min_confidence = conf
        r = bt.run_backtest(f"{symbol}_CONF{conf}", klines, risk_pct)
        variations[f"min_conf_{conf}"] = {"pf": r.profit_factor, "profit": r.net_profit, "trades": r.total_trades}

    return {"base_pf": base_pf, "base_profit": base_profit, "variations": variations}


# ══════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 80)
    print("EMA_V5 INSTITUTIONAL-GRADE BACKTEST")
    print("ALL PERPETUAL FUTURES — REAL HISTORICAL DATA")
    print("=" * 80)

    # Load data
    db_path = os.path.join(os.path.dirname(__file__), 'data', 'database', 'historical_klines.db')
    if not os.path.exists(db_path):
        db_path = os.path.join(os.path.dirname(__file__), 'packages', 'ai-engine', 'data', 'database', 'historical_klines.db')
    
    conn = sqlite3.connect(db_path)
    symbols = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM klines WHERE interval='1h' ORDER BY symbol"
    ).fetchall()]

    print(f"\n  Symbols: {len(symbols)}")
    print(f"  Interval: 1h")
    print(f"  Risk Levels: 0.25%, 0.5%, 1%, 2%")
    print(f"  Initial Capital: $10,000")
    print(f"  Commission: 4 bps")
    print(f"  Slippage: 2 bps")

    risk_levels = [0.25, 0.5, 1.0, 2.0]
    all_results = {}  # (symbol, risk) -> BacktestResult

    for risk in risk_levels:
        print(f"\n{'─' * 80}")
        print(f"  RISK LEVEL: {risk}%")
        print(f"{'─' * 80}")
        for sym in symbols:
            rows = conn.execute(
                "SELECT open_time, open, high, low, close, volume FROM klines WHERE symbol=? AND interval='1h' ORDER BY open_time ASC",
                (sym,)
            ).fetchall()
            klines = np.array(rows, dtype=float)

            bt = EMAv5Backtester(capital=10000)
            result = bt.run_backtest(sym, klines, risk)
            all_results[(sym, risk)] = result

            pf_str = f"{result.profit_factor:.2f}" if result.profit_factor < 999 else "INF"
            print(f"  {sym:<12} trades={result.total_trades:>3}  "
                  f"net=${result.net_profit:>8.2f}  "
                  f"PF={pf_str:>6}  "
                  f"MDD={result.max_drawdown_pct:>5.1f}%  "
                  f"Sharpe={result.sharpe:>5.2f}  "
                  f"WR={result.win_rate:>5.1f}%  "
                  f"E=${result.expectancy:>6.2f}")

    conn.close()

    # ════════════════════════════════════════════════════════════════
    # PORTFOLIO ANALYSIS (at 1% risk)
    # ════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("PORTFOLIO ANALYSIS — 1% Risk Level")
    print(f"{'=' * 80}")

    results_1pct = [(sym, all_results[(sym, 1.0)]) for sym in symbols]
    results_1pct.sort(key=lambda x: x[1].net_profit, reverse=True)

    # Top 20
    print(f"\n  TOP {min(20, len(results_1pct))} MOST PROFITABLE SYMBOLS:")
    print(f"  {'Rank':<5} {'Symbol':<12} {'NetProfit':>12} {'PF':>8} {'MDD%':>7} {'Sharpe':>8} {'Sortino':>8} {'Calmar':>8} {'Trades':>7} {'WR%':>6} {'Expct':>8}")
    print(f"  {'─' * 105}")
    for rank, (sym, r) in enumerate(results_1pct[:20], 1):
        pf_str = f"{r.profit_factor:.2f}" if r.profit_factor < 999 else "INF"
        print(f"  {rank:<5} {sym:<12} ${r.net_profit:>10.2f} {pf_str:>8} {r.max_drawdown_pct:>6.1f}% "
              f"{r.sharpe:>7.2f} {r.sortino:>7.2f} {r.calmar:>7.2f} {r.total_trades:>7} {r.win_rate:>5.1f}% {r.expectancy:>7.2f}")

    # Worst 20
    print(f"\n  WORST 20 SYMBOLS:")
    for rank, (sym, r) in enumerate(reversed(results_1pct[-20:]), 1):
        pf_str = f"{r.profit_factor:.2f}" if r.profit_factor < 999 else "INF"
        print(f"  {rank:<5} {sym:<12} ${r.net_profit:>10.2f} {pf_str:>8} {r.max_drawdown_pct:>6.1f}% "
              f"{r.sharpe:>7.2f} {r.sortino:>7.2f} {r.calmar:>7.2f} {r.total_trades:>7} {r.win_rate:>5.1f}% {r.expectancy:>7.2f}")

    # Risk level comparison
    print(f"\n{'=' * 80}")
    print("RISK LEVEL COMPARISON — AGGREGATE PORTFOLIO")
    print(f"{'=' * 80}")
    for risk in risk_levels:
        results_r = [all_results[(sym, risk)] for sym in symbols]
        total_profit = sum(r.net_profit for r in results_r)
        total_trades = sum(r.total_trades for r in results_r)
        profitable = sum(1 for r in results_r if r.net_profit > 0)
        avg_pf = statistics.mean([r.profit_factor for r in results_r if r.profit_factor < 999]) if any(r.profit_factor < 999 for r in results_r) else 999
        avg_sharpe = statistics.mean([r.sharpe for r in results_r])
        max_dd = max(r.max_drawdown_pct for r in results_r) if results_r else 0
        print(f"\n  Risk {risk}%:")
        print(f"    Total Net Profit:   ${total_profit:>12.2f}")
        print(f"    Total Trades:       {total_trades:>8}")
        print(f"    Profitable Symbols: {profitable}/{len(symbols)}")
        print(f"    Avg PF:             {avg_pf:>8.2f}")
        print(f"    Avg Sharpe:         {avg_sharpe:>8.2f}")
        print(f"    Worst MDD:          {max_dd:>8.1f}%")

    # ════════════════════════════════════════════════════════════════
    # ROBUSTNESS TESTS (on top 5 symbols at 1% risk)
    # ════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("ROBUSTNESS TESTS")
    print(f"{'=' * 80}")

    top5 = [sym for sym, _ in results_1pct[:5]]

    for sym in top5:
        print(f"\n  ── {sym} ──")
        
        # Monte Carlo
        r = all_results[(sym, 1.0)]
        if r.total_trades >= 5:
            mc = monte_carlo_simulation(r.trades, n_simulations=500)
            if "error" not in mc:
                print(f"    Monte Carlo (500 sims):")
                print(f"      Median Final:     ${mc['median_final']:>10.2f}")
                print(f"      P5 Final:         ${mc['p5_final']:>10.2f}")
                print(f"      P95 Final:        ${mc['p95_final']:>10.2f}")
                print(f"      Median MaxDD:     {mc['median_max_dd']:>8.1f}%")
                print(f"      P95 MaxDD:        {mc['p95_max_dd']:>8.1f}%")
                print(f"      Profit Prob:      {mc['profit_probability']*100:>8.1f}%")

        # Walk Forward
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT open_time, open, high, low, close, volume FROM klines WHERE symbol=? AND interval='1h' ORDER BY open_time ASC",
            (sym,)
        ).fetchall()
        klines = np.array(rows, dtype=float)
        conn.close()

        wf = walk_forward_analysis(sym, klines, n_folds=5, risk_pct=1.0)
        print(f"    Walk Forward (5 folds):")
        print(f"      Profitable:       {wf['profitable_folds']}/{wf['total_folds']} ({wf['consistency']:.0f}%)")
        print(f"      Avg PF:           {wf['avg_profit_factor']:.2f}")
        for fold_r in wf['folds']:
            print(f"      Fold {fold_r['fold']}: trades={fold_r['trades']} profit=${fold_r['net_profit']:.2f} PF={fold_r['profit_factor']:.2f}")

        # Parameter Sensitivity
        ps = parameter_sensitivity(sym, klines, risk_pct=1.0)
        print(f"    Parameter Sensitivity (base PF={ps['base_pf']:.2f}):")
        for key, val in sorted(ps['variations'].items()):
            delta = val['pf'] - ps['base_pf']
            arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
            print(f"      {key:<20s} PF={val['pf']:>6.2f} ({arrow}{abs(delta):.2f}) profit=${val['profit']:>8.2f}")

    # ════════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print("FINAL PORTFOLIO SUMMARY — 1% Risk")
    print(f"{'=' * 80}")

    all_r = [all_results[(sym, 1.0)] for sym in symbols]
    total_profit = sum(r.net_profit for r in all_r)
    total_trades = sum(r.total_trades for r in all_r)
    profitable_syms = [r for r in all_r if r.net_profit > 0]
    loss_syms = [r for r in all_r if r.net_profit <= 0]
    avg_pf = statistics.mean([r.profit_factor for r in all_r if r.profit_factor < 999]) if any(r.profit_factor < 999 for r in all_r) else 999
    avg_sharpe = statistics.mean([r.sharpe for r in all_r])
    avg_sortino = statistics.mean([r.sortino for r in all_r])
    avg_calmar = statistics.mean([r.calmar for r in all_r if r.calmar < 100 and r.calmar > -100]) if all_r else 0
    avg_exp = statistics.mean([r.expectancy for r in all_r])
    max_dd = max(r.max_drawdown_pct for r in all_r) if all_r else 0

    # Approved symbols
    approved = [r for r in all_r if (
        r.profit_factor >= 2.0 and
        r.expectancy > 0 and
        r.recovery_factor >= 3 and
        r.max_drawdown_pct <= 15 and
        r.net_profit > 0 and
        r.sharpe > 1.5
    )]

    print(f"""
  ── AGGREGATE METRICS ──
  Total Symbols Tested:   {len(symbols)}
  Total Trades:           {total_trades}
  Total Net Profit:       ${total_profit:>12.2f}
  Profitable Symbols:     {len(profitable_syms)}/{len(symbols)}
  Losing Symbols:         {len(loss_syms)}/{len(symbols)}

  ── AVERAGE METRICS (across all symbols) ──
  Profit Factor:          {avg_pf:.2f}
  Expectancy:             ${avg_exp:.2f}
  Sharpe Ratio:           {avg_sharpe:.2f}
  Sortino Ratio:          {avg_sortino:.2f}
  Calmar Ratio:           {avg_calmar:.2f}
  Worst MaxDD:            {max_dd:.1f}%

  ── APPROVAL CRITERIA ──
  PF ≥ 2.0:               {'✅ PASS' if avg_pf >= 2.0 else '❌ FAIL'} ({avg_pf:.2f})
  Expectancy > 0:         {'✅ PASS' if avg_exp > 0 else '❌ FAIL'} (${avg_exp:.2f})
  MaxDD ≤ 15%:            {'✅ PASS' if max_dd <= 15 else '❌ FAIL'} ({max_dd:.1f}%)
  Sharpe > 1.5:           {'✅ PASS' if avg_sharpe > 1.5 else '❌ FAIL'} ({avg_sharpe:.2f})
  Sortino > 2:            {'✅ PASS' if avg_sortino > 2 else '❌ FAIL'} ({avg_sortino:.2f})
  Calmar > 2:             {'✅ PASS' if avg_calmar > 2 else '❌ FAIL'} ({avg_calmar:.2f})

  ── APPROVED SYMBOLS FOR LIVE TRADING ──
  {len(approved)}/{len(symbols)} symbols approved
""")
    for r in sorted(approved, key=lambda x: x.net_profit, reverse=True):
        print(f"    ✅ {r.symbol:<12} PF={r.profit_factor:.2f}  Net=${r.net_profit:.2f}  MDD={r.max_drawdown_pct:.1f}%  Sharpe={r.sharpe:.2f}")

    if len(approved) < len(symbols):
        rejected_syms = [r for r in all_r if r not in approved]
        print(f"\n  ── REJECTED SYMBOLS ──")
        for r in sorted(rejected_syms, key=lambda x: x.net_profit):
            reasons = []
            if r.profit_factor < 2.0: reasons.append(f"PF={r.profit_factor:.2f}<2.0")
            if r.expectancy <= 0: reasons.append(f"E≤0")
            if r.recovery_factor < 3: reasons.append(f"RF={r.recovery_factor:.2f}<3")
            if r.max_drawdown_pct > 15: reasons.append(f"MDD={r.max_drawdown_pct:.1f}%>15%")
            if r.net_profit <= 0: reasons.append("Net≤0")
            if r.sharpe <= 1.5: reasons.append(f"Sharpe={r.sharpe:.2f}<1.5")
            print(f"    ❌ {r.symbol:<12} {', '.join(reasons)}")

    # Deployment decision
    overall_pass = avg_pf >= 2.0 and avg_exp > 0 and max_dd <= 15 and avg_sharpe > 1.5 and total_profit > 0 and len(approved) >= 3

    print(f"\n{'=' * 80}")
    if overall_pass:
        print(f"  🟢 DEPLOYMENT DECISION: GO LIVE")
        print(f"  {len(approved)} symbols approved for live trading")
    else:
        print(f"  🔴 DEPLOYMENT DECISION: REJECT")
        print(f"  Strategy does not meet institutional approval criteria")
    print(f"{'=' * 80}")

    # Save results
    output = {
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
        "symbols_tested": len(symbols),
        "total_trades": total_trades,
        "total_net_profit": total_profit,
        "avg_pf": avg_pf,
        "avg_sharpe": avg_sharpe,
        "avg_sortino": avg_sortino,
        "max_dd": max_dd,
        "approved_symbols": [r.symbol for r in approved],
        "deployment": "GO" if overall_pass else "REJECT",
    }
    os.makedirs('backtest_reports', exist_ok=True)
    with open('backtest_reports/institutional_backtest_summary.json', 'w') as f:
        json.dump(output, f, indent=2)
    print(f"\n  Results saved to backtest_reports/institutional_backtest_summary.json")


if __name__ == "__main__":
    main()
