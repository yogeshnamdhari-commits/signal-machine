"""
Correlation-Aware Portfolio Selector — Prevent concentration risk.

Per Executive Assessment v3:
    "If five highly correlated Layer-1 coins generate signals together,
     executing all five often concentrates risk without improving expected return."

    "Improve portfolio diversification:
        - Profit Factor by symbol
        - Expectancy by symbol
        - Correlation between active positions
        - Sector concentration
        - Simultaneous exposure"

Key Features:
    1. Sector/Category Detection — Group symbols by sector (L1, L2, DeFi, etc.)
    2. Correlation Scoring — Estimate correlation from price action
    3. Concentration Limits — Max N positions per sector
    4. Diversification Bonus — Reward portfolio diversity
    5. Adaptive Max Positions — Reduce when opportunity density is low

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# SECTOR MAPPING
# ═══════════════════════════════════════════════════════════════

SECTOR_MAP = {
    "L1": {"BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "ADAUSDT", "DOTUSDT",
            "NEARUSDT", "APTUSDT", "SUIUSDT", "SEIUSDT", "TONUSDT"},
    "L2": {"ARBUSDT", "OPUSDT", "MATICUSDT", "BASEUSDT", "STRKUSDT"},
    "DEFI": {"UNIUSDT", "AAVEUSDT", "LINKUSDT", "CRVUSDT", "MKRUSDT",
             "COMPUSDT", "SUSHIUSDT", "YFIUSDT"},
    "MEME": {"DOGEUSDT", "SHIBUSDT", "1000PEPEUSDT", "WIFUSDT", "FLOKIUSDT",
             "BONKUSDT", "1000BONKUSDT"},
    "AI": {"FETUSDT", "RENDERUSDT", "TAOUSDT", "NEARUSDT", "AIUSDT",
           "VIRTUALUSDT", "GAMEUSDT", "GRTUSDT"},
    "GAMING": {"IMXUSDT", "GALAUSDT", "SANDUSDT", "MANAUSDT", "ENJUSDT",
               "AXSUSDT", "ILVUSDT"},
    "EXCHANGE": {"BNBUSDT", "CETUSDT", "MXUSDT"},
}

# Reverse mapping: symbol → sector
SYMBOL_TO_SECTOR = {}
for sector, symbols in SECTOR_MAP.items():
    for sym in symbols:
        SYMBOL_TO_SECTOR[sym] = sector


# ═══════════════════════════════════════════════════════════════
# PORTFOLIO CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Maximum simultaneous open positions
MAX_OPEN_POSITIONS = 5

# Maximum positions per sector
MAX_PER_SECTOR = 2

# Maximum correlation between any two positions (0-1)
MAX_CORRELATION = 0.7

# Diversification bonus (multiplied into capital allocation)
DIVERSIFICATION_BONUS = 1.15  # 15% bonus for diversified portfolio
CONCENTRATION_PENALTY = 0.7   # 30% penalty for concentrated portfolio

# Adaptive execution count
MIN_EXECUTIONS = 1
MAX_EXECUTIONS = 3


@dataclass
class SectorExposure:
    """Current exposure to a sector."""
    sector: str = ""
    count: int = 0
    symbols: List[str] = field(default_factory=list)
    total_pnl: float = 0.0
    avg_pf: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "sector": self.sector,
            "count": self.count,
            "symbols": self.symbols,
            "total_pnl": round(self.total_pnl, 2),
            "avg_pf": round(self.avg_pf, 2),
        }


@dataclass
class PortfolioDiversification:
    """Diversification metrics for the current portfolio."""
    unique_sectors: int = 0
    sector_exposure: Dict[str, SectorExposure] = field(default_factory=dict)
    concentration_score: float = 0.0  # 0 = perfectly diversified, 1 = fully concentrated
    diversification_score: float = 0.0  # 0 = poor, 100 = excellent
    max_correlation: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "unique_sectors": self.unique_sectors,
            "sector_exposure": {k: v.to_dict() for k, v in self.sector_exposure.items()},
            "concentration_score": round(self.concentration_score, 3),
            "diversification_score": round(self.diversification_score, 1),
            "max_correlation": round(self.max_correlation, 3),
        }


@dataclass
class PortfolioSelectionResult:
    """Result from the Correlation-Aware Portfolio Selector."""
    total_candidates: int = 0
    selected_count: int = 0
    max_executions: int = MAX_EXECUTIONS
    selected_signals: List[Dict[str, Any]] = field(default_factory=list)
    rejected_signals: List[Dict[str, Any]] = field(default_factory=list)
    diversification: Optional[PortfolioDiversification] = None
    sector_limits_hit: List[str] = field(default_factory=list)
    correlation_limits_hit: List[str] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "total_candidates": self.total_candidates,
            "selected_count": self.selected_count,
            "max_executions": self.max_executions,
            "diversification": self.diversification.to_dict() if self.diversification else {},
            "sector_limits_hit": self.sector_limits_hit,
            "correlation_limits_hit": self.correlation_limits_hit,
            "timestamp": self.timestamp,
        }


class CorrelationAwarePortfolioSelector:
    """
    Selects signals for execution while maintaining portfolio diversification.

    Per Executive Assessment v3:
        "If five highly correlated Layer-1 coins generate signals together,
         executing all five often concentrates risk."

    This engine:
        1. Groups signals by sector
        2. Tracks current portfolio sector exposure
        3. Limits positions per sector
        4. Estimates correlation between signals
        5. Applies diversification bonus/penalty to capital allocation
        6. Adaptively adjusts execution count based on opportunity quality

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self) -> None:
        self._open_positions: List[Dict] = []

    def set_open_positions(self, positions: List[Dict]) -> None:
        """Set current open positions for portfolio analysis."""
        self._open_positions = positions

    def select_for_execution(
        self,
        ranked_signals: List[Dict[str, Any]],
        open_positions: Optional[List[Dict]] = None,
        eligible_count: int = 0,
    ) -> PortfolioSelectionResult:
        """
        Select signals for execution while maintaining diversification.

        Args:
            ranked_signals: Signals ranked by composite score (best first)
            open_positions: Current open positions
            eligible_count: Total number of eligible signals

        Returns:
            PortfolioSelectionResult with selected and rejected signals
        """
        positions = open_positions or self._open_positions
        result = PortfolioSelectionResult(
            total_candidates=len(ranked_signals),
            timestamp=time.time(),
        )

        # ── Analyze current portfolio ──
        current_div = self._analyze_portfolio(positions)
        result.diversification = current_div

        # ── Determine adaptive execution count ──
        max_exec = self._calculate_adaptive_execution_count(
            eligible_count, len(positions), current_div,
        )
        result.max_executions = max_exec

        # ── Select signals with diversification checks ──
        selected = []
        selected_sectors = set()
        sector_counts: Dict[str, int] = {}
        selected_symbols: Set[str] = set()

        # Count existing sector exposure
        for pos in positions:
            sym = pos.get("symbol", "")
            sector = SYMBOL_TO_SECTOR.get(sym, "OTHER")
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

        for sig in ranked_signals:
            if len(selected) >= max_exec:
                result.rejected_signals.append(sig)
                continue

            sym = sig.get("symbol", "")
            side = sig.get("side", "")
            sector = SYMBOL_TO_SECTOR.get(sym, "OTHER")

            # ── Check: Already selected this symbol ──
            if sym in selected_symbols:
                result.rejected_signals.append(sig)
                continue

            # ── Check: Sector limit ──
            current_sector_count = sector_counts.get(sector, 0)
            if current_sector_count >= MAX_PER_SECTOR:
                result.sector_limits_hit.append(f"{sym}:{sector}")
                result.rejected_signals.append(sig)
                continue

            # ── Check: Correlation with selected signals ──
            too_correlated = False
            for sel in selected:
                corr = self._estimate_correlation(sym, sel.get("symbol", ""))
                if corr > MAX_CORRELATION:
                    result.correlation_limits_hit.append(
                        f"{sym}↔{sel.get('symbol', '')} corr={corr:.2f}"
                    )
                    too_correlated = True
                    break

            if too_correlated:
                result.rejected_signals.append(sig)
                continue

            # ── Check: Same side concentration ──
            same_side_count = sum(
                1 for s in selected if s.get("side", "") == side
            )
            if same_side_count >= 3:
                result.rejected_signals.append(sig)
                continue

            # ── Select this signal ──
            selected.append(sig)
            selected_symbols.add(sym)
            sector_counts[sector] = current_sector_count + 1
            selected_sectors.add(sector)

        # ── Calculate diversification bonus ──
        unique_sectors = len(selected_sectors)
        if unique_sectors >= 3:
            div_bonus = DIVERSIFICATION_BONUS
        elif unique_sectors == 2:
            div_bonus = 1.05  # Slight bonus
        else:
            div_bonus = CONCENTRATION_PENALTY  # Penalty for single sector

        # Apply diversification bonus to selected signals
        for sig in selected:
            sig["_diversification_bonus"] = div_bonus

        result.selected_signals = selected
        result.selected_count = len(selected)

        logger.info(
            "📊 PORTFOLIO SELECT: {} candidates → {} selected (max={}) "
            "sectors={} div_bonus={:.2f}×",
            result.total_candidates, result.selected_count,
            result.max_executions, unique_sectors, div_bonus,
        )

        return result

    def _analyze_portfolio(self, positions: List[Dict]) -> PortfolioDiversification:
        """Analyze current portfolio diversification."""
        div = PortfolioDiversification()

        if not positions:
            div.diversification_score = 100.0  # Empty portfolio is perfectly diversified
            return div

        # Count sectors
        sector_counts: Dict[str, int] = {}
        sector_pnl: Dict[str, float] = {}
        for pos in positions:
            sym = pos.get("symbol", "")
            sector = SYMBOL_TO_SECTOR.get(sym, "OTHER")
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            pnl = pos.get("pnl", 0) or 0
            sector_pnl[sector] = sector_pnl.get(sector, 0) + pnl

        div.unique_sectors = len(sector_counts)

        # Build sector exposure
        for sector, count in sector_counts.items():
            div.sector_exposure[sector] = SectorExposure(
                sector=sector,
                count=count,
                total_pnl=sector_pnl.get(sector, 0),
            )

        # Concentration score (Herfindahl-like)
        total = len(positions)
        if total > 0:
            hhi = sum((c / total) ** 2 for c in sector_counts.values())
            div.concentration_score = hhi  # 1.0 = fully concentrated, lower = diversified

        # Diversification score (100 = perfectly diversified)
        div.diversification_score = max(0, min(100,
            (1.0 - div.concentration_score) * 100 * (div.unique_sectors / 3)
        ))

        # Estimate max correlation
        max_corr = 0.0
        for i, p1 in enumerate(positions):
            for j, p2 in enumerate(positions):
                if i < j:
                    corr = self._estimate_correlation(
                        p1.get("symbol", ""), p2.get("symbol", "")
                    )
                    max_corr = max(max_corr, corr)
        div.max_correlation = max_corr

        return div

    def _estimate_correlation(self, sym1: str, sym2: str) -> float:
        """
        Estimate correlation between two symbols.

        Uses sector-based heuristic:
        - Same sector: 0.6-0.8 correlation
        - Adjacent sectors: 0.3-0.5
        - Different sectors: 0.1-0.3
        - BTC/ETH: 0.85 (known high correlation)
        """
        # Special cases
        major_pairs = {
            frozenset({"BTCUSDT", "ETHUSDT"}): 0.85,
            frozenset({"SOLUSDT", "AVAXUSDT"}): 0.75,
            frozenset({"DOGEUSDT", "SHIBUSDT"}): 0.80,
            frozenset({"ARBUSDT", "OPUSDT"}): 0.78,
        }
        pair = frozenset({sym1, sym2})
        if pair in major_pairs:
            return major_pairs[pair]

        sector1 = SYMBOL_TO_SECTOR.get(sym1, "OTHER")
        sector2 = SYMBOL_TO_SECTOR.get(sym2, "OTHER")

        if sector1 == sector2:
            return 0.65  # Same sector
        elif sector1 == "OTHER" or sector2 == "OTHER":
            return 0.25
        else:
            # Adjacent sectors have higher correlation
            adjacent = {
                ("L1", "L2"), ("L1", "DEFI"), ("L2", "DEFI"),
                ("AI", "GAMING"), ("MEME", "L1"),
            }
            if (sector1, sector2) in adjacent or (sector2, sector1) in adjacent:
                return 0.40
            return 0.20  # Different sectors

    def _calculate_adaptive_execution_count(
        self,
        eligible_count: int,
        open_count: int,
        diversification: PortfolioDiversification,
    ) -> int:
        """
        Adaptively determine how many signals to execute.

        Per Executive Assessment v3:
            "As opportunity density increases, capital should become
             more selective."

        Rules:
            - Few eligible signals → execute all (max 3)
            - Many eligible signals → execute top 2-3 only
            - Portfolio already concentrated → reduce by 1
            - Portfolio well-diversified → allow 1 more
        """
        available_slots = MAX_OPEN_POSITIONS - open_count
        if available_slots <= 0:
            return 0

        # Base: min(eligible, 3)
        base = min(eligible_count, MAX_EXECUTIONS)

        # Adjust for diversification
        if diversification.unique_sectors >= 3 and diversification.diversification_score > 60:
            base = min(base + 1, MAX_EXECUTIONS)  # Allow 1 more if well-diversified
        elif diversification.concentration_score > 0.5:
            base = max(base - 1, MIN_EXECUTIONS)  # Reduce if concentrated

        # Adjust for opportunity density
        if eligible_count > 8:
            base = min(base, 2)  # Very selective when many opportunities
        elif eligible_count > 5:
            base = min(base, 3)

        return max(MIN_EXECUTIONS, min(base, available_slots))

    def get_sector_for_symbol(self, symbol: str) -> str:
        """Get the sector for a symbol."""
        return SYMBOL_TO_SECTOR.get(symbol, "OTHER")

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get portfolio diversification summary."""
        div = self._analyze_portfolio(self._open_positions)
        return div.to_dict()
