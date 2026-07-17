"""
Portfolio Ranking Engine — Execute only the highest expected-profit signals.

Per Executive Assessment v2:
    "Build an execution queue.
     12 signals → Rank Expected Profit → Take Top 3 → Ignore remaining 9.
     Capital should go to the best opportunities, not every qualifying setup."

Also implements Adaptive Risk:
    "Risk = Expected Profit × Symbol PF × Session PF × Recent Strategy PF"

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from .execution_eligibility import ExecutionEligibilityEngine, EligibilityResult
from .continuous_learning import ContinuousLearningLayer
from .position_sizing_engine import calculate_expected_profit_score


# ═══════════════════════════════════════════════════════════════
# RANKING CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Maximum simultaneous open positions
MAX_OPEN_POSITIONS = 5

# Maximum signals to execute per cycle
MAX_EXECUTIONS_PER_CYCLE = 3

# Minimum gap between executions (seconds)
MIN_EXECUTION_GAP_SEC = 30

# Capital allocation by rank
RANK_ALLOCATIONS = {
    1: 1.5,   # Rank 1: 1.5x base allocation
    2: 1.25,  # Rank 2: 1.25x
    3: 1.0,   # Rank 3: 1.0x
    4: 0.75,  # Rank 4: 0.75x (only if slots available)
    5: 0.5,   # Rank 5: 0.5x (only if slots available)
}


@dataclass
class RankedSignal:
    """A signal with its ranking and capital allocation."""
    signal: Dict[str, Any]
    eligibility: EligibilityResult
    rank: int = 0
    capital_allocation: float = 1.0
    composite_score: float = 0.0
    symbol_pf: float = 0.0
    session_pf: float = 0.0
    strategy_pf: float = 1.0
    adaptive_risk: float = 1.0
    blocked: bool = False
    block_reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.signal.get("symbol", ""),
            "side": self.signal.get("side", ""),
            "rank": self.rank,
            "composite_score": round(self.composite_score, 1),
            "capital_allocation": round(self.capital_allocation, 2),
            "adaptive_risk": round(self.adaptive_risk, 3),
            "symbol_pf": round(self.symbol_pf, 2),
            "session_pf": round(self.session_pf, 2),
            "strategy_pf": round(self.strategy_pf, 2),
            "execution_score": round(self.eligibility.execution_score, 1),
            "blocked": self.blocked,
            "block_reason": self.block_reason,
        }


@dataclass
class RankingResult:
    """Result from the Portfolio Ranking Engine."""
    total_signals: int = 0
    eligible_signals: int = 0
    blocked_signals: int = 0
    executed_count: int = 0
    max_executions: int = MAX_EXECUTIONS_PER_CYCLE
    ranked_signals: List[RankedSignal] = field(default_factory=list)
    executed_signals: List[RankedSignal] = field(default_factory=list)
    rejected_signals: List[RankedSignal] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "total_signals": self.total_signals,
            "eligible_signals": self.eligible_signals,
            "blocked_signals": self.blocked_signals,
            "executed_count": self.executed_count,
            "max_executions": self.max_executions,
            "executed": [r.to_dict() for r in self.executed_signals],
            "rejected": [r.to_dict() for r in self.rejected_signals],
            "timestamp": self.timestamp,
        }


class PortfolioRankingEngine:
    """
    Ranks all signals by expected profit and executes only the best ones.

    Per Executive Assessment v2:
        "12 signals → Rank Expected Profit → Take Top 3 → Ignore remaining 9"

    Combines:
        - Execution Eligibility Engine (9-dimension score)
        - Continuous Learning Layer (symbol/session PF)
        - Expected Profit Score (trend × ATR × volume × momentum × liquidity × RR)
        - Adaptive Risk (score × symbol PF × session PF × strategy PF)

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self.eligibility = ExecutionEligibilityEngine()
        self.learning = ContinuousLearningLayer()
        self._last_execution_time: Dict[str, float] = {}

    def rank_and_select(
        self,
        signals: List[Dict[str, Any]],
        open_positions: Optional[List[Dict]] = None,
        market_data: Optional[Dict] = None,
        balance: float = 10_000.0,
    ) -> RankingResult:
        """
        Rank all signals and select top N for execution.

        Args:
            signals: List of signal dicts from scanner
            open_positions: Current open positions
            market_data: Optional market data
            balance: Account balance

        Returns:
            RankingResult with executed and rejected signals
        """
        result = RankingResult(
            total_signals=len(signals),
            timestamp=time.time(),
        )

        positions = open_positions or []
        open_symbols = {p.get("symbol", "") for p in positions}
        open_count = len(positions)

        # ── Step 1: Get learning statistics ──
        strategy_pf = self.learning.get_strategy_pf()

        # Update eligibility engine with latest stats
        for sym, stats in self.learning.get_all_symbol_stats().items():
            self.eligibility.update_symbol_pf(sym, stats.profit_factor)
        self.eligibility.update_strategy_pf(strategy_pf)

        # ── Step 2: Score and rank all signals ──
        ranked: List[RankedSignal] = []

        for sig in signals:
            sym = sig.get("symbol", "")
            side = sig.get("side", "")
            session = sig.get("session", sig.get("at_open_session", "unknown"))

            # Check if symbol is blocked
            if self.learning.is_symbol_blocked(sym):
                ranked.append(RankedSignal(
                    signal=sig,
                    eligibility=EligibilityResult(symbol=sym, side=side, eligible=False),
                    blocked=True,
                    block_reason=f"symbol_blocked: {sym} PF={self.learning.get_symbol_pf(sym):.2f}",
                ))
                result.blocked_signals += 1
                continue

            # Check if session is blocked
            if self.learning.is_session_blocked(session):
                ranked.append(RankedSignal(
                    signal=sig,
                    eligibility=EligibilityResult(symbol=sym, side=side, eligible=False),
                    blocked=True,
                    block_reason=f"session_blocked: {session} PF={self.learning.get_session_pf(session):.2f}",
                ))
                result.blocked_signals += 1
                continue

            # Check if already in this symbol
            if sym in open_symbols:
                ranked.append(RankedSignal(
                    signal=sig,
                    eligibility=EligibilityResult(symbol=sym, side=side, eligible=False),
                    blocked=True,
                    block_reason=f"already_open: {sym}",
                ))
                result.blocked_signals += 1
                continue

            # ── Evaluate eligibility ──
            symbol_pf = self.learning.get_symbol_pf(sym)
            session_pf = self.learning.get_session_pf(session)

            eligibility = self.eligibility.evaluate(
                sig, market_data, symbol_pf, session_pf, strategy_pf,
            )

            # ── Calculate composite score ──
            ep_score = calculate_expected_profit_score(sig)
            composite = (
                eligibility.execution_score * 0.6   # 60% execution quality
                + ep_score * 0.4                     # 40% expected profit
            )

            # ── Calculate adaptive risk ──
            # Risk = Execution Score × Symbol PF × Session PF × Strategy PF
            sym_adj = self.learning.get_symbol_adjustment(sym)
            sess_adj = self.learning.get_session_adjustment(session)
            adaptive_risk = (
                (eligibility.execution_score / 100)
                * max(0.5, min(2.0, sym_adj))
                * max(0.5, min(2.0, sess_adj))
                * max(0.5, min(2.0, strategy_pf))
            )

            rs = RankedSignal(
                signal=sig,
                eligibility=eligibility,
                composite_score=composite,
                symbol_pf=symbol_pf,
                session_pf=session_pf,
                strategy_pf=strategy_pf,
                adaptive_risk=adaptive_risk,
                capital_allocation=1.0,
            )

            if eligibility.eligible:
                result.eligible_signals += 1
            ranked.append(rs)

        # ── Step 3: Sort by composite score (best first) ──
        ranked.sort(key=lambda r: r.composite_score, reverse=True)

        # ── Step 4: Assign ranks and capital allocations ──
        eligible_ranked = [r for r in ranked if r.eligibility.eligible and not r.blocked]

        for i, rs in enumerate(eligible_ranked):
            rs.rank = i + 1
            rs.capital_allocation = RANK_ALLOCATIONS.get(rs.rank, 0.5)

        # ── Step 5: Select top N for execution ──
        available_slots = MAX_OPEN_POSITIONS - open_count
        max_exec = min(MAX_EXECUTIONS_PER_CYCLE, available_slots)

        executed = []
        rejected = []

        for rs in eligible_ranked:
            if len(executed) >= max_exec:
                rejected.append(rs)
                continue

            # Check execution cooldown
            sym = rs.signal.get("symbol", "")
            last_exec = self._last_execution_time.get(sym, 0)
            if time.time() - last_exec < MIN_EXECUTION_GAP_SEC:
                rejected.append(rs)
                continue

            executed.append(rs)
            self._last_execution_time[sym] = time.time()

        result.ranked_signals = ranked
        result.executed_signals = executed
        result.rejected_signals = rejected + [r for r in ranked if r.blocked]
        result.executed_count = len(executed)

        logger.info(
            "📊 RANKING: {} signals → {} eligible, {} blocked → {} executed (max={})",
            result.total_signals, result.eligible_signals,
            result.blocked_signals, result.executed_count, max_exec,
        )

        if executed:
            for rs in executed:
                logger.info(
                    "  ✅ #{} {} {} → score={:.1f} alloc={:.2f}× adaptive={:.3f}",
                    rs.rank, rs.signal.get("side", ""), rs.signal.get("symbol", ""),
                    rs.composite_score, rs.capital_allocation, rs.adaptive_risk,
                )

        return result

    def get_summary(self) -> Dict[str, Any]:
        """Get ranking engine summary including learning data."""
        return {
            "learning": self.learning.get_summary(),
            "eligibility_pfs": self.eligibility.get_all_symbol_pfs(),
        }
