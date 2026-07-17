"""
Trade Quality Engine — 12-dimension signal scoring using ONLY Live Sheet data.

READ-ONLY with respect to upstream data. Never modifies signals, only scores them.

Dimensions (weighted composite 0-100):
    1. Trend Quality       (12%) — regime strength, MTF alignment
    2. Momentum            (10%) — CVD, delta velocity, price momentum
    3. Confidence           (10%) — raw confidence from scanner
    4. Risk                 (10%) — stop distance, max adverse excursion potential
    5. Reward               (12%) — R:R ratio, projected move vs risk
    6. Liquidity            (8%)  — volume, spread, depth
    7. Institution Alignment (12%) — agreement among OI, funding, flow, CVD, delta
    8. Expected Move         (8%)  — ATR-based expected excursion
    9. Volatility            (5%)  — regime-appropriate volatility level
    10. Signal Freshness     (5%)  — age of signal data
    11. Market Structure     (4%)  — MSS, FVG, sweep quality
    12. Session Quality      (4%)  — active session vs off-hours

Output: TradeQualityScore (0-100) + per-dimension breakdown.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# DIMENSION WEIGHTS (must sum to 1.0)
# ═══════════════════════════════════════════════════════════════

WEIGHTS = {
    "trend":            0.12,
    "momentum":         0.10,
    "confidence":       0.10,
    "risk":             0.10,
    "reward":           0.12,
    "liquidity":        0.08,
    "institution":      0.12,
    "expected_move":    0.08,
    "volatility":       0.05,
    "freshness":        0.05,
    "market_structure": 0.04,
    "session":          0.04,
}

# Priority thresholds
THRESHOLD_ELITE = 85
THRESHOLD_HIGH = 75
THRESHOLD_MEDIUM = 60
THRESHOLD_LOW = 45
# Below THRESHOLD_LOW → REJECT


@dataclass
class DimensionScore:
    """Score for a single dimension."""
    name: str
    raw_value: float          # The raw input value
    score: float              # Normalized 0-100
    weight: float             # Weight in composite
    weighted: float           # score * weight
    detail: str = ""          # Human-readable explanation


@dataclass
class TradeQualityScore:
    """Complete quality assessment for a single signal."""
    symbol: str = ""
    side: str = ""
    composite_score: float = 0.0
    priority: str = "REJECT"  # ELITE / HIGH / MEDIUM / LOW / REJECT
    dimensions: List[DimensionScore] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "composite_score": round(self.composite_score, 2),
            "priority": self.priority,
            "dimensions": {
                d.name: {
                    "score": round(d.score, 1),
                    "weighted": round(d.weighted, 2),
                    "detail": d.detail,
                }
                for d in self.dimensions
            },
            "timestamp": self.timestamp,
        }


class TradeQualityEngine:
    """
    Scores every signal on 12 dimensions using only Live Sheet data.

    READ-ONLY: never modifies upstream signals or market data.
    """

    def __init__(self) -> None:
        self._scores: Dict[str, TradeQualityScore] = {}

    # ── Public API ───────────────────────────────────────────────

    def score_signal(self, signal: Dict[str, Any], market_data: Optional[Dict] = None) -> TradeQualityScore:
        """
        Score a single signal across all 12 dimensions.

        Args:
            signal: Live Sheet signal dict with all available fields
            market_data: Optional market data dict for the symbol

        Returns:
            TradeQualityScore with composite and per-dimension breakdown
        """
        md = market_data or {}
        ts = TradeQualityScore(
            symbol=signal.get("symbol", ""),
            side=signal.get("side", ""),
            timestamp=time.time(),
        )

        # ── Dimension 1: Trend Quality ──
        ts.dimensions.append(self._score_trend(signal, md))

        # ── Dimension 2: Momentum ──
        ts.dimensions.append(self._score_momentum(signal, md))

        # ── Dimension 3: Confidence ──
        ts.dimensions.append(self._score_confidence(signal))

        # ── Dimension 4: Risk ──
        ts.dimensions.append(self._score_risk(signal))

        # ── Dimension 5: Reward ──
        ts.dimensions.append(self._score_reward(signal))

        # ── Dimension 6: Liquidity ──
        ts.dimensions.append(self._score_liquidity(signal, md))

        # ── Dimension 7: Institution Alignment ──
        ts.dimensions.append(self._score_institution(signal))

        # ── Dimension 8: Expected Move ──
        ts.dimensions.append(self._score_expected_move(signal, md))

        # ── Dimension 9: Volatility ──
        ts.dimensions.append(self._score_volatility(signal, md))

        # ── Dimension 10: Signal Freshness ──
        ts.dimensions.append(self._score_freshness(signal))

        # ── Dimension 11: Market Structure ──
        ts.dimensions.append(self._score_market_structure(signal))

        # ── Dimension 12: Session Quality ──
        ts.dimensions.append(self._score_session(signal))

        # ── Composite ──
        ts.composite_score = sum(d.weighted for d in ts.dimensions)
        ts.priority = self._classify_priority(ts.composite_score)

        # ── Store ──
        key = f"{ts.symbol}_{ts.side}"
        self._scores[key] = ts

        logger.debug(
            "TQ: {} {} → {:.1f} [{}] (trend={:.0f} inst={:.0f} reward={:.0f})",
            ts.symbol, ts.side, ts.composite_score, ts.priority,
            ts.dimensions[0].score, ts.dimensions[6].score, ts.dimensions[4].score,
        )

        return ts

    def get_score(self, symbol: str, side: str) -> Optional[TradeQualityScore]:
        """Retrieve cached score for a symbol."""
        return self._scores.get(f"{symbol}_{side}")

    def get_all_scores(self) -> Dict[str, TradeQualityScore]:
        """Get all cached scores."""
        return dict(self._scores)

    # ── Dimension Scorers ────────────────────────────────────────

    def _score_trend(self, sig: Dict, md: Dict) -> DimensionScore:
        """Trend quality from regime + MTF alignment."""
        regime = sig.get("regime", sig.get("market_regime", "unknown"))
        mtf = sig.get("mtf_alignment", 0)
        inst_score = sig.get("institutional_score", 0)

        # Regime score
        regime_scores = {
            "trending_bull": 85, "trending_bear": 85,
            "volatile": 60, "range": 30,
            "breakout": 70, "compression": 25, "unknown": 40,
        }
        r_score = regime_scores.get(regime, 40)

        # MTF alignment (0-5 scale → 0-100)
        mtf_score = min(mtf / 5.0 * 100, 100) if mtf else 0

        # Combined
        score = r_score * 0.6 + mtf_score * 0.4
        detail = f"regime={regime}({r_score:.0f}) mtf={mtf}({mtf_score:.0f})"

        return DimensionScore(
            name="trend", raw_value=r_score, score=score,
            weight=WEIGHTS["trend"], weighted=score * WEIGHTS["trend"],
            detail=detail,
        )

    def _score_momentum(self, sig: Dict, md: Dict) -> DimensionScore:
        """Momentum from CVD, delta, and price movement."""
        cvd = sig.get("cvd", 0)
        delta = sig.get("delta", 0)
        side = sig.get("side", "")

        # CVD alignment with trade direction
        if side == "LONG":
            cvd_aligned = cvd > 0
            delta_aligned = delta > 0
        else:
            cvd_aligned = cvd < 0
            delta_aligned = delta < 0

        score = 50.0  # baseline
        if cvd_aligned:
            score += 20
        if delta_aligned:
            score += 15
        if cvd_aligned and delta_aligned:
            score += 15  # bonus for both aligned

        score = min(score, 100)
        detail = f"cvd={'aligned' if cvd_aligned else 'anti'} delta={'aligned' if delta_aligned else 'anti'}"

        return DimensionScore(
            name="momentum", raw_value=cvd, score=score,
            weight=WEIGHTS["momentum"], weighted=score * WEIGHTS["momentum"],
            detail=detail,
        )

    def _score_confidence(self, sig: Dict) -> DimensionScore:
        """Raw confidence from scanner — but with diminishing returns above 95%."""
        raw_conf = sig.get("confidence", 0)
        # Normalize to 0-100
        conf = raw_conf * 100 if raw_conf <= 1.0 else raw_conf

        # Diminishing returns above 95 — 95 and 99 shouldn't be drastically different
        if conf >= 95:
            score = 90 + (conf - 95) * 2  # 95→90, 100→100
        elif conf >= 85:
            score = 70 + (conf - 85) * 2  # 85→70, 95→90
        elif conf >= 75:
            score = 50 + (conf - 75) * 2  # 75→50, 85→70
        else:
            score = max(conf * 0.67, 0)  # linear below 75

        detail = f"raw={conf:.1f}%"
        return DimensionScore(
            name="confidence", raw_value=conf, score=score,
            weight=WEIGHTS["confidence"], weighted=score * WEIGHTS["confidence"],
            detail=detail,
        )

    def _score_risk(self, sig: Dict) -> DimensionScore:
        """Risk quality — stop loss distance and structure."""
        entry = sig.get("entry_price", sig.get("entry", 0))
        sl = sig.get("stop_loss", 0)

        if entry <= 0 or sl <= 0:
            return DimensionScore(
                name="risk", raw_value=0, score=20,
                weight=WEIGHTS["risk"], weighted=20 * WEIGHTS["risk"],
                detail="no SL defined",
            )

        sl_distance_pct = abs(entry - sl) / entry * 100

        # Optimal SL distance: 0.5-2.0%
        if 0.5 <= sl_distance_pct <= 2.0:
            score = 80 + (1.0 - abs(sl_distance_pct - 1.0)) * 20
        elif 0.3 <= sl_distance_pct < 0.5:
            score = 60  # tight but possible
        elif 2.0 < sl_distance_pct <= 3.0:
            score = 50  # wide
        elif sl_distance_pct < 0.3:
            score = 30  # too tight — noise stops
        else:
            score = 20  # very wide — poor R:R

        detail = f"sl_dist={sl_distance_pct:.2f}%"
        return DimensionScore(
            name="risk", raw_value=sl_distance_pct, score=score,
            weight=WEIGHTS["risk"], weighted=score * WEIGHTS["risk"],
            detail=detail,
        )

    def _score_reward(self, sig: Dict) -> DimensionScore:
        """Reward quality — R:R ratio."""
        rr = sig.get("risk_reward", 0)

        # R:R scoring: 2.5+ is excellent, <1.5 is poor
        if rr >= 3.5:
            score = 100
        elif rr >= 2.5:
            score = 85 + (rr - 2.5) * 15
        elif rr >= 2.0:
            score = 70 + (rr - 2.0) * 30
        elif rr >= 1.5:
            score = 45 + (rr - 1.5) * 50
        elif rr >= 1.0:
            score = 25 + (rr - 1.0) * 40
        else:
            score = max(rr * 25, 0)

        detail = f"rr={rr:.2f}"
        return DimensionScore(
            name="reward", raw_value=rr, score=score,
            weight=WEIGHTS["reward"], weighted=score * WEIGHTS["reward"],
            detail=detail,
        )

    def _score_liquidity(self, sig: Dict, md: Dict) -> DimensionScore:
        """Liquidity quality from volume, spread, absorption."""
        abs_score = sig.get("absorption_score", 0)
        sweep = sig.get("sweep_score", 0)

        # Base from absorption (higher = more liquidity available)
        score = 50.0
        if abs_score > 0.7:
            score += 25
        elif abs_score > 0.4:
            score += 10

        if sweep > 0.6:
            score += 15  # sweep = liquidity grabbed

        score = min(score, 100)
        detail = f"abs={abs_score:.2f} sweep={sweep:.2f}"
        return DimensionScore(
            name="liquidity", raw_value=abs_score, score=score,
            weight=WEIGHTS["liquidity"], weighted=score * WEIGHTS["liquidity"],
            detail=detail,
        )

    def _score_institution(self, sig: Dict) -> DimensionScore:
        """
        Institution alignment — agreement among Flow, CVD, Delta, OI, Funding.

        This is the KEY dimension per the Master Directive.
        Strong agreement = high score. Disagreement = reject.
        """
        cvd = sig.get("cvd", 0)
        delta = sig.get("delta", 0)
        oi = sig.get("open_interest", 0)
        oi_delta = sig.get("oi_delta", 0)
        funding = sig.get("funding_rate", 0)
        exchange_flow = sig.get("exchange_flow", 0)
        inst_score = sig.get("institutional_score", 0)
        side = sig.get("side", "")

        # Determine bullish vs bearish signals
        bullish_votes = 0
        bearish_votes = 0
        total_sources = 0

        # CVD
        if cvd != 0:
            total_sources += 1
            if (side == "LONG" and cvd > 0) or (side == "SHORT" and cvd < 0):
                bullish_votes += 1
            else:
                bearish_votes += 1

        # Delta
        if delta != 0:
            total_sources += 1
            if (side == "LONG" and delta > 0) or (side == "SHORT" and delta < 0):
                bullish_votes += 1
            else:
                bearish_votes += 1

        # OI Delta (rising OI = new positions = directional conviction)
        if oi_delta != 0:
            total_sources += 1
            if (side == "LONG" and oi_delta > 0) or (side == "SHORT" and oi_delta < 0):
                bullish_votes += 1
            else:
                bearish_votes += 1

        # Funding rate (negative funding = shorts paying longs = bullish)
        if funding != 0:
            total_sources += 1
            if (side == "LONG" and funding < 0) or (side == "SHORT" and funding > 0):
                bullish_votes += 1
            else:
                bearish_votes += 1

        # Exchange flow (positive = inflow to exchange = selling pressure)
        if exchange_flow != 0:
            total_sources += 1
            if (side == "LONG" and exchange_flow < 0) or (side == "SHORT" and exchange_flow > 0):
                bullish_votes += 1
            else:
                bearish_votes += 1

        # Calculate agreement
        aligned = 0
        agreement = 0.0
        if total_sources == 0:
            score = 30  # no data = low confidence
        else:
            aligned = bullish_votes if side == "LONG" else bearish_votes
            agreement = aligned / total_sources
            score = agreement * 100

        # Bonus for institutional_score from scanner
        if inst_score >= 85:
            score = min(score + 10, 100)

        detail = f"agree={agreement:.0%} ({aligned}/{total_sources}) inst={inst_score:.0f}"
        return DimensionScore(
            name="institution", raw_value=agreement, score=score,
            weight=WEIGHTS["institution"], weighted=score * WEIGHTS["institution"],
            detail=detail,
        )

    def _score_expected_move(self, sig: Dict, md: Dict) -> DimensionScore:
        """Expected move based on ATR or volatility."""
        entry = sig.get("entry_price", sig.get("entry", 0))
        tp = sig.get("take_profit", 0)
        sl = sig.get("stop_loss", 0)

        if entry <= 0 or tp <= 0 or sl <= 0:
            return DimensionScore(
                name="expected_move", raw_value=0, score=30,
                weight=WEIGHTS["expected_move"], weighted=30 * WEIGHTS["expected_move"],
                detail="no TP/SL",
            )

        # Expected move = TP distance as % of entry
        expected_pct = abs(tp - entry) / entry * 100
        risk_pct = abs(entry - sl) / entry * 100

        # Good if expected move > 1.5% for crypto
        if expected_pct >= 2.0:
            score = 90
        elif expected_pct >= 1.5:
            score = 75
        elif expected_pct >= 1.0:
            score = 60
        elif expected_pct >= 0.5:
            score = 40
        else:
            score = 20

        detail = f"exp_move={expected_pct:.2f}% risk={risk_pct:.2f}%"
        return DimensionScore(
            name="expected_move", raw_value=expected_pct, score=score,
            weight=WEIGHTS["expected_move"], weighted=score * WEIGHTS["expected_move"],
            detail=detail,
        )

    def _score_volatility(self, sig: Dict, md: Dict) -> DimensionScore:
        """Volatility quality — not too low, not extreme."""
        regime = sig.get("regime", sig.get("market_regime", "unknown"))
        score = 50  # default

        # Appropriate volatility for regime
        if regime in ("trending_bull", "trending_bear"):
            score = 80  # trends need volatility
        elif regime == "volatile":
            score = 70  # high vol = opportunity but risk
        elif regime == "range":
            score = 35  # low vol = poor for trend following
        elif regime == "compression":
            score = 25  # very low vol
        elif regime == "breakout":
            score = 75  # breakout = vol expansion

        detail = f"regime={regime}"
        return DimensionScore(
            name="volatility", raw_value=0, score=score,
            weight=WEIGHTS["volatility"], weighted=score * WEIGHTS["volatility"],
            detail=detail,
        )

    def _score_freshness(self, sig: Dict) -> DimensionScore:
        """Signal freshness — how recent is the data."""
        ts = sig.get("timestamp", 0)
        now = time.time()

        if ts <= 0:
            age_sec = 999
        else:
            age_sec = now - ts

        # Fresh = <30s, stale = >5min
        if age_sec <= 30:
            score = 100
        elif age_sec <= 60:
            score = 85
        elif age_sec <= 180:
            score = 60
        elif age_sec <= 300:
            score = 35
        else:
            score = 10

        detail = f"age={age_sec:.0f}s"
        return DimensionScore(
            name="freshness", raw_value=age_sec, score=score,
            weight=WEIGHTS["freshness"], weighted=score * WEIGHTS["freshness"],
            detail=detail,
        )

    def _score_market_structure(self, sig: Dict) -> DimensionScore:
        """Market structure quality — MSS, FVG, sweep."""
        mss = sig.get("mss_score", 0)
        fvg = sig.get("fvg_score", 0)
        sweep = sig.get("sweep_score", 0)

        # Combine structural signals
        score = 40  # baseline — no structure
        if mss > 0.7:
            score += 25  # strong MSS
        elif mss > 0.4:
            score += 10

        if fvg > 0.6:
            score += 20  # FVG present
        elif fvg > 0.3:
            score += 8

        if sweep > 0.7:
            score += 15  # sweep completed

        score = min(score, 100)
        detail = f"mss={mss:.2f} fvg={fvg:.2f} sweep={sweep:.2f}"
        return DimensionScore(
            name="market_structure", raw_value=mss, score=score,
            weight=WEIGHTS["market_structure"], weighted=score * WEIGHTS["market_structure"],
            detail=detail,
        )

    def _score_session(self, sig: Dict) -> DimensionScore:
        """Session quality — active trading hours vs off-hours."""
        ts = sig.get("timestamp", time.time())
        from datetime import datetime, timezone
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour

        # London-NY overlap is best (13:00-16:00 UTC)
        if 13 <= hour < 16:
            score = 95  # London-NY overlap
        elif 7 <= hour < 13:
            score = 75  # London session
        elif 16 <= hour < 21:
            score = 70  # NY session
        elif 0 <= hour < 3:
            score = 55  # Asia open
        else:
            score = 35  # off-hours

        detail = f"hour_utc={hour}"
        return DimensionScore(
            name="session", raw_value=hour, score=score,
            weight=WEIGHTS["session"], weighted=score * WEIGHTS["session"],
            detail=detail,
        )

    # ── Classification ───────────────────────────────────────────

    @staticmethod
    def _classify_priority(composite: float) -> str:
        """Classify composite score into priority bucket."""
        if composite >= THRESHOLD_ELITE:
            return "ELITE"
        elif composite >= THRESHOLD_HIGH:
            return "HIGH"
        elif composite >= THRESHOLD_MEDIUM:
            return "MEDIUM"
        elif composite >= THRESHOLD_LOW:
            return "LOW"
        else:
            return "REJECT"
