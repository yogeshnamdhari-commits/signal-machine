"""
EMA_V5 Environment Configuration — Production environment setup.
Isolated from existing configuration systems.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


class EMAv5EnvConfig:
    """Production environment configuration for EMA_V5."""

    # Default configuration
    DEFAULTS = {
        "EMA_V5_ENV": "production",
        "EMA_V5_LOG_LEVEL": "INFO",
        "EMA_V5_TELEGRAM_ENABLED": "false",
        "EMA_V5_RISK_PER_TRADE": "1.0",
        "EMA_V5_MAX_POSITIONS": "3",
        "EMA_V5_MAX_DAILY_LOSS": "5.0",
        "EMA_V5_MAX_DRAWDOWN": "15.0",
        "EMA_V5_MIN_CONFIDENCE": "90.0",
        "EMA_V5_SL_ATR_MULT": "1.5",
        "EMA_V5_TP1_RR": "1.5",
        "EMA_V5_TP2_RR": "3.0",
        "EMA_V5_TP3_RR": "5.0",
        "EMA_V5_DASHBOARD_PORT": "8501",
        "EMA_V5_AUTO_REFRESH": "120",
    }

    def __init__(self) -> None:
        self._config: Dict[str, str] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from environment and .env file."""
        # Load from .env file
        env_file = Path(".env.ema_v5")
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        self._config[key.strip()] = value.strip()

        # Override with environment variables
        for key in self.DEFAULTS:
            env_value = os.environ.get(key)
            if env_value:
                self._config[key] = env_value

        # Apply defaults
        for key, default in self.DEFAULTS.items():
            if key not in self._config:
                self._config[key] = default

    def get(self, key: str, default: Optional[str] = None) -> str:
        """Get a configuration value."""
        return self._config.get(key, default or self.DEFAULTS.get(key, ""))

    def get_int(self, key: str, default: int = 0) -> int:
        """Get a configuration value as integer."""
        try:
            return int(self.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        """Get a configuration value as float."""
        try:
            return float(self.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a configuration value as boolean."""
        value = self.get(key, str(default)).lower()
        return value in ("true", "1", "yes", "on")

    def set(self, key: str, value: str) -> None:
        """Set a configuration value."""
        self._config[key] = value

    def save(self, filepath: str = ".env.ema_v5") -> None:
        """Save configuration to .env file."""
        with open(filepath, "w") as f:
            f.write("# EMA_V5 Environment Configuration\n")
            f.write(f"# Generated at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            for key, value in sorted(self._config.items()):
                f.write(f"{key}={value}\n")

        logger.info("EMAv5 config saved to {}", filepath)

    def get_all(self) -> Dict[str, str]:
        """Get all configuration values."""
        return dict(self._config)

    def get_summary(self) -> Dict[str, Any]:
        """Get configuration summary."""
        return {
            "environment": self.get("EMA_V5_ENV"),
            "log_level": self.get("EMA_V5_LOG_LEVEL"),
            "telegram_enabled": self.get_bool("EMA_V5_TELEGRAM_ENABLED"),
            "risk_per_trade": self.get_float("EMA_V5_RISK_PER_TRADE"),
            "max_positions": self.get_int("EMA_V5_MAX_POSITIONS"),
            "min_confidence": self.get_float("EMA_V5_MIN_CONFIDENCE"),
            "dashboard_port": self.get_int("EMA_V5_DASHBOARD_PORT"),
        }
