from validation.statistical_validation import ResearchEvidence, evaluate_research_evidence


def _complete_evidence(**overrides):
    values = {
        "completed_trades": 150,
        "train_period": ("2025-01-01", "2025-05-01"),
        "validation_period": ("2025-05-02", "2025-06-01"),
        "test_period": ("2025-06-02", "2025-07-01"),
        "regime_counts": {"bull": 80, "bear": 70},
        "cost_model": "fees+spread",
        "slippage_model": "historical_orderbook_bps",
        "funding_source": "historical",
        "rejected_signals": 2000,
        "rejected_outcomes_complete": True,
        "bootstrap_seed": 1,
        "code_commit": "abc",
        "config_fingerprint": "def",
        "profit_factor": 1.35,
        "expectancy": 2.5,
        "max_drawdown_pct": 8.0,
    }
    values.update(overrides)
    return ResearchEvidence(**values)


def test_insufficient_evidence_is_rejected():
    evidence = ResearchEvidence(
        completed_trades=47,
        train_period=("2025-01-01", "2025-05-01"),
        validation_period=("2025-05-02", "2025-06-01"),
        test_period=("2025-06-02", "2025-07-01"),
    )
    decision = evaluate_research_evidence(evidence)
    assert not decision.approved
    assert "insufficient_completed_trades" in decision.reasons
    assert "missing_cost_model" in decision.reasons
    assert "missing_slippage_model" in decision.reasons
    assert "missing_bootstrap_seed" in decision.reasons


def test_temporal_overlap_is_rejected():
    evidence = _complete_evidence(
        validation_period=("2025-04-15", "2025-06-01"),
    )
    decision = evaluate_research_evidence(evidence)
    assert not decision.approved
    assert "overlapping_temporal_windows" in decision.reasons


def test_invalid_temporal_window_is_rejected():
    decision = evaluate_research_evidence(
        _complete_evidence(test_period=("2025-07-01", "2025-06-01"))
    )
    assert not decision.approved
    assert "invalid_temporal_window" in decision.reasons


def test_malformed_temporal_window_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(train_period=("2025-01-01",)))
    assert not decision.approved
    assert "invalid_temporal_window" in decision.reasons


def test_invalid_calendar_date_is_rejected():
    decision = evaluate_research_evidence(
        _complete_evidence(train_period=("2025-02-30", "2025-05-01"))
    )
    assert not decision.approved
    assert "invalid_temporal_window" in decision.reasons


def test_timezone_naive_datetime_is_rejected():
    decision = evaluate_research_evidence(
        _complete_evidence(
            train_period=("2025-01-01T00:00:00", "2025-05-01T00:00:00"),
        )
    )
    assert not decision.approved
    assert "invalid_temporal_window" in decision.reasons


def test_timezone_aware_datetime_window_is_accepted():
    decision = evaluate_research_evidence(
        _complete_evidence(
            train_period=("2025-01-01T00:00:00Z", "2025-05-01T00:00:00Z"),
            validation_period=("2025-05-02T00:00:00+00:00", "2025-06-01T00:00:00+00:00"),
            test_period=("2025-06-02T00:00:00+00:00", "2025-07-01T00:00:00+00:00"),
        )
    )
    assert decision.approved


def test_mixed_date_and_datetime_endpoints_are_comparable():
    decision = evaluate_research_evidence(
        _complete_evidence(
            train_period=("2025-01-01", "2025-05-01T00:00:00Z"),
            validation_period=("2025-05-02", "2025-06-01T00:00:00Z"),
            test_period=("2025-06-02", "2025-07-01T00:00:00Z"),
        )
    )
    assert decision.approved


def test_placeholder_cost_model_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(cost_model="not modeled"))
    assert not decision.approved
    assert "missing_cost_model" in decision.reasons


def test_placeholder_slippage_model_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(slippage_model="N/A"))
    assert not decision.approved
    assert "missing_slippage_model" in decision.reasons


def test_placeholder_funding_source_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(funding_source="unknown"))
    assert not decision.approved
    assert "missing_funding_source" in decision.reasons


def test_negative_completed_trade_count_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(completed_trades=-1))
    assert not decision.approved
    assert "invalid_completed_trade_count" in decision.reasons


def test_invalid_regime_counts_are_rejected():
    decision = evaluate_research_evidence(_complete_evidence(regime_counts={"bull": -1, "bear": 2}))
    assert not decision.approved
    assert "missing_regime_coverage" in decision.reasons


def test_regime_trade_count_mismatch_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(regime_counts={"bull": 80, "bear": 69}))
    assert not decision.approved
    assert "regime_trade_count_mismatch" in decision.reasons


def test_negative_profit_factor_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(profit_factor=0.82))
    assert not decision.approved
    assert "profit_factor_below_minimum" in decision.reasons


def test_negative_expectancy_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(expectancy=-4.27))
    assert not decision.approved
    assert "expectancy_not_positive" in decision.reasons


def test_negative_rejected_signal_count_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(rejected_signals=-1))
    assert not decision.approved
    assert "invalid_rejected_signal_count" in decision.reasons


def test_missing_slippage_model_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(slippage_model=""))
    assert not decision.approved
    assert "missing_slippage_model" in decision.reasons


def test_missing_bootstrap_seed_is_rejected():
    decision = evaluate_research_evidence(_complete_evidence(bootstrap_seed=None))
    assert not decision.approved
    assert "missing_bootstrap_seed" in decision.reasons
