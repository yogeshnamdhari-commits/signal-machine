"""
EMA_V5 Regime Analytics — Performance breakdown by market regime.
Reads from database. Pure computation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database


class RegimeAnalytics:
    """Performance breakdown by market regime (BUY_MODE, SELL_MODE, etc.)."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()

    def compute_all(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Compute regime performance breakdown."""
        if signals is None:
            signals = self._db.get_all_signals()

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        if not closed:
            return {"regimes": {}, "summary": {}}

        # Group by regime
        regimes: Dict[str, List[Dict]] = {}
        for t in closed:
            regime = t.get("regime", "unknown")
            regimes.setdefault(regime, []).append(t)

        # Compute per-regime stats
        regime_stats = {}
        for regime, trades in regimes.items():
            wins = sum(1 for t in trades if t.get("result") == "win")
            total = len(trades)
            pnl = sum(t.get("pnl", 0) for t in trades)
            gross_wins = sum(t.get("pnl", 0) for t in trades if t.get("result") == "win")
            gross_losses = abs(sum(t.get("pnl", 0) for t in trades if t.get("result") == "loss"))

            regime_stats[regime] = {
                "trades": total,
                "wins": wins,
                "losses": total - wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "total_pnl": round(pnl, 4),
                "avg_pnl": round(pnl / total, 4) if total > 0 else 0,
                "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else (
                    99.99 if gross_wins > 0 else 0
                ),
                "avg_confidence": round(
                    sum(t.get("confidence", 0) for t in trades) / total * 100, 1
                ) if total > 0 else 0,
            }

        # Summary: best/worst regime
        best = max(regime_stats.items(), key=lambda x: x[1]["total_pnl"]) if regime_stats else ("", {})
        worst = min(regime_stats.items(), key=lambda x: x[1]["total_pnl"]) if regime_stats else ("", {})

        return {
            "regimes": regime_stats,
            "summary": {
                "best_regime": best[0] if isinstance(best, tuple) else "",
                "best_pnl": best[1].get("total_pnl", 0) if isinstance(best, tuple) and isinstance(best[1], dict) else 0,
                "worst_regime": worst[0] if isinstance(worst, tuple) else "",
                "worst_pnl": worst[1].get("total_pnl", 0) if isinstance(worst, tuple) and isinstance(worst[1], dict) else 0,
                "total_regimes": len(regime_stats),
            },
        }

    def get_regime(self, regime: str) -> Dict[str, Any]:
        """Get detailed stats for a specific regime."""
        all_data = self.compute_all()
        return all_data.get("regimes", {}).get(regime, {})
