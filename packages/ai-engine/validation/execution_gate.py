#!/usr/bin/env python3
"""
Dynamic Execution Gate — Conditional Execution Layer for EMA_V5.

CODE FREEZE: NO strategy changes. This is a PURE SCORING FILTER.

Sits between Scanner and Execution. Decides:
  EXECUTE  → Send to trade execution
  WATCH    → Monitor only, no execution
  IGNORE   → Skip entirely

The signal itself NEVER changes.
Only execution quality changes.

Architecture:
  Scanner → Signal Engine → State Machine → EXECUTION GATE → Trade Execution
                                                       ↑
                                              Learning Engine
                                              (updates after every closed trade)
"""
from __future__ import annotations

import json
import time
import sqlite3
import statistics
import math
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from loguru import logger


# ══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "execution_gate.db"
BRIDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "bridge"

# Exponential decay: recent trades weighted more heavily
DECAY_FACTOR = 0.95     # 0.95^30 ≈ 0.21 — last 30 trades dominate
LOOKBACK_TRADES = 200   # Max trades to consider for scoring
MIN_TRADES_FOR_SCORE = 5  # Minimum trades before a component gets a real score

# Execution levels
EXECUTE_THRESHOLD = 80   # Elite: execute immediately
GOOD_THRESHOLD = 60      # Good: execute normal size
AVERAGE_THRESHOLD = 40   # Average: paper trade only
WEAK_THRESHOLD = 20      # Weak: watch only
# Below 20: ignore

# Position size multipliers per level
SIZE_MULTIPLIERS = {
    "EXECUTE": 1.00,
    "GOOD": 0.75,
    "AVERAGE": 0.50,
    "WEAK": 0.25,
    "IGNORE": 0.00,
}


# ══════════════════════════════════════════════════════════════════════
# EXECUTION DECISION
# ══════════════════════════════════════════════════════════════════════

@dataclass
class ExecutionDecision:
    """Output for every signal that passes through the gate."""
    decision: str = "IGNORE"         # EXECUTE, WATCH, IGNORE
    alpha_score: float = 0.0         # 0-100
    position_size_pct: float = 0.0   # 0, 25, 50, 75, 100
    expected_profit: float = 0.0     # Historical expected profit per trade
    expected_drawdown: float = 0.0   # Historical expected max DD contribution
    historical_pf: float = 0.0       # Historical profit factor for this condition
    historical_expectancy: float = 0.0
    historical_recovery: float = 0.0
    historical_sharpe: float = 0.0
    execution_reason: str = ""       # Human-readable reason
    component_scores: Dict = field(default_factory=dict)  # Score breakdown
    symbol_rank: str = "NORMAL"      # ELITE, GOOD, NORMAL, WEAK
    optimal_confidence: float = 80.0  # Current adaptive threshold
    reasons_for_trade: List[str] = field(default_factory=list)  # Audit trail

    def explain(self) -> str:
        """Generate human-readable explainable score breakdown."""
        lines = [f"Overall Score: {self.alpha_score:.0f}/100"]
        
        # Component breakdown with max weights
        component_labels = {
            "symbol_history": ("Symbol History", 15),
            "session_history": ("Session History", 10),
            "regime_history": ("Regime History", 10),
            "confidence": ("Confidence Quality", 10),
            "trend": ("Trend Alignment", 10),
            "volatility": ("Volatility", 5),
            "order_flow": ("Order Flow", 10),
            "smart_money": ("Smart Money", 10),
            "historical": ("Historical PF", 10),
            "raw_score": ("Signal Score", 10),
        }
        
        for key, (label, max_pts) in component_labels.items():
            raw = self.component_scores.get(key, 50)
            pts = round(raw / 100 * max_pts)
            lines.append(f"  {label:<25} {pts:>3}/{max_pts}")
        
        # Decision
        lines.append(f"Decision: {self.decision} ({self.position_size_pct:.0f}% size)")
        lines.append(f"Reason: {self.execution_reason}")
        lines.append(f"Symbol Rank: {self.symbol_rank}")
        
        # Reasons for trade
        if self.reasons_for_trade:
            lines.append("Reasons:")
            for r in self.reasons_for_trade:
                lines.append(f"  ✓ {r}")
        
        # Expected metrics
        if self.expected_profit > 0:
            lines.append(f"Expected Profit: ${self.expected_profit:.2f}")
        if self.expected_drawdown > 0:
            lines.append(f"Expected Drawdown: {self.expected_drawdown:.1f}%")
        if self.historical_pf > 0:
            lines.append(f"Historical PF: {self.historical_pf:.2f}")
        
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════

class GateDB:
    """SQLite persistence for execution gate state."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("PRAGMA journal_mode=WAL")

        # Component scores per dimension (updated after each trade)
        db.execute("""
            CREATE TABLE IF NOT EXISTS component_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension TEXT NOT NULL,      -- symbol, session, regime, etc.
                dimension_value TEXT NOT NULL, -- BTCUSDT, asia, BUY_MODE, etc.
                score REAL DEFAULT 50.0,       -- 0-100 adaptive score
                trade_count INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                avg_r REAL DEFAULT 0,
                profit_factor REAL DEFAULT 1.0,
                expectancy REAL DEFAULT 0,
                sharpe REAL DEFAULT 0,
                recovery_factor REAL DEFAULT 0,
                max_dd REAL DEFAULT 0,
                last_updated REAL DEFAULT 0,
                UNIQUE(dimension, dimension_value)
            )
        """)

        # Adaptive thresholds (updated continuously)
        db.execute("""
            CREATE TABLE IF NOT EXISTS adaptive_thresholds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                threshold_name TEXT UNIQUE NOT NULL,
                current_value REAL DEFAULT 0,
                last_updated REAL DEFAULT 0
            )
        """)

        # Trade log for decay calculations
        db.execute("""
            CREATE TABLE IF NOT EXISTS gate_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                session TEXT DEFAULT '',
                regime TEXT DEFAULT '',
                confidence REAL DEFAULT 0,
                alpha_score REAL DEFAULT 0,
                decision TEXT DEFAULT '',
                net_profit REAL DEFAULT 0,
                r_multiple REAL DEFAULT 0,
                mfe_pct REAL DEFAULT 0,
                mae_pct REAL DEFAULT 0,
                hold_minutes REAL DEFAULT 0,
                outcome TEXT DEFAULT ''
            )
        """)

        # Indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_cs_dim ON component_scores(dimension)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_gt_sym ON gate_trades(symbol)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_gt_time ON gate_trades(timestamp)")

        db.commit()
        db.close()

    def get_score(self, dimension: str, value: str) -> Dict:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM component_scores WHERE dimension=? AND dimension_value=?",
            (dimension, value)
        ).fetchone()
        db.close()
        if row:
            return dict(row)
        return {"dimension": dimension, "dimension_value": value, "score": 50.0,
                "trade_count": 0, "total_pnl": 0, "avg_r": 0, "profit_factor": 1.0,
                "expectancy": 0, "sharpe": 0, "recovery_factor": 0, "max_dd": 0}

    def update_score(self, dimension: str, value: str, stats: Dict) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("""
            INSERT OR REPLACE INTO component_scores 
            (dimension, dimension_value, score, trade_count, total_pnl, avg_r,
             profit_factor, expectancy, sharpe, recovery_factor, max_dd, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dimension, value,
            stats.get("score", 50.0),
            stats.get("trade_count", 0),
            stats.get("total_pnl", 0),
            stats.get("avg_r", 0),
            stats.get("profit_factor", 1.0),
            stats.get("expectancy", 0),
            stats.get("sharpe", 0),
            stats.get("recovery_factor", 0),
            stats.get("max_dd", 0),
            time.time(),
        ))
        db.commit()
        db.close()

    def get_threshold(self, name: str, default: float = 80.0) -> float:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        row = db.execute("SELECT current_value FROM adaptive_thresholds WHERE threshold_name=?", (name,)).fetchone()
        db.close()
        return row[0] if row else default

    def set_threshold(self, name: str, value: float) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("INSERT OR REPLACE INTO adaptive_thresholds (threshold_name, current_value, last_updated) VALUES (?, ?, ?)",
                   (name, value, time.time()))
        db.commit()
        db.close()

    def record_trade(self, trade: Dict) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("""
            INSERT INTO gate_trades (timestamp, symbol, direction, session, regime,
            confidence, alpha_score, decision, net_profit, r_multiple, mfe_pct,
            mae_pct, hold_minutes, outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade.get("timestamp", time.time()),
            trade.get("symbol", ""),
            trade.get("direction", ""),
            trade.get("session", ""),
            trade.get("market_regime", ""),
            trade.get("confidence", 0),
            trade.get("alpha_score", 0),
            trade.get("decision", ""),
            trade.get("net_profit", 0),
            trade.get("r_multiple", trade.get("actual_rr", 0)),
            trade.get("mfe_pct", 0),
            trade.get("mae_pct", 0),
            trade.get("hold_minutes", 0),
            "win" if trade.get("net_profit", 0) > 0 else "loss",
        ))
        db.commit()
        db.close()

    def get_recent_trades(self, n: int = LOOKBACK_TRADES) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM gate_trades ORDER BY timestamp DESC LIMIT ?", (n,)).fetchall()
        db.close()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════
# LEARNING ENGINE
# ══════════════════════════════════════════════════════════════════════

class LearningEngine:
    """
    After every closed trade, updates all component scores.
    
    Uses exponential decay: recent trades carry more weight.
    Weight = DECAY_FACTOR ^ (age_in_trades)
    
    Never permanently blacklists — only reduces priority.
    """

    # Dimensions to learn from
    DIMENSIONS = [
        "symbol", "session", "regime", "direction",
        "confidence_bucket", "trend", "htf_trend",
        "liquidity_sweep", "order_block", "fvg",
        "volatility_bucket", "atr_bucket", "rvol_bucket",
        "funding_bucket", "oi_bucket",
    ]

    def __init__(self, db: GateDB):
        self.db = db

    def on_trade_closed(self, trade: Dict) -> None:
        """Update all component scores after a closed trade."""
        # Record the trade
        self.db.record_trade(trade)
        
        # Get recent trades for decay-weighted computation
        recent = self.db.get_recent_trades(LOOKBACK_TRADES)
        
        # Update each dimension
        dimensions = self._extract_dimensions(trade)
        for dim, value in dimensions.items():
            if dim in self.DIMENSIONS:
                self._update_dimension(dim, value, recent)

    def _extract_dimensions(self, trade: Dict) -> Dict[str, str]:
        """Extract all dimension values from a trade."""
        confidence = trade.get("confidence", 0)
        conf_bucket = self._confidence_bucket(confidence)
        
        atr = trade.get("atr_entry", 0)
        atr_bucket = self._atr_bucket(atr)
        
        rvol = trade.get("rvol", 0)
        rvol_bucket = self._rvol_bucket(rvol)
        
        funding = trade.get("funding_rate", 0)
        funding_bucket = self._funding_bucket(funding)
        
        oi = trade.get("open_interest", 0)
        oi_bucket = self._oi_bucket(oi)
        
        return {
            "symbol": trade.get("symbol", "unknown"),
            "session": trade.get("session", "unknown"),
            "regime": trade.get("market_regime", trade.get("regime", "unknown")),
            "direction": trade.get("direction", "unknown"),
            "confidence_bucket": conf_bucket,
            "trend": trade.get("trend", trade.get("htf_trend", "unknown")),
            "htf_trend": trade.get("htf_trend", "unknown"),
            "liquidity_sweep": str(trade.get("liquidity_sweep", 0)),
            "order_block": str(trade.get("order_block", 0)),
            "fvg": str(trade.get("fvg", 0)),
            "volatility_bucket": atr_bucket,
            "atr_bucket": atr_bucket,
            "rvol_bucket": rvol_bucket,
            "funding_bucket": funding_bucket,
            "oi_bucket": oi_bucket,
        }

    def _update_dimension(self, dimension: str, value: str, recent_trades: List[Dict]) -> None:
        """Update a single dimension's score using exponential decay."""
        # Filter trades matching this dimension
        matching = []
        for i, t in enumerate(recent_trades):
            t_dims = self._extract_dimensions(t)
            if t_dims.get(dimension) == value:
                weight = DECAY_FACTOR ** i  # Exponential decay
                matching.append((weight, t))
        
        if not matching:
            return
        
        # Compute decay-weighted statistics
        total_weight = sum(w for w, _ in matching)
        weighted_pnl = sum(w * t.get("net_profit", 0) for w, t in matching) / total_weight
        weighted_r = sum(w * (t.get("r_multiple", 0) or 0) for w, t in matching) / total_weight
        
        # Win rate
        total_wins = sum(w for w, t in matching if t.get("net_profit", 0) > 0)
        wr = total_wins / total_weight * 100
        
        # Profit factor
        wins_pnl = sum(w * t.get("net_profit", 0) for w, t in matching if t.get("net_profit", 0) > 0)
        losses_pnl = abs(sum(w * t.get("net_profit", 0) for w, t in matching if t.get("net_profit", 0) <= 0))
        pf = wins_pnl / max(losses_pnl, 0.01)
        
        # Sharpe
        pnls = [t.get("net_profit", 0) for _, t in matching]
        if len(pnls) > 1:
            std = statistics.stdev(pnls)
            sharpe = statistics.mean(pnls) / std * math.sqrt(252) if std > 0 else 0
        else:
            sharpe = 0
        
        # Recovery factor
        ec = [10000]
        for w, t in sorted(matching, key=lambda x: x[1].get("timestamp", 0)):
            ec.append(ec[-1] + t.get("net_profit", 0))
        peak = max(ec)
        max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        rf = sum(pnls) / max_dd if max_dd > 0 else 0
        
        # Compute adaptive score (0-100)
        score = self._compute_score(wr, pf, weighted_r, sharpe, rf, len(matching))
        
        self.db.update_score(dimension, value, {
            "score": score,
            "trade_count": len(matching),
            "total_pnl": round(sum(t.get("net_profit", 0) for _, t in matching), 2),
            "avg_r": round(weighted_r, 2),
            "profit_factor": round(pf, 2),
            "expectancy": round(weighted_pnl, 2),
            "sharpe": round(sharpe, 2),
            "recovery_factor": round(rf, 2),
            "max_dd": round(max_dd, 2),
        })

    def _compute_score(self, wr: float, pf: float, avg_r: float, sharpe: float, rf: float, n: int) -> float:
        """Compute adaptive score 0-100 from statistics."""
        if n < MIN_TRADES_FOR_SCORE:
            return 50.0  # Neutral until sufficient data
        
        # PF component (0-30 points)
        pf_score = min(30, max(0, (pf - 0.5) / 2.5 * 30))
        
        # Expectancy/R component (0-25 points)
        r_score = min(25, max(0, (avg_r + 1) / 4 * 25))
        
        # Sharpe component (0-20 points)
        sharpe_score = min(20, max(0, sharpe / 3 * 20))
        
        # Win rate component (0-15 points)
        wr_score = min(15, max(0, (wr - 30) / 40 * 15))
        
        # Recovery factor component (0-10 points)
        rf_score = min(10, max(0, rf / 500 * 10))
        
        # Sample size bonus (0-5 points)
        sample_score = min(5, n / 50 * 5)
        
        return round(pf_score + r_score + sharpe_score + wr_score + rf_score + sample_score, 1)

    # ── Bucket helpers ──
    
    def _confidence_bucket(self, conf: float) -> str:
        if conf >= 90: return "90+"
        elif conf >= 85: return "85-90"
        elif conf >= 80: return "80-85"
        elif conf >= 75: return "75-80"
        elif conf >= 70: return "70-75"
        elif conf >= 65: return "65-70"
        elif conf >= 60: return "60-65"
        elif conf >= 55: return "55-60"
        else: return "50-55"

    def _atr_bucket(self, atr: float) -> str:
        if atr <= 0: return "unknown"
        elif atr < 0.5: return "very_low"
        elif atr < 1.0: return "low"
        elif atr < 2.0: return "medium"
        elif atr < 5.0: return "high"
        else: return "very_high"

    def _rvol_bucket(self, rvol: float) -> str:
        if rvol <= 0: return "unknown"
        elif rvol < 0.5: return "very_low"
        elif rvol < 1.0: return "low"
        elif rvol < 1.5: return "normal"
        elif rvol < 3.0: return "high"
        else: return "very_high"

    def _funding_bucket(self, funding: float) -> str:
        if abs(funding) < 0.001: return "neutral"
        elif funding > 0.01: return "very_positive"
        elif funding > 0: return "positive"
        elif funding < -0.01: return "very_negative"
        else: return "negative"

    def _oi_bucket(self, oi: float) -> str:
        if oi <= 0: return "unknown"
        elif oi < 1e6: return "very_low"
        elif oi < 1e7: return "low"
        elif oi < 1e8: return "medium"
        else: return "high"


# ══════════════════════════════════════════════════════════════════════
# ADAPTIVE THRESHOLDS
# ══════════════════════════════════════════════════════════════════════

class AdaptiveThresholds:
    """
    Continuously recalculate optimal thresholds from live data.
    
    - Optimal confidence threshold
    - Optimal session priorities
    - Optimal regime priorities
    """

    def __init__(self, db: GateDB):
        self.db = db

    def update(self) -> None:
        """Recalculate all adaptive thresholds from recent trades."""
        recent = self.db.get_recent_trades(LOOKBACK_TRADES)
        if len(recent) < MIN_TRADES_FOR_SCORE:
            return
        
        # ── Optimal confidence threshold ──
        self._update_confidence_threshold(recent)
        
        # ── Session priorities ──
        self._update_session_priorities(recent)
        
        # ── Regime priorities ──
        self._update_regime_priorities(recent)

    def _update_confidence_threshold(self, trades: List[Dict]) -> None:
        """Find the confidence level that maximizes R-multiple."""
        # Bin by confidence
        buckets = defaultdict(list)
        for t in trades:
            conf = t.get("confidence", 0)
            if conf >= 90: buckets["90+"].append(t)
            elif conf >= 85: buckets["85-90"].append(t)
            elif conf >= 80: buckets["80-85"].append(t)
            elif conf >= 75: buckets["75-80"].append(t)
            elif conf >= 70: buckets["70-75"].append(t)
            elif conf >= 65: buckets["65-70"].append(t)
            elif conf >= 60: buckets["60-65"].append(t)
            else: buckets["<60"].append(t)
        
        # Find lowest bucket with positive expectancy
        best_threshold = 80  # Default
        for label in ["<60", "60-65", "65-70", "70-75", "75-80", "80-85", "85-90", "90+"]:
            bucket_trades = buckets.get(label, [])
            if len(bucket_trades) >= MIN_TRADES_FOR_SCORE:
                avg_r = statistics.mean([t.get("r_multiple", 0) or 0 for t in bucket_trades])
                if avg_r > 0:
                    # This bucket is profitable — set threshold at its lower bound
                    if label == "<60": best_threshold = 50
                    elif label == "60-65": best_threshold = 60
                    elif label == "65-70": best_threshold = 65
                    elif label == "70-75": best_threshold = 70
                    elif label == "75-80": best_threshold = 75
                    elif label == "80-85": best_threshold = 80
                    elif label == "85-90": best_threshold = 85
                    elif label == "90+": best_threshold = 90
                    break
        
        self.db.set_threshold("optimal_confidence", best_threshold)

    def _update_session_priorities(self, trades: List[Dict]) -> None:
        """Rank sessions by decay-weighted expectancy."""
        session_stats = defaultdict(list)
        for i, t in enumerate(trades):
            session = t.get("session", "unknown")
            weight = DECAY_FACTOR ** i
            session_stats[session].append((weight, t))
        
        for session, weighted_trades in session_stats.items():
            total_w = sum(w for w, _ in weighted_trades)
            avg_r = sum(w * (t.get("r_multiple", 0) or 0) for w, t in weighted_trades) / total_w
            avg_pnl = sum(w * t.get("net_profit", 0) for w, t in weighted_trades) / total_w
            wins = sum(w for w, t in weighted_trades if t.get("net_profit", 0) > 0)
            wr = wins / total_w * 100 if total_w > 0 else 0
            
            self.db.update_score("session_priority", session, {
                "score": self._compute_session_score(wr, avg_r, len(weighted_trades)),
                "trade_count": len(weighted_trades),
                "total_pnl": round(sum(t.get("net_profit", 0) for _, t in weighted_trades), 2),
                "avg_r": round(avg_r, 2),
                "profit_factor": 0,
                "expectancy": round(avg_pnl, 2),
                "sharpe": 0, "recovery_factor": 0, "max_dd": 0,
            })

    def _update_regime_priorities(self, trades: List[Dict]) -> None:
        """Rank regimes by decay-weighted expectancy."""
        regime_stats = defaultdict(list)
        for i, t in enumerate(trades):
            regime = t.get("regime", "unknown")
            weight = DECAY_FACTOR ** i
            regime_stats[regime].append((weight, t))
        
        for regime, weighted_trades in regime_stats.items():
            total_w = sum(w for w, _ in weighted_trades)
            avg_r = sum(w * (t.get("r_multiple", 0) or 0) for w, t in weighted_trades) / total_w
            avg_pnl = sum(w * t.get("net_profit", 0) for w, t in weighted_trades) / total_w
            wins = sum(w for w, t in weighted_trades if t.get("net_profit", 0) > 0)
            wr = wins / total_w * 100 if total_w > 0 else 0
            
            self.db.update_score("regime_priority", regime, {
                "score": self._compute_session_score(wr, avg_r, len(weighted_trades)),
                "trade_count": len(weighted_trades),
                "total_pnl": round(sum(t.get("net_profit", 0) for _, t in weighted_trades), 2),
                "avg_r": round(avg_r, 2),
                "profit_factor": 0,
                "expectancy": round(avg_pnl, 2),
                "sharpe": 0, "recovery_factor": 0, "max_dd": 0,
            })

    def _compute_session_score(self, wr: float, avg_r: float, n: int) -> float:
        """Compute adaptive score for session/regime priorities."""
        if n < MIN_TRADES_FOR_SCORE:
            return 50.0
        pf_score = min(30, max(0, (1.0 - 0.5) / 2.5 * 30))  # Neutral PF
        r_score = min(25, max(0, (avg_r + 1) / 4 * 25))
        wr_score = min(15, max(0, (wr - 30) / 40 * 15))
        sample_score = min(5, n / 50 * 5)
        return round(pf_score + r_score + wr_score + sample_score, 1)


# ══════════════════════════════════════════════════════════════════════
# PORTFOLIO RANKER
# ══════════════════════════════════════════════════════════════════════

class PortfolioRanker:
    """
    When multiple signals appear simultaneously, rank by:
    1. Expected Profit
    2. Profit Factor
    3. Recovery Factor
    4. Drawdown
    5. Correlation (avoid correlated positions)
    
    Only execute the highest quality opportunities.
    """

    # Correlated clusters (simplified)
    CORRELATION_CLUSTERS = {
        "L1_MAJOR": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
        "L1_ALT": ["ADAUSDT", "AVAXUSDT", "DOTUSDT", "NEARUSDT", "ATOMUSDT"],
        "MEME": ["DOGEUSDT", "1000BONKUSDT", "1000PEPEUSDT", "SHIBUSDT", "WIFUSDT"],
        "AI": ["FETUSDT", "RENDERUSDT", "ARUSDT", "TAOUSDT", "WLDUSDT"],
        "DEFI": ["UNIUSDT", "AAVEUSDT", "CRVUSDT", "LINKUSDT"],
    }

    MAX_CLUSTER_EXPOSURE = 2  # Max positions per correlation cluster
    MAX_CONCURRENT = 6

    def __init__(self, gate_db: GateDB):
        self.db = gate_db

    def rank_and_select(self, decisions: List[ExecutionDecision], 
                        open_positions: List[Dict] = None) -> List[ExecutionDecision]:
        """Rank multiple decisions and select the best for execution."""
        if not decisions:
            return []
        
        # Filter to EXECUTE or GOOD only
        executable = [d for d in decisions if d.decision in ("EXECUTE", "GOOD")]
        
        if not executable:
            return decisions  # Return all as-is
        
        # Sort by alpha score (highest first)
        executable.sort(key=lambda d: d.alpha_score, reverse=True)
        
        # Apply portfolio constraints
        selected = []
        cluster_counts = defaultdict(int)
        open_count = len(open_positions) if open_positions else 0
        
        # Count existing cluster exposure from open positions
        if open_positions:
            for pos in open_positions:
                sym = pos.get("symbol", "")
                cluster = self._get_cluster(sym)
                cluster_counts[cluster] += 1
        
        for decision in executable:
            if open_count >= self.MAX_CONCURRENT:
                decision.decision = "WATCH"
                decision.execution_reason = f"Max concurrent reached ({self.MAX_CONCURRENT})"
                continue
            
            # Check correlation cluster
            # We need the symbol from the original signal — stored in component_scores
            sym = decision.component_scores.get("symbol", "")
            cluster = self._get_cluster(sym)
            
            if cluster_counts[cluster] >= self.MAX_CLUSTER_EXPOSURE:
                decision.decision = "WATCH"
                decision.execution_reason = f"Cluster {cluster} max exposure ({self.MAX_CLUSTER_EXPOSURE})"
                continue
            
            selected.append(decision)
            cluster_counts[cluster] += 1
            open_count += 1
        
        # Merge selected back with non-executable decisions
        result = selected + [d for d in decisions if d.decision not in ("EXECUTE", "GOOD")]
        return result

    def _get_cluster(self, symbol: str) -> str:
        for cluster, syms in self.CORRELATION_CLUSTERS.items():
            if symbol in syms:
                return cluster
        return "OTHER"


# ══════════════════════════════════════════════════════════════════════
# DYNAMIC EXECUTION GATE (Main Orchestrator)
# ══════════════════════════════════════════════════════════════════════

class ExecutionGate:
    """
    Dynamic Execution Gate — sits between Scanner and Execution.
    
    Every signal receives a dynamic institutional score (0-100).
    Score decides: EXECUTE, WATCH, or IGNORE.
    
    The signal itself NEVER changes. Only execution changes.
    
    Usage:
        gate = ExecutionGate()
        
        # When scanner emits a signal:
        decision = gate.score_signal(signal_dict)
        if decision.decision == "EXECUTE":
            execute_trade(signal, size=decision.position_size_pct)
        elif decision.decision == "WATCH":
            log_for_monitoring(signal)
        # IGNORE: do nothing
        
        # After trade closes:
        gate.on_trade_closed(trade_result)
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db = GateDB(db_path)
        self.learning = LearningEngine(self.db)
        self.thresholds = AdaptiveThresholds(self.db)
        self.ranker = PortfolioRanker(self.db)
        logger.info("✅ ExecutionGate initialized")

    def score_signal(self, signal: Dict, open_positions: List[Dict] = None) -> ExecutionDecision:
        """
        Score a signal and return an execution decision.
        
        Signal dict should contain: symbol, side/direction, entry, sl, tp, 
        confidence, regime, score, atr, rvol, funding_rate, open_interest,
        cvd, delta, orderbook_imbalance, liquidity_sweep, order_block, fvg,
        htf_trend, session, trend
        """
        decision = ExecutionDecision()
        decision.component_scores = {"symbol": signal.get("symbol", "")}
        
        # ── Extract signal dimensions ──
        symbol = signal.get("symbol", "")
        direction = signal.get("side", signal.get("direction", ""))
        confidence = signal.get("confidence", signal.get("conf", 0))
        regime = signal.get("regime", signal.get("market_regime", ""))
        session = signal.get("session", "")
        trend = signal.get("trend", signal.get("htf_trend", ""))
        htf_trend = signal.get("htf_trend", "")
        atr = signal.get("atr", signal.get("atr_entry", 0))
        rvol = signal.get("rvol", 0)
        funding = signal.get("funding_rate", 0)
        oi = signal.get("open_interest", 0)
        cvd = signal.get("cvd", 0)
        liq_sweep = 1 if signal.get("liquidity_sweep") else 0
        order_block = 1 if signal.get("order_block") else 0
        fvg = 1 if signal.get("fvg") else 0
        score_raw = signal.get("score", 0)
        
        # ── Score each component ──
        components = {}
        
        # 1. Historical Symbol Performance (0-15)
        sym_score = self.db.get_score("symbol", symbol)
        components["symbol"] = {"score": sym_score["score"], "weight": 15, "trades": sym_score["trade_count"]}
        decision.historical_pf = sym_score.get("profit_factor", 1.0)
        decision.historical_expectancy = sym_score.get("expectancy", 0)
        decision.historical_recovery = sym_score.get("recovery_factor", 0)
        decision.historical_sharpe = sym_score.get("sharpe", 0)
        
        # 2. Historical Session Performance (0-10)
        sess_score = self.db.get_score("session_priority", session) if session else {"score": 50}
        components["session"] = {"score": sess_score["score"], "weight": 10}
        
        # 3. Historical Regime Performance (0-10)
        regime_score = self.db.get_score("regime_priority", regime) if regime else {"score": 50}
        components["regime"] = {"score": regime_score["score"], "weight": 10}
        
        # 4. Confidence Score (0-10)
        opt_conf = self.db.get_threshold("optimal_confidence", 80)
        if confidence >= opt_conf:
            conf_score = min(100, 50 + (confidence - opt_conf) * 2)
        else:
            conf_score = max(0, 50 - (opt_conf - confidence) * 3)
        components["confidence"] = {"score": conf_score, "weight": 10, "threshold": opt_conf}
        decision.optimal_confidence = opt_conf
        
        # 5. Trend Alignment (0-10)
        trend_score_val = 50
        if trend and regime:
            if (trend == "bullish" and regime == "BUY_MODE") or \
               (trend == "bearish" and regime == "SELL_MODE"):
                trend_score_val = 90
            elif trend == "neutral":
                trend_score_val = 40
            else:
                trend_score_val = 20
        components["trend"] = {"score": trend_score_val, "weight": 10}
        
        # 6. Volatility Score (0-5)
        atr_bucket = self._bucket_atr(atr)
        vol_score = self.db.get_score("volatility_bucket", atr_bucket)
        components["volatility"] = {"score": vol_score["score"], "weight": 5}
        
        # 7. Order Flow Score (0-10)
        flow_score = 50
        if funding != 0:
            # Negative funding = longs paying shorts = bullish for shorts
            if direction == "SHORT" and funding > 0.005: flow_score += 15
            elif direction == "LONG" and funding < -0.005: flow_score += 15
            elif direction == "LONG" and funding > 0.01: flow_score -= 10
            elif direction == "SHORT" and funding < -0.01: flow_score -= 10
        if cvd != 0:
            if direction == "LONG" and cvd > 0: flow_score += 10
            elif direction == "SHORT" and cvd < 0: flow_score += 10
        flow_score = max(0, min(100, flow_score))
        components["order_flow"] = {"score": flow_score, "weight": 10}
        
        # 8. Smart Money Score (0-10)
        smart_score = 50
        if liq_sweep: smart_score += 15
        if order_block: smart_score += 10
        if fvg: smart_score += 10
        smart_score = min(100, smart_score)
        components["smart_money"] = {"score": smart_score, "weight": 10}
        
        # 9. Historical PF/Expectancy (0-10)
        hist_score = min(100, max(0, sym_score.get("score", 50)))
        components["historical"] = {"score": hist_score, "weight": 10}
        
        # 10. Recent Performance / Raw Score (0-10)
        raw_score = min(100, max(0, score_raw * 100 if score_raw <= 1 else score_raw))
        components["raw_score"] = {"score": raw_score, "weight": 10}
        
        # ── Compute weighted alpha score ──
        total_weight = sum(c["weight"] for c in components.values())
        weighted_sum = sum(c["score"] * c["weight"] for c in components.values())
        alpha_score = weighted_sum / total_weight if total_weight > 0 else 50
        
        decision.alpha_score = round(alpha_score, 1)
        decision.component_scores = {k: round(v["score"], 1) for k, v in components.items()}
        
        # ── Determine execution level ──
        if alpha_score >= EXECUTE_THRESHOLD:
            decision.decision = "EXECUTE"
            decision.position_size_pct = 100
            decision.execution_reason = f"Elite score {alpha_score:.0f} >= {EXECUTE_THRESHOLD}"
        elif alpha_score >= GOOD_THRESHOLD:
            decision.decision = "GOOD"
            decision.position_size_pct = 75
            decision.execution_reason = f"Good score {alpha_score:.0f} >= {GOOD_THRESHOLD}"
        elif alpha_score >= AVERAGE_THRESHOLD:
            decision.decision = "AVERAGE"
            decision.position_size_pct = 50
            decision.execution_reason = f"Average score {alpha_score:.0f} — paper trade"
        elif alpha_score >= WEAK_THRESHOLD:
            decision.decision = "WEAK"
            decision.position_size_pct = 25
            decision.execution_reason = f"Weak score {alpha_score:.0f} — watch only"
        else:
            decision.decision = "IGNORE"
            decision.position_size_pct = 0
            decision.execution_reason = f"Poor score {alpha_score:.0f} < {WEAK_THRESHOLD}"
        
        # ── Symbol rank ──
        if sym_score.get("score", 50) >= 70:
            decision.symbol_rank = "ELITE"
        elif sym_score.get("score", 50) >= 60:
            decision.symbol_rank = "GOOD"
        elif sym_score.get("score", 50) >= 40:
            decision.symbol_rank = "NORMAL"
        else:
            decision.symbol_rank = "WEAK"
        
        # ── Expected profit from historical data ──
        decision.expected_profit = sym_score.get("expectancy", 0)
        decision.expected_drawdown = sym_score.get("max_dd", 0)
        
        # ── Generate reasons for trade (human-readable audit trail) ──
        reasons = []
        avg_r = components.get("historical", {}).get("score", 50) / 100 * 2.0  # Approximate R from score
        if components["trend"]["score"] >= 70:
            reasons.append(f"Strong {trend} trend alignment with {regime}")
        if components["confidence"]["score"] >= 70:
            reasons.append(f"Confidence {confidence:.0f} above optimal {opt_conf:.0f}")
        if components["smart_money"]["score"] >= 70:
            sm_parts = []
            if liq_sweep: sm_parts.append("liquidity sweep")
            if order_block: sm_parts.append("order block")
            if fvg: sm_parts.append("FVG")
            reasons.append(f"Market structure: {', '.join(sm_parts)} confirmed")
        if components["order_flow"]["score"] >= 70:
            reasons.append("Order flow supportive (CVD/funding aligned)")
        if sym_score.get("score", 50) >= 60:
            reasons.append(f"Strong historical expectancy for {symbol}")
        if sess_score.get("score", 50) >= 60:
            reasons.append(f"Session '{session}' historically profitable")
        if regime_score.get("score", 50) >= 60:
            reasons.append(f"Regime '{regime}' historically profitable")
        if not reasons:
            reasons.append("Mixed signals — moderate confidence")
        
        # Add expected metrics
        if sym_score.get("profit_factor", 0) > 1.0:
            reasons.append(f"Expected PF: {sym_score['profit_factor']:.2f}")
        if avg_r > 0:
            reasons.append(f"Expected RR: {avg_r:.1f}R")
        if sym_score.get("max_dd", 0) > 0:
            reasons.append(f"Expected Drawdown: {sym_score['max_dd']:.1f}%")
        
        decision.reasons_for_trade = reasons
        
        return decision

    def on_trade_closed(self, trade: Dict) -> None:
        """Update learning engine after every closed trade."""
        self.learning.on_trade_closed(trade)
        self.thresholds.update()
        
        logger.debug("🧠 Learning updated: {} {} PnL=${:+.2f} score={:.0f}",
                      trade.get("symbol", ""), trade.get("direction", ""),
                      trade.get("net_profit", 0), trade.get("alpha_score", 0))

    def rank_signals(self, signals: List[Dict], 
                     open_positions: List[Dict] = None) -> List[Tuple[Dict, ExecutionDecision]]:
        """Score and rank multiple signals. Returns (signal, decision) pairs."""
        decisions = []
        for sig in signals:
            decision = self.score_signal(sig, open_positions)
            decisions.append((sig, decision))
        
        # Apply portfolio ranking
        dec_objects = [d for _, d in decisions]
        ranked = self.ranker.rank_and_select(dec_objects, open_positions)
        
        # Re-pair
        result = []
        for sig, dec in decisions:
            # Find the updated decision
            for ranked_dec in ranked:
                if ranked_dec.component_scores.get("symbol") == dec.component_scores.get("symbol"):
                    result.append((sig, ranked_dec))
                    break
            else:
                result.append((sig, dec))
        
        # Sort by alpha score
        result.sort(key=lambda x: x[1].alpha_score, reverse=True)
        return result

    def _bucket_atr(self, atr: float) -> str:
        if atr <= 0: return "unknown"
        elif atr < 0.5: return "very_low"
        elif atr < 1.0: return "low"
        elif atr < 2.0: return "medium"
        elif atr < 5.0: return "high"
        else: return "very_high"

    def get_status(self) -> Dict:
        """Get current gate status for monitoring."""
        recent = self.db.get_recent_trades(50)
        if not recent:
            return {"status": "NO_DATA", "trades": 0}
        
        pnls = [t.get("net_profit", 0) for t in recent]
        decisions = defaultdict(int)
        for t in recent:
            decisions[t.get("decision", "UNKNOWN")] += 1
        
        return {
            "status": "OK",
            "recent_trades": len(recent),
            "net_pnl": round(sum(pnls), 2),
            "avg_pnl": round(statistics.mean(pnls), 2),
            "decision_distribution": dict(decisions),
            "optimal_confidence": self.db.get_threshold("optimal_confidence", 80),
        }


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    gate = ExecutionGate()
    print("ExecutionGate initialized")
    print(f"Status: {gate.get_status()}")
