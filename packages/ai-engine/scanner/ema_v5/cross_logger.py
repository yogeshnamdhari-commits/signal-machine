"""
Cross Detection Logger — Diagnostic instrumentation for EMA crossover analysis.

PURPOSE: Collect data to answer:
  "Does delayed entry after a valid EMA crossover consistently reduce expectancy?"

This module is PURELY OBSERVATIONAL. It does NOT change any trading logic.
It logs crossover events and their relationship to signal generation.

Data collected per crossover:
  - Cross time, price, symbol, side
  - Cross quality score (slope, volume, candle strength)
  - Signal time, price (when signal is generated)
  - Entry delay (candles between cross and signal)
  - Distance from cross (price change since cross)
  - Trade outcome (when trade completes)

Usage:
    from scanner.ema_v5.cross_logger import CrossLogger
    
    logger = CrossLogger()
    
    # When EMA20 crosses EMA50:
    logger.record_cross(symbol, side, cross_price, cross_time, quality_metrics)
    
    # When signal is generated:
    logger.record_signal(symbol, signal_price, signal_time)
    
    # When trade closes:
    logger.record_outcome(symbol, outcome, pnl, r_multiple)
"""

import sqlite3
import time
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "cross_analysis.db"


class CrossLogger:
    """Logs EMA crossover events for diagnostic analysis."""
    
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = Path(db_path) if db_path else _DB_PATH
        self._init_db()
        # In-memory cache of active crosses (symbol → cross data)
        self._active_crosses: Dict[str, Dict] = {}
    
    def _init_db(self):
        """Create tables if they don't exist."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            
            db.execute("""
                CREATE TABLE IF NOT EXISTS cross_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    cross_time REAL NOT NULL,
                    cross_price REAL NOT NULL,
                    ema20_at_cross REAL,
                    ema50_at_cross REAL,
                    ema20_slope REAL,
                    ema50_slope REAL,
                    atr_at_cross REAL,
                    volume_ratio REAL,
                    candle_body_ratio REAL,
                    cross_quality_score REAL,
                    signal_time REAL,
                    signal_price REAL,
                    entry_delay_candles INTEGER,
                    distance_from_cross_pct REAL,
                    outcome TEXT,
                    pnl REAL,
                    r_multiple REAL,
                    mfe_pct REAL,
                    mae_pct REAL,
                    exit_reason TEXT,
                    earliest_valid_entry REAL,
                    earliest_valid_time REAL,
                    lost_distance_pct REAL,
                    cross_efficiency_pct REAL,
                    trend_high REAL,
                    trend_low REAL,
                    regime TEXT,
                    created_at REAL DEFAULT (strftime('%s', 'now'))
                )
            """)
            
            # Migration: add regime column if missing
            try:
                existing_cols = [r[1] for r in db.execute("PRAGMA table_info(cross_events)").fetchall()]
                if 'regime' not in existing_cols:
                    db.execute("ALTER TABLE cross_events ADD COLUMN regime TEXT")
                    db.commit()
            except Exception:
                pass
            
            # Rejection reasons table
            db.execute("""
                CREATE TABLE IF NOT EXISTS cross_rejections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    cross_time REAL NOT NULL,
                    cross_price REAL NOT NULL,
                    rejection_stage TEXT NOT NULL,
                    rejection_reason TEXT NOT NULL,
                    candle_time REAL,
                    candle_price REAL,
                    filter_value REAL,
                    threshold_value REAL,
                    regime TEXT,
                    created_at REAL DEFAULT (strftime('%s', 'now'))
                )
            """)
            
            # Migration: add regime column if missing
            try:
                existing_cols = [r[1] for r in db.execute("PRAGMA table_info(cross_rejections)").fetchall()]
                if 'regime' not in existing_cols:
                    db.execute("ALTER TABLE cross_rejections ADD COLUMN regime TEXT")
                    db.commit()
            except Exception:
                pass
            
            # Filter latency timeline table
            db.execute("""
                CREATE TABLE IF NOT EXISTS filter_latency (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    cross_time REAL NOT NULL,
                    stage TEXT NOT NULL,
                    stage_time REAL,
                    stage_price REAL,
                    delay_candles INTEGER,
                    passed INTEGER DEFAULT 0,
                    created_at REAL DEFAULT (strftime('%s', 'now'))
                )
            """)
            
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_cross_symbol 
                ON cross_events(symbol, cross_time)
            """)
            
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_cross_outcome 
                ON cross_events(outcome)
            """)
            
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_rejection_symbol 
                ON cross_rejections(symbol, cross_time)
            """)
            
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_latency_symbol 
                ON filter_latency(symbol, cross_time)
            """)
            
            # Missed opportunity tracking (NEW)
            db.execute("""
                CREATE TABLE IF NOT EXISTS missed_opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    cross_time REAL NOT NULL,
                    cross_price REAL NOT NULL,
                    rejection_stage TEXT,
                    final_outcome TEXT,
                    max_adverse REAL,
                    max_favorable REAL,
                    moved_1atr INTEGER DEFAULT 0,
                    moved_2atr INTEGER DEFAULT 0,
                    moved_3atr INTEGER DEFAULT 0,
                    moved_5atr INTEGER DEFAULT 0,
                    created_at REAL DEFAULT (strftime('%s', 'now'))
                )
            """)
            
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_missed_symbol 
                ON missed_opportunities(symbol, cross_time)
            """)
            
            # Rejection outcome tracking (NEW) — did rejected crosses later become profitable?
            db.execute("""
                CREATE TABLE IF NOT EXISTS rejection_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    cross_time REAL NOT NULL,
                    cross_price REAL NOT NULL,
                    rejection_stage TEXT NOT NULL,
                    rejection_reason TEXT,
                    price_1h_after REAL,
                    price_4h_after REAL,
                    price_24h_after REAL,
                    move_1h_pct REAL,
                    move_4h_pct REAL,
                    move_24h_pct REAL,
                    would_have_been_win INTEGER,
                    max_favorable_after REAL,
                    max_adverse_after REAL,
                    created_at REAL DEFAULT (strftime('%s', 'now'))
                )
            """)
            
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_rejection_outcome_symbol 
                ON rejection_outcomes(symbol, cross_time)
            """)
            
            db.commit()
            db.close()
        except Exception as e:
            logger.warning("CrossLogger DB init failed: {}", e)
    
    def record_cross(
        self,
        symbol: str,
        side: str,
        cross_price: float,
        cross_time: float,
        ema20: float = 0,
        ema50: float = 0,
        ema20_slope: float = 0,
        ema50_slope: float = 0,
        atr: float = 0,
        volume_ratio: float = 0,
        candle_body_ratio: float = 0,
        cross_quality_score: float = 0,
        regime: str = "",
    ):
        """Record an EMA crossover event."""
        try:
            # Store in memory for later signal matching
            self._active_crosses[symbol] = {
                "side": side,
                "cross_time": cross_time,
                "cross_price": cross_price,
                "ema20_slope": ema20_slope,
                "ema50_slope": ema50_slope,
                "volume_ratio": volume_ratio,
                "candle_body_ratio": candle_body_ratio,
                "cross_quality_score": cross_quality_score,
                "regime": regime,
            }
            
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                INSERT INTO cross_events 
                (symbol, side, cross_time, cross_price, ema20_at_cross, ema50_at_cross,
                 ema20_slope, ema50_slope, atr_at_cross, volume_ratio, 
                 candle_body_ratio, cross_quality_score, regime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, side, cross_time, cross_price, ema20, ema50,
                ema20_slope, ema50_slope, atr, volume_ratio,
                candle_body_ratio, cross_quality_score, regime,
            ))
            db.commit()
            db.close()
            
            logger.debug(
                "📊 CROSS_LOG: {} {} at {:.6f} quality={:.2f}",
                symbol, side, cross_price, cross_quality_score
            )
        except Exception as e:
            logger.debug("CrossLogger record_cross failed: {}", e)
    
    def record_signal(
        self,
        symbol: str,
        signal_price: float,
        signal_time: float,
    ):
        """Record when a signal is generated for a symbol with an active cross."""
        try:
            cross = self._active_crosses.get(symbol)
            if not cross:
                return  # No active cross to match
            
            # Calculate metrics
            delay_seconds = signal_time - cross["cross_time"]
            # Approximate candles (assuming 5m default)
            delay_candles = max(0, int(delay_seconds / 300))
            distance_pct = abs(signal_price - cross["cross_price"]) / cross["cross_price"] * 100 if cross["cross_price"] > 0 else 0
            
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                UPDATE cross_events SET
                    signal_time = ?,
                    signal_price = ?,
                    entry_delay_candles = ?,
                    distance_from_cross_pct = ?
                WHERE symbol = ? AND cross_time = ?
                AND signal_time IS NULL
            """, (
                signal_time, signal_price, delay_candles, distance_pct,
                symbol, cross["cross_time"],
            ))
            db.commit()
            db.close()
            
            logger.debug(
                "📊 SIGNAL_LOG: {} signal at {:.6f} delay={} candles distance={:.2f}%",
                symbol, signal_price, delay_candles, distance_pct
            )
        except Exception as e:
            logger.debug("CrossLogger record_signal failed: {}", e)
    
    def record_outcome(
        self,
        symbol: str,
        outcome: str,
        pnl: float = 0,
        r_multiple: float = 0,
        mfe_pct: float = 0,
        mae_pct: float = 0,
        exit_reason: str = "",
    ):
        """Record trade outcome for the most recent cross event."""
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                UPDATE cross_events SET
                    outcome = ?,
                    pnl = ?,
                    r_multiple = ?,
                    mfe_pct = ?,
                    mae_pct = ?,
                    exit_reason = ?
                WHERE symbol = ? 
                AND outcome IS NULL
                ORDER BY cross_time DESC
                LIMIT 1
            """, (outcome, pnl, r_multiple, mfe_pct, mae_pct, exit_reason, symbol))
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("CrossLogger record_outcome failed: {}", e)
    
    def record_rejection_outcome(
        self,
        symbol: str,
        side: str,
        cross_time: float,
        cross_price: float,
        rejection_stage: str,
        rejection_reason: str = "",
        price_1h_after: float = 0,
        price_4h_after: float = 0,
        price_24h_after: float = 0,
        max_favorable_after: float = 0,
        max_adverse_after: float = 0,
    ):
        """Record what happened after a cross was rejected.
        
        This answers: "Would this rejected cross have been profitable?"
        """
        try:
            move_1h = (price_1h_after - cross_price) / cross_price * 100 if cross_price > 0 and price_1h_after > 0 else 0
            move_4h = (price_4h_after - cross_price) / cross_price * 100 if cross_price > 0 and price_4h_after > 0 else 0
            move_24h = (price_24h_after - cross_price) / cross_price * 100 if cross_price > 0 and price_24h_after > 0 else 0
            
            # Would it have been a win? (favorable move > adverse move)
            would_win = 1 if max_favorable_after > max_adverse_after and max_favorable_after > 0 else 0
            
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                INSERT INTO rejection_outcomes
                (symbol, side, cross_time, cross_price, rejection_stage, rejection_reason,
                 price_1h_after, price_4h_after, price_24h_after,
                 move_1h_pct, move_4h_pct, move_24h_pct,
                 would_have_been_win, max_favorable_after, max_adverse_after)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, side, cross_time, cross_price, rejection_stage, rejection_reason,
                price_1h_after, price_4h_after, price_24h_after,
                move_1h, move_4h, move_24h,
                would_win, max_favorable_after, max_adverse_after,
            ))
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("CrossLogger record_rejection_outcome failed: {}", e)
    
    def get_false_positive_analysis(self) -> Dict[str, Any]:
        """Analyze losing trades — why did they lose?"""
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            # Losing trades analysis
            losers = db.execute("""
                SELECT symbol, side, cross_quality_score, entry_delay_candles,
                       distance_from_cross_pct, mfe_pct, mae_pct, exit_reason,
                       pnl, r_multiple
                FROM cross_events
                WHERE outcome = 'loss'
            """).fetchall()
            
            result["total_losses"] = len(losers)
            
            if losers:
                # Average MFE/MAE for losers
                avg_mfe = sum(r["mfe_pct"] or 0 for r in losers) / len(losers)
                avg_mae = sum(r["mae_pct"] or 0 for r in losers) / len(losers)
                result["avg_mfe_losers"] = round(avg_mfe, 2)
                result["avg_mae_losers"] = round(avg_mae, 2)
                
                # Loss reason breakdown
                exit_reasons = {}
                for r in losers:
                    reason = r["exit_reason"] or "unknown"
                    exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
                result["exit_reasons"] = exit_reasons
                
                # Average delay for losers vs winners
                avg_delay_loss = sum(r["entry_delay_candles"] or 0 for r in losers) / len(losers)
                result["avg_delay_losers"] = round(avg_delay_loss, 1)
                
                winners = db.execute("""
                    SELECT entry_delay_candles, mfe_pct, mae_pct
                    FROM cross_events WHERE outcome = 'win'
                """).fetchall()
                if winners:
                    avg_delay_win = sum(r["entry_delay_candles"] or 0 for r in winners) / len(winners)
                    result["avg_delay_winners"] = round(avg_delay_win, 1)
                
                # Quality score for losers vs winners
                avg_quality_loss = sum(r["cross_quality_score"] or 0 for r in losers) / len(losers)
                result["avg_quality_losers"] = round(avg_quality_loss, 1)
                
                if winners:
                    avg_quality_win = sum(db.execute("SELECT cross_quality_score FROM cross_events WHERE outcome='win'").fetchone()[0] or 0 for _ in winners) / len(winners)
                    result["avg_quality_winners"] = round(avg_quality_win, 1)
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger false positive analysis failed: {}", e)
            return {}
    
    def record_rejection(
        self,
        symbol: str,
        side: str,
        cross_time: float,
        cross_price: float,
        stage: str,
        reason: str,
        candle_time: float = 0,
        candle_price: float = 0,
        filter_value: float = 0,
        threshold_value: float = 0,
        regime: str = "",
    ):
        """Record why a crossover was rejected at a specific stage.
        
        Stages: pullback, confirmation, volume, confidence, cooldown, 
                distance, age, regime, mtf, risk_reward
        """
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                INSERT INTO cross_rejections
                (symbol, side, cross_time, cross_price, rejection_stage,
                 rejection_reason, candle_time, candle_price,
                 filter_value, threshold_value, regime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, side, cross_time, cross_price, stage, reason,
                candle_time, candle_price, filter_value, threshold_value, regime,
            ))
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("CrossLogger record_rejection failed: {}", e)
    
    def record_filter_latency(
        self,
        symbol: str,
        cross_time: float,
        stage: str,
        stage_time: float,
        stage_price: float,
        delay_candles: int,
        passed: bool = True,
    ):
        """Record when each filter stage was evaluated.
        
        This creates a timeline showing which stage introduced delay.
        """
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                INSERT INTO filter_latency
                (symbol, cross_time, stage, stage_time, stage_price,
                 delay_candles, passed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, cross_time, stage, stage_time, stage_price,
                delay_candles, 1 if passed else 0,
            ))
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("CrossLogger record_filter_latency failed: {}", e)
    
    def record_earliest_entry(
        self,
        symbol: str,
        cross_time: float,
        earliest_price: float,
        earliest_time: float,
        actual_price: float,
        cross_price: float,
        trend_high: float = 0,
        trend_low: float = 0,
    ):
        """Record earliest valid entry and compute cross efficiency.
        
        Cross Efficiency = (Entry - Cross) / (Trend - Cross) × 100
        Lost Distance = (Actual Entry - Earliest Valid Entry) / Cross Price × 100
        """
        try:
            # Compute metrics
            lost_distance = abs(actual_price - earliest_price) / cross_price * 100 if cross_price > 0 else 0
            
            # Cross efficiency: how much of the trend was captured
            cross_efficiency = 0
            if trend_high > 0 and trend_low > 0 and cross_price > 0:
                trend_range = abs(trend_high - trend_low)
                if trend_range > 0:
                    captured = abs(actual_price - cross_price)
                    cross_efficiency = (captured / trend_range) * 100
            
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                UPDATE cross_events SET
                    earliest_valid_entry = ?,
                    earliest_valid_time = ?,
                    lost_distance_pct = ?,
                    cross_efficiency_pct = ?,
                    trend_high = ?,
                    trend_low = ?
                WHERE symbol = ? AND cross_time = ?
                AND earliest_valid_entry IS NULL
            """, (
                earliest_price, earliest_time, lost_distance, cross_efficiency,
                trend_high, trend_low, symbol, cross_time,
            ))
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("CrossLogger record_earliest_entry failed: {}", e)
    
    def get_rejection_analysis(self) -> Dict[str, Any]:
        """Analyze rejection reasons across all crosses."""
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            # Rejections by stage
            by_stage = db.execute("""
                SELECT rejection_stage, COUNT(*) as cnt
                FROM cross_rejections
                GROUP BY rejection_stage
                ORDER BY cnt DESC
            """).fetchall()
            result["by_stage"] = [{"stage": r["rejection_stage"], "count": r["cnt"]} for r in by_stage]
            
            # Rejections by reason within each stage
            by_reason = db.execute("""
                SELECT rejection_stage, rejection_reason, COUNT(*) as cnt
                FROM cross_rejections
                GROUP BY rejection_stage, rejection_reason
                ORDER BY rejection_stage, cnt DESC
            """).fetchall()
            result["by_reason"] = [
                {"stage": r["rejection_stage"], "reason": r["rejection_reason"], "count": r["cnt"]}
                for r in by_reason
            ]
            
            # Total crosses vs signals vs rejections
            total_crosses = db.execute("SELECT COUNT(DISTINCT symbol || cross_time) FROM cross_events").fetchone()[0]
            total_signals = db.execute("SELECT COUNT(*) FROM cross_events WHERE signal_time IS NOT NULL").fetchone()[0]
            total_rejections = db.execute("SELECT COUNT(*) FROM cross_rejections").fetchone()[0]
            result["summary"] = {
                "total_crosses": total_crosses,
                "signals_generated": total_signals,
                "total_rejections": total_rejections,
                "conversion_rate": total_signals / total_crosses * 100 if total_crosses > 0 else 0,
            }
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger rejection analysis failed: {}", e)
            return {}
    
    def get_latency_analysis(self) -> Dict[str, Any]:
        """Analyze filter latency timelines."""
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            # Average delay per stage
            by_stage = db.execute("""
                SELECT stage, 
                       AVG(delay_candles) as avg_delay,
                       MAX(delay_candles) as max_delay,
                       COUNT(*) as evaluations,
                       SUM(passed) as passes
                FROM filter_latency
                GROUP BY stage
                ORDER BY AVG(delay_candles) DESC
            """).fetchall()
            result["by_stage"] = [
                {
                    "stage": r["stage"],
                    "avg_delay": round(r["avg_delay"], 1),
                    "max_delay": r["max_delay"],
                    "evaluations": r["evaluations"],
                    "passes": r["passes"],
                    "pass_rate": r["passes"] / r["evaluations"] * 100 if r["evaluations"] > 0 else 0,
                }
                for r in by_stage
            ]
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger latency analysis failed: {}", e)
            return {}
    
    def get_efficiency_analysis(self) -> Dict[str, Any]:
        """Analyze cross efficiency metrics."""
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            # Overall efficiency stats
            stats = db.execute("""
                SELECT 
                    AVG(cross_efficiency_pct) as avg_efficiency,
                    AVG(lost_distance_pct) as avg_lost,
                    AVG(earliest_valid_entry) as avg_earliest,
                    COUNT(*) as total
                FROM cross_events
                WHERE cross_efficiency_pct IS NOT NULL
            """).fetchone()
            
            if stats and stats["total"] > 0:
                result["avg_efficiency"] = round(stats["avg_efficiency"] or 0, 1)
                result["avg_lost_distance"] = round(stats["avg_lost"] or 0, 2)
                result["total_with_efficiency"] = stats["total"]
            
            # Efficiency by outcome
            by_outcome = db.execute("""
                SELECT outcome,
                       AVG(cross_efficiency_pct) as avg_eff,
                       AVG(lost_distance_pct) as avg_lost,
                       COUNT(*) as cnt
                FROM cross_events
                WHERE cross_efficiency_pct IS NOT NULL AND outcome IS NOT NULL
                GROUP BY outcome
            """).fetchall()
            result["by_outcome"] = [
                {
                    "outcome": r["outcome"],
                    "avg_efficiency": round(r["avg_eff"] or 0, 1),
                    "avg_lost": round(r["avg_lost"] or 0, 2),
                    "count": r["cnt"],
                }
                for r in by_outcome
            ]
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger efficiency analysis failed: {}", e)
            return {}
    
    def get_analysis(self) -> Dict[str, Any]:
        """Get analysis of cross events for reporting."""
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            # Total crosses
            total = db.execute("SELECT COUNT(*) FROM cross_events").fetchone()[0]
            result["total_crosses"] = total
            
            # Crosses with signals
            with_signal = db.execute(
                "SELECT COUNT(*) FROM cross_events WHERE signal_time IS NOT NULL"
            ).fetchone()[0]
            result["crosses_with_signals"] = with_signal
            
            # Crosses with outcomes
            with_outcome = db.execute(
                "SELECT COUNT(*) FROM cross_events WHERE outcome IS NOT NULL"
            ).fetchone()[0]
            result["crosses_with_outcomes"] = with_outcome
            
            # Win rate by delay bucket
            delay_buckets = db.execute("""
                SELECT 
                    CASE 
                        WHEN entry_delay_candles <= 2 THEN '0-2'
                        WHEN entry_delay_candles <= 5 THEN '3-5'
                        WHEN entry_delay_candles <= 10 THEN '6-10'
                        ELSE '10+'
                    END as bucket,
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
                    AVG(r_multiple) as avg_r
                FROM cross_events
                WHERE outcome IS NOT NULL
                GROUP BY bucket
            """).fetchall()
            result["by_delay"] = [
                {
                    "bucket": r["bucket"],
                    "total": r["total"],
                    "wins": r["wins"],
                    "win_rate": r["wins"] / r["total"] * 100 if r["total"] > 0 else 0,
                    "avg_r": r["avg_r"] or 0,
                }
                for r in delay_buckets
            ]
            
            # Win rate by distance bucket
            distance_buckets = db.execute("""
                SELECT 
                    CASE 
                        WHEN distance_from_cross_pct <= 1.0 THEN '0-1%'
                        WHEN distance_from_cross_pct <= 3.0 THEN '1-3%'
                        WHEN distance_from_cross_pct <= 5.0 THEN '3-5%'
                        ELSE '5%+'
                    END as bucket,
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
                    AVG(r_multiple) as avg_r
                FROM cross_events
                WHERE outcome IS NOT NULL AND distance_from_cross_pct IS NOT NULL
                GROUP BY bucket
            """).fetchall()
            result["by_distance"] = [
                {
                    "bucket": r["bucket"],
                    "total": r["total"],
                    "wins": r["wins"],
                    "win_rate": r["wins"] / r["total"] * 100 if r["total"] > 0 else 0,
                    "avg_r": r["avg_r"] or 0,
                }
                for r in distance_buckets
            ]
            
            # Win rate by quality score
            quality_buckets = db.execute("""
                SELECT 
                    CASE 
                        WHEN cross_quality_score >= 80 THEN '80-100'
                        WHEN cross_quality_score >= 60 THEN '60-80'
                        WHEN cross_quality_score >= 40 THEN '40-60'
                        ELSE '0-40'
                    END as bucket,
                    COUNT(*) as total,
                    SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
                    AVG(r_multiple) as avg_r
                FROM cross_events
                WHERE outcome IS NOT NULL AND cross_quality_score IS NOT NULL
                GROUP BY bucket
            """).fetchall()
            result["by_quality"] = [
                {
                    "bucket": r["bucket"],
                    "total": r["total"],
                    "wins": r["wins"],
                    "win_rate": r["wins"] / r["total"] * 100 if r["total"] > 0 else 0,
                    "avg_r": r["avg_r"] or 0,
                }
                for r in quality_buckets
            ]
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger analysis failed: {}", e)
            return {}
    
    def get_rejection_outcome_analysis(self) -> Dict[str, Any]:
        """Analyze what happened after crosses were rejected.
        
        Answers: "Would rejected crosses have been profitable?"
        """
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            total = db.execute("SELECT COUNT(*) FROM rejection_outcomes").fetchone()[0]
            result["total_tracked"] = total
            
            if total == 0:
                db.close()
                return result
            
            # By rejection stage
            by_stage = db.execute("""
                SELECT rejection_stage,
                       COUNT(*) as total,
                       SUM(would_have_been_win) as would_win,
                       AVG(move_1h_pct) as avg_move_1h,
                       AVG(move_4h_pct) as avg_move_4h,
                       AVG(move_24h_pct) as avg_move_24h,
                       AVG(max_favorable_after) as avg_mfe,
                       AVG(max_adverse_after) as avg_mae
                FROM rejection_outcomes
                GROUP BY rejection_stage
                ORDER BY total DESC
            """).fetchall()
            
            result["by_stage"] = [
                {
                    "stage": r["rejection_stage"],
                    "total": r["total"],
                    "would_have_won": r["would_win"] or 0,
                    "win_rate_if_taken": round((r["would_win"] or 0) / r["total"] * 100, 1),
                    "avg_move_1h": round(r["avg_move_1h"] or 0, 2),
                    "avg_move_4h": round(r["avg_move_4h"] or 0, 2),
                    "avg_move_24h": round(r["avg_move_24h"] or 0, 2),
                    "avg_mfe": round(r["avg_mfe"] or 0, 2),
                    "avg_mae": round(r["avg_mae"] or 0, 2),
                }
                for r in by_stage
            ]
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger rejection outcome analysis failed: {}", e)
            return {}
    
    def record_missed_opportunity(
        self,
        symbol: str,
        side: str,
        cross_time: float,
        cross_price: float,
        rejection_stage: str = "",
        atr: float = 0,
    ):
        """Record a cross that was rejected (potential missed opportunity)."""
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                INSERT INTO missed_opportunities
                (symbol, side, cross_time, cross_price, rejection_stage)
                VALUES (?, ?, ?, ?, ?)
            """, (symbol, side, cross_time, cross_price, rejection_stage))
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("CrossLogger record_missed_opportunity failed: {}", e)
    
    def update_missed_outcome(
        self,
        symbol: str,
        cross_time: float,
        max_favorable: float,
        max_adverse: float,
        atr: float = 0,
    ):
        """Update a missed opportunity with outcome data (called periodically)."""
        try:
            moved_1atr = 1 if atr > 0 and max_favorable >= atr else 0
            moved_2atr = 1 if atr > 0 and max_favorable >= 2 * atr else 0
            moved_3atr = 1 if atr > 0 and max_favorable >= 3 * atr else 0
            moved_5atr = 1 if atr > 0 and max_favorable >= 5 * atr else 0
            
            outcome = "favorable" if max_favorable > max_adverse else "adverse"
            
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                UPDATE missed_opportunities SET
                    final_outcome = ?,
                    max_adverse = ?,
                    max_favorable = ?,
                    moved_1atr = ?,
                    moved_2atr = ?,
                    moved_3atr = ?,
                    moved_5atr = ?
                WHERE symbol = ? AND cross_time = ?
                AND final_outcome IS NULL
            """, (
                outcome, max_adverse, max_favorable,
                moved_1atr, moved_2atr, moved_3atr, moved_5atr,
                symbol, cross_time,
            ))
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("CrossLogger update_missed_outcome failed: {}", e)
    
    def get_missed_opportunity_analysis(self) -> Dict[str, Any]:
        """Analyze missed opportunities — crosses that never became signals."""
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            # Total missed
            total = db.execute("SELECT COUNT(*) FROM missed_opportunities").fetchone()[0]
            result["total_missed"] = total
            
            # By rejection stage
            by_stage = db.execute("""
                SELECT rejection_stage, COUNT(*) as cnt
                FROM missed_opportunities
                WHERE rejection_stage IS NOT NULL
                GROUP BY rejection_stage
                ORDER BY cnt DESC
            """).fetchall()
            result["by_stage"] = [{"stage": r["rejection_stage"], "count": r["cnt"]} for r in by_stage]
            
            # How many moved X ATR after rejection
            atr_stats = db.execute("""
                SELECT 
                    SUM(moved_1atr) as moved_1,
                    SUM(moved_2atr) as moved_2,
                    SUM(moved_3atr) as moved_3,
                    SUM(moved_5atr) as moved_5,
                    COUNT(*) as total
                FROM missed_opportunities
                WHERE moved_1atr IS NOT NULL
            """).fetchone()
            
            if atr_stats and atr_stats["total"] > 0:
                result["moved_1atr"] = atr_stats["moved_1"] or 0
                result["moved_2atr"] = atr_stats["moved_2"] or 0
                result["moved_3atr"] = atr_stats["moved_3"] or 0
                result["moved_5atr"] = atr_stats["moved_5"] or 0
                result["total_with_outcome"] = atr_stats["total"]
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger missed analysis failed: {}", e)
            return {}
    
    def get_symbol_efficiency(self) -> Dict[str, Any]:
        """Analyze per-symbol entry efficiency and performance."""
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            # Per-symbol stats for completed trades
            by_symbol = db.execute("""
                SELECT symbol,
                       COUNT(*) as trades,
                       AVG(entry_delay_candles) as avg_delay,
                       AVG(distance_from_cross_pct) as avg_distance,
                       AVG(cross_efficiency_pct) as avg_efficiency,
                       SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
                       AVG(r_multiple) as avg_r
                FROM cross_events
                WHERE outcome IS NOT NULL
                GROUP BY symbol
                HAVING trades >= 2
                ORDER BY avg_r DESC
            """).fetchall()
            
            result["by_symbol"] = [
                {
                    "symbol": r["symbol"],
                    "trades": r["trades"],
                    "avg_delay": round(r["avg_delay"] or 0, 1),
                    "avg_distance": round(r["avg_distance"] or 0, 2),
                    "avg_efficiency": round(r["avg_efficiency"] or 0, 1),
                    "win_rate": round(r["wins"] / r["trades"] * 100, 1) if r["trades"] > 0 else 0,
                    "avg_r": round(r["avg_r"] or 0, 2),
                }
                for r in by_symbol
            ]
            
            # Per-symbol missed opportunities
            missed_by_symbol = db.execute("""
                SELECT symbol,
                       COUNT(*) as missed,
                       SUM(moved_2atr) as moved_2atr,
                       SUM(moved_3atr) as moved_3atr
                FROM missed_opportunities
                WHERE moved_1atr IS NOT NULL
                GROUP BY symbol
                HAVING missed >= 2
                ORDER BY missed DESC
            """).fetchall()
            
            result["missed_by_symbol"] = [
                {
                    "symbol": r["symbol"],
                    "missed": r["missed"],
                    "moved_2atr": r["moved_2atr"] or 0,
                    "moved_3atr": r["moved_3atr"] or 0,
                }
                for r in missed_by_symbol
            ]
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger symbol efficiency failed: {}", e)
            return {}
    
    def get_decision_matrix(self) -> Dict[str, Any]:
        """Generate a Decision Matrix: which filters help vs hurt performance.
        
        Uses R-multiple (expectancy) as the primary metric.
        For each filter (rejection stage), this reports:
          - How often it rejected candidates
          - How many of those rejected candidates later became profitable
          - How many later failed
          - The simulated R-multiple if those trades had been taken
          - Confidence interval estimate
        
        This answers: "Is each filter contributing to edge or destroying it?"
        """
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            # Check if we have enough data
            total_outcomes = db.execute("SELECT COUNT(*) FROM rejection_outcomes").fetchone()[0]
            if total_outcomes == 0:
                db.close()
                return {"total_outcomes": 0, "filters": []}
            
            # For each rejection stage, compute:
            #   total_rejected, would_win, would_lose, simulated R-multiple
            filters = db.execute("""
                SELECT rejection_stage,
                       COUNT(*) as total,
                       SUM(would_have_been_win) as would_win,
                       AVG(move_24h_pct) as avg_move_24h,
                       AVG(max_favorable_after) as avg_mfe,
                       AVG(max_adverse_after) as avg_mae,
                       -- Simulated R: wins * 1R - losses * 0.5R (conservative)
                       (SUM(CASE WHEN would_have_been_win = 1 THEN 1 ELSE 0 END) * 1.0 -
                        SUM(CASE WHEN would_have_been_win = 0 THEN 0.5 ELSE 0 END)) as simulated_r_total,
                       -- Std dev for confidence interval
                       AVG(CASE WHEN would_have_been_win = 1 THEN max_favorable_after ELSE -max_adverse_after END) as avg_outcome,
                       -- Variance approximation for CI
                       SUM(
                           (CASE WHEN would_have_been_win = 1 THEN max_favorable_after ELSE -max_adverse_after END -
                            (SELECT AVG(CASE WHEN would_have_been_win = 1 THEN max_favorable_after ELSE -max_adverse_after END)
                             FROM rejection_outcomes r2 WHERE r2.rejection_stage = rejection_outcomes.rejection_stage)
                           ) * 
                           (CASE WHEN would_have_been_win = 1 THEN max_favorable_after ELSE -max_adverse_after END -
                            (SELECT AVG(CASE WHEN would_have_been_win = 1 THEN max_favorable_after ELSE -max_adverse_after END)
                             FROM rejection_outcomes r2 WHERE r2.rejection_stage = rejection_outcomes.rejection_stage)
                           )
                       ) as variance_sum
                FROM rejection_outcomes
                GROUP BY rejection_stage
                HAVING total >= 3
                ORDER BY total DESC
            """).fetchall()
            
            filter_list = []
            for f in filters:
                total = f["total"]
                would_win = f["would_win"] or 0
                would_lose = total - would_win
                win_rate = would_win / total * 100 if total > 0 else 0
                
                # Simulated R-multiple per trade
                simulated_r_per_trade = f["simulated_r_total"] / total if total > 0 else 0
                
                # Standard error for confidence interval
                import math
                variance = (f["variance_sum"] or 0) / total if total > 1 else 0
                std_err = math.sqrt(variance) / math.sqrt(total) if total > 1 else 0
                
                filter_list.append({
                    "stage": f["rejection_stage"],
                    "total_rejected": total,
                    "would_win": would_win,
                    "would_lose": would_lose,
                    "win_rate_if_taken": round(win_rate, 1),
                    "avg_move_24h": round(f["avg_move_24h"] or 0, 2),
                    "simulated_r_per_trade": round(simulated_r_per_trade, 3),
                    "std_err": round(std_err, 3),
                    "sample_size": total,
                })
            
            result["total_outcomes"] = total_outcomes
            result["filters"] = filter_list
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger decision matrix failed: {}", e)
            return {}
    
    def get_filter_interaction_matrix(self) -> Dict[str, Any]:
        """Evaluate combinations of filters to find interactions.
        
        For each unique pair of rejection stages, reports:
          - Trades rejected by both filters
          - Trades rejected by only one
          - Performance comparison
          - Whether filters are redundant or complementary
        
        This answers: "Which filter combinations add the most value?"
        """
        try:
            db = sqlite3.connect(str(self._db_path), timeout=10)
            db.row_factory = sqlite3.Row
            
            result = {}
            
            total_outcomes = db.execute("SELECT COUNT(*) FROM rejection_outcomes").fetchone()[0]
            if total_outcomes < 10:
                db.close()
                return {"total_outcomes": total_outcomes, "interactions": [], "note": "Need 10+ rejection outcomes"}
            
            # Get unique rejection stages
            stages = [r["rejection_stage"] for r in db.execute(
                "SELECT DISTINCT rejection_stage FROM rejection_outcomes"
            ).fetchall()]
            
            interactions = []
            
            # For each pair of stages, compute interaction metrics
            for i, stage_a in enumerate(stages):
                for stage_b in stages[i+1:]:
                    # Count crosses rejected by both (same symbol+cross_time)
                    both = db.execute("""
                        SELECT COUNT(*) as cnt
                        FROM rejection_outcomes a
                        JOIN rejection_outcomes b 
                            ON a.symbol = b.symbol AND a.cross_time = b.cross_time
                        WHERE a.rejection_stage = ? AND b.rejection_stage = ?
                    """, (stage_a, stage_b)).fetchone()["cnt"]
                    
                    if both < 2:
                        continue  # Skip pairs with insufficient overlap
                    
                    # Win rates when each would have been taken
                    wr_a = db.execute("""
                        SELECT AVG(would_have_been_win) as wr
                        FROM rejection_outcomes WHERE rejection_stage = ?
                    """, (stage_a,)).fetchone()["wr"] or 0
                    
                    wr_b = db.execute("""
                        SELECT AVG(would_have_been_win) as wr
                        FROM rejection_outcomes WHERE rejection_stage = ?
                    """, (stage_b,)).fetchone()["wr"] or 0
                    
                    # Win rate of trades rejected by both
                    wr_both = db.execute("""
                        SELECT AVG(a.would_have_been_win) as wr
                        FROM rejection_outcomes a
                        JOIN rejection_outcomes b 
                            ON a.symbol = b.symbol AND a.cross_time = b.cross_time
                        WHERE a.rejection_stage = ? AND b.rejection_stage = ?
                    """, (stage_a, stage_b)).fetchone()["wr"] or 0
                    
                    # Total rejected by each
                    total_a = db.execute(
                        "SELECT COUNT(*) FROM rejection_outcomes WHERE rejection_stage = ?",
                        (stage_a,)
                    ).fetchone()[0]
                    total_b = db.execute(
                        "SELECT COUNT(*) FROM rejection_outcomes WHERE rejection_stage = ?",
                        (stage_b,)
                    ).fetchone()[0]
                    
                    # Redundancy check: if both have similar WR to individual, filters may be redundant
                    redundancy_score = 0
                    if wr_both > 0 and wr_a > 0 and wr_b > 0:
                        # If combined rejection doesn't filter more losers, they're redundant
                        expected_wr = (wr_a + wr_b) / 2
                        redundancy_score = abs(wr_both - expected_wr)
                    
                    interactions.append({
                        "stage_a": stage_a,
                        "stage_b": stage_b,
                        "both_rejected": both,
                        "wr_stage_a": round(wr_a * 100, 1),
                        "wr_stage_b": round(wr_b * 100, 1),
                        "wr_both_rejected": round(wr_both * 100, 1),
                        "total_a": total_a,
                        "total_b": total_b,
                        "redundancy_score": round(redundancy_score, 3),
                    })
            
            result["total_outcomes"] = total_outcomes
            result["interactions"] = sorted(interactions, key=lambda x: x["both_rejected"], reverse=True)
            
            db.close()
            return result
        except Exception as e:
            logger.debug("CrossLogger filter interaction matrix failed: {}", e)
            return {"total_outcomes": 0, "interactions": []}


# Singleton
_instance: Optional[CrossLogger] = None

def get_cross_logger() -> CrossLogger:
    global _instance
    if _instance is None:
        _instance = CrossLogger()
    return _instance
