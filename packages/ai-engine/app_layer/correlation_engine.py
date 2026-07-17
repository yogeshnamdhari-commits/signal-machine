"""
Correlation Engine — Recognizes when multiple signals are the same trade.

READ-ONLY with respect to upstream data. Never modifies signals.

Per Master Directive:
    "The App should recognize when multiple signals are effectively the same trade.
     Example: BTC LONG, ETH LONG, SOL LONG, AVAX LONG — these often share market
     direction. The App should reduce combined exposure rather than treating them
     as four independent opportunities."

Correlation Detection:
    1. Same-side correlation: All LONG or all SHORT
    2. Sector correlation: L1, L2, MEME, AI, etc.
    3. Market-direction correlation: BTC-driven signals
    4. Time correlation: Signals within same time window

Exposure Management:
    - Combined risk for correlated positions must stay within limits
    - Reduce individual position size when holding correlated trades
    - Block new trades when correlation exposure is too high
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# SECTOR DEFINITIONS
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
    "PRIVACY": {"XMRUSDT", "ZECUSDT", "SCRTUSDT"},
    "INFRA": {"LINKUSDT", "DOTUSDT", "ATOMUSDT", "MINAUSDT"},
    "EXCHANGE": {"BNBUSDT", "CETUSDT", "MXUSDT"},
}

# BTC beta symbols — highly correlated with BTC direction
BTC_BETA_SYMBOLS = {"ETHUSDT", "SOLUSDT", "AVAXUSDT", "ADAUSDT", "DOTUSDT",
                     "NEARUSDT", "LINKUSDT", "UNIUSDT"}

# Correlation limits
MAX_CORRELATED_POSITIONS = 3
MAX_SECTOR_EXPOSURE = 2
MAX_SAME_DIRECTION = 3
CORRELATED_RISK_LIMIT_PCT = 3.0  # Max 3% of balance in correlated trades


@dataclass
class CorrelationGroup:
    """A group of correlated signals."""
    name: str
    correlation_type: str  # "same_side" | "sector" | "btc_beta" | "time"
    symbols: List[str] = field(default_factory=list)
    sides: List[str] = field(default_factory=list)
    total_risk_pct: float = 0.0
    blocked: bool = False
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "type": self.correlation_type,
            "symbols": self.symbols,
            "sides": self.sides,
            "total_risk_pct": round(self.total_risk_pct, 2),
            "blocked": self.blocked,
            "reason": self.reason,
        }


@dataclass
class CorrelationCheckResult:
    """Result of correlation check for a signal."""
    symbol: str = ""
    side: str = ""
    approved: bool = False
    correlation_reduction: float = 1.0  # Multiplier for position size
    existing_correlated: int = 0
    sector_exposure: int = 0
    same_direction_count: int = 0
    groups: List[CorrelationGroup] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "approved": self.approved,
            "correlation_reduction": round(self.correlation_reduction, 2),
            "existing_correlated": self.existing_correlated,
            "sector_exposure": self.sector_exposure,
            "same_direction_count": self.same_direction_count,
            "groups": [g.to_dict() for g in self.groups],
            "reason": self.reason,
        }


class CorrelationEngine:
    """
    Detects and manages correlated positions.

    Per Master Directive: Reduce combined exposure for correlated trades.
    Don't treat them as four independent opportunities.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self._open_positions: List[Dict] = []

    def set_open_positions(self, positions: List[Dict]) -> None:
        """Set current open positions for correlation analysis."""
        self._open_positions = positions

    def check_trade(
        self,
        signal: Dict[str, Any],
        balance: float = 10_000.0,
    ) -> CorrelationCheckResult:
        """
        Check correlation for a new signal against existing positions.

        Args:
            signal: Trade signal dict
            balance: Account balance

        Returns:
            CorrelationCheckResult with approval and reduction factors
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")

        result = CorrelationCheckResult(symbol=symbol, side=side)

        if not self._open_positions:
            result.approved = True
            result.reason = "no open positions — no correlation"
            return result

        # ── Count same-direction positions ──
        same_dir = [p for p in self._open_positions if p.get("side") == side]
        result.same_direction_count = len(same_dir)

        if result.same_direction_count >= MAX_SAME_DIRECTION:
            result.approved = False
            result.correlation_reduction = 0.0
            result.reason = (
                f"max same-direction positions reached "
                f"({result.same_direction_count}/{MAX_SAME_DIRECTION})"
            )
            return result

        # ── Sector exposure ──
        sector = self._get_sector(symbol)
        if sector:
            same_sector = [
                p for p in self._open_positions
                if self._get_sector(p.get("symbol", "")) == sector
            ]
            result.sector_exposure = len(same_sector)

            if result.sector_exposure >= MAX_SECTOR_EXPOSURE:
                result.approved = False
                result.correlation_reduction = 0.0
                result.reason = (
                    f"sector '{sector}' exposure maxed "
                    f"({result.sector_exposure}/{MAX_SECTOR_EXPOSURE})"
                )
                return result

        # ── BTC beta correlation ──
        if symbol in BTC_BETA_SYMBOLS:
            btc_correlated = [
                p for p in self._open_positions
                if p.get("symbol", "") in BTC_BETA_SYMBOLS
                and p.get("side") == side
            ]
            result.existing_correlated = len(btc_correlated)

            if result.existing_correlated >= 2:
                result.approved = False
                result.correlation_reduction = 0.0
                result.reason = (
                    f"BTC beta correlation maxed "
                    f"({result.existing_correlated} correlated positions)"
                )
                return result

        # ── Calculate reduction factor ──
        # More correlated positions = more reduction
        reduction = 1.0

        # Same direction reduction
        if result.same_direction_count >= 2:
            reduction *= 0.7  # 30% reduction
        if result.same_direction_count >= 1:
            reduction *= 0.85  # 15% reduction

        # Sector reduction
        if result.sector_exposure >= 1:
            reduction *= 0.8  # 20% reduction

        # BTC beta reduction
        if result.existing_correlated >= 1:
            reduction *= 0.75  # 25% reduction

        result.correlation_reduction = reduction
        result.approved = True
        result.reason = (
            f"correlation reduction: {reduction:.0%} "
            f"(same_dir={result.same_direction_count}, "
            f"sector={result.sector_exposure}, "
            f"btc_beta={result.existing_correlated})"
        )

        logger.debug(
            "CORR: {} {} → approved={} reduction={:.0%} "
            "(dir={}, sector={}, btc={})",
            symbol, side, result.approved, reduction,
            result.same_direction_count, result.sector_exposure,
            result.existing_correlated,
        )

        return result

    def get_correlated_groups(
        self,
        signals: List[Dict[str, Any]],
    ) -> List[CorrelationGroup]:
        """
        Group signals by correlation type.

        Args:
            signals: List of signal dicts

        Returns:
            List of CorrelationGroup with correlated signals grouped
        """
        groups: List[CorrelationGroup] = []

        # Group by sector
        sector_groups: Dict[str, List[str]] = {}
        for sig in signals:
            sector = self._get_sector(sig.get("symbol", ""))
            if sector:
                if sector not in sector_groups:
                    sector_groups[sector] = []
                sector_groups[sector].append(sig.get("symbol", ""))

        for sector, symbols in sector_groups.items():
            if len(symbols) > 1:
                groups.append(CorrelationGroup(
                    name=sector,
                    correlation_type="sector",
                    symbols=symbols,
                ))

        # Group by same direction (BTC beta)
        long_signals = [s for s in signals if s.get("side") == "LONG"]
        short_signals = [s for s in signals if s.get("side") == "SHORT"]

        btc_beta_longs = [
            s.get("symbol", "") for s in long_signals
            if s.get("symbol", "") in BTC_BETA_SYMBOLS
        ]
        if len(btc_beta_longs) > 1:
            groups.append(CorrelationGroup(
                name="BTC_BETA_LONG",
                correlation_type="btc_beta",
                symbols=btc_beta_longs,
                sides=["LONG"] * len(btc_beta_longs),
            ))

        btc_beta_shorts = [
            s.get("symbol", "") for s in short_signals
            if s.get("symbol", "") in BTC_BETA_SYMBOLS
        ]
        if len(btc_beta_shorts) > 1:
            groups.append(CorrelationGroup(
                name="BTC_BETA_SHORT",
                correlation_type="btc_beta",
                symbols=btc_beta_shorts,
                sides=["SHORT"] * len(btc_beta_shorts),
            ))

        return groups

    @staticmethod
    def _get_sector(symbol: str) -> str:
        """Get sector for a symbol."""
        for sector, symbols in SECTOR_MAP.items():
            if symbol in symbols:
                return sector
        return "OTHER"
