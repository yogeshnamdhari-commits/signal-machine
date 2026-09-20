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
    engine_source = inspect.getsource(DeltaTerminalEngine)

    assert "Generate a synthetic trade" not in source
    assert "_oi_proxy_state" not in oi_source
    assert "Trade flow proxy" not in oi_source
    assert "_rest_trade_poll_loop" not in engine_source


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


def test_funding_direction_uses_percent_units():
    from core.parameter_semantics import direction_for_parameter
    from core.directional_factor import DirectionState

    row = {
        "funding": 0.005,  # 0.005%, below +/-0.01% neutral band
        "mark_price": 100.0,
        "timestamp": time.time(),
    }
    factor = direction_for_parameter("funding", row)
    assert factor.state is DirectionState.NEUTRAL


def test_live_sheet_suppresses_stale_metric_independently():
    from dashboard.live_sheet_contract import display_value

    stale = time.time() - 120
    row = {
        "net_delta": 123.0,
        "flow_total_trades": 100,
        "metric_timestamps": {"net_delta": stale},
    }
    assert display_value(row, "net_delta") is None


def test_l1_quotes_cannot_drive_l2_imbalance():
    from core.engine import DeltaTerminalEngine

    source = inspect.getsource(DeltaTerminalEngine._on_data)
    assert 'depth_quality == "L1"' in source
    assert 'depth_quality != "L2_TOP20_SNAPSHOT"' in source


def test_taker_flow_is_exact_five_minute_window():
    from scanner.exchange_flow import ExchangeFlowEngine

    async def run():
        eng = ExchangeFlowEngine()
        now_ms = int(time.time() * 1000)
        await eng.process_trade("BTCUSDT", {
            "price": 100.0, "quantity": 10.0,
            "is_buyer_maker": True,
            "trade_time": now_ms - 6 * 60 * 1000,
        })
        await eng.process_trade("BTCUSDT", {
            "price": 100.0, "quantity": 2.0,
            "is_buyer_maker": False,
            "trade_time": now_ms - 30 * 1000,
        })
        return eng.get_analysis("BTCUSDT")

    result = asyncio.run(run())
    assert result is not None
    assert result["taker_buy_vol"] == 200.0
    assert result["taker_sell_vol"] == 0.0
    assert result["window_trades"] == 1


def test_fvg_does_not_mix_candle_timeframes():
    from scanner.fvg_detector import FVGDetect

    async def run():
        det = FVGDetect()
        base = int(time.time() * 1000)
        def k(iv, idx, high, low, close):
            return {
                "symbol": "BTCUSDT",
                "interval": iv,
                "open_time": base + idx * 60_000,
                "close_time": base + idx * 60_000 + 59_999,
                "high": high, "low": low, "close": close,
                "is_closed": True,
            }

        # Two 5m candles, then an unrelated 1m candle with a large gap.
        await det.process_kline("BTCUSDT", k("5m", 0, 101, 99, 100))
        await det.process_kline("BTCUSDT", k("5m", 1, 102, 100, 101))
        event = await det.process_kline("BTCUSDT", k("1m", 2, 120, 110, 115))
        assert event is None

        # A third 5m candle then forms only from the 5m sequence.
        event = await det.process_kline("BTCUSDT", k("5m", 2, 103, 99, 101))
        return event

    event = asyncio.run(run())
    assert event is None
