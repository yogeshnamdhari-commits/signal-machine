"""
Configuration Governance Module
================================
Immutable configuration records with approval workflow.

Each configuration record contains:
- ID, Timestamp, Parameters, Statistical Results
- Validation Score, Promotion Level
- Approval Status, Rollback Version

READ-ONLY — Never modifies trading logic.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class ConfigurationRecord:
    """Immutable configuration record."""
    config_id: str = ""
    timestamp: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    statistical_results: Dict[str, Any] = field(default_factory=dict)
    validation_score: float = 0.0
    promotion_level: str = "L0"
    approval_status: str = "pending"
    rollback_version: str = ""
    approved_by: str = ""
    notes: str = ""


class ConfigurationGovernance:
    """
    Configuration governance with immutable records.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"
    
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or self.DB_PATH
        self._init_db()
    
    def _init_db(self):
        """Initialize configuration governance table."""
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS configuration_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    config_id TEXT UNIQUE NOT NULL,
                    timestamp TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    statistical_results TEXT NOT NULL,
                    validation_score REAL DEFAULT 0,
                    promotion_level TEXT DEFAULT 'L0',
                    approval_status TEXT DEFAULT 'pending',
                    rollback_version TEXT DEFAULT '',
                    approved_by TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    created_at REAL NOT NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def record_configuration(self, record: ConfigurationRecord) -> str:
        """Record a new configuration (immutable)."""
        if not record.config_id:
            record.config_id = f"config_{int(time.time())}"
        if not record.timestamp:
            record.timestamp = datetime.now(timezone.utc).isoformat()
        
        conn = self._connect()
        try:
            conn.execute("""
                INSERT INTO configuration_records 
                (config_id, timestamp, parameters, statistical_results, 
                 validation_score, promotion_level, approval_status, 
                 rollback_version, approved_by, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.config_id,
                record.timestamp,
                json.dumps(record.parameters),
                json.dumps(record.statistical_results),
                record.validation_score,
                record.promotion_level,
                record.approval_status,
                record.rollback_version,
                record.approved_by,
                record.notes,
                time.time(),
            ))
            conn.commit()
            logger.info("📋 GOVERNANCE: Recorded configuration {}", record.config_id)
            return record.config_id
        finally:
            conn.close()
    
    def approve_configuration(self, config_id: str, approved_by: str, notes: str = "") -> bool:
        """Approve a configuration."""
        conn = self._connect()
        try:
            conn.execute("""
                UPDATE configuration_records 
                SET approval_status = 'approved', approved_by = ?, notes = ?
                WHERE config_id = ?
            """, (approved_by, notes, config_id))
            conn.commit()
            logger.info("✅ GOVERNANCE: Approved configuration {}", config_id)
            return True
        except Exception as e:
            logger.error("❌ GOVERNANCE: Failed to approve {}: {}", config_id, e)
            return False
        finally:
            conn.close()
    
    def reject_configuration(self, config_id: str, reason: str) -> bool:
        """Reject a configuration."""
        conn = self._connect()
        try:
            conn.execute("""
                UPDATE configuration_records 
                SET approval_status = 'rejected', notes = ?
                WHERE config_id = ?
            """, (reason, config_id))
            conn.commit()
            logger.info("❌ GOVERNANCE: Rejected configuration {}: {}", config_id, reason)
            return True
        except Exception as e:
            logger.error("❌ GOVERNANCE: Failed to reject {}: {}", config_id, e)
            return False
        finally:
            conn.close()
    
    def get_configuration(self, config_id: str) -> Optional[ConfigurationRecord]:
        """Get a configuration record."""
        conn = self._connect()
        try:
            row = conn.execute("""
                SELECT * FROM configuration_records WHERE config_id = ?
            """, (config_id,)).fetchone()
            
            if not row:
                return None
            
            return ConfigurationRecord(
                config_id=row["config_id"],
                timestamp=row["timestamp"],
                parameters=json.loads(row["parameters"]),
                statistical_results=json.loads(row["statistical_results"]),
                validation_score=row["validation_score"],
                promotion_level=row["promotion_level"],
                approval_status=row["approval_status"],
                rollback_version=row["rollback_version"],
                approved_by=row["approved_by"],
                notes=row["notes"],
            )
        finally:
            conn.close()
    
    def get_latest_configuration(self) -> Optional[ConfigurationRecord]:
        """Get the latest approved configuration."""
        conn = self._connect()
        try:
            row = conn.execute("""
                SELECT * FROM configuration_records 
                WHERE approval_status = 'approved'
                ORDER BY created_at DESC LIMIT 1
            """).fetchone()
            
            if not row:
                return None
            
            return ConfigurationRecord(
                config_id=row["config_id"],
                timestamp=row["timestamp"],
                parameters=json.loads(row["parameters"]),
                statistical_results=json.loads(row["statistical_results"]),
                validation_score=row["validation_score"],
                promotion_level=row["promotion_level"],
                approval_status=row["approval_status"],
                rollback_version=row["rollback_version"],
                approved_by=row["approved_by"],
                notes=row["notes"],
            )
        finally:
            conn.close()
    
    def get_all_configurations(self, limit: int = 50) -> List[ConfigurationRecord]:
        """Get all configuration records."""
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT * FROM configuration_records 
                ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            
            return [
                ConfigurationRecord(
                    config_id=row["config_id"],
                    timestamp=row["timestamp"],
                    parameters=json.loads(row["parameters"]),
                    statistical_results=json.loads(row["statistical_results"]),
                    validation_score=row["validation_score"],
                    promotion_level=row["promotion_level"],
                    approval_status=row["approval_status"],
                    rollback_version=row["rollback_version"],
                    approved_by=row["approved_by"],
                    notes=row["notes"],
                )
                for row in rows
            ]
        finally:
            conn.close()
    
    def get_promotion_history(self) -> List[Dict]:
        """Get promotion history."""
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT config_id, timestamp, validation_score, 
                       promotion_level, approval_status, approved_by
                FROM configuration_records 
                ORDER BY created_at DESC
            """).fetchall()
            
            return [dict(row) for row in rows]
        finally:
            conn.close()
    
    def generate_report(self) -> str:
        """Generate configuration governance report."""
        configs = self.get_all_configurations(20)
        
        lines = []
        lines.append("=" * 80)
        lines.append("📋 CONFIGURATION GOVERNANCE REPORT")
        lines.append("=" * 80)
        
        if not configs:
            lines.append("\n   No configurations recorded.")
        else:
            lines.append(f"\n📊 SUMMARY:")
            approved = sum(1 for c in configs if c.approval_status == "approved")
            rejected = sum(1 for c in configs if c.approval_status == "rejected")
            pending = sum(1 for c in configs if c.approval_status == "pending")
            lines.append(f"   Total: {len(configs)}")
            lines.append(f"   Approved: {approved}")
            lines.append(f"   Rejected: {rejected}")
            lines.append(f"   Pending: {pending}")
            
            lines.append(f"\n📝 RECENT CONFIGURATIONS:")
            for c in configs[:10]:
                emoji = "✅" if c.approval_status == "approved" else "❌" if c.approval_status == "rejected" else "⏳"
                lines.append(f"   {emoji} {c.config_id} | Level: {c.promotion_level} | Score: {c.validation_score:.1f}")
        
        lines.append("\n" + "=" * 80)
        return "\n".join(lines)


# Global singleton
_governance: Optional[ConfigurationGovernance] = None

def get_configuration_governance() -> ConfigurationGovernance:
    """Get or create the global configuration governance."""
    global _governance
    if _governance is None:
        _governance = ConfigurationGovernance()
    return _governance
