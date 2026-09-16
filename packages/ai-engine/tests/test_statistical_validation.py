from validation.statistical_validation import ResearchEvidence, evaluate_research_evidence


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


def test_temporal_overlap_is_rejected():
    evidence = ResearchEvidence(
        completed_trades=150,
        train_period=("2025-01-01", "2025-05-01"),
        validation_period=("2025-04-15", "2025-06-01"),
        test_period=("2025-06-02", "2025-07-01"),
        regime_counts={"bull": 80, "bear": 70},
        cost_model="fees+spread+slippage",
        funding_source="historical",
        rejected_signals=2000,
        rejected_outcomes_complete=True,
        bootstrap_seed=1,
        code_commit="abc",
        config_fingerprint="def",
    )
    decision = evaluate_research_evidence(evidence)
    assert not decision.approved
    assert "overlapping_temporal_windows" in decision.reasons
