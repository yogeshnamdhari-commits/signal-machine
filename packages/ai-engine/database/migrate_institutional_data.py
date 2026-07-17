"""
Database Migration — FIX #6: Store institutional data in signals table.

Adds missing columns to the signals table for full audit trail.
NO NULL VALUES — defaults provided for all new columns.
"""
import sqlite3
import sys
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


def migrate():
    """Run migration to add institutional data columns."""
    db = sqlite3.connect(str(DB_PATH))
    
    # New columns for institutional data
    new_columns = [
        ("mss_score", "REAL DEFAULT 0"),
        ("fvg_score", "REAL DEFAULT 0"),
        ("entry_reason", "TEXT DEFAULT ''"),
        ("mae_pct", "REAL DEFAULT 0"),
        ("mfe_pct", "REAL DEFAULT 0"),
        ("realized_r", "REAL DEFAULT 0"),
        ("outcome", "TEXT DEFAULT 'pending'"),
    ]
    
    migrated = 0
    for col_name, col_def in new_columns:
        try:
            db.execute(f"ALTER TABLE signals ADD COLUMN {col_name} {col_def}")
            migrated += 1
            print(f"  ✅ Added column: {col_name} ({col_def})")
        except sqlite3.OperationalError:
            print(f"  ⏭️  Column {col_name} already exists")
    
    # Also add to positions table
    pos_columns = [
        ("mss_score", "REAL DEFAULT 0"),
        ("fvg_score", "REAL DEFAULT 0"),
        ("entry_reason", "TEXT DEFAULT ''"),
        ("outcome", "TEXT DEFAULT 'pending'"),
        ("planned_rr", "REAL DEFAULT 0"),
        ("at_open_regime", "TEXT DEFAULT ''"),
        ("at_open_session", "TEXT DEFAULT ''"),
        ("volatility_score", "REAL DEFAULT 0"),
        ("quiet_market_blocked", "INTEGER DEFAULT 0"),
    ]
    
    for col_name, col_def in pos_columns:
        try:
            db.execute(f"ALTER TABLE positions ADD COLUMN {col_name} {col_def}")
            migrated += 1
            print(f"  ✅ Added to positions: {col_name} ({col_def})")
        except sqlite3.OperationalError:
            print(f"  ⏭️  positions.{col_name} already exists")
    
    db.commit()
    db.close()
    
    print(f"\n  Migration complete: {migrated} columns added")
    return migrated


if __name__ == "__main__":
    migrate()
