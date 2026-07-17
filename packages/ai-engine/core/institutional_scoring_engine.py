"""
Institutional Scoring Engine — Phase 1: Institutional Weighted Confidence.

Replaces the old 8-pillar equal-weight system with the institutional 7-pillar
weighted model per spec:

    Liquidity Sweep       = 25%
    Market Structure Shift = 20%
    Fair Value Gap        = 15%
    Open Interest         = 15%
    Delta                 = 10%
    CVD                   = 10%
    Funding               =  5%

Total = 100%

Only signals with confidence >= 85 are eligible.
"""
from __future__ import annotations
from typing import Dict, Any, Optional
from loguru import logger


class InstitutionalScoringEngine:
    """
    Calculates a 0-100 composite confidence score using institutional 7-pillar weights.

    Each pillar input should be a raw score 0-100.  The engine applies the
    specified weights and returns the weighted sum as the final confidence.
    """

    # ── Institutional Weights (Phase 1 spec) ──
    WEIGHTS = {
        "sweep":                  25,   # Liquidity Sweep detection
        "market_structure_shift": 20,   # MSS / regime quality
        "fair_value_gap":         15,   # FVG presence + quality
        "open_interest":          15,   # OI expansion / positioning
        "delta":                  10,   # Delta (taker aggression)
        "cvd":                    10,   # Cumulative Volume Delta trend
        "funding":                 5,   # Funding rate contrarian signal
    }
    TOTAL_WEIGHT = 100

    # ── Classification tiers (confidence 0-100) ──
    TIER_ELITE = 70
    TIER_HIGH = 60
    TIER_CANDIDATE = 40

    # ── Minimum confidence to generate a signal ──
    MIN_CONFIDENCE = 70

    def calculate_score(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Compute institutional confidence from 7 pillar scores.

        Parameters
        ----------
        data : dict
            Keys (each 0-100 scale, or None if data unavailable):
              - sweep_score:             Liquidity sweep quality
              - mss_score:               Market Structure Shift quality
              - fvg_score:               Fair Value Gap quality
              - oi_score:                Open Interest expansion score
              - delta_score:             Delta aggression score
              - cvd_score:               CVD trend score
              - funding_score:           Funding rate contrarian score

        Returns
        -------
        dict with:
            institutional_score : float (0-100)
            confidence          : float (0-100), same as institutional_score
            classification      : str
            available_pillars   : int
            missing_pillars     : int
            pillar_scores       : dict {pillar: weighted_contribution}
            score_breakdown     : dict {label: weighted_value}
            below_threshold     : bool  (True if < MIN_CONFIDENCE)
        """
        pillar_map = [
            ("sweep_score",             "sweep",                  self.WEIGHTS["sweep"]),
            ("mss_score",               "market_structure_shift", self.WEIGHTS["market_structure_shift"]),
            ("fvg_score",               "fair_value_gap",         self.WEIGHTS["fair_value_gap"]),
            ("oi_score",                "open_interest",          self.WEIGHTS["open_interest"]),
            ("delta_score",             "delta",                  self.WEIGHTS["delta"]),
            ("cvd_score",               "cvd",                    self.WEIGHTS["cvd"]),
            ("funding_score",           "funding",                self.WEIGHTS["funding"]),
        ]

        total_score = 0.0
        available_pillars = 0
        missing_pillars = 0
        pillar_scores: Dict[str, float] = {}

        for data_key, pillar_name, weight in pillar_map:
            val = data.get(data_key)
            if val is not None:
                # val is 0-100, weight is the percentage points
                contribution = val * (weight / self.TOTAL_WEIGHT)
                total_score += contribution
                available_pillars += 1
                pillar_scores[pillar_name] = round(contribution, 2)
            else:
                missing_pillars += 1

        # ── Missing data penalty: 2 points per missing pillar ──
        # Reduced from 3 to avoid over-penalizing partial data.
        missing_penalty = missing_pillars * 2
        total_score = max(0.0, total_score - missing_penalty)

        # ── Data coverage bonus: reward signals with all 7 pillars ──
        if available_pillars == 7:
            total_score = min(100.0, total_score + 3)  # +3 bonus for full coverage
        elif available_pillars >= 5:
            total_score = min(100.0, total_score + 1)  # +1 for 5-6 pillars

        # ── CONFIDENCE CAP: never output 100% — max is 95% ──
        # Raw scores should never mean certainty. Cap at 95.
        total_score = min(95.0, total_score)

        # ── Base calibration: +3 (reduced from +5 to prevent score inflation) ──
        total_score = min(95.0, total_score + 3)

        total_score = max(0.0, min(100.0, total_score))

        classification = self._get_tier(total_score)
        below_threshold = total_score < self.MIN_CONFIDENCE

        # ── Build human-readable score breakdown ──
        LABEL_MAP = {
            "sweep":                  "Sweep",
            "market_structure_shift": "MSS",
            "fair_value_gap":         "FVG",
            "open_interest":          "OI",
            "delta":                  "Delta",
            "cvd":                    "CVD",
            "funding":                "Funding",
        }
        score_breakdown: Dict[str, float] = {}
        for pillar_key, weighted_val in pillar_scores.items():
            label = LABEL_MAP.get(pillar_key, pillar_key.title())
            score_breakdown[label] = round(weighted_val, 2)

        return {
            "institutional_score": round(total_score, 2),
            "confidence": round(total_score, 2),
            "classification": classification,
            "available_pillars": available_pillars,
            "missing_pillars": missing_pillars,
            "pillar_scores": pillar_scores,
            "score_breakdown": score_breakdown,
            "below_threshold": below_threshold,
        }

    def _get_tier(self, score: float) -> str:
        if score >= self.TIER_ELITE:
            return "ELITE"
        if score >= self.TIER_HIGH:
            return "HIGH CONVICTION"
        if score >= self.TIER_CANDIDATE:
            return "TRADE CANDIDATE"
        return "WATCHLIST"

    # ── Backward-compatible alias ──
    def get_tier(self, score: float) -> str:
        return self._get_tier(score)
