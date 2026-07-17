"""
App Position Sizing Engine — Dynamic sizing by Expected Profit Score + quality.

READ-ONLY with respect to upstream data. Never modifies signals or positions.

Per Executive Assessment Problem 3 (Equal Position Size):
    Problem: Quantity appears almost identical except price normalization.
    Fix: High confidence trades receive more capital.

Per Executive Assessment Problem 10 (Capital Allocation):
    Expected Profit Score → Capital multiplier:
        Score 95+  → 1.50x
        Score 90   → 1.25x
        Score 85   → 1.00x
        Score 80   → 0.50x
        Score <80  → Reject

Per Master Directive:
    "Use dynamic sizing. Never equal sizing. Capital allocation must depend on:
     Trade Quality, Risk, Expected Return, Portfolio Exposure, Daily Drawdown,
     Correlation, Maximum simultaneous exposure."

Sizing by Expected Profit Score (primary) + Quality Score (secondary):
    95-100:  1.5x base size (Elite — maximum allocation)
    90-95:   1.25x base size (Strong — above normal)
    85-90:   1.0x base size (Normal — standard)
    80-85:   0.5x base size (Conservative — reduced)
    <80:     Skip (below minimum threshold)

Risk Adjustments:
    - Drawdown reduction: reduce size during drawdowns
    - Correlation reduction: reduce size when correlated positions open
    - Daily loss limit: reduce size after losses
    - Max exposure cap: never exceed portfolio limits
    - Expected Profit multiplier: rank by expected return, not just quality
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# SIZING CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Base risk per trade as % of balance
BASE_RISK_PCT = 1.0

# Expected Profit Score-based multipliers (Problem 3 + 10 fix)
# Primary sizing input — replaces pure quality-based sizing
EXPECTED_PROFIT_MULTIPLIERS = {
    (95, 100): 1.50,    # Elite — maximum allocation
    (90, 95):  1.25,    # Strong — above normal
    (85, 90):  1.00,    # Normal — standard size
    (80, 85):  0.50,    # Conservative — reduced size
    (0, 80):   0.0,     # Below threshold — skip
}

# Quality-based multipliers (secondary input, blended with expected profit)
QUALITY_MULTIPLIERS = {
    (98, 100): 2.0,    # Elite+ — maximum allocation
    (95, 98): 1.5,     # Strong — above normal
    (90, 95): 1.0,     # Normal — standard size
    (85, 90): 0.5,     # Conservative — reduced size
    (0, 85): 0.0,      # Below threshold — skip
}

# Blend weights: how much each factor contributes to final multiplier
EXPECTED_PROFIT_WEIGHT = 0.6   # 60% weight to expected profit
QUALITY_WEIGHT = 0.4           # 40% weight to quality

# Maximum simultaneous open positions
MAX_OPEN_POSITIONS = 5

# Maximum portfolio heat (sum of all position risks as % of balance)
MAX_PORTFOLIO_HEAT_PCT = 5.0

# Maximum single position as % of balance
MAX_SINGLE_POSITION_PCT = 3.0

# Drawdown adjustment thresholds
DD_REDUCTION_START = 0.03   # Start reducing at 3% drawdown
DD_REDUCTION_FULL = 0.10    # Full reduction at 10% drawdown

# Daily loss limit as % of balance
DAILY_LOSS_LIMIT_PCT = 3.0


# ═══════════════════════════════════════════════════════════════
# EXPECTED PROFIT SCORE CALCULATION (Problem 8)
# ═══════════════════════════════════════════════════════════════

def calculate_expected_profit_score(signal: Dict[str, Any]) -> float:
    """
    Calculate Expected Profit Score (0-100) from signal data.

    Formula (Problem 8):
        Expected Profit = Trend Strength × ATR Expansion × Volume Expansion
                        × Momentum × Liquidity × Risk Reward

    Each component normalized to 0-100, then geometric mean for composite.

    Args:
        signal: Live Sheet signal dict

    Returns:
        Expected Profit Score (0-100)
    """
    # ── Trend Strength ──
    # Use regime strength + EMA alignment
    regime_strength = signal.get("regime_strength", signal.get("trend_strength", 50))
    ema_alignment = signal.get("ema_alignment", 50)
    trend = (regime_strength * 0.6 + ema_alignment * 0.4)

    # ── ATR Expansion ──
    # Higher ATR = more opportunity for profit
    atr = signal.get("atr", 0)
    atr_pct = signal.get("atr_pct", 0)
    if atr_pct > 0:
        # Normalize: 1% ATR = 50, 3% ATR = 100
        atr_score = min(100, max(0, atr_pct * 33.3))
    elif atr > 0 and signal.get("entry_price", 0) > 0:
        atr_pct = atr / signal["entry_price"] * 100
        atr_score = min(100, max(0, atr_pct * 33.3))
    else:
        atr_score = 50  # Default

    # ── Volume Expansion ──
    # Higher volume = better fills, more participation
    volume = signal.get("volume", 0)
    avg_volume = signal.get("avg_volume", signal.get("volume_20", 0))
    if avg_volume > 0 and volume > 0:
        vol_ratio = volume / avg_volume
        vol_score = min(100, max(0, vol_ratio * 50))
    else:
        vol_score = 50  # Default

    # ── Momentum ──
    cvd = abs(signal.get("cvd", 0))
    delta = abs(signal.get("delta", 0))
    momentum = signal.get("momentum", signal.get("momentum_score", 50))
    # Combine CVD, delta, and raw momentum
    momentum_score = min(100, max(0, (cvd * 100 * 0.3 + delta * 100 * 0.3 + momentum * 0.4)))

    # ── Liquidity ──
    spread_pct = signal.get("spread_pct", 0.05)
    liq_score = max(0, 100 - spread_pct * 1000)  # Lower spread = better liquidity

    # ── Risk Reward ──
    rr = signal.get("risk_reward", signal.get("rr", 1.5))
    rr_score = min(100, max(0, rr * 33.3))  # 3R = 100, 0R = 0

    # ── Geometric Mean (prevents one strong factor from dominating) ──
    factors = [trend, atr_score, vol_score, momentum_score, li_score := liq_score, rr_score]
    # Avoid log(0) by clamping to minimum 1
    factors = [max(1, f) for f in factors]
    import math
    geo_mean = math.exp(sum(math.log(f) for f in factors) / len(factors))

    return round(min(100, max(0, geo_mean)), 1)


@dataclass
class SizingResult:
    """Position sizing output."""
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    position_value: float = 0.0
    margin_required: float = 0.0
    risk_amount: float = 0.0
    risk_pct: float = 0.0
    quality_multiplier: float = 0.0
    drawdown_adjustment: float = 1.0
    correlation_adjustment: float = 1.0
    daily_adjustment: float = 1.0
    final_multiplier: float = 0.0
    approved: bool = False
    rejection_reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": round(self.quantity, 6),
            "position_value": round(self.position_value, 2),
            "margin_required": round(self.margin_required, 2),
            "risk_amount": round(self.risk_amount, 2),
            "risk_pct": round(self.risk_pct, 2),
            "quality_multiplier": round(self.quality_multiplier, 2),
            "drawdown_adjustment": round(self.drawdown_adjustment, 2),
            "correlation_adjustment": round(self.correlation_adjustment, 2),
            "daily_adjustment": round(self.daily_adjustment, 2),
            "final_multiplier": round(self.final_multiplier, 2),
            "approved": self.approved,
            "rejection_reason": self.rejection_reason,
        }


class AppPositionSizingEngine:
    """
    Dynamic position sizing based on quality, risk, and portfolio state.

    Per Master Directive: Never equal sizing.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self.balance: float = 10_000.0
        self.peak_balance: float = 10_000.0
        self.daily_pnl: float = 0.0
        self.open_positions: List[Dict] = []
        self._recent_losses: int = 0  # consecutive recent losses

    def set_portfolio_state(
        self,
        balance: float,
        peak_balance: float,
        daily_pnl: float,
        open_positions: List[Dict],
        recent_losses: int = 0,
    ) -> None:
        """Update portfolio state for sizing calculations."""
        self.balance = balance
        self.peak_balance = peak_balance
        self.daily_pnl = daily_pnl
        self.open_positions = open_positions
        self._recent_losses = recent_losses

    def calculate_size(
        self,
        signal: Dict[str, Any],
        trade_quality_score: float = 0.0,
        leverage: int = 10,
        expected_profit_score: float = 0.0,
    ) -> SizingResult:
        """
        Calculate position size for a signal.

        Uses Expected Profit Score as primary sizing input (Problem 3 + 10),
        blended with Trade Quality Score for robust sizing.

        Args:
            signal: Live Sheet signal dict
            trade_quality_score: Composite TQ score (0-100)
            leverage: Position leverage
            expected_profit_score: Expected Profit Score (0-100) from pipeline

        Returns:
            SizingResult with size and adjustment breakdown
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        entry = signal.get("entry_price", signal.get("entry", 0))
        sl = signal.get("stop_loss", 0)

        result = SizingResult(symbol=symbol, side=side)

        if entry <= 0 or sl <= 0:
            result.rejection_reason = "missing entry/SL"
            return result

        # ── Check 1: Max open positions ──
        if len(self.open_positions) >= MAX_OPEN_POSITIONS:
            result.rejection_reason = (
                f"max positions reached ({len(self.open_positions)}/{MAX_OPEN_POSITIONS})"
            )
            return result

        # ── Check 2: Daily loss limit ──
        daily_loss_pct = abs(self.daily_pnl) / self.balance * 100 if self.daily_pnl < 0 else 0
        if daily_loss_pct >= DAILY_LOSS_LIMIT_PCT:
            result.rejection_reason = (
                f"daily loss limit reached ({daily_loss_pct:.1f}% >= {DAILY_LOSS_LIMIT_PCT}%)"
            )
            return result

        # ── Calculate Expected Profit Score if not provided ──
        if expected_profit_score <= 0:
            expected_profit_score = calculate_expected_profit_score(signal)

        # ── Expected Profit Multiplier (primary — Problem 3 + 10 fix) ──
        ep_multiplier = self._get_expected_profit_multiplier(expected_profit_score)
        if ep_multiplier == 0:
            result.rejection_reason = (
                f"expected_profit_score {expected_profit_score:.1f} < 80 minimum threshold"
            )
            return result

        # ── Quality Multiplier (secondary) ──
        result.quality_multiplier = self._get_quality_multiplier(trade_quality_score)

        # ── Blend multipliers: 60% expected profit + 40% quality ──
        if result.quality_multiplier > 0:
            blended_multiplier = (
                ep_multiplier * EXPECTED_PROFIT_WEIGHT
                + result.quality_multiplier * QUALITY_WEIGHT
            )
        else:
            # No quality data — use expected profit only
            blended_multiplier = ep_multiplier

        result.final_multiplier = blended_multiplier

        # ── Drawdown Adjustment ──
        result.drawdown_adjustment = self._get_drawdown_adjustment()

        # ── Correlation Adjustment ──
        result.correlation_adjustment = self._get_correlation_adjustment(signal)

        # ── Daily Adjustment ──
        result.daily_adjustment = self._get_daily_adjustment()

        # ── Final Multiplier with adjustments ──
        result.final_multiplier = (
            blended_multiplier
            * result.drawdown_adjustment
            * result.correlation_adjustment
            * result.daily_adjustment
        )

        # ── Calculate Size ──
        risk_distance = abs(entry - sl)
        base_risk_usd = self.balance * BASE_RISK_PCT / 100
        adjusted_risk_usd = base_risk_usd * result.final_multiplier

        # Risk-adjusted quantity
        result.risk_amount = adjusted_risk_usd
        result.risk_pct = adjusted_risk_usd / self.balance * 100

        # Check portfolio heat
        current_heat = sum(
            p.get("risk_amount", 0) for p in self.open_positions
        )
        remaining_heat = (self.balance * MAX_PORTFOLIO_HEAT_PCT / 100) - current_heat
        if adjusted_risk_usd > remaining_heat:
            adjusted_risk_usd = max(remaining_heat * 0.9, 0)
            result.risk_amount = adjusted_risk_usd
            result.risk_pct = adjusted_risk_usd / self.balance * 100

        if adjusted_risk_usd <= 0:
            result.rejection_reason = "no remaining risk capacity"
            return result

        # Final quantity
        result.quantity = adjusted_risk_usd / risk_distance if risk_distance > 0 else 0
        result.position_value = result.quantity * entry
        result.margin_required = result.position_value / leverage

        # Check single position limit
        max_position_value = self.balance * MAX_SINGLE_POSITION_PCT / 100 * leverage
        if result.position_value > max_position_value:
            result.position_value = max_position_value
            result.quantity = result.position_value / entry
            result.margin_required = result.position_value / leverage

        result.approved = True

        logger.debug(
            "SIZE: {} {} → qty={:.4f} val=${:.2f} risk=${:.2f} ({:.2f}%) "
            "ep_score={:.1f} ep_mult={:.2f} q_mult={:.2f} "
            "blend={:.2f} dd={:.2f} corr={:.2f} day={:.2f}",
            symbol, side, result.quantity, result.position_value,
            result.risk_amount, result.risk_pct, result.final_multiplier,
            expected_profit_score, ep_multiplier, result.quality_multiplier,
            blended_multiplier, result.drawdown_adjustment,
            result.correlation_adjustment, result.daily_adjustment,
        )

        return result

    def _get_expected_profit_multiplier(self, ep_score: float) -> float:
        """Get position size multiplier based on Expected Profit Score (Problem 10)."""
        for (lo, hi), mult in EXPECTED_PROFIT_MULTIPLIERS.items():
            if lo <= ep_score < hi:
                return mult
        return 0.0

    def _get_quality_multiplier(self, tq_score: float) -> float:
        """Get position size multiplier based on trade quality score."""
        for (lo, hi), mult in QUALITY_MULTIPLIERS.items():
            if lo <= tq_score < hi:
                return mult
        return 0.0

    def _get_drawdown_adjustment(self) -> float:
        """Reduce size during drawdowns."""
        if self.peak_balance <= 0:
            return 1.0

        dd = (self.peak_balance - self.balance) / self.peak_balance

        if dd < DD_REDUCTION_START:
            return 1.0
        elif dd >= DD_REDUCTION_FULL:
            return 0.3  # Max 70% reduction
        else:
            # Linear interpolation between start and full reduction
            range_dd = DD_REDUCTION_FULL - DD_REDUCTION_START
            progress = (dd - DD_REDUCTION_START) / range_dd
            return 1.0 - (progress * 0.7)

    def _get_correlation_adjustment(self, signal: Dict) -> float:
        """Reduce size when holding correlated positions."""
        if not self.open_positions:
            return 1.0

        # Simple correlation check: same side = correlated
        side = signal.get("side", "")
        same_side_count = sum(
            1 for p in self.open_positions
            if p.get("side") == side
        )

        if same_side_count >= 3:
            return 0.5  # Heavy reduction
        elif same_side_count >= 2:
            return 0.7  # Moderate reduction
        elif same_side_count >= 1:
            return 0.85  # Light reduction
        return 1.0

    def _get_daily_adjustment(self) -> float:
        """Adjust based on daily performance."""
        if self.daily_pnl >= 0:
            # Winning day — no reduction (could even increase)
            return 1.0

        daily_loss_pct = abs(self.daily_pnl) / self.balance * 100

        if daily_loss_pct >= DAILY_LOSS_LIMIT_PCT * 0.8:
            return 0.3  # Near limit — heavy reduction
        elif daily_loss_pct >= DAILY_LOSS_LIMIT_PCT * 0.5:
            return 0.6  # Moderate reduction
        elif self._recent_losses >= 3:
            return 0.7  # Consecutive losses
        return 1.0
