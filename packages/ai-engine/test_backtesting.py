"""
Phase 5 — Backtesting + Optimization: Test Suite
Validates all 6 modules work correctly.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
from datetime import datetime, timedelta


def generate_sample_data(n_bars: int = 1000, base_price: float = 50000) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(42)
    dates = [datetime.now() - timedelta(minutes=5 * (n_bars - i)) for i in range(n_bars)]
    
    # Random walk for price
    returns = np.random.normal(0.0001, 0.02, n_bars)
    prices = base_price * np.exp(np.cumsum(returns))
    
    data = pd.DataFrame({
        "open_time": dates,
        "open": prices * (1 + np.random.uniform(-0.005, 0.005, n_bars)),
        "high": prices * (1 + np.random.uniform(0, 0.02, n_bars)),
        "low": prices * (1 - np.random.uniform(0, 0.02, n_bars)),
        "close": prices,
        "volume": np.random.uniform(100, 10000, n_bars) * base_price / 50000,
        "trades": np.random.randint(100, 1000, n_bars),
    })
    
    # Ensure high >= open, close and low <= open, close
    data["high"] = data[["open", "high", "close"]].max(axis=1) * 1.001
    data["low"] = data[["open", "low", "close"]].min(axis=1) * 0.999
    
    return data


async def test_historical_data():
    """Test HistoricalDataEngine."""
    from backtesting.historical_data import HistoricalDataEngine
    
    engine = HistoricalDataEngine()
    await engine.initialize()
    
    # Test data parsing
    df = generate_sample_data(500)
    stats = engine.validate_data(df)
    assert stats.bars == 500, f"Expected 500 bars, got {stats.bars}"
    
    # Test indicators
    df_ind = engine.add_indicators(df)
    assert "rsi" in df_ind.columns, "RSI not computed"
    assert "macd" in df_ind.columns, "MACD not computed"
    assert "atr" in df_ind.columns, "ATR not computed"
    assert "bb_upper" in df_ind.columns, "Bollinger Bands not computed"
    
    # Test resample
    df_1h = engine.resample(df, "1h")
    assert len(df_1h) < len(df), "Resample should reduce bars"
    
    # Test numpy conversion
    arrays = engine.to_numpy(df)
    assert "close" in arrays, "close array missing"
    assert len(arrays["close"]) == 500
    
    await engine.stop()
    print("✅ HistoricalDataEngine: PASSED")


async def test_backtester():
    """Test BacktestEngine."""
    from backtesting.backtester import BacktestEngine, BacktestConfig
    
    config = BacktestConfig(
        initial_capital=10000,
        leverage=10,
        risk_per_trade_pct=0.02,
    )
    engine = BacktestEngine(config)
    await engine.initialize()
    
    df = generate_sample_data(1000)
    
    # Simple SMA crossover strategy
    def sma_strategy(data: pd.DataFrame, i: int) -> dict:
        if i < 50:
            return None
        
        sma_20 = data["close"].iloc[i-20:i].mean()
        sma_50 = data["close"].iloc[i-50:i].mean()
        price = data["close"].iloc[i]
        atr = data["high"].iloc[i] - data["low"].iloc[i]
        
        if sma_20 > sma_50 and price > sma_20:
            return {
                "side": "LONG",
                "confidence": 0.7,
                "stop_loss": price - 2 * atr,
                "take_profit": price + 3 * atr,
            }
        return None
    
    result = await engine.run("BTCUSDT", df, sma_strategy)
    
    assert result.symbol == "BTCUSDT", f"Wrong symbol: {result.symbol}"
    assert result.total_bars == 1000, f"Wrong bars: {result.total_bars}"
    assert result.initial_capital == 10000
    print(f"✅ BacktestEngine: PASSED — {result.total_trades} trades, "
          f"win_rate={result.win_rate:.1f}%, pnl=${result.net_pnl:,.2f}")
    
    return result


async def test_monte_carlo():
    """Test MonteCarloEngine."""
    from backtesting.monte_carlo import MonteCarloEngine, MonteCarloConfig
    
    config = MonteCarloConfig(n_simulations=1000, random_seed=42)
    engine = MonteCarloEngine(config)
    await engine.initialize()
    
    # Generate sample trade PnLs
    np.random.seed(42)
    trade_pnls = np.random.normal(50, 200, 100).tolist()
    
    # Test bootstrap
    result = await engine.bootstrap_trades(trade_pnls)
    assert result.n_simulations == 1000
    assert result.mean_return != 0
    assert 0 <= result.probability_of_profit <= 1
    assert 0 <= result.probability_of_ruin <= 1
    
    # Test permutation
    result2 = await engine.permute_trades(trade_pnls)
    assert result2.n_simulations == 1000
    
    # Test stress test
    stress_results = await engine.stress_test(trade_pnls)
    assert "normal" in stress_results
    assert "high_slippage" in stress_results
    
    print(f"✅ MonteCarloEngine: PASSED — P(profit)={result.probability_of_profit:.1%}, "
          f"P(ruin)={result.probability_of_ruin:.1%}")


async def test_optimizer():
    """Test AIAdaptiveOptimizer."""
    from backtesting.optimizer import AIAdaptiveOptimizer, AIAdaptiveConfig
    
    config = AIAdaptiveConfig(
        method="bayesian",
        n_iterations=50,
        n_initial_random=10,
    )
    optimizer = AIAdaptiveOptimizer(config)
    await optimizer.initialize()
    
    # Simple objective function (maximize)
    def objective(params: dict) -> float:
        # Peak at stop_loss=2.0, take_profit=4.0
        sl_score = 1.0 - abs(params.get("stop_loss_atr_mult", 2) - 2.0) / 2.0
        tp_score = 1.0 - abs(params.get("take_profit_atr_mult", 3) - 4.0) / 3.0
        return (sl_score + tp_score) / 2
    
    result = await optimizer.optimize(objective)
    
    assert result.best_score > 0
    assert len(result.all_trials) > 0
    assert len(result.feature_importance) > 0
    
    print(f"✅ AIAdaptiveOptimizer: PASSED — best_score={result.best_score:.4f}")


async def test_analytics():
    """Test PerformanceAnalyticsEngine."""
    from backtesting.analytics import PerformanceAnalyticsEngine
    
    engine = PerformanceAnalyticsEngine(initial_capital=10000)
    await engine.initialize()
    
    # Generate sample trades
    np.random.seed(42)
    trades = []
    base_time = datetime.now() - timedelta(days=30)
    
    for i in range(50):
        is_long = np.random.random() > 0.4
        entry_price = 50000 + np.random.normal(0, 1000)
        pnl = np.random.normal(100, 300)
        
        trades.append({
            "side": "LONG" if is_long else "SHORT",
            "entry_price": entry_price,
            "exit_price": entry_price + (pnl / 0.1 if is_long else -pnl / 0.1),
            "size": 0.1,
            "pnl": pnl,
            "fees": abs(pnl) * 0.001,
            "slippage": abs(pnl) * 0.0005,
            "entry_time": base_time + timedelta(hours=i * 12),
            "exit_time": base_time + timedelta(hours=i * 12 + np.random.randint(1, 6)),
            "exit_reason": "take_profit" if pnl > 0 else "stop_loss",
            "hold_time_minutes": np.random.randint(30, 360),
        })
    
    report = await engine.analyze("BTCUSDT", trades)
    
    assert report.symbol == "BTCUSDT"
    assert report.trade_analytics.total_trades == 50
    assert 0 <= report.overall_score <= 100
    assert report.risk_analytics.sharpe_ratio != 0 or report.risk_analytics.max_drawdown_pct == 0
    
    # Test JSON export
    filepath = engine.export_json(report)
    assert Path(filepath).exists()
    
    print(f"✅ PerformanceAnalyticsEngine: PASSED — score={report.overall_score:.0f}/100, "
          f"sharpe={report.risk_analytics.sharpe_ratio:.2f}")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Phase 5 — Backtesting + Optimization: Test Suite")
    print("=" * 60)
    
    await test_historical_data()
    await test_backtester()
    await test_monte_carlo()
    await test_optimizer()
    await test_analytics()
    
    print("=" * 60)
    print("✅ ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
