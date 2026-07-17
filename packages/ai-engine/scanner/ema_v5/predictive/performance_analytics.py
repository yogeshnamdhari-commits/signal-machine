"""
EMA V5 Performance Analytics — Production analytics and monitoring.

Provides comprehensive analytics for production monitoring:
  - Live P&L tracking
  - Rolling performance metrics
  - Confidence correlation analysis
  - Feature importance tracking
  - Session performance
  - Symbol performance
  - Regime performance
  - Volume profile analysis
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


class PerformanceAnalytics:
    """Production analytics and monitoring for EMA V5."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"
    TRADES_DB = Path(__file__).resolve().parent.parent.parent.parent / "data" / "institutional_v1.db"

    def __init__(self) -> None:
        self._analytics_cache: Dict = {}
        self._last_update: float = 0
        self._update_interval: float = 300  # Update every 5 minutes

    def get_live_analytics(self, force: bool = False) -> Dict:
        """Get comprehensive live analytics.

        Returns:
            Dictionary with all analytics data
        """
        now = time.time()
        if not force and (now - self._last_update) < self._update_interval:
            return self._analytics_cache

        try:
            analytics = {
                "timestamp": now,
                "portfolio": self._get_portfolio_metrics(),
                "rolling": self._get_rolling_metrics(),
                "confidence": self._get_confidence_analysis(),
                "sessions": self._get_session_performance(),
                "symbols": self._get_symbol_performance(),
                "regimes": self._get_regime_performance(),
                "directional": self._get_directional_analysis(),
                "feature_importance": self._get_feature_importance(),
            }

            self._analytics_cache = analytics
            self._last_update = now
            return analytics

        except Exception as e:
            logger.error("Failed to compute analytics: {}", e)
            return {"error": str(e)}

    def _get_portfolio_metrics(self) -> Dict:
        """Get portfolio-level metrics."""
        try:
            conn = sqlite3.connect(str(self.TRADES_DB))
            cur = conn.cursor()

            # Closed trades
            cur.execute("""
                SELECT COUNT(*), SUM(pnl), 
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END)
                FROM positions 
                WHERE status='closed' AND strategy_version='ema_v5'
            """)
            row = cur.fetchone()
            n_trades = row[0] or 0
            total_pnl = row[1] or 0
            n_wins = row[2] or 0

            # Win rate
            win_rate = n_wins / n_trades if n_trades > 0 else 0

            # Average trade
            avg_trade = total_pnl / n_trades if n_trades > 0 else 0

            # Open positions
            cur.execute("""
                SELECT COUNT(*), SUM(unrealized_pnl)
                FROM positions 
                WHERE status='open' AND strategy_version='ema_v5'
            """)
            open_row = cur.fetchone()
            n_open = open_row[0] or 0
            unrealized_pnl = open_row[1] or 0

            conn.close()

            return {
                "total_trades": n_trades,
                "total_pnl": round(total_pnl, 2),
                "win_rate": round(win_rate, 4),
                "avg_trade": round(avg_trade, 2),
                "open_positions": n_open,
                "unrealized_pnl": round(unrealized_pnl, 2),
                "realized_pnl": round(total_pnl, 2),
                "total_pnl_with_unrealized": round(total_pnl + unrealized_pnl, 2),
            }

        except Exception as e:
            logger.debug("Portfolio metrics error: {}", e)
            return {}

    def _get_rolling_metrics(self) -> Dict:
        """Get rolling performance metrics."""
        try:
            conn = sqlite3.connect(str(self.TRADES_DB))
            cur = conn.cursor()

            cur.execute("""
                SELECT pnl FROM positions 
                WHERE status='closed' AND strategy_version='ema_v5'
                ORDER BY closed_at DESC
            """)
            pnls = [row[0] for row in cur.fetchall()]
            conn.close()

            if len(pnls) < 10:
                return {"error": "Insufficient data"}

            returns = np.array(pnls)

            windows = [10, 25, 50, 100]
            rolling = {}

            for w in windows:
                if len(returns) >= w:
                    window_returns = returns[-w:]
                    wins = window_returns[window_returns > 0]
                    losses = window_returns[window_returns <= 0]

                    win_rate = len(wins) / w
                    gp = np.sum(wins) if len(wins) > 0 else 0
                    gl = abs(np.sum(losses)) if len(losses) > 0 else 0.001
                    pf = gp / gl
                    avg_return = np.mean(window_returns)

                    rolling[f"last_{w}"] = {
                        "win_rate": round(win_rate, 4),
                        "profit_factor": round(pf, 4),
                        "expectancy": round(avg_return, 2),
                        "total_pnl": round(np.sum(window_returns), 2),
                    }

            return rolling

        except Exception as e:
            logger.debug("Rolling metrics error: {}", e)
            return {}

    def _get_confidence_analysis(self) -> Dict:
        """Analyze confidence vs returns correlation."""
        try:
            conn = sqlite3.connect(str(self.DB_PATH))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("""
                SELECT confidence, return_pct
                FROM candidates 
                WHERE outcome_tracked=1 AND return_pct IS NOT NULL
                ORDER BY timestamp
            """)
            rows = cur.fetchall()
            conn.close()

            if len(rows) < 25:
                return {"error": "Insufficient data"}

            confidences = np.array([r['confidence'] for r in rows])
            returns = np.array([r['return_pct'] for r in rows])

            # Correlation
            corr = self._pearson_correlation(confidences, returns)

            # Bucket analysis
            n_buckets = 5
            percentiles = np.linspace(0, 100, n_buckets + 1)
            bucket_edges = np.percentile(confidences, percentiles)

            buckets = []
            for i in range(n_buckets):
                low = bucket_edges[i]
                high = bucket_edges[i + 1]
                mask = (confidences >= low) & (confidences < high)
                if i == n_buckets - 1:
                    mask = (confidences >= low) & (confidences <= high)

                bucket_returns = returns[mask]
                if len(bucket_returns) > 0:
                    wins = bucket_returns[bucket_returns > 0]
                    wr = len(wins) / len(bucket_returns)
                    gp = np.sum(wins) if len(wins) > 0 else 0
                    gl = abs(np.sum(bucket_returns[bucket_returns <= 0])) if len(bucket_returns[bucket_returns <= 0]) > 0 else 0.001
                    pf = gp / gl

                    buckets.append({
                        "range": f"{low:.1f}-{high:.1f}",
                        "n": len(bucket_returns),
                        "win_rate": round(wr, 4),
                        "profit_factor": round(pf, 4),
                        "expectancy": round(np.mean(bucket_returns), 4),
                    })

            return {
                "correlation": round(corr, 4),
                "is_positive": corr > 0,
                "buckets": buckets,
                "total_samples": len(rows),
            }

        except Exception as e:
            logger.debug("Confidence analysis error: {}", e)
            return {}

    def _get_session_performance(self) -> Dict:
        """Get performance by trading session."""
        # Simplified - return placeholder
        return {
            "note": "Session performance requires timestamp analysis"
        }

    def _get_symbol_performance(self) -> Dict:
        """Get performance by symbol."""
        try:
            conn = sqlite3.connect(str(self.TRADES_DB))
            cur = conn.cursor()

            cur.execute("""
                SELECT symbol, COUNT(*) as n, SUM(pnl) as total_pnl,
                       AVG(pnl) as avg_pnl,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                FROM positions 
                WHERE status='closed' AND strategy_version='ema_v5'
                GROUP BY symbol
                ORDER BY total_pnl DESC
                LIMIT 20
            """)
            rows = cur.fetchall()
            conn.close()

            symbols = {}
            for row in rows:
                sym, n, total_pnl, avg_pnl, wins = row
                wr = wins / n if n > 0 else 0
                symbols[sym] = {
                    "trades": n,
                    "total_pnl": round(total_pnl, 2),
                    "avg_pnl": round(avg_pnl, 2),
                    "win_rate": round(wr, 4),
                }

            return symbols

        except Exception as e:
            logger.debug("Symbol performance error: {}", e)
            return {}

    def _get_regime_performance(self) -> Dict:
        """Get performance by regime."""
        try:
            conn = sqlite3.connect(str(self.TRADES_DB))
            cur = conn.cursor()

            cur.execute("""
                SELECT regime, COUNT(*) as n, SUM(pnl) as total_pnl,
                       AVG(pnl) as avg_pnl,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                FROM positions 
                WHERE status='closed' AND strategy_version='ema_v5'
                GROUP BY regime
            """)
            rows = cur.fetchall()
            conn.close()

            regimes = {}
            for row in rows:
                regime, n, total_pnl, avg_pnl, wins = row
                wr = wins / n if n > 0 else 0
                regimes[regime] = {
                    "trades": n,
                    "total_pnl": round(total_pnl, 2),
                    "avg_pnl": round(avg_pnl, 2),
                    "win_rate": round(wr, 4),
                }

            return regimes

        except Exception as e:
            logger.debug("Regime performance error: {}", e)
            return {}

    def _get_directional_analysis(self) -> Dict:
        """Get performance by direction (LONG/SHORT)."""
        try:
            conn = sqlite3.connect(str(self.TRADES_DB))
            cur = conn.cursor()

            cur.execute("""
                SELECT side, COUNT(*) as n, SUM(pnl) as total_pnl,
                       AVG(pnl) as avg_pnl,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                FROM positions 
                WHERE status='closed' AND strategy_version='ema_v5'
                GROUP BY side
            """)
            rows = cur.fetchall()
            conn.close()

            directional = {}
            for row in rows:
                side, n, total_pnl, avg_pnl, wins = row
                wr = wins / n if n > 0 else 0
                directional[side] = {
                    "trades": n,
                    "total_pnl": round(total_pnl, 2),
                    "avg_pnl": round(avg_pnl, 2),
                    "win_rate": round(wr, 4),
                }

            return directional

        except Exception as e:
            logger.debug("Directional analysis error: {}", e)
            return {}

    def _get_feature_importance(self) -> Dict:
        """Get feature importance from calibration database."""
        try:
            conn = sqlite3.connect(str(self.DB_PATH))
            cur = conn.cursor()

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_weights'")
            if not cur.fetchone():
                conn.close()
                return {"note": "No trained weights available"}

            cur.execute("SELECT weights_json FROM model_weights ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            conn.close()

            if row:
                weights = json.loads(row[0])
                # Sort by absolute importance
                sorted_weights = sorted(weights.items(), key=lambda x: abs(x[1]), reverse=True)
                return {name: round(w, 4) for name, w in sorted_weights}

            return {}

        except Exception as e:
            logger.debug("Feature importance error: {}", e)
            return {}

    def _pearson_correlation(self, x: np.ndarray, y: np.ndarray) -> float:
        """Compute Pearson correlation coefficient."""
        if len(x) < 10:
            return 0
        mx, my = np.mean(x), np.mean(y)
        sx, sy = np.std(x), np.std(y)
        if sx == 0 or sy == 0:
            return 0
        return float(np.mean((x - mx) * (y - my)) / (sx * sy))
