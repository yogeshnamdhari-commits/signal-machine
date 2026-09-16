import math

from execution.forward_test_validator import ForwardTestValidator


def _trade(*, pnl=2.0, exit_reason="take_profit", **extra):
    trade = {"pnl": pnl, "exit_reason": exit_reason}
    trade.update(extra)
    return trade


def test_unknown_exit_reasons_block_production_readiness(tmp_path):
    validator = ForwardTestValidator(tmp_path / "audit.json")
    validator._check_interval = 0
    trades = [_trade() for _ in range(100)]
    trades[0]["exit_reason"] = "unknown"

    result = validator.validate(trades)

    assert result["ready"] is False
    assert result["checks"]["outcome_attribution"]["pass"] is False
    assert result["checks"]["outcome_attribution"]["unknown_exit_reasons"] == 1


def test_paper_trader_exit_aliases_are_attributed(tmp_path):
    validator = ForwardTestValidator(tmp_path / "audit.json")
    validator._check_interval = 0
    trades = [_trade() for _ in range(100)]
    trades[0]["exit_reason"] = "SL"
    trades[1]["exit_reason"] = "TP3"
    trades[2]["exit_reason"] = "TIME"

    result = validator.validate(trades)

    assert result["checks"]["outcome_attribution"]["pass"] is True
    assert result["checks"]["outcome_attribution"]["unknown_exit_reasons"] == 0


def test_net_pnl_is_used_when_available(tmp_path):
    validator = ForwardTestValidator(tmp_path / "audit.json")
    validator._check_interval = 0
    trades = [_trade(pnl=100.0, net_pnl=-1.0) for _ in range(100)]

    result = validator.validate(trades)

    assert result["summary"]["total_pnl"] == -100.0
    assert result["checks"]["data_quality"]["pnl_source"] == "net_pnl_when_available"
    assert result["ready"] is False


def test_invalid_fees_block_production_readiness(tmp_path):
    validator = ForwardTestValidator(tmp_path / "audit.json")
    validator._check_interval = 0
    trades = [_trade(fees=0.5) for _ in range(100)]
    trades[0]["fees"] = math.nan

    result = validator.validate(trades)

    assert result["ready"] is False
    assert result["checks"]["data_quality"]["pass"] is False
    assert result["checks"]["data_quality"]["invalid_fee_records"] == 1


def test_non_finite_pnl_blocks_production_readiness(tmp_path):
    validator = ForwardTestValidator(tmp_path / "audit.json")
    validator._check_interval = 0
    trades = [_trade() for _ in range(100)]
    trades[0]["pnl"] = math.nan

    result = validator.validate(trades)

    assert result["ready"] is False
    assert result["checks"]["data_quality"]["pass"] is False
    assert result["checks"]["data_quality"]["non_finite_pnl"] == 1
