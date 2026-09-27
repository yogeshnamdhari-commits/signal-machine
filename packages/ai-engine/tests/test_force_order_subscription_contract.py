from unittest.mock import AsyncMock

import pytest

from exchanges.binance_ws import BinanceWebSocket


@pytest.mark.asyncio
async def test_market_subscription_ack_and_force_order_listing_are_recorded() -> None:
    ws = BinanceWebSocket()

    await ws._dispatch({"result": None, "id": 1}, "market")
    assert ws.get_stats()["subscription_ack_count"]["market"] == 1

    await ws._dispatch(
        {"result": ["!ticker@arr", "!forceOrder@arr"], "id": 990001},
        "market",
    )
    assert ws.get_stats()["force_order_subscribed"] is True


@pytest.mark.asyncio
async def test_market_subscription_error_is_visible_and_fail_closed() -> None:
    ws = BinanceWebSocket()

    await ws._dispatch(
        {"code": 2, "msg": "Invalid request", "id": 1},
        "market",
    )

    stats = ws.get_stats()
    assert stats["subscription_error_count"]["market"] == 1
    assert stats["force_order_subscribed"] is False
    assert stats["last_subscription_error"]["market"]["code"] == 2


@pytest.mark.asyncio
async def test_authentic_force_order_event_updates_runtime_observation_stats() -> None:
    ws = BinanceWebSocket()
    ws._callback = AsyncMock()

    event = {
        "o": {
            "s": "BTCUSDT",
            "S": "SELL",
            "p": "100000.0",
            "q": "0.25",
            "o": "MARKET",
            "T": 1780000000000,
        }
    }

    await ws._on_force_order(event)

    stats = ws.get_stats()
    assert stats["force_order_event_count"] == 1
    assert stats["last_force_order_event_ms"] == 1780000000000
    assert stats["force_order_subscribed"] is True
    ws._callback.assert_awaited_once()
