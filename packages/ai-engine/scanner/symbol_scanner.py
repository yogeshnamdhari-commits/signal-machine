"""
Auto Symbol Scanner — dynamic symbol discovery, filtering, rotation.
Continuously discovers and ranks USDT perpetual futures by opportunity.
Tier assignments are overridden by Adaptive Alpha Ranking Engine when available.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import numpy as np
from loguru import logger
from config import config


@dataclass
class SymbolProfile:
    symbol: str
    base_asset: str
    volume_24h: float = 0
    change_24h: float = 0
    volatility: float = 0
    momentum_score: float = 0
    volume_score: float = 0
    spread_score: float = 0
    composite_score: float = 0
    tier: str = "C"  # A, B, C
    last_update: float = 0
    data_quality: float = 0  # 0-1 how much data we have


class AutoSymbolScanner:
    """
    Discovers, filters, and ranks symbols dynamically.
    - Volume-based filtering
    - Volatility scoring
    - Momentum detection
    - Tier assignment (A/B/C)
    - Auto-rotation of stale symbols
    """

    def __init__(self) -> None:
        self._profiles: Dict[str, SymbolProfile] = {}
        self._excluded: Set[str] = set()
        self._last_scan: float = 0
        self._scan_interval: float = 300  # 5 minutes
        self._alpha_ranking = None  # Set by engine for tier overrides

    def set_alpha_ranking(self, alpha_ranking) -> None:
        """Set the alpha ranking engine for tier overrides."""
        self._alpha_ranking = alpha_ranking

    async def initialize(self) -> None:
        logger.info("AutoSymbolScanner ready")

    async def scan(self, tickers: List[Dict], existing: Set[str]) -> List[SymbolProfile]:
        """Scan and rank all available symbols."""
        now = time.time()
        if now - self._last_scan < self._scan_interval:
            return list(self._profiles.values())
        self._last_scan = now

        # Filter USDT perps with minimum volume
        candidates = [
            t for t in tickers
            if t["symbol"].endswith("USDT")
            and t.get("quoteVolume", 0) >= config.scanner.min_volume_24h
            and t["symbol"] not in self._excluded
        ]

        # Score each symbol
        for t in candidates:
            sym = t["symbol"]
            profile = self._profiles.setdefault(sym, SymbolProfile(
                symbol=sym,
                base_asset=sym.replace("USDT", ""),
            ))
            profile.volume_24h = t.get("quoteVolume", 0)
            profile.change_24h = t.get("change_pct", 0)
            profile.last_update = now
            self._update_scores(profile, t)

        # Assign tiers
        self._assign_tiers()

        # Remove stale profiles
        stale = [s for s, p in self._profiles.items() if now - p.last_update > 600]
        for s in stale:
            del self._profiles[s]

        ranked = sorted(self._profiles.values(), key=lambda p: p.composite_score, reverse=True)
        logger.info("Symbol scan: {} candidates, {} A-tier", len(candidates),
                     sum(1 for p in ranked if p.tier == "A"))
        return ranked[:config.scanner.max_symbols]

    def _update_scores(self, profile: SymbolProfile, ticker: Dict) -> None:
        """Update composite score from ticker data."""
        # Volume score (0-1, log scale)
        vol = max(profile.volume_24h, 1)
        profile.volume_score = min(np.log10(vol) / 9, 1)  # $1B = score 1.0

        # Volatility score from 24h range
        high = ticker.get("high", 0)
        low = ticker.get("low", 0)
        if low > 0:
            rng_pct = (high - low) / low * 100
            profile.volatility = rng_pct
        else:
            profile.volatility = 0

        # Momentum score from 24h change
        profile.momentum_score = min(abs(profile.change_24h) / 10, 1)

        # Spread score (inverse of spread — tighter = better)
        # Placeholder — real spread comes from orderbook
        profile.spread_score = 0.5

        # Composite
        profile.composite_score = (
            profile.volume_score * 0.35
            + profile.momentum_score * 0.25
            + profile.volatility / 20 * 0.20
            + profile.spread_score * 0.20
        )

    def _assign_tiers(self) -> None:
        """Assign A/B/C tiers based on composite score.
        
        If alpha ranking engine is available, use its tier assignments instead.
        This gives us S/A/B/C tiers based on actual trade performance.
        """
        if self._alpha_ranking:
            # Use alpha ranking tiers (S/A/B/C based on ALPHA_SCORE)
            for sym, profile in self._profiles.items():
                alpha_tier = self._alpha_ranking.get_tier(sym)
                profile.tier = alpha_tier
            return

        # Fallback: volume-based tier assignment
        scores = [p.composite_score for p in self._profiles.values()]
        if not scores:
            return
        p75 = np.percentile(scores, 75)
        p50 = np.percentile(scores, 50)
        for p in self._profiles.values():
            if p.composite_score >= p75:
                p.tier = "A"
            elif p.composite_score >= p50:
                p.tier = "B"
            else:
                p.tier = "C"

    def exclude_symbol(self, symbol: str) -> None:
        """Exclude a symbol from scanning."""
        self._excluded.add(symbol)

    def get_tier_symbols(self, tier: str = "A") -> List[str]:
        """Get all symbols in a given tier."""
        return [p.symbol for p in self._profiles.values() if p.tier == tier]

    def get_profile(self, symbol: str) -> Optional[SymbolProfile]:
        return self._profiles.get(symbol)

    def get_all_profiles(self) -> List[SymbolProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.composite_score, reverse=True)
