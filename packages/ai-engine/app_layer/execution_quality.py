"""
Execution Quality Filter — Pre-trade execution quality assessment.

READ-ONLY with respect to upstream data. Never modifies orders or positions.

Per Master Directive:
    "Before execution, evaluate: Spread, Estimated slippage, Available liquidity,
     Order-book imbalance. If execution quality is poor, skip the trade even if
     the signal is otherwise strong."

Execution Quality Factors:
    1. Spread: Bid-ask spread as % of price
    2. Slippage: Estimated fill slippage based on order size vs depth
    3. Liquidity: Available volume at target price levels
    4. Imbalance: Order book bid/ask ratio
    5. Volatility: Current volatility vs normal (high vol = worse fills)
    6. Timing: Time of day impact on execution quality

Output:
    - ExecutionQualityScore (0-100)
    - Estimated slippage in bps
    - Approval/rejection decision
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# EXECUTION QUALITY THRESHOLDS
# ═══════════════════════════════════════════════════════════════

# Maximum acceptable spread as % of price
MAX_SPREAD_PCT = 0.10  # 0.10%

# Maximum acceptable slippage in basis points
MAX_SLIPPAGE_BPS = 10  # 10 bps = 0.10%

# Minimum liquidity score (0-100)
MIN_LIQUIDITY_SCORE = 40

# Maximum order book imbalance ratio (ask/bid)
MAX_IMBALANCE_RATIO = 3.0

# Minimum execution quality score to allow trade
MIN_EXEC_QUALITY = 50


@dataclass
class ExecutionQualityResult:
    """Result of execution quality assessment."""
    symbol: str = ""
    approved: bool = False
    quality_score: float = 0.0
    estimated_slippage_bps: float = 0.0
    spread_pct: float = 0.0
    liquidity_score: float = 0.0
    imbalance_ratio: float = 0.0
    volatility_impact: float = 0.0
    timing_score: float = 0.0
    rejection_reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "approved": self.approved,
            "quality_score": round(self.quality_score, 1),
            "estimated_slippage_bps": round(self.estimated_slippage_bps, 2),
            "spread_pct": round(self.spread_pct, 4),
            "liquidity_score": round(self.liquidity_score, 1),
            "imbalance_ratio": round(self.imbalance_ratio, 2),
            "volatility_impact": round(self.volatility_impact, 2),
            "timing_score": round(self.timing_score, 1),
            "rejection_reason": self.rejection_reason,
        }


class ExecutionQualityFilter:
    """
    Evaluates execution quality before trade approval.

    Per Master Directive: Skip the trade if execution quality is poor,
    even if the signal is otherwise strong.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self._last_prices: Dict[str, float] = {}
        self._last_spreads: Dict[str, float] = {}

    def evaluate(
        self,
        signal: Dict[str, Any],
        market_data: Optional[Dict] = None,
    ) -> ExecutionQualityResult:
        """
        Evaluate execution quality for a signal.

        Args:
            signal: Trade signal dict
            market_data: Optional market data with spread/depth info

        Returns:
            ExecutionQualityResult with quality assessment
        """
        symbol = signal.get("symbol", "")
        entry = signal.get("entry_price", signal.get("entry", 0))
        side = signal.get("side", "")

        result = ExecutionQualityResult(symbol=symbol)

        if entry <= 0:
            result.rejection_reason = "no entry price"
            return result

        md = market_data or {}

        # ── Factor 1: Spread ──
        spread = md.get("spread", 0) or self._last_spreads.get(symbol, 0)
        if spread > 0:
            result.spread_pct = spread / entry * 100 if entry > 0 else 0
        else:
            # Estimate spread from typical market data
            result.spread_pct = self._estimate_spread(symbol)

        # ── Factor 2: Liquidity ──
        result.liquidity_score = self._assess_liquidity(md, symbol)

        # ── Factor 3: Imbalance ──
        result.imbalance_ratio = self._assess_imbalance(md, side)

        # ── Factor 4: Volatility Impact ──
        result.volatility_impact = self._assess_volatility_impact(md)

        # ── Factor 5: Timing ──
        result.timing_score = self._assess_timing()

        # ── Factor 6: Estimate Slippage ──
        result.estimated_slippage_bps = self._estimate_slippage(
            result.spread_pct, result.liquidity_score, result.volatility_impact
        )

        # ── Composite Quality Score ──
        # Spread quality (lower is better)
        spread_score = max(0, 100 - result.spread_pct * 1000)  # 0.1% = 0 score

        # Liquidity (already 0-100)
        liquidity = result.liquidity_score

        # Imbalance (lower ratio is better)
        imbalance_score = max(0, 100 - (result.imbalance_ratio - 1) * 30)

        # Timing (already 0-100)
        timing = result.timing_score

        # Volatility (lower impact is better)
        vol_score = max(0, 100 - result.volatility_impact * 50)

        # Weighted composite
        result.quality_score = (
            spread_score * 0.25
            + liquidity * 0.25
            + imbalance_score * 0.20
            + timing * 0.10
            + vol_score * 0.20
        )

        # ── Decision ──
        if result.quality_score >= MIN_EXEC_QUALITY:
            result.approved = True
        else:
            result.approved = False
            result.rejection_reason = (
                f"execution quality {result.quality_score:.1f} < {MIN_EXEC_QUALITY} "
                f"(spread={result.spread_pct:.4f}% liquidity={result.liquidity_score:.0f} "
                f"imbalance={result.imbalance_ratio:.2f})"
            )

        logger.debug(
            "EXEC: {} → {} (quality={:.1f} slippage={:.1f}bps spread={:.4f}%)",
            symbol, "PASS" if result.approved else "FAIL",
            result.quality_score, result.estimated_slippage_bps, result.spread_pct,
        )

        return result

    # ── Individual Assessors ─────────────────────────────────────

    def _estimate_spread(self, symbol: str) -> float:
        """Estimate spread percentage based on symbol characteristics."""
        # Typical spreads for different tiers
        if "BTC" in symbol:
            return 0.01  # 0.01% for BTC
        elif "ETH" in symbol:
            return 0.02  # 0.02% for ETH
        elif any(x in symbol for x in ["SOL", "BNB", "XRP"]):
            return 0.03  # 0.03% for major alts
        else:
            return 0.05  # 0.05% for smaller alts

    def _assess_liquidity(self, md: Dict, symbol: str) -> float:
        """Assess available liquidity."""
        volume = md.get("volume_24h", 0) or md.get("quote_volume", 0)
        if volume <= 0:
            return 50  # No data — assume moderate

        # Volume-based liquidity scoring
        if volume >= 100_000_000:  # $100M+
            return 95
        elif volume >= 50_000_000:  # $50M+
            return 85
        elif volume >= 10_000_000:  # $10M+
            return 70
        elif volume >= 1_000_000:  # $1M+
            return 50
        else:
            return 25

    def _assess_imbalance(self, md: Dict, side: str) -> float:
        """Assess order book imbalance."""
        bid_depth = md.get("bid_depth", 0) or md.get("bids_total", 0)
        ask_depth = md.get("ask_depth", 0) or md.get("asks_total", 0)

        if bid_depth <= 0 or ask_depth <= 0:
            return 1.0  # No data — assume balanced

        if side == "LONG":
            # For LONG: we want more bids (support)
            return ask_depth / max(bid_depth, 1)
        else:
            # For SHORT: we want more asks (resistance)
            return bid_depth / max(ask_depth, 1)

    def _assess_volatility_impact(self, md: Dict) -> float:
        """Assess volatility impact on execution quality."""
        volatility = md.get("volatility", 0) or md.get("atr_pct", 0)
        if volatility <= 0:
            return 0.3  # Default moderate

        # Higher volatility = worse execution
        if volatility >= 5.0:
            return 1.0  # Extreme
        elif volatility >= 3.0:
            return 0.7
        elif volatility >= 1.5:
            return 0.4
        else:
            return 0.2

    def _assess_timing(self) -> float:
        """Assess timing-based execution quality."""
        from datetime import datetime, timezone
        hour = datetime.now(timezone.utc).hour

        # Best execution during high-volume sessions
        if 13 <= hour < 16:
            return 95  # London-NY overlap
        elif 7 <= hour < 13:
            return 80  # London
        elif 16 <= hour < 21:
            return 75  # NY
        elif 0 <= hour < 3:
            return 50  # Asia open
        else:
            return 35  # Off-hours

    def _estimate_slippage(
        self, spread_pct: float, liquidity_score: float, vol_impact: float
    ) -> float:
        """Estimate slippage in basis points."""
        # Base slippage from spread
        base_bps = spread_pct * 100  # Convert % to bps

        # Liquidity adjustment (low liquidity = more slippage)
        liquidity_mult = 1.0 + (1 - liquidity_score / 100) * 0.5

        # Volatility adjustment
        vol_mult = 1.0 + vol_impact * 0.3

        estimated_bps = base_bps * liquidity_mult * vol_mult

        return estimated_bps
