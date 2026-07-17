"""
EMA_V5 Environment Setup — Validates and configures the runtime environment.
Isolated from existing environment setup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5EnvSetup:
    """Validates and configures EMA_V5 runtime environment."""

    REQUIRED_PYTHON = (3, 10)
    REQUIRED_PACKAGES = [
        "numpy", "pandas", "loguru", "httpx", "openpyxl",
    ]

    def __init__(self) -> None:
        self._issues: List[str] = []
        self._warnings: List[str] = []

    def validate(self) -> Dict[str, Any]:
        """Validate the runtime environment."""
        self._issues = []
        self._warnings = []

        self._check_python_version()
        self._check_packages()
        self._check_directories()
        self._check_config()
        self._check_database()

        return {
            "healthy": len(self._issues) == 0,
            "issues": self._issues,
            "warnings": self._warnings,
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": sys.platform,
        }

    def _check_python_version(self) -> None:
        """Check Python version meets requirements."""
        current = (sys.version_info.major, sys.version_info.minor)
        if current < self.REQUIRED_PYTHON:
            self._issues.append(
                f"Python {self.REQUIRED_PYTHON[0]}.{self.REQUIRED_PYTHON[1]}+ required, "
                f"found {current[0]}.{current[1]}"
            )

    def _check_packages(self) -> None:
        """Check required packages are installed."""
        for package in self.REQUIRED_PACKAGES:
            try:
                __import__(package)
            except ImportError:
                self._issues.append(f"Missing package: {package}")

    def _check_directories(self) -> None:
        """Check required directories exist."""
        required_dirs = [
            "data/bridge",
            "data/logs",
        ]
        for dir_path in required_dirs:
            if not Path(dir_path).exists():
                self._warnings.append(f"Directory not found: {dir_path} (will be created)")

    def _check_config(self) -> None:
        """Check configuration is valid."""
        try:
            from ..config import ema_v5_config
            if ema_v5_config.confidence.min_confidence < 80:
                self._warnings.append("Confidence threshold below 80% — may generate low-quality signals")
            if ema_v5_config.trade.max_positions > 10:
                self._warnings.append("Max positions > 10 — may over-leverage")
        except Exception as e:
            self._issues.append(f"Config load failed: {e}")

    def _check_database(self) -> None:
        """Check database is accessible."""
        db_path = Path("data/ema_v5_signals.db")
        if db_path.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path), timeout=5)
                conn.execute("SELECT 1")
                conn.close()
            except Exception as e:
                self._issues.append(f"Database access failed: {e}")
        else:
            self._warnings.append("Database not found (will be created on first run)")

    def setup_directories(self) -> List[str]:
        """Create required directories."""
        dirs = [
            "data/bridge",
            "data/logs",
            "data/ema_v5_exports",
        ]
        created = []
        for dir_path in dirs:
            try:
                Path(dir_path).mkdir(parents=True, exist_ok=True)
                created.append(dir_path)
            except Exception as e:
                self._issues.append(f"Failed to create {dir_path}: {e}")
        return created

    def get_status(self) -> Dict[str, Any]:
        """Get environment status."""
        validation = self.validate()
        return {
            "healthy": validation["healthy"],
            "python_version": validation["python_version"],
            "platform": validation["platform"],
            "issues_count": len(validation["issues"]),
            "warnings_count": len(validation["warnings"]),
        }
