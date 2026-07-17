"""
YOG'Z Backtest Engine — Full simulation of the trading pipeline.

Simulates the complete scanning → scoring → risk management → exit pipeline
on historical kline data, with trailing stops, breakeven, partial profit taking,
time-based exits, and confidence-scaled sizing — matching the live engine exactly.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger


# ── Configuration ────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    """Backtest simulation parameters — mirrors live risk engine."""
    initial_balance: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 2.0
    max_open_positions: int = 10
    max_daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    max_leverage: int = 10
    taker_fee: float = 0.0004
    maker_fee: float = 0.0002
    funding_rate_8h: float = 0.0001  # avg 0.01% per 8h
    # Signal quality
    min_score: float = 40.0
    min_rr: float = 1.5
    # Exit logic
    breakeven_at_r: float = 1.2
    trailing_activate_at_r: float = 2.5
    trailing_pct: float = 0.60
    partial_exit_at_r: float = 2.0
    partial_exit_pct: float = 0.30
    time_exit_hours: float = 10.0
    time_exit_min_r: float = 1.5
    # Cooldown
    cooldown_bars: int = 60  # bars to wait before re-entry


# ── Data Structures ──────────────────────────────────────────────

@dataclass
class Trade:
    symbol: str
    side: str  # LONG / SHORT
    entry_price: float
    quantity: float
    entry_bar: int
    entry_time: str
    entry_score: float
    stop_loss: float
    take_profit: float
    # Exit
    exit_price: float = 0.0
    exit_bar: int = 0
    exit_time: str = ""
    exit_reason: str = ""
    pnl: float = 0.0
    fees: float = 0.0
    funding_cost: float = 0.0
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    # Trailing state
    peak_r: float = 0.0
    breakeven_hit: bool = False
    partial_taken: bool = False
    remaining_qty: float = 0.0  # after partial exit


@dataclass
class BacktestResult:
    # Core metrics
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    final_balance: float = 0.0
    # Trade stats
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    # PnL stats
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_r_multiple: float = 0.0
    # Risk metrics
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_bars: int = 0
    # Expectancy
    expectancy: float = 0.0
    kelly_criterion: float = 0.0
    # Cost analysis
    total_fees: float = 0.0
    total_funding: float = 0.0
    # Streak stats
    max_win_streak: int = 0
    max_loss_streak: int = 0
    avg_hold_bars: float = 0.0
    # Exit attribution
    exit_reasons: Dict[str, int] = field(default_factory=dict)
    # Equity curve + trades
    equity_curve: List[Dict] = field(default_factory=list)
    trades: List[Dict] = field(default_factory=list)


# ── Historical Data Fetcher ──────────────────────────────────────

class HistoricalDataFetcher:
    """Fetch OHLCV kline data from Binance REST API."""

    BASE_URL = "https://fapi.binance.com"

    @staticmethod
    async def fetch_klines(
        symbol: str,
        interval: str = "5m",
        limit: int = 1500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch kline data from Binance Futures REST API."""
        import aiohttp

        url = f"{HistoricalDataFetcher.BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Binance API error {resp.status}: {await resp.text()}")
                data = await resp.json()

        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_vol",
            "taker_buy_quote_vol", "ignore",
        ])
        for col in ("open", "high", "low", "close", "volume", "quote_volume",
                     "taker_buy_vol", "taker_buy_quote_vol"):
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        df["bar_index"] = range(len(df))
        return df

    @staticmethod
    async def fetch_multi_tf(
        symbol: str,
        days: int = 7,
        intervals: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch multiple timeframe klines for multi-timeframe analysis."""
        if intervals is None:
            intervals = ["1m", "5m", "15m", "1h", "4h"]

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (days * 24 * 3600 * 1000)

        result = {}
        for tf in intervals:
            try:
                df = await HistoricalDataFetcher.fetch_klines(
                    symbol, interval=tf, limit=1500, start_time=start_ms, end_time=now_ms
                )
                result[tf] = df
                logger.debug("Fetched {} klines for {} {}", len(df), symbol, tf)
                await asyncio.sleep(0.1)  # rate limit
            except Exception as e:
                logger.warning("Failed to fetch {} {}: {}", symbol, tf, e)
        return result

    @staticmethod
    def save_cache(df: pd.DataFrame, path: str) -> None:
        """Save fetched data to parquet for caching."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)

    @staticmethod
    def load_cache(path: str) -> Optional[pd.DataFrame]:
        """Load cached data from parquet."""
        if os.path.exists(path):
            return pd.read_parquet(path)
        return None


# ── Technical Indicators ─────────────────────────────────────────

class Indicators:
    """Compute technical indicators on kline DataFrames."""

    @staticmethod
    def rsi(closes: pd.Series, period: int = 14) -> pd.Series:
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(span=period, adjust=False).mean()
        avg_loss = loss.ewm(span=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def bollinger(closes: pd.Series, period: int = 20, std_dev: float = 2.0):
        mid = closes.rolling(period).mean()
        std = closes.rolling(period).std()
        return mid, mid + std_dev * std, mid - std_dev * std

    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.Series:
        typical = (df["high"] + df["low"] + df["close"]) / 3
        cum_tp_vol = (typical * df["volume"]).cumsum()
        cum_vol = df["volume"].cumsum()
        return cum_tp_vol / cum_vol.replace(0, 1e-10)

    @staticmethod
    def volume_ratio(df: pd.DataFrame, short: int = 5, long: int = 20) -> pd.Series:
        recent = df["volume"].rolling(short).mean()
        baseline = df["volume"].rolling(long).mean()
        return recent / baseline.replace(0, 1e-10)

    @staticmethod
    def regime(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
        """Simple regime detection: trending_up, trending_down, ranging."""
        close = df["close"]
        ema_short = close.ewm(span=lookback // 2, adjust=False).mean()
        ema_long = close.ewm(span=lookback, adjust=False).mean()
        atr = Indicators.atr(df, lookback)
        range_pct = atr / close * 100

        regime = pd.Series("ranging", index=df.index)
        regime[ema_short > ema_long * 1.002] = "trending_up"
        regime[ema_short < ema_long * 0.998] = "trending_down"
        # Volatile if ATR > 2% of price
        regime[range_pct > 2.0] = "volatile"
        return regime

    @staticmethod
    def support_resistance(df: pd.DataFrame, lookback: int = 50) -> Tuple[List[float], List[float]]:
        """Find recent swing highs/lows as support/resistance."""
        highs = df["high"].tail(lookback)
        lows = df["low"].tail(lookback)
        current = df["close"].iloc[-1]

        supports = sorted([l for l in lows if l < current], reverse=True)[:5]
        resistances = sorted([h for h in highs if h > current])[:5]
        return supports, resistances


# ── Signal Generator ─────────────────────────────────────────────

class SignalGenerator:
    """
    Generate trading signals from historical kline data.
    Mimics the live AI scoring pipeline using technical indicators.
    """

    def __init__(self, min_score: float = 40.0):
        self.min_score = min_score

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate entry signals for each bar.
        Returns DataFrame with columns: bar_index, signal (LONG/SHORT/NONE), score, entry, sl, tp.
        """
        if len(df) < 50:
            return pd.DataFrame()

        df = df.copy()
        df["rsi"] = Indicators.rsi(df["close"])
        df["atr"] = Indicators.atr(df)
        df["ema_fast"] = Indicators.ema(df["close"], 10)
        df["ema_slow"] = Indicators.ema(df["close"], 30)
        df["vol_ratio"] = Indicators.volume_ratio(df)
        df["regime"] = Indicators.regime(df)
        _, df["bb_upper"], df["bb_lower"] = Indicators.bollinger(df["close"])
        df["vwap"] = Indicators.vwap(df)

        signals = []
        for i in range(50, len(df)):
            row = df.iloc[i]
            prev = df.iloc[i - 1]
            score = 0.0
            side = "NONE"
            reasons = []

            # ── Momentum Score ──
            if row["ema_fast"] > row["ema_slow"]:
                score += 15
                side = "LONG"
                reasons.append("ema_bullish")
            elif row["ema_fast"] < row["ema_slow"]:
                score += 15
                side = "SHORT"
                reasons.append("ema_bearish")

            # ── RSI Score ──
            rsi = row["rsi"]
            if side == "LONG" and 30 < rsi < 65:
                score += 12
                reasons.append("rsi_favorable")
            elif side == "SHORT" and 35 < rsi < 70:
                score += 12
                reasons.append("rsi_favorable")
            # Extreme RSI bonus
            if rsi < 30 and side == "LONG":
                score += 8
                reasons.append("rsi_oversold")
            elif rsi > 70 and side == "SHORT":
                score += 8
                reasons.append("rsi_overbought")

            # ── Volume Score ──
            vr = row["vol_ratio"]
            if vr > 1.5:
                score += 10
                reasons.append("volume_spike")
            elif vr > 1.2:
                score += 5

            # ── Regime Score ──
            regime = row["regime"]
            if regime in ("trending_up", "trending_down"):
                score += 8
                reasons.append("trending")
            elif regime == "breakout":
                score += 12
                reasons.append("breakout")

            # ── Bollinger Band Position ──
            if side == "LONG" and row["close"] < row["bb_lower"]:
                score += 6
                reasons.append("bb_oversold")
            elif side == "SHORT" and row["close"] > row["bb_upper"]:
                score += 6
                reasons.append("bb_overbought")

            # ── VWAP Divergence ──
            if side == "LONG" and row["close"] > row["vwap"]:
                score += 5
                reasons.append("above_vwap")
            elif side == "SHORT" and row["close"] < row["vwap"]:
                score += 5
                reasons.append("below_vwap")

            # ── Price Action (engulfing) ──
            body = abs(row["close"] - row["open"])
            prev_body = abs(prev["close"] - prev["open"])
            if body > prev_body * 1.5:
                score += 5
                reasons.append("momentum_accel")

            # ── ATR-based SL/TP ──
            atr = row["atr"]
            if atr <= 0:
                continue

            entry = row["close"]
            if side == "LONG":
                sl = entry - atr * 2.0
                tp = entry + atr * 3.5
            elif side == "SHORT":
                sl = entry + atr * 2.0
                tp = entry - atr * 3.5
            else:
                continue

            risk = abs(entry - sl)
            reward = abs(tp - entry)
            rr = reward / risk if risk > 0 else 0

            if rr < 1.5:
                score -= 10  # Penalize low R:R

            if score >= self.min_score:
                signals.append({
                    "bar_index": i,
                    "signal": side,
                    "score": round(score, 1),
                    "entry": entry,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "rr": round(rr, 2),
                    "atr": atr,
                    "rsi": rsi,
                    "vol_ratio": vr,
                    "regime": regime,
                    "reasons": reasons,
                    "time": str(row.get("open_time", i)),
                })

        return pd.DataFrame(signals)


# ── Backtest Engine ──────────────────────────────────────────────

class BacktestEngine:
    """
    Full backtesting engine that simulates the live trading pipeline.
    
    Pipeline:
    1. Fetch historical klines
    2. Compute indicators and generate signals
    3. Simulate position management with full exit logic
    4. Calculate comprehensive performance metrics
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()

    async def run(
        self,
        symbol: str,
        days: int = 7,
        interval: str = "5m",
        klines_df: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """
        Run backtest for a single symbol.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            days: Number of days of historical data
            interval: Kline interval
            klines_df: Pre-fetched klines (skip fetch if provided)
        """
        # 1. Fetch data
        if klines_df is None:
            cache_path = f"data/backtest_cache/{symbol}_{interval}_{days}d.parquet"
            klines_df = HistoricalDataFetcher.load_cache(cache_path)
            if klines_df is None:
                logger.info("Fetching {} klines for {} {}...", days, symbol, interval)
                klines_df = await HistoricalDataFetcher.fetch_klines(symbol, interval, limit=1500)
                HistoricalDataFetcher.save_cache(klines_df, cache_path)

        logger.info("Running backtest: {} | {} bars | {} to {}",
                     symbol, len(klines_df),
                     klines_df["open_time"].iloc[0],
                     klines_df["open_time"].iloc[-1])

        # 2. Generate signals
        sig_gen = SignalGenerator(min_score=self.config.min_score)
        signals = sig_gen.generate_signals(klines_df)
        logger.info("Generated {} signals (min_score={})", len(signals), self.config.min_score)

        # 3. Simulate trading
        result = self._simulate(klines_df, signals)
        result.final_balance = self.config.initial_balance + result.total_pnl
        result.total_return_pct = round(result.total_pnl / self.config.initial_balance * 100, 2)

        return result

    async def run_multi_symbol(
        self,
        symbols: List[str],
        days: int = 7,
        interval: str = "5m",
    ) -> BacktestResult:
        """Run backtest across multiple symbols, aggregating results."""
        all_trades = []
        all_equity = []
        balance = self.config.initial_balance

        for sym in symbols:
            try:
                result = await self.run(sym, days, interval)
                all_trades.extend(result.trades)
                # Shift equity curve by accumulated balance
                for pt in result.equity_curve:
                    all_equity.append({
                        "bar": pt["bar"],
                        "equity": pt["equity"] + (balance - self.config.initial_balance),
                    })
                balance += result.total_pnl
            except Exception as e:
                logger.error("Backtest failed for {}: {}", sym, e)

        # Aggregate
        if not all_trades:
            return BacktestResult()

        return self._aggregate_results(all_trades, all_equity, balance)

    def _simulate(self, klines: pd.DataFrame, signals: pd.DataFrame) -> BacktestResult:
        """Core simulation loop — matches live risk engine logic exactly."""
        cfg = self.config
        balance = cfg.initial_balance
        peak_balance = balance
        max_dd = 0.0
        max_dd_bars = 0
        current_dd_start = 0

        open_positions: List[Dict] = []  # active trades
        closed_trades: List[Dict] = []
        equity_curve: List[Dict] = []
        cooldowns: Dict[str, int] = {}  # symbol -> last_exit_bar
        daily_pnl = 0.0
        daily_start_bar = 0
        bars_per_day = 288 if "5m" in str(klines.get("open_time", [""]).__class__) else 288

        # Pre-index signals by bar
        signal_by_bar = {}
        for _, sig in signals.iterrows():
            bar = int(sig["bar_index"])
            signal_by_bar[bar] = sig

        for i in range(len(klines)):
            price = klines.iloc[i]["close"]
            high = klines.iloc[i]["high"]
            low = klines.iloc[i]["low"]

            # ── Daily reset ──
            if i - daily_start_bar >= bars_per_day:
                daily_pnl = 0.0
                daily_start_bar = i

            # ── Check exits on open positions ──
            exits_this_bar = []
            for pos in open_positions:
                exit_price, reason = self._check_exit(pos, price, high, low, i)
                if exit_price is not None:
                    exits_this_bar.append((pos, exit_price, reason))

            for pos, exit_price, reason in exits_this_bar:
                trade = self._close_position(pos, exit_price, reason, i, klines)
                closed_trades.append(trade)
                balance += trade["net_pnl"]
                daily_pnl += trade["net_pnl"]
                open_positions.remove(pos)
                cooldowns[pos["symbol"]] = i

            # ── Check new signals ──
            if i in signal_by_bar:
                sig = signal_by_bar[i]
                symbol = klines.iloc[i].get("symbol", "BACKTEST")
                side = sig["signal"]
                score = sig["score"]

                # Risk checks
                if len(open_positions) >= cfg.max_open_positions:
                    pass  # skip
                elif daily_pnl < -(balance * cfg.max_daily_loss_pct / 100):
                    pass  # daily loss limit
                elif (peak_balance - balance) / peak_balance * 100 >= cfg.max_drawdown_pct:
                    pass  # max drawdown
                elif symbol in cooldowns and (i - cooldowns[symbol]) < cfg.cooldown_bars:
                    pass  # cooldown
                elif any(p["symbol"] == symbol for p in open_positions):
                    pass  # already open
                else:
                    # Open position
                    entry = sig["entry"]
                    sl = sig["stop_loss"]
                    tp = sig["take_profit"]
                    risk_dist = abs(entry - sl)
                    if risk_dist <= 0:
                        continue

                    # Confidence-scaled sizing
                    size_mult = 1.0
                    if score >= 75:
                        size_mult = 1.15
                    elif score >= 55:
                        size_mult = 1.05
                    elif score >= 35:
                        size_mult = 0.90
                    else:
                        size_mult = 0.70

                    risk_usd = balance * cfg.risk_per_trade_pct / 100 * size_mult
                    qty = risk_usd / risk_dist
                    pos_val = qty * entry
                    max_pos = balance * cfg.max_position_pct / 100
                    if pos_val > max_pos:
                        qty = max_pos / entry

                    fee = entry * qty * cfg.taker_fee

                    pos = {
                        "symbol": symbol,
                        "side": side,
                        "entry": entry,
                        "quantity": qty,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "entry_bar": i,
                        "entry_time": str(klines.iloc[i].get("open_time", i)),
                        "entry_score": score,
                        "risk_dist": risk_dist,
                        "peak_r": 0.0,
                        "breakeven_hit": False,
                        "partial_taken": False,
                        "remaining_qty": qty,
                        "initial_qty": qty,
                    }
                    open_positions.append(pos)
                    balance -= fee  # entry fee

            # ── Equity tracking ──
            unrealized = 0.0
            for pos in open_positions:
                if pos["side"] == "LONG":
                    unr = (price - pos["entry"]) * pos["remaining_qty"]
                else:
                    unr = (pos["entry"] - price) * pos["remaining_qty"]
                unrealized += unr

            equity = balance + unrealized
            equity_curve.append({"bar": i, "equity": equity, "balance": balance})

            # Drawdown tracking
            if equity > peak_balance:
                peak_balance = equity
                dd_duration = i - current_dd_start
                if dd_duration > max_dd_bars:
                    max_dd_bars = dd_duration
                current_dd_start = i

            dd = (peak_balance - equity) / peak_balance * 100 if peak_balance > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # Close remaining positions at last price
        last_price = klines.iloc[-1]["close"]
        for pos in list(open_positions):
            trade = self._close_position(pos, last_price, "end_of_data", len(klines) - 1, klines)
            closed_trades.append(trade)

        # Calculate metrics
        return self._calculate_metrics(closed_trades, equity_curve, max_dd, max_dd_bars)

    def _check_exit(
        self, pos: Dict, price: float, high: float, low: float, bar: int
    ) -> Tuple[Optional[float], str]:
        """Check exit conditions — matches live risk_engine.check_exit_conditions."""
        cfg = self.config
        entry = pos["entry"]
        sl = pos["stop_loss"]
        tp = pos["take_profit"]
        side = pos["side"]
        risk_dist = pos["risk_dist"]

        if risk_dist <= 0:
            return None, ""

        # Compute R-multiple
        if side == "LONG":
            unrealized_r = (price - entry) / risk_dist
        else:
            unrealized_r = (entry - price) / risk_dist

        # Track peak R
        if unrealized_r > pos["peak_r"]:
            pos["peak_r"] = unrealized_r

        peak_r = pos["peak_r"]

        # Time-based exit
        bars_held = bar - pos["entry_bar"]
        hours_held = bars_held * 5 / 60  # assuming 5m bars
        if hours_held >= cfg.time_exit_hours and peak_r < cfg.time_exit_min_r:
            return price, "time_exit"

        # Partial profit at R threshold
        if not pos["partial_taken"] and peak_r >= cfg.partial_exit_at_r:
            pos["partial_taken"] = True
            partial_qty = pos["remaining_qty"] * cfg.partial_exit_pct
            pos["remaining_qty"] -= partial_qty
            # Return partial exit price (approximate)
            return price, "partial_profit"

        # Trailing stop (activated after partial or at R threshold)
        activate_r = max(cfg.trailing_activate_at_r, cfg.partial_exit_at_r)
        if peak_r >= activate_r:
            trail_r = peak_r * cfg.trailing_pct
            if unrealized_r <= trail_r:
                return price, "trailing_stop"

        # Breakeven stop
        if peak_r >= cfg.breakeven_at_r and not pos["breakeven_hit"]:
            pos["breakeven_hit"] = True
            be_sl = entry + risk_dist * 0.08 if side == "LONG" else entry - risk_dist * 0.08
            pos["stop_loss"] = be_sl

        # Standard SL
        if side == "LONG" and low <= pos["stop_loss"]:
            return pos["stop_loss"], "stop_loss"
        elif side == "SHORT" and high >= pos["stop_loss"]:
            return pos["stop_loss"], "stop_loss"

        # Standard TP
        if side == "LONG" and high >= tp:
            return tp, "take_profit"
        elif side == "SHORT" and low <= tp:
            return tp, "take_profit"

        return None, ""

    def _close_position(
        self, pos: Dict, exit_price: float, reason: str, bar: int, klines: pd.DataFrame
    ) -> Dict:
        """Close a position and compute final PnL."""
        cfg = self.config
        entry = pos["entry"]
        qty = pos["initial_qty"]
        remaining = pos["remaining_qty"]
        side = pos["side"]
        # Audit: Use consistent 0.2% risk floor for backtest R-Multiple metrics
        risk_dist = max(pos["risk_dist"], entry * 0.002)

        # Exit fee on remaining qty
        exit_fee = exit_price * remaining * cfg.taker_fee

        # Funding cost
        bars_held = bar - pos["entry_bar"]
        hours_held = bars_held * 5 / 60
        funding = entry * qty * cfg.funding_rate_8h * (hours_held / 8)

        # PnL
        if side == "LONG":
            pnl = (exit_price - entry) * remaining
        else:
            pnl = (entry - exit_price) * remaining

        net_pnl = pnl - exit_fee - funding

        # R-multiple
        r_mult = (exit_price - entry) / risk_dist if side == "LONG" else (entry - exit_price) / risk_dist

        return {
            "symbol": pos["symbol"],
            "side": side,
            "entry_price": entry,
            "exit_price": exit_price,
            "quantity": qty,
            "remaining": remaining,
            "entry_bar": pos["entry_bar"],
            "exit_bar": bar,
            "entry_time": pos["entry_time"],
            "entry_score": pos["entry_score"],
            "exit_reason": reason,
            "pnl": round(pnl, 4),
            "fees": round(pos.get("entry_fee", 0) + exit_fee, 4),
            "funding_cost": round(funding, 4),
            "net_pnl": round(net_pnl, 4),
            "r_multiple": round(r_mult, 2),
            "bars_held": bars_held,
            "hours_held": round(hours_held, 1),
        }

    def _calculate_metrics(
        self,
        trades: List[Dict],
        equity_curve: List[Dict],
        max_dd: float,
        max_dd_bars: int,
    ) -> BacktestResult:
        """Calculate comprehensive performance metrics."""
        cfg = self.config
        result = BacktestResult()

        if not trades:
            return result

        pnls = [t["net_pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        result.total_trades = len(trades)
        result.winning_trades = len(wins)
        result.losing_trades = len(losses)
        result.win_rate = len(wins) / len(trades) if trades else 0

        result.total_pnl = round(sum(pnls), 2)
        result.avg_win = round(np.mean(wins), 2) if wins else 0
        result.avg_loss = round(np.mean(losses), 2) if losses else 0
        result.largest_win = round(max(wins), 2) if wins else 0
        result.largest_loss = round(min(losses), 2) if losses else 0
        result.avg_r_multiple = round(np.mean([t["r_multiple"] for t in trades]), 2)

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1e-10
        result.profit_factor = round(gross_profit / gross_loss, 2)

        # Sharpe (annualized, assuming 5m bars)
        if len(pnls) > 1:
            returns = np.array(pnls) / cfg.initial_balance
            avg_r = np.mean(returns)
            std_r = np.std(returns)
            bars_per_year = 365 * 24 * 12  # 5m bars per year
            result.sharpe_ratio = round(
                avg_r / std_r * np.sqrt(bars_per_year) if std_r > 0 else 0, 2
            )

            # Sortino
            downside = returns[returns < 0]
            downside_std = np.std(downside) if len(downside) > 0 else 1e-10
            result.sortino_ratio = round(
                avg_r / downside_std * np.sqrt(bars_per_year), 2
            )

        # Calmar
        result.max_drawdown = round(max_dd, 2)
        result.max_drawdown_pct = round(max_dd, 2)
        result.max_drawdown_duration_bars = max_dd_bars

        total_return = result.total_pnl / cfg.initial_balance
        result.calmar_ratio = round(
            total_return / (max_dd / 100) if max_dd > 0 else 0, 2
        )

        # Expectancy
        wr = result.win_rate
        avg_w = result.avg_win
        avg_l = abs(result.avg_loss)
        result.expectancy = round(wr * avg_w - (1 - wr) * avg_l, 2)

        # Kelly criterion
        if avg_l > 0:
            b = avg_w / avg_l
            result.kelly_criterion = round((wr * b - (1 - wr)) / b, 4)

        # Cost analysis
        result.total_fees = round(sum(t["fees"] for t in trades), 2)
        result.total_funding = round(sum(t["funding_cost"] for t in trades), 2)

        # Streaks
        streak = 0
        max_win_streak = 0
        max_loss_streak = 0
        for p in pnls:
            if p > 0:
                if streak > 0:
                    streak += 1
                else:
                    streak = 1
                max_win_streak = max(max_win_streak, streak)
            else:
                if streak < 0:
                    streak -= 1
                else:
                    streak = -1
                max_loss_streak = max(max_loss_streak, abs(streak))

        result.max_win_streak = max_win_streak
        result.max_loss_streak = max_loss_streak

        # Average hold time
        result.avg_hold_bars = round(np.mean([t["bars_held"] for t in trades]), 1)

        # Exit reason attribution
        reasons = {}
        for t in trades:
            r = t["exit_reason"]
            reasons[r] = reasons.get(r, 0) + 1
        result.exit_reasons = reasons

        # Equity curve & trades
        result.equity_curve = equity_curve
        result.trades = trades

        return result

    def _aggregate_results(
        self,
        all_trades: List[Dict],
        all_equity: List[Dict],
        final_balance: float,
    ) -> BacktestResult:
        """Aggregate results from multi-symbol backtest."""
        cfg = self.config
        result = BacktestResult()
        result.trades = all_trades
        result.equity_curve = all_equity

        if not all_trades:
            return result

        pnls = [t["net_pnl"] for t in all_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        result.total_trades = len(all_trades)
        result.winning_trades = len(wins)
        result.losing_trades = len(losses)
        result.win_rate = len(wins) / len(all_trades)
        result.total_pnl = round(sum(pnls), 2)
        result.final_balance = final_balance
        result.total_return_pct = round(result.total_pnl / cfg.initial_balance * 100, 2)
        result.avg_win = round(np.mean(wins), 2) if wins else 0
        result.avg_loss = round(np.mean(losses), 2) if losses else 0
        result.largest_win = round(max(wins), 2) if wins else 0
        result.largest_loss = round(min(losses), 2) if losses else 0
        result.avg_r_multiple = round(np.mean([t["r_multiple"] for t in all_trades]), 2)
        result.profit_factor = round(
            sum(wins) / max(abs(sum(losses)), 1e-10), 2
        )
        result.total_fees = round(sum(t["fees"] for t in all_trades), 2)
        result.total_funding = round(sum(t["funding_cost"] for t in all_trades), 2)

        reasons = {}
        for t in all_trades:
            r = t["exit_reason"]
            reasons[r] = reasons.get(r, 0) + 1
        result.exit_reasons = reasons

        # Max DD from equity
        if all_equity:
            equities = [e["equity"] for e in all_equity]
            peak = equities[0]
            max_dd = 0
            for eq in equities:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)
            result.max_drawdown = round(max_dd, 2)
            result.max_drawdown_pct = round(max_dd, 2)

        return result


# ── Report Generator ─────────────────────────────────────────────

class BacktestReporter:
    """Generate visual reports from backtest results."""

    @staticmethod
    def generate_report(result: BacktestResult, symbol: str = "BACKTEST") -> None:
        """Generate a comprehensive HTML report with Plotly charts."""
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
        except ImportError:
            logger.warning("plotly not installed — skipping visual report")
            return

        output_dir = Path("data/backtest_reports")
        output_dir.mkdir(parents=True, exist_ok=True)

        fig = make_subplots(
            rows=4, cols=2,
            subplot_titles=(
                "Equity Curve", "Trade PnL Distribution",
                "Cumulative PnL", "R-Multiple Distribution",
                "Drawdown", "Exit Reason Breakdown",
                "Win/Loss Streaks", "Score vs PnL",
            ),
            vertical_spacing=0.08,
            horizontal_spacing=0.08,
            specs=[
                [{"type": "xy"}, {"type": "xy"}],
                [{"type": "xy"}, {"type": "xy"}],
                [{"type": "xy"}, {"type": "pie"}],
                [{"type": "xy"}, {"type": "xy"}],
            ],
        )

        # 1. Equity Curve
        if result.equity_curve:
            bars = [e["bar"] for e in result.equity_curve]
            equities = [e["equity"] for e in result.equity_curve]
            fig.add_trace(go.Scatter(
                x=bars, y=equities, mode="lines", name="Equity",
                line=dict(color="#00ff88", width=1.5),
                fill="tozeroy", fillcolor="rgba(0,255,136,0.08)",
            ), row=1, col=1)
            fig.add_hline(y=10000, line_dash="dash", line_color="#666",
                         annotation_text="Initial", row=1, col=1)

        # 2. Trade PnL Distribution
        if result.trades:
            pnls = [t["net_pnl"] for t in result.trades]
            colors = ["#00ff88" if p > 0 else "#ff4444" for p in pnls]
            fig.add_trace(go.Bar(
                x=list(range(len(pnls))), y=pnls,
                marker_color=colors, name="PnL per Trade",
                showlegend=False,
            ), row=1, col=2)

        # 3. Cumulative PnL
        if result.trades:
            cum_pnl = np.cumsum([t["net_pnl"] for t in result.trades])
            fig.add_trace(go.Scatter(
                y=cum_pnl.tolist(), mode="lines", name="Cumulative PnL",
                line=dict(color="#3b82f6", width=2),
            ), row=2, col=1)

        # 4. R-Multiple Distribution
        if result.trades:
            r_mults = [t["r_multiple"] for t in result.trades]
            fig.add_trace(go.Histogram(
                x=r_mults, nbinsx=30, name="R-Multiple",
                marker_color="#8b5cf6",
            ), row=2, col=2)

        # 5. Drawdown
        if result.equity_curve:
            equities = [e["equity"] for e in result.equity_curve]
            peak = np.maximum.accumulate(equities)
            dd = [(p - e) / p * 100 if p > 0 else 0 for p, e in zip(peak, equities)]
            fig.add_trace(go.Scatter(
                y=dd, mode="lines", name="Drawdown %",
                line=dict(color="#ff4444", width=1),
                fill="tozeroy", fillcolor="rgba(255,68,68,0.1)",
            ), row=3, col=1)

        # 6. Exit Reasons
        if result.exit_reasons:
            labels = list(result.exit_reasons.keys())
            values = list(result.exit_reasons.values())
            fig.add_trace(go.Pie(
                labels=labels, values=values, name="Exits",
                marker=dict(colors=["#00ff88", "#ff4444", "#f59e0b", "#3b82f6", "#8b5cf6", "#ec4899"]),
            ), row=3, col=2)

        # 7. Win/Loss Streaks
        if result.trades:
            streaks = []
            streak = 0
            for t in result.trades:
                if t["net_pnl"] > 0:
                    streak = streak + 1 if streak > 0 else 1
                else:
                    streak = streak - 1 if streak < 0 else -1
                streaks.append(streak)
            fig.add_trace(go.Scatter(
                y=streaks, mode="lines", name="Streak",
                line=dict(color="#f59e0b", width=1),
            ), row=4, col=1)

        # 8. Score vs PnL scatter
        if result.trades:
            scores = [t["entry_score"] for t in result.trades]
            pnls = [t["net_pnl"] for t in result.trades]
            colors = ["#00ff88" if p > 0 else "#ff4444" for p in pnls]
            fig.add_trace(go.Scatter(
                x=scores, y=pnls, mode="markers", name="Score vs PnL",
                marker=dict(color=colors, size=6, opacity=0.7),
            ), row=4, col=2)

        fig.update_layout(
            height=1200, width=1400,
            title_text=f"⚡ YOG'Z Backtest Report — {symbol}",
            template="plotly_dark",
            showlegend=False,
            margin=dict(l=40, r=40, t=60, b=40),
        )

        report_path = output_dir / f"{symbol}_report.html"
        fig.write_html(str(report_path))
        logger.info("📊 Report saved: {}", report_path)

        # Also save JSON summary
        summary = {
            "symbol": symbol,
            "total_pnl": result.total_pnl,
            "total_return_pct": result.total_return_pct,
            "final_balance": result.final_balance,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "sharpe_ratio": result.sharpe_ratio,
            "sortino_ratio": result.sortino_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "expectancy": result.expectancy,
            "kelly_criterion": result.kelly_criterion,
            "avg_r_multiple": result.avg_r_multiple,
            "avg_hold_bars": result.avg_hold_bars,
            "total_fees": result.total_fees,
            "total_funding": result.total_funding,
            "exit_reasons": result.exit_reasons,
            "max_win_streak": result.max_win_streak,
            "max_loss_streak": result.max_loss_streak,
        }
        json_path = output_dir / f"{symbol}_summary.json"
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2)
        logger.info("📋 Summary saved: {}", json_path)

        # Save trade log
        if result.trades:
            trades_path = output_dir / f"{symbol}_trades.json"
            with open(trades_path, "w") as f:
                json.dump(result.trades, f, indent=2)
            logger.info("📝 Trade log saved: {} ({} trades)", trades_path, len(result.trades))

    @staticmethod
    def print_summary(result: BacktestResult, symbol: str = "BACKTEST") -> None:
        """Print a formatted summary to console."""
        print(f"\n{'='*60}")
        print(f"  ⚡ YOG'Z Backtest Report — {symbol}")
        print(f"{'='*60}")
        print(f"  💰 Total PnL:        ${result.total_pnl:+,.2f} ({result.total_return_pct:+.1f}%)")
        print(f"  💼 Final Balance:     ${result.final_balance:,.2f}")
        print(f"  📊 Total Trades:      {result.total_trades}")
        print(f"  🎯 Win Rate:          {result.win_rate:.1%}")
        print(f"  ⚖️  Profit Factor:     {result.profit_factor:.2f}")
        print(f"  📈 Sharpe Ratio:      {result.sharpe_ratio:.2f}")
        print(f"  📉 Sortino Ratio:     {result.sortino_ratio:.2f}")
        print(f"  🔻 Max Drawdown:      {result.max_drawdown_pct:.1f}%")
        print(f"  📐 Calmar Ratio:      {result.calmar_ratio:.2f}")
        print(f"  🎲 Avg R-Multiple:    {result.avg_r_multiple:.2f}R")
        print(f"  💡 Expectancy:        ${result.expectancy:+,.2f}")
        print(f"  🃏 Kelly Criterion:   {result.kelly_criterion:.1%}")
        print(f"  {'─'*60}")
        print(f"  🏆 Avg Win:           ${result.avg_win:+,.2f}")
        print(f"  💀 Avg Loss:          ${result.avg_loss:+,.2f}")
        print(f"  🚀 Largest Win:       ${result.largest_win:+,.2f}")
        print(f"  💥 Largest Loss:      ${result.largest_loss:+,.2f}")
        print(f"  🔥 Max Win Streak:    {result.max_win_streak}")
        print(f"  ❄️  Max Loss Streak:   {result.max_loss_streak}")
        print(f"  ⏱️  Avg Hold:          {result.avg_hold_bars:.0f} bars")
        print(f"  {'─'*60}")
        print(f"  💸 Total Fees:        ${result.total_fees:,.2f}")
        print(f"  📊 Total Funding:     ${result.total_funding:,.2f}")
        print(f"  {'─'*60}")
        print(f"  📋 Exit Reasons:")
        for reason, count in sorted(result.exit_reasons.items(), key=lambda x: -x[1]):
            pct = count / result.total_trades * 100 if result.total_trades else 0
            print(f"     {reason:25s} {count:4d} ({pct:.0f}%)")
        print(f"{'='*60}\n")
