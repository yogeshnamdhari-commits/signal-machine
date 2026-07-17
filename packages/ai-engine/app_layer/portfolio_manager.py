"""
Portfolio Manager — Prevents overexposure, correlation, and concentration risk.

READ-ONLY with respect to upstream data. Never modifies positions or orders.

Per Master Directive:
    "Prevent: Duplicate positions, Highly correlated trades, Too many longs,
     Too many shorts, Overexposure, Repeated losses on same symbol,
     Sector concentration."

Portfolio Rules:
    1. No duplicate positions (same symbol + same side)
    2. Max 3 positions in same direction
    3. Max 5 total open positions
    4. Max 2 positions in same sector
    5. Block symbol after 2 consecutive losses
    6. Max portfolio heat 5%
    7. Daily drawdown limit 3%
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Set

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# PORTFOLIO LIMITS
# ═══════════════════════════════════════════════════════════════

MAX_OPEN_POSITIONS = 5
MAX_SAME_DIRECTION = 3
MAX_SAME_SECTOR = 2
MAX_PORTFOLIO_HEAT_PCT = 5.0
MAX_DAILY_LOSS_PCT = 3.0
CONSECUTIVE_LOSS_BLOCK = 2  # Block symbol after N consecutive losses

# Sector mapping (simplified — crypto sectors)
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


@dataclass
class PortfolioCheckResult:
    """Result of portfolio check."""
    symbol: str = ""
    side: str = ""
    approved: bool = False
    blocked: bool = False
    reason: str = ""
    warnings: List[str] = None

    def __init__(self, **kwargs):
        super().__init__()
        self.warnings = []
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "approved": self.approved,
            "blocked": self.blocked,
            "reason": self.reason,
            "warnings": self.warnings,
        }


class PortfolioManager:
    """
    Portfolio-level risk management and position limits.

    READ-ONLY: never modifies upstream data. Returns approval/rejection
    decisions for the execution layer.
    """

    def __init__(self) -> None:
        self._loss_history: Dict[str, int] = {}  # symbol → consecutive losses

    def check_trade(
        self,
        signal: Dict[str, Any],
        open_positions: List[Dict],
        balance: float = 10_000.0,
        daily_pnl: float = 0.0,
    ) -> PortfolioCheckResult:
        """
        Check if a trade is allowed given current portfolio state.

        Args:
            signal: Trade signal dict
            open_positions: List of current open position dicts
            balance: Current account balance
            daily_pnl: Today's realized PnL

        Returns:
            PortfolioCheckResult with approval status
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")

        result = PortfolioCheckResult(symbol=symbol, side=side)

        # ── Check 1: Duplicate position ──
        for pos in open_positions:
            if pos.get("symbol") == symbol and pos.get("side") == side:
                result.blocked = True
                result.reason = f"duplicate position: {symbol} {side} already open"
                return result

        # ── Check 2: Max open positions ──
        if len(open_positions) >= MAX_OPEN_POSITIONS:
            result.blocked = True
            result.reason = f"max positions reached ({len(open_positions)}/{MAX_OPEN_POSITIONS})"
            return result

        # ── Check 3: Max same direction ──
        same_dir = sum(1 for p in open_positions if p.get("side") == side)
        if same_dir >= MAX_SAME_DIRECTION:
            result.blocked = True
            result.reason = f"max {side} positions reached ({same_dir}/{MAX_SAME_DIRECTION})"
            return result

        # ── Check 4: Sector concentration ──
        sector = self._get_sector(symbol)
        if sector:
            same_sector = sum(
                1 for p in open_positions
                if self._get_sector(p.get("symbol", "")) == sector
            )
            if same_sector >= MAX_SAME_SECTOR:
                result.blocked = True
                result.reason = (
                    f"sector '{sector}' concentration reached "
                    f"({same_sector}/{MAX_SAME_SECTOR})"
                )
                return result
            elif same_sector >= MAX_SAME_SECTOR - 1:
                result.warnings.append(
                    f"sector '{sector}' at {same_sector}/{MAX_SAME_SECTOR}"
                )

        # ── Check 5: Consecutive losses on symbol ──
        losses = self._loss_history.get(symbol, 0)
        if losses >= CONSECUTIVE_LOSS_BLOCK:
            result.blocked = True
            result.reason = (
                f"symbol blocked: {losses} consecutive losses on {symbol}"
            )
            return result

        # ── Check 6: Portfolio heat ──
        current_heat = sum(
            abs(p.get("entry_price", 0) * p.get("quantity", 0))
            for p in open_positions
        )
        heat_pct = current_heat / balance * 100 if balance > 0 else 0
        if heat_pct >= MAX_PORTFOLIO_HEAT_PCT:
            result.blocked = True
            result.reason = f"portfolio heat {heat_pct:.1f}% >= {MAX_PORTFOLIO_HEAT_PCT}%"
            return result
        elif heat_pct >= MAX_PORTFOLIO_HEAT_PCT * 0.8:
            result.warnings.append(f"portfolio heat high: {heat_pct:.1f}%")

        # ── Check 7: Daily loss limit ──
        if daily_pnl < 0:
            daily_loss_pct = abs(daily_pnl) / balance * 100
            if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
                result.blocked = True
                result.reason = (
                    f"daily loss limit {daily_loss_pct:.1f}% >= {MAX_DAILY_LOSS_PCT}%"
                )
                return result
            elif daily_loss_pct >= MAX_DAILY_LOSS_PCT * 0.7:
                result.warnings.append(f"daily loss approaching limit: {daily_loss_pct:.1f}%")

        # ── Approved ──
        result.approved = True
        result.reason = "portfolio check passed"
        return result

    def record_loss(self, symbol: str) -> None:
        """Record a loss for a symbol (for consecutive loss tracking)."""
        self._loss_history[symbol] = self._loss_history.get(symbol, 0) + 1

    def record_win(self, symbol: str) -> None:
        """Record a win for a symbol (resets consecutive loss counter)."""
        self._loss_history[symbol] = 0

    def _get_sector(self, symbol: str) -> str:
        """Get sector for a symbol."""
        for sector, symbols in SECTOR_MAP.items():
            if symbol in symbols:
                return sector
        return "OTHER"

    def get_portfolio_summary(
        self,
        open_positions: List[Dict],
        balance: float = 10_000.0,
    ) -> Dict:
        """Get current portfolio summary."""
        longs = [p for p in open_positions if p.get("side") == "LONG"]
        shorts = [p for p in open_positions if p.get("side") == "SHORT"]

        total_exposure = sum(
            abs(p.get("entry_price", 0) * p.get("quantity", 0))
            for p in open_positions
        )

        sectors = {}
        for p in open_positions:
            sector = self._get_sector(p.get("symbol", ""))
            sectors[sector] = sectors.get(sector, 0) + 1

        return {
            "total_positions": len(open_positions),
            "longs": len(longs),
            "shorts": len(shorts),
            "total_exposure": round(total_exposure, 2),
            "exposure_pct": round(total_exposure / balance * 100, 2) if balance > 0 else 0,
            "sectors": sectors,
            "blocked_symbols": {
                s: c for s, c in self._loss_history.items()
                if c >= CONSECUTIVE_LOSS_BLOCK
            },
        }
