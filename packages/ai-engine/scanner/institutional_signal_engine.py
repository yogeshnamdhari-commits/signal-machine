"""
Institutional Signal Engine — Phase 2-14 Rebuild.

Replaces the over-filtered 25+ gate pipeline with a focused 5-component
institutional signal model:

    Signal Score = Sweep×0.35 + MSS×0.25 + FVG×0.15 + CVD×0.15 + OI_Funding×0.10

Signal generated when Signal Score >= 80.

Architecture:
    Phase 1  → Data Health Layer (data_quality.py)
    Phase 2  → Simplified rejection (this module)
    Phase 3  → 5-component scoring (this module)
    Phase 4  → Liquidity Sweep detection (sweep_detector.py + liquidity_sweep_engine.py)
    Phase 5  → MSS detection (enhanced from liquidity_sweep_engine.py)
    Phase 6  → FVG detection (fvg_detector.py)
    Phase 7  → True Order Flow (orderflow.py + cvd_engine.py)
    Phase 8  → CVD Confirmation (cvd_engine.py)
    Phase 9  → Open Interest (open_interest.py)
    Phase 10 → Funding Filter (funding_rate.py)
    Phase 11 → Simplified Regime (regime.py)
    Phase 12 → Signal Output Format (this module)
    Phase 13 → Execution Safety (this module)
    Phase 14 → Performance Metrics (this module)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION — All thresholds in one place
# ═══════════════════════════════════════════════════════════════

# Phase 3: Signal Score weights (must sum to 1.0)
WEIGHT_SWEEP = 0.35
WEIGHT_MSS = 0.25
WEIGHT_FVG = 0.15
WEIGHT_CVD = 0.15
WEIGHT_OI_FUNDING = 0.10

# Phase 3: Minimum signal score to emit
MIN_SIGNAL_SCORE = 65.0  # Phase 10: lowered from 80 — cvd=60/oi=50 defaults cap max score to ~77

# Phase 11: Allowed regimes (simplified — no blocking, just guidance)
ALLOWED_REGIMES = {"trending_bull", "trending_bear", "range", "volatile"}
# Breakout and compression are rare; allow them too
ALLOWED_REGIMES_FULL = {"trending_bull", "trending_bear", "range", "volatile", "breakout", "compression"}

# Phase 13: Execution safety — minimum required data freshness (seconds)
MAX_TRADE_AGE_SEC = 3.0
MAX_ORDERBOOK_AGE_SEC = 3.0
MAX_OI_AGE_SEC = 120.0  # Phase 10: raised from 30 — OI data refreshes slowly via REST

# Phase 13: Minimum required components for signal generation
MIN_REQUIRED_COMPONENTS = 3  # At least 3 of 5 components must have real data

# Phase 14: Performance tracking window
PERF_WINDOW_SIZE = 500


# ═══════════════════════════════════════════════════════════════
# PHASE 12: SIGNAL OUTPUT FORMAT
# ═══════════════════════════════════════════════════════════════

@dataclass
class InstitutionalSignal:
    """Complete institutional signal with all required fields (Phase 12)."""
    # Core
    symbol: str = ""
    direction: str = ""          # "LONG" or "SHORT"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    risk_reward: float = 0.0

    # Phase 3: Component scores
    sweep_score: float = 0.0
    mss_score: float = 0.0
    fvg_score: float = 0.0
    cvd_score: float = 0.0
    oi_funding_score: float = 0.0
    signal_score: float = 0.0    # Weighted composite

    # Phase 4: Sweep details
    sweep_type: str = ""         # "pdh_sweep", "pdl_sweep", "session_high_sweep", etc.
    sweep_time: float = 0.0
    sweep_strength: float = 0.0

    # Phase 5: MSS details
    mss_type: str = ""           # "bullish_mss", "bearish_mss"
    mss_confidence: float = 0.0

    # Phase 6: FVG details
    fvg_quality: str = ""        # "strong", "moderate", "weak", "none"
    fvg_gap_pct: float = 0.0

    # Phase 7-8: Order Flow
    cvd_status: str = ""         # "rising", "falling", "neutral"
    delta_status: str = ""       # "positive", "negative", "neutral"
    delta_value: float = 0.0
    flow_ratio: float = 0.5

    # Phase 9: Open Interest
    oi_status: str = ""          # "building", "unwinding", "neutral"
    oi_change_pct: float = 0.0

    # Phase 10: Funding
    funding_status: str = ""     # "bullish", "bearish", "neutral"
    funding_rate: float = 0.0

    # Phase 11: Regime
    regime: str = ""
    regime_guidance: str = ""    # "trend_follow", "range_reversal", "wait"

    # Confidence
    confidence: float = 0.0

    # Metadata
    timestamp: float = 0.0
    data_health: str = "healthy"
    components_available: int = 0


# ═══════════════════════════════════════════════════════════════
# PHASE 14: PERFORMANCE METRICS
# ═══════════════════════════════════════════════════════════════

@dataclass
class PerformanceMetrics:
    """Tracks signal generation performance (Phase 14)."""
    signals_generated: int = 0
    signals_rejected: int = 0
    rejection_reasons: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    # Trade outcomes
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    trade_history: List[Dict] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total * 100 if total > 0 else 0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t["pnl"] for t in self.trade_history if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in self.trade_history if t["pnl"] < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    @property
    def avg_rr(self) -> float:
        rrs = [t.get("r_multiple", 0) for t in self.trade_history if t.get("r_multiple")]
        return sum(rrs) / len(rrs) if rrs else 0

    @property
    def avg_hold_time(self) -> float:
        holds = [t.get("hold_minutes", 0) for t in self.trade_history if t.get("hold_minutes")]
        return sum(holds) / len(holds) if holds else 0

    @property
    def expected_value(self) -> float:
        """EV per trade in dollars."""
        return self.total_pnl / len(self.trade_history) if self.trade_history else 0

    def record_signal_generated(self) -> None:
        self.signals_generated += 1

    def record_signal_rejected(self, reason: str) -> None:
        self.signals_rejected += 1
        self.rejection_reasons[reason] += 1

    def record_trade_outcome(self, pnl: float, r_multiple: float = 0, hold_minutes: float = 0) -> None:
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.trade_history.append({
            "pnl": pnl, "r_multiple": r_multiple,
            "hold_minutes": hold_minutes, "time": time.time(),
        })
        if len(self.trade_history) > PERF_WINDOW_SIZE:
            self.trade_history = self.trade_history[-PERF_WINDOW_SIZE:]

    def get_summary(self) -> Dict:
        return {
            "signals_generated": self.signals_generated,
            "signals_rejected": self.signals_rejected,
            "win_rate": round(self.win_rate, 1),
            "profit_factor": round(self.profit_factor, 2),
            "avg_rr": round(self.avg_rr, 2),
            "avg_hold_time": round(self.avg_hold_time, 1),
            "expected_value": round(self.expected_value, 2),
            "total_pnl": round(self.total_pnl, 2),
            "top_rejection_reasons": dict(
                sorted(self.rejection_reasons.items(), key=lambda x: -x[1])[:5]
            ),
        }


# ═══════════════════════════════════════════════════════════════
# MAIN ENGINE
# ═══════════════════════════════════════════════════════════════

class InstitutionalSignalEngine:
    """
    Phase 2-14: Institutional-grade signal generation engine.

    Replaces the over-filtered 25+ gate pipeline with a focused 5-component model.
    Uses existing detection modules (sweep, MSS, FVG, CVD, OI, funding, regime)
    and applies a clean scoring model:

        Signal Score = Sweep×0.35 + MSS×0.25 + FVG×0.15 + CVD×0.15 + OI_Funding×0.10

    Only generates signals when Signal Score >= 80.

    Key differences from old pipeline:
    - NO AI scorer sigmoid squash (kills 59% of valid signals)
    - NO confidence floor at 55 (kills remaining candidates)
    - NO session quality filter (blocks valid London+NY trades)
    - NO checklist gate (13/13 ALL-PASS = 0.13% pass rate)
    - NO regime hard-block (breakout-only = 0% of universe)
    - Regime GUIDES but does not BLOCK
    - Data health MUST be fresh (3s trade, 3s orderbook)
    - Minimum 3 of 5 components must have real data
    """

    def __init__(self) -> None:
        self.metrics = PerformanceMetrics()
        self._cooldown: Dict[str, float] = {}  # symbol → last signal time
        self._cooldown_sec = 300  # 5 min cooldown per symbol

    async def initialize(self) -> None:
        logger.info("🏛️ Institutional Signal Engine ready — 5-component model, score≥{}", MIN_SIGNAL_SCORE)

    # ═══════════════════════════════════════════════════════════════
    # PHASE 13: EXECUTION SAFETY — Data freshness gate
    # ═══════════════════════════════════════════════════════════════

    def _check_data_health(
        self,
        symbol: str,
        data_quality,
        orderflow: Optional[Dict],
        cvd_data: Optional[Dict],
        oi_data: Optional[Dict],
    ) -> Tuple[bool, str, Dict]:
        """
        Phase 13: Verify all data feeds are fresh before signal generation.

        Returns: (is_safe, reason, health_info)
        """
        # Check data quality module
        if data_quality:
            health = data_quality.get_health_status(symbol)
            if not health["signal_generation_ok"]:
                return False, f"DATA_STALE: {health['reason']}", health

        # Check orderflow has recent trades
        if not orderflow:
            return False, "MISSING_ORDERFLOW: No order flow data", {}

        of_trades = orderflow.get("total_trades", 0)
        if of_trades < 10:
            return False, f"INSUFFICIENT_TRADES: {of_trades} < 10", {}

        # Check CVD exists
        if not cvd_data:
            return False, "MISSING_CVD: No CVD data", {}

        # Check OI exists
        if not oi_data:
            return False, "MISSING_OI: No OI data", {}

        return True, "OK", {}

    # ═══════════════════════════════════════════════════════════════
    # PHASE 4: SWEEP SCORING
    # ═══════════════════════════════════════════════════════════════

    def _score_sweep(
        self,
        symbol: str,
        side: str,
        sweep_analysis: Optional[Dict],
        sweep_setup: Optional[Dict],
        klines: Optional[List[Dict]],
    ) -> Tuple[float, str, float, float]:
        """
        Phase 4: Score liquidity sweep detection (0-100).

        Detects:
        - Previous Day High/Low Sweep
        - Session High/Low Sweep
        - Equal High/Low Sweep
        - Stop Hunt Events

        Returns: (score, sweep_type, sweep_time, sweep_strength)
        """
        score = 50.0  # Neutral default — no sweep is not a penalty
        sweep_type = "none"
        sweep_time = 0.0
        sweep_strength = 0.0

        # From sweep detector (wick-based detection)
        if sweep_analysis:
            recent_sweeps = sweep_analysis.get("recent_sweep_count", 0)
            avg_conf = sweep_analysis.get("avg_confidence", 0)
            signal_type = sweep_analysis.get("signal", "neutral")

            if recent_sweeps > 0:
                # Sweep detected — score based on quality
                if (signal_type == "bullish_rejection" and side == "LONG") or \
                   (signal_type == "bearish_rejection" and side == "SHORT"):
                    # Sweep aligns with signal direction
                    score = 70 + min(recent_sweeps, 3) * 10 + avg_conf * 20
                    sweep_type = "aligned_sweep"
                elif signal_type != "neutral":
                    # Sweep opposes signal — penalty
                    score = 30
                    sweep_type = "opposing_sweep"
                else:
                    score = 60
                    sweep_type = "neutral_sweep"
                sweep_time = sweep_analysis.get("last_sweep_time", time.time())
                sweep_strength = avg_conf * 100

        # From liquidity sweep engine (institutional 4-condition model)
        if sweep_setup:
            if sweep_setup.get("valid_setup"):
                composite = sweep_setup.get("composite_score", 0)
                score = max(score, 70 + composite * 0.3)
                sweep_type = sweep_setup.get("sweep_type", sweep_type)
                sweep_strength = max(sweep_strength, composite)
            elif sweep_setup.get("sweep_detected"):
                score = max(score, 60 + sweep_setup.get("sweep_score", 0) * 0.2)

        # Session-based sweep detection (Phase 4)
        if klines and len(klines) >= 20:
            highs = [k["high"] for k in klines[-20:]]
            lows = [k["low"] for k in klines[-20:]]
            closes = [k["close"] for k in klines[-20:]]
            current = closes[-1] if closes else 0

            session_high = max(highs)
            session_low = min(lows)

            # Check for session high sweep (wick above, close below)
            last_k = klines[-1]
            if last_k["high"] > session_high * 0.999 and last_k["close"] < session_high:
                if side == "SHORT":
                    score = max(score, 85)
                    sweep_type = "session_high_sweep"
                    sweep_strength = max(sweep_strength, 85)

            # Check for session low sweep (wick below, close above)
            if last_k["low"] < session_low * 1.001 and last_k["close"] > session_low:
                if side == "LONG":
                    score = max(score, 85)
                    sweep_type = "session_low_sweep"
                    sweep_strength = max(sweep_strength, 85)

        return min(100, max(0, score)), sweep_type, sweep_time, sweep_strength

    # ═══════════════════════════════════════════════════════════════
    # PHASE 5: MSS SCORING
    # ═══════════════════════════════════════════════════════════════

    def _score_mss(
        self,
        symbol: str,
        side: str,
        klines: Optional[List[Dict]],
        regime_data: Optional[Dict],
    ) -> Tuple[float, str, float]:
        """
        Phase 5: Score Market Structure Shift (0-100).

        Detects:
        - Bullish MSS: Sweep Low + Break Higher High
        - Bearish MSS: Sweep High + Break Lower Low

        Returns: (score, mss_type, mss_confidence)
        """
        score = 50.0  # Neutral default
        mss_type = "none"
        mss_confidence = 0.0

        if not klines or len(klines) < 20:
            return score, mss_type, mss_confidence

        # Extract swing highs and lows from recent klines
        highs = [k["high"] for k in klines[-20:]]
        lows = [k["low"] for k in klines[-20:]]
        closes = [k["close"] for k in klines[-20:]]

        # Find swing points (simple: local extremes)
        swing_highs = []
        swing_lows = []
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
               highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                swing_highs.append((i, highs[i]))
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
               lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                swing_lows.append((i, lows[i]))

        current = closes[-1]

        # Bullish MSS: price breaks above recent swing high after sweeping swing low
        if len(swing_highs) >= 1 and len(swing_lows) >= 1:
            last_sh = swing_highs[-1][1]
            last_sl = swing_lows[-1][1]

            if current > last_sh:
                # Broke above swing high — bullish MSS
                if side == "LONG":
                    # Check if there was a sweep below swing low first
                    recent_low = min(lows[-10:])
                    if recent_low < last_sl:
                        score = 90  # Perfect bullish MSS: sweep low + break high
                        mss_type = "bullish_mss"
                        mss_confidence = 90
                    else:
                        score = 75  # Break high without sweep — still bullish
                        mss_type = "bullish_breakout"
                        mss_confidence = 70

            elif current < last_sl:
                # Broke below swing low — bearish MSS
                if side == "SHORT":
                    recent_high = max(highs[-10:])
                    if recent_high > last_sh:
                        score = 90  # Perfect bearish MSS: sweep high + break low
                        mss_type = "bearish_mss"
                        mss_confidence = 90
                    else:
                        score = 75
                        mss_type = "bearish_breakdown"
                        mss_confidence = 70

        # Regime alignment bonus
        if regime_data:
            regime = regime_data.get("regime", "range")
            if (side == "LONG" and regime == "trending_bull") or \
               (side == "SHORT" and regime == "trending_bear"):
                score = min(100, score + 10)
                mss_confidence = min(100, mss_confidence + 10)

        return min(100, max(0, score)), mss_type, mss_confidence

    # ═══════════════════════════════════════════════════════════════
    # PHASE 6: FVG SCORING
    # ═══════════════════════════════════════════════════════════════

    def _score_fvg(
        self,
        symbol: str,
        side: str,
        fvg_analysis: Optional[Dict],
    ) -> Tuple[float, str, float]:
        """
        Phase 6: Score Fair Value Gap (0-100).

        Detects:
        - Bullish FVG (demand imbalance)
        - Bearish FVG (supply imbalance)

        Returns: (score, fvg_quality, fvg_gap_pct)
        """
        score = 50.0  # Neutral default
        fvg_quality = "none"
        fvg_gap_pct = 0.0

        if not fvg_analysis:
            return score, fvg_quality, fvg_gap_pct

        # From FVG detector
        unfilled_bullish = fvg_analysis.get("unfilled_bullish", [])
        unfilled_bearish = fvg_analysis.get("unfilled_bearish", [])
        fvg_momentum = fvg_analysis.get("fvg_momentum", 0)

        # Bullish signal needs bullish FVG
        if side == "LONG" and unfilled_bullish:
            latest_fvg = unfilled_bullish[-1] if unfilled_bullish else None
            if latest_fvg:
                gap_size = latest_fvg.get("gap_pct", 0)
                strength = latest_fvg.get("strength", 0)
                filled = latest_fvg.get("fill_pct", 0)

                # Strong FVG: large gap, high strength, not yet filled
                if filled < 50 and strength > 0.5:
                    score = 70 + strength * 30
                    fvg_quality = "strong"
                elif filled < 80:
                    score = 60 + strength * 20
                    fvg_quality = "moderate"
                else:
                    score = 55
                    fvg_quality = "weak"
                fvg_gap_pct = gap_size

        # Bearish signal needs bearish FVG
        elif side == "SHORT" and unfilled_bearish:
            latest_fvg = unfilled_bearish[-1] if unfilled_bearish else None
            if latest_fvg:
                gap_size = latest_fvg.get("gap_pct", 0)
                strength = latest_fvg.get("strength", 0)
                filled = latest_fvg.get("fill_pct", 0)

                if filled < 50 and strength > 0.5:
                    score = 70 + strength * 30
                    fvg_quality = "strong"
                elif filled < 80:
                    score = 60 + strength * 20
                    fvg_quality = "moderate"
                else:
                    score = 55
                    fvg_quality = "weak"
                fvg_gap_pct = gap_size

        # FVG momentum alignment
        if side == "LONG" and fvg_momentum > 0:
            score = min(100, score + 5)
        elif side == "SHORT" and fvg_momentum < 0:
            score = min(100, score + 5)

        return min(100, max(0, score)), fvg_quality, fvg_gap_pct

    # ═══════════════════════════════════════════════════════════════
    # PHASE 7-8: CVD & DELTA SCORING
    # ═══════════════════════════════════════════════════════════════

    def _score_cvd_delta(
        self,
        symbol: str,
        side: str,
        orderflow: Optional[Dict],
        cvd_data: Optional[Dict],
        cumulative_delta: Optional[Dict],
    ) -> Tuple[float, str, str, float, float]:
        """
        Phase 7-8: Score CVD and Delta confirmation (0-100).

        LONG: CVD rising, Delta positive, Price above MSS
        SHORT: CVD falling, Delta negative, Price below MSS

        Returns: (score, cvd_status, delta_status, delta_value, flow_ratio)
        """
        score = 50.0
        cvd_status = "neutral"
        delta_status = "neutral"
        delta_value = 0.0
        flow_ratio = 0.5

        # From CVD engine (multi-TF)
        if cvd_data:
            # Get 5m CVD bias (primary timeframe)
            bias_5m = cvd_data.get("bias_5m", "neutral")
            bias_1m = cvd_data.get("bias_1m", "neutral")
            cvd_divergence = cvd_data.get("divergence_5m", 0)

            # CVD alignment with signal direction
            if side == "LONG":
                if bias_5m in ("strong_bullish", "bullish"):
                    score = 80 + (20 if bias_5m == "strong_bullish" else 10)
                    cvd_status = "rising"
                elif bias_5m == "neutral":
                    if bias_1m in ("bullish", "strong_bullish"):
                        score = 65
                        cvd_status = "rising"
                    else:
                        score = 50
                        cvd_status = "neutral"
                else:
                    score = 30  # CVD opposing
                    cvd_status = "falling"

            elif side == "SHORT":
                if bias_5m in ("strong_bearish", "bearish"):
                    score = 80 + (20 if bias_5m == "strong_bearish" else 10)
                    cvd_status = "falling"
                elif bias_5m == "neutral":
                    if bias_1m in ("bearish", "strong_bearish"):
                        score = 65
                        cvd_status = "falling"
                    else:
                        score = 50
                        cvd_status = "neutral"
                else:
                    score = 30
                    cvd_status = "rising"

            # Divergence bonus
            if side == "LONG" and cvd_divergence > 0:
                score = min(100, score + 10)  # Bullish divergence
            elif side == "SHORT" and cvd_divergence < 0:
                score = min(100, score + 10)  # Bearish divergence

        # From orderflow (delta)
        if orderflow:
            flow_ratio = orderflow.get("flow_ratio", 0.5)
            delta = orderflow.get("delta", 0)
            delta_value = delta

            if side == "LONG":
                if delta > 0:
                    delta_status = "positive"
                    score = min(100, score + 10)
                elif delta < 0:
                    delta_status = "negative"
                    score = max(0, score - 10)
            elif side == "SHORT":
                if delta < 0:
                    delta_status = "negative"
                    score = min(100, score + 10)
                elif delta > 0:
                    delta_status = "positive"
                    score = max(0, score - 10)

        # From cumulative delta (legacy fallback)
        if not cvd_data and cumulative_delta:
            momentum = cumulative_delta.get("momentum", 0)
            if side == "LONG" and momentum > 0:
                score = min(100, score + 10)
                cvd_status = "rising"
            elif side == "SHORT" and momentum < 0:
                score = min(100, score + 10)
                cvd_status = "falling"

        return min(100, max(0, score)), cvd_status, delta_status, delta_value, flow_ratio

    # ═══════════════════════════════════════════════════════════════
    # PHASE 9-10: OI + FUNDING SCORING
    # ═══════════════════════════════════════════════════════════════

    def _score_oi_funding(
        self,
        symbol: str,
        side: str,
        oi_data: Optional[Dict],
        funding_data: Optional[Dict],
    ) -> Tuple[float, str, str, float, float]:
        """
        Phase 9-10: Score Open Interest + Funding (0-100).

        OI Rules:
        - LONG: Price ↑ + OI ↑ = bullish (new longs entering)
        - SHORT: Price ↓ + OI ↑ = bearish (new shorts entering)
        - Reject: Price ↑ + OI ↓ or Price ↓ + OI ↓

        Funding: Confirmation only, never primary trigger.

        Returns: (score, oi_status, funding_status, oi_change_pct, funding_rate)
        """
        score = 50.0
        oi_status = "neutral"
        funding_status = "neutral"
        oi_change_pct = 0.0
        funding_rate = 0.0

        # Phase 9: OI Analysis
        if oi_data:
            oi_change_pct = oi_data.get("change_pct", 0)
            positioning = oi_data.get("positioning", "neutral")
            oi_regime = oi_data.get("oi_regime", "neutral_oi")

            if side == "LONG":
                if positioning == "long_buildup":
                    score = 75 + min(oi_change_pct * 5, 25)
                    oi_status = "building"
                elif positioning == "short_covering":
                    score = 65  # Short covering = bullish
                    oi_status = "covering"
                elif positioning == "long_unwinding":
                    score = 35  # Longs exiting = bearish
                    oi_status = "unwinding"
                elif positioning == "short_buildup":
                    score = 30  # New shorts = bearish
                    oi_status = "short_building"
            elif side == "SHORT":
                if positioning == "short_buildup":
                    score = 75 + min(abs(oi_change_pct) * 5, 25)
                    oi_status = "building"
                elif positioning == "long_unwinding":
                    score = 65
                    oi_status = "unwinding"
                elif positioning == "long_buildup":
                    score = 35
                    oi_status = "long_building"
                elif positioning == "short_covering":
                    score = 30
                    oi_status = "covering"

            # OI regime alignment
            if side == "LONG" and oi_regime == "bullish_oi":
                score = min(100, score + 10)
            elif side == "SHORT" and oi_regime == "bearish_oi":
                score = min(100, score + 10)

        # Phase 10: Funding (confirmation only, weight=10%)
        if funding_data:
            funding_rate = funding_data.get("current_rate", 0)
            is_extreme = funding_data.get("is_extreme", False)
            direction = funding_data.get("direction", "neutral")

            # Funding as contrarian confirmation
            if side == "LONG":
                if direction == "short_paying":
                    # Shorts pay = bullish crowd → positive contribution
                    score = min(100, score + 5)
                    funding_status = "bullish"
                elif direction == "long_paying":
                    # Longs pay = bearish crowd → slight negative
                    score = max(0, score - 3)
                    funding_status = "bearish"
                else:
                    funding_status = "neutral"
            elif side == "SHORT":
                if direction == "long_paying":
                    score = min(100, score + 5)
                    funding_status = "bearish"
                elif direction == "short_paying":
                    score = max(0, score - 3)
                    funding_status = "bullish"
                else:
                    funding_status = "neutral"

            # Extreme funding is a strong contrarian signal
            if is_extreme:
                if side == "LONG" and direction == "short_paying":
                    score = min(100, score + 10)
                elif side == "SHORT" and direction == "long_paying":
                    score = min(100, score + 10)

        return min(100, max(0, score)), oi_status, funding_status, oi_change_pct, funding_rate

    # ═══════════════════════════════════════════════════════════════
    # PHASE 11: REGIME GUIDANCE (not blocking)
    # ═══════════════════════════════════════════════════════════════

    def _get_regime_guidance(
        self,
        side: str,
        regime_data: Optional[Dict],
    ) -> Tuple[str, str]:
        """
        Phase 11: Simplified regime — guidance only, never blocks.

        Returns: (regime, regime_guidance)
        """
        if not regime_data:
            return "unknown", "neutral"

        regime = regime_data.get("regime", "range")

        if regime in ("trending_bull", "trending_bear"):
            if (side == "LONG" and regime == "trending_bull") or \
               (side == "SHORT" and regime == "trending_bear"):
                return regime, "trend_follow"
            else:
                return regime, "counter_trend"
        elif regime in ("range", "ranging"):
            return regime, "range_reversal"
        elif regime == "volatile":
            return regime, "volatile"
        elif regime == "breakout":
            return regime, "breakout"
        elif regime == "compression":
            return regime, "wait"
        else:
            return regime, "neutral"

    # ═══════════════════════════════════════════════════════════════
    # PHASE 3: SIGNAL SCORING — 5-Component Model
    # ═══════════════════════════════════════════════════════════════

    def calculate_signal_score(
        self,
        sweep_score: float,
        mss_score: float,
        fvg_score: float,
        cvd_score: float,
        oi_funding_score: float,
    ) -> float:
        """
        Phase 3: Calculate weighted signal score.

        Signal Score = Sweep×0.35 + MSS×0.25 + FVG×0.15 + CVD×0.15 + OI_Funding×0.10
        """
        return (
            sweep_score * WEIGHT_SWEEP +
            mss_score * WEIGHT_MSS +
            fvg_score * WEIGHT_FVG +
            cvd_score * WEIGHT_CVD +
            oi_funding_score * WEIGHT_OI_FUNDING
        )

    # ═══════════════════════════════════════════════════════════════
    # MAIN: GENERATE SIGNAL
    # ═══════════════════════════════════════════════════════════════

    async def evaluate_symbol(
        self,
        symbol: str,
        side: str,
        market_data: Dict,
        orderflow: Optional[Dict],
        cvd_data: Optional[Dict],
        cumulative_delta: Optional[Dict],
        regime_data: Optional[Dict],
        funding_data: Optional[Dict],
        oi_data: Optional[Dict],
        sweep_analysis: Optional[Dict],
        sweep_setup: Optional[Dict],
        fvg_analysis: Optional[Dict],
        data_quality=None,
    ) -> Optional[InstitutionalSignal]:
        """
        Evaluate a symbol for signal generation using the 5-component model.

        Returns InstitutionalSignal if score >= MIN_SIGNAL_SCORE, None otherwise.
        """
        now = time.time()

        # ── Phase 13: Execution Safety ──
        safe, reason, health = self._check_data_health(
            symbol, data_quality, orderflow, cvd_data, oi_data
        )
        if not safe:
            self.metrics.record_signal_rejected(reason)
            logger.debug("🚫 {} SAFETY: {}", symbol, reason)
            return None

        # ── Cooldown check ──
        last_signal = self._cooldown.get(symbol, 0)
        if now - last_signal < self._cooldown_sec:
            self.metrics.record_signal_rejected("COOLDOWN")
            return None

        # ── Get klines for sweep/MSS detection ──
        klines = market_data.get("klines", {}).get("5m", [])
        if not klines:
            klines = market_data.get("klines", {}).get("1m", [])

        # ── Phase 4: Sweep Score ──
        sweep_sc, sweep_type, sweep_time, sweep_str = self._score_sweep(
            symbol, side, sweep_analysis, sweep_setup, klines
        )

        # ── Phase 5: MSS Score ──
        mss_sc, mss_type, mss_conf = self._score_mss(
            symbol, side, klines, regime_data
        )

        # ── Phase 6: FVG Score ──
        fvg_sc, fvg_quality, fvg_gap = self._score_fvg(
            symbol, side, fvg_analysis
        )

        # ── Phase 7-8: CVD + Delta Score ──
        cvd_sc, cvd_status, delta_status, delta_val, flow_ratio = self._score_cvd_delta(
            symbol, side, orderflow, cvd_data, cumulative_delta
        )

        # ── Phase 9-10: OI + Funding Score ──
        oi_sc, oi_status, fund_status, oi_chg, fund_rate = self._score_oi_funding(
            symbol, side, oi_data, funding_data
        )

        # ── Phase 11: Regime Guidance ──
        regime, regime_guide = self._get_regime_guidance(side, regime_data)

        # ── Phase 3: Calculate Composite Signal Score ──
        signal_score = self.calculate_signal_score(
            sweep_sc, mss_sc, fvg_sc, cvd_sc, oi_sc
        )

        # ── Count available components ──
        components = sum(1 for x in [sweep_analysis, cvd_data, orderflow, oi_data, funding_data] if x)

        # ── Phase 2: Rejection Logic (simplified) ──
        # Only reject if score is too low OR too few components
        # BREAKOUT REGIME: lower threshold to 65 (was 80) — preserves PF=4.82 edge
        # Rationale: breakout candidates have strong technicals but may lack
        # institutional flow data (exchange_flow, OI delta) that boosts score
        _regime = regime_data.get("regime", "range") if regime_data else "range"
        _is_breakout = _regime == "breakout"
        _min_score = 65.0 if _is_breakout else MIN_SIGNAL_SCORE
        if signal_score < _min_score:
            self.metrics.record_signal_rejected(f"LOW_SCORE:{signal_score:.0f}")
            logger.debug("⏭️ {} score={:.0f} < {:.0f} {} (sweep={:.0f} mss={:.0f} fvg={:.0f} cvd={:.0f} oi={:.0f})",
                         symbol, signal_score, _min_score,
                         "breakout" if _is_breakout else "standard",
                         sweep_sc, mss_sc, fvg_sc, cvd_sc, oi_sc)
            return None

        if components < MIN_REQUIRED_COMPONENTS:
            self.metrics.record_signal_rejected(f"LOW_COMPONENTS:{components}")
            logger.debug("⏭️ {} components={}/{}", symbol, components, MIN_REQUIRED_COMPONENTS)
            return None

        # ── Calculate Entry/SL/TP ──
        current_price = klines[-1]["close"] if klines else market_data.get("price", 0)
        if current_price <= 0:
            self.metrics.record_signal_rejected("NO_PRICE")
            return None

        # ATR-based SL/TP
        atr = self._calculate_atr(klines) if klines else current_price * 0.01

        if side == "LONG":
            entry = current_price
            stop_loss = entry - atr * 1.5
            target_1 = entry + atr * 2.5
            target_2 = entry + atr * 4.0
        else:
            entry = current_price
            stop_loss = entry + atr * 1.5
            target_1 = entry - atr * 2.5
            target_2 = entry - atr * 4.0

        risk = abs(entry - stop_loss)
        reward = abs(target_1 - entry)
        rr = reward / risk if risk > 0 else 0

        # ── Phase 12: Build Signal Output ──
        sig = InstitutionalSignal(
            symbol=symbol,
            direction=side,
            entry_price=entry,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            risk_reward=round(rr, 2),
            sweep_score=round(sweep_sc, 1),
            mss_score=round(mss_sc, 1),
            fvg_score=round(fvg_sc, 1),
            cvd_score=round(cvd_sc, 1),
            oi_funding_score=round(oi_sc, 1),
            signal_score=round(signal_score, 1),
            sweep_type=sweep_type,
            sweep_time=sweep_time,
            sweep_strength=round(sweep_sc, 1),  # Phase 10: fixed undefined variable — use sweep_sc score
            mss_type=mss_type,
            mss_confidence=round(mss_conf, 1),
            fvg_quality=fvg_quality,
            fvg_gap_pct=round(fvg_gap, 4),
            cvd_status=cvd_status,
            delta_status=delta_status,
            delta_value=round(delta_val, 2),
            flow_ratio=round(flow_ratio, 4),
            oi_status=oi_status,
            oi_change_pct=round(oi_chg, 4),
            funding_status=fund_status,
            funding_rate=round(fund_rate, 6),
            regime=regime,
            regime_guidance=regime_guide,
            confidence=round(signal_score, 1),
            timestamp=now,
            data_health="healthy",
            components_available=components,
        )

        # ── Record success ──
        self.metrics.record_signal_generated()
        self._cooldown[symbol] = now

        logger.info(
            "🏛️ {} {} SIGNAL: score={:.0f} entry={:.4f} SL={:.4f} TP1={:.4f} rr={:.1f} "
            "sweep={:.0f}({}) mss={:.0f}({}) fvg={:.0f}({}) cvd={:.0f}({}) oi={:.0f}({}) "
            "regime={}({}) components={}/5",
            symbol, side, signal_score, entry, stop_loss, target_1, rr,
            sweep_sc, sweep_type, mss_sc, mss_type, fvg_sc, fvg_quality,
            cvd_sc, cvd_status, oi_sc, oi_status, regime, regime_guide, components, 5,
        )

        return sig

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════

    def _calculate_atr(self, klines: List[Dict], period: int = 14) -> float:
        """Calculate Average True Range from klines."""
        if len(klines) < period + 1:
            if klines:
                return (klines[-1]["high"] - klines[-1]["low"]) or 0.01
            return 0.01

        trs = []
        for i in range(1, len(klines)):
            h = klines[i]["high"]
            l = klines[i]["low"]
            pc = klines[i - 1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)

        # EMA of TR
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 0.01

        atr_val = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr_val = (atr_val * (period - 1) + tr) / period

        return atr_val if atr_val > 0 else 0.01

    def get_metrics(self) -> Dict:
        """Phase 14: Return current performance metrics."""
        return self.metrics.get_summary()
