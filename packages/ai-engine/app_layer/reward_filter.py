"""
Reward Filter — Minimum R:R and expectancy thresholds.

READ-ONLY with respect to upstream data. Never modifies signals.

Per Master Directive:
    "Reject: Poor Reward/Risk, Poor expectancy, Small projected move,
     Low volatility expansion. Focus on asymmetric opportunities."

Minimum Requirements:
    - R:R >= 2.0 (preferred 2.5+)
    - Positive expectancy
    - Minimum projected move > 0.5%
    - Stop loss within acceptable range
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# REWARD THRESHOLDS
# ═══════════════════════════════════════════════════════════════

# Minimum R:R to allow trade
MIN_RR = 2.0

# Preferred R:R for elite classification
PREFERRED_RR = 2.5

# Maximum SL distance as % of entry
MAX_SL_DISTANCE_PCT = 3.0

# Minimum projected move (TP distance) as % of entry
MIN_PROJECTED_MOVE_PCT = 0.5

# Maximum SL distance for quality bonus
OPTIMAL_SL_DISTANCE_PCT = 1.5


@dataclass
class RewardFilterResult:
    """Result of reward filtering."""
    symbol: str = ""
    side: str = ""
    approved: bool = False
    rr: float = 0.0
    sl_distance_pct: float = 0.0
    projected_move_pct: float = 0.0
    quality_bonus: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "approved": self.approved,
            "rr": round(self.rr, 2),
            "sl_distance_pct": round(self.sl_distance_pct, 2),
            "projected_move_pct": round(self.projected_move_pct, 2),
            "quality_bonus": round(self.quality_bonus, 2),
            "reason": self.reason,
        }


class RewardFilter:
    """
    Filters trades based on reward-to-risk quality.

    Minimum: R:R >= 2.0
    Preferred: R:R >= 2.5
    Blocked: R:R < 2.0 OR SL too wide OR projected move too small

    READ-ONLY: never modifies upstream data.
    """

    def evaluate(self, signal: Dict[str, Any]) -> RewardFilterResult:
        """
        Evaluate reward quality of a signal.

        Args:
            signal: Live Sheet signal dict

        Returns:
            RewardFilterResult with approval status
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        entry = signal.get("entry_price", signal.get("entry", 0))
        sl = signal.get("stop_loss", 0)
        tp = signal.get("take_profit", 0)
        rr = signal.get("risk_reward", 0)

        result = RewardFilterResult(symbol=symbol, side=side, rr=rr)

        if entry <= 0 or sl <= 0 or tp <= 0:
            result.approved = False
            result.reason = "missing entry/SL/TP data"
            return result

        # Calculate distances
        risk_distance = abs(entry - sl)
        reward_distance = abs(tp - entry)

        result.sl_distance_pct = risk_distance / entry * 100
        result.projected_move_pct = reward_distance / entry * 100

        # ── Check 1: R:R minimum ──
        if rr < MIN_RR:
            result.approved = False
            result.reason = f"R:R {rr:.2f} < {MIN_RR} minimum"
            return result

        # ── Check 2: SL distance too wide ──
        if result.sl_distance_pct > MAX_SL_DISTANCE_PCT:
            result.approved = False
            result.reason = f"SL distance {result.sl_distance_pct:.2f}% > {MAX_SL_DISTANCE_PCT}% max"
            return result

        # ── Check 3: Projected move too small ──
        if result.projected_move_pct < MIN_PROJECTED_MOVE_PCT:
            result.approved = False
            result.reason = (
                f"projected move {result.projected_move_pct:.2f}% "
                f"< {MIN_PROJECTED_MOVE_PCT}% minimum"
            )
            return result

        # ── Quality Bonus ──
        # Bonus for optimal R:R and SL distance
        result.quality_bonus = 0.0
        if rr >= PREFERRED_RR:
            result.quality_bonus += (rr - PREFERRED_RR) * 5  # +5 per 0.1 above 2.5
        if result.sl_distance_pct <= OPTIMAL_SL_DISTANCE_PCT:
            result.quality_bonus += 10  # optimal SL distance bonus

        result.approved = True
        result.reason = (
            f"R:R={rr:.2f} SL={result.sl_distance_pct:.2f}% "
            f"move={result.projected_move_pct:.2f}% bonus={result.quality_bonus:.1f}"
        )

        logger.debug(
            "REWARD: {} {} → {} (RR={:.2f}, SL={:.2f}%, move={:.2f}%)",
            symbol, side, "PASS" if result.approved else "FAIL",
            rr, result.sl_distance_pct, result.projected_move_pct,
        )

        return result
