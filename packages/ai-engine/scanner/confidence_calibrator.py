"""
Confidence Calibrator — replaces raw confidence with historical probability.

Tracks: symbol, regime, confidence, outcome, R-multiple
Computes: actual_win_rate_by_bucket
Outputs: calibrated confidence = historical probability

This alone is often worth more than adding indicators.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from loguru import logger
import numpy as np

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "database" / "confidence_calibration.db"

# Bucket edges for confidence calibration
BUCKETS = [
    (0, 50, "0-50"),
    (50, 60, "50-60"),
    (60, 70, "60-70"),
    (70, 80, "70-80"),
    (80, 90, "80-90"),
    (90, 95, "90-95"),
    (95, 101, "95-100"),
]

# Minimum samples before calibration kicks in
MIN_SAMPLES = 5


class ConfidenceCalibrator:
    """
    Replaces raw confidence scores with historically-calibrated probabilities.
    
    Instead of: confidence = 96 (meaningless)
    Outputs:    confidence = 63 (actual historical win rate at this score level)
    """

    def __init__(self) -> None:
        self._init_db()
        self._cache: Dict[str, Dict] = {}
        self._cache_ttl = 300  # 5 min

    def _init_db(self) -> None:
        """Create calibration table if not exists."""
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.execute("""
                CREATE TABLE IF NOT EXISTS calibration_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    side TEXT,
                    regime TEXT,
                    raw_confidence REAL,
                    institutional_score REAL,
                    outcome TEXT,
                    pnl REAL DEFAULT 0,
                    r_multiple REAL DEFAULT 0,
                    timestamp REAL,
                    calibrated_confidence REAL DEFAULT 0
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_calibration_bucket 
                ON calibration_log(raw_confidence)
            """)
            db.commit()
            db.close()
        except Exception as e:
            logger.warning("ConfidenceCalibrator DB init failed: {}", e)

    def record_signal(
        self,
        symbol: str,
        side: str,
        regime: str,
        raw_confidence: float,
        institutional_score: float,
    ) -> None:
        """Record a signal for later calibration when outcome is known."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.execute(
                """INSERT INTO calibration_log 
                   (symbol, side, regime, raw_confidence, institutional_score, outcome, timestamp)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (symbol, side, regime, raw_confidence, institutional_score, time.time()),
            )
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("Calibrator record failed: {}", e)

    def record_outcome(
        self,
        symbol: str,
        side: str,
        pnl: float,
        r_multiple: float,
    ) -> None:
        """Record trade outcome and update calibration."""
        try:
            # Guard against invalid values
            if not symbol or not side:
                return
            pnl = float(pnl) if pnl else 0.0
            r_multiple = float(r_multiple) if r_multiple else 0.0
            if not np.isfinite(pnl) or not np.isfinite(r_multiple):
                return
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            # Find the most recent pending signal for this symbol+side
            row = db.execute(
                """SELECT id FROM calibration_log 
                   WHERE symbol=? AND side=? AND outcome='pending'
                   ORDER BY timestamp DESC LIMIT 1""",
                (symbol, side),
            ).fetchone()
            if row:
                outcome = "win" if pnl > 0 else "loss"
                db.execute(
                    "UPDATE calibration_log SET outcome=?, pnl=?, r_multiple=? WHERE id=?",
                    (outcome, pnl, r_multiple, row[0]),
                )
                db.commit()
            db.close()
            # Invalidate cache
            self._cache.clear()
        except Exception as e:
            logger.debug("Calibrator outcome failed: {}", e)

    def calibrate(self, raw_confidence: float) -> float:
        """
        Convert raw confidence to calibrated probability.
        
        Example:
            raw_confidence = 96
            historical_win_rate_at_90_100 = 58%
            calibrated_confidence = 58
        """
        cached = self._cache.get("buckets")
        if cached and time.time() - cached.get("ts", 0) < self._cache_ttl:
            return self._apply_calibration(raw_confidence, cached["data"])

        bucket_stats = self._compute_bucket_stats()
        self._cache["buckets"] = {"data": bucket_stats, "ts": time.time()}
        return self._apply_calibration(raw_confidence, bucket_stats)

    def _compute_bucket_stats(self) -> Dict[str, Dict]:
        """Compute win rate and avg R-multiple for each confidence bucket."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT raw_confidence, outcome, r_multiple FROM calibration_log WHERE outcome != 'pending'"
            ).fetchall()
            db.close()
        except Exception:
            return {}

        if len(rows) < MIN_SAMPLES:
            return {}

        stats = {}
        for lo, hi, label in BUCKETS:
            bucket_rows = [r for r in rows if lo <= r["raw_confidence"] < hi]
            if len(bucket_rows) >= 3:
                wins = sum(1 for r in bucket_rows if r["outcome"] == "win")
                total = len(bucket_rows)
                win_rate = wins / total
                avg_r = np.mean([r["r_multiple"] for r in bucket_rows])
                stats[label] = {
                    "win_rate": win_rate,
                    "avg_r": float(avg_r),
                    "count": total,
                    "confidence": (lo + hi) / 2,  # midpoint
                }
            else:
                stats[label] = None  # Not enough data

        return stats

    def _apply_calibration(self, raw_confidence: float, bucket_stats: Dict) -> float:
        """Map raw confidence to calibrated probability using bucket stats."""
        if not bucket_stats:
            # No calibration data yet — PASSTHROUGH instead of conservative penalty
            # The conservative map was a 28-point artificial penalty that killed signals.
            # Passthrough lets the confidence floor do its real job.
            return raw_confidence

        # Find the matching bucket
        for lo, hi, label in BUCKETS:
            if lo <= raw_confidence < hi:
                stat = bucket_stats.get(label)
                if stat and stat["count"] >= MIN_SAMPLES:
                    # Interpolate between buckets if adjacent data exists
                    return stat["win_rate"] * 100  # Convert to 0-100 scale

        # No matching bucket — passthrough
        return raw_confidence

    def _conservative_map(self, raw: float) -> float:
        """
        Conservative confidence mapping when no calibration data exists.
        
        Maps 0-100 raw score to a conservative probability:
        - 95-100 raw → 65-70 calibrated (never trust 100%)
        - 85-95 raw → 55-65 calibrated
        - 75-85 raw → 48-55 calibrated
        - 65-75 raw → 40-48 calibrated
        - <65 raw → <40 calibrated
        """
        if raw >= 95:
            return 65 + (raw - 95) * 1.0  # 65-70
        elif raw >= 85:
            return 55 + (raw - 85) * 1.0  # 55-65
        elif raw >= 75:
            return 48 + (raw - 75) * 0.7  # 48-55
        elif raw >= 65:
            return 40 + (raw - 65) * 0.8  # 40-48
        elif raw >= 50:
            return 25 + (raw - 50) * 1.0  # 25-40
        else:
            return raw * 0.5  # Very low

    def get_calibration_stats(self) -> Dict:
        """Get current calibration statistics for display."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            total = db.execute("SELECT COUNT(*) FROM calibration_log").fetchone()[0]
            pending = db.execute("SELECT COUNT(*) FROM calibration_log WHERE outcome='pending'").fetchone()[0]
            resolved = total - pending
            wins = db.execute("SELECT COUNT(*) FROM calibration_log WHERE outcome='win'").fetchone()[0]
            db.close()
            return {
                "total_signals": total,
                "pending": pending,
                "resolved": resolved,
                "wins": wins,
                "overall_win_rate": f"{wins/resolved*100:.1f}%" if resolved > 0 else "N/A",
                "calibration_ready": resolved >= MIN_SAMPLES,
            }
        except Exception:
            return {"total_signals": 0, "calibration_ready": False}

    def get_bucket_display(self) -> List[Dict]:
        """Get bucket stats for dashboard display."""
        stats = self._compute_bucket_stats()
        result = []
        for lo, hi, label in BUCKETS:
            stat = stats.get(label)
            if stat:
                result.append({
                    "bucket": f"{lo}-{hi}",
                    "win_rate": f"{stat['win_rate']*100:.1f}%",
                    "avg_r": f"{stat['avg_r']:+.2f}",
                    "count": stat["count"],
                    "status": "🟢" if stat["win_rate"] > 0.5 else "🟡" if stat["win_rate"] > 0.4 else "🔴",
                })
            else:
                result.append({
                    "bucket": f"{lo}-{hi}",
                    "win_rate": "N/A",
                    "avg_r": "N/A",
                    "count": 0,
                    "status": "⚪",
                })
        return result
