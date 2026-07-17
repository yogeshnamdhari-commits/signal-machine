"""
EMA_V5 Backtest Engine — Simulates EMA_V5 strategy on historical kline data.
Isolated from existing backtest engine. Uses same data patterns.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class EMAv5BacktestConfig:
    """Backtest configuration for EMA_V5 strategy."""
    initial_balance: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    max_positions: int = 3
    max_daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 15.0
    leverage: int = 5
    taker_fee: float = 0.0004
    maker_fee: float = 0.0002
    funding_rate_8h: float = 0.0001

    # EMA_V5 specific
    sl_atr_mult: float = 1.5
    tp1_rr: float = 1.5
    tp2_rr: float = 3.0
    tp3_rr: float = 5.0
    tp1_exit_pct: float = 0.35
    tp2_exit_pct: float = 0.40
    tp3_exit_pct: float = 0.25
    breakeven_at_r: float = 1.0
    trailing_atr_mult: float = 1.0
    max_hold_hours: float = 48.0

    # EMA periods
    ema_fast: int = 20
    ema_medium: int = 50
    ema_institutional: int = 144
    ema_long_term: int = 200

    # Confidence
    min_confidence: float = 0.90

    # Timeframe
    timeframe: str = "5m"
    atr_period: int = 14


@dataclass
class EMAv5Trade:
    """Single backtest trade record."""
    symbol: str
    side: str
    entry_price: float
    entry_bar: int
    entry_time: str
    sl: float
    tp1: float
    tp2: float
    tp3: float
    confidence: float
    regime: str

    # Exit
    exit_price: float = 0.0
    exit_bar: int = 0
    exit_time: str = ""
    exit_reason: str = ""
    pnl: float = 0.0
    fees: float = 0.0
    r_multiple: float = 0.0
    hold_bars: int = 0

    # Partial exits
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    partial_pnl: float = 0.0
    remaining_qty: float = 1.0


@dataclass
class EMAv5BacktestResult:
    """Aggregated backtest results."""
    # Core
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    final_balance: float = 0.0
    # Trades
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    # PnL
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    # R-multiple
    avg_r: float = 0.0
    # Risk
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    sharpe_ratio: float = 0.0
    # Trades list
    trades: List[EMAv5Trade] = field(default_factory=list)
    # Equity curve
    equity_curve: List[float] = field(default_factory=list)


class EMAv5BacktestEngine:
    """Simulates EMA_V5 strategy on historical kline data."""

    def __init__(self, config: Optional[EMAv5BacktestConfig] = None) -> None:
        self.config = config or EMAv5BacktestConfig()
        self._balance = self.config.initial_balance
        self._peak_balance = self.config.initial_balance
        self._trades: List[EMAv5Trade] = []
        self._equity: List[float] = [self.config.initial_balance]
        self._daily_pnl = 0.0
        self._daily_reset_bar = 0

    def run(self, klines: pd.DataFrame, symbol: str = "UNKNOWN") -> EMAv5BacktestResult:
        """Run backtest on kline data. Klines must have: open, high, low, close, volume."""
        if klines.empty or len(klines) < self.config.ema_long_term + 50:
            logger.warning("EMAv5 backtest: insufficient data for {}", symbol)
            return EMAv5BacktestResult()

        # Compute indicators
        df = self._compute_indicators(klines.copy())

        # Scan for signals
        signals = self._scan_signals(df, symbol)

        # Simulate trades
        self._simulate_trades(df, signals, symbol)

        return self._compile_result(symbol)

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute EMA, ATR, and derived indicators."""
        cfg = self.config

        # EMAs
        df["ema20"] = df["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=cfg.ema_medium, adjust=False).mean()
        df["ema144"] = df["close"].ewm(span=cfg.ema_institutional, adjust=False).mean()
        df["ema200"] = df["close"].ewm(span=cfg.ema_long_term, adjust=False).mean()

        # ATR
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=cfg.atr_period).mean()

        # Volume SMA
        df["vol_sma20"] = df["volume"].rolling(window=20).mean()

        # EMA slopes
        df["slope_ema20"] = df["ema20"].pct_change(5) * 100
        df["slope_ema50"] = df["ema50"].pct_change(5) * 100

        # EMA alignment
        df["ema_bull"] = (df["ema20"] > df["ema50"]) & (df["ema50"] > df["ema144"]) & (df["ema144"] > df["ema200"])
        df["ema_bear"] = (df["ema20"] < df["ema50"]) & (df["ema50"] < df["ema144"]) & (df["ema144"] < df["ema200"])

        return df

    def _scan_signals(self, df: pd.DataFrame, symbol: str) -> List[Dict]:
        """Scan for EMA_V5 signals across all bars."""
        signals = []
        cfg = self.config
        min_idx = cfg.ema_long_term + 10

        for i in range(min_idx, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i - 1]

            # Skip if insufficient data
            if pd.isna(row["atr"]) or row["atr"] <= 0:
                continue
            if pd.isna(row["vol_sma20"]) or row["vol_sma20"] <= 0:
                continue

            # Regime check
            if row["ema_bull"]:
                regime = "BUY_MODE"
            elif row["ema_bear"]:
                regime = "SELL_MODE"
            else:
                continue

            # Pullback check (price near EMA20 or EMA50)
            close = row["close"]
            ema20 = row["ema20"]
            ema50 = row["ema50"]

            if regime == "BUY_MODE":
                # Price must be near or above EMA20
                touch_20 = abs(close - ema20) / close * 100 < 0.5
                touch_50 = abs(close - ema50) / close * 100 < 0.5
                if not (touch_20 or touch_50 or close > ema20):
                    continue
            else:
                touch_20 = abs(close - ema20) / close * 100 < 0.5
                touch_50 = abs(close - ema50) / close * 100 < 0.5
                if not (touch_20 or touch_50 or close < ema20):
                    continue

            # Candlestick pattern (simplified: engulfing check)
            candle_ok = False
            if i >= 2:
                prev_row = df.iloc[i - 1]
                if regime == "BUY_MODE":
                    # Bullish engulfing
                    if (prev_row["close"] < prev_row["open"] and
                        row["close"] > row["open"] and
                        row["close"] > prev_row["open"] and
                        row["open"] < prev_row["close"]):
                        candle_ok = True
                else:
                    # Bearish engulfing
                    if (prev_row["close"] > prev_row["open"] and
                        row["close"] < row["open"] and
                        row["close"] < prev_row["open"] and
                        row["open"] > prev_row["close"]):
                        candle_ok = True

            if not candle_ok:
                continue

            # Volume check
            if row["volume"] < row["vol_sma20"] * 1.0:
                continue

            # Slope check
            if regime == "BUY_MODE" and row["slope_ema20"] <= 0:
                continue
            if regime == "SELL_MODE" and row["slope_ema20"] >= 0:
                continue

            # Compute confidence (simplified)
            confidence = 0.90
            if abs(row["slope_ema20"]) > 0.5:
                confidence += 0.02
            if row["volume"] > row["vol_sma20"] * 1.5:
                confidence += 0.03
            confidence = min(confidence, 0.99)

            if confidence < cfg.min_confidence:
                continue

            # Compute SL/TP
            atr = row["atr"]
            if regime == "BUY_MODE":
                sl = close - atr * cfg.sl_atr_mult
                tp1 = close + abs(close - sl) * cfg.tp1_rr
                tp2 = close + abs(close - sl) * cfg.tp2_rr
                tp3 = close + abs(close - sl) * cfg.tp3_rr
            else:
                sl = close + atr * cfg.sl_atr_mult
                tp1 = close - abs(sl - close) * cfg.tp1_rr
                tp2 = close - abs(sl - close) * cfg.tp2_rr
                tp3 = close - abs(sl - close) * cfg.tp3_rr

            signals.append({
                "bar": i,
                "time": str(df.index[i]) if hasattr(df.index[i], 'strftime') else str(i),
                "symbol": symbol,
                "side": "LONG" if regime == "BUY_MODE" else "SHORT",
                "entry": close,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "tp3": tp3,
                "confidence": confidence,
                "regime": regime,
                "atr": atr,
            })

        return signals

    def _simulate_trades(self, df: pd.DataFrame, signals: List[Dict], symbol: str) -> None:
        """Simulate trades from signals."""
        cfg = self.config
        open_trade: Optional[EMAv5Trade] = None

        for sig in signals:
            # Skip if we have an open trade
            if open_trade is not None:
                continue

            bar = sig["bar"]
            entry = sig["entry"]
            sl = sig["sl"]
            side = sig["side"]

            # Risk sizing
            risk_amount = self._balance * (cfg.risk_per_trade_pct / 100)
            risk_per_unit = abs(entry - sl)
            if risk_per_unit <= 0:
                continue
            qty = risk_amount / risk_per_unit

            # Create trade
            trade = EMAv5Trade(
                symbol=symbol,
                side=side,
                entry_price=entry,
                entry_bar=bar,
                entry_time=sig["time"],
                sl=sl,
                tp1=sig["tp1"],
                tp2=sig["tp2"],
                tp3=sig["tp3"],
                confidence=sig["confidence"],
                regime=sig["regime"],
                remaining_qty=qty,
            )

            # Simulate forward
            for j in range(bar + 1, min(bar + int(cfg.max_hold_hours * 12) + 1, len(df))):
                row = df.iloc[j]
                high = row["high"]
                low = row["low"]
                close = row["close"]

                if side == "LONG":
                    # Stop loss
                    if low <= sl:
                        trade.exit_price = sl
                        trade.exit_bar = j
                        trade.exit_time = str(df.index[j]) if hasattr(df.index[j], 'strftime') else str(j)
                        trade.exit_reason = "stop_loss"
                        break

                    # TP1
                    if not trade.tp1_hit and high >= trade.tp1:
                        partial_pnl = (trade.tp1 - entry) * qty * cfg.tp1_exit_pct
                        trade.partial_pnl += partial_pnl
                        trade.remaining_qty *= (1 - cfg.tp1_exit_pct)
                        trade.tp1_hit = True

                    # TP2
                    if trade.tp1_hit and not trade.tp2_hit and high >= trade.tp2:
                        partial_pnl = (trade.tp2 - entry) * qty * cfg.tp2_exit_pct
                        trade.partial_pnl += partial_pnl
                        trade.remaining_qty *= (1 - cfg.tp2_exit_pct)
                        trade.tp2_hit = True

                    # TP3 (full close)
                    if trade.tp2_hit and not trade.tp3_hit and high >= trade.tp3:
                        trade.exit_price = trade.tp3
                        trade.exit_bar = j
                        trade.exit_time = str(df.index[j]) if hasattr(df.index[j], 'strftime') else str(j)
                        trade.exit_reason = "take_profit_3"
                        trade.tp3_hit = True
                        break

                    # Move SL to breakeven
                    current_r = (close - entry) / risk_per_unit
                    if current_r >= cfg.breakeven_at_r and not trade.tp1_hit:
                        sl = entry

                else:  # SHORT
                    # Stop loss
                    if high >= sl:
                        trade.exit_price = sl
                        trade.exit_bar = j
                        trade.exit_time = str(df.index[j]) if hasattr(df.index[j], 'strftime') else str(j)
                        trade.exit_reason = "stop_loss"
                        break

                    # TP1
                    if not trade.tp1_hit and low <= trade.tp1:
                        partial_pnl = (entry - trade.tp1) * qty * cfg.tp1_exit_pct
                        trade.partial_pnl += partial_pnl
                        trade.remaining_qty *= (1 - cfg.tp1_exit_pct)
                        trade.tp1_hit = True

                    # TP2
                    if trade.tp1_hit and not trade.tp2_hit and low <= trade.tp2:
                        partial_pnl = (entry - trade.tp2) * qty * cfg.tp2_exit_pct
                        trade.partial_pnl += partial_pnl
                        trade.remaining_qty *= (1 - cfg.tp2_exit_pct)
                        trade.tp2_hit = True

                    # TP3
                    if trade.tp2_hit and not trade.tp3_hit and low <= trade.tp3:
                        trade.exit_price = trade.tp3
                        trade.exit_bar = j
                        trade.exit_time = str(df.index[j]) if hasattr(df.index[j], 'strftime') else str(j)
                        trade.exit_reason = "take_profit_3"
                        trade.tp3_hit = True
                        break

                    # Move SL to breakeven
                    current_r = (entry - close) / risk_per_unit
                    if current_r >= cfg.breakeven_at_r and not trade.tp1_hit:
                        sl = entry

                # Max hold check
                if (j - bar) >= cfg.max_hold_hours * 12:
                    trade.exit_price = close
                    trade.exit_bar = j
                    trade.exit_time = str(df.index[j]) if hasattr(df.index[j], 'strftime') else str(j)
                    trade.exit_reason = "max_hold"
                    break
            else:
                # End of data — close at last price
                trade.exit_price = df.iloc[-1]["close"]
                trade.exit_bar = len(df) - 1
                trade.exit_time = str(df.index[-1]) if hasattr(df.index[-1], 'strftime') else str(len(df) - 1)
                trade.exit_reason = "end_of_data"

            # Compute PnL
            if side == "LONG":
                raw_pnl = (trade.exit_price - entry) * qty
            else:
                raw_pnl = (entry - trade.exit_price) * qty

            # Fees
            fee_entry = entry * qty * cfg.taker_fee
            fee_exit = trade.exit_price * qty * cfg.taker_fee
            trade.fees = fee_entry + fee_exit

            # Total PnL
            trade.pnl = raw_pnl + trade.partial_pnl - trade.fees
            trade.hold_bars = trade.exit_bar - trade.entry_bar

            # R-multiple
            trade.r_multiple = trade.pnl / risk_amount if risk_amount > 0 else 0

            # Update balance
            self._balance += trade.pnl
            self._peak_balance = max(self._peak_balance, self._balance)
            self._equity.append(self._balance)

            self._trades.append(trade)
            open_trade = None

    def _compile_result(self, symbol: str) -> EMAv5BacktestResult:
        """Compile final backtest result."""
        trades = self._trades
        if not trades:
            return EMAv5BacktestResult()

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]

        total_pnl = sum(t.pnl for t in trades)
        gross_wins = sum(t.pnl for t in wins)
        gross_losses = abs(sum(t.pnl for t in losses))

        # Drawdown
        peak = self.config.initial_balance
        max_dd = 0
        max_dd_usd = 0
        for eq in self._equity:
            peak = max(peak, eq)
            dd = peak - eq
            dd_pct = (dd / peak * 100) if peak > 0 else 0
            max_dd = max(max_dd, dd_pct)
            max_dd_usd = max(max_dd_usd, dd)

        # Sharpe
        returns = []
        for i in range(1, len(self._equity)):
            prev = self._equity[i - 1]
            if prev > 0:
                returns.append((self._equity[i] - prev) / prev)
        import math
        if returns and len(returns) > 1:
            mean_r = sum(returns) / len(returns)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1))
            sharpe = (mean_r / std_r) * math.sqrt(4 * 365) if std_r > 0 else 0
        else:
            sharpe = 0

        return EMAv5BacktestResult(
            total_pnl=round(total_pnl, 4),
            total_return_pct=round(total_pnl / self.config.initial_balance * 100, 2),
            final_balance=round(self._balance, 4),
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(len(wins) / len(trades) * 100, 1) if trades else 0,
            avg_win=round(gross_wins / len(wins), 4) if wins else 0,
            avg_loss=round(gross_losses / len(losses), 4) if losses else 0,
            largest_win=round(max(t.pnl for t in trades), 4),
            largest_loss=round(min(t.pnl for t in trades), 4),
            profit_factor=round(gross_wins / gross_losses, 2) if gross_losses > 0 else 99.99,
            expectancy=round(total_pnl / len(trades), 4) if trades else 0,
            avg_r=round(sum(t.r_multiple for t in trades) / len(trades), 2) if trades else 0,
            max_drawdown_pct=round(max_dd, 2),
            max_drawdown_usd=round(max_dd_usd, 4),
            sharpe_ratio=round(sharpe, 3),
            trades=trades,
            equity_curve=self._equity,
        )

    def reset(self) -> None:
        """Reset engine state for new backtest."""
        self._balance = self.config.initial_balance
        self._peak_balance = self.config.initial_balance
        self._trades = []
        self._equity = [self.config.initial_balance]
        self._daily_pnl = 0.0
        self._daily_reset_bar = 0
