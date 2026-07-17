"""
Arbitrage Ranker — Scores and prioritizes arbitrage opportunities.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from loguru import logger


@dataclass
class ArbitrageOpportunity:
    """Detected arbitrage opportunity."""
    arb_id: str = field(default_factory=lambda: f"ARB-{int(time.time())}-{time.time_ns() % 100000}")
    arb_type: str = "" # cross_exchange_spread, funding_arbitrage, basis_arbitrage, statistical_arbitrage, synthetic_arbitrage
    symbol: str = ""
    long_exchange: str = ""
    short_exchange: str = ""
    
    # Metrics for ranking
    entry_spread_bps: float = 0.0  # Raw spread in basis points
    net_edge_bps: float = 0.0      # Estimated profit after all costs, in basis points
    expected_profit_usd: float = 0.0
    expected_fee_usd: float = 0.0
    expected_slippage_usd: float = 0.0
    confidence: float = 0.0        # 0-1, higher is better
    liquidity_score: float = 0.0   # 0-1, higher is better
    latency_ms: float = 0.0        # Expected round-trip latency
    risk_score: float = 0.0        # 0-100, lower is better (e.g., correlation risk)
    
    # Final score after ranking
    score: float = 0.0             # 0-100, higher is better
    
    timestamp: int = 0
    meta: Dict[str, Any] = field(default_factory=dict) # Additional strategy-specific data

    def to_dict(self) -> Dict:
        return {
            k: getattr(self, k) for k in self.__dataclass_fields__
        }


class ArbitrageRanker:
    """
    Scores and prioritizes arbitrage opportunities based on various factors.
    """
    def __init__(self) -> None:
        # Configurable weights for ranking factors
        self.weights = {
            "net_edge_bps": 0.40,
            "confidence": 0.25,
            "liquidity_score": 0.15,
            "latency_ms": 0.10,
            "risk_score": 0.10, # Inverse weight
        }

    def rank_opportunities(self, opportunities: List[ArbitrageOpportunity]) -> List[ArbitrageOpportunity]:
        """
        Calculates a composite score for each opportunity and ranks them.
        """
        for opp in opportunities:
            # Normalize factors to a 0-1 scale where 1 is best
            normalized_edge = min(1.0, max(0.0, opp.net_edge_bps / 50.0)) # 50 bps is excellent
            normalized_confidence = opp.confidence
            normalized_liquidity = opp.liquidity_score # Assumed to be 0-1 already
            normalized_latency = max(0.0, 1.0 - (opp.latency_ms / 200.0)) # 200ms is bad
            normalized_risk = max(0.0, 1.0 - (opp.risk_score / 100.0)) # 100 is max risk

            opp.score = (
                self.weights["net_edge_bps"] * normalized_edge +
                self.weights["confidence"] * normalized_confidence +
                self.weights["liquidity_score"] * normalized_liquidity +
                self.weights["latency_ms"] * normalized_latency +
                self.weights["risk_score"] * normalized_risk
            ) * 100 # Scale to 0-100

        # Sort by score in descending order
        opportunities.sort(key=lambda x: x.score, reverse=True)
        return opportunities