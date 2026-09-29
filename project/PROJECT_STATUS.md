# PROJECT STATUS

## Project
Name: Signal Machine / DeltaTerminal
Purpose: Provenance-aware Binance research and signal platform with fail-closed validation.

## Current state
Status: ACTIVE
Base branch: main
Baseline commit: 1003b958849ebbc7e453fefad3946346f932b0dd
Current objective: Establish durable project-control documents and agent operating rules without changing trading logic.

## Architecture invariants
- Python remains the canonical executable market-data/signal/risk authority.
- Node/TypeScript remains an integration/API/UI layer.
- Real-market signal inputs remain provenance-aware and non-synthetic.
- Historical validation is evidence, not current certification.
- Live execution remains fail-closed until current qualifying evidence exists.

## Protected
- Certification/provenance/evidence artifacts whose hashes or source-commit bindings establish a freeze.
- Production/live-readiness gates.
- Historical validation records unless explicitly superseded through a documented decision.

## Active work
- YPOS v3.2 repository bootstrap.
- Preserve existing CI and integrity controls.

## Latest known validation
- Integrity audit: `docs/AUDIT_2026-09-16.md`.
- Engineering integrity controls are documented as implemented.
- Current profitability evidence is not established by the historical audit.
- CI success is not claimed here without a fresh authoritative run.

## Open blockers
- Fresh reproducible research evidence is required before any current profitability/live-readiness claim.
- Current CI/certification state must be verified from actual execution before release claims.

## Next action
Run repository-specific audit/validation from a clean checkout and replace this bootstrap status with measured results.
