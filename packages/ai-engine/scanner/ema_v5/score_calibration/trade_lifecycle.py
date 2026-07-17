"""
Trade Lifecycle Validator — Verifies stop-loss execution, take-profit execution,
partial exits, and proper trade closure.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class TradeLifecycleValidator:
    """Validates trade lifecycle: SL/TP execution, partial exits, closure quality."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "institutional_v1.db"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def validate(self) -> Dict:
        """Full trade lifecycle validation."""
        cur = self._conn.cursor()

        # Get all closed trades
        cur.execute("""
            SELECT symbol, side, entry_price, stop_loss, take_profit,
                   pnl, exit_reason, confidence, strategy_version,
                   opened_at, closed_at, hold_minutes,
                   mfe_pct, mae_pct, realized_r, planned_rr,
                   highest_pnl, take_profit_2, take_profit_3, current_tp_index
            FROM positions
            WHERE status = 'closed'
            ORDER BY closed_at DESC
        """)
        rows = cur.fetchall()

        if not rows:
            return {"status": "no_data", "message": "No closed trades"}

        # Get open trades
        cur.execute("""
            SELECT symbol, side, entry_price, stop_loss, take_profit,
                   pnl, confidence, strategy_version, opened_at,
                   take_profit_2, take_profit_3, current_tp_index
            FROM positions
            WHERE status = 'open'
        """)
        open_rows = cur.fetchall()

        # Analyze exit reasons
        exit_analysis = {}
        for r in rows:
            reason = r["exit_reason"] or "unknown"
            if reason not in exit_analysis:
                exit_analysis[reason] = {"count": 0, "wins": 0, "total_pnl": 0, "trades": []}
            exit_analysis[reason]["count"] += 1
            if (r["pnl"] or 0) > 0:
                exit_analysis[reason]["wins"] += 1
            exit_analysis[reason]["total_pnl"] += r["pnl"] or 0
            exit_analysis[reason]["trades"].append(r["symbol"])

        # SL execution analysis
        sl_trades = [r for r in rows if r["exit_reason"] == "stop_loss"]
        sl_analysis = {
            "count": len(sl_trades),
            "avg_mae": sum(r["mae_pct"] or 0 for r in sl_trades) / len(sl_trades) if sl_trades else 0,
            "symbols": [r["symbol"] for r in sl_trades],
        }

        # TP execution analysis
        tp_trades = [r for r in rows if r["exit_reason"] and "take_profit" in r["exit_reason"]]
        tp_analysis = {
            "count": len(tp_trades),
            "avg_mfe": sum(r["mfe_pct"] or 0 for r in tp_trades) / len(tp_trades) if tp_trades else 0,
            "symbols": [r["symbol"] for r in tp_trades],
        }

        # Trailing stop analysis
        ts_trades = [r for r in rows if r["exit_reason"] and "trailing_stop" in r["exit_reason"]]
        ts_analysis = {
            "count": len(ts_trades),
            "symbols": [r["symbol"] for r in ts_trades],
        }

        # Time exit analysis
        te_trades = [r for r in rows if r["exit_reason"] and "time_exit" in r["exit_reason"]]
        te_analysis = {
            "count": len(te_trades),
            "avg_hold": sum(r["hold_minutes"] or 0 for r in te_trades) / len(te_trades) if te_trades else 0,
            "symbols": [r["symbol"] for r in te_trades],
            "total_pnl": sum(r["pnl"] or 0 for r in te_trades),
        }

        # Partial exit tracking
        partial_exits = [r for r in rows if r["take_profit_2"] or r["take_profit_3"]]
        partial_analysis = {
            "count": len(partial_exits),
            "avg_tp_index": sum(r["current_tp_index"] or 0 for r in partial_exits) / len(partial_exits) if partial_exits else 0,
        }

        # R-multiple analysis
        r_multiples = [r["realized_r"] or 0 for r in rows if r["realized_r"] is not None]
        r_analysis = {
            "avg_r": sum(r_multiples) / len(r_multiples) if r_multiples else 0,
            "max_r": max(r_multiples) if r_multiples else 0,
            "min_r": min(r_multiples) if r_multiples else 0,
            "positive_r_count": sum(1 for r in r_multiples if r > 0),
            "negative_r_count": sum(1 for r in r_multiples if r < 0),
        }

        # Hold time analysis
        hold_times = [r["hold_minutes"] or 0 for r in rows if r["hold_minutes"]]
        hold_analysis = {
            "avg_minutes": sum(hold_times) / len(hold_times) if hold_times else 0,
            "median_minutes": sorted(hold_times)[len(hold_times) // 2] if hold_times else 0,
            "max_minutes": max(hold_times) if hold_times else 0,
            "min_minutes": min(hold_times) if hold_times else 0,
        }

        # Open trade health
        open_health = []
        for r in open_rows:
            open_health.append({
                "symbol": r["symbol"],
                "side": r["side"],
                "entry": r["entry_price"],
                "sl": r["stop_loss"],
                "tp": r["take_profit"],
                "pnl": r["pnl"],
                "confidence": round((r["confidence"] or 0) * 100, 1),
                "strategy": r["strategy_version"],
            })

        return {
            "status": "complete",
            "total_closed": len(rows),
            "total_open": len(open_rows),
            "exit_analysis": {
                reason: {
                    "count": data["count"],
                    "wins": data["wins"],
                    "win_rate": round(data["wins"] / data["count"] * 100, 1) if data["count"] > 0 else 0,
                    "total_pnl": round(data["total_pnl"], 3),
                    "avg_pnl": round(data["total_pnl"] / data["count"], 3) if data["count"] > 0 else 0,
                }
                for reason, data in exit_analysis.items()
            },
            "stop_loss": sl_analysis,
            "take_profit": tp_analysis,
            "trailing_stop": ts_analysis,
            "time_exit": te_analysis,
            "partial_exits": partial_analysis,
            "r_multiples": r_analysis,
            "hold_time": hold_analysis,
            "open_trades": open_health,
        }

    def close(self) -> None:
        self._conn.close()
