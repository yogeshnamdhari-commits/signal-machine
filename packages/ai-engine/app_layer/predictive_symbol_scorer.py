"""
Predictive Symbol Scorer — Rolling execution score for symbols.

Per Executive Assessment v5:
    "Instead of only showing historical results, compute a rolling
     execution score that combines:
        - Recent Profit Factor
        - Recent Expectancy
        - Drawdown
        - Sample size
        - Trade frequency

     That score should directly influence whether new trades are accepted."

Key Features:
    1. Composite Execution Score — 0-100 score per symbol
    2. Multi-dimensional — PF, EV, drawdown, frequency, consistency
    3. Time-weighted — Recent trades weighted more heavily
    4. Confidence-adjusted — Low sample = low confidence
    5. Adaptive thresholds — Score determines execution eligibility

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# ═══════════════════════════════════════════════════════════════
# SCORING CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Component weights (must sum to 1.0)
SCORE_WEIGHTS = {
    "profit_factor":    0.25,  # Rolling PF
    "expectancy":       0.25,  # Rolling EV in R
    "consistency":      0.15,  # Win rate stability
    "drawdown":         0.15,  # Max drawdown control
    "frequency":        0.10,  # Trade frequency (not too few, not too many)
    "recency":          0.10,  # Recent performance vs older
}

# Rolling window
ROLLING_WINDOW = 50

# Score thresholds
SCORE_ELITE = 85       # Elite — maximum allocation
SCORE_STRONG = 70      # Strong — above normal
SCORE_NORMAL = 55      # Normal — standard
SCORE_WEAK = 40        # Weak — reduced
SCORE_DISABLE = 25     # Disable — no trading

# Minimum trades for scoring
MIN_TRADES_FOR_SCORING = 10


@dataclass
class SymbolScore:
    """Execution score for a single symbol."""
    symbol: str = ""
    total_trades: int = 0
    rolling_trades: int = 0

    # Component scores (0-100)
    pf_score: float = 0.0
    ev_score: float = 0.0
    consistency_score: float = 0.0
    drawdown_score: float = 0.0
    frequency_score: float = 0.0
    recency_score: float = 0.0

    # Composite
    execution_score: float = 0.0
    score_grade: str = ""    # ELITE / STRONG / NORMAL / WEAK / DISABLED

    # Raw metrics
    rolling_pf: float = 0.0
    rolling_ev_r: float = 0.0
    rolling_win_rate: float = 0.0
    max_drawdown_r: float = 0.0
    avg_hold_minutes: float = 0.0
    trades_per_day: float = 0.0

    # Size adjustment
    size_adjustment: float = 1.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "total_trades": self.total_trades,
            "rolling_trades": self.rolling_trades,
            "execution_score": round(self.execution_score, 1),
            "score_grade": self.score_grade,
            "size_adjustment": round(self.size_adjustment, 2),
            "components": {
                "pf_score": round(self.pf_score, 1),
                "ev_score": round(self.ev_score, 1),
                "consistency_score": round(self.consistency_score, 1),
                "drawdown_score": round(self.drawdown_score, 1),
                "frequency_score": round(self.frequency_score, 1),
                "recency_score": round(self.recency_score, 1),
            },
            "metrics": {
                "rolling_pf": round(self.rolling_pf, 2),
                "rolling_ev_r": round(self.rolling_ev_r, 3),
                "rolling_win_rate": round(self.rolling_win_rate, 3),
                "max_drawdown_r": round(self.max_drawdown_r, 3),
                "trades_per_day": round(self.trades_per_day, 2),
            },
        }


class PredictiveSymbolScorer:
    """
    Computes rolling execution scores for symbols.

    Per Executive Assessment v5:
        "Compute a rolling execution score that directly influences
         whether new trades are accepted."

    This engine:
        1. Calculates multi-dimensional score per symbol
        2. Uses time-weighted recent trades
        3. Adjusts for confidence (sample size)
        4. Provides actionable grades (ELITE/STRONG/NORMAL/WEAK/DISABLED)

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._symbol_scores: Dict[str, SymbolScore] = {}
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load data from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_scores()

    def _load_scores(self) -> None:
        """Load all trades and compute scores per symbol."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, realized_r, pnl, closed_at, hold_minutes
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            # Group by symbol
            by_symbol: Dict[str, List[Dict]] = defaultdict(list)
            for row in rows:
                r = dict(row)
                by_symbol[r.get("symbol", "")].append(r)

            # Compute scores
            self._symbol_scores = {}
            for sym, trades in by_symbol.items():
                self._symbol_scores[sym] = self._compute_score(sym, trades)

            self._last_load = time.time()
            logger.info(
                "📊 Predictive Scorer loaded: {} symbols",
                len(self._symbol_scores),
            )

        except Exception as e:
            logger.warning("Could not load predictive scorer: {}", e)

    def _compute_score(self, symbol: str, trades: List[Dict]) -> SymbolScore:
        """Compute execution score for a symbol."""
        score = SymbolScore(symbol=symbol, total_trades=len(trades))

        if not trades:
            return score

        # Rolling window
        rolling = trades[:ROLLING_WINDOW]
        score.rolling_trades = len(rolling)

        # ── Component 1: Profit Factor (25%) ──
        wins = [t.get("realized_r", 0) or 0 for t in rolling if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in rolling if (t.get("realized_r", 0) or 0) < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        score.rolling_pf = gross_profit / max(0.01, gross_loss)
        score.pf_score = self._score_pf(score.rolling_pf)

        # ── Component 2: Expectancy (25%) ──
        all_r = [t.get("realized_r", 0) or 0 for t in rolling]
        score.rolling_ev_r = sum(all_r) / max(1, len(all_r))
        score.ev_score = self._score_ev(score.rolling_ev_r)

        # ── Component 3: Consistency (15%) ──
        score.rolling_win_rate = len(wins) / max(1, len(rolling))
        # Consistency = how stable is the win rate across sub-windows
        if len(rolling) >= 20:
            mid = len(rolling) // 2
            wr1 = len([r for r in all_r[:mid] if r > 0]) / max(1, mid)
            wr2 = len([r for r in all_r[mid:] if r > 0]) / max(1, len(rolling) - mid)
            consistency = 1 - abs(wr1 - wr2)  # 0 = very inconsistent, 1 = very consistent
            score.consistency_score = consistency * 100
        else:
            score.consistency_score = 50  # Default

        # ── Component 4: Drawdown (15%) ──
        # Calculate max drawdown in R
        equity = 0
        peak = 0
        max_dd = 0
        for r in all_r:
            equity += r
            if equity > peak:
                peak = equity
            dd = peak - equity
            max_dd = max(max_dd, dd)
        score.max_drawdown_r = max_dd
        score.drawdown_score = self._score_drawdown(max_dd)

        # ── Component 5: Frequency (10%) ──
        if trades:
            first_trade = trades[-1].get("closed_at", 0) or 0
            last_trade = trades[0].get("closed_at", 0) or 0
            if first_trade > 0 and last_trade > 0:
                days_span = max(1, (last_trade - first_trade) / 86400)
                score.trades_per_day = len(trades) / days_span
            else:
                score.trades_per_day = 0
        score.frequency_score = self._score_frequency(score.trades_per_day)

        # ── Component 6: Recency (10%) ──
        # Compare first half vs second half performance
        if len(rolling) >= 10:
            mid = len(rolling) // 2
            r1_avg = sum(all_r[:mid]) / max(1, mid)
            r2_avg = sum(all_r[mid:]) / max(1, len(rolling) - mid)
            if r1_avg > 0:
                recency_ratio = (r2_avg - r1_avg) / max(0.01, abs(r1_avg))
            else:
                recency_ratio = 0
            score.recency_score = max(0, min(100, 50 + recency_ratio * 50))
        else:
            score.recency_score = 50

        # ── Composite Score ──
        score.execution_score = (
            score.pf_score * SCORE_WEIGHTS["profit_factor"]
            + score.ev_score * SCORE_WEIGHTS["expectancy"]
            + score.consistency_score * SCORE_WEIGHTS["consistency"]
            + score.drawdown_score * SCORE_WEIGHTS["drawdown"]
            + score.frequency_score * SCORE_WEIGHTS["frequency"]
            + score.recency_score * SCORE_WEIGHTS["recency"]
        )

        # ── Confidence adjustment ──
        if score.rolling_trades < MIN_TRADES_FOR_SCORING:
            # Low confidence — pull toward 50
            confidence_factor = score.rolling_trades / MIN_TRADES_FOR_SCORING
            score.execution_score = 50 * (1 - confidence_factor) + score.execution_score * confidence_factor

        # ── Grade ──
        score.score_grade = self._get_grade(score.execution_score)
        score.size_adjustment = self._get_size_adjustment(score.execution_score)

        return score

    def _score_pf(self, pf: float) -> float:
        """Score profit factor (0-100)."""
        if pf >= 2.0:
            return 100
        elif pf >= 1.5:
            return 80 + (pf - 1.5) * 40
        elif pf >= 1.2:
            return 65 + (pf - 1.2) * 50
        elif pf >= 1.0:
            return 50 + (pf - 1.0) * 75
        elif pf >= 0.8:
            return 30 + (pf - 0.8) * 100
        elif pf >= 0.5:
            return 10 + (pf - 0.5) * 67
        return max(0, pf * 20)

    def _score_ev(self, ev: float) -> float:
        """Score expectancy in R (0-100)."""
        if ev >= 0.5:
            return 100
        elif ev >= 0.3:
            return 80 + (ev - 0.3) * 100
        elif ev >= 0.1:
            return 60 + (ev - 0.1) * 100
        elif ev >= 0:
            return 50 + ev * 100
        elif ev >= -0.2:
            return 30 + (ev + 0.2) * 100
        elif ev >= -0.5:
            return 10 + (ev + 0.5) * 67
        return max(0, 10 + ev * 20)

    def _score_drawdown(self, max_dd_r: float) -> float:
        """Score drawdown control (0-100, lower DD = higher score)."""
        if max_dd_r <= 0.5:
            return 100
        elif max_dd_r <= 1.0:
            return 80 + (1.0 - max_dd_r) * 40
        elif max_dd_r <= 2.0:
            return 50 + (2.0 - max_dd_r) * 30
        elif max_dd_r <= 3.0:
            return 25 + (3.0 - max_dd_r) * 25
        return max(0, 25 - max_dd_r * 5)

    def _score_frequency(self, trades_per_day: float) -> float:
        """Score trade frequency (0-100, sweet spot around 1-3/day)."""
        if 1.0 <= trades_per_day <= 3.0:
            return 100
        elif 0.5 <= trades_per_day < 1.0:
            return 60 + (trades_per_day - 0.5) * 80
        elif 3.0 < trades_per_day <= 5.0:
            return 60 + (5.0 - trades_per_day) * 20
        elif trades_per_day < 0.5:
            return max(20, trades_per_day * 80)
        else:
            return max(20, 100 - (trades_per_day - 5) * 10)

    def _get_grade(self, score: float) -> str:
        """Get grade from score."""
        if score >= SCORE_ELITE:
            return "ELITE"
        elif score >= SCORE_STRONG:
            return "STRONG"
        elif score >= SCORE_NORMAL:
            return "NORMAL"
        elif score >= SCORE_WEAK:
            return "WEAK"
        return "DISABLED"

    def _get_size_adjustment(self, score: float) -> float:
        """Get size adjustment from score."""
        if score >= SCORE_ELITE:
            return 1.25
        elif score >= SCORE_STRONG:
            return 1.10
        elif score >= SCORE_NORMAL:
            return 1.00
        elif score >= SCORE_WEAK:
            return 0.50
        return 0.0

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════

    def get_score(self, symbol: str) -> SymbolScore:
        """Get execution score for a symbol."""
        self._ensure_loaded()
        return self._symbol_scores.get(symbol, SymbolScore(symbol=symbol))

    def get_size_adjustment(self, symbol: str) -> float:
        """Get size adjustment for a symbol."""
        score = self.get_score(symbol)
        return score.size_adjustment

    def is_eligible(self, symbol: str) -> bool:
        """Check if a symbol is eligible for trading."""
        score = self.get_score(symbol)
        return score.execution_score >= SCORE_WEAK

    def get_elite_symbols(self) -> List[SymbolScore]:
        """Get all elite symbols."""
        self._ensure_loaded()
        return [
            s for s in self._symbol_scores.values()
            if s.score_grade == "ELITE"
        ]

    def get_disabled_symbols(self) -> List[str]:
        """Get all disabled symbols."""
        self._ensure_loaded()
        return [
            s.symbol for s in self._symbol_scores.values()
            if s.score_grade == "DISABLED"
        ]

    def get_top_symbols(self, n: int = 10) -> List[SymbolScore]:
        """Get top N symbols by execution score."""
        self._ensure_loaded()
        return sorted(
            self._symbol_scores.values(),
            key=lambda s: s.execution_score,
            reverse=True,
        )[:n]

    def get_worst_symbols(self, n: int = 10) -> List[SymbolScore]:
        """Get worst N symbols by execution score."""
        self._ensure_loaded()
        return sorted(
            self._symbol_scores.values(),
            key=lambda s: s.execution_score,
        )[:n]

    def get_all_scores(self) -> Dict[str, SymbolScore]:
        """Get all symbol scores."""
        self._ensure_loaded()
        return dict(self._symbol_scores)

    def get_summary(self) -> Dict[str, Any]:
        """Get scoring summary."""
        self._ensure_loaded()
        grades = defaultdict(int)
        for s in self._symbol_scores.values():
            grades[s.score_grade] += 1
        return {
            "total_symbols": len(self._symbol_scores),
            "grade_distribution": dict(grades),
            "elite_count": grades.get("ELITE", 0),
            "disabled_count": grades.get("DISABLED", 0),
        }
