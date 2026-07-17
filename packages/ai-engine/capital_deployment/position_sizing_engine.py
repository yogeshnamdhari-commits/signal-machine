"""
Position Sizing Engine — 7 Institutional Sizing Methods
========================================================
Implements: Fixed Fractional, Volatility Adjusted, ATR Based,
            Kelly Fraction, Half-Kelly, Risk Parity, Max Exposure Control
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

# ─── Data Path ───────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data" / "capital"


# ─── Enums ───────────────────────────────────────────────────────────────────
class SizingMethod(Enum):
    FIXED_FRACTIONAL = "fixed_fractional"
    VOLATILITY_ADJUSTED = "volatility_adjusted"
    ATR_BASED = "atr_based"
    KELLY_FRACTION = "kelly_fraction"
    HALF_KELLY = "half_kelly"
    RISK_PARITY = "risk_parity"
    MAX_EXPOSURE = "max_exposure"


# ─── Data Classes ────────────────────────────────────────────────────────────
@dataclass
class SizingRequest:
    """Input for position sizing calculation."""
    symbol: str
    signal_id: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    stop_loss_price: float
    current_price: float = 0.0
    confidence: float = 0.5          # 0-1 signal confidence
    atr: float = 0.0                 # ATR value
    volatility: float = 0.0          # Annualized volatility
    win_rate: float = 0.5            # Historical win rate
    payoff_ratio: float = 1.5        # Avg win / Avg loss
    current_positions: int = 0       # Number of open positions
    total_exposure: float = 0.0      # Current total exposure in USD
    leverage: int = 1
    sector: str = "CRYPTO"
    correlation_group: str = "default"

    def __post_init__(self):
        if self.current_price <= 0:
            self.current_price = self.entry_price


@dataclass
class SizingResult:
    """Output of position sizing calculation."""
    symbol: str
    signal_id: str
    method: str
    recommended_quantity: float
    position_value_usd: float
    margin_required: float
    risk_amount_usd: float
    risk_percent: float
    risk_reward_ratio: float
    confidence_adjusted: bool
    tier_adjusted: bool
    capped: bool
    cap_reason: str
    metadata: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class PortfolioState:
    """Current portfolio state for sizing decisions."""
    total_equity: float
    available_margin: float
    open_positions: int
    total_exposure: float
    current_drawdown: float
    daily_pnl: float
    weekly_pnl: float
    monthly_pnl: float
    risk_level: str = "NORMAL"
    tier: int = 0
    symbol_exposures: dict = field(default_factory=dict)
    sector_exposures: dict = field(default_factory=dict)


# ─── Position Sizing Engine ──────────────────────────────────────────────────
class PositionSizingEngine:
    """
    Institutional-grade position sizing with 7 methods.

    Usage:
        engine = PositionSizingEngine()
        result = engine.calculate(request, portfolio, method=SizingMethod.HALF_KELLY)
    """

    # ── Default limits ───────────────────────────────────────────────────────
    DEFAULT_MAX_RISK_PER_TRADE = 0.01       # 1%
    DEFAULT_MAX_POSITION_SIZE = 0.20        # 20% of equity
    DEFAULT_MAX_LEVERAGE = 20
    DEFAULT_MAX_EXPOSURE = 1.0              # 100% of equity
    DEFAULT_MIN_POSITION_USD = 10.0
    DEFAULT_MAX_POSITIONS = 10
    DEFAULT_KELLY_CAP = 0.25               # 25% max Kelly
    DEFAULT_FEE_RATE = 0.0004              # 0.04% taker fee

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.max_risk_per_trade = self.config.get("max_risk_per_trade", self.DEFAULT_MAX_RISK_PER_TRADE)
        self.max_position_size = self.config.get("max_position_size", self.DEFAULT_MAX_POSITION_SIZE)
        self.max_leverage = self.config.get("max_leverage", self.DEFAULT_MAX_LEVERAGE)
        self.max_exposure = self.config.get("max_exposure", self.DEFAULT_MAX_EXPOSURE)
        self.min_position_usd = self.config.get("min_position_usd", self.DEFAULT_MIN_POSITION_USD)
        self.max_positions = self.config.get("max_positions", self.DEFAULT_MAX_POSITIONS)
        self.kelly_cap = self.config.get("kelly_cap", self.DEFAULT_KELLY_CAP)
        self.fee_rate = self.config.get("fee_rate", self.DEFAULT_FEE_RATE)
        self._history: list[dict] = []
        logger.info("PositionSizingEngine initialized: max_risk=%.2f%%, max_pos=%.2f%%",
                     self.max_risk_per_trade * 100, self.max_position_size * 100)

    # ── Main Entry Point ─────────────────────────────────────────────────────
    def calculate(
        self,
        request: SizingRequest,
        portfolio: PortfolioState,
        method: SizingMethod = SizingMethod.HALF_KELLY,
    ) -> SizingResult:
        """Calculate position size using the specified method."""
        # Validate inputs
        self._validate_request(request, portfolio)

        # Calculate raw size by method
        if method == SizingMethod.FIXED_FRACTIONAL:
            raw = self._fixed_fractional(request, portfolio)
        elif method == SizingMethod.VOLATILITY_ADJUSTED:
            raw = self._volatility_adjusted(request, portfolio)
        elif method == SizingMethod.ATR_BASED:
            raw = self._atr_based(request, portfolio)
        elif method == SizingMethod.KELLY_FRACTION:
            raw = self._kelly_fraction(request, portfolio)
        elif method == SizingMethod.HALF_KELLY:
            raw = self._half_kelly(request, portfolio)
        elif method == SizingMethod.RISK_PARITY:
            raw = self._risk_parity(request, portfolio)
        elif method == SizingMethod.MAX_EXPOSURE:
            raw = self._max_exposure_control(request, portfolio)
        else:
            raise ValueError(f"Unknown sizing method: {method}")

        # Apply confidence adjustment
        quantity, confidence_adj = self._apply_confidence(raw["quantity"], request.confidence)

        # Apply tier adjustment
        quantity, tier_adj = self._apply_tier_limits(quantity, request.entry_price, portfolio)

        # Apply hard caps
        quantity, capped, cap_reason = self._apply_hard_caps(
            quantity, request, portfolio
        )

        # Calculate derived values
        position_value = quantity * request.entry_price
        risk_per_unit = abs(request.entry_price - request.stop_loss_price)
        risk_amount = quantity * risk_per_unit
        risk_pct = risk_amount / portfolio.total_equity if portfolio.total_equity > 0 else 0
        margin = position_value / request.leverage if request.leverage > 0 else position_value

        # Risk-reward ratio
        rr = risk_per_unit / risk_per_unit if risk_per_unit > 0 else 0
        # Estimate reward from stop distance and typical R:R
        rr = request.payoff_ratio if request.payoff_ratio > 0 else 1.5

        result = SizingResult(
            symbol=request.symbol,
            signal_id=request.signal_id,
            method=method.value,
            recommended_quantity=round(quantity, 8),
            position_value_usd=round(position_value, 2),
            margin_required=round(margin, 2),
            risk_amount_usd=round(risk_amount, 2),
            risk_percent=round(risk_pct * 100, 4),
            risk_reward_ratio=round(rr, 2),
            confidence_adjusted=confidence_adj,
            tier_adjusted=tier_adj,
            capped=capped,
            cap_reason=cap_reason,
            metadata=raw.get("metadata", {}),
        )

        self._history.append(asdict(result))
        self._save_state()
        return result

    # ── Method 1: Fixed Fractional ───────────────────────────────────────────
    def _fixed_fractional(self, req: SizingRequest, port: PortfolioState) -> dict:
        """Risk a fixed fraction of equity per trade."""
        risk_per_unit = abs(req.entry_price - req.stop_loss_price)
        if risk_per_unit <= 0:
            return {"quantity": 0, "metadata": {"reason": "zero_risk_distance"}}

        max_risk_usd = port.total_equity * self.max_risk_per_trade
        quantity = max_risk_usd / risk_per_unit

        return {
            "quantity": quantity,
            "metadata": {
                "risk_per_unit": risk_per_unit,
                "max_risk_usd": max_risk_usd,
                "equity": port.total_equity,
            }
        }

    # ── Method 2: Volatility Adjusted ────────────────────────────────────────
    def _volatility_adjusted(self, req: SizingRequest, port: PortfolioState) -> dict:
        """Size inversely proportional to volatility."""
        vol = req.volatility if req.volatility > 0 else self._estimate_volatility(req)
        if vol <= 0:
            return {"quantity": 0, "metadata": {"reason": "zero_volatility"}}

        # Target risk per trade as % of equity
        target_risk = port.total_equity * self.max_risk_per_trade

        # Vol-adjusted: reduce size when vol is high
        # Normalized to ~20% daily vol baseline
        vol_scalar = 0.20 / vol if vol > 0 else 1.0
        vol_scalar = min(vol_scalar, 3.0)  # Cap at 3x

        risk_per_unit = abs(req.entry_price - req.stop_loss_price)
        if risk_per_unit <= 0:
            risk_per_unit = req.entry_price * vol / math.sqrt(365)

        quantity = (target_risk * vol_scalar) / risk_per_unit

        return {
            "quantity": quantity,
            "metadata": {
                "volatility": vol,
                "vol_scalar": vol_scalar,
                "target_risk": target_risk,
            }
        }

    # ── Method 3: ATR Based ──────────────────────────────────────────────────
    def _atr_based(self, req: SizingRequest, port: PortfolioState) -> dict:
        """Size based on ATR (Average True Range)."""
        atr = req.atr if req.atr > 0 else self._estimate_atr(req)
        if atr <= 0:
            return {"quantity": 0, "metadata": {"reason": "zero_atr"}}

        # Risk budget
        risk_budget = port.total_equity * self.max_risk_per_trade

        # ATR stop distance (typically 2x ATR)
        atr_stop = atr * 2.0

        # Use max of ATR stop and actual stop distance
        stop_distance = max(atr_stop, abs(req.entry_price - req.stop_loss_price))

        quantity = risk_budget / stop_distance if stop_distance > 0 else 0

        return {
            "quantity": quantity,
            "metadata": {
                "atr": atr,
                "atr_stop": atr_stop,
                "stop_distance": stop_distance,
                "risk_budget": risk_budget,
            }
        }

    # ── Method 4: Kelly Fraction ─────────────────────────────────────────────
    def _kelly_fraction(self, req: SizingRequest, port: PortfolioState) -> dict:
        """Kelly Criterion optimal sizing."""
        kelly = self._calculate_kelly(req.win_rate, req.payoff_ratio)
        kelly = min(kelly, self.kelly_cap)  # Cap Kelly

        # Apply to equity
        kelly_usd = port.total_equity * kelly

        # Convert to quantity
        quantity = kelly_usd / req.entry_price if req.entry_price > 0 else 0

        return {
            "quantity": quantity,
            "metadata": {
                "kelly_raw": self._calculate_kelly(req.win_rate, req.payoff_ratio),
                "kelly_capped": kelly,
                "win_rate": req.win_rate,
                "payoff_ratio": req.payoff_ratio,
                "kelly_usd": kelly_usd,
            }
        }

    # ── Method 5: Half-Kelly ─────────────────────────────────────────────────
    def _half_kelly(self, req: SizingRequest, port: PortfolioState) -> dict:
        """Half-Kelly for conservative sizing (recommended)."""
        kelly = self._calculate_kelly(req.win_rate, req.payoff_ratio)
        half_kelly = min(kelly / 2.0, self.kelly_cap)

        kelly_usd = port.total_equity * half_kelly
        quantity = kelly_usd / req.entry_price if req.entry_price > 0 else 0

        return {
            "quantity": quantity,
            "metadata": {
                "kelly_raw": kelly,
                "half_kelly": half_kelly,
                "win_rate": req.win_rate,
                "payoff_ratio": req.payoff_ratio,
                "kelly_usd": kelly_usd,
            }
        }

    # ── Method 6: Risk Parity ────────────────────────────────────────────────
    def _risk_parity(self, req: SizingRequest, port: PortfolioState) -> dict:
        """Equal risk contribution across positions."""
        n = max(req.current_positions + 1, 1)  # +1 for this new position

        # Each position gets 1/N of the total risk budget
        total_risk_budget = port.total_equity * self.max_risk_per_trade * min(n, self.max_positions)
        per_position_risk = total_risk_budget / n

        risk_per_unit = abs(req.entry_price - req.stop_loss_price)
        if risk_per_unit <= 0:
            return {"quantity": 0, "metadata": {"reason": "zero_risk_distance"}}

        quantity = per_position_risk / risk_per_unit

        return {
            "quantity": quantity,
            "metadata": {
                "positions": n,
                "total_risk_budget": total_risk_budget,
                "per_position_risk": per_position_risk,
                "risk_per_unit": risk_per_unit,
            }
        }

    # ── Method 7: Max Exposure Control ───────────────────────────────────────
    def _max_exposure_control(self, req: SizingRequest, port: PortfolioState) -> dict:
        """Size limited by maximum total exposure."""
        max_total_exposure = port.total_equity * self.max_exposure
        remaining_exposure = max_total_exposure - port.total_exposure

        if remaining_exposure <= 0:
            return {
                "quantity": 0,
                "metadata": {"reason": "max_exposure_reached", "remaining": 0}
            }

        # Also cap per-position
        max_position = port.total_equity * self.max_position_size
        max_for_symbol = max_position * 0.5  # 50% of max per symbol

        # Check existing symbol exposure
        existing = port.symbol_exposures.get(req.symbol, 0)
        available_for_symbol = max_for_symbol - existing

        # Take the minimum
        available = min(remaining_exposure, available_for_symbol, max_position)
        quantity = available / req.entry_price if req.entry_price > 0 else 0

        return {
            "quantity": quantity,
            "metadata": {
                "max_total_exposure": max_total_exposure,
                "remaining_exposure": remaining_exposure,
                "max_position": max_position,
                "available_for_symbol": available_for_symbol,
                "existing_symbol_exposure": existing,
            }
        }

    # ── Helper: Kelly Calculation ─────────────────────────────────────────────
    @staticmethod
    def _calculate_kelly(win_rate: float, payoff_ratio: float) -> float:
        """Calculate Kelly fraction: f* = (bp - q) / b"""
        if payoff_ratio <= 0 or win_rate <= 0 or win_rate >= 1:
            return 0.0
        b = payoff_ratio
        p = win_rate
        q = 1.0 - p
        kelly = (b * p - q) / b
        return max(kelly, 0.0)

    # ── Helper: Confidence Adjustment ─────────────────────────────────────────
    def _apply_confidence(self, quantity: float, confidence: float) -> tuple[float, bool]:
        """Scale size by signal confidence (0.5 = full, 0.0 = zero)."""
        if confidence >= 0.8:
            return quantity, False
        # Linear scale from 0.5 (full) to 0.0 (zero)
        scale = max(confidence / 0.8, 0.0)
        scale = min(scale, 1.0)
        return quantity * scale, True

    # ── Helper: Tier Limits ───────────────────────────────────────────────────
    def _apply_tier_limits(self, quantity: float, price: float, port: PortfolioState) -> tuple[float, bool]:
        """Apply tier-based position limits."""
        tier_limits = {
            0: port.total_equity * 0.0,     # Simulation — no real capital
            1: port.total_equity * 0.25,     # Micro — 25% max position
            2: port.total_equity * 0.20,     # Small — 20% max position
            3: port.total_equity * 0.15,     # Moderate — 15% max position
            4: port.total_equity * self.max_position_size,  # Production
        }

        max_usd = tier_limits.get(port.tier, tier_limits[4])
        max_qty = max_usd / price if price > 0 else 0

        if quantity > max_qty:
            return max_qty, True
        return quantity, False

    # ── Helper: Hard Caps ─────────────────────────────────────────────────────
    def _apply_hard_caps(
        self, quantity: float, req: SizingRequest, port: PortfolioState
    ) -> tuple[float, bool, str]:
        """Apply non-negotiable hard caps."""
        capped = False
        reason = ""

        # Cap 1: Max risk per trade
        risk_per_unit = abs(req.entry_price - req.stop_loss_price)
        max_qty_by_risk = (port.total_equity * self.max_risk_per_trade) / risk_per_unit if risk_per_unit > 0 else 0
        if quantity > max_qty_by_risk:
            quantity = max_qty_by_risk
            capped = True
            reason = "max_risk_per_trade"

        # Cap 2: Max position count
        if req.current_positions >= self.max_positions:
            quantity = 0
            capped = True
            reason = "max_positions_reached"

        # Cap 3: Minimum position size
        position_usd = quantity * req.entry_price
        if 0 < position_usd < self.min_position_usd:
            quantity = 0
            capped = True
            reason = "below_minimum_position"

        # Cap 4: Leverage limit
        max_qty_by_leverage = (port.available_margin * req.leverage) / req.entry_price if req.entry_price > 0 else 0
        if quantity > max_qty_by_leverage:
            quantity = max_qty_by_leverage
            capped = True
            reason = "leverage_limit"

        # Cap 5: Drawdown throttle
        if port.current_drawdown > 0.05:
            throttle = 1.0 - (port.current_drawdown - 0.05) * 5  # Reduce 5% per 1% DD over 5%
            throttle = max(throttle, 0.1)  # Min 10%
            quantity *= throttle
            capped = True
            reason = f"drawdown_throttle_{throttle:.0%}"

        return quantity, capped, reason

    # ── Helper: Estimate Volatility ───────────────────────────────────────────
    @staticmethod
    def _estimate_volatility(req: SizingRequest) -> float:
        """Estimate volatility from stop distance."""
        if req.entry_price > 0:
            return abs(req.entry_price - req.stop_loss_price) / req.entry_price * math.sqrt(365)
        return 0.20  # Default 20%

    # ── Helper: Estimate ATR ──────────────────────────────────────────────────
    @staticmethod
    def _estimate_atr(req: SizingRequest) -> float:
        """Estimate ATR from stop distance."""
        return abs(req.entry_price - req.stop_loss_price) / 2.0

    # ── Validation ────────────────────────────────────────────────────────────
    def _validate_request(self, req: SizingRequest, port: PortfolioState):
        """Validate sizing inputs."""
        if req.entry_price <= 0:
            raise ValueError(f"Invalid entry price: {req.entry_price}")
        if req.stop_loss_price <= 0:
            raise ValueError(f"Invalid stop loss: {req.stop_loss_price}")
        if port.total_equity <= 0:
            raise ValueError(f"Invalid equity: {port.total_equity}")
        if req.leverage > self.max_leverage:
            raise ValueError(f"Leverage {req.leverage} exceeds max {self.max_leverage}")

    # ── Convenience: Calculate All Methods ────────────────────────────────────
    def calculate_all(
        self, request: SizingRequest, portfolio: PortfolioState
    ) -> dict[str, SizingResult]:
        """Calculate position size using all 7 methods for comparison."""
        results = {}
        for method in SizingMethod:
            try:
                results[method.value] = self.calculate(request, portfolio, method)
            except Exception as e:
                logger.error("Sizing method %s failed: %s", method.value, e)
        return results

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save sizing history."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / "sizing_history.json"
        # Keep last 1000
        recent = self._history[-1000:]
        path.write_text(json.dumps(recent, indent=2, default=str))

    def get_stats(self) -> dict:
        """Get sizing engine statistics."""
        if not self._history:
            return {"total_calculations": 0}

        methods_used = {}
        capped_count = 0
        for h in self._history:
            m = h.get("method", "unknown")
            methods_used[m] = methods_used.get(m, 0) + 1
            if h.get("capped"):
                capped_count += 1

        return {
            "total_calculations": len(self._history),
            "methods_used": methods_used,
            "capped_count": capped_count,
            "capped_pct": round(capped_count / len(self._history) * 100, 1),
            "avg_position_value": round(
                sum(h.get("position_value_usd", 0) for h in self._history) / len(self._history), 2
            ),
            "avg_risk_pct": round(
                sum(h.get("risk_percent", 0) for h in self._history) / len(self._history), 4
            ),
        }
