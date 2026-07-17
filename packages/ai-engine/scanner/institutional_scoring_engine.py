"""
Institutional Scoring Engine — Implements weighted orderflow intelligence.
"""
from __future__ import annotations
from typing import Dict, Any, Optional
from loguru import logger

class InstitutionalScoringEngine:
    """
    Calculates a 0-100 score based on 8 institutional pillars.
    """
    WEIGHTS = {
        "delta": 15,
        "cvd": 15,
        "open_interest": 15,
        "funding": 10,
        "exchange_flow": 10,
        "liquidation": 10,
        "absorption": 15,
        "market_regime": 10
    }

    def calculate_score(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate institutional score from pillar data.
        Returns dict with score (0-100) and pillar breakdown.
        Missing data is penalized (not defaulted to 0.5).
        """
        total_score = 0.0
        available_pillars = 0
        missing_pillars = 0
        pillar_scores = {}
        
        # 1. Delta (15%)
        if data.get("delta_score") is not None:
            total_score += data["delta_score"] * self.WEIGHTS["delta"]
            available_pillars += 1
            pillar_scores["delta"] = data["delta_score"] * self.WEIGHTS["delta"]
        else:
            missing_pillars += 1
        
        # 2. CVD (15%)
        if data.get("cvd_score") is not None:
            total_score += data["cvd_score"] * self.WEIGHTS["cvd"]
            available_pillars += 1
            pillar_scores["cvd"] = data["cvd_score"] * self.WEIGHTS["cvd"]
        else:
            missing_pillars += 1
        
        # 3. Open Interest (15%)
        if data.get("oi_score") is not None:
            total_score += data["oi_score"] * self.WEIGHTS["open_interest"]
            available_pillars += 1
            pillar_scores["oi"] = data["oi_score"] * self.WEIGHTS["open_interest"]
        else:
            missing_pillars += 1
        
        # 4. Funding (10%)
        if data.get("funding_score") is not None:
            total_score += data["funding_score"] * self.WEIGHTS["funding"]
            available_pillars += 1
            pillar_scores["funding"] = data["funding_score"] * self.WEIGHTS["funding"]
        else:
            missing_pillars += 1
        
        # 5. Exchange Flow (10%)
        if data.get("flow_score") is not None:
            total_score += data["flow_score"] * self.WEIGHTS["exchange_flow"]
            available_pillars += 1
            pillar_scores["flow"] = data["flow_score"] * self.WEIGHTS["exchange_flow"]
        else:
            missing_pillars += 1
        
        # 6. Liquidation (10%)
        if data.get("liq_score") is not None:
            total_score += data["liq_score"] * self.WEIGHTS["liquidation"]
            available_pillars += 1
            pillar_scores["liquidation"] = data["liq_score"] * self.WEIGHTS["liquidation"]
        else:
            missing_pillars += 1
        
        # 7. Absorption (15%)
        if data.get("absorption_score") is not None:
            total_score += data["absorption_score"] * self.WEIGHTS["absorption"]
            available_pillars += 1
            pillar_scores["absorption"] = data["absorption_score"] * self.WEIGHTS["absorption"]
        else:
            missing_pillars += 1
        
        # 8. Market Regime (10%)
        if data.get("regime_fit") is not None:
            total_score += data["regime_fit"] * self.WEIGHTS["market_regime"]
            available_pillars += 1
            pillar_scores["regime"] = data["regime_fit"] * self.WEIGHTS["market_regime"]
        else:
            missing_pillars += 1
        
        # Penalize missing data: reduce score by 3% per missing pillar
        # This prevents signals from getting inflated scores when data is incomplete
        missing_penalty = missing_pillars * 3
        total_score = max(0, total_score - missing_penalty)
        
        return {
            "institutional_score": total_score,
            "available_pillars": available_pillars,
            "missing_pillars": missing_pillars,
            "pillar_scores": pillar_scores,
        }

    def get_tier(self, score: float) -> str:
        if score >= 85: return "ELITE"
        if score >= 70: return "HIGH CONVICTION"
        if score >= 50: return "TRADE CANDIDATE"
        return "WATCHLIST"