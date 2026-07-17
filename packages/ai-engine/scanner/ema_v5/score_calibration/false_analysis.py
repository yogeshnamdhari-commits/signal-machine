"""
False Positive & False Negative Analysis — Phases 7 & 8.

Phase 7: Identify candidates rejected by EMA_V5 that later achieved 2R/3R/5R/10R.
Phase 8: Identify candidates that would have passed but later failed.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class FalseAnalysis:
    """Identifies false negatives and false positives in the scoring model."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    COMPONENTS = ["trend_score", "pullback_score", "candle_score", "volume_score", "regime_score"]
    COMP_LABELS = {
        "trend_score": "trend",
        "pullback_score": "pullback",
        "candle_score": "candle",
        "volume_score": "volume",
        "regime_score": "regime",
    }

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    # ── Phase 7: False Negative Analysis ─────────────────────────

    def false_negatives(self, min_rr: float = 2.0) -> Dict:
        """Find rejected candidates that would have produced ≥min_rr return."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT symbol, timestamp, confidence, direction, entry_price,
                   stop_loss, take_profit, trend_score, pullback_score,
                   candle_score, volume_score, regime_score, return_pct,
                   mfe, mae, rr_achieved, rejection_reason, atr_14, volume_ratio
            FROM candidates
            WHERE passed = 0 AND outcome_tracked = 1 AND rr_achieved >= ?
            ORDER BY rr_achieved DESC
        """, (min_rr,))
        rows = cur.fetchall()

        false_neg = []
        for r in rows:
            # Determine which component was the primary limiter
            comp_scores = {
                "trend": r[7] or 0,
                "pullback": r[8] or 0,
                "candle": r[9] or 0,
                "volume": r[10] or 0,
                "regime": r[11] or 0,
            }
            min_comp = min(comp_scores, key=comp_scores.get)

            false_neg.append({
                "symbol": r[0],
                "timestamp": r[1],
                "confidence": r[2],
                "direction": r[3],
                "entry": r[4],
                "sl": r[5],
                "tp": r[6],
                "trend": r[7],
                "pullback": r[8],
                "candle": r[9],
                "volume": r[10],
                "regime": r[11],
                "return_pct": r[12],
                "mfe": r[13],
                "mae": r[14],
                "rr_achieved": r[15],
                "rejection_reason": r[16],
                "atr_14": r[17],
                "volume_ratio": r[18],
                "weakest_component": min_comp,
                "weakest_score": comp_scores[min_comp],
            })

        # Aggregate: which component rejects the most profitable trades
        comp_rejection_counts = {}
        for fn in false_neg:
            comp = fn["weakest_component"]
            comp_rejection_counts[comp] = comp_rejection_counts.get(comp, 0) + 1

        # Sort by rejection frequency
        ranked = sorted(comp_rejection_counts.items(), key=lambda x: x[1], reverse=True)

        # Lost profit estimation
        total_lost_profit = sum(fn["return_pct"] or 0 for fn in false_neg)
        avg_lost_profit = total_lost_profit / len(false_neg) if false_neg else 0
        missed_win_rate = len([fn for fn in false_neg if (fn["return_pct"] or 0) > 0]) / len(false_neg) * 100 if false_neg else 0

        return {
            "count": len(false_neg),
            "min_rr_threshold": min_rr,
            "trades": false_neg[:50],
            "component_rejection_ranking": [
                {"component": comp, "count": cnt, "pct": round(cnt / len(false_neg) * 100, 1) if false_neg else 0}
                for comp, cnt in ranked
            ],
            "total_lost_profit_pct": round(total_lost_profit, 3),
            "avg_lost_profit_pct": round(avg_lost_profit, 3),
            "missed_win_rate": round(missed_win_rate, 1),
            "missed_expectancy": round(avg_lost_profit, 3),
        }

    # ── Phase 8: False Positive Analysis ─────────────────────────

    def false_positives(self) -> Dict:
        """Identify candidates that passed the gate but later failed."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT symbol, timestamp, confidence, direction, entry_price,
                   stop_loss, take_profit, trend_score, pullback_score,
                   candle_score, volume_score, regime_score, return_pct,
                   mfe, mae, rr_achieved, rejection_reason, atr_14, volume_ratio
            FROM candidates
            WHERE passed = 1 AND outcome_tracked = 1 AND return_pct <= 0
            ORDER BY return_pct ASC
        """)
        rows = cur.fetchall()

        false_pos = []
        for r in rows:
            comp_scores = {
                "trend": r[7] or 0,
                "pullback": r[8] or 0,
                "candle": r[9] or 0,
                "volume": r[10] or 0,
                "regime": r[11] or 0,
            }
            # Which component was strongest (failed to prevent the loss)
            max_comp = max(comp_scores, key=comp_scores.get)

            false_pos.append({
                "symbol": r[0],
                "timestamp": r[1],
                "confidence": r[2],
                "direction": r[3],
                "entry": r[4],
                "sl": r[5],
                "tp": r[6],
                "trend": r[7],
                "pullback": r[8],
                "candle": r[9],
                "volume": r[10],
                "regime": r[11],
                "return_pct": r[12],
                "mfe": r[13],
                "mae": r[14],
                "rr_achieved": r[15],
                "weakest_prevention": max_comp,
                "weakest_score": comp_scores[max_comp],
            })

        # Which component correctly prevented losses
        comp_prevention_counts = {}
        for fp in false_pos:
            comp = fp["weakest_prevention"]
            comp_prevention_counts[comp] = comp_prevention_counts.get(comp, 0) + 1

        total_prevented_loss = sum(abs(fp["return_pct"] or 0) for fp in false_pos)
        avg_prevented_loss = total_prevented_loss / len(false_pos) if false_pos else 0

        return {
            "count": len(false_pos),
            "trades": false_pos[:50],
            "component_prevention_ranking": [
                {"component": comp, "count": cnt}
                for comp, cnt in sorted(comp_prevention_counts.items(), key=lambda x: x[1], reverse=True)
            ],
            "total_prevented_loss_pct": round(total_prevented_loss, 3),
            "avg_prevented_loss_pct": round(avg_prevented_loss, 3),
        }

    # ── Summary ──────────────────────────────────────────────────

    def summary(self) -> Dict:
        """Combined false positive/negative summary."""
        fn = self.false_negatives(min_rr=2.0)
        fp = self.false_positives()

        return {
            "false_negatives": {
                "count": fn["count"],
                "missed_win_rate": fn["missed_win_rate"],
                "avg_lost_profit": fn["avg_lost_profit_pct"],
                "top_rejecting_component": fn["component_rejection_ranking"][0] if fn["component_rejection_ranking"] else None,
            },
            "false_positives": {
                "count": fp["count"],
                "total_prevented_loss": fp["total_prevented_loss_pct"],
                "top_prevention_component": fp["component_prevention_ranking"][0] if fp["component_prevention_ranking"] else None,
            },
        }

    def close(self) -> None:
        self._conn.close()
