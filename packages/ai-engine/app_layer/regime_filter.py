"""
Regime Filter — Blocks trades during unfavorable market regimes.

READ-ONLY with respect to upstream data. Never modifies signals or regime data.

Per Master Directive:
    "Reject trades during: Weak trend, Range, Low volatility, Low liquidity.
     Unless: Trade Quality Score exceeds elite threshold."

Regime Classification:
    BLOCK:    range, compression, unknown
    ALLOW:    trending_bull, trending_bear, volatile, breakout
    ELITE:    All regimes allowed if Trade Quality Score >= 85
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# REGIME CLASSIFICATIONS
# ═══════════════════════════════════════════════════════════════

# Regimes that are always allowed
ALLOWED_REGIMES = {
    "trending_bull",
    "trending_bear",
    "volatile",
    "breakout",
}

# Regimes that are blocked unless elite override
BLOCKED_REGIMES = {
    "range",
    "compression",
    "unknown",
    "0.0",  # legacy null value
}

# Trade Quality Score threshold for regime override
ELITE_TQ_THRESHOLD = 85


@dataclass
class RegimeFilterResult:
    """Result of regime filtering."""
    symbol: str = ""
    side: str = ""
    approved: bool = False
    regime: str = ""
    blocked: bool = False
    elite_override: bool = False
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "approved": self.approved,
            "regime": self.regime,
            "blocked": self.blocked,
            "elite_override": self.elite_override,
            "reason": self.reason,
        }


class RegimeFilter:
    """
    Filters trades based on market regime.

    BLOCKED: range, compression, unknown — unless Trade Quality Score >= 85.

    READ-ONLY: never modifies upstream data.
    """

    def evaluate(
        self,
        signal: Dict[str, Any],
        trade_quality_score: float = 0.0,
    ) -> RegimeFilterResult:
        """
        Evaluate whether a trade should be allowed based on regime.

        Args:
            signal: Live Sheet signal dict
            trade_quality_score: Composite TQ score (0-100)

        Returns:
            RegimeFilterResult with approval status
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        regime = signal.get("regime", signal.get("market_regime", "unknown"))

        result = RegimeFilterResult(
            symbol=symbol, side=side, regime=regime,
        )

        # Normalize regime string
        regime_str = str(regime).lower().strip()

        # Check if regime is in allowed list
        if regime_str in ALLOWED_REGIMES:
            result.approved = True
            result.reason = f"regime '{regime}' is allowed"
            return result

        # Check if regime is blocked
        if regime_str in BLOCKED_REGIMES:
            # Check for elite override
            if trade_quality_score >= ELITE_TQ_THRESHOLD:
                result.approved = True
                result.elite_override = True
                result.reason = (
                    f"regime '{regime}' blocked but TQ={trade_quality_score:.1f} "
                    f">= {ELITE_TQ_THRESHOLD} elite threshold"
                )
                logger.info(
                    "REGIME ELITE OVERRIDE: {} {} regime={} TQ={:.1f}",
                    symbol, side, regime, trade_quality_score,
                )
            else:
                result.approved = False
                result.blocked = True
                result.reason = (
                    f"regime '{regime}' blocked (TQ={trade_quality_score:.1f} "
                    f"< {ELITE_TQ_THRESHOLD} needed for override)"
                )
        else:
            # Unknown regime — default to blocked
            if trade_quality_score >= ELITE_TQ_THRESHOLD:
                result.approved = True
                result.elite_override = True
                result.reason = f"unknown regime but TQ={trade_quality_score:.1f} elite"
            else:
                result.approved = False
                result.blocked = True
                result.reason = f"unknown regime '{regime}' — blocked by default"

        logger.debug(
            "REGIME: {} {} → {} (regime={}, TQ={:.1f})",
            symbol, side, "ALLOW" if result.approved else "BLOCK",
            regime, trade_quality_score,
        )

        return result
