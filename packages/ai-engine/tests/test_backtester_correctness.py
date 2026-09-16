import asyncio
from datetime import datetime, timedelta

import pandas as pd
import pytest

from backtesting.backtester import BacktestConfig, BacktestEngine


def _data(n=80):
    start = datetime(2026, 1, 1)
    rows = []
    price = 100.0
    for i in range(n):
        price += 0.2
        ts = start + timedelta(minutes=5 * i)
        rows.append({
            "open_time": ts,
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price,
            "volume": 1000.0,
            "funding_rate": 0.0001,
        })
    return pd.DataFrame(rows)


def test_backtest_rejects_future_data_access_pattern():
    seen = []
    data = _data()

    def signal(frame, i):
        seen.append(len(frame))
        assert len(frame) == i + 1
        return None

    asyncio.run(BacktestEngine(BacktestConfig(random_seed=7)).run("BTCUSDT", data, signal))
    assert seen


def test_backtest_is_reproducible_with_same_seed():
    data = _data()

    def signal(frame, i):
        if i == 50:
            return {"side": "LONG", "stop_loss": float(frame.iloc[i]["close"] - 1), "take_profit": float(frame.iloc[i]["close"] + 2)}
        return None

    a = asyncio.run(BacktestEngine(BacktestConfig(random_seed=7)).run("BTCUSDT", data, signal))
    b = asyncio.run(BacktestEngine(BacktestConfig(random_seed=7)).run("BTCUSDT", data, signal))
    assert a.final_equity == b.final_equity
    assert [(t.entry_time, t.exit_time, t.net_pnl) for t in a.trades] == [(t.entry_time, t.exit_time, t.net_pnl) for t in b.trades]


def test_trade_net_pnl_includes_funding():
    from backtesting.backtester import Trade, Side
    trade = Trade(
        symbol="BTCUSDT", side=Side.LONG, entry_price=100, exit_price=101, size=1,
        entry_time=datetime(2026, 1, 1), exit_time=datetime(2026, 1, 1, 1),
        pnl=1, fees=0.1, slippage=0.1, funding=0.2, exit_reason="test", hold_time_minutes=60,
    )
    assert trade.net_pnl == pytest.approx(0.6, abs=1e-12)
