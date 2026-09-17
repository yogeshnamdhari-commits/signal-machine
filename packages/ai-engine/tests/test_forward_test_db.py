from scanner.forward_test_db import ForwardTestDB


def test_record_trade_includes_funding_in_net_pnl(tmp_path):
    db = ForwardTestDB(str(tmp_path / "forward_test.db"))

    trade_id = db.record_trade(
        {
            "timestamp": 1_700_000_000,
            "symbol": "TEST",
            "side": "LONG",
            "entry_price": 100.0,
            "entry_time": 1_700_000_000,
            "exit_price": 110.0,
            "exit_time": 1_700_000_600,
            "exit_reason": "TP3",
            "pnl": 100.0,
            "fees": 3.0,
            "funding": 2.0,
        }
    )

    row = db.query("SELECT gross_pnl, net_pnl, funding, outcome FROM forward_trades WHERE id=?", (trade_id,))[0]

    assert row["gross_pnl"] == 100.0
    assert row["net_pnl"] == 95.0
    assert row["funding"] == 2.0
    assert row["outcome"] == "win"


def test_record_trade_persists_realized_pnl_decomposition(tmp_path):
    db = ForwardTestDB(str(tmp_path / "forward_test.db"))

    trade_id = db.record_trade(
        {
            "timestamp": 1_700_000_000,
            "symbol": "TEST",
            "side": "LONG",
            "entry_price": 100.0,
            "entry_time": 1_700_000_000,
            "exit_price": 110.0,
            "exit_time": 1_700_000_600,
            "pnl": 100.0,
            "fees": 3.0,
            "funding": 2.0,
        }
    )

    row = db.query(
        "SELECT gross_pnl, fees, funding, realized_pnl_raw, realized_pnl_rounded, net_pnl "
        "FROM forward_trades WHERE id=?",
        (trade_id,),
    )[0]

    assert row["gross_pnl"] == 100.0
    assert row["fees"] == 3.0
    assert row["funding"] == 2.0
    assert row["realized_pnl_raw"] == 95.0
    assert row["realized_pnl_rounded"] == 95.0
    assert row["net_pnl"] == 95.0


def test_record_trade_includes_slippage_in_net_pnl(tmp_path):
    db = ForwardTestDB(str(tmp_path / "forward_test.db"))

    trade_id = db.record_trade(
        {
            "timestamp": 1_700_000_000,
            "symbol": "TEST",
            "side": "LONG",
            "entry_price": 100.0,
            "entry_time": 1_700_000_000,
            "exit_price": 110.0,
            "exit_time": 1_700_000_600,
            "pnl": 100.0,
            "fees": 3.0,
            "funding": 2.0,
            "slippage": 4.0,
        }
    )

    row = db.query(
        "SELECT gross_pnl, fees, funding, slippage, realized_pnl_raw, realized_pnl_rounded, net_pnl, outcome "
        "FROM forward_trades WHERE id=?",
        (trade_id,),
    )[0]

    assert row["gross_pnl"] == 100.0
    assert row["fees"] == 3.0
    assert row["funding"] == 2.0
    assert row["slippage"] == 4.0
    assert row["realized_pnl_raw"] == 91.0
    assert row["realized_pnl_rounded"] == 91.0
    assert row["net_pnl"] == 91.0
    assert row["outcome"] == "win"
