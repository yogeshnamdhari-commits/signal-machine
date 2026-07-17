"""
Performance Tracker — Strategy Version Tracking + Forward Performance + Dynamic Threshold.

1. Strategy Version Tracking — compare old vs new engine performance
2. Forward Performance Tracker — last 50 signals with WR, PF, Expectancy
3. Dynamic Confidence Threshold — adapt to market regime
4. Trade Quality Score — combine confidence + RR + regime + flow + SM
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from loguru import logger
import numpy as np


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "database" / "performance_tracker.db"

# Strategy versions
VERSION_INSTUTIONAL_V1 = "inst_v1"   # Old: 7-pillar with 85 threshold
VERSION_INSTUTIONAL_V2 = "inst_v2"   # New: calibrated + SM + dynamic threshold


# ══════════════════════════════════════════════════════════════
# DYNAMIC CONFIDENCE THRESHOLD
# ══════════════════════════════════════════════════════════════

class DynamicThresholdEngine:
    """
    DEPRECATED — kept for backward compatibility.
    Use AdaptiveThresholdEngine for market-breadth-aware thresholds.
    """

    REGIME_THRESHOLDS = {
        "trending_bull": 65,
        "trending_bear": 65,
        "breakout": 65,
        "volatile": 80,
        "range": 85,
        "compression": 90,
    }

    def get_threshold(self, regime: str, regime_confidence: float = 0.5) -> float:
        """Get dynamic threshold based on regime."""
        base = self.REGIME_THRESHOLDS.get(regime, 70)
        adjustment = (regime_confidence - 0.5) * -10
        return max(55, min(90, base + adjustment))

    def classify_signal(self, confidence: float, regime: str, regime_conf: float) -> Tuple[bool, str]:
        """Classify if signal passes dynamic threshold."""
        threshold = self.get_threshold(regime, regime_conf)
        if confidence >= threshold:
            return True, f"PASS (conf={confidence:.1f} >= threshold={threshold:.1f})"
        else:
            return False, f"REJECT (conf={confidence:.1f} < threshold={threshold:.1f})"


# ══════════════════════════════════════════════════════════════
# ADAPTIVE THRESHOLD ENGINE (Market-Breadth-Aware)
# ══════════════════════════════════════════════════════════════

class AdaptiveThresholdEngine:
    """
    Market-breadth-aware dynamic thresholds for Phase-1 and Regime gate.

    Instead of per-symbol regime lookup, this analyzes the DISTRIBUTION
    of regimes across ALL symbols to determine the overall market state,
    then sets thresholds accordingly.

    Market State          │ Phase-1 │ Regime │ Rationale
    ──────────────────────┼─────────┼────────┼──────────────────────────
    Strong Trend (>40%)   │   55    │   60   │ Trends dominate → loosen gates
    Moderate Trend (20-40)│   60    │   65   │ Mixed → standard gates
    Weak/Range (<20%)     │   50    │   55   │ Ranging → allow reversals
    Full Range (>60%)     │   50    │   55   │ Most market is flat → range mode

    Goal: Maintain 1%–5% pass rate. Avoid zero-signal situations.
    """

    # Threshold profiles per market state
    PROFILES = {
        "strong_trend":  {"phase1": 55, "regime": 60, "label": "📈 Strong Trend", "color": "#22c55e"},
        "moderate":      {"phase1": 50, "regime": 55, "label": "📊 Moderate",     "color": "#f5a623"},
        "weak_range":    {"phase1": 50, "regime": 55, "label": "↔️ Weak/Range",    "color": "#3b82f6"},
        "full_range":    {"phase1": 50, "regime": 55, "label": "↔️ Full Range",    "color": "#8b5cf6"},
        "volatile":      {"phase1": 65, "regime": 70, "label": "⚡ Volatile",      "color": "#f97316"},
    }

    def __init__(self) -> None:
        self._current_state: str = "moderate"
        self._phase1_threshold: float = 50
        self._regime_threshold: float = 55
        self._regime_distribution: Dict[str, int] = {}
        self._total_symbols: int = 0
        self._pass_rate_history: List[float] = []
        self._last_update: float = 0
        self._last_death_snapshot: Dict[str, int] = {}
        # Death report tracking
        self._death_counts: Dict[str, int] = {
            "scorer": 0, "phase1": 0, "regime": 0,
            "sweep": 0, "oi": 0, "cvd": 0, "filter": 0, "other": 0,
        }
        self._cycle_scanned: int = 0
        self._cycle_signals_emitted: int = 0

    def update_market_breadth(self, regime_distribution: Dict[str, int], total_symbols: int) -> None:
        """
        Analyze regime distribution across all symbols to determine market state.

        Parameters
        ----------
        regime_distribution : dict
            e.g. {"trending_bull": 30, "trending_bear": 15, "range": 60, "compression": 20}
        total_symbols : int
            Total number of active symbols
        """
        self._regime_distribution = regime_distribution
        self._total_symbols = total_symbols
        self._last_update = time.time()

        if total_symbols == 0:
            self._current_state = "moderate"
            return

        # Count regime categories
        trending = regime_distribution.get("trending_bull", 0) + regime_distribution.get("trending_bear", 0)
        ranging = regime_distribution.get("range", 0) + regime_distribution.get("compression", 0)
        volatile = regime_distribution.get("volatile", 0)
        breakout = regime_distribution.get("breakout", 0)

        trending_pct = trending / total_symbols
        ranging_pct = ranging / total_symbols
        volatile_pct = volatile / total_symbols

        # Determine market state
        if volatile_pct > 0.30:
            state = "volatile"
        elif trending_pct > 0.40:
            state = "strong_trend"
        elif trending_pct > 0.20:
            state = "moderate"
        elif ranging_pct > 0.60:
            state = "full_range"
        else:
            state = "weak_range"

        profile = self.PROFILES[state]
        self._current_state = state
        self._phase1_threshold = profile["phase1"]
        self._regime_threshold = profile["regime"]

        logger.info(
            "📊 ADAPTIVE THRESHOLDS: {} | Phase1={} Regime={} | Trend={:.0f}% Range={:.0f}% Vol={:.0f}%",
            profile["label"], profile["phase1"], profile["regime"],
            trending_pct * 100, ranging_pct * 100, volatile_pct * 100,
        )

    def classify_phase1(self, confidence: float, symbol_regime: str = "range",
                        regime_confidence: float = 0.5) -> Tuple[bool, str]:
        """
        Phase-1 gate with adaptive threshold.

        Uses the market-breadth threshold as baseline, then adjusts
        ±5 based on the individual symbol's regime confidence.
        """
        # Base adaptive threshold
        threshold = self._phase1_threshold

        # Micro-adjustment: high-confidence regime → slightly lower threshold
        adjustment = (regime_confidence - 0.5) * -5  # ±2.5 range
        threshold = max(45, min(80, threshold + adjustment))

        if confidence >= threshold:
            return True, f"PASS (conf={confidence:.1f} >= {threshold:.1f} [{self._current_state}])"
        else:
            return False, f"REJECT (conf={confidence:.1f} < {threshold:.1f} [{self._current_state}])"

    def classify_regime(self, confidence_100: float, raw_regime: str,
                        regime_confidence: float, signal_type: str = "trend_following") -> Tuple[bool, str]:
        """
        Regime gate with adaptive threshold + Range Reversal Mode.

        For ranging markets, lowers the regime confidence bar
        and allows mean-reversion signals.
        """
        threshold = self._regime_threshold

        # Range Reversal Mode: when market is mostly ranging,
        # be MORE lenient on regime for mean-reversion signals
        is_ranging_market = self._current_state in ("full_range", "weak_range")
        is_ranging_symbol = raw_regime in ("range", "compression")

        if is_ranging_market and is_ranging_symbol:
            # Range Reversal Mode: lower threshold significantly for reversal signals
            threshold = min(threshold, 55)
            if signal_type == "mean_reversion":
                threshold = min(threshold, 50)

        # High confidence regime → slightly more lenient
        adjustment = (regime_confidence - 0.5) * -5
        threshold = max(45, min(75, threshold + adjustment))

        if confidence_100 >= threshold:
            return True, f"PASS (conf={confidence_100:.1f} >= {threshold:.1f} [{self._current_state}])"
        else:
            return False, f"REJECT (conf={confidence_100:.1f} < {threshold:.1f} [{self._current_state}])"

    def record_death(self, stage: str) -> None:
        """Record a signal death at a specific pipeline stage."""
        self._death_counts[stage] = self._death_counts.get(stage, 0) + 1

    def record_cycle(self, scanned: int, emitted: int) -> None:
        """Record cycle totals for pass rate tracking. Saves death snapshot first."""
        self._cycle_scanned = scanned
        self._cycle_signals_emitted = emitted
        # Snapshot death counts for the report BEFORE resetting
        self._last_death_snapshot = dict(self._death_counts)
        if scanned > 0:
            rate = emitted / scanned * 100
            self._pass_rate_history.append(rate)
            self._pass_rate_history = self._pass_rate_history[-20:]
        # Reset per-cycle death counts after snapshot
        self._death_counts = {k: 0 for k in self._death_counts}

    def get_state(self) -> Dict:
        """Get current adaptive threshold state for dashboard display."""
        profile = self.PROFILES.get(self._current_state, self.PROFILES["moderate"])
        avg_pass_rate = (sum(self._pass_rate_history) / len(self._pass_rate_history)
                         if self._pass_rate_history else 0)

        return {
            "market_state": self._current_state,
            "market_state_label": profile["label"],
            "market_state_color": profile["color"],
            "phase1_threshold": self._phase1_threshold,
            "regime_threshold": self._regime_threshold,
            "regime_distribution": self._regime_distribution,
            "total_symbols": self._total_symbols,
            "pass_rate_history": self._pass_rate_history,
            "avg_pass_rate": round(avg_pass_rate, 1),
            "death_counts": dict(self._death_counts),
            "cycle_scanned": self._cycle_scanned,
            "cycle_emitted": self._cycle_signals_emitted,
        }

    def get_death_report(self) -> Dict:
        """Get the signal death report for the current/last cycle."""
        # Use snapshot (saved at record_cycle time) for the last completed cycle
        deaths = getattr(self, '_last_death_snapshot', self._death_counts)
        total_deaths = sum(deaths.values())
        top_killer = max(deaths, key=deaths.get) if deaths and any(deaths.values()) else "none"
        top_killer_count = deaths.get(top_killer, 0)

        return {
            "scanned": self._cycle_scanned,
            "emitted": self._cycle_signals_emitted,
            "total_rejected": total_deaths,
            "deaths_by_stage": dict(deaths),
            "top_killer": top_killer,
            "top_killer_count": top_killer_count,
            "pass_rate": round(self._cycle_signals_emitted / max(self._cycle_scanned, 1) * 100, 1),
        }


# ══════════════════════════════════════════════════════════════
# TRADE QUALITY SCORE
# ══════════════════════════════════════════════════════════════

class TradeQualityEngine:
    """
    Combines multiple factors into a Trade Quality Score with letter grade.
    
    Trade Quality = Confidence + RR + Regime Strength + Flow + SM Score
    """

    def compute_quality(
        self,
        confidence: float,       # 0-100
        risk_reward: float,       # e.g. 3.1
        regime_strength: float,   # 0-100
        flow_strength: float,     # 0-100
        sm_score: float,          # 0-100
    ) -> Dict:
        """Compute trade quality score and grade."""
        # Normalize RR to 0-100 scale (3.0 = 100, 1.0 = 33)
        rr_normalized = min(100, (risk_reward / 3.0) * 100) if risk_reward > 0 else 0

        # Weighted combination
        quality = (
            confidence * 0.30 +      # Confidence is primary
            rr_normalized * 0.25 +   # Risk/reward is critical
            regime_strength * 0.15 + # Regime alignment matters
            flow_strength * 0.15 +   # Flow confirms direction
            sm_score * 0.15          # Smart Money backing
        )

        # Grade assignment
        if quality >= 85:
            grade = "A+"
        elif quality >= 75:
            grade = "A"
        elif quality >= 65:
            grade = "B"
        elif quality >= 50:
            grade = "C"
        else:
            grade = "REJECT"

        return {
            "quality_score": round(quality, 1),
            "grade": grade,
            "confidence_contribution": round(confidence * 0.30, 1),
            "rr_contribution": round(rr_normalized * 0.25, 1),
            "regime_contribution": round(regime_strength * 0.15, 1),
            "flow_contribution": round(flow_strength * 0.15, 1),
            "sm_contribution": round(sm_score * 0.15, 1),
        }


# ══════════════════════════════════════════════════════════════
# FORWARD PERFORMANCE TRACKER
# ══════════════════════════════════════════════════════════════

class ForwardPerformanceTracker:
    """
    Tracks performance of signals from the NEW engine only.
    Shows: Last 50 signals, WR, PF, Expectancy.
    """

    def __init__(self) -> None:
        self._init_db()

    def _init_db(self) -> None:
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.execute("""
                CREATE TABLE IF NOT EXISTS signal_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    side TEXT,
                    regime TEXT,
                    confidence REAL,
                    institutional_score REAL,
                    sm_score REAL,
                    trade_quality REAL,
                    quality_grade TEXT,
                    risk_reward REAL,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    outcome TEXT DEFAULT 'pending',
                    pnl REAL DEFAULT 0,
                    r_multiple REAL DEFAULT 0,
                    version TEXT DEFAULT 'inst_v2',
                    timestamp REAL,
                    closed_at REAL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_perf_version 
                ON signal_performance(version)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_perf_outcome 
                ON signal_performance(outcome)
            """)
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("PerformanceTracker DB init failed: {}", e)

    def record_signal(
        self,
        symbol: str,
        side: str,
        regime: str,
        confidence: float,
        institutional_score: float,
        sm_score: float,
        trade_quality: float,
        quality_grade: str,
        risk_reward: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        version: str = VERSION_INSTUTIONAL_V2,
    ) -> None:
        """Record a signal for performance tracking."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.execute(
                """INSERT INTO signal_performance 
                   (symbol, side, regime, confidence, institutional_score,
                    sm_score, trade_quality, quality_grade, risk_reward,
                    entry_price, stop_loss, take_profit, version, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol, side, regime, confidence, institutional_score,
                 sm_score, trade_quality, quality_grade, risk_reward,
                 entry_price, stop_loss, take_profit, version, time.time()),
            )
            db.commit()
            db.close()
        except Exception:
            pass

    def record_outcome(
        self, symbol: str, side: str, pnl: float, r_multiple: float
    ) -> None:
        """Record trade outcome."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            row = db.execute(
                """SELECT id FROM signal_performance 
                   WHERE symbol=? AND side=? AND outcome='pending'
                   ORDER BY timestamp DESC LIMIT 1""",
                (symbol, side),
            ).fetchone()
            if row:
                outcome = "win" if pnl > 0 else "loss"
                db.execute(
                    "UPDATE signal_performance SET outcome=?, pnl=?, r_multiple=?, closed_at=? WHERE id=?",
                    (outcome, pnl, r_multiple, time.time(), row[0]),
                )
                db.commit()
            db.close()
        except Exception:
            pass

    def get_forward_stats(self, version: str = VERSION_INSTUTIONAL_V2, limit: int = 50) -> Dict:
        """Get performance stats for the new engine."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """SELECT * FROM signal_performance 
                   WHERE version=? AND outcome != 'pending'
                   ORDER BY timestamp DESC LIMIT ?""",
                (version, limit),
            ).fetchall()
            db.close()

            if not rows:
                return {
                    "total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "profit_factor": 0, "expectancy": 0, "avg_rr": 0,
                    "avg_confidence": 0, "avg_quality": 0,
                    "by_grade": {}, "recent": [],
                }

            total = len(rows)
            wins = sum(1 for r in rows if r["outcome"] == "win")
            losses = total - wins
            win_rate = wins / total * 100 if total > 0 else 0

            gross_wins = sum(r["pnl"] for r in rows if r["pnl"] > 0)
            gross_losses = abs(sum(r["pnl"] for r in rows if r["pnl"] <= 0))
            profit_factor = gross_wins / gross_losses if gross_losses > 0 else 999

            expectancy = np.mean([r["pnl"] for r in rows])
            avg_rr = np.mean([r["r_multiple"] for r in rows])
            avg_conf = np.mean([r["confidence"] for r in rows])
            avg_quality = np.mean([r["trade_quality"] for r in rows])

            # Grade breakdown
            by_grade = {}
            for r in rows:
                g = r["quality_grade"]
                if g not in by_grade:
                    by_grade[g] = {"count": 0, "wins": 0, "pnl": 0}
                by_grade[g]["count"] += 1
                if r["outcome"] == "win":
                    by_grade[g]["wins"] += 1
                by_grade[g]["pnl"] += r["pnl"]

            # Recent 10
            recent = []
            for r in rows[:10]:
                recent.append({
                    "symbol": r["symbol"], "side": r["side"],
                    "confidence": round(r["confidence"], 1),
                    "grade": r["quality_grade"],
                    "outcome": r["outcome"],
                    "pnl": round(r["pnl"], 2),
                    "r_multiple": round(r["r_multiple"], 2),
                })

            db.close()
            return {
                "total": total, "wins": wins, "losses": losses,
                "win_rate": round(win_rate, 1),
                "profit_factor": round(profit_factor, 2),
                "expectancy": round(expectancy, 2),
                "avg_rr": round(avg_rr, 2),
                "avg_confidence": round(avg_conf, 1),
                "avg_quality": round(avg_quality, 1),
                "by_grade": by_grade,
                "recent": recent,
            }
        except Exception:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0}

    def get_version_comparison(self) -> Dict:
        """Compare old vs new engine performance."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            result = {}
            for ver in [VERSION_INSTUTIONAL_V1, VERSION_INSTUTIONAL_V2]:
                rows = db.execute(
                    """SELECT * FROM signal_performance 
                       WHERE version=? AND outcome != 'pending'""",
                    (ver,),
                ).fetchall()
                if rows:
                    total = len(rows)
                    wins = sum(1 for r in rows if r["outcome"] == "win")
                    gross_wins = sum(r["pnl"] for r in rows if r["pnl"] > 0)
                    gross_losses = abs(sum(r["pnl"] for r in rows if r["pnl"] <= 0))
                    result[ver] = {
                        "total": total,
                        "win_rate": round(wins / total * 100, 1),
                        "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else 999,
                        "expectancy": round(np.mean([r["pnl"] for r in rows]), 2),
                    }
                else:
                    result[ver] = {"total": 0, "win_rate": 0, "profit_factor": 0, "expectancy": 0}
            db.close()
            return result
        except Exception:
            return {}


# ══════════════════════════════════════════════════════════════
# ENGINE HEALTH MONITOR
# ══════════════════════════════════════════════════════════════

class EngineHealthMonitor:
    """
    Real-time engine health metrics for dashboard display.
    """

    def __init__(self) -> None:
        self.signals_generated_today = 0
        self.signals_rejected_today = 0
        self.elite_signals_today = 0
        self.total_confidence_sum = 0
        self.total_rr_sum = 0
        self.total_signals = 0
        self.pass_rate = 0
        self._last_reset = time.time()

    def record_generated(self, confidence: float, risk_reward: float) -> None:
        """Record a generated signal."""
        self._maybe_reset()
        self.signals_generated_today += 1
        self.total_confidence_sum += confidence
        self.total_rr_sum += risk_reward
        self.total_signals += 1

    def record_rejected(self) -> None:
        """Record a rejected signal."""
        self._maybe_reset()
        self.signals_rejected_today += 1

    def record_elite(self) -> None:
        """Record an elite signal."""
        self._maybe_reset()
        self.elite_signals_today += 1

    def _maybe_reset(self) -> None:
        """Reset daily counters at midnight."""
        now = time.time()
        if now - self._last_reset > 86400:
            self.signals_generated_today = 0
            self.signals_rejected_today = 0
            self.elite_signals_today = 0
            self.total_confidence_sum = 0
            self.total_rr_sum = 0
            self.total_signals = 0
            self._last_reset = now

    def get_health(self) -> Dict:
        """Get engine health metrics."""
        self._maybe_reset()
        avg_conf = self.total_confidence_sum / self.total_signals if self.total_signals > 0 else 0
        avg_rr = self.total_rr_sum / self.total_signals if self.total_signals > 0 else 0
        total_attempts = self.signals_generated_today + self.signals_rejected_today
        pass_rate = self.signals_generated_today / total_attempts * 100 if total_attempts > 0 else 0

        return {
            "signals_generated": self.signals_generated_today,
            "signals_rejected": self.signals_rejected_today,
            "elite_signals": self.elite_signals_today,
            "avg_confidence": round(avg_conf, 1),
            "avg_rr": round(avg_rr, 2),
            "pass_rate": round(pass_rate, 1),
            "total_attempts": total_attempts,
        }


# ══════════════════════════════════════════════════════════════
# UNIFIED PERFORMANCE ORCHESTRATOR
# ══════════════════════════════════════════════════════════════

class PerformanceOrchestrator:
    """Unified orchestrator for all performance tracking features."""

    def __init__(self) -> None:
        self.dynamic_threshold = DynamicThresholdEngine()
        self.adaptive_threshold = AdaptiveThresholdEngine()
        self.quality_engine = TradeQualityEngine()
        self.forward_tracker = ForwardPerformanceTracker()
        self.health_monitor = EngineHealthMonitor()

    def evaluate_signal(
        self,
        symbol: str,
        side: str,
        confidence: float,
        regime: str,
        regime_confidence: float,
        risk_reward: float,
        regime_strength: float,
        flow_strength: float,
        sm_score: float,
        institutional_score: float,
        entry_price: float = 0,
        stop_loss: float = 0,
        take_profit: float = 0,
    ) -> Dict:
        """Full signal evaluation with dynamic threshold and quality scoring."""

        # 1. Dynamic threshold
        passes, threshold_msg = self.dynamic_threshold.classify_signal(
            confidence, regime, regime_confidence
        )

        # 2. Trade quality score
        quality = self.quality_engine.compute_quality(
            confidence, risk_reward, regime_strength, flow_strength, sm_score
        )

        # 3. Record for health monitoring
        if passes:
            self.health_monitor.record_generated(confidence, risk_reward)
        else:
            self.health_monitor.record_rejected()

        # 4. Record for forward tracking
        if passes and quality["grade"] in ("A+", "A"):
            self.forward_tracker.record_signal(
                symbol=symbol, side=side, regime=regime,
                confidence=confidence, institutional_score=institutional_score,
                sm_score=sm_score, trade_quality=quality["quality_score"],
                quality_grade=quality["grade"], risk_reward=risk_reward,
                entry_price=entry_price, stop_loss=stop_loss,
                take_profit=take_profit,
            )
            self.health_monitor.record_elite()

        return {
            "passes_threshold": passes,
            "threshold_msg": threshold_msg,
            "quality": quality,
        }

    def get_dashboard_data(self) -> Dict:
        """Get all data needed for dashboard display."""
        return {
            "health": self.health_monitor.get_health(),
            "forward_stats": self.forward_tracker.get_forward_stats(),
            "version_comparison": self.forward_tracker.get_version_comparison(),
            "adaptive_thresholds": self.adaptive_threshold.get_state(),
            "death_report": self.adaptive_threshold.get_death_report(),
        }
