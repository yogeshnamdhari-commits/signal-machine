"""
Champion–Challenger Framework — Maintain two configurations.

Per Priority C:
    Champion: Current production settings.
    Challenger: Learning proposes improvements.
    Only replace Champion if Challenger proves better over a statistically
    meaningful sample. This avoids performance drift.

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_CONFIG_DIR = Path(__file__).resolve().parent.parent / "data" / "app_layer_configs"


@dataclass
class Configuration:
    """A named configuration set."""
    name: str = ""  # "champion" or "challenger"
    version: str = "1.0.0"
    parameters: Dict[str, float] = field(default_factory=dict)
    created_at: float = 0.0
    last_modified: float = 0.0
    is_active: bool = False

    # Performance tracking
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "version": self.version,
            "parameters": self.parameters,
            "created_at": self.created_at,
            "is_active": self.is_active,
            "total_trades": self.total_trades,
            "win_rate": round(self.winning_trades / self.total_trades, 3) if self.total_trades > 0 else 0,
            "total_pnl": round(self.total_pnl, 2),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
        }


class ChampionChallenger:
    """
    Maintains two configurations and manages promotion.

    Per Priority C: Champion/Challenger with statistical promotion.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        self._config_dir = config_dir or _CONFIG_DIR
        self._config_dir.mkdir(parents=True, exist_ok=True)

        self._champion = Configuration(name="champion", is_active=True)
        self._challenger = Configuration(name="challenger", is_active=False)
        self._history: List[Dict] = []

        self._load_configurations()

    @property
    def champion(self) -> Configuration:
        """Get champion configuration."""
        return self._champion

    @property
    def challenger(self) -> Configuration:
        """Get challenger configuration."""
        return self._challenger

    def get_active_config(self) -> Configuration:
        """Get the currently active configuration."""
        return self._champion if self._champion.is_active else self._challenger

    def update_challenger_parameter(
        self,
        parameter_name: str,
        value: float,
    ) -> None:
        """Update a challenger parameter."""
        self._challenger.parameters[parameter_name] = value
        self._challenger.last_modified = time.time()

    def record_trade(self, pnl: float, realized_r: float, is_champion: bool = True) -> None:
        """Record a trade for the appropriate configuration."""
        config = self._champion if is_champion else self._challenger
        config.total_trades += 1
        if pnl > 0:
            config.winning_trades += 1
        config.total_pnl += pnl

        # Update profit factor
        wins = config.winning_trades
        losses = config.total_trades - wins
        avg_win = config.total_pnl / config.total_trades if config.total_trades > 0 else 0
        config.profit_factor = (wins * abs(avg_win)) / max(1, losses * abs(avg_win)) if avg_win != 0 else 0

    def should_promote(self) -> tuple[bool, str]:
        """
        Check if challenger should replace champion.

        Returns:
            Tuple of (should_promote, reason)
        """
        # Minimum trades for comparison
        MIN_TRADES = 50

        if self._challenger.total_trades < MIN_TRADES:
            return False, f"challenger has {self._challenger.total_trades} trades < {MIN_TRADES} minimum"

        if self._champion.total_trades < MIN_TRADES:
            # Champion has insufficient data — promote if challenger is better
            if self._challenger.profit_factor > self._champion.profit_factor:
                return True, "champion has insufficient data, challenger is better"
            return False, "champion has insufficient data, challenger is not better"

        # Compare profit factor
        pf_improvement = (
            (self._challenger.profit_factor - self._champion.profit_factor)
            / max(self._champion.profit_factor, 0.01) * 100
        )

        # Compare expectancy
        ev_improvement = self._challenger.expectancy_r - self._champion.expectancy_r

        # Require meaningful improvement (10%+ PF improvement AND positive EV)
        if pf_improvement > 10 and self._challenger.expectancy_r > self._champion.expectancy_r:
            return True, (
                f"challenger PF improved by {pf_improvement:.1f}% "
                f"(EV: {self._challenger.expectancy_r:.3f}R vs {self._champion.expectancy_r:.3f}R)"
            )

        # Reject if challenger is worse
        if pf_improvement < -10:
            return False, f"challenger PF declined by {abs(pf_improvement):.1f}%"

        return False, f"insufficient improvement (PF: {pf_improvement:.1f}%, EV diff: {ev_improvement:.3f}R)"

    def promote_challenger(self) -> bool:
        """
        Promote challenger to champion.

        Returns:
            True if promotion succeeded
        """
        should, reason = self.should_promote()
        if not should:
            logger.warning("Cannot promote: {}", reason)
            return False

        # Archive current champion
        self._history.append({
            "action": "promote",
            "champion_version": self._champion.version,
            "challenger_version": self._challenger.version,
            "champion_pf": self._champion.profit_factor,
            "challenger_pf": self._challenger.profit_factor,
            "reason": reason,
            "timestamp": time.time(),
        })

        # Swap configurations
        old_champion_params = dict(self._champion.parameters)
        old_champion_version = self._champion.version

        self._champion.parameters = dict(self._challenger.parameters)
        self._champion.version = self._increment_version(self._challenger.version)
        self._champion.total_trades = 0
        self._champion.winning_trades = 0
        self._champion.total_pnl = 0
        self._champion.profit_factor = 0
        self._champion.expectancy_r = 0
        self._champion.last_modified = time.time()

        # Reset challenger
        self._challenger.parameters = dict(old_champion_params)
        self._challenger.version = self._increment_version(old_champion_version)
        self._challenger.total_trades = 0
        self._challenger.winning_trades = 0
        self._challenger.total_pnl = 0
        self._challenger.profit_factor = 0
        self._challenger.expectancy_r = 0

        self._save_configurations()

        logger.info(
            "PROMOTED: challenger v{} → champion v{} (PF improved from {:.2f} to {:.2f})",
            self._challenger.version, self._champion.version,
            self._champion.profit_factor, self._challenger.profit_factor,
        )

        return True

    def rollback(self) -> bool:
        """
        Rollback champion to previous version.

        Returns:
            True if rollback succeeded
        """
        if not self._history:
            logger.warning("No history to rollback")
            return False

        last = self._history[-1]
        if last.get("action") != "promote":
            logger.warning("Last action was not a promotion")
            return False

        # Swap back
        self._champion.parameters, self._challenger.parameters = (
            self._challenger.parameters, self._champion.parameters
        )
        self._champion.version = last.get("champion_version", "1.0.0")
        self._challenger.version = last.get("challenger_version", "1.0.0")

        self._history.append({
            "action": "rollback",
            "from_version": self._champion.version,
            "timestamp": time.time(),
        })

        self._save_configurations()

        logger.info("ROLLBACK: reverted to champion v{}", self._champion.version)
        return True

    def get_status(self) -> Dict:
        """Get current champion/challenger status."""
        return {
            "champion": self._champion.to_dict(),
            "challenger": self._challenger.to_dict(),
            "history_count": len(self._history),
        }

    def _save_configurations(self) -> None:
        """Save configurations to disk."""
        try:
            champion_file = self._config_dir / "champion.json"
            challenger_file = self._config_dir / "challenger.json"
            history_file = self._config_dir / "history.json"

            with open(champion_file, "w") as f:
                json.dump(self._champion.to_dict(), f, indent=2)

            with open(challenger_file, "w") as f:
                json.dump(self._challenger.to_dict(), f, indent=2)

            with open(history_file, "w") as f:
                json.dump(self._history[-50:], f, indent=2)  # Keep last 50

        except Exception as e:
            logger.warning("Config save error: {}", e)

    def _load_configurations(self) -> None:
        """Load configurations from disk."""
        try:
            champion_file = self._config_dir / "champion.json"
            if champion_file.exists():
                with open(champion_file) as f:
                    data = json.load(f)
                self._champion.parameters = data.get("parameters", {})
                self._champion.version = data.get("version", "1.0.0")

            challenger_file = self._config_dir / "challenger.json"
            if challenger_file.exists():
                with open(challenger_file) as f:
                    data = json.load(f)
                self._challenger.parameters = data.get("parameters", {})
                self._challenger.version = data.get("version", "1.0.0")

            history_file = self._config_dir / "history.json"
            if history_file.exists():
                with open(history_file) as f:
                    self._history = json.load(f)

        except Exception as e:
            logger.warning("Config load error: {}", e)

    @staticmethod
    def _increment_version(version: str) -> str:
        """Increment minor version."""
        parts = version.split(".")
        if len(parts) >= 2:
            parts[1] = str(int(parts[1]) + 1)
            return ".".join(parts)
        return "1.1.0"
