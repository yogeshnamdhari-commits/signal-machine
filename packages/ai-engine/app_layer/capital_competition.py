"""
Capital Competition Allocator — Proportional capital allocation by expected value.

Per Executive Assessment v6:
    "The system still evaluates trades individually.
     I would introduce capital competition.

     Five eligible trades:
         Symbol Expected Return
         BTC 4.8R
         ETH 2.9R
         SOL 2.5R
         XRP 1.6R
         DOGE 1.2R

     Instead of simply taking the top N, allocate capital proportionally
     to expected value.

     Example:
         BTC: 40%
         ETH: 25%
         SOL: 20%
         XRP: 10%
         DOGE: 5%

     This uses your capital more efficiently without changing the
     underlying signal engine."

Key Features:
    1. Expected Value Ranking — rank signals by expected return
    2. Proportional Allocation — capital split by EV, not equal
    3. Minimum Threshold — reject signals below minimum EV
    4. Maximum Position Size — cap single position exposure
    5. Portfolio Heat Management — total risk within limits
    6. Dynamic Count — execute 1-5 signals based on opportunity quality

READ-ONLY: Returns allocation decisions for execution layer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# ALLOCATION CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Maximum simultaneous positions
MAX_POSITIONS = 5

# Minimum expected return to consider (R-multiples)
MIN_EXPECTED_R = 0.5

# Maximum single position as % of capital
MAX_SINGLE_POSITION_PCT = 25.0

# Minimum single position as % of capital
MIN_SINGLE_POSITION_PCT = 5.0

# Total portfolio heat limit (% of capital at risk)
MAX_PORTFOLIO_HEAT_PCT = 10.0

# Opportunity density thresholds
HIGH_OPPORTUNITY_COUNT = 5    # 5+ signals = high density
MEDIUM_OPPORTUNITY_COUNT = 3  # 3-4 signals = medium
LOW_OPPORTUNITY_COUNT = 1     # 1-2 signals = low


@dataclass
class SignalAllocation:
    """Allocation for a single signal."""
    symbol: str = ""
    side: str = ""
    expected_r: float = 0.0
    allocation_pct: float = 0.0     # % of capital allocated
    allocation_usd: float = 0.0     # USD amount allocated
    position_size: float = 0.0      # Number of units
    priority: int = 0               # Rank (1 = highest)
    execution_score: float = 0.0    # Execution eligibility score
    rejected: bool = False
    rejection_reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "expected_r": round(self.expected_r, 3),
            "allocation_pct": round(self.allocation_pct, 1),
            "allocation_usd": round(self.allocation_usd, 2),
            "position_size": round(self.position_size, 6),
            "priority": self.priority,
            "execution_score": round(self.execution_score, 1),
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
        }


@dataclass
class CapitalCompetitionResult:
    """Result from capital competition allocation."""
    timestamp: float = 0.0
    total_signals: int = 0
    allocated_count: int = 0
    rejected_count: int = 0
    total_allocation_pct: float = 0.0
    total_allocation_usd: float = 0.0
    portfolio_heat_pct: float = 0.0
    opportunity_density: str = ""  # HIGH / MEDIUM / LOW
    allocations: List[SignalAllocation] = field(default_factory=list)
    rejected: List[SignalAllocation] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "total_signals": self.total_signals,
            "allocated_count": self.allocated_count,
            "rejected_count": self.rejected_count,
            "total_allocation_pct": round(self.total_allocation_pct, 1),
            "total_allocation_usd": round(self.total_allocation_usd, 2),
            "portfolio_heat_pct": round(self.portfolio_heat_pct, 1),
            "opportunity_density": self.opportunity_density,
            "allocations": [a.to_dict() for a in self.allocations],
        }


class CapitalCompetitionAllocator:
    """
    Allocates capital proportionally to expected value.

    Per Executive Assessment v6:
        "Instead of simply taking the top N, allocate capital
         proportionally to expected value."

    This engine:
        1. Ranks signals by expected return
        2. Calculates proportional allocation (% of capital)
        3. Enforces minimum/maximum position sizes
        4. Manages portfolio heat limits
        5. Adaptively determines how many signals to execute

    READ-ONLY: Returns allocation decisions for execution layer.
    """

    def __init__(self) -> None:
        pass

    def allocate(
        self,
        signals: List[Dict[str, Any]],
        balance: float = 10_000.0,
        open_positions: Optional[List[Dict]] = None,
    ) -> CapitalCompetitionResult:
        """
        Allocate capital proportionally to expected value.

        Args:
            signals: List of signal dicts with expected_r field
            balance: Account balance in USD
            open_positions: Current open positions

        Returns:
            CapitalCompetitionResult with allocations
        """
        result = CapitalCompetitionResult(timestamp=time.time())
        positions = open_positions or []

        if not signals:
            return result

        result.total_signals = len(signals)

        # ── Filter by minimum expected return ──
        eligible = []
        for sig in signals:
            expected_r = sig.get("expected_r", sig.get("risk_reward", 0))
            exec_score = sig.get("execution_score", sig.get("_execution_score", 0))

            if expected_r < MIN_EXPECTED_R:
                result.rejected.append(SignalAllocation(
                    symbol=sig.get("symbol", ""),
                    side=sig.get("side", ""),
                    expected_r=expected_r,
                    rejected=True,
                    rejection_reason=f"expected_r={expected_r:.2f} < {MIN_EXPECTED_R}",
                ))
                continue

            eligible.append({
                "symbol": sig.get("symbol", ""),
                "side": sig.get("side", ""),
                "expected_r": expected_r,
                "execution_score": exec_score,
                "signal": sig,
            })

        # ── Rank by expected return ──
        eligible.sort(key=lambda x: x["expected_r"], reverse=True)

        # ── Determine how many to execute ──
        available_slots = MAX_POSITIONS - len(positions)
        max_execute = min(available_slots, len(eligible))

        # Adjust by opportunity density
        if len(eligible) >= HIGH_OPPORTUNITY_COUNT:
            result.opportunity_density = "HIGH"
            # High density — be more selective, take top 3
            max_execute = min(max_execute, 3)
        elif len(eligible) >= MEDIUM_OPPORTUNITY_COUNT:
            result.opportunity_density = "MEDIUM"
            max_execute = min(max_execute, 3)
        else:
            result.opportunity_density = "LOW"
            max_execute = min(max_execute, 2)

        # ── Calculate proportional allocation ──
        to_allocate = eligible[:max_execute]

        if not to_allocate:
            return result

        # Total expected R for proportional calculation
        total_expected_r = sum(s["expected_r"] for s in to_allocate)

        # Current heat from open positions
        current_heat = sum(
            abs(p.get("position_value", 0) or 0) / balance * 100
            for p in positions
        )
        remaining_heat = MAX_PORTFOLIO_HEAT_PCT - current_heat

        allocated_usd_total = 0.0

        for i, sig in enumerate(to_allocate):
            expected_r = sig["expected_r"]

            # Proportional allocation (% of capital)
            if total_expected_r > 0:
                raw_pct = (expected_r / total_expected_r) * 100
            else:
                raw_pct = 100 / max(1, len(to_allocate))

            # Clamp to min/max
            alloc_pct = max(MIN_SINGLE_POSITION_PCT, min(MAX_SINGLE_POSITION_PCT, raw_pct))

            # Check portfolio heat
            alloc_usd = balance * alloc_pct / 100
            if allocated_usd_total + alloc_usd > balance * remaining_heat / 100:
                # Reduce to fit within heat limit
                remaining_usd = (balance * remaining_heat / 100) - allocated_usd_total
                if remaining_usd <= 0:
                    result.rejected.append(SignalAllocation(
                        symbol=sig["symbol"],
                        side=sig["side"],
                        expected_r=expected_r,
                        rejected=True,
                        rejection_reason="portfolio_heat_limit",
                    ))
                    continue
                alloc_usd = remaining_usd
                alloc_pct = alloc_usd / balance * 100

            allocation = SignalAllocation(
                symbol=sig["symbol"],
                side=sig["side"],
                expected_r=expected_r,
                allocation_pct=alloc_pct,
                allocation_usd=alloc_usd,
                position_size=alloc_usd / max(0.01, sig.get("signal", {}).get("entry_price", 1)),
                priority=i + 1,
                execution_score=sig.get("execution_score", 0),
            )

            result.allocations.append(allocation)
            allocated_usd_total += alloc_usd

        # ── Reject remaining signals ──
        for sig in eligible[max_execute:]:
            result.rejected.append(SignalAllocation(
                symbol=sig["symbol"],
                side=sig["side"],
                expected_r=sig["expected_r"],
                rejected=True,
                rejection_reason="not_in_top_n",
            ))

        # ── Update totals ──
        result.allocated_count = len(result.allocations)
        result.rejected_count = len(result.rejected)
        result.total_allocation_pct = sum(a.allocation_pct for a in result.allocations)
        result.total_allocation_usd = allocated_usd_total
        result.portfolio_heat_pct = current_heat + (allocated_usd_total / balance * 100)

        logger.info(
            "📊 CAPITAL COMPETITION: {} signals → {} allocated ({:.1f}% of capital) "
            "density={}",
            result.total_signals, result.allocated_count,
            result.total_allocation_pct, result.opportunity_density,
        )

        return result

    def calculate_kelly_fraction(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """
        Calculate Kelly Criterion fraction for optimal bet sizing.

        Kelly % = (W × A - L) / A
        Where:
            W = win rate
            A = avg win / avg loss (payoff ratio)
            L = 1 - win rate

        Returns fraction (0-1) of capital to risk.
        """
        if avg_loss <= 0 or win_rate <= 0:
            return 0.0

        payoff_ratio = avg_win / avg_loss
        kelly = (win_rate * payoff_ratio - (1 - win_rate)) / payoff_ratio

        # Use half-Kelly for safety
        return max(0, min(0.5, kelly / 2))
