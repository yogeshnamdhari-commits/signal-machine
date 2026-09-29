#!/usr/bin/env bash
set -euo pipefail
printf '=== YPOS COMPLETION CHECK ===\n'
printf 'Branch: '; git branch --show-current || true
printf 'Commit: '; git rev-parse --short HEAD || true
printf '\nWorking tree:\n'; git status --short || true
printf '\nProject status:\n'; sed -n '1,160p' project/PROJECT_STATUS.md 2>/dev/null || true
printf '\nNext: run the project-specific build/test/validation commands required by REQUIREMENTS.md and VALIDATION_RULES.md.\n'
