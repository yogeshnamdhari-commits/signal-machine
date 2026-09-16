from core.signal_consensus import aggregate_signals
from core.signal_contract import EngineSignal


def _signal(engine, group, side="BUY", score=90):
    return EngineSignal(
        engine_id=engine,
        engine_version="1.0.0",
        symbol="BTCUSDT",
        side=side,
        score=score,
        event_ts=1000,
        provenance_ids=("p1",),
        independent_group=group,
        config_fingerprint="cfg",
        source="binance",
    )


def test_shared_evidence_group_is_not_double_counted():
    result = aggregate_signals([_signal("orderflow", "market_microstructure"), _signal("dom", "market_microstructure")], symbol="BTCUSDT")[0]
    assert result.agreement_count == 2
    assert result.independent_agreement == 1


def test_conflicting_engine_is_recorded():
    result = aggregate_signals([_signal("orderflow", "flow"), _signal("regime", "regime", side="SELL")], symbol="BTCUSDT")
    buy = next(r for r in result if r.side == "BUY")
    assert buy.conflict_count == 1
    assert buy.unanimous is False
