"""
EMA_V5 Quality — Quality scoring and assessment for signals.
Assigns quality grades and identifies improvement opportunities.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .diagnostics import SignalDiagnostics


class EMAv5Quality:
    """Quality scoring and assessment for EMA_V5 signals."""

    # Quality thresholds
    GRADE_THRESHOLDS = {
        "A+": 95, "A": 90, "B+": 85, "B": 80,
        "C+": 70, "C": 60, "D": 50, "F": 0,
    }

    # Check weights for quality score
    CHECK_WEIGHTS = {
        "ema_alignment": 15,
        "trend_direction": 15,
        "ema_slopes": 10,
        "pullback": 12,
        "candlestick": 10,
        "volume": 8,
        "confidence": 12,
        "state_transition": 5,
        "duplicate": 3,
        "risk_reward": 5,
        "price_validity": 3,
        "trade_lifecycle": 2,
    }

    def score_signal(self, diag: SignalDiagnostics) -> Dict[str, Any]:
        """Compute quality score for a verified signal.
        
        Returns quality assessment with score, grade, and breakdown.
        """
        score = 0
        max_score = sum(self.CHECK_WEIGHTS.values())
        breakdown = {}

        for check in diag.checks:
            weight = self.CHECK_WEIGHTS.get(check.name, 1)
            if check.passed:
                # Full weight for passed checks
                # Bonus for strong passes (high confidence, strong slopes, etc.)
                bonus = self._compute_bonus(check)
                earned = weight + bonus
                score += earned
                breakdown[check.name] = {"earned": round(earned, 1), "max": weight, "status": "PASS"}
            else:
                # Partial credit for some checks
                partial = self._compute_partial_credit(check)
                score += partial
                breakdown[check.name] = {"earned": round(partial, 1), "max": weight, "status": "FAIL"}

        # Normalize to 0-100
        quality_score = round((score / max_score) * 100, 1) if max_score > 0 else 0
        quality_score = min(quality_score, 100)

        # Assign grade
        grade = self._assign_grade(quality_score)

        # Identify strengths and weaknesses
        strengths = [k for k, v in breakdown.items() if v["status"] == "PASS" and v["earned"] > v["max"] * 0.8]
        weaknesses = [k for k, v in breakdown.items() if v["status"] == "FAIL"]

        # Recommendations
        recommendations = self._generate_recommendations(breakdown, weaknesses)

        return {
            "quality_score": quality_score,
            "grade": grade,
            "breakdown": breakdown,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "recommendations": recommendations,
            "verdict": diag.verdict,
            "confidence": diag.confidence_score,
        }

    def score_batch(self, diagnostics: List[SignalDiagnostics]) -> Dict[str, Any]:
        """Score a batch of signals and compute aggregate quality."""
        if not diagnostics:
            return {"avg_score": 0, "avg_grade": "F", "distribution": {}, "count": 0}

        scores = [self.score_signal(d) for d in diagnostics]
        quality_scores = [s["quality_score"] for s in scores]

        # Grade distribution
        distribution: Dict[str, int] = {}
        for s in scores:
            grade = s["grade"]
            distribution[grade] = distribution.get(grade, 0) + 1

        # Common weaknesses
        all_weaknesses = []
        for s in scores:
            all_weaknesses.extend(s["weaknesses"])

        weakness_counts: Dict[str, int] = {}
        for w in all_weaknesses:
            weakness_counts[w] = weakness_counts.get(w, 0) + 1

        top_weaknesses = sorted(weakness_counts.items(), key=lambda x: -x[1])[:5]

        return {
            "avg_score": round(sum(quality_scores) / len(quality_scores), 1),
            "avg_grade": self._assign_grade(sum(quality_scores) / len(quality_scores)),
            "distribution": distribution,
            "count": len(diagnostics),
            "min_score": round(min(quality_scores), 1),
            "max_score": round(max(quality_scores), 1),
            "top_weaknesses": [{"check": w, "count": c} for w, c in top_weaknesses],
        }

    def _compute_bonus(self, check) -> float:
        """Compute bonus points for strong passes."""
        if check.name == "confidence":
            # Bonus for high confidence
            value = check.value if isinstance(check.value, (int, float)) else 0
            if value >= 0.95:
                return 2.0
            elif value >= 0.92:
                return 1.0
        elif check.name == "ema_slopes":
            # Bonus for strong slopes
            value = check.value
            if isinstance(value, dict):
                slope_20 = abs(value.get("slope_ema20", 0))
                if slope_20 > 0.5:
                    return 1.5
                elif slope_20 > 0.2:
                    return 0.5
        elif check.name == "risk_reward":
            # Bonus for high R:R
            value = check.value if isinstance(check.value, (int, float)) else 0
            if value >= 3.0:
                return 1.0
            elif value >= 2.0:
                return 0.5
        elif check.name == "volume":
            # Bonus for volume surge
            value = check.value
            if isinstance(value, dict):
                ratio = value.get("ratio", 0)
                if ratio >= 1.5:
                    return 1.0
        return 0.0

    def _compute_partial_credit(self, check) -> float:
        """Compute partial credit for failed checks."""
        # Some checks deserve partial credit
        if check.name == "pullback":
            return 2.0  # Partial credit if pullback was attempted
        elif check.name == "candlestick":
            return 1.0  # Partial credit for candle analysis
        elif check.name == "ema_slopes":
            return 1.0  # Partial credit if slopes are weak but present
        return 0.0

    def _assign_grade(self, score: float) -> str:
        """Assign letter grade from score."""
        for grade, threshold in self.GRADE_THRESHOLDS.items():
            if score >= threshold:
                return grade
        return "F"

    def _generate_recommendations(self, breakdown: Dict, weaknesses: List[str]) -> List[str]:
        """Generate improvement recommendations."""
        recommendations = []

        for weakness in weaknesses:
            if weakness == "ema_alignment":
                recommendations.append("Wait for EMA chain to fully align before entry")
            elif weakness == "trend_direction":
                recommendations.append("Ensure trend direction matches signal side")
            elif weakness == "ema_slopes":
                recommendations.append("Consider waiting for stronger EMA slopes")
            elif weakness == "pullback":
                recommendations.append("Wait for confirmed pullback to EMA20/EMA50")
            elif weakness == "candlestick":
                recommendations.append("Wait for stronger candlestick confirmation")
            elif weakness == "volume":
                recommendations.append("Wait for volume expansion above SMA20")
            elif weakness == "confidence":
                recommendations.append("Increase minimum confidence threshold")
            elif weakness == "state_transition":
                recommendations.append("Check state machine before signal generation")
            elif weakness == "duplicate":
                recommendations.append("Strengthen duplicate detection logic")
            elif weakness == "risk_reward":
                recommendations.append("Adjust SL/TP for better R:R ratio")
            elif weakness == "price_validity":
                recommendations.append("Validate entry/SL/TP price values")
            elif weakness == "trade_lifecycle":
                recommendations.append("Ensure signal has all required metadata")

        return recommendations[:5]  # Max 5 recommendations
