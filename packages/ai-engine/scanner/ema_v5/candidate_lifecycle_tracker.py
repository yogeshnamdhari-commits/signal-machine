"""
EMA V5 Candidate Lifecycle Tracker — Outcome-based validation.

Tracks every candidate from BUY_MODE/SELL_MODE through the pipeline,
logs rejection stages, and forward-tracks price to measure outcomes.

This answers: "Are rejected candidates profitable or losing?"
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class CandidateLifecycleTracker:
    """Tracks candidate lifecycle and outcomes for threshold validation."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_lifecycle.db"
    DEDUP_WINDOW_SEC = 60  # Don't re-log same symbol within 1 minute

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._recent: Dict[str, float] = {}
        logger.info("📊 Candidate Lifecycle Tracker initialized → {}", self._db_path)

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS candidate_lifecycle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT,
                regime TEXT,
                
                -- Entry point data
                entry_price REAL,
                atr_14 REAL,
                ema20 REAL,
                ema50 REAL,
                ema144 REAL,
                ema200 REAL,
                volume_ratio REAL,
                
                -- Pipeline stage results
                regime_pass INTEGER DEFAULT 0,
                trend_pass INTEGER DEFAULT 0,
                pullback_pass INTEGER DEFAULT 0,
                candle_pass INTEGER DEFAULT 0,
                volume_pass INTEGER DEFAULT 0,
                confidence_pass INTEGER DEFAULT 0,
                signal_pass INTEGER DEFAULT 0,
                
                -- Rejection info (NULL if passed)
                rejection_stage TEXT,
                rejection_reason TEXT,
                
                -- Component scores
                trend_score REAL,
                pullback_score REAL,
                candle_score REAL,
                volume_score REAL,
                confidence REAL,
                
                -- Decision latency timestamps
                regime_timestamp REAL,
                trend_timestamp REAL,
                pullback_timestamp REAL,
                candle_timestamp REAL,
                volume_timestamp REAL,
                confidence_timestamp REAL,
                signal_timestamp REAL,
                
                -- Decision latency (seconds from candidate entry)
                latency_regime REAL,
                latency_trend REAL,
                latency_pullback REAL,
                latency_candle REAL,
                latency_volume REAL,
                latency_confidence REAL,
                latency_signal REAL,
                latency_total REAL,
                
                -- Price at detection vs signal
                price_at_detection REAL,
                price_at_signal REAL,
                price_slippage_pct REAL,
                best_achievable_entry REAL,
                
                -- Outcome tracking (updated later)
                price_15m REAL,
                price_30m REAL,
                price_1h REAL,
                price_2h REAL,
                price_4h REAL,
                price_8h REAL,
                price_24h REAL,
                
                -- Outcome metrics
                return_15m_pct REAL,
                return_30m_pct REAL,
                return_1h_pct REAL,
                return_2h_pct REAL,
                return_4h_pct REAL,
                return_8h_pct REAL,
                return_24h_pct REAL,
                
                -- Would-be trade metrics (if we had entered at entry_price)
                would_be_mfe REAL,  -- Maximum favorable excursion
                would_be_mae REAL,  -- Maximum adverse excursion
                would_be_rr REAL,   -- Would-be R:R achieved
                
                -- Outcome classification
                outcome TEXT,  -- 'profitable', 'breakeven', 'losing', 'unknown'
                opportunity_type TEXT,  -- 'strong_winner', 'small_winner', 'breakeven', 'small_loser', 'large_loser'
                outcome_tracked INTEGER DEFAULT 0,
                
                -- Signal info (if became a signal)
                signal_id TEXT,
                signal_confidence REAL,
                actual_entry REAL,
                actual_exit REAL,
                actual_pnl REAL,
                
                -- Post-confidence gate results
                gate_duplicate INTEGER,  -- 0=pass, 1=rejected, NULL=not reached
                gate_cooldown INTEGER,
                gate_entry_atr INTEGER,
                gate_momentum INTEGER,
                gate_rr INTEGER,
                gate_session INTEGER,
                gate_cycle_limit INTEGER,
                gate_risk INTEGER,
                gate_position_limit INTEGER,
                gate_final TEXT  -- 'passed', 'duplicate', 'cooldown', etc.
            );
            
            CREATE INDEX IF NOT EXISTS idx_lifecycle_symbol ON candidate_lifecycle(symbol, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_lifecycle_timestamp ON candidate_lifecycle(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_lifecycle_rejection ON candidate_lifecycle(rejection_stage);
            CREATE INDEX IF NOT EXISTS idx_lifecycle_outcome ON candidate_lifecycle(outcome_tracked, outcome);
            CREATE INDEX IF NOT EXISTS idx_lifecycle_opportunity ON candidate_lifecycle(opportunity_type);
            CREATE INDEX IF NOT EXISTS idx_lifecycle_gates ON candidate_lifecycle(gate_final);
        """)
        self._conn.commit()

    def log_candidate_entry(
        self,
        symbol: str,
        direction: str,
        regime: str,
        entry_price: float,
        atr_14: float,
        ema20: float,
        ema50: float,
        ema144: float,
        ema200: float,
        volume_ratio: float,
    ) -> int:
        """Log when a candidate first enters BUY_MODE or SELL_MODE.
        
        Returns the candidate ID for later updates.
        """
        now = time.time()
        
        # Deduplication
        last_seen = self._recent.get(symbol, 0)
        if now - last_seen < self.DEDUP_WINDOW_SEC:
            return -1  # Duplicate
        self._recent[symbol] = now
        
        # Evict old entries
        if len(self._recent) > 5000:
            cutoff = now - self.DEDUP_WINDOW_SEC * 2
            self._recent = {k: v for k, v in self._recent.items() if v > cutoff}
        
        try:
            cur = self._conn.execute("""
                INSERT INTO candidate_lifecycle (
                    timestamp, symbol, direction, regime,
                    entry_price, atr_14, ema20, ema50, ema144, ema200,
                    volume_ratio
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now, symbol, direction, regime,
                entry_price, atr_14, ema20, ema50, ema144, ema200,
                volume_ratio,
            ))
            self._conn.commit()
            candidate_id = cur.lastrowid
            
            logger.debug(
                "📊 CANDIDATE_ENTRY sym={} dir={} regime={} entry={:.4f} atr={:.4f}",
                symbol, direction, regime, entry_price, atr_14,
            )
            
            return candidate_id
            
        except Exception as e:
            logger.debug("Candidate lifecycle log error: {}", e)
            return -1

    def update_pipeline_stage(
        self,
        candidate_id: int,
        stage: str,
        passed: bool,
        reason: str = "",
        score: float = 0,
    ) -> None:
        """Update a pipeline stage result for a candidate."""
        if candidate_id < 0:
            return
        
        now = time.time()
        stage_column = f"{stage}_pass"
        timestamp_column = f"{stage}_timestamp"
        latency_column = f"latency_{stage}"
        
        try:
            # Get candidate entry timestamp
            cur = self._conn.execute(
                "SELECT timestamp FROM candidate_lifecycle WHERE id = ?",
                (candidate_id,)
            )
            row = cur.fetchone()
            entry_timestamp = row[0] if row else now
            
            # Calculate latency from entry
            latency = now - entry_timestamp
            
            # Update the stage pass/fail, timestamp, and latency
            self._conn.execute(f"""
                UPDATE candidate_lifecycle
                SET {stage_column} = ?, {timestamp_column} = ?, {latency_column} = ?
                WHERE id = ?
            """, (1 if passed else 0, now, latency, candidate_id))
            
            # If rejected, record rejection info
            if not passed:
                self._conn.execute("""
                    UPDATE candidate_lifecycle
                    SET rejection_stage = ?, rejection_reason = ?
                    WHERE id = ?
                """, (stage, reason, candidate_id))
            
            # If this is the signal stage and passed, calculate total latency
            if stage == "signal" and passed:
                self._conn.execute("""
                    UPDATE candidate_lifecycle
                    SET latency_total = ?, price_at_signal = ?
                    WHERE id = ?
                """, (latency, 0, candidate_id))  # price_at_signal will be updated later
            
            self._conn.commit()
            
        except Exception as e:
            logger.debug("Candidate lifecycle update error: {}", e)

    def update_scores(
        self,
        candidate_id: int,
        trend_score: float = 0,
        pullback_score: float = 0,
        candle_score: float = 0,
        volume_score: float = 0,
        confidence: float = 0,
    ) -> None:
        """Update component scores for a candidate."""
        if candidate_id < 0:
            return
        
        try:
            self._conn.execute("""
                UPDATE candidate_lifecycle
                SET trend_score = ?, pullback_score = ?, candle_score = ?,
                    volume_score = ?, confidence = ?
                WHERE id = ?
            """, (trend_score, pullback_score, candle_score, volume_score, confidence, candidate_id))
            self._conn.commit()
            
        except Exception as e:
            logger.debug("Candidate lifecycle score update error: {}", e)

    def update_outcome(
        self,
        candidate_id: int,
        price_15m: float = 0,
        price_30m: float = 0,
        price_1h: float = 0,
        price_2h: float = 0,
        price_4h: float = 0,
        price_8h: float = 0,
        price_24h: float = 0,
    ) -> None:
        """Update forward price tracking for a candidate."""
        if candidate_id < 0:
            return
        
        try:
            # Calculate returns
            entry = self._get_entry_price(candidate_id)
            if entry <= 0:
                return
            
            # For LONG candidates, profit = (future - entry) / entry
            # For SHORT candidates, profit = (entry - future) / entry
            direction = self._get_direction(candidate_id)
            multiplier = 1 if direction == "LONG" else -1
            
            returns = {}
            for label, price in [
                ("15m", price_15m), ("30m", price_30m), ("1h", price_1h),
                ("2h", price_2h), ("4h", price_4h), ("8h", price_8h), ("24h", price_24h),
            ]:
                if price > 0:
                    returns[label] = multiplier * (price - entry) / entry * 100
            
            # Calculate MFE and MAE
            prices = [p for p in [price_15m, price_30m, price_1h, price_2h, price_4h, price_8h, price_24h] if p > 0]
            if prices:
                if direction == "LONG":
                    mfe = max(p - entry for p in prices) / entry * 100
                    mae = min(p - entry for p in prices) / entry * 100
                else:
                    mfe = entry - min(p for p in prices) / entry * 100
                    mae = entry - max(p for p in prices) / entry * 100
            else:
                mfe = 0
                mae = 0
            
            # Classify outcome (binary: profitable vs losing)
            if returns.get("1h", 0) > 0.5:
                outcome = "profitable"
            elif returns.get("1h", 0) < -0.5:
                outcome = "losing"
            else:
                outcome = "breakeven"
            
            # Classify opportunity type (5-level classification)
            best_return = max(returns.values()) if returns else 0
            if best_return >= 2.0:
                opportunity_type = "strong_winner"
            elif best_return >= 0.5:
                opportunity_type = "small_winner"
            elif best_return >= -0.5:
                opportunity_type = "breakeven"
            elif best_return >= -2.0:
                opportunity_type = "small_loser"
            else:
                opportunity_type = "large_loser"
            
            self._conn.execute("""
                UPDATE candidate_lifecycle
                SET price_15m = ?, price_30m = ?, price_1h = ?, price_2h = ?,
                    price_4h = ?, price_8h = ?, price_24h = ?,
                    return_15m_pct = ?, return_30m_pct = ?, return_1h_pct = ?,
                    return_2h_pct = ?, return_4h_pct = ?, return_8h_pct = ?,
                    return_24h_pct = ?,
                    would_be_mfe = ?, would_be_mae = ?,
                    outcome = ?, opportunity_type = ?, outcome_tracked = 1
                WHERE id = ?
            """, (
                price_15m, price_30m, price_1h, price_2h, price_4h, price_8h, price_24h,
                returns.get("15m", 0), returns.get("30m", 0), returns.get("1h", 0),
                returns.get("2h", 0), returns.get("4h", 0), returns.get("8h", 0),
                returns.get("24h", 0),
                mfe, mae,
                outcome, opportunity_type, candidate_id,
            ))
            self._conn.commit()
            
        except Exception as e:
            logger.debug("Candidate lifecycle outcome update error: {}", e)

    def get_untracked_candidates(self, max_age_hours: int = 24) -> List[Dict]:
        """Get candidates that need outcome tracking."""
        cutoff = time.time() - (max_age_hours * 3600)
        cur = self._conn.execute("""
            SELECT id, symbol, direction, regime, entry_price, timestamp
            FROM candidate_lifecycle
            WHERE outcome_tracked = 0 AND timestamp > ?
            ORDER BY timestamp ASC
        """, (cutoff,))
        
        return [
            {"id": row[0], "symbol": row[1], "direction": row[2],
             "regime": row[3], "entry_price": row[4], "timestamp": row[5]}
            for row in cur.fetchall()
        ]

    def get_rejection_analysis(self, hours: int = 24) -> Dict:
        """Analyze rejection patterns and outcomes."""
        cutoff = time.time() - (hours * 3600)
        cur = self._conn.execute("""
            SELECT 
                rejection_stage,
                COUNT(*) as count,
                AVG(entry_price) as avg_entry,
                AVG(trend_score) as avg_trend,
                AVG(volume_score) as avg_volume,
                AVG(confidence) as avg_confidence,
                SUM(CASE WHEN outcome = 'profitable' THEN 1 ELSE 0 END) as profitable_count,
                SUM(CASE WHEN outcome = 'losing' THEN 1 ELSE 0 END) as losing_count,
                AVG(return_1h_pct) as avg_return_1h,
                AVG(would_be_mfe) as avg_mfe,
                AVG(would_be_mae) as avg_mae
            FROM candidate_lifecycle
            WHERE timestamp > ? AND outcome_tracked = 1
            GROUP BY rejection_stage
            ORDER BY count DESC
        """, (cutoff,))
        
        results = {}
        for row in cur.fetchall():
            stage = row[0] or "passed"
            results[stage] = {
                "count": row[1],
                "avg_entry": round(row[2] or 0, 4),
                "avg_trend": round(row[3] or 0, 1),
                "avg_volume": round(row[4] or 0, 1),
                "avg_confidence": round(row[5] or 0, 1),
                "profitable_count": row[6] or 0,
                "losing_count": row[7] or 0,
                "avg_return_1h": round(row[8] or 0, 2),
                "avg_mfe": round(row[9] or 0, 2),
                "avg_mae": round(row[10] or 0, 2),
            }
        
        return results

    def get_funnel_analysis(self, hours: int = 24) -> Dict:
        """Get complete funnel analysis with outcomes."""
        cutoff = time.time() - (hours * 3600)
        cur = self._conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(regime_pass) as regime_pass,
                SUM(trend_pass) as trend_pass,
                SUM(pullback_pass) as pullback_pass,
                SUM(candle_pass) as candle_pass,
                SUM(volume_pass) as volume_pass,
                SUM(confidence_pass) as confidence_pass,
                SUM(signal_pass) as signal_pass
            FROM candidate_lifecycle
            WHERE timestamp > ?
        """, (cutoff,))
        
        row = cur.fetchone()
        return {
            "total_candidates": row[0] or 0,
            "regime_pass": row[1] or 0,
            "trend_pass": row[2] or 0,
            "pullback_pass": row[3] or 0,
            "candle_pass": row[4] or 0,
            "volume_pass": row[5] or 0,
            "confidence_pass": row[6] or 0,
            "signal_pass": row[7] or 0,
        }

    def get_opportunity_capture_rate(self, hours: int = 24) -> Dict:
        """Calculate opportunity capture rate with Precision and Recall.
        
        SUCCESS CRITERIA (rigorously defined):
        A "profitable opportunity" is defined as a candidate where:
        - Price moved at least +1% in the favorable direction within 4 hours
        - OR price reached TP1 equivalent (1.5R) before SL
        
        This ensures we only count genuinely profitable setups, not noise.
        
        PRECISION: Of signals EMA V5 generated, how many became profitable?
        RECALL: Of all profitable opportunities, how many did EMA V5 capture?
        
        Returns:
            {
                "success_criteria": str,
                "precision": float (0-100),
                "recall": float (0-100),
                "f1_score": float (0-100),
                "signals_generated": int,
                "signals_profitable": int,
                "opportunities_total": int,
                "opportunities_profitable": int,
                "true_positives": int,
                "false_positives": int,
                "true_negatives": int,
                "false_negatives": int,
            }
        """
        cutoff = time.time() - (hours * 3600)
        
        # Get all tracked candidates with outcomes
        cur = self._conn.execute("""
            SELECT 
                id,
                signal_pass,
                return_4h_pct,
                would_be_mfe,
                would_be_mae,
                outcome,
                opportunity_type
            FROM candidate_lifecycle
            WHERE timestamp > ? AND outcome_tracked = 1
        """, (cutoff,))
        
        rows = cur.fetchall()
        
        if not rows:
            return {
                "success_criteria": "Price moved ≥+1% in favorable direction within 4h",
                "precision": 0,
                "recall": 0,
                "f1_score": 0,
                "signals_generated": 0,
                "signals_profitable": 0,
                "opportunities_total": 0,
                "opportunities_profitable": 0,
                "true_positives": 0,
                "false_positives": 0,
                "true_negatives": 0,
                "false_negatives": 0,
            }
        
        # Define success: price moved at least +1% in favorable direction within 4h
        SUCCESS_THRESHOLD = 1.0  # 1% return
        
        signals_generated = 0
        signals_profitable = 0
        opportunities_profitable = 0
        
        true_positives = 0   # Signal generated AND profitable
        false_positives = 0  # Signal generated BUT not profitable
        true_negatives = 0   # Rejected AND would not have been profitable
        false_negatives = 0  # Rejected BUT would have been profitable
        
        for candidate_id, signal_pass, return_4h, mfe, mae, outcome, opp_type in rows:
            # Determine if this was actually a profitable opportunity
            is_profitable = (return_4h or 0) >= SUCCESS_THRESHOLD
            
            if is_profitable:
                opportunities_profitable += 1
            
            if signal_pass:
                signals_generated += 1
                if is_profitable:
                    signals_profitable += 1
                    true_positives += 1  # Correct signal
                else:
                    false_positives += 1  # Bad signal
            else:
                if is_profitable:
                    false_negatives += 1  # Missed profitable opportunity
                else:
                    true_negatives += 1  # Correctly rejected
        
        # Calculate Precision: Of signals generated, how many were profitable?
        precision = true_positives / max(signals_generated, 1) * 100
        
        # Calculate Recall: Of all profitable opportunities, how many were captured?
        recall = true_positives / max(opportunities_profitable, 1) * 100
        
        # F1 Score: Harmonic mean of precision and recall
        f1 = 2 * precision * recall / max(precision + recall, 0.01)
        
        return {
            "success_criteria": f"Price moved ≥+{SUCCESS_THRESHOLD}% in favorable direction within 4h",
            "precision": round(precision, 1),
            "recall": round(recall, 1),
            "f1_score": round(f1, 1),
            "signals_generated": signals_generated,
            "signals_profitable": signals_profitable,
            "opportunities_total": len(rows),
            "opportunities_profitable": opportunities_profitable,
            "true_positives": true_positives,
            "false_positives": false_positives,
            "true_negatives": true_negatives,
            "false_negatives": false_negatives,
            "sample_size_sufficient": signals_generated >= 100 and len(rows) >= 500,
        }

    def update_post_confidence_gates(
        self,
        candidate_id: int,
        gate_duplicate: Optional[int] = None,
        gate_cooldown: Optional[int] = None,
        gate_entry_atr: Optional[int] = None,
        gate_momentum: Optional[int] = None,
        gate_rr: Optional[int] = None,
        gate_session: Optional[int] = None,
        gate_cycle_limit: Optional[int] = None,
        gate_risk: Optional[int] = None,
        gate_position_limit: Optional[int] = None,
        gate_final: str = "passed",
    ) -> None:
        """Update post-confidence gate results for a candidate.
        
        Args:
            candidate_id: ID of the candidate
            gate_duplicate: 0=pass, 1=rejected, None=not reached
            gate_cooldown: 0=pass, 1=rejected, None=not reached
            gate_entry_atr: 0=pass, 1=rejected, None=not reached
            gate_momentum: 0=pass, 1=rejected, None=not reached
            gate_rr: 0=pass, 1=rejected, None=not reached
            gate_session: 0=pass, 1=rejected, None=not reached
            gate_cycle_limit: 0=pass, 1=rejected, None=not reached
            gate_risk: 0=pass, 1=rejected, None=not reached
            gate_position_limit: 0=pass, 1=rejected, None=not reached
            gate_final: Final result ('passed', 'duplicate', 'cooldown', etc.)
        """
        if candidate_id < 0:
            return
        
        try:
            self._conn.execute("""
                UPDATE candidate_lifecycle
                SET gate_duplicate = ?, gate_cooldown = ?, gate_entry_atr = ?,
                    gate_momentum = ?, gate_rr = ?, gate_session = ?,
                    gate_cycle_limit = ?, gate_risk = ?, gate_position_limit = ?,
                    gate_final = ?
                WHERE id = ?
            """, (
                gate_duplicate, gate_cooldown, gate_entry_atr,
                gate_momentum, gate_rr, gate_session,
                gate_cycle_limit, gate_risk, gate_position_limit,
                gate_final, candidate_id,
            ))
            self._conn.commit()
            
        except Exception as e:
            logger.debug("Post-confidence gate update error: {}", e)

    def get_post_confidence_analysis(self, hours: int = 24) -> Dict:
        """Analyze post-confidence gate rejections.
        
        Shows exactly where candidates are lost after passing confidence.
        
        Returns:
            {
                "confidence_passed": int,
                "signal_engine_passed": int,
                "published": int,
                "gates": {
                    "duplicate": {count, ...},
                    "cooldown": {count, ...},
                    ...
                }
            }
        """
        cutoff = time.time() - (hours * 3600)
        
        # Count candidates that passed confidence
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND confidence_pass = 1
        """, (cutoff,))
        confidence_passed = cur.fetchone()[0] or 0
        
        # Count candidates that passed all signal engine gates
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND gate_final = 'passed'
        """, (cutoff,))
        signal_engine_passed = cur.fetchone()[0] or 0
        
        # Count published signals
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND signal_pass = 1
        """, (cutoff,))
        published = cur.fetchone()[0] or 0
        
        # Count rejections by gate
        gates = {}
        for gate_name in ["duplicate", "cooldown", "entry_atr", "momentum", "rr", 
                          "session", "cycle_limit", "risk", "position_limit"]:
            cur = self._conn.execute(f"""
                SELECT COUNT(*) FROM candidate_lifecycle
                WHERE timestamp > ? AND gate_{gate_name} = 1
            """, (cutoff,))
            count = cur.fetchone()[0] or 0
            gates[gate_name] = {
                "rejected": count,
                "pct_of_confidence_passed": round(count / max(confidence_passed, 1) * 100, 1),
            }
        
        return {
            "confidence_passed": confidence_passed,
            "signal_engine_passed": signal_engine_passed,
            "published": published,
            "gates": gates,
            "loss_after_confidence": confidence_passed - published,
            "loss_rate": round((confidence_passed - published) / max(confidence_passed, 1) * 100, 1),
        }

    def get_profit_expectancy(self, hours: int = 24) -> Dict:
        """Calculate profit expectancy for published signals.
        
        This directly answers: "Does the current EMA V5 decision engine make money?"
        
        Expectancy = P(win) × AvgWin(R) - P(loss) × AvgLoss(R)
        
        A strategy with lower recall but strong positive expectancy
        can outperform one with higher recall but weak expectancy.
        
        Returns:
            {
                "trades_analyzed": int,
                "win_rate": float (0-100),
                "avg_winner_r": float,
                "avg_loser_r": float,
                "expectancy_r": float,
                "profit_factor": float,
                "sample_size_sufficient": bool,
            }
        """
        cutoff = time.time() - (hours * 3600)
        
        # Get completed trades (signals that have been tracked through outcome)
        cur = self._conn.execute("""
            SELECT 
                id,
                signal_pass,
                return_4h_pct,
                would_be_mfe,
                would_be_mae,
                direction,
                entry_price,
                atr_14
            FROM candidate_lifecycle
            WHERE timestamp > ? AND outcome_tracked = 1 AND signal_pass = 1
        """, (cutoff,))
        
        rows = cur.fetchall()
        
        if not rows:
            return {
                "trades_analyzed": 0,
                "win_rate": 0,
                "avg_winner_r": 0,
                "avg_loser_r": 0,
                "expectancy_r": 0,
                "profit_factor": 0,
                "sample_size_sufficient": False,
            }
        
        winners = []
        losers = []
        
        for candidate_id, signal_pass, return_4h, mfe, mae, direction, entry, atr in rows:
            # Calculate R-multiple based on 4h return vs initial risk (1R)
            # Assume 1R = 1.5 * ATR (standard SL distance)
            risk = (atr or 0) * 1.5
            
            if risk <= 0:
                continue
            
            # Calculate profit in R-multiples
            if direction == "LONG":
                pnl_r = (return_4h or 0) / 100 * entry / risk
            else:
                pnl_r = -(return_4h or 0) / 100 * entry / risk
            
            if pnl_r > 0:
                winners.append(pnl_r)
            else:
                losers.append(abs(pnl_r))  # Store as positive for calculation
        
        total = len(winners) + len(losers)
        
        if total == 0:
            return {
                "trades_analyzed": 0,
                "win_rate": 0,
                "avg_winner_r": 0,
                "avg_loser_r": 0,
                "expectancy_r": 0,
                "profit_factor": 0,
                "sample_size_sufficient": False,
            }
        
        win_rate = len(winners) / total * 100
        avg_winner = sum(winners) / len(winners) if winners else 0
        avg_loser = sum(losers) / len(losers) if losers else 0
        
        # Expectancy = P(win) × AvgWin - P(loss) × AvgLoss
        expectancy = (win_rate / 100 * avg_winner) - ((100 - win_rate) / 100 * avg_loser)
        
        # Profit Factor = Gross Profit / Gross Loss
        gross_profit = sum(winners)
        gross_loss = sum(losers)
        profit_factor = gross_profit / max(gross_loss, 0.01)
        
        return {
            "trades_analyzed": total,
            "win_rate": round(win_rate, 1),
            "avg_winner_r": round(avg_winner, 2),
            "avg_loser_r": round(avg_loser, 2),
            "expectancy_r": round(expectancy, 3),
            "profit_factor": round(profit_factor, 2),
            "sample_size_sufficient": total >= 100,
            "gross_profit_r": round(gross_profit, 2),
            "gross_loss_r": round(gross_loss, 2),
        }

    def get_production_readiness(self, hours: int = 24) -> Dict:
        """Check if sample size is sufficient for statistical significance.
        
        Minimum requirements before making strategy changes:
        - Generated signals: ≥ 100
        - Completed trades: ≥ 100
        - Profitable opportunities observed: ≥ 500
        - Data collection period: ≥ 7 days
        """
        cutoff = time.time() - (hours * 3600)
        
        # Count signals generated
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND signal_pass = 1
        """, (cutoff,))
        signals_generated = cur.fetchone()[0] or 0
        
        # Count completed trades (with outcome tracked)
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND outcome_tracked = 1 AND signal_pass = 1
        """, (cutoff,))
        completed_trades = cur.fetchone()[0] or 0
        
        # Count profitable opportunities
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND outcome_tracked = 1 AND return_4h_pct >= 1.0
        """, (cutoff,))
        profitable_opportunities = cur.fetchone()[0] or 0
        
        # Count total candidates
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ?
        """, (cutoff,))
        total_candidates = cur.fetchone()[0] or 0
        
        # Data collection period
        hours_collected = hours
        
        # Check gates
        gates = {
            "signals_generated": {
                "current": signals_generated,
                "required": 100,
                "passed": signals_generated >= 100,
            },
            "completed_trades": {
                "current": completed_trades,
                "required": 100,
                "passed": completed_trades >= 100,
            },
            "profitable_opportunities": {
                "current": profitable_opportunities,
                "required": 500,
                "passed": profitable_opportunities >= 500,
            },
            "data_collection_days": {
                "current": round(hours_collected / 24, 1),
                "required": 7,
                "passed": hours_collected >= 168,  # 7 days
            },
        }
        
        all_passed = all(gate["passed"] for gate in gates.values())
        
        return {
            "gates": gates,
            "all_gates_passed": all_passed,
            "recommendation": (
                "✅ Sufficient data for statistical analysis" if all_passed
                else "⏳ Continue collecting data before making strategy changes"
            ),
        }

    def get_signal_efficiency(self, hours: int = 24) -> Dict:
        """Calculate Signal Efficiency — how well EMA V5 balances quality vs capture.
        
        This prevents optimizing solely for more trades or solely for stricter filtering.
        
        Formula:
            Efficiency = Winning Signals / (Winning Signals + Missed Profitable Opportunities)
        
        Returns:
            {
                "trend_candidates": int,
                "published_signals": int,
                "winning_signals": int,
                "missed_profitable": int,
                "efficiency": float (0-100),
                "interpretation": str,
            }
        """
        cutoff = time.time() - (hours * 3600)
        
        # Count trend candidates (entered BUY_MODE or SELL_MODE)
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND (regime = 'BUY_MODE' OR regime = 'SELL_MODE')
        """, (cutoff,))
        trend_candidates = cur.fetchone()[0] or 0
        
        # Count published signals
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND signal_pass = 1
        """, (cutoff,))
        published_signals = cur.fetchone()[0] or 0
        
        # Count winning signals (signals that became profitable)
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND signal_pass = 1 AND return_4h_pct >= 1.0
        """, (cutoff,))
        winning_signals = cur.fetchone()[0] or 0
        
        # Count missed profitable opportunities (rejected but would have been profitable)
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND signal_pass = 0 AND return_4h_pct >= 1.0
        """, (cutoff,))
        missed_profitable = cur.fetchone()[0] or 0
        
        # Calculate efficiency
        total_opportunities = winning_signals + missed_profitable
        efficiency = winning_signals / max(total_opportunities, 1) * 100
        
        # Interpretation
        if efficiency > 50:
            interpretation = "✅ Good balance — capturing majority of profitable opportunities"
        elif efficiency > 30:
            interpretation = "⚖️ Moderate — missing some profitable opportunities"
        else:
            interpretation = "⚠️ Low efficiency — missing most profitable opportunities"
        
        return {
            "trend_candidates": trend_candidates,
            "published_signals": published_signals,
            "winning_signals": winning_signals,
            "missed_profitable": missed_profitable,
            "efficiency": round(efficiency, 1),
            "interpretation": interpretation,
        }

    def get_market_condition_coverage(self, hours: int = 24) -> Dict:
        """Check if data covers different market conditions.
        
        A robust strategy should be validated across:
        - Trending markets (BUY_MODE/SELL_MODE)
        - Ranging markets (NO_TREND)
        - Both LONG and SHORT trades
        - Multiple volatility regimes
        
        Returns:
            {
                "trending_count": int,
                "ranging_count": int,
                "long_signals": int,
                "short_signals": int,
                "coverage_score": float (0-100),
                "sufficient": bool,
            }
        """
        cutoff = time.time() - (hours * 3600)
        
        # Count trending candidates
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND (regime = 'BUY_MODE' OR regime = 'SELL_MODE')
        """, (cutoff,))
        trending_count = cur.fetchone()[0] or 0
        
        # Count ranging candidates
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND regime = 'NO_TREND'
        """, (cutoff,))
        ranging_count = cur.fetchone()[0] or 0
        
        # Count LONG signals
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND signal_pass = 1 AND direction = 'LONG'
        """, (cutoff,))
        long_signals = cur.fetchone()[0] or 0
        
        # Count SHORT signals
        cur = self._conn.execute("""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND signal_pass = 1 AND direction = 'SHORT'
        """, (cutoff,))
        short_signals = cur.fetchone()[0] or 0
        
        # Calculate coverage score (0-100)
        # Need both trending and ranging data
        # Need both LONG and SHORT signals
        conditions_met = 0
        total_conditions = 4
        
        if trending_count >= 50:
            conditions_met += 1
        if ranging_count >= 50:
            conditions_met += 1
        if long_signals >= 10:
            conditions_met += 1
        if short_signals >= 10:
            conditions_met += 1
        
        coverage_score = conditions_met / total_conditions * 100
        sufficient = coverage_score >= 75  # At least 3 of 4 conditions
        
        return {
            "trending_count": trending_count,
            "ranging_count": ranging_count,
            "long_signals": long_signals,
            "short_signals": short_signals,
            "coverage_score": round(coverage_score, 1),
            "sufficient": sufficient,
        }

    def get_regime_segmentation(self, hours: int = 24) -> Dict:
        """Break down all metrics by market regime.
        
        A strategy can look excellent overall while actually making all of its
        profits in one regime and consistently underperforming in another.
        
        Returns:
            {
                "BUY_MODE": {candidates, signals, expectancy, precision, ...},
                "SELL_MODE": {...},
                "NO_TREND": {...},
            }
        """
        cutoff = time.time() - (hours * 3600)
        
        regimes = {}
        
        for regime in ["BUY_MODE", "SELL_MODE", "NO_TREND"]:
            # Count candidates
            cur = self._conn.execute("""
                SELECT COUNT(*) FROM candidate_lifecycle
                WHERE timestamp > ? AND regime = ?
            """, (cutoff, regime))
            candidates = cur.fetchone()[0] or 0
            
            # Count signals
            cur = self._conn.execute("""
                SELECT COUNT(*) FROM candidate_lifecycle
                WHERE timestamp > ? AND regime = ? AND signal_pass = 1
            """, (cutoff, regime))
            signals = cur.fetchone()[0] or 0
            
            # Count profitable opportunities (return >= 1%)
            cur = self._conn.execute("""
                SELECT COUNT(*) FROM candidate_lifecycle
                WHERE timestamp > ? AND regime = ? AND outcome_tracked = 1 AND return_4h_pct >= 1.0
            """, (cutoff, regime))
            profitable_opps = cur.fetchone()[0] or 0
            
            # Count winning signals
            cur = self._conn.execute("""
                SELECT COUNT(*) FROM candidate_lifecycle
                WHERE timestamp > ? AND regime = ? AND signal_pass = 1 AND return_4h_pct >= 1.0
            """, (cutoff, regime))
            winning_signals = cur.fetchone()[0] or 0
            
            # Calculate precision (signals that were profitable)
            precision = winning_signals / max(signals, 1) * 100
            
            # Calculate recall (profitable opportunities captured)
            recall = winning_signals / max(profitable_opps, 1) * 100
            
            # Calculate average return
            cur = self._conn.execute("""
                SELECT AVG(return_4h_pct) FROM candidate_lifecycle
                WHERE timestamp > ? AND regime = ? AND signal_pass = 1 AND outcome_tracked = 1
            """, (cutoff, regime))
            avg_return = cur.fetchone()[0] or 0
            
            regimes[regime] = {
                "candidates": candidates,
                "signals": signals,
                "profitable_opportunities": profitable_opps,
                "winning_signals": winning_signals,
                "precision": round(precision, 1),
                "recall": round(recall, 1),
                "avg_return_4h": round(avg_return, 2),
            }
        
        return regimes

    def get_filter_contribution(self, hours: int = 24) -> Dict:
        """Analyze contribution of each filter stage.
        
        For each filter, measure:
        - How many candidates it rejected
        - How many of those would have been profitable (profit saved or missed)
        
        This tells you whether each filter is adding value or simply reducing
        trade frequency.
        
        Returns:
            {
                "pullback": {rejected, would_have_been_profitable, ...},
                "candle": {...},
                "volume": {...},
                "confidence": {...},
            }
        """
        cutoff = time.time() - (hours * 3600)
        
        filters = {}
        
        for stage in ["pullback", "candle", "volume", "confidence"]:
            # Count candidates rejected at this stage
            cur = self._conn.execute("""
                SELECT COUNT(*) FROM candidate_lifecycle
                WHERE timestamp > ? AND rejection_stage = ? AND outcome_tracked = 1
            """, (cutoff, stage))
            rejected = cur.fetchone()[0] or 0
            
            # Count how many of those would have been profitable (return >= 1%)
            cur = self._conn.execute("""
                SELECT COUNT(*) FROM candidate_lifecycle
                WHERE timestamp > ? AND rejection_stage = ? AND outcome_tracked = 1 AND return_4h_pct >= 1.0
            """, (cutoff, stage))
            would_have_been_profitable = cur.fetchone()[0] or 0
            
            # Count how many would have been losers
            cur = self._conn.execute("""
                SELECT COUNT(*) FROM candidate_lifecycle
                WHERE timestamp > ? AND rejection_stage = ? AND outcome_tracked = 1 AND return_4h_pct < 0
            """, (cutoff, stage))
            would_have_been_loser = cur.fetchone()[0] or 0
            
            # Calculate filter value
            # Good filter: rejects mostly losers
            # Bad filter: rejects mostly winners
            if rejected > 0:
                filter_accuracy = would_have_been_loser / rejected * 100
            else:
                filter_accuracy = 0
            
            filters[stage] = {
                "rejected": rejected,
                "would_have_been_profitable": would_have_been_profitable,
                "would_have_been_loser": would_have_been_loser,
                "filter_accuracy": round(filter_accuracy, 1),
                "interpretation": (
                    "✅ Correctly filtering" if filter_accuracy > 70
                    else "⚠️ May be filtering profitable setups" if filter_accuracy < 50
                    else "⚖️ Mixed results"
                ),
            }
        
        return filters

    def _get_entry_price(self, candidate_id: int) -> float:
        """Get entry price for a candidate."""
        cur = self._conn.execute(
            "SELECT entry_price FROM candidate_lifecycle WHERE id = ?",
            (candidate_id,)
        )
        row = cur.fetchone()
        return row[0] if row else 0

    def _get_direction(self, candidate_id: int) -> str:
        """Get direction for a candidate."""
        cur = self._conn.execute(
            "SELECT direction FROM candidate_lifecycle WHERE id = ?",
            (candidate_id,)
        )
        row = cur.fetchone()
        return row[0] if row else "LONG"


# Global instance
_lifecycle_tracker: Optional[CandidateLifecycleTracker] = None


def get_lifecycle_tracker() -> CandidateLifecycleTracker:
    """Get the global lifecycle tracker instance."""
    global _lifecycle_tracker
    if _lifecycle_tracker is None:
        _lifecycle_tracker = CandidateLifecycleTracker()
    return _lifecycle_tracker
