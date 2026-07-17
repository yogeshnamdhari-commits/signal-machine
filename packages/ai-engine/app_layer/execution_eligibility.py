"""
Execution Eligibility Engine — Elite trade filtering.

Per Executive Assessment v2:
    "The trade selection entering the execution layer is still allowing
     too many low-expectancy trades."

    Need:
        Scanner → Expected Profit Ranking → Elite Filter → Execution

    Only execute trades with Execution Score ≥ 90.

Score Components (weighted composite 0-100):
    1. Confidence          (12%) — Raw signal confidence
    2. Expected RR         (15%) — Risk/reward ratio
    3. ATR Expansion       (10%) — Volatility opportunity
    4. OI Agreement        (12%) — Open interest confirms direction
    5. Funding Agreement   (10%) — Funding rate confirms direction
    6. Volume Expansion    (10%) — Volume supports move
    7. Session Quality     (8%)  — Active session vs off-hours
    8. Symbol Performance  (13%) — Historical PF for this symbol
    9. Recent Strategy PF  (10%) — Recent system performance

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# ELIGIBILITY CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Minimum Execution Score to allow trade
MIN_ELIGIBILITY_SCORE = 90

# Component weights (must sum to 1.0)
WEIGHTS = {
    "confidence":        0.12,
    "expected_rr":       0.15,
    "atr_expansion":     0.10,
    "oi_agreement":      0.12,
    "funding_agreement": 0.10,
    "volume_expansion":  0.10,
    "session_quality":   0.08,
    "symbol_performance": 0.13,
    "strategy_pf":       0.10,
}

# Session definitions (UTC hours)
SESSIONS = {
    "asia":      (0, 8),     # 00:00–08:00 UTC
    "london":    (7, 15),    # 07:00–15:00 UTC
    "new_york":  (13, 21),   # 13:00–21:00 UTC
    "off_hours": (21, 24),   # 21:00–00:00 UTC (low volume)
}


@dataclass
class ComponentScore:
    """Score for a single eligibility component."""
    name: str
    raw_value: float
    score: float        # 0-100
    weight: float
    weighted: float     # score * weight
    detail: str = ""


@dataclass
class EligibilityResult:
    """Result from the Execution Eligibility Engine."""
    symbol: str = ""
    side: str = ""
    eligible: bool = False
    execution_score: float = 0.0
    components: List[ComponentScore] = field(default_factory=list)
    rejection_reason: str = ""
    ranking_position: int = 0  # Position in sorted queue (1 = best)
    is_a_plus: bool = False    # Top-tier trade

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "eligible": self.eligible,
            "execution_score": round(self.execution_score, 1),
            "rejection_reason": self.rejection_reason,
            "ranking_position": self.ranking_position,
            "is_a_plus": self.is_a_plus,
            "components": {
                c.name: {
                    "raw": round(c.raw_value, 3),
                    "score": round(c.score, 1),
                    "weighted": round(c.weighted, 2),
                    "detail": c.detail,
                }
                for c in self.components
            },
        }


class ExecutionEligibilityEngine:
    """
    Elite trade filtering — only pass highest-expectancy trades to execution.

    Per Executive Assessment v2:
        "Execution cannot turn poor expectancy trades into profitable ones."

    This engine scores every signal on 9 dimensions and only approves
    trades with Execution Score ≥ 90.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self._symbol_pf_cache: Dict[str, float] = {}
        self._session_pf_cache: Dict[str, float] = {}
        self._strategy_pf_recent: float = 1.0
        self._last_load = 0.0

    def evaluate(
        self,
        signal: Dict[str, Any],
        market_data: Optional[Dict] = None,
        symbol_pf: float = 0.0,
        session_pf: float = 0.0,
        strategy_pf: float = 1.0,
    ) -> EligibilityResult:
        """
        Evaluate whether a signal meets elite execution criteria.

        Args:
            signal: Live Sheet signal dict
            market_data: Optional market data
            symbol_pf: Historical profit factor for this symbol
            session_pf: Historical profit factor for this session
            strategy_pf: Recent strategy-wide profit factor

        Returns:
            EligibilityResult with score and decision
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        result = EligibilityResult(symbol=symbol, side=side)

        # ── Component 1: Confidence (12%) ──
        confidence = signal.get("confidence", 0)
        if confidence > 1.0:
            confidence = confidence  # Already 0-100
        else:
            confidence = confidence * 100  # Convert 0-1 to 0-100

        conf_score = self._score_confidence(confidence)
        result.components.append(ComponentScore(
            name="confidence", raw_value=confidence,
            score=conf_score, weight=WEIGHTS["confidence"],
            weighted=conf_score * WEIGHTS["confidence"],
            detail=f"confidence={confidence:.1f}",
        ))

        # ── Component 2: Expected RR (15%) ──
        rr = signal.get("risk_reward", signal.get("rr", 0))
        rr_score = self._score_rr(rr)
        result.components.append(ComponentScore(
            name="expected_rr", raw_value=rr,
            score=rr_score, weight=WEIGHTS["expected_rr"],
            weighted=rr_score * WEIGHTS["expected_rr"],
            detail=f"rr={rr:.2f}",
        ))

        # ── Component 3: ATR Expansion (10%) ──
        atr_pct = signal.get("atr_pct", 0)
        if atr_pct == 0 and signal.get("entry_price", 0) > 0:
            atr = signal.get("atr", 0)
            atr_pct = atr / signal["entry_price"] * 100 if atr > 0 else 0
        atr_score = self._score_atr_expansion(atr_pct)
        result.components.append(ComponentScore(
            name="atr_expansion", raw_value=atr_pct,
            score=atr_score, weight=WEIGHTS["atr_expansion"],
            weighted=atr_score * WEIGHTS["atr_expansion"],
            detail=f"atr_pct={atr_pct:.2f}%",
        ))

        # ── Component 4: OI Agreement (12%) ──
        oi_delta = signal.get("oi_delta", signal.get("oi_change", 0))
        oi_score = self._score_oi_agreement(oi_delta, side)
        result.components.append(ComponentScore(
            name="oi_agreement", raw_value=oi_delta,
            score=oi_score, weight=WEIGHTS["oi_agreement"],
            weighted=oi_score * WEIGHTS["oi_agreement"],
            detail=f"oi_delta={oi_delta:.4f} side={side}",
        ))

        # ── Component 5: Funding Agreement (10%) ──
        funding = signal.get("funding_rate", 0)
        funding_score = self._score_funding_agreement(funding, side)
        result.components.append(ComponentScore(
            name="funding_agreement", raw_value=funding,
            score=funding_score, weight=WEIGHTS["funding_agreement"],
            weighted=funding_score * WEIGHTS["funding_agreement"],
            detail=f"funding={funding:.6f} side={side}",
        ))

        # ── Component 6: Volume Expansion (10%) ──
        volume = signal.get("volume", 0)
        avg_volume = signal.get("avg_volume", signal.get("volume_20", 0))
        vol_score = self._score_volume_expansion(volume, avg_volume)
        vol_ratio = volume / avg_volume if avg_volume > 0 else 1.0
        result.components.append(ComponentScore(
            name="volume_expansion", raw_value=vol_ratio,
            score=vol_score, weight=WEIGHTS["volume_expansion"],
            weighted=vol_score * WEIGHTS["volume_expansion"],
            detail=f"vol_ratio={vol_ratio:.2f}",
        ))

        # ── Component 7: Session Quality (8%) ──
        session = signal.get("session", signal.get("at_open_session", ""))
        if not session:
            session = self._detect_session()
        session_score = self._score_session(session)
        result.components.append(ComponentScore(
            name="session_quality", raw_value=0,
            score=session_score, weight=WEIGHTS["session_quality"],
            weighted=session_score * WEIGHTS["session_quality"],
            detail=f"session={session}",
        ))

        # ── Component 8: Symbol Performance (13%) ──
        sym_pf = symbol_pf if symbol_pf > 0 else self._get_symbol_pf(symbol)
        sym_score = self._score_symbol_performance(sym_pf)
        result.components.append(ComponentScore(
            name="symbol_performance", raw_value=sym_pf,
            score=sym_score, weight=WEIGHTS["symbol_performance"],
            weighted=sym_score * WEIGHTS["symbol_performance"],
            detail=f"symbol_pf={sym_pf:.2f}",
        ))

        # ── Component 9: Strategy PF (10%) ──
        strat_pf = strategy_pf if strategy_pf > 0 else self._strategy_pf_recent
        strat_score = self._score_strategy_pf(strat_pf)
        result.components.append(ComponentScore(
            name="strategy_pf", raw_value=strat_pf,
            score=strat_score, weight=WEIGHTS["strategy_pf"],
            weighted=strat_score * WEIGHTS["strategy_pf"],
            detail=f"strategy_pf={strat_pf:.2f}",
        ))

        # ── Composite Score ──
        result.execution_score = sum(c.weighted for c in result.components)

        # ── Eligibility Decision ──
        result.eligible = result.execution_score >= MIN_ELIGIBILITY_SCORE
        result.is_a_plus = result.execution_score >= 95

        if not result.eligible:
            # Find the weakest component for rejection reason
            weakest = min(result.components, key=lambda c: c.score)
            result.rejection_reason = (
                f"score={result.execution_score:.1f} < {MIN_ELIGIBILITY_SCORE} "
                f"(weakest: {weakest.name}={weakest.score:.1f})"
            )

        logger.debug(
            "ELIGIBILITY: {} {} → {:.1f}/100 {} (A+={})",
            symbol, side, result.execution_score,
            "✓ ELIGIBLE" if result.eligible else "✗ REJECTED",
            result.is_a_plus,
        )

        return result

    def rank_signals(
        self,
        signals: List[Dict[str, Any]],
        market_data: Optional[Dict] = None,
    ) -> List[EligibilityResult]:
        """
        Rank multiple signals by execution score.

        Per Executive Assessment v2:
            "12 signals → Rank Expected Profit → Take Top 3 → Ignore remaining 9"

        Returns:
            List of EligibilityResult sorted by score (best first),
            with ranking_position set.
        """
        results = []
        for sig in signals:
            result = self.evaluate(sig, market_data)
            results.append(result)

        # Sort by execution score (best first)
        results.sort(key=lambda r: r.execution_score, reverse=True)

        # Set ranking positions
        for i, result in enumerate(results):
            result.ranking_position = i + 1

        return results

    def get_elite_count(self, signals: List[Dict]) -> int:
        """Count how many signals pass the elite filter."""
        results = self.rank_signals(signals)
        return sum(1 for r in results if r.eligible)

    # ═══════════════════════════════════════════════════════════════
    # SCORING FUNCTIONS (0-100)
    # ═══════════════════════════════════════════════════════════════

    def _score_confidence(self, confidence: float) -> float:
        """Score confidence: 90+ = 100, 80 = 60, <70 = 0."""
        if confidence >= 95:
            return 100
        elif confidence >= 90:
            return 85 + (confidence - 90) * 3  # 85-100
        elif confidence >= 85:
            return 65 + (confidence - 85) * 4  # 65-85
        elif confidence >= 80:
            return 40 + (confidence - 80) * 5  # 40-65
        elif confidence >= 70:
            return 10 + (confidence - 70) * 3  # 10-40
        return 0

    def _score_rr(self, rr: float) -> float:
        """Score risk/reward: 3R = 100, 2R = 70, 1.5R = 50, <1R = 0."""
        if rr >= 3.5:
            return 100
        elif rr >= 3.0:
            return 85 + (rr - 3.0) * 30  # 85-100
        elif rr >= 2.0:
            return 60 + (rr - 2.0) * 25  # 60-85
        elif rr >= 1.5:
            return 40 + (rr - 1.5) * 40  # 40-60
        elif rr >= 1.0:
            return 15 + (rr - 1.0) * 50  # 15-40
        return max(0, rr * 15)

    def _score_atr_expansion(self, atr_pct: float) -> float:
        """Score ATR expansion: 2-4% = ideal, >5% = too volatile."""
        if 2.0 <= atr_pct <= 4.0:
            return 100
        elif atr_pct > 4.0:
            return max(40, 100 - (atr_pct - 4.0) * 15)  # Decay above 4%
        elif atr_pct >= 1.0:
            return 40 + (atr_pct - 1.0) * 20  # 40-60
        elif atr_pct >= 0.5:
            return 20 + (atr_pct - 0.5) * 40  # 20-40
        return max(0, atr_pct * 40)

    def _score_oi_agreement(self, oi_delta: float, side: str) -> float:
        """Score OI agreement: OI increasing in trade direction = high score."""
        if side == "LONG":
            # Positive OI delta = new longs opening = bullish
            if oi_delta > 0.05:
                return 100
            elif oi_delta > 0.02:
                return 70 + (oi_delta - 0.02) * 1000  # 70-100
            elif oi_delta > 0:
                return 50 + oi_delta * 1000  # 50-70
            elif oi_delta > -0.02:
                return 30 + (oi_delta + 0.02) * 1000  # 30-50
            return max(0, 30 + oi_delta * 500)
        else:  # SHORT
            # Negative OI delta = new shorts opening = bearish
            if oi_delta < -0.05:
                return 100
            elif oi_delta < -0.02:
                return 70 + (-oi_delta - 0.02) * 1000
            elif oi_delta < 0:
                return 50 + (-oi_delta) * 1000
            elif oi_delta < 0.02:
                return 30 + (0.02 - oi_delta) * 1000
            return max(0, 30 - oi_delta * 500)

    def _score_funding_agreement(self, funding: float, side: str) -> float:
        """Score funding agreement: Funding confirms trade direction."""
        if side == "LONG":
            # Negative funding = shorts paying longs = bullish
            if funding < -0.0005:
                return 100
            elif funding < -0.0001:
                return 70 + (-funding - 0.0001) * 60000  # 70-100
            elif funding < 0.0001:
                return 50 + (-funding) * 100000  # 50-70
            elif funding < 0.0005:
                return 30 + (0.0005 - funding) * 50000  # 30-50
            return max(0, 30 - funding * 20000)
        else:  # SHORT
            # Positive funding = longs paying shorts = bearish
            if funding > 0.0005:
                return 100
            elif funding > 0.0001:
                return 70 + (funding - 0.0001) * 60000
            elif funding > -0.0001:
                return 50 + funding * 100000
            elif funding > -0.0005:
                return 30 + (funding + 0.0005) * 50000
            return max(0, 30 + funding * 20000)

    def _score_volume_expansion(self, volume: float, avg_volume: float) -> float:
        """Score volume expansion: Volume > avg = good, >1.5x = excellent."""
        if avg_volume <= 0 or volume <= 0:
            return 50  # Default if no data

        ratio = volume / avg_volume
        if ratio >= 2.0:
            return 100
        elif ratio >= 1.5:
            return 80 + (ratio - 1.5) * 40  # 80-100
        elif ratio >= 1.0:
            return 50 + (ratio - 1.0) * 60  # 50-80
        elif ratio >= 0.7:
            return 25 + (ratio - 0.7) * 83  # 25-50
        return max(0, ratio * 36)

    def _score_session(self, session: str) -> float:
        """Score session quality: London/NY = high, Asia = medium, Off-hours = low."""
        session_scores = {
            "new_york": 95,
            "london": 90,
            "asia": 65,
            "off_hours": 30,
            "overlap": 100,  # London-NY overlap
        }
        return session_scores.get(session.lower().replace(" ", "_"), 50)

    def _score_symbol_performance(self, symbol_pf: float) -> float:
        """Score symbol historical performance: PF > 1.5 = 100, < 0.8 = 0."""
        if symbol_pf <= 0:
            return 50  # No data — neutral
        if symbol_pf >= 2.0:
            return 100
        elif symbol_pf >= 1.5:
            return 80 + (symbol_pf - 1.5) * 40  # 80-100
        elif symbol_pf >= 1.0:
            return 50 + (symbol_pf - 1.0) * 60  # 50-80
        elif symbol_pf >= 0.7:
            return 20 + (symbol_pf - 0.7) * 100  # 20-50
        return max(0, symbol_pf * 29)

    def _score_strategy_pf(self, strategy_pf: float) -> float:
        """Score recent strategy performance: PF > 1.3 = 100, < 0.8 = 20."""
        if strategy_pf >= 1.5:
            return 100
        elif strategy_pf >= 1.3:
            return 80 + (strategy_pf - 1.3) * 100  # 80-100
        elif strategy_pf >= 1.0:
            return 50 + (strategy_pf - 1.0) * 100  # 50-80
        elif strategy_pf >= 0.8:
            return 20 + (strategy_pf - 0.8) * 150  # 20-50
        return max(0, strategy_pf * 25)

    # ═══════════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═══════════════════════════════════════════════════════════════

    def _detect_session(self) -> str:
        """Detect current trading session from UTC hour."""
        utc_hour = time.gmtime().tm_hour
        for session, (start, end) in SESSIONS.items():
            if start <= utc_hour < end:
                return session
        return "off_hours"

    def _get_symbol_pf(self, symbol: str) -> float:
        """Get cached symbol profit factor (0 = no data)."""
        return self._symbol_pf_cache.get(symbol, 0)

    def update_symbol_pf(self, symbol: str, pf: float) -> None:
        """Update symbol PF cache (called by Continuous Learning Layer)."""
        self._symbol_pf_cache[symbol] = pf

    def update_strategy_pf(self, pf: float) -> None:
        """Update recent strategy PF (called by Continuous Learning Layer)."""
        self._strategy_pf_recent = pf

    def get_all_symbol_pfs(self) -> Dict[str, float]:
        """Get all cached symbol PFs."""
        return dict(self._symbol_pf_cache)
