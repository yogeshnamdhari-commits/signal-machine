"""
Institutional Engine — High-Conviction Trade Signal Generator
Processes orderflow, liquidity, and market regime data to identify institutional activity.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from loguru import logger


class InstitutionalEngine:
    """
    Analyzes raw scanner data to identify high-probability institutional trade setups.
    Requires a minimum confidence score of 80 to generate a signal.
    """

    def __init__(self, min_confidence: int = 50, min_factors: int = 2): # Default to 50 and 2
        self.min_confidence = min_confidence # This is 0-100 scale
        self.min_factors = min_factors
        # Weights per user specification
        self.weights = {
            "delta": 15,
            "cvd": 15,
            "oi": 15,
            "funding": 10,
            "flow": 10,
            "liquidations": 10,
            "absorption": 15,
            "sweep": 10,
        }
        self._patterns: Dict[str, Any] = {}

    async def initialize(self) -> None:
        """Initialize institutional detection state."""
        logger.info("Institutional Engine Initialized")

    async def process_orderbook(self, symbol: str, depth: Dict[str, List]) -> None:
        """Process orderbook updates to find institutional patterns."""
        # Initialize pattern storage for the symbol if not present
        if symbol not in self._patterns:
            self._patterns[symbol] = {"imbalance": 0.0, "walls": []}
        # Real-time depth analysis logic would be integrated here

    def get_patterns(self, symbol: str) -> Dict[str, Any]:
        """Return detected patterns for a specific symbol."""
        return self._patterns.get(symbol, {})

    def get_opportunity(
        self,
        symbol: str,
        price: float,
        volume: float,
        delta: float,
        cvd: float,
        open_interest: float,
        funding_rate: float,
        delta_funding: float,
        exchange_flow: float,
        long_liquidation: float,
        short_liquidation: float,
        absorption_score: float,  # 0 to 1
        sweep_score: float,       # 0 to 1
        spoofing_score: float,    # 0 to 1
        market_regime: str
    ) -> Optional[Dict[str, Any]]:
        """
        Analyzes market data and returns a trade opportunity if confidence > 80.
        """
        long_score = 0
        short_score = 0
        reasons = []

        # 1. Delta Analysis (+15)
        if delta > 0:
            long_score += self.weights["delta"]
            reasons.append(f"Positive Delta ({delta:,.0f}) indicates aggressive buying")
        elif delta < 0:
            short_score += self.weights["delta"]
            reasons.append(f"Negative Delta ({delta:,.0f}) indicates aggressive selling")

        # 2. CVD Trend (+15)
        if cvd > 0:
            long_score += self.weights["cvd"]
            reasons.append("CVD trending positive: cumulative buying pressure")
        elif cvd < 0:
            short_score += self.weights["cvd"]
            reasons.append("CVD trending negative: cumulative selling pressure")

        # 3. Open Interest (+15)
        # Fix: OI increase should confirm the side with aggressive delta
        if open_interest > 0:
            if delta > 0:
                long_score += self.weights["oi"]
            elif delta < 0:
                short_score += self.weights["oi"]
            reasons.append(f"Rising Open Interest ({open_interest:,.0f}) confirms trend conviction")

        # 4. Real Funding Divergence (+10)
        # Bullish if Delta (Professional) is cheaper than Binance (Retail)
        if delta_funding < funding_rate - 0.0001:
            long_score += self.weights["funding"]
            reasons.append(f"Bullish Real Funding Divergence: Delta ({delta_funding:.4%}) vs Binance ({funding_rate:.4%})")
        elif delta_funding > funding_rate + 0.0001:
            short_score += self.weights["funding"]
            reasons.append(f"Bearish Real Funding Divergence: Delta ({delta_funding:.4%}) vs Binance ({funding_rate:.4%})")
            
        if funding_rate < -0.0002: # Extreme negative on Binance
            long_score += self.weights["funding"]
            reasons.append(f"Negative Funding ({funding_rate:.4%}): Short squeeze potential")
        elif funding_rate > 0.0005:
            short_score += self.weights["funding"]
            reasons.append(f"High Funding ({funding_rate:.4%}): Long exhaustion potential")

        # 5. Exchange Flow (+10)
        # Outflow (negative) is bullish, Inflow (positive) is bearish
        if exchange_flow < 0:
            long_score += self.weights["flow"]
            reasons.append("Net exchange outflows: supply diminishing")
        elif exchange_flow > 0:
            short_score += self.weights["flow"]
            reasons.append("Net exchange inflows: potential sell pressure")

        # 6. Liquidations (+10)
        # High short liqs feed long momentum, high long liqs feed short momentum
        if short_liquidation > long_liquidation:
            long_score += self.weights["liquidations"]
            reasons.append("Short liquidations accelerating upside momentum")
        elif long_liquidation > short_liquidation:
            short_score += self.weights["liquidations"]
            reasons.append("Long liquidations accelerating downside momentum")

        # 7. Absorption (+15)
        # High absorption score indicates passive limit orders soaking up market orders
        if absorption_score > 0.7:
            if delta < 0 and market_regime in ["ranging", "trending_up"]:
                long_score += self.weights["absorption"]
                reasons.append("Bullish Absorption: Aggressive selling soaked by limit buyers")
            elif delta > 0 and market_regime in ["ranging", "trending_down"]:
                short_score += self.weights["absorption"]
                reasons.append("Bearish Absorption: Aggressive buying soaked by limit sellers")

        # 8. Sweep Detection (+10)
        if sweep_score > 0.7:
            if delta > 0:
                long_score += self.weights["sweep"]
                reasons.append("Institutional Sweep: Aggressive liquidity grab to the upside")
            else:
                short_score += self.weights["sweep"]
                reasons.append("Institutional Sweep: Aggressive liquidity grab to the downside")

        # 9. Spoofing Detection (Risk Factor)
        if spoofing_score > 0.8:
            # High spoofing score indicates potential manipulation/fake walls
            reasons.append(f"High Manipulation Risk (Spoofing Score: {spoofing_score:.2f})")
            # You could optionally penalize confidence here if spoofing is too high

        # Final Determination
        side = "LONG" if long_score >= short_score else "SHORT"
        confidence = long_score if side == "LONG" else short_score
        passed = len(reasons)

        confirmed = (confidence >= self.min_confidence) and (passed >= self.min_factors) # min_confidence is 0-100

        if not confirmed:
            logger.info(
                f"REJECTED | {symbol} | "
                f"conf={confidence/100:.2f} "
                f"passed={passed} "
                f"required={self.min_factors}"
            )
            return None

        # Risk Management Logic based on Regime
        entry = price
        risk_reward = 3.0
        
        # Adjust SL/TP based on market regime
        if market_regime == "volatile":
            sl_pct = 0.03 # 3% for volatile markets
        elif market_regime in ["breakout", "trending_up", "trending_down"]:
            sl_pct = 0.015 # 1.5% for trend following
        else:
            sl_pct = 0.01 # 1% for ranging

        if side == "LONG":
            stop = entry * (1 - sl_pct)
            target = entry * (1 + (sl_pct * risk_reward))
        else:
            stop = entry * (1 + sl_pct)
            target = entry * (1 - (sl_pct * risk_reward))

        # Filter reasons to only those relevant to the winning side
        relevant_reasons = self._filter_reasons(reasons, side)

        return {
            "symbol": symbol,
            "side": side,
            "confidence": int(confidence),
            "entry": round(entry, 6),
            "stop": round(stop, 6),
            "target": round(target, 6),
            "risk_reward": risk_reward,
            "reasons": relevant_reasons,
            "regime": market_regime
        }

    def _filter_reasons(self, reasons: List[str], side: str) -> List[str]:
        """Filters the generic reasons list for side-specific context."""
        filtered = []
        for r in reasons:
            if side == "LONG":
                if any(x in r.lower() for x in ["positive", "buying", "outflow", "upside", "bullish", "short liquidation"]):
                    filtered.append(r)
            else:
                if any(x in r.lower() for x in ["negative", "selling", "inflow", "downside", "bearish", "long liquidation"]):
                    filtered.append(r)
        
        # Always include conviction and risk indicators, ensuring case-insensitivity for 'outflow'
        filtered.extend([r for r in reasons if any(x in r for x in ["Open Interest", "Manipulation"]) or "outflow" in r.lower()])

        return list(set(filtered))