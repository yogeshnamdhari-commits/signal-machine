"""
Versioned Parameters — Every parameter update is versioned with rollback.

Per Priority D:
    Decision Model v1.8 → v1.9 → v2.0
    If performance deteriorates: Rollback.

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class ParameterVersion:
    """A versioned snapshot of all parameters."""
    version: str = ""
    model_name: str = ""
    parameters: Dict[str, float] = field(default_factory=dict)
    created_at: float = 0.0
    parent_version: str = ""
    change_reason: str = ""
    performance_at_creation: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "model": self.model_name,
            "parameters": self.parameters,
            "created_at": self.created_at,
            "parent": self.parent_version,
            "reason": self.change_reason,
            "performance": self.performance_at_creation,
        }


class VersionedParameterManager:
    """
    Manages versioned parameter snapshots with rollback.

    Per Priority D: Every parameter update is versioned.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._current_version: Optional[ParameterVersion] = None
        self._versions: List[ParameterVersion] = []
        self._ensure_table()
        self._load_latest()

    @property
    def current_version(self) -> str:
        """Get current version string."""
        return self._current_version.version if self._current_version else "0.0.0"

    def get_parameter(self, name: str, default: float = 0.0) -> float:
        """Get a parameter value from current version."""
        if self._current_version:
            return self._current_version.parameters.get(name, default)
        return default

    def set_parameter(self, name: str, value: float) -> None:
        """Set a parameter in current version."""
        if self._current_version:
            self._current_version.parameters[name] = value

    def create_version(
        self,
        parameters: Dict[str, float],
        model_name: str = "decision_model",
        reason: str = "",
        performance: Optional[Dict[str, float]] = None,
    ) -> str:
        """
        Create a new version snapshot.

        Returns:
            New version string
        """
        parent = self.current_version
        new_version = self._increment_version(parent)

        pv = ParameterVersion(
            version=new_version,
            model_name=model_name,
            parameters=dict(parameters),
            created_at=time.time(),
            parent_version=parent,
            change_reason=reason,
            performance_at_creation=performance or {},
        )

        self._versions.append(pv)
        self._current_version = pv

        self._store_version(pv)

        logger.info(
            "VERSION: {} {} → {} (reason: {})",
            model_name, parent, new_version, reason,
        )

        return new_version

    def rollback(self, target_version: str) -> bool:
        """
        Rollback to a previous version.

        Returns:
            True if rollback succeeded
        """
        target = None
        for v in self._versions:
            if v.version == target_version:
                target = v
                break

        if not target:
            logger.warning("Version {} not found", target_version)
            return False

        # Create rollback version
        rollback_version = self.create_version(
            parameters=dict(target.parameters),
            model_name=target.model_name,
            reason=f"rollback to {target_version}",
            performance=target.performance_at_creation,
        )

        logger.info("ROLLBACK: {} → {}", self.current_version, rollback_version)
        return True

    def get_version_history(self, limit: int = 20) -> List[ParameterVersion]:
        """Get version history."""
        return self._versions[-limit:]

    def get_parameter_history(self, parameter_name: str) -> List[Dict]:
        """Get history of a specific parameter."""
        history = []
        for v in self._versions:
            if parameter_name in v.parameters:
                history.append({
                    "version": v.version,
                    "value": v.parameters[parameter_name],
                    "created_at": v.created_at,
                    "reason": v.change_reason,
                })
        return history

    def _load_latest(self) -> None:
        """Load latest version from database."""
        try:
            import sqlite3
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("""
                SELECT * FROM parameter_versions
                ORDER BY created_at DESC LIMIT 1
            """)
            row = cur.fetchone()

            if row:
                d = dict(row)
                params = json.loads(d.get("parameters", "{}"))
                self._current_version = ParameterVersion(
                    version=d["version"],
                    model_name=d["model_name"],
                    parameters=params,
                    created_at=d["created_at"],
                    parent_version=d.get("parent_version", ""),
                    change_reason=d.get("change_reason", ""),
                )

            conn.close()

        except Exception as e:
            logger.debug("No existing version history: {}", e)

        if not self._current_version:
            self._current_version = ParameterVersion(
                version="1.0.0",
                model_name="decision_model",
                parameters={},
                created_at=time.time(),
            )

    def _store_version(self, pv: ParameterVersion) -> None:
        """Store version in database."""
        try:
            import sqlite3
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO parameter_versions (
                    version, model_name, parameters, created_at,
                    parent_version, change_reason, performance
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                pv.version, pv.model_name, json.dumps(pv.parameters),
                pv.created_at, pv.parent_version, pv.change_reason,
                json.dumps(pv.performance_at_creation),
            ))

            conn.commit()
            conn.close()

        except Exception as e:
            logger.warning("Version storage error: {}", e)

    def _ensure_table(self) -> None:
        """Create version table if it doesn't exist."""
        try:
            import sqlite3
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS parameter_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT,
                    model_name TEXT,
                    parameters TEXT,
                    created_at REAL,
                    parent_version TEXT,
                    change_reason TEXT,
                    performance TEXT
                )
            """)

            conn.commit()
            conn.close()

        except Exception as e:
            logger.warning("Version table creation error: {}", e)

    @staticmethod
    def _increment_version(version: str) -> str:
        """Increment minor version."""
        parts = version.split(".")
        if len(parts) >= 2:
            parts[1] = str(int(parts[1]) + 1)
            return ".".join(parts)
        return "1.1.0"
