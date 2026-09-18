import csv

from backtesting.paper_trading_validator import load_backtest_baseline


def _write_trade_log(path):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["pnl"])
        writer.writeheader()
        writer.writerows([{"pnl": "100"}, {"pnl": "-50"}, {"pnl": "25"}])


def test_backtest_baseline_is_derived_from_source_trade_log(tmp_path):
    path = tmp_path / "trade_log.csv"
    _write_trade_log(path)

    baseline = load_backtest_baseline(path)

    assert baseline is not None
    assert baseline["trade_count"] == 3
    assert baseline["profit_factor"] == 2.5
    assert baseline["win_rate"] == 2 / 3
    assert baseline["max_drawdown_pct"] > 0
    assert baseline["source_sha256"]


def test_missing_backtest_baseline_returns_unavailable(tmp_path):
    assert load_backtest_baseline(tmp_path / "missing.csv") is None


def test_backtest_baseline_rejects_malformed_pnl(tmp_path):
    path = tmp_path / "trade_log.csv"
    path.write_text("pnl\nnot-a-number\n", encoding="utf-8")
    assert load_backtest_baseline(path) is None
