"""
Trade Analytics — MFE, MAE, Exit Efficiency, and Capital Efficiency.

Per Executive Assessment Problem 12:
    Missing metrics:
        - Maximum Favorable Excursion (MFE): best price reached during trade
        - Maximum Adverse Excursion (MAE): worst price reached during trade
        - Exit Efficiency: how much of the MFE was captured
        - Trade Efficiency Score: current PnL vs maximum unrealized PnL

Per Executive Assessment Live Sheet Improvements:
    - Trade Efficiency Score = Current PnL vs Maximum Unrealized PnL
    - Exit Quality = Good / Early / Late based on momentum decay, CVD, Flow, OI
    - Capital Efficiency: Rank all open trades, reduce weaker positions

READ-ONLY: Returns analytics for display and decision-making.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class TradeExcursion:
    """Tracks MFE and MAE for a single position."""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    entry_time: float = 0.0
    risk_per_unit: float = 0.0

    # Price extremes
    highest_price: float = 0.0   # Best price during trade
    lowest_price: float = 0.0    # Worst price during trade

    # R-multiple extremes
    max_favorable_r: float = 0.0   # MFE in R
    max_adverse_r: float = 0.0     # MAE in R (negative or 0)

    # Percentage extremes
    max_favorable_pct: float = 0.0  # MFE as %
    max_adverse_pct: float = 0.0    # MAE as % (negative or 0)

    # Current state
    current_price: float = 0.0
    current_pnl_r: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": round(self.entry_price, 6),
            "highest_price": round(self.highest_price, 6),
            "lowest_price": round(self.lowest_price, 6),
            "max_favorable_r": round(self.max_favorable_r, 3),
            "max_adverse_r": round(self.max_adverse_r, 3),
            "max_favorable_pct": round(self.max_favorable_pct, 4),
            "max_adverse_pct": round(self.max_adverse_pct, 4),
            "current_pnl_r": round(self.current_pnl_r, 3),
            "efficiency_pct": round(self.get_efficiency(), 1),
        }

    def get_efficiency(self) -> float:
        """Calculate trade efficiency: current PnL / MFE × 100."""
        if self.max_favorable_r <= 0:
            return 0.0
        return (self.current_pnl_r / self.max_favorable_r) * 100


@dataclass
class ExitQualityAssessment:
    """Assessment of exit quality after a trade closes."""
    symbol: str = ""
    quality: str = ""        # GOOD / EARLY / LATE / POOR
    score: float = 0.0       # 0-100
    efficiency_pct: float = 0.0  # % of MFE captured
    exit_reason: str = ""
    details: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "quality": self.quality,
            "score": round(self.score, 1),
            "efficiency_pct": round(self.efficiency_pct, 1),
            "exit_reason": self.exit_reason,
            "details": self.details,
        }


@dataclass
class CapitalEfficiencyRank:
    """Rank of a position by capital efficiency."""
    symbol: str = ""
    side: str = ""
    efficiency_score: float = 0.0   # 0-100, higher = better use of capital
    unrealized_r: float = 0.0
    hold_time_hours: float = 0.0
    mfe_r: float = 0.0
    should_reduce: bool = False
    reduce_pct: float = 0.0         # How much to reduce (0-100%)

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "efficiency_score": round(self.efficiency_score, 1),
            "unrealized_r": round(self.unrealized_r, 3),
            "hold_time_hours": round(self.hold_time_hours, 1),
            "mfe_r": round(self.mfe_r, 3),
            "should_reduce": self.should_reduce,
            "reduce_pct": round(self.reduce_pct, 1),
        }


class TradeAnalyticsEngine:
    """
    Tracks MFE, MAE, exit efficiency, and capital efficiency for all positions.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._excursions: Dict[str, TradeExcursion] = {}

    # ── MFE / MAE Tracking ───────────────────────────────────

    def register_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        entry_time: Optional[float] = None,
        risk_per_unit: float = 0.0,
    ) -> None:
        """Register a new position for excursion tracking."""
        if risk_per_unit <= 0:
            risk_per_unit = max(entry_price * 0.01, entry_price * 0.002)

        self._excursions[symbol] = TradeExcursion(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_time=entry_time or time.time(),
            risk_per_unit=risk_per_unit,
            highest_price=entry_price,
            lowest_price=entry_price,
            current_price=entry_price,
        )

    def update_price(self, symbol: str, current_price: float) -> Optional[TradeExcursion]:
        """
        Update current price and recalculate MFE/MAE.

        Call this on every tick or price update for open positions.

        Returns:
            Updated TradeExcursion or None if position not tracked
        """
        exc = self._excursions.get(symbol)
        if not exc:
            return None

        exc.current_price = current_price
        risk = exc.risk_per_unit

        # Update price extremes
        if current_price > exc.highest_price:
            exc.highest_price = current_price
        if current_price < exc.lowest_price:
            exc.lowest_price = current_price

        # Recalculate R-multiples
        if exc.side == "LONG":
            exc.max_favorable_r = (exc.highest_price - exc.entry_price) / risk
            exc.max_adverse_r = (exc.lowest_price - exc.entry_price) / risk
            exc.current_pnl_r = (current_price - exc.entry_price) / risk
        else:
            exc.max_favorable_r = (exc.entry_price - exc.lowest_price) / risk
            exc.max_adverse_r = (exc.entry_price - exc.highest_price) / risk
            exc.current_pnl_r = (exc.entry_price - current_price) / risk

        # Percentage extremes
        if exc.entry_price > 0:
            exc.max_favorable_pct = (exc.highest_price - exc.entry_price) / exc.entry_price * 100
            exc.max_adverse_pct = (exc.lowest_price - exc.entry_price) / exc.entry_price * 100
            if exc.side == "SHORT":
                exc.max_favorable_pct = -exc.max_favorable_pct
                exc.max_adverse_pct = -exc.max_adverse_pct

        return exc

    def get_excursion(self, symbol: str) -> Optional[TradeExcursion]:
        """Get current excursion data for a symbol."""
        return self._excursions.get(symbol)

    def get_all_excursions(self) -> Dict[str, TradeExcursion]:
        """Get all active excursion data."""
        return dict(self._excursions)

    # ── Exit Quality Assessment ───────────────────────────────

    def assess_exit_quality(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
        side: str = "",
        entry_price: float = 0.0,
        risk_per_unit: float = 0.0,
        momentum_at_exit: float = 0.0,
        cvd_at_exit: float = 0.0,
        oi_change_at_exit: float = 0.0,
    ) -> ExitQualityAssessment:
        """
        Assess the quality of an exit after a trade closes.

        Uses MFE, MAE, and efficiency to determine if exit was good/early/late.

        Args:
            symbol: Trade symbol
            exit_price: Price at exit
            exit_reason: Why the trade was closed
            side: LONG or SHORT
            entry_price: Entry price
            risk_per_unit: Risk per unit
            momentum_at_exit: Momentum indicator at exit time
            cvd_at_exit: CVD at exit time
            oi_change_at_exit: OI change at exit time

        Returns:
            ExitQualityAssessment with quality rating
        """
        exc = self._excursions.get(symbol)
        if not exc:
            # Reconstruct from provided data
            if risk_per_unit <= 0:
                risk_per_unit = max(entry_price * 0.01, entry_price * 0.002)
            exc = TradeExcursion(
                symbol=symbol, side=side, entry_price=entry_price,
                risk_per_unit=risk_per_unit, highest_price=exit_price,
                lowest_price=exit_price, current_price=exit_price,
            )

        # Calculate exit R-multiple
        if side and entry_price > 0:
            if side == "LONG":
                exit_r = (exit_price - entry_price) / exc.risk_per_unit
            else:
                exit_r = (entry_price - exit_price) / exc.risk_per_unit
        else:
            exit_r = exc.current_pnl_r

        efficiency = exc.get_efficiency()

        # ── Quality Classification ──
        quality = "GOOD"
        score = 70.0
        details = ""

        if exit_reason == "stop_loss":
            quality = "POOR"
            score = 20.0
            details = f"Stopped out at MAE={exc.max_adverse_r:.2f}R"

        elif exit_reason in ("time_stop", "no_progress_6h", "max_hold_24h"):
            if exit_r > 0:
                quality = "EARLY"
                score = 40.0
                details = f"Time exit with profit {exit_r:.2f}R but MFE was {exc.max_favorable_r:.2f}R"
            else:
                quality = "GOOD"
                score = 60.0
                details = f"Time exit — capital freed (hold={exc.max_adverse_r:.2f}R MAE)"

        elif exit_reason in ("trailing_stop", "trailing_stop_r"):
            if efficiency > 60:
                quality = "GOOD"
                score = 80.0
                details = f"Good trail exit — captured {efficiency:.0f}% of MFE"
            elif efficiency > 30:
                quality = "EARLY"
                score = 55.0
                details = f"Trail exited early — captured only {efficiency:.0f}% of MFE"
            else:
                quality = "POOR"
                score = 30.0
                details = f"Poor trail — {efficiency:.0f}% efficiency (MFE={exc.max_favorable_r:.2f}R)"

        elif exit_reason in ("take_profit_1", "take_profit_2", "take_profit_3"):
            if efficiency > 70:
                quality = "GOOD"
                score = 85.0
                details = f"TP hit — {efficiency:.0f}% efficiency"
            else:
                quality = "LATE"
                score = 50.0
                details = f"TP hit but MFE was higher ({exc.max_favorable_r:.2f}R vs exit {exit_r:.2f}R)"

        elif exit_reason in ("protected_sl_hit",):
            if exit_r > 0:
                quality = "GOOD"
                score = 75.0
                details = f"Protected exit — locked {exit_r:.2f}R profit"
            else:
                quality = "EARLY"
                score = 45.0
                details = f"Protected exit — {exit_r:.2f}R (MFE was {exc.max_favorable_r:.2f}R)"

        elif exit_reason in ("momentum_reversal",):
            if exit_r > 0:
                quality = "GOOD"
                score = 70.0
                details = f"Momentum exit — captured {exit_r:.2f}R"
            else:
                quality = "LATE"
                score = 35.0
                details = f"Momentum exit at loss — should have exited earlier"

        elif exit_reason in ("breakeven",):
            quality = "EARLY"
            score = 40.0
            details = f"Breakeven exit — MFE was {exc.max_favorable_r:.2f}R"

        # ── Momentum-based adjustment ──
        # If momentum was still strong at exit, we exited too early
        if momentum_at_exit > 0.5 and exit_r > 0 and quality != "POOR":
            score -= 10
            details += " | momentum still strong at exit"

        # If CVD was reversing, exit was justified
        if cvd_at_exit != 0:
            if (side == "LONG" and cvd_at_exit < -0.3) or (side == "SHORT" and cvd_at_exit > 0.3):
                score += 5
                details += " | CVD confirmed exit"

        score = max(0, min(100, score))

        return ExitQualityAssessment(
            symbol=symbol,
            quality=quality,
            score=score,
            efficiency_pct=efficiency,
            exit_reason=exit_reason,
            details=details,
        )

    # ── Capital Efficiency Ranking ────────────────────────────

    def rank_capital_efficiency(
        self,
        positions: List[Dict],
        current_prices: Dict[str, float],
    ) -> List[CapitalEfficiencyRank]:
        """
        Rank all open positions by capital efficiency.

        Positions that are stagnant (low R, long hold time) are candidates
        for reduction when better opportunities appear.

        Args:
            positions: List of open position dicts
            current_prices: Dict of symbol → current price

        Returns:
            List of CapitalEfficiencyRank sorted by efficiency (best first)
        """
        ranks = []

        for pos in positions:
            sym = pos.get("symbol", "")
            side = pos.get("side", "LONG")
            entry = pos.get("entry_price", 0)
            opened_at = pos.get("opened_at", 0)
            sl = pos.get("stop_loss", 0)

            risk = max(abs(entry - sl) if sl else entry * 0.01, entry * 0.002)
            price = current_prices.get(sym, entry)

            if side == "LONG":
                unrealized_r = (price - entry) / risk
            else:
                unrealized_r = (entry - price) / risk

            exc = self._excursions.get(sym)
            mfe_r = exc.max_favorable_r if exc else max(0, unrealized_r)
            hold_hours = (time.time() - opened_at) / 3600 if opened_at > 0 else 0

            # ── Efficiency Score ──
            # High R + low hold time = high efficiency
            # Low R + high hold time = low efficiency
            r_score = min(50, max(0, unrealized_r * 10))  # 0-50 points for R
            time_score = max(0, 50 - hold_hours * 2)       # 0-50 points (deduct 2 per hour)
            mfe_bonus = min(20, mfe_r * 5)                 # 0-20 bonus for MFE

            efficiency = r_score + time_score + mfe_bonus
            efficiency = max(0, min(100, efficiency))

            # ── Should Reduce? ──
            # Reduce if: low efficiency AND long hold time AND minimal profit
            should_reduce = (
                efficiency < 30
                and hold_hours > 4
                and unrealized_r < 0.5
                and mfe_r < 1.0
            )
            reduce_pct = 50.0 if should_reduce else 0.0

            ranks.append(CapitalEfficiencyRank(
                symbol=sym,
                side=side,
                efficiency_score=efficiency,
                unrealized_r=unrealized_r,
                hold_time_hours=hold_hours,
                mfe_r=mfe_r,
                should_reduce=should_reduce,
                reduce_pct=reduce_pct,
            ))

        # Sort by efficiency (best first)
        ranks.sort(key=lambda r: r.efficiency_score, reverse=True)
        return ranks

    # ── Cleanup ───────────────────────────────────────────────

    def cleanup(self, symbol: str) -> Optional[TradeExcursion]:
        """Remove tracking for a closed position. Returns final excursion data."""
        return self._excursions.pop(symbol, None)

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics for all tracked positions."""
        if not self._excursions:
            return {"active": 0}

        efficiencies = [e.get_efficiency() for e in self._excursions.values()]
        mfe_vals = [e.max_favorable_r for e in self._excursions.values()]
        mae_vals = [e.max_adverse_r for e in self._excursions.values()]

        return {
            "active": len(self._excursions),
            "avg_efficiency_pct": round(sum(efficiencies) / len(efficiencies), 1),
            "avg_mfe_r": round(sum(mfe_vals) / len(mfe_vals), 3),
            "avg_mae_r": round(sum(mae_vals) / len(mae_vals), 3),
            "positions": {s: e.to_dict() for s, e in self._excursions.items()},
        }
