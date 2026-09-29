# DECISIONS

## DEC-20260929-001 — YPOS v3.2 repository bootstrap
Date: 2026-09-29
Decision: Add lean repository-level YPOS controls on a dedicated branch rather than modifying main directly.
Context: Standardize agent execution, validation, concurrency safety, and project state for future work.
Evidence: Baseline commit 1003b958849ebbc7e453fefad3946346f932b0dd and repository audit `docs/AUDIT_2026-09-16.md`.
Consequence: Adds agent/project-control files only; no trading logic or certification artifact changes.
Owner: Authorized project maintainer/agent

## Decision template
Date: [YYYY-MM-DD]
Decision: [WHAT]
Context: [WHY]
Evidence: [PATH/LINK/COMMAND]
Consequence: [WHAT CHANGES]
Owner: [PERSON/AGENT]
