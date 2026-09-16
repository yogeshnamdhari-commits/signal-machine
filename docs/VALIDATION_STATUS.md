# Current Validation Status

**Status date:** 2026-09-16
**Status:** ENGINEERING_VALIDATION_IN_PROGRESS

## Certification policy

This repository distinguishes historical reports from current certification. Files describing audits or forward tests from earlier dates are research records and do not by themselves authorize live deployment.

Current certification must be tied to:

- the exact source commit;
- the effective configuration fingerprint;
- the dependency/build state;
- automated tests and integration checks;
- deterministic backtest evidence;
- current out-of-sample/forward-test evidence.

## Strategy evidence

The repository does not currently contain sufficient current statistical evidence to assert that the strategy is profitable or live-eligible. The gate requires at least 100 completed trades, non-overlapping train/validation/test windows, explicit fees/spread/slippage and funding treatment, regime coverage, reproducibility fingerprints, and complete rejected-signal outcome attribution.

Existing June/July 2026 validation material remains historical evidence and must not be read as a current profitability certification.

## Live eligibility

`LIVE_ELIGIBLE` is intentionally unavailable until the research evidence gate passes. Engineering hardening and deployment authorization are separate states.
