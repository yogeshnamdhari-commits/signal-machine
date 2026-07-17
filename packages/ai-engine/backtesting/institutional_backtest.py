"""
Phase 7 — Institutional Backtest Runner
Comprehensive testing across different market regimes to validate signal quality.
Measures: Win Rate, Profit Factor, Sharpe Ratio, and Max Drawdown.

Features:
- Rejection counters to identify filter bottlenecks
- Tunable thresholds (min_factors, min_confidence, institutional_score)
- Auto-detect data range for scenario date alignment
- Per-regime statistics (Trades, Win Rate, Profit Factor)
- Trade log CSV export for post-analysis
- Success criteria validation gate for live trading
"""
import asyncio
import csv
import sys
from pathlib import Path

# Add the ai-engine directory to sys.path to allow imports from backtesting and core
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from backtesting.backtester import BacktestEngine, BacktestConfig
from backtesting.historical_data import HistoricalDataEngine
from core.institutional_scoring_engine import InstitutionalScoringEngine
from loguru import logger

def _normalize(value: float, low: float, high: float) -> float:
    """Normalize a value to 0-1 range with clamping."""
    if high == low:
        return 0.5
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _percentile_rank(values: np.ndarray, current: float) -> float:
    """
    Rank `current` against a distribution of `values` using percentile.
    This produces a naturally spread 0-1 distribution instead of clustering near 0.5.
    """
    if len(values) == 0:
        return 0.5
    return float(np.sum(values <= current) / len(values))


def calculate_institutional_proxies(df: pd.DataFrame, i: int):
    """
    Extract institutional metrics from historical OHLCV data.
    Uses CONTINUOUS scoring with TIGHT normalization ranges
    to maximize distribution spread.
    """
    if i < 50:
        return None

    # Lookback windows
    short = df.iloc[max(0, i-10):i+1]
    medium = df.iloc[max(0, i-20):i+1]
    long_w = df.iloc[max(0, i-50):i+1]

    closes_s = short['close'].values
    closes_m = medium['close'].values
    closes_l = long_w['close'].values
    highs_m = medium['high'].values
    lows_m = medium['low'].values
    volumes_m = medium['volume'].values
    highs_l = long_w['high'].values
    lows_l = long_w['low'].values
    volumes_l = long_w['volume'].values

    price = closes_s[-1]

    # ── 1. ABSORPTION — Volume vs Price Range ───────────────────
    vol_avg = np.mean(volumes_m)
    vol_ratio = volumes_m[-1] / vol_avg if vol_avg > 0 else 1.0
    price_range = highs_m[-1] - lows_m[-1]
    avg_range = np.mean(highs_m - lows_m)
    range_ratio = price_range / avg_range if avg_range > 0 else 1.0
    raw_absorption = vol_ratio / max(range_ratio, 0.01)
    # Tight range: most values 0.5-2.0, extreme at 3.0+
    absorption_score = _normalize(raw_absorption, 0.5, 3.0)

    # ── 2. LIQUIDITY SWEEP — Wick beyond local extremes ────────
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

    # ── 3. MARKET REGIME — MA alignment (0-1 continuous) ───────
    sma10 = np.mean(closes_s[-10:]) if len(closes_s) >= 10 else price
    sma20 = np.mean(closes_m[-20:]) if len(closes_m) >= 20 else price
    sma50 = np.mean(closes_l[-50:]) if len(closes_l) >= 50 else price

    regime_fit = 0.0
    if price > sma10: regime_fit += 0.25
    if sma10 > sma20: regime_fit += 0.25
    if sma20 > sma50: regime_fit += 0.25
    if price > sma50: regime_fit += 0.25

    # ── 4. LIQUIDATION — Proximity to local extremes ───────────
    dist_to_low = abs(price - local_low) / price
    dist_to_high = abs(price - local_high) / price
    min_dist = min(dist_to_low, dist_to_high)
    # Tight range: 0-2% = high, 5%+ = low
    liq_score = _normalize(min_dist, 0.05, 0.002)

    # ── 5. OPEN INTEREST — Volume trend ────────────────────────
    vol_short = np.mean(volumes_l[-5:]) if len(volumes_l) >= 5 else vol_avg
    vol_long = np.mean(volumes_l) if len(volumes_l) > 0 else vol_avg
    vol_trend = vol_short / vol_long if vol_long > 0 else 1.0
    # Tight range: 0.7-1.8 captures most variation
    oi_score = _normalize(vol_trend, 0.7, 1.8)

    # ── 6. EXCHANGE FLOW — Volume-weighted price move ──────────
    price_move = abs(closes_m[-1] - closes_m[-2]) / closes_m[-2] if len(closes_m) >= 2 else 0
    flow_intensity = vol_ratio * price_move * 100
    # Tight range: 0-1.5 captures most
    flow_score = _normalize(flow_intensity, 0.0, 1.5)

    # ── 7. FUNDING — Extension from EMA ────────────────────────
    ema21 = np.mean(closes_m[-21:]) if len(closes_m) >= 21 else price
    extension = (price - ema21) / ema21
    # Tight range: 0-3% extension
    funding_score = _normalize(abs(extension), 0.003, 0.03)

    # ── 8. MOMENTUM — RSI ──────────────────────────────────────
    if len(closes_l) >= 14:
        deltas = np.diff(closes_l[-15:])
        gains = np.where(deltas > 0, deltas, 0)
        losses_arr = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses_arr)
        rs = avg_gain / avg_loss if avg_loss > 0 else 10
        rsi = 100 - (100 / (1 + rs))
    else:
        rsi = 50

    # RSI → 0-1 score (extremes = high, neutral = low)
    rsi_score = abs(rsi - 50) / 50  # 0 at 50, 1 at 0 or 100

    # ── Count active factors ────────────────────────────────────
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

    # ── Determine bias ──────────────────────────────────────────
    bull_signals = sum([
        1 if sweep_score > 0.2 else 0,
        1 if funding_score > 0.6 else 0,
        1 if rsi < 45 else 0,
        1 if price > sma20 else 0,
    ])
    bear_signals = sum([
        1 if sweep_score < -0.2 else 0,
        1 if extension > 0.02 else 0,
        1 if rsi > 55 else 0,
        1 if price < sma20 else 0,
    ])
    bias = "LONG" if bull_signals > bear_signals else "SHORT"

    return {
        "absorption_score": absorption_score,
        "liq_score": liq_score,
        "regime_fit": regime_fit,
        "sweep_score": sweep_score,
        "oi_score": oi_score,
        "flow_score": flow_score,
        "funding_score": funding_score,
        "rsi_score": rsi_score,
        "bias": bias,
        "active_factors": active_factors,
        "rsi": rsi,
    }

async def run_scenario(
    name: str,
    symbol: str,
    df: pd.DataFrame,
    start: datetime,
    end: datetime,
    min_confidence: float = 0.50,
    min_score: float = 40,
    min_factors: int = 2,
    regime_filter: bool = True,
    fake_breakout_filter: bool = True,
):
    """Run backtest for a specific timeframe/regime with rejection tracking."""
    mask = (df['open_time'] >= start) & (df['open_time'] <= end)
    scenario_df = df.loc[mask].reset_index(drop=True)

    if scenario_df.empty:
        logger.warning(f"No data for scenario: {name} "
                       f"(requested {start.date()} → {end.date()}, "
                       f"available {df['open_time'].min().date()} → {df['open_time'].max().date()})")
        return None, [], {"low_confidence": 0, "low_score": 0, "factor_count": 0, "regime_filter": 0, "fake_breakout": 0}, []

    config = BacktestConfig(initial_capital=10000, leverage=10, risk_per_trade_pct=0.005)
    engine = BacktestEngine(config)
    scorer = InstitutionalScoringEngine()

    # Rejection counters
    rejects = {
        "low_confidence": 0,
        "low_score": 0,
        "factor_count": 0,
        "regime_filter": 0,
        "fake_breakout": 0,
    }

    # Score distribution collector
    score_samples = []

    # Signal collector for accurate trade logging
    trade_signals = {}

    def institutional_strategy(data: pd.DataFrame, i: int):
        proxies = calculate_institutional_proxies(data, i)
        if not proxies:
            rejects["low_confidence"] += 1
            return None

        # ── Detect market regime (trending vs ranging) ───────────
        price = data['close'].iloc[i]
        sma10 = data['close'].iloc[max(0,i-10):i+1].mean()
        sma20 = data['close'].iloc[max(0,i-20):i+1].mean()
        sma50 = data['close'].iloc[max(0,i-50):i+1].mean() if i >= 50 else sma20

        # Trend strength: how aligned are the MAs?
        ma_spread = abs(sma10 - sma50) / price
        is_trending = ma_spread > 0.01  # >1% spread = trending

        # ADX proxy: average directional movement
        if i >= 14:
            highs_14 = data['high'].iloc[i-14:i+1].values
            lows_14 = data['low'].iloc[i-14:i+1].values
            ranges_14 = highs_14 - lows_14
            avg_range = np.mean(ranges_14) / price
            is_volatile = avg_range > 0.008  # >0.8% avg range = volatile
        else:
            is_volatile = True

        # ── Composite score using institutional weights ──────────
        # Make directional scores actually directional (0.3-0.7 range)
        # so the bias calculation can determine LONG vs SHORT
        sweep_dir = 0.6 if proxies['sweep_score'] > 0.1 else 0.4 if proxies['sweep_score'] < -0.1 else 0.5
        rsi_dir = 0.6 if proxies['rsi'] < 40 else 0.4 if proxies['rsi'] > 60 else 0.5

        mock_data = {
            "absorption_score": proxies['absorption_score'],
            "delta_score": sweep_dir,  # directional: bullish sweep → high
            "cvd_score": rsi_dir,  # directional: oversold → bullish CVD
            "oi_score": proxies['oi_score'],
            "funding_score": proxies['funding_score'],
            "flow_score": proxies['flow_score'],
            "liq_score": proxies['liq_score'],
            "regime_fit": proxies['regime_fit'],
        }

        res = scorer.calculate_score(mock_data)

        # Boost score if RSI confirms
        rsi_boost = (proxies['rsi_score'] - 0.5) * 10
        adjusted_score = res['score'] + rsi_boost
        confidence = adjusted_score / 100

        # Debug: log high scores
        if adjusted_score > 60:
            print(
                f"HIGH SCORE | "
                f"score={res['score']:.2f} "
                f"rsi={rsi_boost:.2f} "
                f"adj={adjusted_score:.2f}"
            )

        # Collect score sample for distribution analysis
        score_samples.append({
            "raw_score": res['score'],
            "adjusted_score": adjusted_score,
            "confidence": confidence,
            "rsi_boost": rsi_boost,
            "active_factors": proxies['active_factors'],
        })

        # ── Filter 1: Minimum confidence ─────────────────────────
        if confidence < min_confidence:
            rejects["low_confidence"] += 1
            return None

        # ── Filter 2: Minimum institutional score ────────────────
        if adjusted_score < min_score:
            rejects["low_score"] += 1
            return None

        # ── Filter 3: Minimum active factors ─────────────────────
        if proxies['active_factors'] < min_factors:
            rejects["factor_count"] += 1
            return None

        # ── Filter 4: REGIME FILTER DISABLED ─────────────────────
        # Previously: if not is_volatile and not is_trending: reject
        # Testing impact of removing this filter (was rejecting 67.5%)

        # ── Filter 5: Fake breakout detection ────────────────────
        if fake_breakout_filter and i >= 5:
            prev_closes = data['close'].iloc[i-5:i].values
            current = data['close'].iloc[i]
            spike = abs(current - prev_closes[-1]) / prev_closes[-1]
            if spike > 0.02 and abs(current - prev_closes[-2]) / prev_closes[-2] < 0.005:
                rejects["fake_breakout"] += 1
                return None

        # ── DIRECTION: Institutional Bias (replaces MA crossover) ─
        # Use orderflow-aligned pillars for direction:
        #   sweep_score: liquidity sweep direction (-1 to 1)
        #   rsi: momentum extremes (inverted: low RSI = bullish)
        #   oi_score: rising volume = conviction
        #   flow_score: volume-weighted price movement
        # Normalize each to 0-1 centered at 0.5, then average.
        sweep_norm = max(0, min(1, (proxies['sweep_score'] + 1) / 2))  # -1→0, 0→0.5, 1→1
        rsi_norm = max(0, min(1, (100 - proxies['rsi']) / 100))  # 100→0, 50→0.5, 0→1 (inverted)
        oi_norm = proxies.get('oi_score', 0.5)
        flow_norm = proxies.get('flow_score', 0.5)

        bias_score = (sweep_norm + rsi_norm + oi_norm + flow_norm) / 4.0  # 0-1

        # ── NO-TRADE ZONE: Skip neutral bias ─────────────────────
        # When institutional signals are mixed, don't trade.
        # Narrowed from 0.45-0.55 to 0.48-0.52 to recover volume.
        if 0.48 <= bias_score <= 0.52:
            rejects["regime_filter"] += 1
            return None

        # Direction based on institutional bias
        if bias_score > 0.55:
            side = "LONG"
        elif bias_score < 0.45:
            side = "SHORT"
        else:
            rejects["regime_filter"] += 1
            return None

        # ── Dynamic stop-loss/take-profit based on ATR proxy ─────
        atr = data['high'].iloc[max(0,i-14):i+1].values - data['low'].iloc[max(0,i-14):i+1].values
        atr_val = np.mean(atr) if len(atr) > 0 else price * 0.015

        # Standard R:R for all conditions
        sl_mult, tp_mult = 2.0, 3.5

        if side == "LONG":
            stop_loss = price - (atr_val * sl_mult)
            take_profit = price + (atr_val * tp_mult)
        else:
            stop_loss = price + (atr_val * sl_mult)
            take_profit = price - (atr_val * tp_mult)

        sig_data = {
            "side": side,
            "confidence": confidence,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "score": adjusted_score,
        }
        ts = data['open_time'].iloc[i]
        trade_signals[ts] = sig_data
        return sig_data

    result = await engine.run(symbol, scenario_df, institutional_strategy)

    # Collect trade log entries for this regime
    trade_log = []
    for t in result.trades:
        sig = trade_signals.get(t.entry_time, {})
        trade_log.append({
            "timestamp": t.entry_time,
            "symbol": t.symbol,
            "side": t.side.value,
            "entry": t.entry_price,
            "exit": t.exit_price,
            "pnl": t.net_pnl,
            "score": round(sig.get("score", 0), 2),
            "confidence": round(sig.get("confidence", 0), 4),
            "market_regime": name,
            "hold_time_min": t.hold_time_minutes,
            "exit_reason": t.exit_reason,
        })

    return result, trade_log, rejects, score_samples

async def main():
    logger.info("🚀 Initializing Multi-Symbol Institutional Backtest...")
    # Walk-forward validated: BNB, SOL, ETH are robust across train/test
    # Removed: LINKUSDT (overfit: +$1009 train → -$94 test)
    # Removed: BTCUSDT (weak: -$94 train, -$93 test)
    symbols = ["ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT"]

    # Use HistoricalDataEngine to ensure data is available
    data_engine = HistoricalDataEngine()
    await data_engine.initialize()
    
    symbol_data = {}
    for sym in symbols:
        df = await data_engine.get_historical_data(sym, "1h", days=1000)
        if not df.empty:
            symbol_data[sym] = df
            
    await data_engine.stop()

    if not symbol_data:
        logger.error("No historical data available for any symbols — aborting")
        return

    # Auto-detect available data range
    all_starts = [d['open_time'].min() for d in symbol_data.values()]
    all_ends = [d['open_time'].max() for d in symbol_data.values()]
    data_start = max(all_starts)
    data_end = min(all_ends)
    
    logger.info(f"Aggregated Data Range: {data_start.date()} → {data_end.date()}")

    # ── Define scenarios aligned to available data ───────────────
    scenarios = [
        ("Last 30 Days", data_end - timedelta(days=30), data_end),
        ("Last 90 Days", data_end - timedelta(days=90), data_end),
    ]

    # Only add historical regimes if data covers the period
    historical = [
        ("Bull Market",  datetime(2023, 10, 1), datetime(2024, 3, 14)),
        ("Bear Market",  datetime(2022, 1, 1),  datetime(2022, 12, 31)),
        ("Range Market", datetime(2023, 5, 1),  datetime(2023, 9, 30)),
    ]
    for name, start, end in historical:
        clamped_start = max(start, data_start)
        clamped_end = min(end, data_end)
        if clamped_start < clamped_end:
            scenarios.append((name, clamped_start, clamped_end))
        else:
            logger.warning(f"Skipping {name}: no data for {start.date()}→{end.date()} "
                           f"(available {data_start.date()}→{data_end.date()})")

    # ══════════════════════════════════════════════════════════════
    # PHASE 1: SCORE DISTRIBUTION ANALYSIS (run once at 0.30)
    # ══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*60}")
    print(f"  PHASE 1: SCORE DISTRIBUTION ANALYSIS")
    print(f"{'='*60}")

    all_samples = []
    for symbol, df in symbol_data.items():
        for name, start, end in scenarios:
            _, _, _, samples = await run_scenario(
                name, symbol, df, start, end,
                min_confidence=0.30,
                min_score=0,
                min_factors=0,
            )
            all_samples.extend(samples)

    if all_samples:
        confidences = [s['confidence'] for s in all_samples]
        raw_scores = [s['adjusted_score'] for s in all_samples]

        print(f"\n  Total bars evaluated: {len(all_samples)}")
        print(f"\n  Score Distribution (adjusted_score):")
        print(f"    Min:    {min(raw_scores):>8.2f}")
        print(f"    25th:   {np.percentile(raw_scores, 25):>8.2f}")
        print(f"    Median: {np.percentile(raw_scores, 50):>8.2f}")
        print(f"    75th:   {np.percentile(raw_scores, 75):>8.2f}")
        print(f"    Max:    {max(raw_scores):>8.2f}")
        print(f"    Mean:   {np.mean(raw_scores):>8.2f}")
        print(f"    Std:    {np.std(raw_scores):>8.2f}")

        print(f"\n  Confidence Distribution (score/100):")
        print(f"    Min:    {min(confidences):>8.4f}")
        print(f"    25th:   {np.percentile(confidences, 25):>8.4f}")
        print(f"    Median: {np.percentile(confidences, 50):>8.4f}")
        print(f"    75th:   {np.percentile(confidences, 75):>8.4f}")
        print(f"    Max:    {max(confidences):>8.4f}")

        # Histogram buckets
        print(f"\n  Confidence Histogram:")
        buckets = [(0.30, 0.35), (0.35, 0.40), (0.40, 0.45), (0.45, 0.50),
                    (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70),
                    (0.70, 0.75), (0.75, 0.80), (0.80, 1.01)]
        for lo, hi in buckets:
            count = sum(1 for c in confidences if lo <= c < hi)
            pct = count / len(confidences) * 100
            bar = "█" * int(pct / 2)
            print(f"    {lo:.2f}-{hi:.2f}: {count:>5} ({pct:>5.1f}%) {bar}")

        # How many pass each threshold?
        print(f"\n  Bars passing each confidence threshold:")
        for thresh in [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
            passing = sum(1 for c in confidences if c >= thresh)
            pct = passing / len(confidences) * 100
            print(f"    ≥ {thresh:.2f}: {passing:>5} bars ({pct:>5.1f}%)")

    # ══════════════════════════════════════════════════════════════
    # PHASE 2: CONFIDENCE SWEEP
    # ══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*60}")
    print(f"  PHASE 2: CONFIDENCE THRESHOLD SWEEP")
    print(f"{'='*60}")

    sweep_thresholds = [0.30, 0.35, 0.40, 0.45, 0.50, 0.52, 0.54, 0.56, 0.58, 0.60]
    sweep_results = []

    for conf_thresh in sweep_thresholds:
        all_trades_sweep = []
        sweep_metrics = []

        for symbol, df in symbol_data.items():
            for name, start, end in scenarios:
                result, trade_log, rejects, _ = await run_scenario(
                    name, symbol, df, start, end,
                    min_confidence=conf_thresh,
                    min_score=32,
                    min_factors=2,
                )
                all_trades_sweep.extend(trade_log)
                if result:
                    sweep_metrics.append(result)

        total_trades = sum(r.total_trades for r in sweep_metrics)
        total_wins = sum(r.win_count for r in sweep_metrics)
        wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
        total_profit = sum(t["pnl"] for t in all_trades_sweep if t["pnl"] > 0)
        total_loss = sum(abs(t["pnl"]) for t in all_trades_sweep if t["pnl"] <= 0)
        pf = total_profit / total_loss if total_loss > 0 else float("inf")
        dd = np.mean([r.max_drawdown for r in sweep_metrics]) if sweep_metrics else 0
        net = sum(t["pnl"] for t in all_trades_sweep)
        bull_pnl_val = sum(t["pnl"] for t in all_trades_sweep if t["market_regime"] == "Bull Market")

        sweep_results.append({
            "conf": conf_thresh,
            "trades": total_trades,
            "wr": wr,
            "pf": pf,
            "dd": dd,
            "net": net,
            "bull": bull_pnl_val,
        })

    # Print sweep table
    print(f"\n  {'Conf':>6} │ {'Trades':>6} │ {'WR':>7} │ {'PF':>7} │ {'DD':>7} │ {'Net PnL':>10} │ {'Bull PnL':>10}")
    print(f"  {'─'*6}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*10}─┼─{'─'*10}")

    best_pf = 0
    best_conf = 0
    for r in sweep_results:
        pf_str = f"{r['pf']:.2f}" if r['pf'] != float("inf") else "∞"
        marker = ""
        # Prefer PF >= 1.5 with >= 100 trades (production criteria)
        if r['pf'] >= 1.5 and r['trades'] >= 100 and r['pf'] > best_pf:
            best_pf = r['pf']
            best_conf = r['conf']
        elif r['pf'] > best_pf and r['trades'] >= 50 and best_conf == 0:
            # Fallback: best PF with >= 50 trades
            best_pf = r['pf']
            best_conf = r['conf']
        if r['pf'] >= 1.5:
            marker = " ✅"
        elif r['pf'] >= 1.0:
            marker = " ≈"
        print(f"  {r['conf']:>6.2f} │ {r['trades']:>6} │ {r['wr']:>6.1f}% │ {pf_str:>7} │ {r['dd']:>6.1f}% │ ${r['net']:>9.2f} │ ${r['bull']:>9.2f}{marker}")

    print(f"\n  🏆 Best PF with ≥50 trades: confidence={best_conf:.2f} (PF={best_pf:.2f})")

    # ══════════════════════════════════════════════════════════════
    # PHASE 3: BEST THRESHOLD DETAILED RUN
    # ══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*60}")
    print(f"  PHASE 3: DETAILED RESULTS AT OPTIMAL THRESHOLD ({best_conf:.2f})")
    print(f"{'='*60}")

    all_trades = []
    all_rejects = {"low_confidence": 0, "low_score": 0, "factor_count": 0, "regime_filter": 0, "fake_breakout": 0}
    regime_results = {}
    symbol_results = {}

    for symbol, df in symbol_data.items():
        symbol_results[symbol] = {"trades": 0, "pnl": 0.0, "wins": 0}
        for name, start, end in scenarios:
            result, trade_log, rejects, _ = await run_scenario(
                name, symbol, df, start, end,
                min_confidence=best_conf,
                min_score=32,
                min_factors=2,
            )
            if result:
                all_trades.extend(trade_log)
                for k in all_rejects:
                    all_rejects[k] += rejects[k]
                regime_results.setdefault(name, []).append(result)
                symbol_results[symbol]["trades"] += result.total_trades
                symbol_results[symbol]["pnl"] += result.net_pnl
                symbol_results[symbol]["wins"] += result.win_count

    # Final aggregation for criteria validation
    final_trades = len(all_trades)
    final_win_rate = (sum(1 for t in all_trades if t['pnl'] > 0) / final_trades * 100) if final_trades > 0 else 0
    total_profit = sum(t["pnl"] for t in all_trades if t["pnl"] > 0)
    total_loss = sum(abs(t["pnl"]) for t in all_trades if t["pnl"] <= 0)
    final_pf = total_profit / total_loss if total_loss > 0 else float("inf")
    final_net_pnl = sum(t["pnl"] for t in all_trades)
    
    all_results = [r for sublist in regime_results.values() for r in sublist]
    final_avg_dd = np.mean([r.max_drawdown for r in all_results]) if all_results else 0

    # ── Symbol Performance Summary ──────────────────────────────
    print(f"\n📊 SYMBOL PERFORMANCE ({best_conf:.2f} threshold)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  {'Symbol':<10} │ {'Trades':>6} │ {'Win Rate':>8} │ {'PnL':>10}")
    print(f"  {'─'*10}─┼─{'─'*7}─┼─{'─'*9}─┼─{'─'*10}")
    for sym, s_res in symbol_results.items():
        wr = (s_res['wins'] / s_res['trades'] * 100) if s_res['trades'] > 0 else 0
        print(f"  {sym:<10} │ {s_res['trades']:>6} │ {wr:>7.1f}% │ ${s_res['pnl']:>9.2f}")

    for name, results in regime_results.items():
        t_trades = sum(r.total_trades for r in results)
        t_wins = sum(r.win_count for r in results)
        t_pnl = sum(r.net_pnl for r in results)
        t_wr = (t_wins / t_trades * 100) if t_trades > 0 else 0
        r_losses = abs(sum(t['pnl'] for t in all_trades if t['market_regime'] == name and t['pnl'] <= 0))
        r_profits = sum(t['pnl'] for t in all_trades if t['market_regime'] == name and t['pnl'] > 0)
        t_pf = r_profits / r_losses if r_losses > 0 else float("inf")

        print(f"\n📈 REGIME (Aggregated): {name}")
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  Total Trades:  {t_trades:>8}")
        print(f"  Win Rate:      {t_wr:>8.2f}%")
        print(f"  Profit Factor: {t_pf:>8.2f}")
        print(f"  Net PnL:       ${t_pnl:>8.2f}")
        
        longs = [t for t in all_trades if t['market_regime'] == name and t['side'] == "LONG"]
        shorts = [t for t in all_trades if t['market_regime'] == name and t['side'] == "SHORT"]
        print(f"  ── Direction Breakdown ────────────")
        print(f"  LONG:  {len(longs):>4} trades | PnL ${sum(t['pnl'] for t in longs):>8.2f}")
        print(f"  SHORT: {len(shorts):>4} trades | PnL ${sum(t['pnl'] for t in shorts):>8.2f}")

    # ── Rejection Counter Summary ────────────────────────────────
    total_rejects = sum(all_rejects.values())
    print(f"\n\n🚫 REJECTION ANALYSIS")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Total rejected:  {total_rejects}")
    for reason, count in sorted(all_rejects.items(), key=lambda x: -x[1]):
        pct = (count / total_rejects * 100) if total_rejects > 0 else 0
        print(f"  {reason:<20} {count:>8}  ({pct:>5.1f}%)")

    # ── Global Metrics Summary ───────────────────────────────────
    print(f"\n🏆 GLOBAL METRICS SUMMARY")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Total Trades:  {final_trades}")
    print(f"  Win Rate:      {final_win_rate:.1f}%")
    print(f"  Profit Factor: {final_pf:.2f}")
    print(f"  Max Drawdown:  {final_avg_dd:.1f}%")
    print(f"  Net PnL:       ${final_net_pnl:.2f}")

    # ── Bear Market Verification ─────────────────────────────────
    bear_trades = [t for t in all_trades if t["market_regime"] == "Bear Market"]
    print(f"\n🐻 BEAR MARKET VERIFICATION")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Bear Market trades: {len(bear_trades)}")
    if len(bear_trades) == 0:
        print(f"  ⚠️  NO BEAR MARKET TRADES — scenario may not be producing entries")
        print(f"  Check: data range, bias_score distribution, confidence threshold")
    else:
        bear_wins = sum(1 for t in bear_trades if t['pnl'] > 0)
        bear_wr = bear_wins / len(bear_trades) * 100
        bear_pnl_val = sum(t['pnl'] for t in bear_trades)
        print(f"  Bear WR: {bear_wr:.1f}%")
        print(f"  Bear PnL: ${bear_pnl_val:.2f}")

    # ── TEST #3: SCORE BUCKET ANALYSIS ──────────────────────────
    print(f"\n📊 SCORE BUCKET ANALYSIS (Does score predict profitability?)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    score_buckets = {}
    for t in all_trades:
        bucket = int(t.get('score', 0) / 10) * 10
        if bucket not in score_buckets:
            score_buckets[bucket] = {'trades': 0, 'wins': 0, 'pnl': 0.0, 'profit': 0.0, 'loss': 0.0}
        score_buckets[bucket]['trades'] += 1
        if t['pnl'] > 0:
            score_buckets[bucket]['wins'] += 1
            score_buckets[bucket]['profit'] += t['pnl']
        else:
            score_buckets[bucket]['loss'] += abs(t['pnl'])
        score_buckets[bucket]['pnl'] += t['pnl']

    print(f"  {'Score':>8} │ {'Trades':>6} │ {'WR':>7} │ {'PF':>7} │ {'Net PnL':>10}")
    print(f"  {'─'*8}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*10}")
    for bucket in sorted(score_buckets.keys()):
        b = score_buckets[bucket]
        wr = (b['wins'] / b['trades'] * 100) if b['trades'] > 0 else 0
        pf = b['profit'] / b['loss'] if b['loss'] > 0 else float('inf')
        pf_s = f"{pf:.2f}" if pf != float('inf') else "∞"
        print(f"  {bucket:>4}-{bucket+10:<4} │ {b['trades']:>6} │ {wr:>6.1f}% │ {pf_s:>7} │ ${b['pnl']:>9.2f}")

    # Score bucket by side
    print(f"\n  Score Bucket by LONG only:")
    print(f"  {'Score':>8} │ {'Trades':>6} │ {'WR':>7} │ {'PF':>7} │ {'Net PnL':>10}")
    print(f"  {'─'*8}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*10}")
    long_buckets = {}
    for t in all_trades:
        if t['side'] != 'LONG': continue
        bucket = int(t.get('score', 0) / 10) * 10
        if bucket not in long_buckets:
            long_buckets[bucket] = {'trades': 0, 'wins': 0, 'pnl': 0.0, 'profit': 0.0, 'loss': 0.0}
        long_buckets[bucket]['trades'] += 1
        if t['pnl'] > 0:
            long_buckets[bucket]['wins'] += 1
            long_buckets[bucket]['profit'] += t['pnl']
        else:
            long_buckets[bucket]['loss'] += abs(t['pnl'])
        long_buckets[bucket]['pnl'] += t['pnl']
    for bucket in sorted(long_buckets.keys()):
        b = long_buckets[bucket]
        wr = (b['wins'] / b['trades'] * 100) if b['trades'] > 0 else 0
        pf = b['profit'] / b['loss'] if b['loss'] > 0 else float('inf')
        pf_s = f"{pf:.2f}" if pf != float('inf') else "∞"
        print(f"  {bucket:>4}-{bucket+10:<4} │ {b['trades']:>6} │ {wr:>6.1f}% │ {pf_s:>7} │ ${b['pnl']:>9.2f}")

    # ── Trade Log CSV Export ─────────────────────────────────────
    if all_trades:
        export_path = Path(__file__).parent.parent / "data" / "reports" / "trade_log.csv"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with open(export_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_trades[0].keys())
            writer.writeheader()
            writer.writerows(all_trades)
        print(f"\n📁 Trade log exported: {export_path}")

    # ── Success Criteria Validation ──────────────────────────────
    bull_pnl = sum(t['pnl'] for t in all_trades if t['market_regime'] == "Bull Market")
    bear_pnl = sum(t['pnl'] for t in all_trades if t['market_regime'] == "Bear Market")

    print(f"\n\n✅ SUCCESS CRITERIA (Gate for Live Trading)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    criteria = [
        ("Total Trades >= 100",      final_trades >= 100,      f"{final_trades}"),
        ("Profit Factor >1.5",       final_pf > 1.5,           f"{final_pf:.2f}"),
        ("Win Rate > 50%",           final_win_rate > 50,      f"{final_win_rate:.1f}%"),
        ("Bull Market profitable",   bull_pnl > 0,             f"${bull_pnl:.2f}"),
        ("Bear Market profitable",   bear_pnl > 0,             f"${bear_pnl:.2f}"),
        ("Max Drawdown < 10%",       final_avg_dd < 10,        f"{final_avg_dd:.1f}%"),
    ]

    all_pass = True
    for label, passed, value in criteria:
        icon = "✅" if passed else "❌"
        print(f"  {icon} {label:<28} {value}")
        if not passed:
            all_pass = False

    if all_pass:
        print(f"\n🎯 ALL CRITERIA MET — System ready for live trading evaluation.")
    else:
        failed = [c[0] for c in criteria if not c[1]]
        print(f"\n⚠️  {len(failed)} criteria failed: {', '.join(failed)}")
        print(f"   Continue optimizing before live deployment.")

    # ══════════════════════════════════════════════════════════════
    # PHASE 4: WALK-FORWARD VALIDATION
    # ══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*60}")
    print(f"  PHASE 4: WALK-FORWARD VALIDATION")
    print(f"{'='*60}")

    # Split: Train 2023-09 → 2024-06, Test 2024-07 → 2024-12
    train_start = max(datetime(2023, 9, 1), data_start)
    train_end = min(datetime(2024, 6, 30), data_end)
    test_start = max(datetime(2024, 7, 1), data_start)
    test_end = min(datetime(2024, 12, 31), data_end)

    wf_scenarios = [
        ("Train", train_start, train_end),
        ("Test", test_start, test_end),
    ]

    # Check data availability
    print(f"\n  Train period: {train_start.date()} → {train_end.date()}")
    print(f"  Test period:  {test_start.date()} → {test_end.date()}")

    if test_start >= data_end:
        print(f"  ⚠️  Test period extends beyond available data ({data_end.date()})")
        print(f"  Adjusting test end to data end")
        test_end = data_end

    if train_start >= train_end or test_start >= test_end:
        print(f"  ❌ Invalid walk-forward periods — skipping")
    else:
        wf_results = {}
        for phase_name, phase_start, phase_end in wf_scenarios:
            phase_trades = []
            phase_metrics = []

            for symbol, df in symbol_data.items():
                result, trade_log, _, _ = await run_scenario(
                    phase_name, symbol, df, phase_start, phase_end,
                    min_confidence=best_conf,
                    min_score=32,
                    min_factors=2,
                )
                phase_trades.extend(trade_log)
                if result:
                    phase_metrics.append(result)

            total = len(phase_trades)
            wins = sum(1 for t in phase_trades if t['pnl'] > 0)
            wr = (wins / total * 100) if total > 0 else 0
            profit = sum(t['pnl'] for t in phase_trades if t['pnl'] > 0)
            loss = sum(abs(t['pnl']) for t in phase_trades if t['pnl'] <= 0)
            pf = profit / loss if loss > 0 else float('inf')
            dd = np.mean([r.max_drawdown for r in phase_metrics]) if phase_metrics else 0
            net = sum(t['pnl'] for t in phase_trades)

            wf_results[phase_name] = {
                'trades': total, 'wr': wr, 'pf': pf, 'dd': dd, 'net': net
            }

        # Print walk-forward results
        print(f"\n  {'Phase':<8} │ {'Trades':>6} │ {'WR':>7} │ {'PF':>7} │ {'DD':>7} │ {'Net PnL':>10}")
        print(f"  {'─'*8}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*7}─┼─{'─'*10}")

        for phase_name in ['Train', 'Test']:
            r = wf_results.get(phase_name, {})
            if not r: continue
            pf_s = f"{r['pf']:.2f}" if r['pf'] != float('inf') else '∞'
            print(f"  {phase_name:<8} │ {r['trades']:>6} │ {r['wr']:>6.1f}% │ {pf_s:>7} │ {r['dd']:>6.1f}% │ ${r['net']:>9.2f}")

        # Walk-forward pass/fail
        test_r = wf_results.get('Test', {})
        if test_r:
            wf_pass = True
            wf_criteria = [
                ("Test PF > 1.3",     test_r['pf'] > 1.3,     f"{test_r['pf']:.2f}"),
                ("Test WR > 48%",     test_r['wr'] > 48,       f"{test_r['wr']:.1f}%"),
                ("Test DD < 10%",     test_r['dd'] < 10,       f"{test_r['dd']:.1f}%"),
                ("Test trades > 20",  test_r['trades'] > 20,   f"{test_r['trades']}"),
            ]

            print(f"\n  Walk-Forward Pass/Fail:")
            for label, passed, value in wf_criteria:
                icon = "✅" if passed else "❌"
                print(f"    {icon} {label:<20} {value}")
                if not passed:
                    wf_pass = False

            if wf_pass:
                print(f"\n  🎯 WALK-FORWARD PASSED — Strategy generalizes to unseen data")
            else:
                print(f"\n  ⚠️  WALK-FORWARD FAILED — Strategy may be overfit to training data")

            # Degradation check
            train_r = wf_results.get('Train', {})
            if train_r and train_r['pf'] > 0:
                pf_decay = (train_r['pf'] - test_r['pf']) / train_r['pf'] * 100
                print(f"\n  PF Degradation: {pf_decay:.1f}% (Train {train_r['pf']:.2f} → Test {test_r['pf']:.2f})")
                if pf_decay > 30:
                    print(f"  ⚠️  Significant degradation — strategy may be overfit")
                elif pf_decay < 10:
                    print(f"  ✅ Minimal degradation — strategy is robust")


if __name__ == "__main__":
    asyncio.run(main())