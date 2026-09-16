import math

from execution.forward_test_validator import ForwardTestValidator


def _trade(*, pnl=2.0, exit_reason="take_profit"):
    return {"pnl": pnl, "exit_reason": exit_reason}


def test_unknown_exit_reasons_block_production_readiness(tmp_path):
    validator = ForwardTestValidator(tmp_path / "audit.json")
    validator._check_interval = 0
    trades = [_trade() for _ in range(100)]
    trades[0]["exit_reason"] = "unknown"

    result = validator.validate(trades)

    assert result["ready"] is False
    assert result["checks"]["outcome_attribution"]["pass"] is False
    assert result["checks"]["outcome_attribution"]["unknown_exit_reasons"] == 1


def test_non_finite_pnl_blocks_production_readiness(tmp_path):
    validator = ForwardTestValidator(tmp_path / "audit.json")
    validator._check_interval = 0
    trades = [_trade() for _ in range(100)]
    trades[0]["pnl"] = math.nan

    result = validator.validate(trades)

    assert result["ready"] is False
    assert result["checks"]["data_quality"]["pass"] is False
    assert result["checks"]["data_quality"]["non_finite_pnl"] == 1
