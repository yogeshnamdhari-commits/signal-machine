"""
EMA_V5 Equity Curve — Cumulative PnL tracking and visualization data.
Reads from database. Pure computation.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from ..storage.database import EMAv5Database


class EquityCurve:
    """Equity curve data for EMA_V5 trades."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()

    def compute(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Compute full equity curve data."""
        if signals is None:
            signals = self._db.get_all_signals()

        closed = sorted(
            [s for s in signals if s.get("result") in ("win", "loss")],
            key=lambda s: s.get("timestamp", 0),
        )

        if not closed:
            return {"points": [], "peak": 0, "trough": 0, "current": 0}

        # Build curve points
        points = []
        cumulative = 0.0
        peak = 0.0
        for t in closed:
            pnl = t.get("pnl", 0)
            cumulative += pnl
            peak = max(peak, cumulative)
            drawdown = peak - cumulative
            drawdown_pct = (drawdown / abs(peak) * 100) if peak != 0 else 0

            points.append({
                "timestamp": t.get("timestamp", 0),
                "date": t.get("date", ""),
                "symbol": t.get("symbol", ""),
                "side": t.get("side", ""),
                "pnl": round(pnl, 4),
                "cumulative": round(cumulative, 4),
                "peak": round(peak, 4),
                "drawdown": round(drawdown, 4),
                "drawdown_pct": round(drawdown_pct, 2),
                "result": t.get("result", ""),
            })

        # Daily aggregation for chart
        daily = self._aggregate_daily(points)

        # Milestones
        milestones = self._compute_milestones(points)

        return {
            "points": points,
            "daily": daily,
            "peak": round(peak, 4),
            "trough": round(min(p["cumulative"] for p in points), 4),
            "current": round(points[-1]["cumulative"], 4),
            "total_return_pct": round(
                points[-1]["cumulative"] / abs(points[0]["cumulative"]) * 100
                if points[0]["cumulative"] != 0 else 0, 1
            ),
            "milestones": milestones,
        }

    def _aggregate_daily(self, points: List[Dict]) -> List[Dict]:
        """Aggregate equity points by day."""
        daily: Dict[str, Dict] = {}
        for p in points:
            day = p.get("date", "")
            if not day:
                continue
            if day not in daily:
                daily[day] = {"date": day, "pnl": 0, "trades": 0, "wins": 0, "equity": 0}
            daily[day]["pnl"] += p.get("pnl", 0)
            daily[day]["trades"] += 1
            if p.get("result") == "win":
                daily[day]["wins"] += 1

        # Carry forward equity
        equity = 0
        result = []
        for day in sorted(daily.keys()):
            d = daily[day]
            equity += d["pnl"]
            d["equity"] = round(equity, 4)
            d["win_rate"] = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] > 0 else 0
            result.append(d)

        return result

    def _compute_milestones(self, points: List[Dict]) -> List[Dict]:
        """Compute key equity milestones."""
        milestones = []
        if not points:
            return milestones

        # First profit
        for p in points:
            if p.get("pnl", 0) > 0:
                milestones.append({"label": "First Win", "cumulative": p["cumulative"], "date": p["date"]})
                break

        # First loss
        for p in points:
            if p.get("pnl", 0) < 0:
                milestones.append({"label": "First Loss", "cumulative": p["cumulative"], "date": p["date"]})
                break

        # Peak
        peak_point = max(points, key=lambda p: p.get("cumulative", 0))
        milestones.append({"label": "All-Time Peak", "cumulative": peak_point["cumulative"], "date": peak_point["date"]})

        # Highest single win
        best = max(points, key=lambda p: p.get("pnl", 0))
        milestones.append({"label": "Best Trade", "cumulative": best["pnl"], "date": best["date"], "symbol": best.get("symbol")})

        # Worst single loss
        worst = min(points, key=lambda p: p.get("pnl", 0))
        milestones.append({"label": "Worst Trade", "cumulative": worst["pnl"], "date": worst["date"], "symbol": worst.get("symbol")})

        return milestones

    def get_latest(self, n: int = 30) -> List[Dict]:
        """Get last N equity points for mini chart."""
        data = self.compute()
        return data["points"][-n:]
