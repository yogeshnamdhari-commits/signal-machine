import time

from core.trade_tape import has_recent_real_trade


def test_prefetch_synthetic_trade_does_not_count_as_live_trade():
    now = time.time()
    symbol_data = {
        "trades": [
            {
                "price": 100.0,
                "quantity": 0.001,
                "trade_time": now,
                "_source": "prefetch_synthetic",
            }
        ]
    }

    assert has_recent_real_trade(symbol_data, now=now) is False


def test_ticker_array_synthetic_trade_does_not_count_as_live_trade():
    now = time.time()
    symbol_data = {
        "trades": [
            {
                "price": 100.0,
                "quantity": 0.001,
                "trade_time": now,
                "_source": "ticker_arr",
            }
        ]
    }

    assert has_recent_real_trade(symbol_data, now=now) is False


def test_recent_exchange_trade_counts_as_live_trade():
    now = time.time()
    symbol_data = {
        "trades": [
            {
                "price": 100.0,
                "quantity": 0.001,
                "trade_time": now,
                "_source": "rest_trades",
            }
        ]
    }

    assert has_recent_real_trade(symbol_data, now=now) is True


def test_old_real_trade_does_not_count_as_live_trade():
    now = time.time()
    symbol_data = {
        "trades": [
            {
                "price": 100.0,
                "quantity": 0.001,
                "trade_time": now - 61,
                "_source": "rest_trades",
            }
        ]
    }

    assert has_recent_real_trade(symbol_data, now=now, max_age=60) is False
