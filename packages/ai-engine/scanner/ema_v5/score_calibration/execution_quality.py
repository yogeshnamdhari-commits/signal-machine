"""
Execution Quality Analyzer — Verifies entry price accuracy, slippage,
and order execution quality for all trades.

Reads from the production positions database (institutional_v1.db).
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class ExecutionQualityAnalyzer:
    """Analyzes execution quality: slippage, entry accuracy, fill quality."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "institutional_v1.db"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def analyze(self) -> Dict:
        """Full execution quality analysis."""
        cur = self._conn.cursor()

        # Get all closed trades with signal data
        cur.execute("""
            SELECT p.symbol, p.side, p.entry_price, p.stop_loss, p.take_profit,
                   p.pnl, p.fees, p.status, p.exit_reason, p.confidence,
                   p.strategy_version, p.opened_at, p.closed_at, p.hold_minutes,
                   p.mfe_pct, p.mae_pct, p.realized_r, p.planned_rr,
                   p.highest_pnl, p.leverage, p.quantity
            FROM positions p
            WHERE p.status = 'closed'
            ORDER BY p.closed_at DESC
        """)
        rows = cur.fetchall()

        if not rows:
            return {"status": "no_data", "message": "No closed trades to analyze"}

        trades = []
        slippage_data = []
        fee_analysis = []

        for r in rows:
            entry = r["entry_price"]
            sl = r["stop_loss"]
            tp = r["take_profit"]
            pnl = r["pnl"] or 0
            fees = r["fees"] or 0
            confidence = r["confidence"] or 0
            side = r["side"]
            hold = r["hold_minutes"] or 0
            mfe = r["mfe_pct"] or 0
            mae = r["mae_pct"] or 0
            realized_r = r["realized_r"] or 0
            planned_rr = r["planned_rr"] or 0
            highest_pnl = r["highest_pnl"] or 0
            leverage = r["leverage"] or 1
            qty = r["quantity"] or 0

            # Entry quality: how close to intended entry
            # (We assume intended entry = signal entry, actual = position entry)
            # For now, use SL distance as proxy for entry quality
            if sl and entry:
                risk_per_unit = abs(entry - sl)
                risk_pct = (risk_per_unit / entry * 100) if entry > 0 else 0
            else:
                risk_per_unit = 0
                risk_pct = 0

            # R-multiple achieved
            if risk_per_unit > 0:
                if side in ("LONG", "BUY"):
                    r_achieved = (pnl / (risk_per_unit * qty)) if qty > 0 else 0
                else:
                    r_achieved = (pnl / (risk_per_unit * qty)) if qty > 0 else 0
            else:
                r_achieved = 0

            # Slippage proxy: difference between highest_pnl and final pnl
            # If highest_pnl >> final pnl, there was slippage or poor exit timing
            slippage_pct = highest_pnl - (pnl / (entry * qty) * 100 if entry and qty else 0) if entry and qty else 0

            # Fee ratio: fees as % of gross profit
            gross_profit = max(pnl + fees, 0)
            fee_ratio = (fees / gross_profit * 100) if gross_profit > 0 else 0

            trade = {
                "symbol": r["symbol"],
                "side": side,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "pnl": round(pnl, 3),
                "fees": round(fees, 4),
                "confidence": round(confidence * 100, 1),  # Convert to 0-100
                "strategy": r["strategy_version"],
                "exit_reason": r["exit_reason"],
                "hold_minutes": round(hold, 1),
                "mfe_pct": round(mfe, 3),
                "mae_pct": round(mae, 3),
                "realized_r": round(realized_r, 2),
                "planned_rr": round(planned_rr, 2),
                "highest_pnl_pct": round(highest_pnl, 3),
                "leverage": leverage,
                "risk_pct": round(risk_pct, 3),
                "fee_ratio": round(fee_ratio, 2),
                "slippage_proxy": round(slippage_pct, 3),
            }
            trades.append(trade)

            slippage_data.append({
                "symbol": r["symbol"],
                "mfe_pct": mfe,
                "mae_pct": mae,
                "highest_pnl": highest_pnl,
                "final_pnl": pnl,
                "slippage": round(slippage_pct, 3),
            })

            fee_analysis.append({
                "symbol": r["symbol"],
                "fees": fees,
                "pnl": pnl,
                "fee_ratio": round(fee_ratio, 2),
            })

        # Aggregate metrics
        total_trades = len(trades)
        winning = [t for t in trades if t["pnl"] > 0]
        losing = [t for t in trades if t["pnl"] <= 0]

        avg_slippage = sum(s["slippage"] for s in slippage_data) / len(slippage_data) if slippage_data else 0
        max_slippage = max(s["slippage"] for s in slippage_data) if slippage_data else 0
        avg_fee_ratio = sum(f["fee_ratio"] for f in fee_analysis) / len(fee_analysis) if fee_analysis else 0

        # Entry accuracy: % of trades where MFE > 0 (price moved favorably at least once)
        favorable_entries = sum(1 for t in trades if t["mfe_pct"] > 0)
        entry_accuracy = favorable_entries / total_trades * 100 if total_trades else 0

        # Exit quality: % of trades where we captured >50% of MFE
        exit_quality_trades = []
        for t in trades:
            if t["mfe_pct"] > 0:
                captured = abs(t["pnl"]) / abs(t["mfe_pct"]) if t["mfe_pct"] != 0 else 0
                exit_quality_trades.append(min(captured, 1.0))
        avg_exit_quality = sum(exit_quality_trades) / len(exit_quality_trades) * 100 if exit_quality_trades else 0

        return {
            "status": "complete",
            "total_trades": total_trades,
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "entry_accuracy_pct": round(entry_accuracy, 1),
            "avg_slippage_pct": round(avg_slippage, 3),
            "max_slippage_pct": round(max_slippage, 3),
            "avg_fee_ratio_pct": round(avg_fee_ratio, 2),
            "exit_quality_pct": round(avg_exit_quality, 1),
            "trades": trades,
            "slippage_data": slippage_data,
            "fee_analysis": fee_analysis,
        }

    def close(self) -> None:
        self._conn.close()
