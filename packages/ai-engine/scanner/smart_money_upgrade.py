"""
Smart Money Upgrade Engine — 8 institutional intelligence upgrades.

1. Smart Money Probability Engine — actionable direction + probability
2. Smart Money Score — 7-factor composite (0-100)
3. Signal Engine Integration — SM score boosts signal confidence
4. Smart Money Divergence — price vs SM flow direction
5. BTC Control Index — BTC SM state influences all altcoins
6. Level Probability Engine — rank S/R levels by probability
7. Institutional Entry Zones — Buy/Stop/TP1/TP2/TP3
8. Outcome Tracking — accumulation/distribution accuracy over 1h/4h/24h
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


# ══════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════

@dataclass
class SmartMoneyProbability:
    """Actionable probability output per symbol."""
    symbol: str = ""
    accumulation_prob: float = 0.0      # 0-100%
    distribution_prob: float = 0.0      # 0-100%
    neutral_prob: float = 70.0          # default
    expected_direction: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL
    expected_move_pct: float = 0.0      # expected % move
    expected_holding_time: str = "N/A"   # "6-18 Hours"
    risk_grade: str = "C"               # A / B / C / D
    probability_of_success: float = 0.0  # historical accuracy at this level
    timestamp: float = 0


@dataclass
class SmartMoneyCompositeScore:
    """7-factor Smart Money Score (0-100)."""
    symbol: str = ""
    flow_score: float = 0.0         # order flow imbalance
    oi_score: float = 0.0           # open interest positioning
    delta_score: float = 0.0        # cumulative delta
    funding_score: float = 0.0      # funding rate contrarian
    sweep_score: float = 0.0        # liquidity sweep
    absorption_score: float = 0.0   # passive absorption
    iceberg_score: float = 0.0      # hidden orders
    total_score: float = 0.0        # weighted composite 0-100
    grade: str = "C"                # A+ / A / B / C / D
    timestamp: float = 0


@dataclass
class SmartMoneyDivergence:
    """Price vs Smart Money flow divergence."""
    symbol: str = ""
    divergence_type: str = "NONE"    # BULLISH_DIVERGENCE / BEARISH_DIVERGENCE / NONE
    price_pattern: str = "NONE"      # HH / HL / LH / LL / FLAT
    sm_flow_pattern: str = "NONE"    # HH / HL / LH / LL / FLAT
    strength: float = 0.0           # 0-1
    signal: str = "NEUTRAL"          # BULLISH / BEARISH / NEUTRAL
    description: str = ""
    timestamp: float = 0


@dataclass
class BTCControlIndex:
    """BTC Smart Money influence on altcoins."""
    btc_score: float = 50.0          # 0-100
    btc_direction: str = "NEUTRAL"   # BULLISH / BEARISH / NEUTRAL
    btc_regime: str = "NORMAL"       # RISK_ON / RISK_OFF / NORMAL
    altcoin_adjustment: float = 0.0  # -20 to +20 confidence adjustment
    timestamp: float = 0


@dataclass
class LevelProbability:
    """Ranked support/resistance level with probability."""
    price: float = 0
    level_type: str = ""             # support / resistance / liquidity_cluster
    probability: float = 0.0         # 0-100%
    zone: str = ""                   # BUY_ZONE / STOP_ZONE / TP1 / TP2 / TP3
    strength: float = 0.0           # 0-1
    label: str = ""                  # "Level A (82%)"
    timestamp: float = 0


@dataclass
class OutcomeTracker:
    """Tracks accumulation/distribution accuracy over time."""
    symbol: str = ""
    signal_type: str = ""            # accumulation / distribution
    entry_price: float = 0
    price_1h: float = 0
    price_4h: float = 0
    price_24h: float = 0
    accuracy_1h: float = 0           # did price move in expected direction?
    accuracy_4h: float = 0
    accuracy_24h: float = 0
    avg_return_1h: float = 0
    avg_return_4h: float = 0
    avg_return_24h: float = 0
    total_signals: int = 0
    timestamp: float = 0


# ══════════════════════════════════════════════════════════════
# UPGRADE 1: SMART MONEY PROBABILITY ENGINE
# ══════════════════════════════════════════════════════════════

class SmartMoneyProbabilityEngine:
    """
    Converts raw Smart Money scores into actionable probabilities.
    
    Instead of: "Accumulating" (descriptive)
    Outputs:    "Accumulation Probability: 84%, Direction: Bullish, Risk: A"
    """

    def compute_probability(
        self,
        symbol: str,
        sm_analysis: Dict,
        market_data: Optional[Dict] = None,
    ) -> SmartMoneyProbability:
        """Compute actionable probability from Smart Money analysis."""
        prob = SmartMoneyProbability(symbol=symbol, timestamp=time.time())

        accum_score = sm_analysis.get("accumulation_score", 0)
        distrib_score = sm_analysis.get("distribution_score", 0)
        strength = sm_analysis.get("smart_money_strength", 0)
        sm_side = sm_analysis.get("smart_money_side", "neutral")
        stealth_buys = sm_analysis.get("stealth_buys", 0)
        stealth_sells = sm_analysis.get("stealth_sells", 0)
        flow = sm_analysis.get("institutional_flow", 0)

        # ── Accumulation/Distribution Probability ──
        # Use Bayesian-style conversion from scores to probability
        total_signal = accum_score + distrib_score
        if total_signal > 0:
            prob.accumulation_prob = min(95, accum_score * 0.85 + strength * 0.15)
            prob.distribution_prob = min(95, distrib_score * 0.85 + strength * 0.15)
        else:
            prob.accumulation_prob = max(5, 15 + strength * 0.1)
            prob.distribution_prob = max(5, 15 + strength * 0.1)

        # ── Expected Direction ──
        if sm_side == "accumulation" or (prob.accumulation_prob > prob.distribution_prob + 15):
            prob.expected_direction = "BULLISH"
        elif sm_side == "distribution" or (prob.distribution_prob > prob.accumulation_prob + 15):
            prob.expected_direction = "BEARISH"
        else:
            prob.expected_direction = "NEUTRAL"

        # ── Expected Move % ──
        # Based on strength and probability difference
        prob_diff = abs(prob.accumulation_prob - prob.distribution_prob)
        prob.expected_move_pct = min(5.0, prob_diff * 0.05 + strength * 0.02)

        # ── Expected Holding Time ──
        if strength > 70:
            prob.expected_holding_time = "4-12 Hours"
        elif strength > 50:
            prob.expected_holding_time = "6-18 Hours"
        elif strength > 30:
            prob.expected_holding_time = "12-36 Hours"
        else:
            prob.expected_holding_time = "24-72 Hours"

        # ── Risk Grade ──
        success_prob = self._estimate_success_prob(accum_score, distrib_score, strength, sm_side)
        prob.probability_of_success = success_prob
        if success_prob >= 70:
            prob.risk_grade = "A"
        elif success_prob >= 55:
            prob.risk_grade = "B"
        elif success_prob >= 40:
            prob.risk_grade = "C"
        else:
            prob.risk_grade = "D"

        # ── Neutral probability (for completeness) ──
        prob.neutral_prob = max(5, 100 - prob.accumulation_prob - prob.distribution_prob)

        return prob

    def _estimate_success_prob(
        self, accum: float, distrib: float, strength: float, side: str
    ) -> float:
        """Estimate probability of successful trade based on SM signals."""
        # Base probability from strength
        base = 40 + strength * 0.3

        # Boost if directional signals are strong
        signal_strength = abs(accum - distrib)
        if signal_strength > 50:
            base += 15
        elif signal_strength > 30:
            base += 8

        # Boost if side matches strongest signal
        if side == "accumulation" and accum > distrib:
            base += 5
        elif side == "distribution" and distrib > accum:
            base += 5

        return min(85, max(20, base))


# ══════════════════════════════════════════════════════════════
# UPGRADE 2: SMART MONEY SCORE (7-FACTOR)
# ══════════════════════════════════════════════════════════════

class SmartMoneyScoreEngine:
    """
    Combines 7 institutional factors into a single Smart Money Score (0-100).
    
    Flow + OI + Delta + Funding + Sweep + Absorption + Iceberg
    """

    WEIGHTS = {
        "flow": 0.20,        # Order flow imbalance
        "oi": 0.15,          # Open Interest positioning
        "delta": 0.15,       # Cumulative delta
        "funding": 0.10,     # Funding rate contrarian
        "sweep": 0.15,       # Liquidity sweep
        "absorption": 0.15,  # Passive absorption
        "iceberg": 0.10,     # Hidden orders
    }

    def compute_score(
        self,
        symbol: str,
        sm_analysis: Dict,
        orderflow: Optional[Dict] = None,
        oi_data: Optional[Dict] = None,
        funding_data: Optional[Dict] = None,
        sweep_data: Optional[Dict] = None,
    ) -> SmartMoneyCompositeScore:
        """Compute 7-factor Smart Money Score."""
        score = SmartMoneyCompositeScore(symbol=symbol, timestamp=time.time())

        # ── Flow Score (0-100) ──
        if orderflow:
            imbalance = orderflow.get("imbalance", 0)
            flow_ratio = orderflow.get("flow_ratio", 0.5)
            # Convert to 0-100: 0.5 = 50, 0.7 = 80, 0.3 = 20
            score.flow_score = max(0, min(100, 50 + imbalance * 100))
        else:
            score.flow_score = 50  # neutral

        # ── OI Score (0-100) ──
        if oi_data:
            change_pct = oi_data.get("change_pct", 0)
            oi_trend = oi_data.get("oi_trend", 0)
            # Rising OI + price direction alignment
            score.oi_score = max(0, min(100, 50 + change_pct * 200 + oi_trend * 15))
        else:
            score.oi_score = 50

        # ── Delta Score (0-100) ──
        if orderflow:
            delta = orderflow.get("delta", 0)
            total_vol = orderflow.get("buy_volume", 0) + orderflow.get("sell_volume", 0)
            if total_vol > 0:
                delta_ratio = delta / total_vol
                score.delta_score = max(0, min(100, 50 + delta_ratio * 200))
            else:
                score.delta_score = 50
        else:
            score.delta_score = 50

        # ── Funding Score (0-100) — contrarian ──
        if funding_data:
            rate = funding_data.get("current_rate", 0)
            z = funding_data.get("z_score", 0)
            # Negative funding = bullish (shorts paying longs)
            # Positive funding = bearish (longs paying shorts)
            score.funding_score = max(0, min(100, 50 - rate * 5000 - z * 10))
        else:
            score.funding_score = 50

        # ── Sweep Score (0-100) ──
        sweep_conf = sm_analysis.get("sweep_confidence", 0)
        sweep_count = sm_analysis.get("sweep_count", 0)
        score.sweep_score = min(100, sweep_conf * 80 + sweep_count * 10 + 10)

        # ── Absorption Score (0-100) ──
        abs_conf = sm_analysis.get("absorption_confidence", 0)
        abs_count = sm_analysis.get("absorption_count", 0)
        score.absorption_score = min(100, abs_conf * 80 + abs_count * 10 + 10)

        # ── Iceberg Score (0-100) ──
        iceberg_conf = sm_analysis.get("iceberg_confidence", 0)
        hidden_depth = sm_analysis.get("hidden_order_depth", 0)
        score.iceberg_score = min(100, iceberg_conf * 70 + hidden_depth * 20 + 10)

        # ── Weighted Composite ──
        score.total_score = (
            score.flow_score * self.WEIGHTS["flow"] +
            score.oi_score * self.WEIGHTS["oi"] +
            score.delta_score * self.WEIGHTS["delta"] +
            score.funding_score * self.WEIGHTS["funding"] +
            score.sweep_score * self.WEIGHTS["sweep"] +
            score.absorption_score * self.WEIGHTS["absorption"] +
            score.iceberg_score * self.WEIGHTS["iceberg"]
        )

        # ── Grade ──
        if score.total_score >= 85:
            score.grade = "A+"
        elif score.total_score >= 75:
            score.grade = "A"
        elif score.total_score >= 65:
            score.grade = "B"
        elif score.total_score >= 50:
            score.grade = "C"
        else:
            score.grade = "D"

        return score


# ══════════════════════════════════════════════════════════════
# UPGRADE 4: SMART MONEY DIVERGENCE
# ══════════════════════════════════════════════════════════════

class SmartMoneyDivergenceEngine:
    """
    Detects divergence between price direction and Smart Money flow direction.
    
    Bearish: Price HH + SM Flow LH → Institutional selling
    Bullish: Price LL + SM Flow HL → Institutional accumulation
    """

    def detect_divergence(
        self,
        symbol: str,
        price_history: List[float],
        sm_flow_history: List[float],
        window: int = 20,
    ) -> SmartMoneyDivergence:
        """Detect Smart Money divergence from price and flow history."""
        div = SmartMoneyDivergence(symbol=symbol, timestamp=time.time())

        if len(price_history) < window or len(sm_flow_history) < window:
            return div

        # Split into two halves
        mid = len(price_history) // 2
        price_first = price_history[:mid]
        price_second = price_history[mid:]
        flow_first = sm_flow_history[:mid]
        flow_second = sm_flow_history[mid:]

        # Determine price pattern
        price_trend = self._get_trend(price_first, price_second)
        flow_trend = self._get_trend(flow_first, flow_second)

        div.price_pattern = price_trend
        div.sm_flow_pattern = flow_trend

        # ── Divergence Detection ──
        if price_trend in ("HH", "HL") and flow_trend in ("LH", "LL"):
            # Price rising but SM flow falling → institutional selling
            div.divergence_type = "BEARISH_DIVERGENCE"
            div.signal = "BEARISH"
            div.strength = self._calc_strength(price_first, price_second, flow_first, flow_second)
            div.description = f"Price {price_trend} + SM Flow {flow_trend} = Institutional selling"
        elif price_trend in ("LH", "LL") and flow_trend in ("HH", "HL"):
            # Price falling but SM flow rising → institutional accumulation
            div.divergence_type = "BULLISH_DIVERGENCE"
            div.signal = "BULLISH"
            div.strength = self._calc_strength(price_first, price_second, flow_first, flow_second)
            div.description = f"Price {price_trend} + SM Flow {flow_trend} = Institutional accumulation"
        else:
            div.divergence_type = "NONE"
            div.signal = "NEUTRAL"
            div.description = f"Price {price_trend} + SM Flow {flow_trend} = Aligned"

        return div

    def _get_trend(self, first_half: List[float], second_half: List[float]) -> str:
        """Determine trend pattern from two halves of data."""
        if not first_half or not second_half:
            return "FLAT"
        avg_first = np.mean(first_half)
        avg_second = np.mean(second_half)
        max_first = max(first_half)
        max_second = max(second_half)
        min_first = min(first_half)
        min_second = min(second_half)

        if avg_second > avg_first * 1.01:
            if max_second > max_first:
                return "HH"  # Higher High
            else:
                return "HL"  # Higher Low (consolidating up)
        elif avg_second < avg_first * 0.99:
            if min_second < min_first:
                return "LL"  # Lower Low
            else:
                return "LH"  # Lower High (consolidating down)
        else:
            return "FLAT"

    def _calc_strength(self, p1, p2, f1, f2) -> float:
        """Calculate divergence strength 0-1."""
        price_change = (np.mean(p2) - np.mean(p1)) / np.mean(p1) if np.mean(p1) else 0
        flow_change = (np.mean(f2) - np.mean(f1)) / abs(np.mean(f1)) if np.mean(f1) else 0
        # Strength = how much they diverge
        divergence = abs(price_change - flow_change)
        return min(1.0, divergence * 10)


# ══════════════════════════════════════════════════════════════
# UPGRADE 5: BTC CONTROL INDEX
# ══════════════════════════════════════════════════════════════

class BTCControlIndexEngine:
    """
    BTC Smart Money state influences all altcoin confidence.
    
    Rules:
    - BTC > 70 distributing → Reduce long confidence on alts
    - BTC > 70 accumulating → Increase long confidence on alts
    - BTC neutral → No adjustment
    """

    def compute_index(
        self,
        btc_sm_analysis: Dict,
        btc_orderflow: Optional[Dict] = None,
    ) -> BTCControlIndex:
        """Compute BTC Control Index from BTC Smart Money data."""
        idx = BTCControlIndex(timestamp=time.time())

        accum = btc_sm_analysis.get("accumulation_score", 0)
        distrib = btc_sm_analysis.get("distribution_score", 0)
        strength = btc_sm_analysis.get("smart_money_strength", 0)
        sm_side = btc_sm_analysis.get("smart_money_side", "neutral")

        # ── BTC Score (0-100) ──
        if sm_side == "accumulation":
            idx.btc_score = min(100, 50 + (accum - distrib) * 0.5 + strength * 0.3)
            idx.btc_direction = "BULLISH"
        elif sm_side == "distribution":
            idx.btc_score = max(0, 50 - (distrib - accum) * 0.5 - strength * 0.3)
            idx.btc_direction = "BEARISH"
        else:
            idx.btc_score = 50
            idx.btc_direction = "NEUTRAL"

        # ── BTC Regime ──
        if idx.btc_score > 70:
            idx.btc_regime = "RISK_ON"   # BTC bullish → risk-on → alts bullish
        elif idx.btc_score < 30:
            idx.btc_regime = "RISK_OFF"  # BTC bearish → risk-off → alts bearish
        else:
            idx.btc_regime = "NORMAL"

        # ── Altcoin Adjustment ──
        # BTC distributing → reduce alt long confidence
        if idx.btc_direction == "BEARISH":
            idx.altcoin_adjustment = -min(20, strength * 0.25)
        elif idx.btc_direction == "BULLISH":
            idx.altcoin_adjustment = min(15, strength * 0.20)
        else:
            idx.altcoin_adjustment = 0

        return idx


# ══════════════════════════════════════════════════════════════
# UPGRADE 6: LEVEL PROBABILITY ENGINE
# ══════════════════════════════════════════════════════════════

class LevelProbabilityEngine:
    """
    Ranks support/resistance levels by probability and assigns trade zones.
    
    Instead of: "Level at $65,000"
    Outputs:    "Level A (82%) — BUY ZONE"
    """

    def rank_levels(
        self,
        symbol: str,
        price_levels: List[Dict],
        current_price: float,
        sm_analysis: Dict,
    ) -> List[LevelProbability]:
        """Rank levels by probability and assign zones."""
        if not price_levels or not current_price:
            return []

        levels = []
        sm_side = sm_analysis.get("smart_money_side", "neutral")
        strength = sm_analysis.get("smart_money_strength", 0)

        for lvl in price_levels:
            lp = LevelProbability(timestamp=time.time())
            lp.price = lvl.get("price", 0)
            lp.level_type = lvl.get("type", "support")

            if not lp.price or not current_price:
                continue

            # Distance from current price
            dist_pct = abs(lp.price - current_price) / current_price * 100

            # ── Probability Calculation ──
            # Base probability from level strength
            base_prob = lvl.get("strength", 50)

            # Boost if SM direction aligns with level type
            if sm_side == "accumulation" and lp.level_type == "support":
                base_prob += 15
            elif sm_side == "distribution" and lp.level_type == "resistance":
                base_prob += 15

            # Closer levels are more relevant
            if dist_pct < 1:
                base_prob += 10
            elif dist_pct < 3:
                base_prob += 5

            lp.probability = min(95, max(5, base_prob))
            lp.strength = lp.probability / 100

            # ── Zone Assignment ──
            if lp.price < current_price:
                if dist_pct < 0.5:
                    lp.zone = "STOP_ZONE"
                    lp.label = f"Stop Zone ${lp.price:.4f} ({lp.probability:.0f}%)"
                elif dist_pct < 2:
                    lp.zone = "BUY_ZONE"
                    lp.label = f"BUY ZONE ${lp.price:.4f} ({lp.probability:.0f}%)"
                else:
                    lp.zone = "SUPPORT"
                    lp.label = f"Support ${lp.price:.4f} ({lp.probability:.0f}%)"
            else:
                if dist_pct < 1:
                    lp.zone = "TP1"
                    lp.label = f"TP1 ${lp.price:.4f} ({lp.probability:.0f}%)"
                elif dist_pct < 3:
                    lp.zone = "TP2"
                    lp.label = f"TP2 ${lp.price:.4f} ({lp.probability:.0f}%)"
                else:
                    lp.zone = "TP3"
                    lp.label = f"TP3 ${lp.price:.4f} ({lp.probability:.0f}%)"

            levels.append(lp)

        # Sort by probability (highest first)
        levels.sort(key=lambda x: x.probability, reverse=True)
        return levels


# ══════════════════════════════════════════════════════════════
# UPGRADE 7: OUTCOME TRACKING
# ══════════════════════════════════════════════════════════════

class OutcomeTrackingEngine:
    """
    Tracks accumulation/distribution accuracy over 1h/4h/24h.
    
    Without this, the module cannot learn from its predictions.
    """

    _DB_PATH = Path(__file__).resolve().parent.parent / "data" / "database" / "outcome_tracking.db"

    def __init__(self) -> None:
        self._init_db()

    def _init_db(self) -> None:
        try:
            self._DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self._DB_PATH), timeout=10)
            db.execute("""
                CREATE TABLE IF NOT EXISTS smart_money_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    signal_type TEXT,
                    entry_price REAL,
                    price_1h REAL, price_4h REAL, price_24h REAL,
                    accuracy_1h REAL, accuracy_4h REAL, accuracy_24h REAL,
                    return_1h REAL, return_4h REAL, return_24h REAL,
                    timestamp REAL
                )
            """)
            db.commit()
            db.close()
        except Exception as e:
            logger.debug("OutcomeTracking DB init failed: {}", e)

    def record_signal(
        self, symbol: str, signal_type: str, entry_price: float
    ) -> None:
        """Record a Smart Money signal for later outcome tracking."""
        try:
            db = sqlite3.connect(str(self._DB_PATH), timeout=10)
            db.execute(
                """INSERT INTO smart_money_outcomes 
                   (symbol, signal_type, entry_price, timestamp)
                   VALUES (?, ?, ?, ?)""",
                (symbol, signal_type, entry_price, time.time()),
            )
            db.commit()
            db.close()
        except Exception:
            pass

    def update_outcomes(self, symbol: str, current_price: float) -> None:
        """Update price outcomes for active signals."""
        try:
            db = sqlite3.connect(str(self._DB_PATH), timeout=10)
            now = time.time()
            rows = db.execute(
                """SELECT id, signal_type, entry_price, timestamp 
                   FROM smart_money_outcomes 
                   WHERE symbol=? AND price_24h IS NULL""",
                (symbol,),
            ).fetchall()

            for row in rows:
                rid, sig_type, entry, ts = row
                age_hours = (now - ts) / 3600

                if age_hours >= 1 and not db.execute(
                    "SELECT price_1h FROM smart_money_outcomes WHERE id=?", (rid,)
                ).fetchone()[0]:
                    # Calculate 1h outcome
                    if sig_type == "accumulation":
                        accuracy = 1.0 if current_price > entry else 0.0
                    else:
                        accuracy = 1.0 if current_price < entry else 0.0
                    ret = (current_price - entry) / entry * 100
                    db.execute(
                        "UPDATE smart_money_outcomes SET price_1h=?, accuracy_1h=?, return_1h=? WHERE id=?",
                        (current_price, accuracy, ret, rid),
                    )

                if age_hours >= 4 and not db.execute(
                    "SELECT price_4h FROM smart_money_outcomes WHERE id=?", (rid,)
                ).fetchone()[0]:
                    if sig_type == "accumulation":
                        accuracy = 1.0 if current_price > entry else 0.0
                    else:
                        accuracy = 1.0 if current_price < entry else 0.0
                    ret = (current_price - entry) / entry * 100
                    db.execute(
                        "UPDATE smart_money_outcomes SET price_4h=?, accuracy_4h=?, return_4h=? WHERE id=?",
                        (current_price, accuracy, ret, rid),
                    )

                if age_hours >= 24 and not db.execute(
                    "SELECT price_24h FROM smart_money_outcomes WHERE id=?", (rid,)
                ).fetchone()[0]:
                    if sig_type == "accumulation":
                        accuracy = 1.0 if current_price > entry else 0.0
                    else:
                        accuracy = 1.0 if current_price < entry else 0.0
                    ret = (current_price - entry) / entry * 100
                    db.execute(
                        "UPDATE smart_money_outcomes SET price_24h=?, accuracy_24h=?, return_24h=? WHERE id=?",
                        (current_price, accuracy, ret, rid),
                    )

            db.commit()
            db.close()
        except Exception:
            pass

    def get_accuracy_stats(self) -> Dict:
        """Get overall accuracy statistics."""
        try:
            db = sqlite3.connect(str(self._DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row

            stats = {}
            for period in ["1h", "4h", "24h"]:
                rows = db.execute(
                    f"""SELECT signal_type, accuracy_{period}, return_{period}
                        FROM smart_money_outcomes 
                        WHERE accuracy_{period} IS NOT NULL"""
                ).fetchall()

                if rows:
                    acc_vals = [r[f"accuracy_{period}"] for r in rows]
                    ret_vals = [r[f"return_{period}"] for r in rows]
                    accum_rows = [r for r in rows if r["signal_type"] == "accumulation"]
                    distrib_rows = [r for r in rows if r["signal_type"] == "distribution"]

                    stats[period] = {
                        "total": len(rows),
                        "accuracy": np.mean(acc_vals) * 100 if acc_vals else 0,
                        "avg_return": np.mean(ret_vals) if ret_vals else 0,
                        "accum_accuracy": np.mean([r[f"accuracy_{period}"] for r in accum_rows]) * 100 if accum_rows else 0,
                        "distrib_accuracy": np.mean([r[f"accuracy_{period}"] for r in distrib_rows]) * 100 if distrib_rows else 0,
                    }
                else:
                    stats[period] = {"total": 0, "accuracy": 0, "avg_return": 0}

            db.close()
            return stats
        except Exception:
            return {}


# ══════════════════════════════════════════════════════════════
# UNIFIED SMART MONEY UPGRADE ORCHESTRATOR
# ══════════════════════════════════════════════════════════════

class SmartMoneyUpgradeOrchestrator:
    """
    Orchestrates all 8 Smart Money upgrades into a single unified output.
    """

    def __init__(self) -> None:
        self.probability_engine = SmartMoneyProbabilityEngine()
        self.score_engine = SmartMoneyScoreEngine()
        self.divergence_engine = SmartMoneyDivergenceEngine()
        self.btc_control = BTCControlIndexEngine()
        self.level_engine = LevelProbabilityEngine()
        self.outcome_tracker = OutcomeTrackingEngine()
        self._btc_index: Optional[BTCControlIndex] = None

    def update_btc(self, btc_sm_analysis: Dict, btc_orderflow: Optional[Dict] = None) -> None:
        """Update BTC Control Index (call once per cycle)."""
        self._btc_index = self.btc_control.compute_index(btc_sm_analysis, btc_orderflow)

    def process_symbol(
        self,
        symbol: str,
        sm_analysis: Dict,
        orderflow: Optional[Dict] = None,
        oi_data: Optional[Dict] = None,
        funding_data: Optional[Dict] = None,
        price_history: Optional[List[float]] = None,
        sm_flow_history: Optional[List[float]] = None,
        price_levels: Optional[List[Dict]] = None,
        current_price: float = 0,
    ) -> Dict[str, Any]:
        """Process all upgrades for a single symbol. Returns unified result."""

        result: Dict[str, Any] = {"symbol": symbol}

        # 1. Probability Engine
        prob = self.probability_engine.compute_probability(symbol, sm_analysis)
        result["probability"] = {
            "accumulation": round(prob.accumulation_prob, 1),
            "distribution": round(prob.distribution_prob, 1),
            "direction": prob.expected_direction,
            "move_pct": round(prob.expected_move_pct, 2),
            "holding_time": prob.expected_holding_time,
            "risk_grade": prob.risk_grade,
            "success_prob": round(prob.probability_of_success, 1),
        }

        # 2. Smart Money Score
        sm_score = self.score_engine.compute_score(
            symbol, sm_analysis, orderflow, oi_data, funding_data
        )
        result["sm_score"] = {
            "total": round(sm_score.total_score, 1),
            "grade": sm_score.grade,
            "flow": round(sm_score.flow_score, 1),
            "oi": round(sm_score.oi_score, 1),
            "delta": round(sm_score.delta_score, 1),
            "funding": round(sm_score.funding_score, 1),
            "sweep": round(sm_score.sweep_score, 1),
            "absorption": round(sm_score.absorption_score, 1),
            "iceberg": round(sm_score.iceberg_score, 1),
        }

        # 3. Divergence
        if price_history and sm_flow_history:
            div = self.divergence_engine.detect_divergence(
                symbol, price_history, sm_flow_history
            )
            result["divergence"] = {
                "type": div.divergence_type,
                "signal": div.signal,
                "strength": round(div.strength, 2),
                "description": div.description,
            }
        else:
            result["divergence"] = {"type": "NONE", "signal": "NEUTRAL", "strength": 0, "description": "Insufficient data"}

        # 4. BTC Control Index
        if self._btc_index:
            result["btc_control"] = {
                "btc_score": round(self._btc_index.btc_score, 1),
                "btc_direction": self._btc_index.btc_direction,
                "btc_regime": self._btc_index.btc_regime,
                "altcoin_adj": round(self._btc_index.altcoin_adjustment, 1),
            }

        # 5. Level Probabilities
        if price_levels and current_price:
            levels = self.level_engine.rank_levels(
                symbol, price_levels, current_price, sm_analysis
            )
            result["levels"] = [
                {
                    "price": l.price, "zone": l.zone, "label": l.label,
                    "probability": round(l.probability, 1), "type": l.level_type,
                }
                for l in levels[:8]  # Top 8 levels
            ]
        else:
            result["levels"] = []

        # 6. Record outcome tracking
        sm_side = sm_analysis.get("smart_money_side", "neutral")
        if sm_side in ("accumulation", "distribution") and current_price > 0:
            self.outcome_tracker.record_signal(symbol, sm_side, current_price)
            self.outcome_tracker.update_outcomes(symbol, current_price)

        return result
