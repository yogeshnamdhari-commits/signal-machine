"""
EMA_V5 Symbol Analytics — Per-symbol performance breakdown.
Reads from database. Pure computation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database


class SymbolAnalytics:
    """Per-symbol performance analytics."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()

    def compute_all(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Compute per-symbol performance breakdown."""
        if signals is None:
            signals = self._db.get_all_signals()

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        if not closed:
            return {"symbols": {}, "summary": {}}

        # Group by symbol
        symbols: Dict[str, List[Dict]] = {}
        for t in closed:
            sym = t.get("symbol", "")
            symbols.setdefault(sym, []).append(t)

        # Compute per-symbol stats
        symbol_stats = {}
        for sym, trades in symbols.items():
            wins = sum(1 for t in trades if t.get("result") == "win")
            total = len(trades)
            pnl = sum(t.get("pnl", 0) for t in trades)
            gross_wins = sum(t.get("pnl", 0) for t in trades if t.get("result") == "win")
            gross_losses = abs(sum(t.get("pnl", 0) for t in trades if t.get("result") == "loss"))
            longs = sum(1 for t in trades if t.get("side") == "LONG")
            shorts = total - longs

            symbol_stats[sym] = {
                "trades": total,
                "wins": wins,
                "losses": total - wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "total_pnl": round(pnl, 4),
                "avg_pnl": round(pnl / total, 4) if total > 0 else 0,
                "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else (
                    99.99 if gross_wins > 0 else 0
                ),
                "longs": longs,
                "shorts": shorts,
                "avg_confidence": round(
                    sum(t.get("confidence", 0) for t in trades) / total * 100, 1
                ) if total > 0 else 0,
                "best_trade": round(max(t.get("pnl", 0) for t in trades), 4),
                "worst_trade": round(min(t.get("pnl", 0) for t in trades), 4),
            }

        # Summary: top/bottom performers
        sorted_syms = sorted(symbol_stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
        top_5 = [(s, v) for s, v in sorted_syms[:5]]
        bottom_5 = [(s, v) for s, v in sorted_syms[-5:]]

        return {
            "symbols": symbol_stats,
            "summary": {
                "total_symbols": len(symbol_stats),
                "profitable_symbols": sum(1 for v in symbol_stats.values() if v["total_pnl"] > 0),
                "unprofitable_symbols": sum(1 for v in symbol_stats.values() if v["total_pnl"] <= 0),
                "top_5": [{"symbol": s, **v} for s, v in top_5],
                "bottom_5": [{"symbol": s, **v} for s, v in bottom_5],
                "most_traded": sorted_syms[0][0] if sorted_syms else "",
                "most_profitable": sorted_syms[0][0] if sorted_syms else "",
                "least_profitable": sorted_syms[-1][0] if sorted_syms else "",
            },
        }

    def get_symbol(self, symbol: str) -> Dict[str, Any]:
        """Get detailed stats for a specific symbol."""
        all_data = self.compute_all()
        return all_data.get("symbols", {}).get(symbol, {})

    def get_ranking(self, metric: str = "total_pnl") -> List[Dict]:
        """Get symbols ranked by a metric."""
        all_data = self.compute_all()
        symbols = all_data.get("symbols", {})
        sorted_syms = sorted(symbols.items(), key=lambda x: x[1].get(metric, 0), reverse=True)
        return [{"symbol": s, **v} for s, v in sorted_syms]
