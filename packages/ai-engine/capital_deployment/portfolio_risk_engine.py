"""
Portfolio Risk Engine — Real-Time Portfolio Risk Management
===========================================================
Tracks: Total Risk, Symbol Correlation, Sector Exposure,
        Long/Short Exposure, Leverage, Margin Usage
"""

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "capital"


# ─── Enums ───────────────────────────────────────────────────────────────────
class RiskLevel(Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    BREACH = "BREACH"


# ─── Data Classes ────────────────────────────────────────────────────────────
@dataclass
class PositionRisk:
    """Risk metrics for a single position."""
    symbol: str
    side: str  # LONG / SHORT
    quantity: float
    entry_price: float
    current_price: float
    stop_loss: float
    take_profit: float
    leverage: int
    sector: str = "CRYPTO"
    unrealized_pnl: float = 0.0
    risk_amount: float = 0.0
    margin_used: float = 0.0
    notional_value: float = 0.0
    weight: float = 0.0            # % of portfolio
    contribution_to_risk: float = 0.0


@dataclass
class PortfolioSnapshot:
    """Complete portfolio risk snapshot."""
    timestamp: str
    total_equity: float
    available_margin: float
    total_exposure: float
    net_exposure: float            # long - short
    gross_exposure: float          # long + short
    long_exposure: float
    short_exposure: float
    long_count: int
    short_count: int
    total_positions: int
    leverage_used: float           # weighted average
    margin_usage_pct: float
    total_unrealized_pnl: float
    total_risk_amount: float
    portfolio_risk_pct: float
    risk_level: str
    symbol_concentration: float    # max single symbol %
    sector_exposures: dict
    correlation_risk: float        # 0-1
    positions: list


@dataclass
class RiskLimits:
    """Hard risk limits."""
    max_risk_per_trade: float = 0.01       # 1%
    max_portfolio_risk: float = 0.05       # 5%
    max_drawdown: float = 0.10             # 10%
    max_daily_loss: float = 0.03           # 3%
    max_weekly_loss: float = 0.05          # 5%
    max_monthly_loss: float = 0.10         # 10%
    max_positions: int = 10
    max_leverage: int = 20
    max_single_position: float = 0.20      # 20% of equity
    max_sector_exposure: float = 0.60      # 60% in one sector
    max_symbol_exposure: float = 0.15      # 15% in one symbol
    max_correlation_exposure: float = 0.70 # 70% in correlated group
    max_margin_usage: float = 0.80         # 80% margin


# ─── Portfolio Risk Engine ───────────────────────────────────────────────────
class PortfolioRiskEngine:
    """
    Real-time portfolio risk monitoring and enforcement.

    Usage:
        engine = PortfolioRiskEngine(equity=10000)
        engine.update_position(pos)
        snapshot = engine.get_snapshot()
        violations = engine.check_limits()
    """

    def __init__(self, equity: float = 10000.0, limits: Optional[RiskLimits] = None):
        self._equity = equity
        self._peak_equity = equity
        self._limits = limits or RiskLimits()
        self._positions: dict[str, PositionRisk] = {}
        self._daily_pnl = 0.0
        self._weekly_pnl = 0.0
        self._monthly_pnl = 0.0
        self._trade_history: list[dict] = []
        self._snapshots: list[dict] = []
        self._violations: list[dict] = []
        self._correlation_matrix: dict[str, dict[str, float]] = {}

        # Known crypto correlations (simplified)
        self._default_correlations = {
            "BTCUSDT": {"ETHUSDT": 0.85, "BNBUSDT": 0.70, "SOLUSDT": 0.75},
            "ETHUSDT": {"BTCUSDT": 0.85, "BNBUSDT": 0.65, "SOLUSDT": 0.70},
            "SOLUSDT": {"BTCUSDT": 0.75, "ETHUSDT": 0.70, "AVAXUSDT": 0.60},
        }

        logger.info("PortfolioRiskEngine initialized: equity=$%.2f", equity)

    # ── Position Management ──────────────────────────────────────────────────
    def update_position(self, pos: PositionRisk):
        """Add or update a position."""
        # Calculate derived metrics
        pos.notional_value = pos.quantity * pos.current_price
        pos.unrealized_pnl = self._calc_pnl(pos)
        pos.risk_amount = abs(pos.quantity * (pos.entry_price - pos.stop_loss))
        pos.margin_used = pos.notional_value / pos.leverage if pos.leverage > 0 else pos.notional_value
        pos.weight = pos.notional_value / self._equity if self._equity > 0 else 0

        self._positions[pos.symbol] = pos
        logger.debug("Updated position: %s %s qty=%.4f pnl=%.2f",
                      pos.side, pos.symbol, pos.quantity, pos.unrealized_pnl)

    def remove_position(self, symbol: str):
        """Remove a closed position."""
        self._positions.pop(symbol, None)

    def update_price(self, symbol: str, price: float):
        """Update current price for a position."""
        if symbol in self._positions:
            pos = self._positions[symbol]
            pos.current_price = price
            pos.unrealized_pnl = self._calc_pnl(pos)
            pos.notional_value = pos.quantity * price
            pos.weight = pos.notional_value / self._equity if self._equity > 0 else 0

    def update_equity(self, equity: float):
        """Update total equity."""
        self._equity = equity
        self._peak_equity = max(self._peak_equity, equity)

    def record_trade(self, pnl: float):
        """Record a completed trade PnL."""
        self._daily_pnl += pnl
        self._weekly_pnl += pnl
        self._monthly_pnl += pnl
        self._trade_history.append({
            "pnl": pnl,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── Snapshot ─────────────────────────────────────────────────────────────
    def get_snapshot(self) -> PortfolioSnapshot:
        """Generate complete portfolio risk snapshot."""
        positions = list(self._positions.values())

        long_exposure = sum(p.notional_value for p in positions if p.side == "LONG")
        short_exposure = sum(p.notional_value for p in positions if p.side == "SHORT")
        total_exposure = long_exposure + short_exposure
        net_exposure = long_exposure - short_exposure

        long_count = sum(1 for p in positions if p.side == "LONG")
        short_count = sum(1 for p in positions if p.side == "SHORT")

        total_unrealized = sum(p.unrealized_pnl for p in positions)
        total_risk = sum(p.risk_amount for p in positions)
        total_margin = sum(p.margin_used for p in positions)

        # Weighted average leverage
        if total_exposure > 0:
            avg_leverage = sum(
                p.notional_value * p.leverage for p in positions
            ) / total_exposure
        else:
            avg_leverage = 0

        # Margin usage
        margin_pct = total_margin / self._equity if self._equity > 0 else 0

        # Portfolio risk %
        risk_pct = total_risk / self._equity if self._equity > 0 else 0

        # Symbol concentration (max single position weight)
        max_weight = max((p.weight for p in positions), default=0)

        # Sector exposures
        sector_exp = {}
        for p in positions:
            sector_exp[p.sector] = sector_exp.get(p.sector, 0) + p.notional_value

        # Correlation risk
        corr_risk = self._calculate_correlation_risk(positions)

        # Risk level
        risk_level = self._determine_risk_level(risk_pct, margin_pct, max_weight)

        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_equity=self._equity,
            available_margin=self._equity - total_margin,
            total_exposure=total_exposure,
            net_exposure=net_exposure,
            gross_exposure=total_exposure,
            long_exposure=long_exposure,
            short_exposure=short_exposure,
            long_count=long_count,
            short_count=short_count,
            total_positions=len(positions),
            leverage_used=round(avg_leverage, 2),
            margin_usage_pct=round(margin_pct * 100, 2),
            total_unrealized_pnl=round(total_unrealized, 2),
            total_risk_amount=round(total_risk, 2),
            portfolio_risk_pct=round(risk_pct * 100, 4),
            risk_level=risk_level.value,
            symbol_concentration=round(max_weight * 100, 2),
            sector_exposures={k: round(v, 2) for k, v in sector_exp.items()},
            correlation_risk=round(corr_risk, 4),
            positions=[asdict(p) for p in positions],
        )

        self._snapshots.append(asdict(snapshot))
        self._save_state()
        return snapshot

    # ── Limit Checking ───────────────────────────────────────────────────────
    def check_limits(self) -> list[dict]:
        """Check all risk limits and return violations."""
        snapshot = self.get_snapshot()
        violations = []

        # Max portfolio risk
        if snapshot.portfolio_risk_pct > self._limits.max_portfolio_risk * 100:
            violations.append({
                "limit": "max_portfolio_risk",
                "current": snapshot.portfolio_risk_pct,
                "limit_value": self._limits.max_portfolio_risk * 100,
                "severity": "CRITICAL",
            })

        # Max drawdown
        dd = self._get_drawdown()
        if dd > self._limits.max_drawdown:
            violations.append({
                "limit": "max_drawdown",
                "current": round(dd * 100, 2),
                "limit_value": self._limits.max_drawdown * 100,
                "severity": "BREACH",
            })

        # Max daily loss
        if self._daily_pnl < 0:
            daily_loss_pct = abs(self._daily_pnl) / self._equity if self._equity > 0 else 0
            if daily_loss_pct > self._limits.max_daily_loss:
                violations.append({
                    "limit": "max_daily_loss",
                    "current": round(daily_loss_pct * 100, 2),
                    "limit_value": self._limits.max_daily_loss * 100,
                    "severity": "HIGH",
                })

        # Max weekly loss
        if self._weekly_pnl < 0:
            weekly_loss_pct = abs(self._weekly_pnl) / self._equity if self._equity > 0 else 0
            if weekly_loss_pct > self._limits.max_weekly_loss:
                violations.append({
                    "limit": "max_weekly_loss",
                    "current": round(weekly_loss_pct * 100, 2),
                    "limit_value": self._limits.max_weekly_loss * 100,
                    "severity": "CRITICAL",
                })

        # Max monthly loss
        if self._monthly_pnl < 0:
            monthly_loss_pct = abs(self._monthly_pnl) / self._equity if self._equity > 0 else 0
            if monthly_loss_pct > self._limits.max_monthly_loss:
                violations.append({
                    "limit": "max_monthly_loss",
                    "current": round(monthly_loss_pct * 100, 2),
                    "limit_value": self._limits.max_monthly_loss * 100,
                    "severity": "BREACH",
                })

        # Max positions
        if snapshot.total_positions > self._limits.max_positions:
            violations.append({
                "limit": "max_positions",
                "current": snapshot.total_positions,
                "limit_value": self._limits.max_positions,
                "severity": "HIGH",
            })

        # Max leverage
        if snapshot.leverage_used > self._limits.max_leverage:
            violations.append({
                "limit": "max_leverage",
                "current": snapshot.leverage_used,
                "limit_value": self._limits.max_leverage,
                "severity": "CRITICAL",
            })

        # Max single position
        if snapshot.symbol_concentration > self._limits.max_single_position * 100:
            violations.append({
                "limit": "max_single_position",
                "current": snapshot.symbol_concentration,
                "limit_value": self._limits.max_single_position * 100,
                "severity": "HIGH",
            })

        # Max sector exposure
        for sector, value in snapshot.sector_exposures.items():
            sector_pct = value / self._equity * 100 if self._equity > 0 else 0
            if sector_pct > self._limits.max_sector_exposure * 100:
                violations.append({
                    "limit": "max_sector_exposure",
                    "sector": sector,
                    "current": round(sector_pct, 2),
                    "limit_value": self._limits.max_sector_exposure * 100,
                    "severity": "HIGH",
                })

        # Max margin usage
        if snapshot.margin_usage_pct > self._limits.max_margin_usage * 100:
            violations.append({
                "limit": "max_margin_usage",
                "current": snapshot.margin_usage_pct,
                "limit_value": self._limits.max_margin_usage * 100,
                "severity": "CRITICAL",
            })

        # Max correlation exposure
        if snapshot.correlation_risk > self._limits.max_correlation_exposure:
            violations.append({
                "limit": "max_correlation_exposure",
                "current": snapshot.correlation_risk,
                "limit_value": self._limits.max_correlation_exposure,
                "severity": "HIGH",
            })

        if violations:
            self._violations.extend(violations)
            logger.warning("Risk violations detected: %d", len(violations))

        return violations

    # ── Can Open Position ─────────────────────────────────────────────────────
    def can_open_position(
        self, symbol: str, side: str, notional: float, leverage: int, sector: str = "CRYPTO"
    ) -> tuple[bool, list[str]]:
        """Check if a new position can be opened within risk limits."""
        reasons = []

        # Position count
        if len(self._positions) >= self._limits.max_positions:
            reasons.append(f"Max positions reached ({self._limits.max_positions})")

        # Single position size
        weight = notional / self._equity if self._equity > 0 else 0
        if weight > self._limits.max_single_position:
            reasons.append(f"Position too large: {weight:.1%} > {self._limits.max_single_position:.1%}")

        # Total exposure
        current_exposure = sum(p.notional_value for p in self._positions.values())
        new_total = current_exposure + notional
        if new_total > self._equity * 2:  # Max 200% gross exposure
            reasons.append(f"Total exposure too high: ${new_total:,.0f}")

        # Margin check
        current_margin = sum(p.margin_used for p in self._positions.values())
        new_margin = notional / leverage if leverage > 0 else notional
        if (current_margin + new_margin) > self._equity * self._limits.max_margin_usage:
            reasons.append("Insufficient margin")

        # Drawdown throttle
        dd = self._get_drawdown()
        if dd > self._limits.max_drawdown * 0.8:  # 80% of max drawdown
            reasons.append(f"Drawdown too high: {dd:.1%}")

        # Sector concentration
        sector_exposure = sum(
            p.notional_value for p in self._positions.values() if p.sector == sector
        )
        if (sector_exposure + notional) / self._equity > self._limits.max_sector_exposure:
            reasons.append(f"Sector concentration too high: {sector}")

        # Symbol duplicate
        if symbol in self._positions:
            existing = self._positions[symbol]
            if existing.side == side:
                reasons.append(f"Already have {side} position in {symbol}")

        can_open = len(reasons) == 0
        return can_open, reasons

    # ── Correlation ──────────────────────────────────────────────────────────
    def _calculate_correlation_risk(self, positions: list[PositionRisk]) -> float:
        """Calculate portfolio correlation risk (0-1)."""
        if len(positions) < 2:
            return 0.0

        symbols = [p.symbol for p in positions]
        total_corr = 0.0
        pairs = 0

        for i, s1 in enumerate(symbols):
            for s2 in symbols[i + 1:]:
                corr = self._get_correlation(s1, s2)
                # Weight by position sizes
                w1 = positions[i].weight
                w2 = next(p.weight for p in positions if p.symbol == s2)
                total_corr += abs(corr) * w1 * w2
                pairs += 1

        if pairs > 0:
            return min(total_corr / pairs * 10, 1.0)  # Normalize
        return 0.0

    def _get_correlation(self, s1: str, s2: str) -> float:
        """Get correlation between two symbols."""
        if s1 in self._correlation_matrix and s2 in self._correlation_matrix[s1]:
            return self._correlation_matrix[s1][s2]
        if s1 in self._default_correlations and s2 in self._default_correlations[s1]:
            return self._default_correlations[s1][s2]
        return 0.5  # Default assumption: moderate correlation

    def set_correlation(self, s1: str, s2: str, corr: float):
        """Set correlation between two symbols."""
        if s1 not in self._correlation_matrix:
            self._correlation_matrix[s1] = {}
        if s2 not in self._correlation_matrix:
            self._correlation_matrix[s2] = {}
        self._correlation_matrix[s1][s2] = corr
        self._correlation_matrix[s2][s1] = corr

    # ── Risk Level ───────────────────────────────────────────────────────────
    def _determine_risk_level(
        self, risk_pct: float, margin_pct: float, concentration: float
    ) -> RiskLevel:
        """Determine overall risk level."""
        score = 0

        # Risk percentage scoring
        if risk_pct > 5:
            score += 3
        elif risk_pct > 3:
            score += 2
        elif risk_pct > 1:
            score += 1

        # Margin scoring
        if margin_pct > 80:
            score += 3
        elif margin_pct > 60:
            score += 2
        elif margin_pct > 40:
            score += 1

        # Concentration scoring
        if concentration > 30:
            score += 2
        elif concentration > 20:
            score += 1

        # Drawdown scoring
        dd = self._get_drawdown()
        if dd > 8:
            score += 3
        elif dd > 5:
            score += 2
        elif dd > 2:
            score += 1

        if score >= 8:
            return RiskLevel.BREACH
        elif score >= 6:
            return RiskLevel.CRITICAL
        elif score >= 4:
            return RiskLevel.HIGH
        elif score >= 2:
            return RiskLevel.ELEVATED
        return RiskLevel.NORMAL

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _calc_pnl(self, pos: PositionRisk) -> float:
        """Calculate unrealized PnL."""
        if pos.side == "LONG":
            return pos.quantity * (pos.current_price - pos.entry_price)
        else:
            return pos.quantity * (pos.entry_price - pos.current_price)

    def _get_drawdown(self) -> float:
        """Current drawdown from peak equity."""
        if self._peak_equity <= 0:
            return 0.0
        return (self._peak_equity - self._equity) / self._peak_equity

    # ── Time Resetters ───────────────────────────────────────────────────────
    def reset_daily(self):
        """Reset daily PnL (call at midnight UTC)."""
        self._daily_pnl = 0.0

    def reset_weekly(self):
        """Reset weekly PnL (call Monday midnight UTC)."""
        self._weekly_pnl = 0.0

    def reset_monthly(self):
        """Reset monthly PnL (call 1st of month)."""
        self._monthly_pnl = 0.0

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save portfolio state."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "equity": self._equity,
            "peak_equity": self._peak_equity,
            "daily_pnl": self._daily_pnl,
            "weekly_pnl": self._weekly_pnl,
            "monthly_pnl": self._monthly_pnl,
            "position_count": len(self._positions),
            "positions": {k: asdict(v) for k, v in self._positions.items()},
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "portfolio_state.json").write_text(json.dumps(state, indent=2, default=str))

    def get_stats(self) -> dict:
        """Get portfolio risk statistics."""
        snap = self.get_snapshot()
        return {
            "equity": self._equity,
            "positions": snap.total_positions,
            "risk_pct": snap.portfolio_risk_pct,
            "risk_level": snap.risk_level,
            "margin_usage": snap.margin_usage_pct,
            "long_exposure": snap.long_exposure,
            "short_exposure": snap.short_exposure,
            "net_exposure": snap.net_exposure,
            "leverage": snap.leverage_used,
            "correlation_risk": snap.correlation_risk,
            "drawdown": round(self._get_drawdown() * 100, 2),
            "daily_pnl": self._daily_pnl,
            "violations": len(self._violations),
        }
