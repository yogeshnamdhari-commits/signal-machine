# REQUIREMENTS

## Functional
1. Preserve one canonical Python market-data/signal/risk authority.
2. Prevent synthetic or estimated market data from silently becoming executable real-market inputs.
3. Preserve explicit data-quality and provenance states.
4. Keep Node/TypeScript as an integration/API/UI layer.
5. Keep live execution fail-closed behind current certification and readiness evidence.
6. Keep backtesting causal, cost-aware, deterministic, and reproducible.
7. Maintain traceability from evidence to configuration and source commit.

## Non-functional
- Fail closed on missing, stale, mismatched, or contradictory evidence.
- No unreviewed changes to frozen/provenance artifacts.
- Use isolated workspaces/worktrees for concurrent agents where practical.
- CI must exercise the gates it claims to enforce.

## Constraints
- Do not add predictive indicators solely to improve backtest results.
- Do not loosen risk limits merely to improve apparent profitability.
- Do not manufacture missing market data.
- Do not promote historical reports to current certification.
- Do not push research branches directly to main.

## Acceptance criteria
- YPOS control files exist on a dedicated branch.
- Agent instructions enforce concurrency, frozen-artifact, push-safety, and evidence rules.
- Existing trading/integrity code is unchanged by this bootstrap.
- Actual validation commands and results are recorded before completion is claimed.

## Out of scope
- Strategy optimization.
- Live deployment.
- Re-freezing certification/provenance artifacts.
- Changing trading architecture or CI behavior.
