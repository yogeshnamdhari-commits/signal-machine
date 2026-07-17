"""
Walk-Forward Validator — Institutional-grade out-of-sample validation.
Validates whether a strategy generalizes to unseen market data by:
1. Optimizing parameters on training data (2023-09 → 2024-06)
2. Running the winning configuration on test data (2024-07 → 2024-12)
3. Measuring edge decay and overfitting

No parameter tuning on test data. Pure out-of-sample validation.

OPTIMIZATION: Precomputes institutional proxies ONCE, then sweeps parameters
via fast signal filtering. ~100x faster than naive per-combo backtest.
"""
from __future__ import annotations

import asyncio
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger


class _NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtesting.backtester import BacktestConfig, BacktestEngine, BacktestResult, Trade
from backtesting.historical_data import HistoricalDataEngine
from core.institutional_scoring_engine import InstitutionalScoringEngine


# ══════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════════════

@dataclass
class ParameterConfig:
    """Single parameter combination for the strategy."""
    confidence: float
    score_threshold: float
    factor_threshold: int
    no_trade_zone: Optional[Tuple[float, float]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "confidence": self.confidence,
            "score_threshold": self.score_threshold,
            "factor_threshold": self.factor_threshold,
            "no_trade_zone": list(self.no_trade_zone) if self.no_trade_zone else None,
        }

    def label(self) -> str:
        ntz = f"{self.no_trade_zone[0]:.2f}-{self.no_trade_zone[1]:.2f}" if self.no_trade_zone else "None"
        return f"conf={self.confidence:.2f} score={self.score_threshold:.0f} factors={self.factor_threshold} ntz={ntz}"


@dataclass
class PrecomputedBar:
    """Precomputed institutional metrics for a single bar."""
    bar_index: int
    open_time: Any
    price: float
    confidence: float       # adjusted_score / 100
    adjusted_score: float   # institutional score + rsi boost
    active_factors: int
    bias_score: float       # 0-1, center=0.5
    side: str               # LONG / SHORT / NEUTRAL
    stop_loss: float
    take_profit: float


@dataclass
class PrecomputedTrade:
    """Precomputed outcome for a single signal — independent SL/TP resolution."""
    entry_bar: int
    exit_bar: int
    entry_time: Any
    exit_time: Any
    side: str
    entry_price: float
    exit_price: float
    pnl: float
    exit_reason: str
    hold_time_min: float
    symbol: str


@dataclass
class MonthlyMetrics:
    """Monthly performance breakdown."""
    year: int
    month: int
    trades: int
    wins: int
    losses: int
    win_rate: float
    net_pnl: float
    profit_factor: float
    max_drawdown: float


@dataclass
class WalkForwardReport:
    """Complete walk-forward validation report."""
    train_trades: int
    train_win_rate: float
    train_profit_factor: float
    train_drawdown: float
    train_net_pnl: float
    train_monthly: List[MonthlyMetrics]
    train_equity_curve: List[float]
    train_all_trades: List[Dict[str, Any]]
    best_params: ParameterConfig
    total_combinations_tested: int
    test_trades: int
    test_win_rate: float
    test_profit_factor: float
    test_drawdown: float
    test_net_pnl: float
    test_monthly: List[MonthlyMetrics]
    test_equity_curve: List[float]
    test_all_trades: List[Dict[str, Any]]
    pass_pf: bool
    pass_wr: bool
    pass_dd: bool
    pass_trades: bool
    pass_net: bool
    overall_pass: bool
    edge_decay_pct: float
    overfit_assessment: str
    production_readiness_score: int
    longest_losing_streak: int
    longest_winning_streak: int
    avg_trade: float
    median_trade: float
    best_trade: float
    worst_trade: float
    symbol_results: Dict[str, Dict[str, Any]]


# ══════════════════════════════════════════════════════════════
# FAST PNL SIMULATOR
# ══════════════════════════════════════════════════════════════

def _precompute_trade_outcomes(
    precomputed: List[PrecomputedBar],
    ohlcv: pd.DataFrame,
    config: BacktestConfig,
    symbol: str,
) -> List[PrecomputedTrade]:
    """
    Precompute independent trade outcomes for each signal.
    For each PrecomputedBar, scan forward to find SL/TP exit.
    Returns PrecomputedTrade list (no position overlap — handled during sweep).
    """
    if not precomputed:
        return []

    highs = ohlcv["high"].values
    lows = ohlcv["low"].values
    closes = ohlcv["close"].values
    open_times = ohlcv["open_time"].values
    n = len(ohlcv)

    outcomes: List[PrecomputedTrade] = []
    for bar in precomputed:
        idx = bar.bar_index
        price = bar.price
        side = bar.side
        sl = bar.stop_loss
        tp = bar.take_profit

        # Entry with slippage
        spread = price * config.spread_pct
        base_slip = max(price * config.slippage_pct, price * config.min_slippage_bps / 10_000)
        if side == "LONG":
            entry_price = price + spread / 2 + base_slip
        else:
            entry_price = price - spread / 2 - base_slip

        # Scan forward for SL/TP
        exit_bar = n - 1
        exit_price_raw = closes[-1]
        reason = "backtest_end"

        for j in range(idx + 1, n):
            h = highs[j]
            l = lows[j]
            hit_sl = False
            hit_tp = False
            if side == "LONG":
                if l <= sl:
                    hit_sl = True
                if h >= tp:
                    hit_tp = True
            else:
                if h >= sl:
                    hit_sl = True
                if l <= tp:
                    hit_tp = True
            if hit_sl or hit_tp:
                exit_bar = j
                exit_price_raw = sl if hit_sl else tp
                reason = "stop_loss" if hit_sl else "take_profit"
                break

        # Exit with slippage
        exit_slip = max(exit_price_raw * config.slippage_pct, exit_price_raw * config.min_slippage_bps / 10_000)
        if side == "LONG":
            actual_exit = exit_price_raw - exit_slip
            gross_pnl = (actual_exit - entry_price)
        else:
            actual_exit = exit_price_raw + exit_slip
            gross_pnl = (entry_price - actual_exit)

        bars_held = exit_bar - idx
        notional = entry_price  # per unit
        fees = (entry_price + actual_exit) * config.taker_fee  # per unit
        funding_periods = bars_held / 96 if bars_held > 0 else 0
        funding = entry_price * config.funding_rate_pct * funding_periods
        pnl_per_unit = gross_pnl - fees - funding

        outcomes.append(PrecomputedTrade(
            entry_bar=idx,
            exit_bar=exit_bar,
            entry_time=open_times[idx],
            exit_time=open_times[exit_bar],
            side=side,
            entry_price=round(entry_price, 4),
            exit_price=round(actual_exit, 4),
            pnl=pnl_per_unit,
            exit_reason=reason,
            hold_time_min=bars_held * 60,
            symbol=symbol,
        ))

    return outcomes


def _fast_simulate_from_outcomes(
    outcomes: List[PrecomputedTrade],
    config: BacktestConfig,
) -> List[Dict[str, Any]]:
    """
    Ultra-fast trade simulation from precomputed outcomes.
    Stitches non-overlapping trades PER SYMBOL with capital tracking.
    """
    if not outcomes:
        return []

    # Group by symbol for per-symbol position overlap
    by_symbol: Dict[str, List[PrecomputedTrade]] = {}
    for oc in outcomes:
        by_symbol.setdefault(oc.symbol, []).append(oc)

    trades: List[Dict[str, Any]] = []
    capital = config.initial_capital

    for symbol, sym_outcomes in by_symbol.items():
        sym_outcomes.sort(key=lambda t: t.entry_bar)
        last_exit_bar = -1

        for oc in sym_outcomes:
            if oc.entry_bar <= last_exit_bar:
                continue

            size = (capital * config.risk_per_trade_pct * config.leverage) / oc.entry_price if oc.entry_price > 0 else 0
            max_size = (capital * config.max_position_pct * config.leverage) / oc.entry_price if oc.entry_price > 0 else 0
            size = min(size, max_size)
            if size <= 0:
                continue

            net_pnl = oc.pnl * size
            capital += net_pnl
            last_exit_bar = oc.exit_bar

            trades.append({
                "symbol": oc.symbol,
                "side": oc.side,
                "entry_price": oc.entry_price,
                "exit_price": oc.exit_price,
                "pnl": round(net_pnl, 2),
                "fees": round((oc.entry_price + oc.exit_price) * config.taker_fee * size, 2),
                "slippage": 0.0,
                "entry_time": oc.entry_time.isoformat() if hasattr(oc.entry_time, "isoformat") else str(oc.entry_time),
                "exit_time": oc.exit_time.isoformat() if hasattr(oc.exit_time, "isoformat") else str(oc.exit_time),
                "exit_reason": oc.exit_reason,
                "hold_time_min": round(oc.hold_time_min, 1),
            })

    return trades


def _compute_metrics(trades: List[Dict[str, Any]], initial_capital: float = 10_000) -> Dict[str, Any]:
    """Compute aggregated metrics from trade list."""
    if not trades:
        return {"trades": 0, "wr": 0, "pf": 0, "dd": 0, "net": 0, "equity_curve": [initial_capital],
                "win_count": 0, "loss_count": 0}

    total = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_count = len(wins)
    wr = (win_count / total * 100) if total > 0 else 0

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = sum(abs(t["pnl"]) for t in losses)
    pf = gross_profit / gross_loss if gross_loss > 0 else 999.99
    net = sum(t["pnl"] for t in trades)

    equity = initial_capital
    peak = equity
    max_dd = 0.0
    curve = [equity]
    for t in trades:
        equity += t["pnl"]
        curve.append(equity)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    return {
        "trades": total, "wr": wr, "pf": pf, "dd": max_dd * 100, "net": net,
        "equity_curve": curve, "win_count": win_count, "loss_count": total - win_count,
    }


# ══════════════════════════════════════════════════════════════
# PRECOMPUTE ENGINE
# ══════════════════════════════════════════════════════════════

def _precompute_bars(df: pd.DataFrame, symbol: str) -> List[PrecomputedBar]:
    """
    Precompute institutional proxies for ALL bars once.
    Returns list of PrecomputedBar with raw scores.
    Parameter filtering happens later during sweep.
    """
    scorer = InstitutionalScoringEngine()
    results: List[PrecomputedBar] = []

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values
    n = len(df)

    for i in range(50, n):
        price = closes[i]

        s_start = max(0, i - 10)
        m_start = max(0, i - 20)
        l_start = max(0, i - 50)

        closes_s = closes[s_start:i+1]
        closes_m = closes[m_start:i+1]
        closes_l = closes[l_start:i+1]
        highs_m = highs[m_start:i+1]
        lows_m = lows[m_start:i+1]
        volumes_m = volumes[m_start:i+1]
        volumes_l = volumes[l_start:i+1]

        # Absorption
        vol_avg = np.mean(volumes_m)
        vol_ratio = volumes_m[-1] / vol_avg if vol_avg > 0 else 1.0
        price_range = highs_m[-1] - lows_m[-1]
        avg_range = np.mean(highs_m - lows_m)
        range_ratio = price_range / avg_range if avg_range > 0 else 1.0
        raw_absorption = vol_ratio / max(range_ratio, 0.01)
        absorption_score = max(0.0, min(1.0, (raw_absorption - 0.5) / 2.5)) if raw_absorption > 0.5 else 0.0

        # Liquidity Sweep
        local_low = np.min(lows_m[:-1])
        local_high = np.max(highs_m[:-1])
        local_range = local_high - local_low if local_high != local_low else price * 0.01

        bull_wick = max(0, local_low - lows_m[-1]) / local_range
        bear_wick = max(0, highs_m[-1] - local_high) / local_range

        if bull_wick > 0 and closes_m[-1] > local_low:
            sweep_score = min(bull_wick * 3.0, 1.0)
        elif bear_wick > 0 and closes_m[-1] < local_high:
            sweep_score = -min(bear_wick * 3.0, 1.0)
        else:
            dist_to_low = abs(price - local_low) / local_range
            dist_to_high = abs(price - local_high) / local_range
            proximity = max(0, 1.0 - min(dist_to_low, dist_to_high) * 5)
            sweep_score = proximity * 0.3

        # Market Regime
        sma10 = np.mean(closes_s[-10:]) if len(closes_s) >= 10 else price
        sma20 = np.mean(closes_m[-20:]) if len(closes_m) >= 20 else price
        sma50 = np.mean(closes_l[-50:]) if len(closes_l) >= 50 else price

        regime_fit = 0.0
        if price > sma10: regime_fit += 0.25
        if sma10 > sma20: regime_fit += 0.25
        if sma20 > sma50: regime_fit += 0.25
        if price > sma50: regime_fit += 0.25

        # Liquidation
        dist_to_low_val = abs(price - local_low) / price
        dist_to_high_val = abs(price - local_high) / price
        min_dist = min(dist_to_low_val, dist_to_high_val)
        liq_score = max(0.0, min(1.0, (0.05 - min_dist) / 0.048)) if min_dist < 0.05 else 0.0

        # Open Interest proxy
        vol_short = np.mean(volumes_l[-5:]) if len(volumes_l) >= 5 else vol_avg
        vol_long = np.mean(volumes_l) if len(volumes_l) > 0 else vol_avg
        vol_trend = vol_short / vol_long if vol_long > 0 else 1.0
        oi_score = max(0.0, min(1.0, (vol_trend - 0.7) / 1.1)) if vol_trend > 0.7 else 0.0

        # Exchange Flow
        price_move = abs(closes_m[-1] - closes_m[-2]) / closes_m[-2] if len(closes_m) >= 2 else 0
        flow_intensity = vol_ratio * price_move * 100
        flow_score = max(0.0, min(1.0, flow_intensity / 1.5))

        # Funding proxy
        ema21 = np.mean(closes_m[-21:]) if len(closes_m) >= 21 else price
        extension = (price - ema21) / ema21
        funding_score = max(0.0, min(1.0, (abs(extension) - 0.003) / 0.027)) if abs(extension) > 0.003 else 0.0

        # RSI
        if len(closes_l) >= 15:
            deltas = np.diff(closes_l[-16:])
            gains_arr = np.where(deltas > 0, deltas, 0)
            losses_arr = np.where(deltas < 0, -deltas, 0)
            avg_gain = np.mean(gains_arr)
            avg_loss = np.mean(losses_arr)
            rs = avg_gain / avg_loss if avg_loss > 0 else 10
            rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 50

        rsi_score = abs(rsi - 50) / 50

        # Active factors
        active_factors = sum([
            1 if absorption_score > 0.50 else 0,
            1 if abs(sweep_score) > 0.30 else 0,
            1 if regime_fit > 0.55 else 0,
            1 if liq_score > 0.50 else 0,
            1 if oi_score > 0.50 else 0,
            1 if flow_score > 0.50 else 0,
            1 if funding_score > 0.50 else 0,
            1 if rsi_score > 0.30 else 0,
        ])

        # Composite score
        sweep_dir = 0.6 if sweep_score > 0.1 else 0.4 if sweep_score < -0.1 else 0.5
        rsi_dir = 0.6 if rsi < 40 else 0.4 if rsi > 60 else 0.5

        mock_data = {
            "absorption_score": absorption_score,
            "delta_score": sweep_dir,
            "cvd_score": rsi_dir,
            "oi_score": oi_score,
            "funding_score": funding_score,
            "flow_score": flow_score,
            "liq_score": liq_score,
            "regime_fit": regime_fit,
        }

        res = scorer.calculate_score(mock_data)
        rsi_boost = (rsi_score - 0.5) * 10
        adjusted_score = res["score"] + rsi_boost
        confidence = adjusted_score / 100

        # Bias
        sweep_norm = max(0, min(1, (sweep_score + 1) / 2))
        rsi_norm = max(0, min(1, (100 - rsi) / 100))
        bias_score = (sweep_norm + rsi_norm + oi_score + flow_score) / 4.0

        # Direction
        if bias_score > 0.55:
            side = "LONG"
        elif bias_score < 0.45:
            side = "SHORT"
        else:
            side = "NEUTRAL"

        # Dynamic SL/TP
        atr_vals = highs[max(0, i-14):i+1] - lows[max(0, i-14):i+1]
        atr_val = np.mean(atr_vals) if len(atr_vals) > 0 else price * 0.015
        sl_mult, tp_mult = 2.0, 3.5

        if side == "LONG":
            stop_loss = price - (atr_val * sl_mult)
            take_profit = price + (atr_val * tp_mult)
        elif side == "SHORT":
            stop_loss = price + (atr_val * sl_mult)
            take_profit = price - (atr_val * tp_mult)
        else:
            stop_loss = price
            take_profit = price

        results.append(PrecomputedBar(
            bar_index=i,
            open_time=df["open_time"].iloc[i],
            price=price,
            confidence=confidence,
            adjusted_score=adjusted_score,
            active_factors=active_factors,
            bias_score=bias_score,
            side=side,
            stop_loss=stop_loss,
            take_profit=take_profit,
        ))

    return results


def _filter_signals(
    precomputed: List[PrecomputedBar],
    confidence_thresh: float,
    score_thresh: float,
    factor_thresh: int,
    no_trade_zone: Optional[Tuple[float, float]],
) -> List[PrecomputedBar]:
    """Fast parameter filtering on precomputed bars."""
    filtered = []
    for bar in precomputed:
        if bar.confidence < confidence_thresh:
            continue
        if bar.adjusted_score < score_thresh:
            continue
        if bar.active_factors < factor_thresh:
            continue
        if bar.side == "NEUTRAL":
            continue
        if no_trade_zone is not None:
            if no_trade_zone[0] <= bar.bias_score <= no_trade_zone[1]:
                continue
        filtered.append(bar)
    return filtered


# ══════════════════════════════════════════════════════════════
# WALK-FORWARD VALIDATOR
# ══════════════════════════════════════════════════════════════

class WalkForwardValidator:
    """
    Institutional-grade walk-forward validation.

    Splits data into:
    - Training: 2023-09-01 → 2024-06-30
    - Testing: 2024-07-01 → 2024-12-31

    Optimizes on training data ONLY.
    Validates on test data with NO further tuning.
    """

    CONFIDENCE_RANGE = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    SCORE_THRESHOLD_RANGE = [25, 30, 35, 40, 45, 50, 55, 60]
    FACTOR_THRESHOLD_RANGE = [1, 2, 3, 4, 5]
    NO_TRADE_ZONE_OPTIONS: List[Optional[Tuple[float, float]]] = [
        None,
        (0.48, 0.52),
        (0.45, 0.55),
        (0.40, 0.60),
    ]

    TRAIN_START = datetime(2023, 9, 1)
    TRAIN_END = datetime(2024, 6, 30)
    TEST_START = datetime(2024, 7, 1)
    TEST_END = datetime(2024, 12, 31)

    MIN_PROFIT_FACTOR = 1.30
    MIN_WIN_RATE = 48.0
    MAX_DRAWDOWN = 10.0
    MIN_TRADES = 50
    MIN_NET_PROFIT = 0.0

    SYMBOLS = ["ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"]

    def __init__(self) -> None:
        self._data_engine = HistoricalDataEngine()
        self._backtest_config = BacktestConfig(
            initial_capital=10_000,
            leverage=10,
            risk_per_trade_pct=0.005,
        )
        self._symbol_data: Dict[str, pd.DataFrame] = {}
        self._train_data: Dict[str, pd.DataFrame] = {}
        self._test_data: Dict[str, pd.DataFrame] = {}
        self._train_precomputed: Dict[str, List[PrecomputedBar]] = {}
        self._test_precomputed: Dict[str, List[PrecomputedBar]] = {}
        self._train_outcomes: Dict[str, List[PrecomputedTrade]] = {}
        self._test_outcomes: Dict[str, List[PrecomputedTrade]] = {}

    # ══════════════════════════════════════════════════════════════
    # PUBLIC API
    # ══════════════════════════════════════════════════════════════

    async def run(self) -> WalkForwardReport:
        """Execute the full walk-forward validation pipeline."""
        logger.info("═══ Walk-Forward Validator Starting ═══")
        logger.info("Train: {} → {}", self.TRAIN_START.date(), self.TRAIN_END.date())
        logger.info("Test:  {} → {}", self.TEST_START.date(), self.TEST_END.date())

        await self.load_data()
        self.split_train_test()
        self.precompute_all()
        best_config, train_trades = await self.optimize_on_train()
        test_trades = await self.validate_on_test(best_config)
        report = self.generate_report(best_config, train_trades, test_trades)
        await self._export_reports(report)
        return report

    # ══════════════════════════════════════════════════════════════
    # STEP 1: LOAD DATA
    # ══════════════════════════════════════════════════════════════

    async def load_data(self) -> None:
        """Load historical data for all symbols."""
        logger.info("Loading historical data for {} symbols...", len(self.SYMBOLS))
        await self._data_engine.initialize()

        for symbol in self.SYMBOLS:
            try:
                df = await self._data_engine.get_historical_data(symbol, "1h", days=1000)
                if not df.empty:
                    self._symbol_data[symbol] = df
                    logger.info("  ✓ {} — {} bars ({})", symbol, len(df),
                                f"{df['open_time'].min().date()} → {df['open_time'].max().date()}")
                else:
                    logger.warning("  ✗ {} — no data", symbol)
            except Exception as e:
                logger.error("  ✗ {} — {}", symbol, e)

        await self._data_engine.stop()

        if not self._symbol_data:
            raise RuntimeError("No historical data available for any symbol")

        logger.info("Data loaded: {} symbols", len(self._symbol_data))

    # ══════════════════════════════════════════════════════════════
    # STEP 2: SPLIT TRAIN/TEST
    # ══════════════════════════════════════════════════════════════

    def split_train_test(self) -> None:
        """Split data into training and testing windows."""
        for symbol, df in self._symbol_data.items():
            train_mask = (df["open_time"] >= self.TRAIN_START) & (df["open_time"] <= self.TRAIN_END)
            test_mask = (df["open_time"] >= self.TEST_START) & (df["open_time"] <= self.TEST_END)

            self._train_data[symbol] = df.loc[train_mask].reset_index(drop=True)
            self._test_data[symbol] = df.loc[test_mask].reset_index(drop=True)

            logger.info("  {} — Train: {} bars, Test: {} bars",
                        symbol, len(self._train_data[symbol]), len(self._test_data[symbol]))

    # ══════════════════════════════════════════════════════════════
    # STEP 3: PRECOMPUTE PROXIES (ONCE)
    # ══════════════════════════════════════════════════════════════

    def precompute_all(self) -> None:
        """Precompute institutional proxies AND trade outcomes for all bars."""
        logger.info("Precomputing institutional proxies and trade outcomes...")
        for symbol in self._symbol_data:
            logger.info("  {} — computing train...", symbol)
            self._train_precomputed[symbol] = _precompute_bars(self._train_data[symbol], symbol)
            self._train_outcomes[symbol] = _precompute_trade_outcomes(
                self._train_precomputed[symbol], self._train_data[symbol],
                self._backtest_config, symbol,
            )
            logger.info("  {} — train: {} signals, {} trade outcomes", symbol,
                        len(self._train_precomputed[symbol]), len(self._train_outcomes[symbol]))
            logger.info("  {} — computing test...", symbol)
            self._test_precomputed[symbol] = _precompute_bars(self._test_data[symbol], symbol)
            self._test_outcomes[symbol] = _precompute_trade_outcomes(
                self._test_precomputed[symbol], self._test_data[symbol],
                self._backtest_config, symbol,
            )
            logger.info("  {} — test: {} signals, {} trade outcomes", symbol,
                        len(self._test_precomputed[symbol]), len(self._test_outcomes[symbol]))
        logger.info("Precomputation complete.")

    # ══════════════════════════════════════════════════════════════
    # STEP 4: OPTIMIZE ON TRAINING SET
    # ══════════════════════════════════════════════════════════════

    async def optimize_on_train(self) -> Tuple[ParameterConfig, List[Dict[str, Any]]]:
        """
        Exhaustive parameter sweep on training data.
        Uses precomputed outcomes + fast stitching for speed.
        """
        combinations = list(product(
            self.CONFIDENCE_RANGE,
            self.SCORE_THRESHOLD_RANGE,
            self.FACTOR_THRESHOLD_RANGE,
            self.NO_TRADE_ZONE_OPTIONS,
        ))

        total = len(combinations)
        logger.info("Parameter sweep: {} combinations", total)

        # Build index: map bar_index -> PrecomputedBar for fast lookup
        train_bar_maps: Dict[str, Dict[int, PrecomputedBar]] = {}
        for symbol in self._train_precomputed:
            train_bar_maps[symbol] = {b.bar_index: b for b in self._train_precomputed[symbol]}

        best_pf = -1.0
        best_dd = 100.0
        best_wr = 0.0
        best_config = None
        best_trades: List[Dict[str, Any]] = []
        tested = 0

        for conf, score, factors, ntz in combinations:
            tested += 1
            all_outcomes: List[PrecomputedTrade] = []

            for symbol in self._train_data:
                bar_map = train_bar_maps[symbol]
                symbol_outcomes = self._train_outcomes[symbol]

                filtered_outcomes = []
                for oc in symbol_outcomes:
                    bar = bar_map.get(oc.entry_bar)
                    if bar is None:
                        continue
                    if bar.confidence < conf:
                        continue
                    if bar.adjusted_score < score:
                        continue
                    if bar.active_factors < factors:
                        continue
                    if bar.side == "NEUTRAL":
                        continue
                    if ntz is not None:
                        if ntz[0] <= bar.bias_score <= ntz[1]:
                            continue
                    filtered_outcomes.append(oc)

                all_outcomes.extend(filtered_outcomes)

            if not all_outcomes:
                continue

            trades = _fast_simulate_from_outcomes(all_outcomes, self._backtest_config)
            if not trades:
                continue

            metrics = _compute_metrics(trades, self._backtest_config.initial_capital)
            pf = metrics["pf"]
            dd = metrics["dd"]
            wr = metrics["wr"]

            is_better = False
            if pf > best_pf:
                is_better = True
            elif pf == best_pf and dd < best_dd:
                is_better = True
            elif pf == best_pf and dd == best_dd and wr > best_wr:
                is_better = True

            if is_better:
                best_pf = pf
                best_dd = dd
                best_wr = wr
                best_config = ParameterConfig(confidence=conf, score_threshold=score,
                                              factor_threshold=factors, no_trade_zone=ntz)
                best_trades = trades

            if tested % 200 == 0:
                logger.info("  Progress: {}/{} — best PF={:.2f}", tested, total, best_pf)

        if best_config is None:
            raise RuntimeError("No valid configurations found during training sweep")

        logger.info("Best config: {} — PF={:.2f}, WR={:.1f}%, DD={:.1f}%, Trades={}",
                     best_config.label(), best_pf, best_wr, best_dd, len(best_trades))

        return best_config, best_trades

    # ══════════════════════════════════════════════════════════════
    # STEP 5: VALIDATE ON TEST SET
    # ══════════════════════════════════════════════════════════════

    async def validate_on_test(self, config: ParameterConfig) -> List[Dict[str, Any]]:
        """Run winning config on test data. No tuning. Pure OOS."""
        logger.info("Running test validation: {}", config.label())

        all_outcomes: List[PrecomputedTrade] = []
        for symbol in self._test_data:
            bar_map = {b.bar_index: b for b in self._test_precomputed[symbol]}
            for oc in self._test_outcomes[symbol]:
                bar = bar_map.get(oc.entry_bar)
                if bar is None:
                    continue
                if bar.confidence < config.confidence:
                    continue
                if bar.adjusted_score < config.score_threshold:
                    continue
                if bar.active_factors < config.factor_threshold:
                    continue
                if bar.side == "NEUTRAL":
                    continue
                if config.no_trade_zone is not None:
                    if config.no_trade_zone[0] <= bar.bias_score <= config.no_trade_zone[1]:
                        continue
                all_outcomes.append(oc)

        trades = _fast_simulate_from_outcomes(all_outcomes, self._backtest_config)

        metrics = _compute_metrics(trades, self._backtest_config.initial_capital)
        logger.info("Test results: PF={:.2f}, WR={:.1f}%, DD={:.1f}%, Trades={}, Net=${:,.2f}",
                     metrics["pf"], metrics["wr"], metrics["dd"], metrics["trades"], metrics["net"])

        return trades

    # ══════════════════════════════════════════════════════════════
    # STEP 6: GENERATE REPORT
    # ══════════════════════════════════════════════════════════════

    def generate_report(
        self,
        best_config: ParameterConfig,
        train_trades: List[Dict[str, Any]],
        test_trades: List[Dict[str, Any]],
    ) -> WalkForwardReport:
        """Generate comprehensive walk-forward report."""
        train_m = _compute_metrics(train_trades, self._backtest_config.initial_capital)
        test_m = _compute_metrics(test_trades, self._backtest_config.initial_capital)

        # Pass/Fail
        pass_pf = test_m["pf"] > self.MIN_PROFIT_FACTOR
        pass_wr = test_m["wr"] > self.MIN_WIN_RATE
        pass_dd = test_m["dd"] < self.MAX_DRAWDOWN
        pass_trades = test_m["trades"] > self.MIN_TRADES
        pass_net = test_m["net"] > self.MIN_NET_PROFIT
        overall_pass = pass_pf and pass_wr and pass_dd and pass_trades and pass_net

        # Edge Decay
        train_pf = train_m["pf"] if train_m["pf"] < 999 else 999.99
        test_pf = test_m["pf"] if test_m["pf"] < 999 else 999.99
        if train_pf > 0:
            edge_decay = ((train_pf - test_pf) / train_pf) * 100
        else:
            edge_decay = 0.0

        if edge_decay < 0:
            overfit = "NEGATIVE DECAY — Test outperformed train (suspicious)"
        elif edge_decay <= 15:
            overfit = "Excellent — Minimal overfitting"
        elif edge_decay <= 30:
            overfit = "Acceptable — Mild generalization loss"
        elif edge_decay <= 50:
            overfit = "Weak — Significant overfitting risk"
        else:
            overfit = "OVERFIT — Strategy does not generalize"

        # Production Readiness
        prod_score = self._calculate_production_score(
            test_pf, test_m["wr"], test_m["dd"], test_m["trades"], test_m["net"], edge_decay
        )

        # Monthly
        train_monthly = self._calculate_monthly(train_trades)
        test_monthly = self._calculate_monthly(test_trades)

        # Streaks
        longest_loss, longest_win = self._calculate_streaks(test_trades)

        # Trade stats
        pnls = [t["pnl"] for t in test_trades] if test_trades else [0]
        avg_trade = float(np.mean(pnls))
        median_trade = float(np.median(pnls))
        best_trade_val = float(max(pnls))
        worst_trade_val = float(min(pnls))

        # Symbol breakdown
        symbol_results = self._symbol_breakdown(test_trades)

        total_combos = (
            len(self.CONFIDENCE_RANGE)
            * len(self.SCORE_THRESHOLD_RANGE)
            * len(self.FACTOR_THRESHOLD_RANGE)
            * len(self.NO_TRADE_ZONE_OPTIONS)
        )

        # Augment trade dicts with phase
        for t in train_trades:
            t["phase"] = "train"
        for t in test_trades:
            t["phase"] = "test"

        return WalkForwardReport(
            train_trades=train_m["trades"],
            train_win_rate=train_m["wr"],
            train_profit_factor=train_pf,
            train_drawdown=train_m["dd"],
            train_net_pnl=train_m["net"],
            train_monthly=train_monthly,
            train_equity_curve=train_m["equity_curve"],
            train_all_trades=train_trades,
            best_params=best_config,
            total_combinations_tested=total_combos,
            test_trades=test_m["trades"],
            test_win_rate=test_m["wr"],
            test_profit_factor=test_pf,
            test_drawdown=test_m["dd"],
            test_net_pnl=test_m["net"],
            test_monthly=test_monthly,
            test_equity_curve=test_m["equity_curve"],
            test_all_trades=test_trades,
            pass_pf=pass_pf,
            pass_wr=pass_wr,
            pass_dd=pass_dd,
            pass_trades=pass_trades,
            pass_net=pass_net,
            overall_pass=overall_pass,
            edge_decay_pct=edge_decay,
            overfit_assessment=overfit,
            production_readiness_score=prod_score,
            longest_losing_streak=longest_loss,
            longest_winning_streak=longest_win,
            avg_trade=avg_trade,
            median_trade=median_trade,
            best_trade=best_trade_val,
            worst_trade=worst_trade_val,
            symbol_results=symbol_results,
        )

    # ══════════════════════════════════════════════════════════════
    # ANALYSIS HELPERS
    # ══════════════════════════════════════════════════════════════

    def _calculate_monthly(self, trades: List[Dict[str, Any]]) -> List[MonthlyMetrics]:
        """Calculate monthly PnL, win rate, and drawdown."""
        if not trades:
            return []

        monthly: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
        for t in trades:
            dt = datetime.fromisoformat(t["exit_time"]) if isinstance(t["exit_time"], str) else t["exit_time"]
            key = (dt.year, dt.month)
            monthly.setdefault(key, []).append(t)

        results = []
        for (year, month), month_trades in sorted(monthly.items()):
            wins = sum(1 for t in month_trades if t["pnl"] > 0)
            losses = len(month_trades) - wins
            wr = (wins / len(month_trades) * 100) if month_trades else 0
            net = sum(t["pnl"] for t in month_trades)
            gross_profit = sum(t["pnl"] for t in month_trades if t["pnl"] > 0)
            gross_loss = sum(abs(t["pnl"]) for t in month_trades if t["pnl"] <= 0)
            pf = gross_profit / gross_loss if gross_loss > 0 else 999.99

            equity = 10_000
            peak = equity
            max_dd = 0
            for t in month_trades:
                equity += t["pnl"]
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

            results.append(MonthlyMetrics(
                year=year, month=month,
                trades=len(month_trades), wins=wins, losses=losses,
                win_rate=wr, net_pnl=net,
                profit_factor=pf,
                max_drawdown=max_dd * 100,
            ))

        return results

    def _calculate_streaks(self, trades: List[Dict[str, Any]]) -> Tuple[int, int]:
        """Calculate longest losing and winning streaks."""
        if not trades:
            return 0, 0

        max_loss = 0
        max_win = 0
        cur_loss = 0
        cur_win = 0

        for t in trades:
            if t["pnl"] > 0:
                cur_win += 1
                cur_loss = 0
                max_win = max(max_win, cur_win)
            else:
                cur_loss += 1
                cur_win = 0
                max_loss = max(max_loss, cur_loss)

        return max_loss, max_win

    def _symbol_breakdown(self, trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Break down results by symbol."""
        symbols: Dict[str, List[Dict[str, Any]]] = {}
        for t in trades:
            symbols.setdefault(t["symbol"], []).append(t)

        result = {}
        for sym, sym_trades in symbols.items():
            wins = sum(1 for t in sym_trades if t["pnl"] > 0)
            gross_profit = sum(t["pnl"] for t in sym_trades if t["pnl"] > 0)
            gross_loss = sum(abs(t["pnl"]) for t in sym_trades if t["pnl"] <= 0)
            result[sym] = {
                "trades": len(sym_trades),
                "wins": wins,
                "losses": len(sym_trades) - wins,
                "win_rate": (wins / len(sym_trades) * 100) if sym_trades else 0,
                "net_pnl": sum(t["pnl"] for t in sym_trades),
                "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 999.99,
            }

        return result

    def _calculate_production_score(
        self,
        pf: float, wr: float, dd: float, trades: int, net: float, decay: float
    ) -> int:
        """Calculate production readiness score (0-100)."""
        score = 0

        if pf >= 2.0: score += 25
        elif pf >= 1.5: score += 20
        elif pf >= 1.3: score += 15
        elif pf >= 1.1: score += 10
        elif pf >= 1.0: score += 5

        if wr >= 55: score += 20
        elif wr >= 50: score += 15
        elif wr >= 48: score += 10
        elif wr >= 45: score += 5

        if dd <= 3: score += 20
        elif dd <= 5: score += 15
        elif dd <= 8: score += 10
        elif dd <= 10: score += 5

        if trades >= 200: score += 10
        elif trades >= 100: score += 8
        elif trades >= 50: score += 5
        elif trades >= 30: score += 3

        if net > 0:
            score += min(10, int(net / 100))

        if decay <= 0: score += 15
        elif decay <= 10: score += 12
        elif decay <= 20: score += 8
        elif decay <= 30: score += 5
        elif decay <= 50: score += 2

        return min(100, max(0, score))

    # ══════════════════════════════════════════════════════════════
    # EXPORTS
    # ══════════════════════════════════════════════════════════════

    async def _export_reports(self, report: WalkForwardReport) -> None:
        """Export all report files."""
        reports_dir = Path(__file__).resolve().parent.parent / "data" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        # 1. CSV
        csv_path = reports_dir / "walk_forward_results.csv"
        all_trades = report.train_all_trades + report.test_all_trades
        if all_trades:
            fieldnames = list(all_trades[0].keys())
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_trades)
            logger.info("Exported: {}", csv_path)

        # 2. JSON Summary
        summary_path = reports_dir / "walk_forward_summary.json"
        summary = {
            "training_period": {
                "start": self.TRAIN_START.isoformat(),
                "end": self.TRAIN_END.isoformat(),
                "trades": report.train_trades,
                "win_rate": round(report.train_win_rate, 2),
                "profit_factor": round(report.train_profit_factor, 2),
                "max_drawdown": round(report.train_drawdown, 2),
                "net_pnl": round(report.train_net_pnl, 2),
            },
            "test_period": {
                "start": self.TEST_START.isoformat(),
                "end": self.TEST_END.isoformat(),
                "trades": report.test_trades,
                "win_rate": round(report.test_win_rate, 2),
                "profit_factor": round(report.test_profit_factor, 2),
                "max_drawdown": round(report.test_drawdown, 2),
                "net_pnl": round(report.test_net_pnl, 2),
            },
            "pass_fail": {
                "pf_gt_1_30": bool(report.pass_pf),
                "wr_gt_48": bool(report.pass_wr),
                "dd_lt_10": bool(report.pass_dd),
                "trades_gt_50": bool(report.pass_trades),
                "net_profit_gt_0": bool(report.pass_net),
                "overall": bool(report.overall_pass),
            },
            "edge_decay_pct": round(report.edge_decay_pct, 2),
            "overfit_assessment": report.overfit_assessment,
            "production_readiness_score": report.production_readiness_score,
            "monthly_performance": {
                "train": [
                    {"year": m.year, "month": m.month, "trades": m.trades,
                     "win_rate": round(m.win_rate, 2), "net_pnl": round(m.net_pnl, 2),
                     "profit_factor": round(m.profit_factor, 2), "max_drawdown": round(m.max_drawdown, 2)}
                    for m in report.train_monthly
                ],
                "test": [
                    {"year": m.year, "month": m.month, "trades": m.trades,
                     "win_rate": round(m.win_rate, 2), "net_pnl": round(m.net_pnl, 2),
                     "profit_factor": round(m.profit_factor, 2), "max_drawdown": round(m.max_drawdown, 2)}
                    for m in report.test_monthly
                ],
            },
            "symbol_breakdown": {
                sym: {k: round(v, 2) if isinstance(v, float) else v for k, v in stats.items()}
                for sym, stats in report.symbol_results.items()
            },
            "streaks": {
                "longest_losing": report.longest_losing_streak,
                "longest_winning": report.longest_winning_streak,
            },
            "trade_stats": {
                "avg_trade": round(report.avg_trade, 2),
                "median_trade": round(report.median_trade, 2),
                "best_trade": round(report.best_trade, 2),
                "worst_trade": round(report.worst_trade, 2),
            },
        }
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2, cls=_NumpyEncoder)
        logger.info("Exported: {}", summary_path)

        # 3. Best Params JSON
        params_path = reports_dir / "walk_forward_best_params.json"
        best_params = {
            "confidence": float(report.best_params.confidence),
            "score_threshold": float(report.best_params.score_threshold),
            "factor_threshold": int(report.best_params.factor_threshold),
            "no_trade_zone": [float(x) for x in report.best_params.no_trade_zone] if report.best_params.no_trade_zone else None,
            "total_combinations_tested": int(report.total_combinations_tested),
            "selection_criteria": {
                "primary": "Highest Profit Factor",
                "secondary": "Lowest Drawdown",
                "tertiary": "Highest Win Rate",
            },
        }
        with open(params_path, "w") as f:
            json.dump(best_params, f, indent=2, cls=_NumpyEncoder)
        logger.info("Exported: {}", params_path)

    # ══════════════════════════════════════════════════════════════
    # CONSOLE REPORT
    # ══════════════════════════════════════════════════════════════

    @staticmethod
    def print_report(report: WalkForwardReport) -> None:
        """Print the full walk-forward report to console."""

        def _pf_str(pf: float) -> str:
            return f"{pf:.2f}" if pf < 999 else "∞"

        print()
        print("=" * 60)
        print("  WALK FORWARD REPORT")
        print("=" * 60)

        # Training Period
        print()
        print("  TRAINING PERIOD")
        print("  " + "-" * 40)
        print(f"    Trades:        {report.train_trades}")
        print(f"    Win Rate:      {report.train_win_rate:.1f}%")
        print(f"    Profit Factor: {_pf_str(report.train_profit_factor)}")
        print(f"    Drawdown:      {report.train_drawdown:.1f}%")
        print(f"    Net Profit:    ${report.train_net_pnl:,.2f}")
        print()
        print("  Selected Parameters:")
        print(f"    confidence     = {report.best_params.confidence:.2f}")
        print(f"    score          = {report.best_params.score_threshold:.0f}")
        print(f"    factors        = {report.best_params.factor_threshold}")
        ntz = report.best_params.no_trade_zone
        print(f"    no_trade_zone  = {f'{ntz[0]:.2f}-{ntz[1]:.2f}' if ntz else 'None'}")

        # Monthly Training
        if report.train_monthly:
            print()
            print("  MONTHLY TRAINING BREAKDOWN")
            print("  " + "-" * 40)
            print(f"    {'Month':<10} │ {'Trades':>6} │ {'WR':>7} │ {'PF':>7} │ {'DD':>7} │ {'Net PnL':>10}")
            print(f"    {'─'*10}─┼─{'─'*6}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*10}")
            for m in report.train_monthly:
                print(f"    {m.year}-{m.month:02d}    │ {m.trades:>6} │ {m.win_rate:>6.1f}% │ {_pf_str(m.profit_factor):>7} │ {m.max_drawdown:>6.1f}% │ ${m.net_pnl:>9.2f}")

        # Test Period
        print()
        print("  " + "-" * 40)
        print("  TEST PERIOD")
        print("  " + "-" * 40)
        print(f"    Trades:        {report.test_trades}")
        print(f"    Win Rate:      {report.test_win_rate:.1f}%")
        print(f"    Profit Factor: {_pf_str(report.test_profit_factor)}")
        print(f"    Drawdown:      {report.test_drawdown:.1f}%")
        print(f"    Net Profit:    ${report.test_net_pnl:,.2f}")

        # Monthly Test
        if report.test_monthly:
            print()
            print("  MONTHLY TEST BREAKDOWN")
            print("  " + "-" * 40)
            print(f"    {'Month':<10} │ {'Trades':>6} │ {'WR':>7} │ {'PF':>7} │ {'DD':>7} │ {'Net PnL':>10}")
            print(f"    {'─'*10}─┼─{'─'*6}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*10}")
            for m in report.test_monthly:
                print(f"    {m.year}-{m.month:02d}    │ {m.trades:>6} │ {m.win_rate:>6.1f}% │ {_pf_str(m.profit_factor):>7} │ {m.max_drawdown:>6.1f}% │ ${m.net_pnl:>9.2f}")

        # Additional Analysis
        print()
        print("  ADDITIONAL ANALYSIS")
        print("  " + "-" * 40)
        print(f"    Longest Losing Streak:  {report.longest_losing_streak}")
        print(f"    Longest Winning Streak: {report.longest_winning_streak}")
        print(f"    Average Trade:          ${report.avg_trade:,.2f}")
        print(f"    Median Trade:           ${report.median_trade:,.2f}")
        print(f"    Best Trade:             ${report.best_trade:,.2f}")
        print(f"    Worst Trade:            ${report.worst_trade:,.2f}")

        # Symbol Breakdown
        if report.symbol_results:
            print()
            print("  SYMBOL BREAKDOWN")
            print("  " + "-" * 40)
            print(f"    {'Symbol':<10} │ {'Trades':>6} │ {'WR':>7} │ {'PF':>7} │ {'Net PnL':>10}")
            print(f"    {'─'*10}─┼─{'─'*6}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*10}")
            for sym, stats in report.symbol_results.items():
                print(f"    {sym:<10} │ {stats['trades']:>6} │ {stats['win_rate']:>6.1f}% │ {_pf_str(stats['profit_factor']):>7} │ ${stats['net_pnl']:>9.2f}")

        # Equity Curve
        if report.test_equity_curve and len(report.test_equity_curve) > 1:
            print()
            print("  EQUITY CURVE (Test Period)")
            print("  " + "-" * 40)
            curve = report.test_equity_curve
            min_eq = min(curve)
            max_eq = max(curve)
            eq_range = max_eq - min_eq if max_eq != min_eq else 1
            width = 40
            step = max(1, len(curve) // 20)
            for idx in range(0, len(curve), step):
                eq = curve[idx]
                bar_len = int((eq - min_eq) / eq_range * width)
                bar = "█" * bar_len
                print(f"    ${eq:>10,.0f} │{bar}")
            eq = curve[-1]
            bar_len = int((eq - min_eq) / eq_range * width)
            bar = "█" * bar_len
            print(f"    ${eq:>10,.0f} │{bar}  ← Final")

        # Pass/Fail
        print()
        print("  " + "-" * 40)
        print("  PASS/FAIL")
        print("  " + "-" * 40)

        criteria = [
            ("PF > 1.3", report.pass_pf, _pf_str(report.test_profit_factor)),
            ("WR > 48%", report.pass_wr, f"{report.test_win_rate:.1f}%"),
            ("DD < 10%", report.pass_dd, f"{report.test_drawdown:.1f}%"),
            ("Trades > 50", report.pass_trades, str(report.test_trades)),
            ("Net Profit > 0", report.pass_net, f"${report.test_net_pnl:,.2f}"),
        ]

        for label, passed, value in criteria:
            icon = "✅" if passed else "❌"
            print(f"    {icon} {label:<18} {value}")

        print()
        if report.overall_pass:
            print("    Overall Result: ✅ PASS")
        else:
            print("    Overall Result: ❌ FAIL")

        # Edge Decay
        print()
        print("  " + "-" * 40)
        print("  ROBUSTNESS CHECK")
        print("  " + "-" * 40)
        print(f"    Training PF:    {_pf_str(report.train_profit_factor)}")
        print(f"    Testing PF:     {_pf_str(report.test_profit_factor)}")
        print(f"    Edge Decay:     {report.edge_decay_pct:.1f}%")
        print(f"    Assessment:     {report.overfit_assessment}")

        # Production Readiness
        print()
        print("  " + "-" * 40)
        print("  PRODUCTION READINESS")
        print("  " + "-" * 40)
        score = report.production_readiness_score
        if score >= 80:
            grade = "A — Ready for live deployment"
        elif score >= 60:
            grade = "B — Promising, needs more validation"
        elif score >= 40:
            grade = "C — Marginal, significant risk"
        elif score >= 20:
            grade = "D — Not recommended for live"
        else:
            grade = "F — Strategy failed validation"

        bar = "█" * (score // 2) + "░" * (50 - score // 2)
        print(f"    Score: {score}/100")
        print(f"    [{bar}]")
        print(f"    Grade: {grade}")

        print()
        print("=" * 60)


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

async def main():
    validator = WalkForwardValidator()
    report = await validator.run()
    WalkForwardValidator.print_report(report)
    return report


if __name__ == "__main__":
    asyncio.run(main())
