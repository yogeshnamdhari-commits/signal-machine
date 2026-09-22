from unittest.mock import AsyncMock, Mock

import pytest

from backtesting.paper_trading_validator import PaperTradingEngine


@pytest.mark.asyncio
async def test_force_order_reaches_liquidation_engine() -> None:
    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine.active_symbols = {"BTCUSDT"}
    engine.symbol_data = {}
    engine.liquidation = Mock()
    engine.liquidation.process_liquidation_event = AsyncMock()
    engine.health_monitor = Mock()

    event = {
        "symbol": "BTCUSDT",
        "side": "SELL",
        "price": 100000.0,
        "quantity": 0.25,
        "timestamp": 1780000000000,
        "source": "binance",
        "feed": "forceOrder",
        "data_quality": "REAL",
    }

    await engine._on_market_data("liquidation", event)

    engine.liquidation.process_liquidation_event.assert_awaited_once_with(
        "BTCUSDT", event
    )


@pytest.mark.asyncio
async def test_non_force_order_liquidation_is_rejected_fail_closed() -> None:
    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    engine.active_symbols = {"BTCUSDT"}
    engine.symbol_data = {}
    engine.liquidation = Mock()
    engine.liquidation.process_liquidation_event = AsyncMock()
    engine.health_monitor = Mock()

    event = {
        "symbol": "BTCUSDT",
        "side": "SELL",
        "price": 100000.0,
        "quantity": 0.25,
        "timestamp": 1780000000000,
        "source": "binance",
        "feed": "aggTrade",
        "data_quality": "REAL",
    }

    await engine._on_market_data("liquidation", event)

    engine.liquidation.process_liquidation_event.assert_not_awaited()
