import pytest

from backtesting.paper_trading_validator import calculate_funding_pnl


def test_long_position_pays_positive_funding_and_receives_negative_funding():
    assert calculate_funding_pnl("LONG", 100000.0, 0.0001) == -10.0
    assert calculate_funding_pnl("LONG", 100000.0, -0.0001) == 10.0


def test_short_position_has_inverse_funding_cashflow():
    assert calculate_funding_pnl("SHORT", 100000.0, 0.0001) == 10.0
    assert calculate_funding_pnl("SHORT", 100000.0, -0.0001) == -10.0


def test_zero_notional_or_zero_rate_has_zero_funding():
    assert calculate_funding_pnl("LONG", 0.0, 0.0001) == 0.0
    assert calculate_funding_pnl("LONG", 100000.0, 0.0) == 0.0


def test_funding_is_accrued_to_trade_and_included_in_closed_net_pnl():
    from backtesting.paper_trading_validator import PaperSignal, SimulatedPositionManager
    import time

    mgr = SimulatedPositionManager()
    sig = PaperSignal(
        id="SIG-FUND-001", timestamp=time.time(), symbol="BTCUSDT",
        side="LONG", entry_price=65000, stop_loss=64000, take_profit=67000,
        confidence=0.8, institutional_score=80, market_regime="trending_up",
    )
    trade = mgr.open_position(sig, 65000, 1.0, 1)

    funding = mgr.apply_funding(trade.id, funding_rate=0.0001, mark_price=65000, settlement_time=1_800_000_000_000)
    assert funding == -6.5
    assert trade.funding_pnl == -6.5
    assert trade.funding_events == 1
    assert trade.last_funding_time == 1_800_000_000_000

    closed = mgr.close_position(trade.id, 65000, "test_close")
    assert closed is not None
    assert closed.funding_pnl == -6.5
    assert closed.net_pnl < 0


def test_same_funding_settlement_cannot_be_applied_twice():
    from backtesting.paper_trading_validator import PaperSignal, SimulatedPositionManager
    import time

    mgr = SimulatedPositionManager()
    sig = PaperSignal(
        id="SIG-FUND-002", timestamp=time.time(), symbol="ETHUSDT",
        side="SHORT", entry_price=3500, stop_loss=3600, take_profit=3300,
        confidence=0.8, institutional_score=80, market_regime="trending_down",
    )
    trade = mgr.open_position(sig, 3500, 1.0, 1)

    first = mgr.apply_funding(trade.id, funding_rate=0.0002, mark_price=3500, settlement_time=1_800_000_000_000)
    second = mgr.apply_funding(trade.id, funding_rate=0.0002, mark_price=3500, settlement_time=1_800_000_000_000)
    assert first == pytest.approx(0.7)
    assert second == 0.0
    assert trade.funding_events == 1
    assert trade.funding_pnl == pytest.approx(0.7)


def test_funding_event_is_ingested_as_real_market_data():
    import time
    from backtesting.paper_trading_validator import PaperTradingEngine

    engine = PaperTradingEngine()
    engine.active_symbols.add("BTCUSDT")
    observed_at = int(time.time() * 1000)
    event = {
        "symbol": "BTCUSDT",
        "mark_price": 65000.0,
        "index_price": 64990.0,
        "funding_rate": 0.0001,
        "next_funding_time": observed_at + 60_000,
        "timestamp": observed_at,
        "source": "binance",
        "feed": "markPrice",
        "data_quality": "REAL",
    }
    import asyncio
    asyncio.run(engine._on_market_data("funding", event))

    cached = engine.symbol_data["BTCUSDT"]["funding"]
    assert cached["funding_rate"] == 0.0001
    assert cached["next_funding_time"] == observed_at + 60_000
    assert cached["data_quality"] == "REAL"


def test_due_funding_uses_recorded_settlement_schedule_once():
    import asyncio
    import time
    from unittest.mock import patch
    from backtesting.paper_trading_validator import PaperSignal, PaperTradingEngine

    engine = PaperTradingEngine()
    engine.active_symbols.add("BTCUSDT")
    sig = PaperSignal(
        id="SIG-FUND-003", timestamp=1700000000.0, symbol="BTCUSDT",
        side="LONG", entry_price=65000, stop_loss=64000, take_profit=67000,
        confidence=0.8, institutional_score=80, market_regime="trending_up",
    )
    trade = engine.position_mgr.open_position(sig, 65000, 1.0, 1)

    settlement_ms = 1_800_000_000_000
    event = {
        "symbol": "BTCUSDT",
        "mark_price": 65000.0,
        "index_price": 64990.0,
        "funding_rate": 0.0001,
        "next_funding_time": settlement_ms,
        "timestamp": settlement_ms - 1_000,
        "source": "binance",
        "feed": "markPrice",
        "data_quality": "REAL",
    }

    asyncio.run(engine._on_market_data("funding", event))

    with patch("backtesting.paper_trading_validator.time.time", return_value=(settlement_ms / 1000.0) + 1):
        engine._settle_due_funding()
        engine._settle_due_funding()

    assert trade.funding_events == 1
    assert trade.last_funding_time == settlement_ms
    assert trade.funding_pnl == -6.5


def test_rolling_next_funding_time_does_not_drop_prior_due_interval():
    import asyncio
    from unittest.mock import patch
    from backtesting.paper_trading_validator import PaperSignal, PaperTradingEngine

    engine = PaperTradingEngine()
    engine.active_symbols.add("BTCUSDT")
    sig = PaperSignal(
        id="SIG-FUND-004", timestamp=1700000000.0, symbol="BTCUSDT",
        side="LONG", entry_price=65000, stop_loss=64000, take_profit=67000,
        confidence=0.8, institutional_score=80, market_regime="trending_up",
    )
    trade = engine.position_mgr.open_position(sig, 65000, 1.0, 1)

    first_settlement = 1_800_000_000_000
    second_settlement = first_settlement + 28_800_000

    first_event = {
        "symbol": "BTCUSDT", "mark_price": 65000.0, "index_price": 64990.0,
        "funding_rate": 0.0001, "next_funding_time": first_settlement,
        "timestamp": first_settlement - 1_000, "source": "binance",
        "feed": "markPrice", "data_quality": "REAL",
    }
    second_event = {
        **first_event,
        "funding_rate": 0.0002,
        "next_funding_time": second_settlement,
        "timestamp": second_settlement - 1_000,
    }

    asyncio.run(engine._on_market_data("funding", first_event))
    asyncio.run(engine._on_market_data("funding", second_event))

    with patch(
        "backtesting.paper_trading_validator.time.time",
        return_value=(first_settlement / 1000.0) + 1,
    ):
        engine._settle_due_funding()

    assert trade.funding_events == 1
    assert trade.last_funding_time == first_settlement
    assert trade.funding_pnl == -6.5
