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
