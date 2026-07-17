"""
Arbitrage Ranker — Scores and prioritizes detected opportunities.
"""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List

@dataclass
class ArbitrageOpportunity:
    arb_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    arb_type: str = ""
    symbol: str = ""
    long_exchange: str = ""
    short_exchange: str = ""
    long_exchange_price: float = 0.0
    short_exchange_price: float = 0.0
    entry_spread_bps: float = 0.0
    net_edge_bps: float = 0.0
    expected_profit_usd: float = 0.0
    expected_fee_usd: float = 0.0
    expected_slippage_usd: float = 0.0
    confidence: float = 0.0
    score: float = 0.0
    timestamp: int = 0
    meta: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)

class ArbitrageRanker:
    """
    Calculates a 0-100 score for opportunities to determine execution priority.
    Formula: Score = (Edge * 0.4) + (Liquidity * 0.3) + (Confidence * 0.2) + (Latency * 0.1)
    """

    def rank_opportunities(self, opportunities: List[ArbitrageOpportunity]) -> List[ArbitrageOpportunity]:
        for opp in opportunities:
            opp.score = self._calculate_score(opp)
        
        # Sort by score descending
        return sorted(opportunities, key=lambda x: x.score, reverse=True)

    def _calculate_score(self, opp: ArbitrageOpportunity) -> float:
        # 1. Edge component (max at 50 bps)
        edge_score = min(100, (opp.net_edge_bps / 50.0) * 100)
        
        # 2. Confidence component
        confidence_score = opp.confidence * 100
        
        # 3. Spread stability (High raw spread relative to net edge is risky)
        stability_score = 100 - min(100, (opp.entry_spread_bps - opp.net_edge_bps) * 2)
        
        # 4. Type Weights (Basis/Funding are generally higher quality)
        type_multiplier = 1.0
        if opp.arb_type in ("basis_arbitrage", "funding_arbitrage"):
            type_multiplier = 1.15

        final_score = (
            (edge_score * 0.5) + 
            (confidence_score * 0.3) + 
            (stability_score * 0.2)
        ) * type_multiplier
        
        return min(100.0, final_score)