# Research Evidence Gate Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the research certification gate reject economically invalid evidence, not merely structurally complete evidence.

**Architecture:** Extend the typed research-evidence contract with explicit performance statistics and minimum acceptance thresholds. Keep the existing fail-closed live gate intact, and add focused regression coverage so negative expectancy/profit-factor evidence can never become research-certified.

**Tech Stack:** Python 3.11, dataclasses, pytest, existing AI-engine validation modules.

**Spec:** `docs/VALIDATION_STATUS.md`

## Global Constraints

- Never infer profitability from incomplete or historical-only records.
- `LIVE_ELIGIBLE` remains unavailable unless all existing evidence gates pass.
- Performance evidence must be tied to source commit and configuration fingerprint.
- Existing callers remain compatible where possible; defaults must fail closed for missing economic evidence.

---

### Task 1: Add failing regression tests

**Files:**
- Modify: `packages/ai-engine/tests/test_statistical_validation.py` (or the repository's existing validation-test location if a dedicated file is absent)

**Interfaces:**
- Consumes: `ResearchEvidence`, `evaluate_research_evidence`
- Produces: regression tests proving negative PF/expectancy is rejected

- [ ] **Step 1:** Locate the existing statistical-validation tests and current `ResearchEvidence` construction sites.
- [ ] **Step 2:** Add a complete-evidence fixture with positive trade count, disjoint periods, cost/funding, regime coverage, fingerprints, and positive metrics.
- [ ] **Step 3:** Add a test that a structurally complete but negative-PF evidence set is rejected.
- [ ] **Step 4:** Add a test that negative expectancy is rejected.
- [ ] **Step 5:** Run only the new tests and confirm they fail for the intended reason.

### Task 2: Strengthen the research evidence contract

**Files:**
- Modify: `packages/ai-engine/validation/statistical_validation.py`

**Interfaces:**
- Consumes: existing `ResearchEvidence`
- Produces: explicit economic metrics and rejection reasons

- [ ] **Step 1:** Add typed performance fields with conservative defaults that fail closed (`profit_factor`, `expectancy`, `max_drawdown_pct`, and a minimum completed-trade count already required by policy).
- [ ] **Step 2:** Add explicit minimum configurable thresholds for certification.
- [ ] **Step 3:** Reject missing/non-finite/non-positive economic evidence as appropriate.
- [ ] **Step 4:** Keep temporal non-overlap, cost-model, funding, regime, fingerprint, and rejected-outcome checks intact.
- [ ] **Step 5:** Run the focused regression tests until green.

### Task 3: Prevent false research certification

**Files:**
- Modify: `packages/ai-engine/validation/certification.py`
- Modify: any certification integration tests covering `generate_certification`

**Interfaces:**
- Consumes: validated research decision
- Produces: `RESEARCH_VALIDATED` only when economic evidence passes

- [ ] **Step 1:** Verify certification-state transitions against the research decision.
- [ ] **Step 2:** Add a regression test proving negative economic evidence cannot yield `RESEARCH_VALIDATED`.
- [ ] **Step 3:** Run the certification tests.

### Task 4: Full verification and promotion

**Files:**
- No additional production files unless tests expose a required integration correction.

- [ ] **Step 1:** Run the complete Python test suite.
- [ ] **Step 2:** Run the repository production validation gate locally where supported.
- [ ] **Step 3:** Commit the tested change to `dev`.
- [ ] **Step 4:** Verify the resulting GitHub Actions run completes successfully before reporting the change as passing.
- [ ] **Step 5:** Verify `main` is synchronized only through the existing CI promotion path.

## Acceptance Criteria

- Negative profit factor cannot be research-certified.
- Negative expectancy cannot be research-certified.
- Missing economic metrics fail closed.
- Existing structural research checks remain enforced.
- Python tests and production validation gate pass on the resulting commit.
- No `LIVE_ELIGIBLE` artifact is fabricated from existing losing evidence.
