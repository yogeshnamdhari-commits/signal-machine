# Production Data and Signal Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live scanner/dashboard production-data truthful, provenance-aware, directionally explicit, and incapable of inventing executable BUY/SELL signals from missing data.

**Architecture:** Exchange adapters remain the sole source of raw market observations. A typed provenance layer labels every observation as LIVE/CALCULATED/STALE/UNAVAILABLE/NOT_APPLICABLE. The Python signal engine is the sole executable signal authority; dashboards may display derived diagnostics but may not manufacture executable signals. Every displayed factor exposes BUY, SELL, or NEUTRAL semantics plus data-quality state and evidence timestamps.

**Tech Stack:** Python 3.11, FastAPI/uvicorn, asyncio, aiohttp, websockets, pandas, Streamlit, TypeScript/Node 20, Jest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-16-multi-engine-integrity-design.md`

## Global Constraints

- No synthetic trade, CVD, delta, order-flow, OI, funding, sweep, or signal values.
- Missing data is explicit (`UNAVAILABLE`, `STALE`, or `NOT_APPLICABLE`), never silently zero-filled for directional decisions.
- Only the Python canonical engine can emit executable BUY/SELL signals.
- Dashboard-derived signals are diagnostic only and cannot populate the canonical signal field.
- Every directional factor maps to BUY, SELL, or NEUTRAL with a machine-readable reason.
- Every live observation carries source/feed/timestamp/data-quality provenance.
- Live trading remains fail-closed until the current certification artifact matches the source/config fingerprint.
- CI must pass Python tests, Node tests/build, frontend build, and the validation gate before `main` is advanced.

---

### Task 1: Exchange endpoint and stream contract hardening

**Files:**
- Modify: `packages/ai-engine/config/settings.py`
- Modify: `packages/ai-engine/config/environment_contract.py`
- Modify: `packages/ai-engine/config/runtime_defaults.py`
- Modify: `packages/ai-engine/exchanges/binance_ws.py`
- Add/Modify: `packages/ai-engine/tests/test_feed_contract.py`

**Interfaces:**
- `DeltaConfig.ws_testnet` and `DeltaConfig.rest_testnet` must be the documented Indian Delta testnet endpoints.
- `ScannerConfig.ws_streams` must include real L2 depth coverage while staying inside the Binance stream budget.
- Feed observations must preserve `source`, `feed`, `timestamp`, and explicit data quality.

- [ ] **Step 1: Write failing tests for endpoint separation and L2 subscription.**
- [ ] **Step 2: Run the focused tests and confirm they fail on the current settings.**
- [ ] **Step 3: Correct Delta production/testnet endpoints and enable real depth feed without synthetic fallbacks.**
- [ ] **Step 4: Add a stream-budget validator that rejects configurations exceeding the exchange limit.**
- [ ] **Step 5: Run focused feed-contract tests and the validation gate.**
- [ ] **Step 6: Commit the feed-contract changes.**

### Task 2: Canonical directional evidence contract

**Files:**
- Modify: `packages/ai-engine/core/provenance.py`
- Modify: `packages/ai-engine/core/market_data.py`
- Modify: `packages/ai-engine/core/signal_contract.py`
- Modify: `packages/ai-engine/core/signal_consensus.py`
- Add: `packages/ai-engine/core/directional_factor.py`
- Add: `packages/ai-engine/tests/test_directional_factor_contract.py`

**Interfaces:**
- `DirectionalFactor(name, state, score, reason, source, observed_at, quality)` where `state` is `BUY`, `SELL`, or `NEUTRAL`.
- `SignalEvidence` must retain factor-level direction, quality, age, source/feed, and supporting/contradicting factors.
- Consensus must exclude unavailable/stale/non-applicable factors from valid evidence counts rather than treating them as neutral votes.

- [ ] **Step 1: Write failing tests covering BUY/SELL/NEUTRAL mapping and missing-data exclusion.**
- [ ] **Step 2: Run the focused tests and verify failures.**
- [ ] **Step 3: Implement the typed factor contract and validation rules.**
- [ ] **Step 4: Update signal consensus to use only valid evidence.**
- [ ] **Step 5: Run focused tests plus existing provenance/consensus tests.**
- [ ] **Step 6: Commit the directional evidence contract.**

### Task 3: Remove dashboard-implied executable signals

**Files:**
- Modify: `packages/ai-engine/dashboard/pages/1_Live_Sheet.py`
- Modify: `packages/ai-engine/dashboard/app.py`
- Modify: dashboard data bridge modules identified by tests/search
- Add: `packages/ai-engine/tests/test_dashboard_signal_authenticity.py`

**Interfaces:**
- Canonical `Signal` column accepts only Python-engine `BUY`, `SELL`, or `NO_SIGNAL`.
- Diagnostic columns may show `derived_bias` or `diagnostic_consensus`, but these must not be labeled or rendered as executable signals.
- Empty event fields render explicit status text rather than empty strings.

- [ ] **Step 1: Write failing tests asserting no `LONG (implied)` or equivalent derived executable signal is emitted.**
- [ ] **Step 2: Run the dashboard-authenticity tests and capture the current failure.**
- [ ] **Step 3: Remove `_compute_implied_signal` from the canonical signal path and introduce explicit diagnostic-only output.**
- [ ] **Step 4: Replace blank event displays with `UNAVAILABLE`, `STALE`, `NONE`, or `NOT_APPLICABLE` as appropriate.**
- [ ] **Step 5: Run dashboard tests and render-path checks.**
- [ ] **Step 6: Commit the dashboard authenticity changes.**

### Task 4: Define and expose BUY/SELL meaning for every parameter

**Files:**
- Add: `packages/ai-engine/core/parameter_semantics.py`
- Modify: `packages/ai-engine/dashboard/pages/1_Live_Sheet.py`
- Modify: `packages/ai-engine/dashboard/app.py`
- Add: `packages/ai-engine/tests/test_parameter_semantics.py`

**Interfaces:**
- `evaluate_parameter(name, observation) -> DirectionalFactor | NonDirectionalMetric`.
- Directional parameters must return explicit BUY/SELL/NEUTRAL and reason text.
- Location/risk parameters such as sweep price, FVG price, regime confidence, and liquidation risk remain contextual and cannot become standalone trade votes.

- [ ] **Step 1: Write failing tests for price, OI, funding, B/S ratio, delta, CVD, flow, imbalance, sweep, FVG, regime and liquidity semantics.**
- [ ] **Step 2: Run focused semantic tests and confirm failures.**
- [ ] **Step 3: Implement deterministic parameter mappings with thresholds sourced from existing engine configuration.**
- [ ] **Step 4: Add human-readable reason strings and machine-readable evidence codes.**
- [ ] **Step 5: Run semantic tests and regression tests.**
- [ ] **Step 6: Commit parameter semantics.**

### Task 5: Freshness and stale-data gate

**Files:**
- Modify: `packages/ai-engine/core/market_data.py`
- Modify: `packages/ai-engine/execution/risk_integrity.py`
- Modify: `packages/ai-engine/validation/live_readiness.py`
- Add: `packages/ai-engine/tests/test_data_freshness_gate.py`

**Interfaces:**
- `classify_age(observed_at, now, max_age_sec) -> LIVE|STALE`.
- Signal admission must reject critical inputs that are stale or unavailable.
- Risk sizing must never proceed from zero/unknown market values.

- [ ] **Step 1: Write failing tests for fresh, stale, missing, and clock-skewed observations.**
- [ ] **Step 2: Run freshness tests and confirm failure.**
- [ ] **Step 3: Implement age classification and fail-closed admission.**
- [ ] **Step 4: Add diagnostics showing which required factor caused a rejection.**
- [ ] **Step 5: Run freshness and risk-integrity tests.**
- [ ] **Step 6: Commit the freshness gate.**

### Task 6: Fix main runtime mode and configuration initialization

**Files:**
- Modify: `packages/ai-engine/main.py`
- Modify: `packages/ai-engine/config/settings.py`
- Add/Modify: `packages/ai-engine/tests/test_main_runtime_contract.py`

**Interfaces:**
- CLI/environment selection must happen before the immutable config singleton is constructed.
- API mode must not autoreload in production.
- `live` mode must verify the current certification/config fingerprint and fail closed when absent or stale.

- [ ] **Step 1: Write failing tests for `--testnet`, API reload behavior, and live certification.**
- [ ] **Step 2: Run focused runtime tests and confirm failure.**
- [ ] **Step 3: Reorder config initialization and add an explicit fail-closed live path.**
- [ ] **Step 4: Validate the runtime contract and fingerprint propagation.**
- [ ] **Step 5: Run runtime tests.**
- [ ] **Step 6: Commit the runtime hardening.**

### Task 7: Node API read-only boundary and truthfulness

**Files:**
- Modify: `packages/backend/src/index.ts`
- Modify: `packages/backend/src/routes/index.ts`
- Add/Modify: `packages/backend/src/__tests__/canonical-authority.test.ts`

**Interfaces:**
- Node remains read-only/relay-only for executable trading decisions.
- All mutation/scan/sizing endpoints either return canonical-authority `409` responses or are removed.
- Node orderflow remains unavailable when real aggTrades are unavailable.

- [ ] **Step 1: Write failing tests covering every executable-looking Node route.**
- [ ] **Step 2: Run Jest and confirm failures.**
- [ ] **Step 3: Enforce route-level canonical-authority responses.**
- [ ] **Step 4: Verify orderflow never fabricates CVD/delta.**
- [ ] **Step 5: Run Node tests and build.**
- [ ] **Step 6: Commit Node authority hardening.**

### Task 8: CI/validation gate correctness

**Files:**
- Modify: `packages/ai-engine/validation_gate.py`
- Modify: `.github/workflows/auto-sync-dev-to-main.yml`
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Verify: `packages/backend/package-lock.json`, `packages/frontend/package-lock.json`

**Interfaces:**
- Validation gate must inspect the actual Node route file, not only `src/index.ts`.
- CI must run Python tests, Node tests/build, frontend build, compile checks, and validation gate.
- Lockfiles must not be ignored or silently absent when deterministic installation is required.

- [ ] **Step 1: Write failing validation checks for route-file location, required dependencies, and lockfiles.**
- [ ] **Step 2: Run the validation gate and record each failure.**
- [ ] **Step 3: Correct validation paths and dependency/lockfile enforcement.**
- [ ] **Step 4: Run the complete local-equivalent gate sequence.**
- [ ] **Step 5: Commit CI/gate corrections.**

### Task 9: End-to-end production-data audit and certification status

**Files:**
- Modify: `docs/AUDIT_2026-09-16.md`
- Modify: `docs/HISTORICAL_VALIDATION_POLICY.md`
- Add: `docs/PRODUCTION_DATA_CONTRACT.md`
- Add: `docs/DASHBOARD_PARAMETER_DICTIONARY.md`

**Interfaces:**
- Documentation must distinguish current live-data integrity from historical strategy-performance evidence.
- No document may claim profitability, win rate, or production readiness without current empirical evidence matching the current config/source fingerprint.

- [ ] **Step 1: Run all Python, Node, frontend, and validation tests.**
- [ ] **Step 2: Inspect the resulting data/signal contract and commit evidence.**
- [ ] **Step 3: Document exact PASS/FAIL/BLOCKED states for the nine integrity gates.**
- [ ] **Step 4: Confirm `main` advances only after the final CI run is green.**
- [ ] **Step 5: Perform the final branch/reference verification.**
- [ ] **Step 6: Commit the final audit documentation.**
