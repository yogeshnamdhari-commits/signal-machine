import pytest

from execution.exchange_adapter import ExchangeAdapter, ExchangeError, ExchangeOrder, OrderSide, OrderType
from execution.order_manager import OrderManager, OrderState, OrderPurpose


class FakeExchange:
    def __init__(self):
        self.place_calls = 0
        self.lookup_calls = 0
        self.lookup_mode = "filled"

    async def place_order(self, **kwargs):
        self.place_calls += 1
        raise ExchangeError("API request failed after retries: /fapi/v1/order")

    async def get_order(self, symbol, order_id=0, client_order_id=""):
        self.lookup_calls += 1
        if self.lookup_mode == "temporary_error":
            raise ExchangeError("temporary transport failure")
        if self.lookup_mode == "not_found":
            raise ExchangeError("API error 400 on /fapi/v1/order: [-2013] Order does not exist")
        status = "FILLED" if self.lookup_mode == "filled" else "NEW"
        return ExchangeOrder(
            order_id=12345,
            client_order_id=client_order_id or "DT-test",
            symbol=symbol,
            side="BUY",
            order_type="MARKET",
            status=status,
            quantity=1.0,
            executed_qty=1.0 if status == "FILLED" else 0.0,
            avg_price=100.0 if status == "FILLED" else 0.0,
        )

    async def cancel_order(self, **kwargs):
        raise AssertionError("cancel_order should not be needed in these tests")


@pytest.mark.asyncio
async def test_ambiguous_submission_reconciles_to_filled():
    exchange = FakeExchange()
    manager = OrderManager(exchange)

    order = await manager.create_order(
        signal_id="SIG-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY.value,
        order_type=OrderType.MARKET,
        purpose=OrderPurpose.ENTRY,
        quantity=1.0,
        timeout_sec=30,
    )

    assert order is not None
    assert order.state == OrderState.FILLED.value
    assert order.exchange_order_id == 12345
    assert exchange.place_calls == 1
    assert exchange.lookup_calls == 1


@pytest.mark.asyncio
async def test_ambiguous_submission_stays_unknown_until_reconciled():
    exchange = FakeExchange()
    exchange.lookup_mode = "temporary_error"
    manager = OrderManager(exchange)

    order = await manager.create_order(
        signal_id="SIG-2",
        symbol="BTCUSDT",
        side=OrderSide.BUY.value,
        order_type=OrderType.MARKET,
        purpose=OrderPurpose.ENTRY,
        quantity=1.0,
        timeout_sec=30,
    )

    assert order.state == OrderState.UNKNOWN.value
    assert order.exchange_order_id == 0

    exchange.lookup_mode = "new"
    await manager.sync_order(order.order_id)

    assert order.state == OrderState.ACCEPTED.value
    assert order.exchange_order_id == 12345


def test_order_formatting_refuses_unknown_symbol_filters():
    adapter = ExchangeAdapter()
    with pytest.raises(ExchangeError, match="Symbol filters unavailable"):
        adapter._format_quantity("UNLISTED", 1.0)


@pytest.mark.asyncio
async def test_load_symbol_filters_uses_exchange_published_rules(monkeypatch):
    adapter = ExchangeAdapter()

    async def fake_request(method, path, params=None, signed=False, retries=5):
        assert method == "GET"
        assert path == "/fapi/v1/exchangeInfo"
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "0.10", "maxPrice": "1000000"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                }
            ]
        }

    monkeypatch.setattr(adapter, "_request", fake_request)
    await adapter.load_symbol_filters(["BTCUSDT"])

    assert adapter._symbol_info_cache["BTCUSDT"]["tick_size"] == 0.10
    assert adapter._symbol_info_cache["BTCUSDT"]["step_size"] == 0.001
    assert adapter._format_quantity("BTCUSDT", 1.23456) == "1.234"
