from app_layer.rejected_trade_learner import RejectedTradeLearner


def test_rejected_signal_counterfactual_attribution_records_path_metrics(tmp_path):
    learner = RejectedTradeLearner(
        log_path=tmp_path / "rejected_signals.json",
    )
    learner.log_rejection(
        symbol="TEST",
        side="LONG",
        rejection_stage="eligibility",
        rejection_reason="score below threshold",
        scores={"eligibility_score": 82},
    )

    bars = [
        {"timestamp": 100.0, "high": 101.5, "low": 99.5, "close": 101.0},
        {"timestamp": 160.0, "high": 102.0, "low": 100.5, "close": 101.5},
        {"timestamp": 220.0, "high": 102.5, "low": 101.0, "close": 102.0},
    ]

    learner.attribute_counterfactual_outcome(
        symbol="TEST",
        side="LONG",
        entry_price=100.0,
        risk_per_unit=1.0,
        bars=bars,
        stop_price=99.0,
        target_price=102.0,
    )

    signal = learner._rejected_signals[0]
    assert signal.outcome_tracked is True
    assert signal.outcome_r == 2.0
    assert signal.mfe_r == 2.5
    assert signal.mae_r == 0.5
    assert signal.holding_bars == 3
    assert signal.exit_reason == "TP"
    assert signal.tp_hit is True
    assert signal.sl_hit is False
    assert signal.outcome_source == "counterfactual_market_path"
