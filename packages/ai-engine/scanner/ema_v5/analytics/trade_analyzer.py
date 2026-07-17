"""
EMA_V5 Trade Analyzer — Individual trade analysis and patterns.
Reads from database. Pure computation.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database


class TradeAnalyzer:
    """Analyzes individual trade patterns and quality."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()

    def analyze_all(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Complete trade analysis."""
        if signals is None:
            signals = self._db.get_all_signals()

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        if not closed:
            return {"trades": [], "patterns": {}, "quality": {}}

        trades = [self._analyze_single(t) for t in closed]
        patterns = self._detect_patterns(trades)
        quality = self._compute_quality(trades)

        return {
            "trades": trades,
            "patterns": patterns,
            "quality": quality,
        }

    def _analyze_single(self, trade: Dict) -> Dict[str, Any]:
        """Analyze a single trade."""
        entry = trade.get("entry", 0)
        sl = trade.get("stop_loss", 0)
        tp1 = trade.get("tp1", 0)
        pnl = trade.get("pnl", 0)
        hold = trade.get("hold_time", 0)
        result = trade.get("result", "")

        # Risk/reward actualized
        risk = abs(entry - sl) if sl > 0 else entry * 0.02
        actualized_r = (pnl / risk) if risk > 0 else 0

        # Quality score (0-100)
        quality = self._trade_quality_score(trade)

        return {
            "uuid": trade.get("uuid", ""),
            "symbol": trade.get("symbol", ""),
            "side": trade.get("side", ""),
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "pnl": round(pnl, 4),
            "actualized_r": round(actualized_r, 2),
            "hold_minutes": round(hold, 1),
            "result": result,
            "date": trade.get("date", ""),
            "confidence": trade.get("confidence", 0),
            "regime": trade.get("regime", ""),
            "pattern": trade.get("pattern", ""),
            "quality_score": quality,
            "rr_planned": trade.get("rr_1", 0),
        }

    def _trade_quality_score(self, trade: Dict) -> int:
        """Compute quality score 0-100 for a trade."""
        score = 50  # base

        # Confidence bonus
        conf = trade.get("confidence", 0) or 0
        if conf >= 0.95:
            score += 15
        elif conf >= 0.90:
            score += 10
        elif conf >= 0.85:
            score += 5

        # Result bonus
        if trade.get("result") == "win":
            score += 15
        else:
            score -= 10

        # R:R actualized
        entry = trade.get("entry", 0)
        sl = trade.get("stop_loss", 0)
        risk = abs(entry - sl) if sl > 0 else 1
        actual_r = trade.get("pnl", 0) / risk if risk > 0 else 0
        if actual_r >= 2.0:
            score += 10
        elif actual_r >= 1.0:
            score += 5
        elif actual_r < -1.0:
            score -= 10

        # Hold time quality (not too fast, not too slow)
        hold = trade.get("hold_time", 0)
        if 30 <= hold <= 480:  # 30min to 8h is optimal
            score += 5
        elif hold < 10:  # too fast
            score -= 5

        # Pattern match bonus
        if trade.get("pattern"):
            score += 5

        return max(0, min(100, score))

    def _detect_patterns(self, trades: List[Dict]) -> Dict[str, Any]:
        """Detect patterns in trade history."""
        if not trades:
            return {}

        # Symbol frequency
        sym_freq: Dict[str, int] = {}
        for t in trades:
            sym = t.get("symbol", "")
            sym_freq[sym] = sym_freq.get(sym, 0) + 1

        # Side performance
        long_trades = [t for t in trades if t.get("side") == "LONG"]
        short_trades = [t for t in trades if t.get("side") == "SHORT"]

        # Time-of-day performance (from date field)
        hour_perf: Dict[int, List[float]] = {}
        for t in trades:
            # Approximate from hold time patterns
            pass

        # Consecutive patterns
        streak = self._streak_analysis(trades)

        return {
            "symbol_frequency": dict(sorted(sym_freq.items(), key=lambda x: -x[1])[:10]),
            "long_count": len(long_trades),
            "short_count": len(short_trades),
            "long_avg_pnl": round(sum(t.get("pnl", 0) for t in long_trades) / len(long_trades), 4) if long_trades else 0,
            "short_avg_pnl": round(sum(t.get("pnl", 0) for t in short_trades) / len(short_trades), 4) if short_trades else 0,
            "streaks": streak,
        }

    def _streak_analysis(self, trades: List[Dict]) -> Dict[str, Any]:
        """Analyze win/loss streaks."""
        streaks = {"current": 0, "current_type": "", "max_win": 0, "max_loss": 0}
        current = 0
        current_type = ""

        for t in trades:
            result = t.get("result", "")
            if result == current_type:
                current += 1
            else:
                current_type = result
                current = 1

            if current_type == "win":
                streaks["max_win"] = max(streaks["max_win"], current)
            else:
                streaks["max_loss"] = max(streaks["max_loss"], current)

        streaks["current"] = current
        streaks["current_type"] = current_type
        return streaks

    def _compute_quality(self, trades: List[Dict]) -> Dict[str, Any]:
        """Compute aggregate quality metrics."""
        if not trades:
            return {}

        scores = [t.get("quality_score", 50) for t in trades]
        return {
            "avg_quality": round(sum(scores) / len(scores), 1),
            "high_quality_count": sum(1 for s in scores if s >= 80),
            "low_quality_count": sum(1 for s in scores if s < 40),
            "quality_distribution": {
                "excellent": sum(1 for s in scores if s >= 90),
                "good": sum(1 for s in scores if 70 <= s < 90),
                "average": sum(1 for s in scores if 50 <= s < 70),
                "poor": sum(1 for s in scores if s < 50),
            },
        }

    def get_best_trades(self, n: int = 10) -> List[Dict]:
        """Get top N trades by PnL."""
        signals = self._db.get_all_signals()
        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        sorted_trades = sorted(closed, key=lambda s: s.get("pnl", 0), reverse=True)
        return [self._analyze_single(t) for t in sorted_trades[:n]]

    def get_worst_trades(self, n: int = 10) -> List[Dict]:
        """Get bottom N trades by PnL."""
        signals = self._db.get_all_signals()
        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        sorted_trades = sorted(closed, key=lambda s: s.get("pnl", 0))
        return [self._analyze_single(t) for t in sorted_trades[:n]]
