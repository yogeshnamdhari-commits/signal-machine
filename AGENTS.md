# Signal Machine — Agent Instructions

Complete authorized work end-to-end with evidence: inspect → plan → implement → build/test → verify → review → document.

Use `project/PROJECT_STATUS.md` for current state, `project/REQUIREMENTS.md` for acceptance criteria, `project/VALIDATION_RULES.md` for gates, `project/DECISIONS.md` for material decisions, `project/KNOWN_ISSUES.md` for known defects, and `project/EXPERIMENTS.md` for research history. Inspect only files relevant to the current task and widen investigation when dependencies or failures require it.

Do not stop at implementation or compilation. A task is complete only when its acceptance criteria are demonstrated by execution evidence. Use PASS / FAIL / BLOCKED / INCONCLUSIVE / NO_DEPLOY.

Before Git mutation, check for concurrent writers. If another writer is active, do not commit, push, merge, rebase, reset, or checkout files they may be changing. After the writer is idle, classify all changes before staging. Use explicit paths; never use `git add -A` for controlled commits.

Never modify frozen, certification, provenance, evidence, or release-gate artifacts without an explicit documented human decision. Preserve hashes, timestamps, source-commit bindings, and dependency provenance.

Never push a research branch directly to `main`. Before pushing, verify remote, target branch, unpublished commits, exact commits, working-tree state, and protected/frozen artifacts. Measure unpublished work with `git rev-list --count HEAD --not --remotes=<remote>`.

For quantitative research, separately validate data provenance, look-ahead/leakage, overfitting, execution realism, fees/spread/slippage/funding, temporal separation, true OOS, regime stability, drawdown, sensitivity, reproducibility, and rejected-signal outcomes. Do not convert engineering success or historical reports into current profitability/live-readiness claims.

Repository architecture: Python is the canonical executable market-data/signal/risk authority. Node/TypeScript is an integration/API/UI layer and must not become an independent executable trading authority. Synthetic/estimated market data must not silently enter real-market signal inputs.

Final report for substantial work:
STATUS:
OBJECTIVE:
CHANGES:
TESTS RUN:
EVIDENCE:
RESULTS:
RISKS:
BLOCKERS:
NEXT ACTION:
