# Multi-Engine Signal Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the nine approved integrity gates so that the repository has one provenance-aware signal authority, a correct deterministic backtester, correct Delta environments, comprehensive CI, and evidence-based deployment certification.

**Architecture:** Python is the canonical market-data/signal/risk/execution authority. Node is reduced to transport/API/UI integration and consumes canonical outputs. Provenance, configuration, execution simulation, and certification are explicit typed boundaries enforced by tests.

**Tech Stack:** Python 3.11, pytest, pydantic, pandas/numpy, Node/TypeScript, React/Vite, GitHub Actions, Binance/Delta adapters.

**Spec:** `docs/superpowers/specs/2026-09-16-multi-engine-integrity-design.md`

## Global Constraints

- Do not add predictive indicators solely to improve backtest results.
- Do not loosen risk limits to improve apparent profitability.
- Synthetic/estimated market data must never enter real-market signal inputs without an explicit synthetic-data contract.
- Python is the only executable signal/risk authority.
- Main synchronization must fail on any required gate failure.
- Historical reports are evidence records, not current certification.
- Backtests must be deterministic for identical inputs and seeds.
- Production/testnet Delta endpoints and credentials must never be interchangeable.

---

### Task 1: Canonical market-data provenance layer

**Files:**
- Create: `packages/ai-engine/core/provenance.py`
- Create: `packages/ai-engine/core/market_data.py`
- Create: `packages/ai-engine/tests/test_provenance.py`
- Modify: `packages/ai-engine/exchanges/binance_ws.py`
- Modify: `packages/ai-engine/core/engine.py`

**Interfaces:**
- `MarketObservation(source: str, feed: str, symbol: str, event_ts: float, received_ts: float, sequence: int|None, synthetic: bool, quality: str, payload: dict)`.
- `validate_observation(obs, now_ts, max_age_s) -> None` raises on stale, malformed, synthetic-for-real, or invalid timestamps.
- `RealMarketEvent` and `SyntheticMarketEvent` must be structurally distinguishable.

- [ ] Write tests for valid real observations, stale observations, future timestamps, synthetic contamination, and source/feed identity.
- [ ] Run `pytest packages/ai-engine/tests/test_provenance.py -q` and verify new tests fail before implementation.
- [ ] Implement the canonical models and validators.
- [ ] Change Binance WS callbacks to emit typed real events only from real trade/book/mark/OI messages.
- [ ] Remove ticker-array conversion into synthetic trade callbacks; 24h ticker data must remain ticker data.
- [ ] Route accepted observations through the quality/provenance gate before feature engines receive them.
- [ ] Run focused tests and the Python compile check.
- [ ] Commit `feat: enforce market data provenance`

### Task 2: Remove synthetic-data contamination from orderflow and backend fallbacks

**Files:**
- Modify: `packages/ai-engine/scanner/*orderflow*`
- Modify: `packages/backend/src/routes/*`
- Create: `packages/ai-engine/tests/test_data_integrity_paths.py`

**Interfaces:**
- `DataQuality.REAL`, `DataQuality.ESTIMATED`, `DataQuality.SYNTHETIC`.
- Signal engines accept `REAL` by default and require an explicit opt-in for non-real data.

- [ ] Add failing tests proving estimated 50/50 orderflow cannot reach executable signal scoring.
- [ ] Replace silent fallbacks with explicit estimated records.
- [ ] Ensure API/UI can display estimated data without exposing it as authoritative orderflow.
- [ ] Add tests covering backend fallback behavior and Python engine rejection.
- [ ] Run focused integrity tests.
- [ ] Commit `fix: isolate estimated market data`

### Task 3: Canonical configuration and environment contract

**Files:**
- Modify: `packages/ai-engine/config/settings.py`
- Create: `packages/ai-engine/config/schema.py`
- Create: `packages/ai-engine/config/environment_contract.py`
- Create: `packages/ai-engine/tests/test_config_contract.py`

**Interfaces:**
- `ConfigFingerprint`: immutable SHA-256 fingerprint of normalized effective config.
- `validate_environment_contract(config) -> ConfigFingerprint`.
- Distinct Delta production/testnet REST and public/private WS endpoints.

- [ ] Add failing tests for wrong Delta testnet endpoints, missing credentials, conflicting environment flags, and risk-threshold drift.
- [ ] Implement typed environment validation and normalized config fingerprinting.
- [ ] Correct Delta testnet endpoints to the documented testnet REST/private/public WS endpoints.
- [ ] Record the fingerprint at engine startup and in signal/certification metadata.
- [ ] Reconcile risk quality thresholds so code and configuration use one source of truth; reject zero-size approval.
- [ ] Run focused config tests.
- [ ] Commit `fix: enforce configuration and exchange environments`

### Task 4: Single Python trading authority; Node as integration layer

**Files:**
- Modify: `packages/backend/src/index.ts`
- Modify: `packages/backend/src/routes/*`
- Modify: `packages/backend/src/services/*`
- Create: `packages/backend/src/contracts/canonicalSignal.ts`
- Create: `packages/ai-engine/tests/test_signal_authority.py`

**Interfaces:**
- Canonical signal payload contains signal ID, symbol, decision, score, engine versions, provenance summary, config fingerprint, risk decision, timestamp.
- Node routes may query/relay canonical signals but cannot synthesize executable BUY/SELL decisions.

- [ ] Add failing tests demonstrating Node-only signal generation is not authoritative.
- [ ] Introduce a canonical signal contract and adapter for Python outputs.
- [ ] Remove/disable independent Node signal-engine and trading-simulator decision paths from production authority.
- [ ] Preserve UI endpoints by returning canonical Python signal/risk state.
- [ ] Add an integration test for one signal flowing Python -> backend -> API without recomputation.
- [ ] Run TypeScript tests/build and Python authority tests.
- [ ] Commit `refactor: establish canonical signal authority`

### Task 5: Backtester correctness and reproducibility

**Files:**
- Modify: `packages/ai-engine/backtesting/backtester.py`
- Create: `packages/ai-engine/backtesting/execution_model.py`
- Create: `packages/ai-engine/tests/test_backtester_correctness.py`

**Interfaces:**
- Strategy callback receives only information available through the current event/bar.
- `ExecutionModel` applies latency, spread, slippage, fee, funding, partial-fill, and fill rules consistently.
- `BacktestResult` final equity equals realized capital after all forced closes and costs.

- [ ] Add failing tests for future-data access, latency, funding in net PnL, trailing-stop exit timestamps, deterministic randomness, final equity, and same-bar SL/TP conventions.
- [ ] Pass a causally bounded view to strategy callbacks.
- [ ] Implement a seeded RNG object rather than global random calls.
- [ ] Replay funding from supplied historical observations when available and record explicit fallback assumptions otherwise.
- [ ] Apply all costs consistently to both account equity and trade-level net PnL.
- [ ] Record the actual exit timestamp for every close path.
- [ ] Reconcile final equity after forced liquidation of open positions.
- [ ] Make same-bar SL/TP handling explicit and configurable, with a conservative default documented in tests.
- [ ] Run the entire backtesting test set twice with identical seed/input and compare byte-stable result summaries.
- [ ] Commit `fix: make backtesting causal and reproducible`

### Task 6: Current certification and historical-report governance

**Files:**
- Create: `packages/ai-engine/validation/certification.py`
- Create: `packages/ai-engine/tests/test_certification.py`
- Create: `docs/VALIDATION_STATUS.md`
- Modify: historical validation headers as needed to mark them historical

**Interfaces:**
- Certification states: `ENGINEERING_VALID`, `RESEARCH_VALIDATED`, `LIVE_ELIGIBLE`.
- `generate_certification(evidence) -> CertificationArtifact`.

- [ ] Add failing tests for expired evidence, mismatched commit/config fingerprints, and historical reports being accepted as current evidence.
- [ ] Implement machine-checkable evidence manifests tied to commit SHA and config fingerprint.
- [ ] Mark existing June/July validation/audit reports explicitly as historical records.
- [ ] Generate current status from test/build/backtest evidence rather than static markdown claims.
- [ ] Ensure live mode refuses stale or mismatched certification.
- [ ] Run certification tests.
- [ ] Commit `feat: add current certification gate`

### Task 7: Full monorepo CI gate

**Files:**
- Modify: `.github/workflows/auto-sync-dev-to-main.yml`
- Create: `.github/workflows/ci.yml`
- Create: `packages/ai-engine/tests/test_repo_contract.py`
- Modify: `packages/backend/package.json`
- Modify: `packages/frontend/package.json`
- Modify: `.gitignore`

**Interfaces:**
- CI jobs: Python compile/test, Node install/test/build, frontend install/lint/build, config/exchange contract, backtester correctness, certification gate.
- `package-lock.json` files are tracked for deterministic JS dependency installation.

- [ ] Add failing CI contract tests for absent Python API dependencies, ignored lockfiles, and missing build/test gates.
- [ ] Add required Python runtime dependencies for documented API mode and pin/lock JS dependency resolution.
- [ ] Remove lockfiles from ignore rules and commit generated lockfiles.
- [ ] Build Node backend and frontend in CI.
- [ ] Run Python tests and repository contract tests.
- [ ] Make auto-sync invoke the full CI workflow or duplicate its required checks before fast-forwarding dev to main.
- [ ] Add a final certification/check-summary job that fails closed.
- [ ] Run CI locally where possible and validate workflow YAML semantics.
- [ ] Commit `ci: make monorepo validation mandatory`

### Task 8: Statistical research and profitability evidence pipeline

**Files:**
- Create: `packages/ai-engine/validation/statistical_validation.py`
- Create: `packages/ai-engine/tests/test_statistical_validation.py`
- Create: `docs/RESEARCH_GATES.md`

**Interfaces:**
- `ResearchEvidence` records sample size, train/validation/test periods, regime coverage, costs, funding treatment, rejected-candidate outcomes, seed, and code/config fingerprints.
- `evaluate_research_evidence(evidence) -> ResearchDecision`.

- [ ] Add tests for insufficient sample, overlapping train/test periods, missing costs, missing regime coverage, and missing rejected-signal outcomes.
- [ ] Implement minimum-sample and temporal-separation gates.
- [ ] Implement cost-sensitivity reporting and uncertainty metrics.
- [ ] Implement rejected-signal outcome attribution with TP/SL/MFE/MAE/holding-time fields.
- [ ] Add walk-forward/held-out evaluation hooks and purging/embargo metadata so validation windows cannot overlap improperly.
- [ ] Ensure current repository documentation reports strategy edge as unproven until new evidence passes the gate; do not manufacture profitability.
- [ ] Run statistical-validation tests.
- [ ] Commit `research: formalize profitability evidence gates`

### Task 9: End-to-end live-readiness gate

**Files:**
- Create: `packages/ai-engine/validation/live_readiness.py`
- Create: `packages/ai-engine/tests/test_live_readiness.py`
- Modify: `packages/ai-engine/main.py`
- Modify: `README.md`

**Interfaces:**
- `evaluate_live_readiness() -> LiveReadinessReport` checks all previous gates and returns a fail-closed decision.

- [ ] Add failing tests for each of the nine failure classes.
- [ ] Implement the aggregate gate with explicit failed-gate reasons.
- [ ] Make `main.py --mode engine/api/live` refuse live execution unless readiness passes.
- [ ] Update README to distinguish architecture readiness from strategy profitability validation.
- [ ] Run the complete Python suite and all available JS builds/tests.
- [ ] Commit `feat: enforce end-to-end live readiness`

### Task 10: Final repository audit and evidence capture

**Files:**
- Create: `docs/AUDIT_2026-09-16.md`
- Create: `docs/evidence/README.md`

- [ ] Run all required checks from a clean checkout state available to CI.
- [ ] Record commit SHA, configuration fingerprint, dependency lock state, test counts, build results, and research-status state.
- [ ] Confirm no synthetic market event reaches real-data signal engines through integration tests.
- [ ] Confirm Node cannot produce an independent executable trading decision.
- [ ] Confirm Delta production/testnet contracts pass.
- [ ] Confirm current statistical evidence status is reported honestly.
- [ ] Commit `docs: publish final integrity audit`
