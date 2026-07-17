"""
Entry Confirmation Engine — multi-factor trade entry validation.
Confirms entries using volume, momentum, structure, and confluence.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


@dataclass
class ConfirmationResult:
    symbol: str
    direction: str          # "LONG" or "SHORT"
    confirmed: bool
    confidence: float       # 0-1
    factors_passed: int
    factors_total: int
    factors: List[Dict]     # [{name, passed, weight, detail}]
    entry_price: float
    stop_loss: float
    take_profit: float
    timestamp: float
    rejection_reason: str = ""


@dataclass
class EntryState:
    symbol: str
    price_history: List[float] = field(default_factory=list)
    volume_history: List[float] = field(default_factory=list)
    confirmations: List[ConfirmationResult] = field(default_factory=list)


class EntryConfirmationEngine:
    """
    Multi-factor entry confirmation:
    - Volume confirmation (above average)
    - Price structure (higher low / lower high)
    - Momentum alignment
    - Key level proximity (support/resistance)
    - Trend filter
    - Confluence scoring
    """

    def __init__(self) -> None:
        self._states: Dict[str, EntryState] = {}
        self._min_factors = 3
        self._min_confidence = 0.6

    async def initialize(self) -> None:
        logger.info("EntryConfirmation engine ready")

    async def confirm_entry(
        self, symbol: str, direction: str, entry_price: float,
        stop_loss: float, take_profit: float,
        market_data: Dict, orderflow: Optional[Dict] = None,
        regime: Optional[Dict] = None, liquidity: Optional[Dict] = None,
    ) -> ConfirmationResult:
        st = self._states.setdefault(symbol, EntryState(symbol=symbol))
        factors: List[Dict] = []

        # 1. Volume confirmation
        vol_confirmed = False
        if orderflow:
            avg_vol = orderflow.get("avg_trade_size", 0)
            recent_vol = orderflow.get("buy_volume" if direction == "LONG" else "sell_volume", 0)
            vol_confirmed = recent_vol > avg_vol * 1.2 if avg_vol > 0 else False
        factors.append({"name": "volume", "passed": vol_confirmed, "weight": 0.2,
                        "detail": "Above-average volume on signal side"})

        # 2. Price structure
        trades = market_data.get("trades", [])
        structure_ok = False
        if len(trades) >= 20:
            prices = [t["price"] for t in trades[-20:]]
            if direction == "LONG":
                # Higher lows
                lows = [min(prices[i:i+5]) for i in range(0, 15, 5)]
                structure_ok = len(lows) >= 2 and lows[-1] > lows[-2]
            else:
                highs = [max(prices[i:i+5]) for i in range(0, 15, 5)]
                structure_ok = len(highs) >= 2 and highs[-1] < highs[-2]
        factors.append({"name": "structure", "passed": structure_ok, "weight": 0.2,
                        "detail": f"{'Higher lows' if direction == 'LONG' else 'Lower highs'}"})

        # 3. Momentum alignment
        momentum_ok = False
        if orderflow:
            imbalance = orderflow.get("imbalance", 0)
            momentum_ok = (imbalance > 0.15 and direction == "LONG") or \
                          (imbalance < -0.15 and direction == "SHORT")
        factors.append({"name": "momentum", "passed": momentum_ok, "weight": 0.15,
                        "detail": f"Order flow imbalance {'positive' if direction == 'LONG' else 'negative'}"})

        # 4. Regime alignment
        regime_ok = False
        if regime:
            r = regime.get("regime", "")
            if direction == "LONG":
                regime_ok = r in ("trending_up", "breakout", "reversal")
            else:
                regime_ok = r in ("trending_down", "breakout", "reversal")
            if r == "ranging":
                regime_ok = True  # Ranging is OK for both with tight stops
        factors.append({"name": "regime", "passed": regime_ok, "weight": 0.15,
                        "detail": f"Market regime: {regime.get('regime', 'unknown') if regime else 'N/A'}"})

        # 5. Risk/reward check
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        rr_ok = reward / risk >= 1.5 if risk > 0 else False
        factors.append({"name": "risk_reward", "passed": rr_ok, "weight": 0.15,
                        "detail": f"R:R = {reward/risk:.2f}" if risk > 0 else "No risk"})

        # 6. Liquidity level proximity
        liq_ok = True  # Default pass if no data
        if liquidity:
            supports = liquidity.get("support_levels", [])
            resistances = liquidity.get("resistance_levels", [])
            if direction == "LONG" and supports:
                nearest = min(supports, key=lambda x: abs(x - entry_price))
                liq_ok = abs(nearest - entry_price) / entry_price < 0.02  # Within 2%
            elif direction == "SHORT" and resistances:
                nearest = min(resistances, key=lambda x: abs(x - entry_price))
                liq_ok = abs(nearest - entry_price) / entry_price < 0.02
        factors.append({"name": "liquidity_proximity", "passed": liq_ok, "weight": 0.15,
                        "detail": "Near key liquidity level"})

        # Calculate confirmation
        passed = sum(1 for f in factors if f["passed"])
        total = len(factors)
        tw = sum(f["weight"] for f in factors)
        confidence = sum(f["weight"] for f in factors if f["passed"]) / tw if tw > 0 else 0

        confirmed = passed >= self._min_factors and confidence >= self._min_confidence
        rejection = "" if confirmed else f"Only {passed}/{total} factors, conf={confidence:.2%}"

        result = ConfirmationResult(
            symbol=symbol, direction=direction, confirmed=confirmed,
            confidence=confidence, factors_passed=passed, factors_total=total,
            factors=factors, entry_price=entry_price, stop_loss=stop_loss,
            take_profit=take_profit, timestamp=time.time(), rejection_reason=rejection,
        )
        st.confirmations.append(result)
        if len(st.confirmations) > 100:
            st.confirmations = st.confirmations[-50:]
        return result

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st or not st.confirmations:
            return None
        recent = st.confirmations[-10:]
        confirmed = sum(1 for c in recent if c.confirmed)
        return {
            "symbol": symbol,
            "recent_confirmations": len(recent),
            "confirmed_count": confirmed,
            "confirmation_rate": confirmed / len(recent) if recent else 0,
            "avg_confidence": float(np.mean([c.confidence for c in recent])),
        }
