"""
EMA_V5 Developer Guide — Developer documentation for contributors.
Isolated from existing documentation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5DeveloperGuide:
    """Generates developer guide documentation for EMA_V5."""

    def generate(self) -> Dict[str, Any]:
        """Generate complete developer guide."""
        return {
            "version": "1.0.0",
            "setup": self._setup(),
            "architecture": self._architecture(),
            "coding_standards": self._coding_standards(),
            "testing": self._testing(),
            "contributing": self._contributing(),
            "troubleshooting": self._troubleshooting(),
        }

    def _setup(self) -> Dict[str, Any]:
        """Setup section."""
        return {
            "title": "Development Setup",
            "requirements": [
                "Python 3.14+",
                "Node.js 18+",
                "PostgreSQL (for existing system)",
                "SQLite (for EMA_V5 isolated storage)",
            ],
            "installation": [
                "cd packages/ai-engine",
                "source ../.venv/bin/activate",
                "pip install -r requirements.txt",
            ],
            "running_tests": [
                "cd packages/ai-engine",
                "python -c 'from scanner.ema_v5.tests import EMAv5TestRunner; r=EMAv5TestRunner(); r.run_all()'",
            ],
            "running_stress_tests": [
                "cd packages/ai-engine",
                "python -c 'from scanner.ema_v5.stress import EMAv5StressReport; r=EMAv5StressReport(); r.run_all()'",
            ],
        }

    def _architecture(self) -> Dict[str, Any]:
        """Architecture section."""
        return {
            "title": "Architecture",
            "principles": [
                "Isolation: EMA_V5 never modifies existing systems",
                "Modularity: Each component is self-contained",
                "Idempotency: Same signal produces same UUID",
                "Auditability: Complete signal history",
                "Recovery: State survives engine restarts",
            ],
            "module_dependencies": {
                "scanner.py": "Depends on all sub-engines",
                "signal_engine.py": "Depends on config, utils",
                "state_manager.py": "Depends on config",
                "storage/": "Depends on nothing (standalone)",
                "analytics/": "Depends on storage",
                "verification/": "Depends on config, state_manager",
                "execution/": "Depends on storage",
                "telegram/": "Depends on httpx",
                "performance/": "Depends on storage, analytics",
                "reports/": "Depends on storage, analytics",
                "stress/": "Depends on all modules",
                "tests/": "Depends on all modules",
            },
            "data_flow": "Market Data → Scanner → Verification → Storage → Dashboard",
        }

    def _coding_standards(self) -> Dict[str, Any]:
        """Coding standards section."""
        return {
            "title": "Coding Standards",
            "python": [
                "Type hints for all function signatures",
                "Docstrings for all public methods",
                "Maximum line length: 100 characters",
                "Use dataclasses for structured data",
                "Use pathlib for file paths",
            ],
            "naming": [
                "Classes: PascalCase (EMAv5Scanner)",
                "Functions: snake_case (get_bridge_data)",
                "Constants: UPPER_SNAKE_CASE (NO_TREND)",
                "Private: underscore prefix (_internal)",
            ],
            "error_handling": [
                "Always use try/except for external operations",
                "Log errors with logger.error()",
                "Return sensible defaults on failure",
                "Never swallow exceptions silently",
            ],
            "testing": [
                "Write tests for all new functionality",
                "Use temp files for database tests",
                "Clean up test artifacts",
                "Test both success and failure paths",
            ],
        }

    def _testing(self) -> Dict[str, Any]:
        """Testing section."""
        return {
            "title": "Testing",
            "test_suites": {
                "unit": "12 tests for individual modules",
                "integration": "7 tests for module interactions",
                "e2e": "6 tests for complete workflows",
                "regression": "7 tests for no regressions",
            },
            "running_tests": {
                "all": "EMAv5TestRunner().run_all()",
                "unit": "EMAv5UnitTests().run_all()",
                "integration": "EMAv5IntegrationTests().run_all()",
                "e2e": "EMAv5E2ETests().run_all()",
                "regression": "EMAv5RegressionTests().run_all()",
                "quick": "EMAv5TestRunner().quick_check()",
            },
            "writing_tests": [
                "Place tests in scanner/ema_v5/tests/",
                "Name test methods with _test_ prefix",
                "Use try/except to catch and record failures",
                "Return detailed failure messages",
            ],
        }

    def _contributing(self) -> Dict[str, Any]:
        """Contributing section."""
        return {
            "title": "Contributing",
            "guidelines": [
                "Never modify existing systems",
                "Always add tests for new functionality",
                "Follow coding standards",
                "Update documentation",
                "Run full test suite before submitting",
            ],
            "adding_a_new_module": [
                "1. Create module in appropriate package",
                "2. Add import to package __init__.py",
                "3. Write unit tests",
                "4. Write integration tests",
                "5. Update API documentation",
                "6. Run full test suite",
            ],
            "adding_a_new_check": [
                "1. Add check method to verifier.py",
                "2. Add to verify() method",
                "3. Add to CHECK_WEIGHTS in quality.py",
                "4. Write tests for new check",
                "5. Update verification documentation",
            ],
        }

    def _troubleshooting(self) -> Dict[str, Any]:
        """Troubleshooting section."""
        return {
            "title": "Troubleshooting",
            "common_issues": {
                "no_signals": {
                    "cause": "Market conditions don't meet strategy requirements",
                    "solution": "Check regime, pullback, and confidence thresholds",
                },
                "low_win_rate": {
                    "cause": "Market conditions have changed",
                    "solution": "Review analytics, consider parameter adjustment",
                },
                "state_stuck": {
                    "cause": "State machine transition failed",
                    "solution": "Check state_manager.py transitions, reset if needed",
                },
                "storage_error": {
                    "cause": "Database locked or corrupted",
                    "solution": "Check file permissions, use recovery module",
                },
                "telegram_not_sending": {
                    "cause": "Bot token or chat_id missing",
                    "solution": "Configure EMAv5TelegramConfig with valid credentials",
                },
            },
            "debugging": [
                "Enable DEBUG logging: logger.level('DEBUG')",
                "Check data/bridge/ema_v5.json for bridge state",
                "Check data/ema_v5_state.json for state machine",
                "Check data/ema_v5_signals.db for stored signals",
                "Run verification diagnostics for signal analysis",
            ],
        }
