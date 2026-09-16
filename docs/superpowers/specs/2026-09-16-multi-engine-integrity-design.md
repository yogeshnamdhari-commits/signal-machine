# Multi-Engine Signal Integrity Architecture

**Date:** 2026-09-16
**Goal:** Make the signal-machine repository deployment-safe by eliminating data ambiguity, synthetic-data contamination, configuration drift, duplicate trading authority, incomplete CI, stale validation claims, backtester correctness gaps, Delta environment errors, and insufficient statistical evidence.

## Scope

The work covers the nine approved gates without redesigning the underlying trading strategy unless a correctness defect makes a strategy implementation invalid.

## Architecture

The runtime will use one canonical market-data model and one canonical signal/risk authority. Python remains the authoritative trading/research engine. Node becomes an API/UI integration layer and must not independently create executable trading decisions. All market observations and derived features carry provenance metadata. Synthetic or estimated values are isolated from production signal inputs.

The data path is:

`Exchange adapters -> canonical observations -> provenance/quality gate -> feature store -> independent signal engines -> overlap/consensus engine -> risk/execution gate -> persistence/alerts`

The research path is:

`historical source -> deterministic event replay -> strategy -> execution simulator -> metrics -> statistical validation -> certification artifact`

## Nine hard gates

1. **Provenance:** every observation includes source, feed type, event time, receive time, sequence where available, freshness, and quality state.
2. **Synthetic isolation:** synthetic/estimated observations are explicit types and are rejected by real-market signal engines unless an engine explicitly declares synthetic input support.
3. **Configuration:** one typed, versioned configuration object is authoritative; startup records a configuration fingerprint and rejects invalid environment combinations.
4. **Single authority:** Python owns signal generation, confidence, risk permission, sizing, and execution decisions. Node consumes those results.
5. **CI:** Python, Node, frontend, unit/integration tests, static checks, dependency integrity, and smoke tests are mandatory before synchronization to main.
6. **Certification:** historical reports are immutable research records. Current certification is generated only from current source/config/test/build evidence and is machine-verifiable.
7. **Backtester:** enforce no-lookahead, actual execution timing, deterministic randomness, consistent fee/slippage/funding accounting, correct exit timestamps, and final-equity reconciliation.
8. **Delta environments:** production and testnet REST/WS endpoints are distinct and validated by environment contract tests.
9. **Statistical evidence:** no profitability claim is current unless the current evidence artifact contains sufficient out-of-sample/forward data, cost sensitivity, regime coverage, rejection analysis, and uncertainty estimates.

## Acceptance criteria

The project is not considered deployable until all nine gates have automated tests or machine-checkable evidence. A failed gate blocks main synchronization and the production/live mode.

A signal is executable only when:

- all source observations are provenance-valid and fresh;
- no synthetic observation has entered a real-data decision path;
- the configuration fingerprint is valid and environment-matched;
- the canonical Python authority produced the decision;
- risk and sizing checks pass with non-zero quantity;
- the execution venue environment contract passes;
- current software certification is valid.

## Non-goals

No new predictive indicators, no optimization of thresholds for historical profit, and no increase in leverage or risk limits are part of this work. Strategy performance must be measured, not assumed.

## Evidence standard

The repository must distinguish three states: `ENGINEERING_VALID`, `RESEARCH_VALIDATED`, and `LIVE_ELIGIBLE`. Engineering validity alone never implies profitability. Research validation must use held-out data and execution costs. Live eligibility additionally requires forward/shadow validation and current infrastructure certification.
