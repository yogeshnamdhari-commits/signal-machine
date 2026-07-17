"""
EMA_V5 Threshold Calibration — Statistical analysis of threshold tradeoffs.

Never lower thresholds automatically. Instead compute:
  If threshold = 90: Win Rate, Profit Factor, Expectancy
  If threshold = 88: Win Rate, Profit Factor, Expectancy
  If threshold = 86: Win Rate, Profit Factor, Expectancy

Recommend only statistically superior thresholds.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from .config import ema_v5_config


class ThresholdCalibration:
    """Analyze candidate outcomes at different confidence thresholds."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    # Candidate thresholds to evaluate
    THRESHOLDS = [86.0, 87.0, 88.0, 89.0, 90.0, 91.0, 92.0, 93.0, 94.0, 95.0]

    def __init__(self) -> None:
        self._db_path = self.DB_PATH
        self._last_analysis_time: float = 0
        self._analysis_interval: float = 3600  # Re-analyze every hour
        self._cached_results: Optional[Dict] = None

    def _connect(self) -> Optional[sqlite3.Connection]:
        """Connect to calibration database."""
        try:
            if not self._db_path.exists():
                return None
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e:
            logger.debug("Threshold calibration DB connect error: {}", e)
            return None

    def analyze(self, force: bool = False) -> Dict:
        """Run full threshold analysis across all stored candidates.

        Returns:
            {
                "thresholds": {90: {...}, 88: {...}, ...},
                "recommendation": str,
                "best_threshold": float,
                "current_threshold": float,
                "total_candidates": int,
                "total_with_outcomes": int,
            }
        """
        now = time.time()
        if not force and self._cached_results and (now - self._last_analysis_time) < self._analysis_interval:
            return self._cached_results

        conn = self._connect()
        if not conn:
            return {"error": "Calibration database not available", "thresholds": {}}

        try:
            cur = conn.cursor()

            # Get total candidates and those with outcomes
            cur.execute("SELECT COUNT(*) FROM candidates")
            total = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM candidates WHERE outcome_tracked = 1 AND return_pct IS NOT NULL")
            total_with_outcomes = cur.fetchone()[0]

            # Get all candidates with outcomes
            cur.execute("""
                SELECT confidence, return_pct, mfe, mae, passed,
                       symbol, timestamp, direction, entry_price
                FROM candidates
                WHERE outcome_tracked = 1 AND return_pct IS NOT NULL
                ORDER BY confidence DESC
            """)
            rows = cur.fetchall()

            if not rows:
                self._cached_results = {
                    "thresholds": {},
                    "recommendation": "No outcome data available yet",
                    "total_candidates": total,
                    "total_with_outcomes": 0,
                }
                return self._cached_results

            # Evaluate each threshold
            threshold_results = {}
            for threshold in self.THRESHOLDS:
                # Candidates that would have been accepted at this threshold
                accepted = [r for r in rows if r["confidence"] >= threshold]

                if not accepted:
                    threshold_results[threshold] = {
                        "n_candidates": 0,
                        "win_rate": 0,
                        "profit_factor": 0,
                        "expectancy": 0,
                        "avg_return": 0,
                        "avg_rr": 0,
                        "total_pnl_pct": 0,
                        "max_drawdown": 0,
                        "sharpe_approx": 0,
                    }
                    continue

                # Win rate
                wins = [r for r in accepted if r["return_pct"] > 0]
                losses = [r for r in accepted if r["return_pct"] <= 0]
                win_rate = len(wins) / len(accepted) * 100

                # Profit factor
                gross_profit = sum(r["return_pct"] for r in wins) if wins else 0
                gross_loss = abs(sum(r["return_pct"] for r in losses)) if losses else 0.001
                profit_factor = gross_profit / gross_loss

                # Expectancy (average return per trade)
                avg_return = sum(r["return_pct"] for r in accepted) / len(accepted)

                # Average MAE and MFE
                avg_mfe = sum(r["mfe"] or 0 for r in accepted) / len(accepted)
                avg_mae = sum(r["mae"] or 0 for r in accepted) / len(accepted)

                # Approximate Sharpe (returns / std)
                returns = [r["return_pct"] for r in accepted]
                mean_ret = sum(returns) / len(returns)
                std_ret = (sum((r - mean_ret) ** 2 for r in returns) / max(len(returns) - 1, 1)) ** 0.5
                sharpe = mean_ret / max(std_ret, 0.001)

                threshold_results[threshold] = {
                    "n_candidates": len(accepted),
                    "win_rate": round(win_rate, 1),
                    "profit_factor": round(profit_factor, 2),
                    "expectancy": round(avg_return, 3),
                    "avg_return": round(avg_return, 3),
                    "avg_mfe": round(avg_mfe, 3),
                    "avg_mae": round(avg_mae, 3),
                    "sharpe_approx": round(sharpe, 2),
                    "gross_profit_pct": round(gross_profit, 2),
                    "gross_loss_pct": round(gross_loss, 2),
                }

            # Find best threshold by profit factor (with minimum 20 candidates)
            best_threshold = ema_v5_config.confidence.min_confidence
            best_pf = 0
            for t, data in threshold_results.items():
                if data["n_candidates"] >= 20 and data["profit_factor"] > best_pf:
                    best_pf = data["profit_factor"]
                    best_threshold = t

            current = ema_v5_config.confidence.min_confidence
            current_data = threshold_results.get(current, {})

            # Generate recommendation
            if best_threshold < current:
                recommendation = (
                    f"CONSIDER LOWERING: threshold={best_threshold:.0f} has PF={best_pf:.2f} "
                    f"vs current={current:.0f} PF={current_data.get('profit_factor', 0):.2f} "
                    f"(need 50+ trades for statistical significance)"
                )
            elif best_threshold > current:
                recommendation = (
                    f"CONSIDER RAISING: threshold={best_threshold:.0f} has PF={best_pf:.2f} "
                    f"vs current={current:.0f} PF={current_data.get('profit_factor', 0):.2f}"
                )
            else:
                recommendation = (
                    f"CURRENT THRESHOLD OPTIMAL: threshold={current:.0f} PF={best_pf:.2f}"
                )

            self._cached_results = {
                "thresholds": threshold_results,
                "recommendation": recommendation,
                "best_threshold": best_threshold,
                "best_pf": best_pf,
                "current_threshold": current,
                "total_candidates": total,
                "total_with_outcomes": total_with_outcomes,
                "analysis_time": time.time(),
            }

            self._last_analysis_time = now
            return self._cached_results

        except Exception as e:
            logger.error("Threshold calibration analysis error: {}", e)
            return {"error": str(e), "thresholds": {}}
        finally:
            conn.close()

    def get_rejection_analysis(self) -> Dict:
        """Analyze rejected candidates — would lowering threshold have improved results?

        Returns:
            {
                "close_misses": [...],  # candidates within 5 points of threshold
                "would_have_passed": {...},  # stats for candidates between new and old threshold
            }
        """
        conn = self._connect()
        if not conn:
            return {"error": "DB not available"}

        try:
            cur = conn.cursor()
            current = ema_v5_config.confidence.min_confidence

            # Close misses: candidates within 5 points below threshold
            cur.execute("""
                SELECT confidence, return_pct, mfe, mae, symbol, direction, entry_price, timestamp
                FROM candidates
                WHERE confidence >= ? AND confidence < ? AND outcome_tracked = 1
                ORDER BY confidence DESC
            """, (current - 5, current))
            close_misses = [dict(r) for r in cur.fetchall()]

            # Would-have-passed at lower threshold
            would_pass_stats = {}
            for lower in [88, 86, 84]:
                cur.execute("""
                    SELECT confidence, return_pct, symbol
                    FROM candidates
                    WHERE confidence >= ? AND confidence < ? AND outcome_tracked = 1
                """, (lower, current))
                rows = cur.fetchall()
                if rows:
                    returns = [r["return_pct"] for r in rows if r["return_pct"] is not None]
                    wins = [r for r in returns if r > 0]
                    would_pass_stats[lower] = {
                        "n_trades": len(rows),
                        "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else 0,
                        "avg_return": round(sum(returns) / len(returns), 3) if returns else 0,
                        "would_have_added_trades": len(rows),
                    }

            return {
                "close_misses_count": len(close_misses),
                "close_misses": close_misses[:20],  # Top 20
                "would_have_passed": would_pass_stats,
            }
        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()
