import asyncio
import inspect
import time


def test_cvd_uses_true_rolling_windows():
    from core.cvd_engine import CVDEngine

    cvd = CVDEngine()
    now_ms = int(time.time() * 1000)

    # Old sell trade: outside 5m but inside 1h.
    cvd.update("BTCUSDT", 100.0, 10.0, True, timestamp_ms=now_ms - 20 * 60 * 1000)
    # Recent buy trade: inside both 5m and 1h.
    cvd.update("BTCUSDT", 100.0, 4.0, False, timestamp_ms=now_ms - 30 * 1000)

    five_m = cvd.get_cvd("BTCUSDT", "5m")
    one_h = cvd.get_cvd("BTCUSDT", "1h")

    assert five_m == 4.0
    assert one_h == -6.0


def test_oi_5m_change_is_separate_from_tick_change():
    from scanner.open_interest import OpenInterestEngine

    async def run():
        oi = OpenInterestEngine()
        base = time.time()

        for i in range(4):
            await oi.process_oi(
                "BTCUSDT",
                1000 + i * 5,
                100 + i,
                base + i,
                change_5m_pct=None,
            )

        await oi.process_oi(
            "BTCUSDT",
            1020,
            105,
            base + 4,
            change_5m_pct=1.25,
        )

        return oi.get_analysis("BTCUSDT")

    result = asyncio.run(run())

    assert result is not None
    assert result["change_5m_pct"] == 1.25
    assert result["positioning"] == "long_buildup"
    assert result["oi_regime"] == "bullish_oi"


def test_ordinary_trades_never_create_liquidation_evidence():
    from scanner.liquidation import LiquidationEngine

    async def run():
        liq = LiquidationEngine()
        await liq.process_trade(
            "BTCUSDT",
            {"price": 100.0, "quantity": 5000.0, "is_buyer_maker": True},
        )
        assert liq.get_analysis("BTCUSDT") is None

        await liq.process_force_order(
            symbol="BTCUSDT",
            side="SELL",
            price=95.0,
            quantity=2.0,
            timestamp_ms=int(time.time() * 1000),
        )
        return liq.get_analysis("BTCUSDT")

    result = asyncio.run(run())

    assert result is not None
    assert result["long_liq_count"] == 1
    assert result["short_liq_count"] == 0
    assert result["long_liq_vol"] == 190.0


def test_live_row_source_has_no_synthetic_trade_or_oi_proxy():
    from core.engine import DeltaTerminalEngine

    source = inspect.getsource(DeltaTerminalEngine._prefetch_klines)
    oi_source = inspect.getsource(DeltaTerminalEngine._oi_poll_loop)

    assert "Generate a synthetic trade" not in source
    assert "_oi_proxy_state" not in oi_source
    assert "Trade flow proxy" not in oi_source


def test_live_sheet_contract_does_not_turn_missing_flow_into_values():
    from dashboard.live_sheet_contract import display_value

    missing = {
        "flow_total_trades": 0,
        "net_delta": None,
        "buy_sell_ratio": None,
        "cvd_5m": None,
        "exchange_flow": None,
        "flow_strength": None,
    }

    assert display_value(missing, "net_delta") is None
    assert display_value(missing, "buy_sell_ratio") is None
    assert display_value(missing, "cvd_5m") is None
    assert display_value(missing, "exchange_flow") is None
    assert display_value(missing, "flow_strength") is None
