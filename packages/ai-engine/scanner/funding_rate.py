"""
Funding Rate Engine — perpetual funding analysis, extreme detection, mean-reversion signals.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


@dataclass
class FundingState:
    symbol: str
    rates: List[Dict] = field(default_factory=list)  # [{rate, timestamp}]
    current_rate: float = 0
    avg_rate: float = 0
    std_rate: float = 0
    cumulative_8h: float = 0
    z_score: float = 0
    is_extreme: bool = False
    direction: str = "neutral"  # "long_paying" or "short_paying"


class FundingRateEngine:
    """
    Funding rate analysis:
    - Current and historical funding rates
    - Z-score deviation detection
    - Extreme funding → reversal signals
    - Cumulative funding cost tracking
    """

    def __init__(self) -> None:
        self._states: Dict[str, FundingState] = {}
        self._extreme_threshold = 2.0  # z-score

    async def initialize(self) -> None:
        logger.info("FundingRate engine ready")

    async def process_funding(self, symbol: str, rate: float, timestamp: float) -> None:
        st = self._states.setdefault(symbol, FundingState(symbol=symbol))

        # Clamp to sane display range — real rates can reach ±0.05 (±5%)
        rate = max(-0.05, min(0.05, rate))

        # Only update if this is a newer timestamp
        if st.rates and timestamp <= st.rates[-1].get("ts", 0):
            return

        st.rates.append({"rate": rate, "ts": timestamp})
        if len(st.rates) > 500:
            st.rates = st.rates[-250:]

        st.current_rate = rate

        if len(st.rates) >= 10:
            rates_arr = np.array([r["rate"] for r in st.rates])
            st.avg_rate = float(np.mean(rates_arr))
            st.std_rate = float(np.std(rates_arr))
            if st.std_rate > 0:
                st.z_score = (rate - st.avg_rate) / st.std_rate
            else:
                st.z_score = 0
            st.is_extreme = abs(st.z_score) > self._extreme_threshold
        else:
            st.z_score = 0
            st.is_extreme = False

        st.cumulative_8h = sum(r["rate"] for r in st.rates[-1:])
        st.direction = "long_paying" if rate > 0 else "short_paying" if rate < 0 else "neutral"

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st:
            return None
        return {
            "symbol": symbol,
            "current_rate": st.current_rate,
            "avg_rate": st.avg_rate,
            "z_score": st.z_score,
            "is_extreme": st.is_extreme,
            "direction": st.direction,
            "cumulative_8h": st.cumulative_8h,
            "annualized": st.current_rate * 3 * 365 * 100,  # 3 funding/day * 365
            "signal": "short_bias" if st.is_extreme and st.z_score > 0 else (
                "long_bias" if st.is_extreme and st.z_score < 0 else "neutral"
            ),
        }
