"""
Signal Grade Engine — Weighted institutional model for signal quality grading.

Replaces raw confidence with a 7-pillar weighted model producing
signal grades: A+, A, B, C.

Pillars and weights:
- Market Structure: 20%  (regime, trend, price action)
- Flow:            20%  (exchange flow, orderflow imbalance)
- Volume:          15%  (volume profile, relative volume)
- Open Interest:   15%  (OI regime, positioning, change)
- Funding:         10%  (funding rate, z-score)
- Sweep:           10%  (sweep detection, sweep intensity)
- Absorption:      10%  (absorption patterns, large trade absorption)

Grade thresholds (weighted score 0-100):
- A+ : ≥80
- A  : ≥65
- B  : ≥45
- C  : <45
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from loguru import logger


# ── Pillar Weights (must sum to 100) ─────────────────────────
PILLAR_WEIGHTS = {
    "market_structure": 20,
    "flow":            20,
    "volume":          15,
    "open_interest":   15,
    "funding":         10,
    "sweep":           10,
    "absorption":      10,
}
TOTAL_WEIGHT = sum(PILLAR_WEIGHTS.values())  # 100

# ── Grade Thresholds ─────────────────────────────────────────
GRADE_THRESHOLDS = [
    (80, "A+"),
    (65, "A"),
    (45, "B"),
    (0,  "C"),
]


class SignalGradeEngine:
    """
    Computes a weighted signal grade from 7 institutional pillars.
    
    Each pillar receives a raw score (0-100) based on available data,
    then the weighted composite determines the signal grade.
    """

    def compute_grade(
        self,
        regime: Optional[Dict] = None,
        orderflow: Optional[Dict] = None,
        exchange_flow: Optional[Dict] = None,
        volume_data: Optional[Dict] = None,
        oi_data: Optional[Dict] = None,
        funding_data: Optional[Dict] = None,
        sweep_data: Optional[Dict] = None,
        absorption_data: Optional[Dict] = None,
        cumulative_delta: Optional[Dict] = None,
        cvd_data: Optional[Dict] = None,
        liquidation_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Compute signal grade from available pillar data.
        
        Returns:
            {
                "signal_grade": "A+" | "A" | "B" | "C",
                "grade_score": float (0-100),
                "grade_confidence": float (0-1),
                "pillar_scores": {"market_structure": 0-100, ...},
                "pillar_details": {...},
                "pillars_available": int,
                "pillars_total": 7,
            }
        """
        pillar_scores = {}
        pillar_details = {}
        available = 0

        # ── 1. Market Structure (20%) ──
        ms_score, ms_detail = self._score_market_structure(regime, cumulative_delta, cvd_data)
        pillar_scores["market_structure"] = ms_score
        pillar_details["market_structure"] = ms_detail
        if ms_score is not None:
            available += 1

        # ── 2. Flow (20%) ──
        flow_score, flow_detail = self._score_flow(orderflow, exchange_flow)
        pillar_scores["flow"] = flow_score
        pillar_details["flow"] = flow_detail
        if flow_score is not None:
            available += 1

        # ── 3. Volume (15%) ──
        vol_score, vol_detail = self._score_volume(volume_data, regime)
        pillar_scores["volume"] = vol_score
        pillar_details["volume"] = vol_detail
        if vol_score is not None:
            available += 1

        # ── 4. Open Interest (15%) ──
        oi_score, oi_detail = self._score_open_interest(oi_data)
        pillar_scores["open_interest"] = oi_score
        pillar_details["open_interest"] = oi_detail
        if oi_score is not None:
            available += 1

        # ── 5. Funding (10%) ──
        fund_score, fund_detail = self._score_funding(funding_data)
        pillar_scores["funding"] = fund_score
        pillar_details["funding"] = fund_detail
        if fund_score is not None:
            available += 1

        # ── 6. Sweep (10%) ──
        sweep_score, sweep_detail = self._score_sweep(sweep_data, liquidation_data)
        pillar_scores["sweep"] = sweep_score
        pillar_details["sweep"] = sweep_detail
        if sweep_score is not None:
            available += 1

        # ── 7. Absorption (10%) ──
        abs_score, abs_detail = self._score_absorption(absorption_data, orderflow)
        pillar_scores["absorption"] = abs_score
        pillar_details["absorption"] = abs_detail
        if abs_score is not None:
            available += 1

        # ── Compute weighted composite ──
        composite = 0.0
        total_weight_used = 0.0

        for pillar, weight in PILLAR_WEIGHTS.items():
            score = pillar_scores.get(pillar)
            if score is not None:
                composite += score * weight
                total_weight_used += weight

        # Normalize to 0-100 using weights of available pillars only
        if total_weight_used > 0:
            grade_score = composite / total_weight_used * (TOTAL_WEIGHT / total_weight_used)
            # Recalculate properly: weighted sum / sum of available weights * 100
            grade_score = composite / total_weight_used
        else:
            grade_score = 50.0  # Default when no data available

        grade_score = max(0.0, min(100.0, grade_score))

        # ── Assign grade ──
        signal_grade = "C"
        for threshold, grade in GRADE_THRESHOLDS:
            if grade_score >= threshold:
                signal_grade = grade
                break

        # ── Grade confidence: based on data availability + pillar agreement ──
        data_coverage = available / len(PILLAR_WEIGHTS)
        # Check pillar agreement (std deviation penalty)
        active_scores = [v for v in pillar_scores.values() if v is not None]
        if len(active_scores) >= 2:
            mean_score = sum(active_scores) / len(active_scores)
            variance = sum((s - mean_score) ** 2 for s in active_scores) / len(active_scores)
            agreement = max(0, 1 - (variance / 2500))  # 50pt std = 0 agreement
        else:
            agreement = 0.5

        grade_confidence = min(1.0, data_coverage * 0.6 + agreement * 0.4)

        return {
            "signal_grade": signal_grade,
            "grade_score": round(grade_score, 1),
            "grade_confidence": round(grade_confidence, 3),
            "pillar_scores": pillar_scores,
            "pillar_details": pillar_details,
            "pillars_available": available,
            "pillars_total": len(PILLAR_WEIGHTS),
        }

    # ── Pillar Scorers ────────────────────────────────────────

    def _score_market_structure(
        self, regime: Optional[Dict], cd: Optional[Dict], cvd: Optional[Dict]
    ) -> tuple:
        """Score market structure: regime type, trend, CVD alignment."""
        if not regime:
            return None, "No regime data"

        score = 50.0  # neutral baseline
        details = []

        # Regime contribution (0-40 points)
        regime_type = regime.get("regime", "range")
        regime_conf = regime.get("confidence", 0.5)
        conf_pct = regime.get("regime_confidence_pct", 50)
        # 6-regime model: directional regimes score highest for signal strength
        regime_map = {
            "trending_bull": 75, "trending_bear": 25,
            "breakout": 65, "compression": 55,
            "volatile": 45, "range": 50,
            # Legacy compat
            "trending_up": 75, "trending_down": 25,
            "ranging": 50, "quiet": 50, "reversal": 50,
        }
        regime_pts = regime_map.get(regime_type, 50)
        # Direction alignment: high absolute deviation from neutral = strong signal
        regime_strength = abs(regime_pts - 50) * 2 * regime_conf  # 0-50
        score = 50 + regime_strength * 0.4
        details.append(f"regime={regime_type}({conf_pct:.0f}%)")

        # Multi-TF alignment bonus from regime engine
        alignment = regime.get("alignment_score", 0)
        if abs(alignment) > 0.5:
            score += 12
            details.append(f"mtf_align={alignment:.2f}")
        elif abs(alignment) > 0.3:
            score += 6
            details.append(f"mtf_align={alignment:.2f}")

        # CVD contribution (0-30 points)
        if cvd:
            cvd_bias = cvd.get("cvd_bias", "neutral")
            if cvd_bias in ("strong_bullish", "strong_bearish"):
                score += 15
                details.append(f"cvd={cvd_bias}")
            elif cvd_bias in ("bullish", "bearish"):
                score += 8
                details.append(f"cvd={cvd_bias}")

        # Multi-TF CVD alignment bonus
        if cvd:
            biases = [cvd.get(f"cvd_bias_{tf}", "neutral") for tf in ["5m", "15m", "1h"]]
            bullish = sum(1 for b in biases if "bullish" in b)
            bearish = sum(1 for b in biases if "bearish" in b)
            aligned = max(bullish, bearish)
            if aligned >= 2:
                score += 10
                details.append(f"cvd_mtf={aligned}")

        return min(100, max(0, score)), " + ".join(details)

    def _score_flow(
        self, orderflow: Optional[Dict], exchange_flow: Optional[Dict]
    ) -> tuple:
        """Score flow: orderflow imbalance + exchange flow signal."""
        if not orderflow and not exchange_flow:
            return None, "No flow data"

        score = 50.0
        details = []

        # Orderflow imbalance (0-40 points)
        if orderflow:
            imbalance = orderflow.get("imbalance", 0)
            # Strong imbalance in either direction = good signal
            abs_imb = abs(imbalance)
            score += abs_imb * 40
            details.append(f"of_imb={imbalance:.3f}")

            # Large trade skew
            lb = orderflow.get("large_buy_trades", 0)
            ls = orderflow.get("large_sell_trades", 0)
            total_large = lb + ls
            if total_large > 3:
                skew = abs(lb - ls) / total_large
                score += skew * 15
                details.append(f"large_skew={skew:.2f}")

        # Exchange flow (0-30 points)
        if exchange_flow:
            flow_signal = exchange_flow.get("flow_signal", "neutral")
            flow_strength = exchange_flow.get("flow_strength_score", 50)
            signal_pts = {
                "strong_buy": 25, "strong_sell": 25,
                "buy": 15, "sell": 15,
                "neutral": 0,
            }
            score += signal_pts.get(flow_signal, 0)
            score += (flow_strength / 100) * 10
            details.append(f"ef={flow_signal}({flow_strength:.0f})")

        return min(100, max(0, score)), " + ".join(details)

    def _score_volume(
        self, volume_data: Optional[Dict], regime: Optional[Dict]
    ) -> tuple:
        """Score volume: relative volume, volume trend, profile."""
        if not volume_data and not regime:
            return None, "No volume data"

        score = 50.0
        details = []

        # Volume profile from regime
        if regime:
            vol_profile = regime.get("volume_profile", 1.0)
            if vol_profile > 2.0:
                score += 25
                details.append(f"vp={vol_profile:.1f}x(very_high)")
            elif vol_profile > 1.5:
                score += 18
                details.append(f"vp={vol_profile:.1f}x(high)")
            elif vol_profile > 1.2:
                score += 10
                details.append(f"vp={vol_profile:.1f}x(above_avg)")
            else:
                details.append(f"vp={vol_profile:.1f}x(normal)")

        # Volume confirmation of signal direction
        if volume_data:
            vol_ratio = volume_data.get("vol_ratio", 1.0)
            if vol_ratio > 2.0:
                score += 20
                details.append(f"vol_ratio={vol_ratio:.1f}x")
            elif vol_ratio > 1.5:
                score += 12
                details.append(f"vol_ratio={vol_ratio:.1f}x")

        return min(100, max(0, score)), " + ".join(details)

    def _score_open_interest(self, oi_data: Optional[Dict]) -> tuple:
        """Score OI: regime, positioning, change, strength."""
        if not oi_data:
            return None, "No OI data"

        score = 50.0
        details = []

        # OI regime
        oi_regime = oi_data.get("oi_regime", "neutral_oi")
        if oi_regime == "bullish_oi":
            score += 20
        elif oi_regime == "bearish_oi":
            score += 20  # Both bullish and bearish OI regimes indicate strong positioning
        details.append(f"regime={oi_regime}")

        # Positioning
        positioning = oi_data.get("positioning", "neutral")
        if positioning in ("long_buildup", "short_covering"):
            score += 15
        elif positioning in ("short_buildup", "long_unwinding"):
            score += 15
        details.append(f"pos={positioning}")

        # OI strength
        strength = oi_data.get("oi_strength_score", 50)
        score += (strength / 100) * 15
        details.append(f"str={strength:.0f}")

        # Spike/flush detection
        if oi_data.get("spike_detected"):
            score += 10
            details.append("spike!")
        elif oi_data.get("flush_detected"):
            score += 10
            details.append("flush!")

        return min(100, max(0, score)), " + ".join(details)

    def _score_funding(self, funding_data: Optional[Dict]) -> tuple:
        """Score funding: rate, z-score, extremity."""
        if not funding_data:
            return None, "No funding data"

        score = 50.0
        details = []

        z_score = funding_data.get("z_score", 0)
        is_extreme = funding_data.get("is_extreme", False)

        # Contrarian: extreme funding in either direction = strong signal
        abs_z = abs(z_score)
        score += abs_z * 12  # ±3σ → 36 points
        details.append(f"z={z_score:.2f}")

        if is_extreme:
            score += 10
            details.append("extreme!")

        return min(100, max(0, score)), " + ".join(details)

    def _score_sweep(
        self, sweep_data: Optional[Dict], liquidation_data: Optional[Dict]
    ) -> tuple:
        """Score sweep detection: sweep events, liquidation sweeps."""
        score = 50.0
        details = []

        # Smart money sweep detection
        if sweep_data:
            sweep_signal = sweep_data.get("signal", "neutral")
            if sweep_signal in ("bullish_sweep", "bearish_sweep"):
                score += 30
                details.append(f"sweep={sweep_signal}")
            elif sweep_signal == "sweep_detected":
                score += 20
                details.append("sweep_active")

        # Liquidation sweep
        if liquidation_data:
            if liquidation_data.get("sweep_detected"):
                score += 20
                liq_sweep_dir = liquidation_data.get("sweep_direction", "")
                details.append(f"liq_sweep={liq_sweep_dir}")

            # Cascade detection
            if liquidation_data.get("cascade_active"):
                intensity = liquidation_data.get("cascade_intensity", 0)
                score += intensity * 15
                details.append(f"cascade={intensity:.2f}")

        if not details:
            return 50.0, "No sweep data"

        return min(100, max(0, score)), " + ".join(details)

    def _score_absorption(
        self, absorption_data: Optional[Dict], orderflow: Optional[Dict]
    ) -> tuple:
        """Score absorption: large trade absorption, iceberg detection."""
        score = 50.0
        details = []

        if absorption_data:
            abs_signal = absorption_data.get("signal", "neutral")
            abs_strength = absorption_data.get("strength", 0)
            if abs_signal in ("bullish_absorption", "bearish_absorption"):
                score += 25
                details.append(f"abs={abs_signal}({abs_strength:.2f})")
            elif abs_signal == "absorption_detected":
                score += 15
                details.append(f"abs_detected({abs_strength:.2f})")

        # Infer absorption from orderflow patterns
        if orderflow:
            lb = orderflow.get("large_buy_trades", 0)
            ls = orderflow.get("large_sell_trades", 0)
            delta = orderflow.get("delta", 0)
            total_large = lb + ls
            if total_large > 5:
                # Absorption = large trades opposing delta
                net_large = lb - ls
                large_dir = 1 if net_large > 0 else -1
                delta_dir = 1 if delta > 0 else -1
                if large_dir != delta_dir:
                    score += 20
                    details.append(f"inferred_absorption(lg={net_large:+.0f} vs δ={delta:+.0f})")
                else:
                    details.append(f"aligned(lg={net_large:+.0f} δ={delta:+.0f})")

        if not details:
            return 50.0, "No absorption data"

        return min(100, max(0, score)), " + ".join(details)


def grade_to_emoji(grade: str) -> str:
    """Convert grade to display emoji."""
    return {"A+": "💎", "A": "⭐", "B": "🔶", "C": "📊"}.get(grade, "📊")


def grade_to_color(grade: str) -> str:
    """Convert grade to display color."""
    return {"A+": "#00ff88", "A": "#00cc66", "B": "#f59e0b", "C": "#888"}.get(grade, "#888")
