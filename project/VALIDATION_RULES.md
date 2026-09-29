# VALIDATION RULES

## Decision states
PASS = mandatory acceptance criteria demonstrated with execution evidence.
FAIL = mandatory criterion failed.
BLOCKED = required execution/dependency is unavailable.
INCONCLUSIVE = evidence is missing or contradictory.
NO_DEPLOY = production/live gate is not satisfied.

## General
- No success claim without evidence.
- Record branch, commit, command, relevant configuration, and artifact paths for material validation.
- Validate changed behavior plus relevant regression paths.
- Prefer clean-checkout/clean-worktree validation for release evidence.

## Frozen artifact integrity
Do not modify frozen/provenance artifacts without a documented human decision. Preserve freeze identifier, frozen timestamp, source commit binding, and dependency/configuration provenance.

## Concurrency and commit gates
- Detect concurrent writers/processes before Git mutation.
- If a concurrent writer is active: no commit, push, merge, rebase, reset, or checkout of active files.
- After concurrency ends: classify every worktree change before staging.
- Use explicit paths; never use `git add -A` for controlled commits.
- Push only to the intended research branch after verifying unpublished commits and exact refs.

## Quantitative research
Do not call a strategy current/live-eligible from historical documentation alone. Research validation must separately address data integrity, temporal separation, realistic fees/spread/slippage/funding, execution assumptions, true OOS performance, regime coverage, drawdown, sensitivity, reproducibility, and uncertainty/robustness.

## Bootstrap validation
- Verify only intended control files changed.
- Verify no protected trading/certification files changed.
- Record final branch and commit SHA.
