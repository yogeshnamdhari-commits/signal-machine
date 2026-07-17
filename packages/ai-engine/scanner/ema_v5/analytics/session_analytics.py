"""
EMA_V5 Session Analytics — Performance by trading session (London, NY, Asia).
Reads from database. Pure computation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database


class SessionAnalytics:
    """Performance breakdown by trading session."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()

    def compute_all(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Compute session performance breakdown."""
        if signals is None:
            signals = self._db.get_all_signals()

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        if not closed:
            return {"sessions": {}, "hourly": {}, "summary": {}}

        # Group by session
        sessions: Dict[str, List[Dict]] = {}
        for t in closed:
            session = t.get("session", "unknown")
            sessions.setdefault(session, []).append(t)

        # Compute per-session stats
        session_stats = {}
        for session, trades in sessions.items():
            wins = sum(1 for t in trades if t.get("result") == "win")
            total = len(trades)
            pnl = sum(t.get("pnl", 0) for t in trades)

            session_stats[session] = {
                "trades": total,
                "wins": wins,
                "losses": total - wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "total_pnl": round(pnl, 4),
                "avg_pnl": round(pnl / total, 4) if total > 0 else 0,
            }

        # Hourly breakdown (from timestamp)
        hourly = self._hourly_breakdown(closed)

        # Summary
        best_session = max(session_stats.items(), key=lambda x: x[1]["total_pnl"]) if session_stats else ("", {})
        worst_session = min(session_stats.items(), key=lambda x: x[1]["total_pnl"]) if session_stats else ("", {})

        return {
            "sessions": session_stats,
            "hourly": hourly,
            "summary": {
                "best_session": best_session[0] if isinstance(best_session, tuple) else "",
                "best_pnl": best_session[1].get("total_pnl", 0) if isinstance(best_session, tuple) and isinstance(best_session[1], dict) else 0,
                "worst_session": worst_session[0] if isinstance(worst_session, tuple) else "",
                "worst_pnl": worst_session[1].get("total_pnl", 0) if isinstance(worst_session, tuple) and isinstance(worst_session[1], dict) else 0,
            },
        }

    def _hourly_breakdown(self, trades: List[Dict]) -> Dict[int, Dict]:
        """Break down performance by hour of day (UTC)."""
        hourly: Dict[int, List[Dict]] = {}
        for t in trades:
            ts = t.get("timestamp", 0)
            if ts:
                import datetime
                hour = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).hour
                hourly.setdefault(hour, []).append(t)

        result = {}
        for hour in sorted(hourly.keys()):
            trades_h = hourly[hour]
            wins = sum(1 for t in trades_h if t.get("result") == "win")
            total = len(trades_h)
            pnl = sum(t.get("pnl", 0) for t in trades_h)
            result[hour] = {
                "trades": total,
                "wins": wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "total_pnl": round(pnl, 4),
            }
        return result

    def get_session(self, session: str) -> Dict[str, Any]:
        """Get detailed stats for a specific session."""
        all_data = self.compute_all()
        return all_data.get("sessions", {}).get(session, {})
