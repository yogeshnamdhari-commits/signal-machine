"""
Candidate Audit Logger — Captures every candidate scoring ≥70 confidence.

Stores complete scoring breakdown, market context, and rejection reason
for later outcome analysis and threshold simulation.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger


class CandidateLogger:
    """Logs high-scoring EMA_V5 candidates for statistical validation."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"
    MIN_LOG_CONFIDENCE = 70.0  # Log candidates scoring ≥ 70
    DEDUP_WINDOW_SEC = 300     # Don't re-log same symbol within 5 minutes

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._logged = 0
        self._recent: Dict[str, float] = {}  # symbol -> last_log_timestamp
        logger.info("📊 Calibration Logger initialized — logging candidates ≥ {:.0f}", self.MIN_LOG_CONFIDENCE)

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,

                -- Confidence scores
                confidence REAL,
                trend_score REAL,
                pullback_score REAL,
                candle_score REAL,
                volume_score REAL,
                regime_score REAL,

                -- Full breakdown JSON
                breakdown TEXT,

                -- Sub-engine evaluations JSON
                trend_eval TEXT,
                pullback_eval TEXT,
                candle_eval TEXT,
                volume_eval TEXT,
                regime_eval TEXT,
                ema_data TEXT,

                -- Market context
                atr_14 REAL,
                last_close REAL,
                volume_ratio REAL,
                funding_rate REAL,
                regime TEXT,
                direction_trend TEXT,
                pullback_detected INTEGER,
                candle_pattern TEXT,

                -- Rejection info
                passed INTEGER DEFAULT 0,
                rejection_stage TEXT,
                rejection_reason TEXT,

                -- Outcome tracking
                price_15m REAL,
                price_30m REAL,
                price_1h REAL,
                price_2h REAL,
                price_4h REAL,
                price_8h REAL,
                price_24h REAL,
                mfe REAL,
                mae REAL,
                max_drawdown REAL,
                max_runup REAL,
                return_pct REAL,
                rr_achieved REAL,
                outcome_tracked INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_candidates_confidence ON candidates(confidence DESC);
            CREATE INDEX IF NOT EXISTS idx_candidates_symbol ON candidates(symbol, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_candidates_outcome ON candidates(outcome_tracked);
            CREATE INDEX IF NOT EXISTS idx_candidates_timestamp ON candidates(timestamp DESC);
        """)
        self._conn.commit()

    def log_candidate(
        self,
        symbol: str,
        confidence_eval: Dict,
        regime_eval: Optional[Dict] = None,
        trend_eval: Optional[Dict] = None,
        pullback_eval: Optional[Dict] = None,
        candle_eval: Optional[Dict] = None,
        volume_eval: Optional[Dict] = None,
        ema_data: Optional[Dict] = None,
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        direction: str = "",
        passed: bool = False,
        rejection_stage: str = "",
        rejection_reason: str = "",
    ) -> None:
        """Log a candidate that reached the confidence stage."""
        confidence = confidence_eval.get("confidence", 0)
        if confidence < self.MIN_LOG_CONFIDENCE:
            return  # Below logging threshold

        # Deduplication: don't re-log same symbol within window
        now = time.time()
        last_seen = self._recent.get(symbol, 0)
        if now - last_seen < self.DEDUP_WINDOW_SEC:
            return  # Duplicate within dedup window
        self._recent[symbol] = now

        # Evict old entries from dedup cache (keep last 1000)
        if len(self._recent) > 1000:
            cutoff = now - self.DEDUP_WINDOW_SEC * 2
            self._recent = {k: v for k, v in self._recent.items() if v > cutoff}

        breakdown = confidence_eval.get("breakdown", {})

        # Extract sub-engine data
        trend_score = breakdown.get("trend", trend_eval.get("trend_score", 0) if trend_eval else 0)
        pullback_score = breakdown.get("pullback", 100 if pullback_eval and pullback_eval.get("pullback_detected") else 0)
        candle_score = breakdown.get("candle", candle_eval.get("candle_score", 0) if candle_eval else 0)
        volume_score = breakdown.get("volume", volume_eval.get("volume_score", 0) if volume_eval else 0)
        regime_score = breakdown.get("regime", 100 if regime_eval and regime_eval.get("regime") in ("BUY_MODE", "SELL_MODE") else 0)

        # Market context
        atr_14 = ema_data.get("atr_14", 0) if ema_data else 0
        last_close = ema_data.get("last_close", 0) if ema_data else 0
        volume_ratio = volume_eval.get("volume_ratio", 0) if volume_eval else 0
        regime = regime_eval.get("regime", "unknown") if regime_eval else "unknown"

        try:
            self._conn.execute("""
                INSERT INTO candidates (
                    timestamp, symbol, direction, entry_price, stop_loss, take_profit,
                    confidence, trend_score, pullback_score, candle_score, volume_score, regime_score,
                    breakdown, trend_eval, pullback_eval, candle_eval, volume_eval, regime_eval, ema_data,
                    atr_14, last_close, volume_ratio, regime, direction_trend,
                    pullback_detected, candle_pattern,
                    passed, rejection_stage, rejection_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                time.time(), symbol, direction, entry_price, stop_loss, take_profit,
                confidence, trend_score, pullback_score, candle_score, volume_score, regime_score,
                json.dumps(breakdown, default=str),
                json.dumps(trend_eval, default=str) if trend_eval else None,
                json.dumps(pullback_eval, default=str) if pullback_eval else None,
                json.dumps(candle_eval, default=str) if candle_eval else None,
                json.dumps(volume_eval, default=str) if volume_eval else None,
                json.dumps(regime_eval, default=str) if regime_eval else None,
                json.dumps(ema_data, default=str) if ema_data else None,
                atr_14, last_close, volume_ratio, regime,
                trend_eval.get("direction", "") if trend_eval else "",
                1 if pullback_eval and pullback_eval.get("pullback_detected") else 0,
                candle_eval.get("pattern", "") if candle_eval else "",
                1 if passed else 0,
                rejection_stage,
                rejection_reason,
            ))
            self._conn.commit()
            self._logged += 1

            if self._logged % 10 == 0:
                logger.info("📊 Calibration: {} candidates logged (latest: {} conf={:.1f})", self._logged, symbol, confidence)

        except Exception as e:
            logger.debug("Calibration log error: {}", e)

    def get_stats(self) -> Dict:
        """Get logging statistics."""
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*), AVG(confidence), MAX(confidence), MIN(confidence) FROM candidates")
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM candidates WHERE passed = 1")
        passed = cur.fetchone()[0]
        return {
            "total_logged": row[0] or 0,
            "avg_confidence": round(row[1] or 0, 1),
            "max_confidence": round(row[2] or 0, 1),
            "min_confidence": round(row[3] or 0, 1),
            "passed_gate": passed,
        }

    def close(self) -> None:
        self._conn.close()
