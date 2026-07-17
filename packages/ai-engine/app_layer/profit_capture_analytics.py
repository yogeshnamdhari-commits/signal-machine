"""
Profit Capture Analytics — Measure how much unrealized profit is retained.

Per Executive Assessment v4:
    "This should become one of the largest cards on the dashboard.
     Maximum Unrealized Profit $100, Actual Exit $42, Profit Capture 42%.
     If this stays below roughly 60–70% on trend trades, the exit engine
     still has room to improve."

Key Metrics:
    1. Profit Capture Ratio — realized R / MFE R (per trade and aggregate)
    2. Exit Efficiency Score — composite of capture, timing, and slippage
    3. MFE Distribution — histogram of where trades peak
    4. Exit Distribution — histogram of where trades exit
    5. Capture by Exit Reason — which exits capture most profit
    6. Capture by Symbol — which symbols have best/worst capture
    7. Capture by Regime — which regimes have best/worst capture
    8. Time Decay Analysis — how profit capture degrades with hold time

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class TradeCapture:
    """Profit capture data for a single trade."""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    realized_r: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    profit_capture_pct: float = 0.0  # realized_r / mfe_r * 100
    exit_reason: str = ""
    hold_minutes: float = 0.0
    session: str = ""
    regime: str = ""
    confidence: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "realized_r": round(self.realized_r, 3),
            "mfe_r": round(self.mfe_r, 3),
            "mae_r": round(self.mae_r, 3),
            "profit_capture_pct": round(self.profit_capture_pct, 1),
            "exit_reason": self.exit_reason,
            "hold_minutes": round(self.hold_minutes, 1),
            "session": self.session,
            "regime": self.regime,
        }


@dataclass
class CaptureByDimension:
    """Aggregated capture metrics for a dimension (symbol, session, etc.)."""
    dimension: str = ""
    name: str = ""
    trade_count: int = 0
    avg_capture_pct: float = 0.0
    median_capture_pct: float = 0.0
    avg_mfe_r: float = 0.0
    avg_realized_r: float = 0.0
    avg_mae_r: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    rating: str = ""  # EXCELLENT / GOOD / AVERAGE / POOR

    def to_dict(self) -> Dict:
        return {
            "dimension": self.dimension,
            "name": self.name,
            "trade_count": self.trade_count,
            "avg_capture_pct": round(self.avg_capture_pct, 1),
            "median_capture_pct": round(self.median_capture_pct, 1),
            "avg_mfe_r": round(self.avg_mfe_r, 3),
            "avg_realized_r": round(self.avg_realized_r, 3),
            "avg_mae_r": round(self.avg_mae_r, 3),
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "rating": self.rating,
        }


@dataclass
class ProfitCaptureDashboard:
    """Complete profit capture analytics."""
    timestamp: float = 0.0

    # Overall metrics
    total_trades: int = 0
    avg_capture_pct: float = 0.0
    median_capture_pct: float = 0.0
    avg_mfe_r: float = 0.0
    avg_realized_r: float = 0.0
    avg_mae_r: float = 0.0
    overall_profit_factor: float = 0.0

    # Capture distribution
    capture_excellent: int = 0   # > 70%
    capture_good: int = 0        # 50-70%
    capture_fair: int = 0        # 30-50%
    capture_poor: int = 0        # < 30%

    # By exit reason
    by_exit_reason: List[CaptureByDimension] = field(default_factory=list)

    # By symbol (top/bottom)
    by_symbol: List[CaptureByDimension] = field(default_factory=list)
    top_symbols: List[CaptureByDimension] = field(default_factory=list)
    worst_symbols: List[CaptureByDimension] = field(default_factory=list)

    # By session
    by_session: List[CaptureByDimension] = field(default_factory=list)

    # By regime
    by_regime: List[CaptureByDimension] = field(default_factory=list)

    # Time decay
    time_decay: List[Dict] = field(default_factory=list)

    # Exit efficiency
    exit_efficiency_score: float = 0.0  # 0-100

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "overall": {
                "total_trades": self.total_trades,
                "avg_capture_pct": round(self.avg_capture_pct, 1),
                "median_capture_pct": round(self.median_capture_pct, 1),
                "avg_mfe_r": round(self.avg_mfe_r, 3),
                "avg_realized_r": round(self.avg_realized_r, 3),
                "avg_mae_r": round(self.avg_mae_r, 3),
                "profit_factor": round(self.overall_profit_factor, 2),
                "exit_efficiency_score": round(self.exit_efficiency_score, 1),
            },
            "capture_distribution": {
                "excellent_70plus": self.capture_excellent,
                "good_50_70": self.capture_good,
                "fair_30_50": self.capture_fair,
                "poor_below_30": self.capture_poor,
            },
            "by_exit_reason": [d.to_dict() for d in self.by_exit_reason],
            "top_symbols": [d.to_dict() for d in self.top_symbols],
            "worst_symbols": [d.to_dict() for d in self.worst_symbols],
            "by_session": [d.to_dict() for d in self.by_session],
            "by_regime": [d.to_dict() for d in self.by_regime],
            "time_decay": self.time_decay,
        }


class ProfitCaptureAnalytics:
    """
    Measures how much unrealized profit is retained at exit.

    Per Executive Assessment v4:
        "If Profit Capture stays below 60-70% on trend trades,
         the exit engine still has room to improve."

    This engine:
        1. Calculates profit capture for every trade
        2. Aggregates by symbol, session, regime, exit reason
        3. Identifies which exits capture most/least profit
        4. Tracks time decay of profit capture
        5. Provides actionable insights for exit optimization

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[TradeCapture] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades and calculate profit capture."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, entry_price, realized_r, highest_pnl,
                       mfe_pct, mae_pct, exit_reason, hold_minutes,
                       session, regime, confidence, pnl
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = []
            for row in rows:
                r = dict(row)
                realized_r = r.get("realized_r", 0) or 0
                mfe_r = r.get("highest_pnl", 0) or 0
                mae_r = abs(r.get("mae_pct", 0) or 0)

                # Calculate profit capture
                if mfe_r > 0:
                    capture = (realized_r / mfe_r) * 100
                else:
                    capture = 0

                self._trades.append(TradeCapture(
                    symbol=r.get("symbol", ""),
                    side=r.get("side", ""),
                    entry_price=r.get("entry_price", 0),
                    realized_r=realized_r,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    profit_capture_pct=capture,
                    exit_reason=r.get("exit_reason", ""),
                    hold_minutes=r.get("hold_minutes", 0) or 0,
                    session=r.get("session", "unknown"),
                    regime=r.get("regime", "unknown"),
                    confidence=r.get("confidence", 0) or 0,
                ))

            self._last_load = time.time()
            logger.info("📊 Profit Capture loaded: {} trades", len(self._trades))

        except Exception as e:
            logger.warning("Could not load profit capture analytics: {}", e)

    def get_dashboard(self) -> ProfitCaptureDashboard:
        """Get complete profit capture dashboard."""
        self._ensure_loaded()

        dash = ProfitCaptureDashboard(timestamp=time.time())

        if not self._trades:
            return dash

        dash.total_trades = len(self._trades)

        # ── Overall metrics ──
        captures = [t.profit_capture_pct for t in self._trades if t.mfe_r > 0]
        realized = [t.realized_r for t in self._trades]
        mfe_vals = [t.mfe_r for t in self._trades if t.mfe_r > 0]
        mae_vals = [t.mae_r for t in self._trades if t.mae_r > 0]

        dash.avg_capture_pct = sum(captures) / max(1, len(captures))
        dash.median_capture_pct = sorted(captures)[len(captures)//2] if captures else 0
        dash.avg_mfe_r = sum(mfe_vals) / max(1, len(mfe_vals))
        dash.avg_realized_r = sum(realized) / max(1, len(realized))
        dash.avg_mae_r = sum(mae_vals) / max(1, len(mae_vals))

        # Profit factor
        wins = [t.realized_r for t in self._trades if t.realized_r > 0]
        losses = [abs(t.realized_r) for t in self._trades if t.realized_r < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        dash.overall_profit_factor = gross_profit / max(0.01, gross_loss)

        # ── Capture distribution ──
        for c in captures:
            if c > 70:
                dash.capture_excellent += 1
            elif c > 50:
                dash.capture_good += 1
            elif c > 30:
                dash.capture_fair += 1
            else:
                dash.capture_poor += 1

        # ── Exit efficiency score ──
        dash.exit_efficiency_score = self._calc_exit_efficiency_score()

        # ── By exit reason ──
        dash.by_exit_reason = self._aggregate_by_dimension("exit_reason")

        # ── By symbol ──
        symbol_dims = self._aggregate_by_dimension("symbol")
        dash.by_symbol = symbol_dims
        dash.top_symbols = sorted(symbol_dims, key=lambda d: d.avg_capture_pct, reverse=True)[:10]
        dash.worst_symbols = sorted(symbol_dims, key=lambda d: d.avg_capture_pct)[:10]

        # ── By session ──
        dash.by_session = self._aggregate_by_dimension("session")

        # ── By regime ──
        dash.by_regime = self._aggregate_by_dimension("regime")

        # ── Time decay ──
        dash.time_decay = self._calc_time_decay()

        return dash

    def _aggregate_by_dimension(self, dimension: str) -> List[CaptureByDimension]:
        """Aggregate capture metrics by a dimension."""
        groups: Dict[str, List[TradeCapture]] = defaultdict(list)
        for t in self._trades:
            key = getattr(t, dimension, "unknown")
            groups[key].append(t)

        results = []
        for name, trades in groups.items():
            if not trades:
                continue

            captures = [t.profit_capture_pct for t in trades if t.mfe_r > 0]
            realized = [t.realized_r for t in trades]
            mfe_vals = [t.mfe_r for t in trades if t.mfe_r > 0]
            mae_vals = [t.mae_r for t in trades if t.mae_r > 0]

            wins = [t.realized_r for t in trades if t.realized_r > 0]
            losses = [abs(t.realized_r) for t in trades if t.realized_r < 0]

            avg_capture = sum(captures) / max(1, len(captures))
            sorted_captures = sorted(captures)
            median_capture = sorted_captures[len(sorted_captures)//2] if sorted_captures else 0

            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(losses) if losses else 0.01
            pf = gross_profit / max(0.01, gross_loss)

            # Rating
            if avg_capture >= 60:
                rating = "EXCELLENT"
            elif avg_capture >= 45:
                rating = "GOOD"
            elif avg_capture >= 30:
                rating = "AVERAGE"
            else:
                rating = "POOR"

            results.append(CaptureByDimension(
                dimension=dimension,
                name=str(name),
                trade_count=len(trades),
                avg_capture_pct=avg_capture,
                median_capture_pct=median_capture,
                avg_mfe_r=sum(mfe_vals) / max(1, len(mfe_vals)),
                avg_realized_r=sum(realized) / max(1, len(realized)),
                avg_mae_r=sum(mae_vals) / max(1, len(mae_vals)),
                win_rate=len(wins) / max(1, len(trades)),
                profit_factor=pf,
                rating=rating,
            ))

        return sorted(results, key=lambda d: d.avg_capture_pct, reverse=True)

    def _calc_exit_efficiency_score(self) -> float:
        """Calculate overall exit efficiency score (0-100)."""
        if not self._trades:
            return 0

        scores = []
        for t in self._trades:
            if t.mfe_r <= 0:
                continue

            # Capture component (0-40)
            capture_score = min(40, t.profit_capture_pct * 0.4)

            # MFE/MAE ratio component (0-30)
            ratio = t.mfe_r / max(0.01, t.mae_r) if t.mae_r > 0 else 1
            ratio_score = min(30, ratio * 10)

            # Winner bonus (0-20)
            winner_bonus = 20 if t.realized_r > 0 else 0

            # Time efficiency (0-10) — faster exit with good capture = better
            if t.hold_minutes > 0 and t.profit_capture_pct > 50:
                time_score = min(10, 10 - (t.hold_minutes / 60))
            else:
                time_score = 5

            scores.append(min(100, capture_score + ratio_score + winner_bonus + time_score))

        return sum(scores) / max(1, len(scores))

    def _calc_time_decay(self) -> List[Dict]:
        """Calculate how profit capture degrades with hold time."""
        time_buckets = [
            ("0-15 min", 0, 15),
            ("15-30 min", 15, 30),
            ("30-60 min", 30, 60),
            ("1-2 hours", 60, 120),
            ("2-4 hours", 120, 240),
            ("4-8 hours", 240, 480),
            ("8+ hours", 480, 99999),
        ]

        results = []
        for label, min_mins, max_mins in time_buckets:
            trades_in_bucket = [
                t for t in self._trades
                if min_mins <= t.hold_minutes < max_mins
            ]
            if not trades_in_bucket:
                continue

            captures = [t.profit_capture_pct for t in trades_in_bucket if t.mfe_r > 0]
            avg_capture = sum(captures) / max(1, len(captures))

            results.append({
                "time_bucket": label,
                "trade_count": len(trades_in_bucket),
                "avg_capture_pct": round(avg_capture, 1),
            })

        return results

    def get_symbol_capture(self, symbol: str) -> Optional[CaptureByDimension]:
        """Get capture metrics for a specific symbol."""
        self._ensure_loaded()
        sym_trades = [t for t in self._trades if t.symbol == symbol]
        if not sym_trades:
            return None

        captures = [t.profit_capture_pct for t in sym_trades if t.mfe_r > 0]
        avg_capture = sum(captures) / max(1, len(captures))

        return CaptureByDimension(
            dimension="symbol",
            name=symbol,
            trade_count=len(sym_trades),
            avg_capture_pct=avg_capture,
            avg_mfe_r=sum(t.mfe_r for t in sym_trades if t.mfe_r > 0) / max(1, len([t for t in sym_trades if t.mfe_r > 0])),
            avg_realized_r=sum(t.realized_r for t in sym_trades) / max(1, len(sym_trades)),
        )

    def get_exit_reason_capture(self) -> Dict[str, float]:
        """Get average capture percentage by exit reason."""
        self._ensure_loaded()
        by_reason: Dict[str, List[float]] = defaultdict(list)
        for t in self._trades:
            if t.mfe_r > 0 and t.exit_reason:
                by_reason[t.exit_reason].append(t.profit_capture_pct)

        return {
            reason: round(sum(captures) / len(captures), 1)
            for reason, captures in by_reason.items()
            if captures
        }
