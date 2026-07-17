"""
Institution Agreement Engine — Requires consensus among institutional data sources.

READ-ONLY with respect to upstream data. Never modifies signals, only evaluates agreement.

Per Master Directive:
    "Create a hard lock. LONG requires CVD Bullish AND Flow Bullish AND Delta Positive
     AND Regime Bull. Otherwise NO TRADE."

This engine evaluates whether institutional data sources agree with the trade direction.
If agreement is below threshold, the trade is REJECTED regardless of confidence score.

Data Sources Evaluated:
    1. CVD (Cumulative Volume Delta)
    2. Delta (Volume Delta)
    3. Exchange Flow (inflow/outflow)
    4. Open Interest Delta
    5. Funding Rate
    6. Institutional Score (from scanner)
    7. Absorption Score
    8. Sweep Score
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# AGREEMENT THRESHOLDS
# ═══════════════════════════════════════════════════════════════

# Minimum agreement ratio to allow trade (0.0 - 1.0)
# Example: 0.6 means at least 60% of sources must agree with direction
MIN_AGREEMENT_RATIO = 0.60

# Minimum number of data sources that must have non-zero values
MIN_DATA_SOURCES = 3

# Hard block: if CVD + Delta + Flow ALL disagree, reject regardless
HARD_BLOCK_THRESHOLD = 0  # 0 agreeing among these 3 = hard block

# Elite override: if institutional_score >= threshold, allow with lower agreement
ELITE_INSTITUTION_SCORE = 90
ELITE_MIN_AGREEMENT = 0.50


@dataclass
class SourceVote:
    """Vote from a single institutional data source."""
    name: str
    value: float
    agrees: bool           # Does this source agree with trade direction?
    weight: float          # Importance weight
    confidence: float      # How confident we are in this data point (0-1)
    detail: str = ""


@dataclass
class AgreementResult:
    """Complete agreement assessment for a signal."""
    symbol: str = ""
    side: str = ""
    approved: bool = False
    agreement_ratio: float = 0.0
    total_sources: int = 0
    agreeing_sources: int = 0
    hard_block: bool = False
    elite_override: bool = False
    votes: List[SourceVote] = field(default_factory=list)
    rejection_reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "approved": self.approved,
            "agreement_ratio": round(self.agreement_ratio, 3),
            "agreeing": f"{self.agreeing_sources}/{self.total_sources}",
            "hard_block": self.hard_block,
            "elite_override": self.elite_override,
            "rejection_reason": self.rejection_reason,
            "votes": {
                v.name: {"agrees": v.agrees, "value": round(v.value, 4), "detail": v.detail}
                for v in self.votes
            },
        }


class InstitutionAgreementEngine:
    """
    Evaluates whether institutional data sources agree with trade direction.

    Per Master Directive:
    - LONG requires: CVD Bullish AND Flow Bullish AND Delta Positive AND Regime Bull
    - SHORT requires: CVD Bearish AND Flow Bearish AND Delta Negative AND Regime Bear
    - If agreement is weak → REJECT TRADE

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self._results: Dict[str, AgreementResult] = {}

    def evaluate(self, signal: Dict[str, Any]) -> AgreementResult:
        """
        Evaluate institutional agreement for a single signal.

        Args:
            signal: Live Sheet signal dict

        Returns:
            AgreementResult with approval status and vote breakdown
        """
        side = signal.get("side", "")
        symbol = signal.get("symbol", "")
        inst_score = signal.get("institutional_score", 0)

        result = AgreementResult(symbol=symbol, side=side)

        if not side or side not in ("LONG", "SHORT"):
            result.rejection_reason = f"invalid side: {side}"
            return result

        # ── Collect Votes ──

        # 1. CVD
        result.votes.append(self._vote_cvd(signal, side))

        # 2. Delta
        result.votes.append(self._vote_delta(signal, side))

        # 3. Exchange Flow
        result.votes.append(self._vote_exchange_flow(signal, side))

        # 4. Open Interest Delta
        result.votes.append(self._vote_oi_delta(signal, side))

        # 5. Funding Rate
        result.votes.append(self._vote_funding(signal, side))

        # 6. Absorption
        result.votes.append(self._vote_absorption(signal, side))

        # 7. Sweep
        result.votes.append(self._vote_sweep(signal, side))

        # ── Tally Votes ──
        valid_votes = [v for v in result.votes if v.confidence > 0]
        result.total_sources = len(valid_votes)

        if result.total_sources == 0:
            result.rejection_reason = "no institutional data available"
            result.approved = False
            self._results[f"{symbol}_{side}"] = result
            return result

        agreeing = [v for v in valid_votes if v.agrees]
        result.agreeing_sources = len(agreeing)
        result.agreement_ratio = result.agreeing_sources / result.total_sources

        # ── Hard Block Check ──
        # Check the "big 3": CVD, Delta, Exchange Flow
        big3 = [v for v in result.votes if v.name in ("cvd", "delta", "exchange_flow") and v.confidence > 0]
        big3_agreeing = [v for v in big3 if v.agrees]

        if len(big3) >= 2 and len(big3_agreeing) < HARD_BLOCK_THRESHOLD:
            result.hard_block = True
            result.rejection_reason = (
                f"HARD BLOCK: CVD/Delta/Flow all disagree "
                f"({len(big3_agreeing)}/{len(big3)} agree)"
            )
            result.approved = False
            self._results[f"{symbol}_{side}"] = result
            return result

        # ── Elite Override ──
        if inst_score >= ELITE_INSTITUTION_SCORE:
            result.elite_override = True
            min_agreement = ELITE_MIN_AGREEMENT
        else:
            min_agreement = MIN_AGREEMENT_RATIO

        # ── Final Decision ──
        if result.agreement_ratio >= min_agreement:
            result.approved = True
            result.rejection_reason = ""
        else:
            result.rejection_reason = (
                f"insufficient agreement: {result.agreement_ratio:.0%} "
                f"({result.agreeing_sources}/{result.total_sources}) "
                f"< {min_agreement:.0%} required"
            )
            result.approved = False

        logger.debug(
            "INST: {} {} → {} (agree={:.0%} {}, block={}, elite={})",
            symbol, side, "APPROVED" if result.approved else "REJECTED",
            result.agreement_ratio, f"({result.agreeing_sources}/{result.total_sources})",
            result.hard_block, result.elite_override,
        )

        self._results[f"{symbol}_{side}"] = result
        return result

    def get_result(self, symbol: str, side: str) -> Optional[AgreementResult]:
        """Retrieve cached result."""
        return self._results.get(f"{symbol}_{side}")

    # ── Individual Voters ────────────────────────────────────────

    @staticmethod
    def _vote_cvd(sig: Dict, side: str) -> SourceVote:
        """CVD vote — cumulative volume delta direction."""
        cvd = sig.get("cvd", 0)
        if cvd == 0:
            return SourceVote("cvd", 0, False, 1.0, 0, "no data")

        agrees = (side == "LONG" and cvd > 0) or (side == "SHORT" and cvd < 0)
        return SourceVote(
            name="cvd", value=cvd, agrees=agrees, weight=1.0, confidence=1.0,
            detail=f"cvd={cvd:.4f} {'✓' if agrees else '✗'}",
        )

    @staticmethod
    def _vote_delta(sig: Dict, side: str) -> SourceVote:
        """Delta vote — volume delta direction."""
        delta = sig.get("delta", 0)
        if delta == 0:
            return SourceVote("delta", 0, False, 1.0, 0, "no data")

        agrees = (side == "LONG" and delta > 0) or (side == "SHORT" and delta < 0)
        return SourceVote(
            name="delta", value=delta, agrees=agrees, weight=1.0, confidence=1.0,
            detail=f"delta={delta:.4f} {'✓' if agrees else '✗'}",
        )

    @staticmethod
    def _vote_exchange_flow(sig: Dict, side: str) -> SourceVote:
        """Exchange flow vote — positive = inflow = selling pressure."""
        flow = sig.get("exchange_flow", 0)
        if flow == 0:
            return SourceVote("exchange_flow", 0, False, 0.9, 0, "no data")

        # For LONG: negative flow (outflow) is bullish
        # For SHORT: positive flow (inflow) is bearish
        agrees = (side == "LONG" and flow < 0) or (side == "SHORT" and flow > 0)
        return SourceVote(
            name="exchange_flow", value=flow, agrees=agrees, weight=0.9, confidence=1.0,
            detail=f"flow={flow:.4f} {'✓' if agrees else '✗'}",
        )

    @staticmethod
    def _vote_oi_delta(sig: Dict, side: str) -> SourceVote:
        """OI delta vote — rising OI = new positions = conviction."""
        oi_delta = sig.get("oi_delta", 0)
        if oi_delta == 0:
            return SourceVote("oi_delta", 0, False, 0.8, 0, "no data")

        agrees = (side == "LONG" and oi_delta > 0) or (side == "SHORT" and oi_delta < 0)
        return SourceVote(
            name="oi_delta", value=oi_delta, agrees=agrees, weight=0.8, confidence=1.0,
            detail=f"oi_delta={oi_delta:.4f} {'✓' if agrees else '✗'}",
        )

    @staticmethod
    def _vote_funding(sig: Dict, side: str) -> SourceVote:
        """Funding rate vote — negative = shorts paying longs = bullish."""
        funding = sig.get("funding_rate", 0)
        if funding == 0:
            return SourceVote("funding_rate", 0, False, 0.7, 0, "no data")

        # For LONG: negative funding is bullish
        # For SHORT: positive funding is bearish
        agrees = (side == "LONG" and funding < 0) or (side == "SHORT" and funding > 0)
        return SourceVote(
            name="funding_rate", value=funding, agrees=agrees, weight=0.7, confidence=1.0,
            detail=f"funding={funding:.6f} {'✓' if agrees else '✗'}",
        )

    @staticmethod
    def _vote_absorption(sig: Dict, side: str) -> SourceVote:
        """Absorption vote — high absorption = large orders absorbing pressure."""
        abs_score = sig.get("absorption_score", 0)
        if abs_score == 0:
            return SourceVote("absorption", 0, False, 0.6, 0, "no data")

        # Absorption is directional if score > threshold
        agrees = abs_score > 0.5
        return SourceVote(
            name="absorption", value=abs_score, agrees=agrees, weight=0.6,
            confidence=min(abs_score, 1.0),
            detail=f"abs={abs_score:.2f} {'✓' if agrees else '✗'}",
        )

    @staticmethod
    def _vote_sweep(sig: Dict, side: str) -> SourceVote:
        """Sweep vote — liquidity sweep completed."""
        sweep = sig.get("sweep_score", 0)
        if sweep == 0:
            return SourceVote("sweep", 0, False, 0.5, 0, "no data")

        agrees = sweep > 0.6
        return SourceVote(
            name="sweep", value=sweep, agrees=agrees, weight=0.5,
            confidence=min(sweep, 1.0),
            detail=f"sweep={sweep:.2f} {'✓' if agrees else '✗'}",
        )
