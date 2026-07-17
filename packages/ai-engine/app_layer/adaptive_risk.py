"""
Adaptive Risk Governor — Reacts to performance during the session.

READ-ONLY with respect to upstream data. Never modifies positions or orders.

Per Master Directive:
    "The App should react to performance during the session.
     Three losses → Reduce position size.
     Five losses → Pause new entries.
     Strong recovery → Gradually restore risk."

Risk States:
    NORMAL:   Full risk allocation
    CAUTION:  Reduced risk after 2+ consecutive losses
    REDUCED:  Significantly reduced risk after 3+ consecutive losses
    PAUSED:   No new entries after 5+ consecutive losses or daily loss limit
    RECOVERY: Gradually restoring risk after recovery from drawdown

Transition Rules:
    NORMAL → CAUTION: 2 consecutive losses
    CAUTION → REDUCED: 3 consecutive losses
    REDUCED → PAUSED: 5 consecutive losses OR daily loss > 3%
    PAUSED → RECOVERY: 1 winning trade
    RECOVERY → NORMAL: 3 consecutive wins
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# RISK STATE CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Risk multipliers by state
RISK_MULTIPLIERS = {
    "NORMAL": 1.0,
    "CAUTION": 0.7,    # 30% reduction
    "REDUCED": 0.4,    # 60% reduction
    "PAUSED": 0.0,     # No trading
    "RECOVERY": 0.5,   # 50% reduction
}

# Transition thresholds
CAUTION_THRESHOLD = 2      # Consecutive losses to enter CAUTION
REDUCED_THRESHOLD = 3      # Consecutive losses to enter REDUCED
PAUSE_THRESHOLD = 5        # Consecutive losses to enter PAUSED
DAILY_LOSS_LIMIT_PCT = 3.0 # Daily loss % to pause
RECOVERY_WIN_THRESHOLD = 1 # Wins needed to enter RECOVERY
NORMAL_WIN_THRESHOLD = 3   # Wins needed to return to NORMAL

# Cooldown after pause (seconds)
PAUSE_COOLDOWN_SEC = 3600  # 1 hour cooldown


@dataclass
class RiskState:
    """Current risk state."""
    state: str = "NORMAL"
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    risk_multiplier: float = 1.0
    last_state_change: float = 0.0
    pause_until: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "state": self.state,
            "consecutive_losses": self.consecutive_losses,
            "consecutive_wins": self.consecutive_wins,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_pct": round(self.daily_pnl_pct, 2),
            "risk_multiplier": round(self.risk_multiplier, 2),
            "reason": self.reason,
        }


@dataclass
class RiskDecision:
    """Decision from the risk governor."""
    approved: bool = False
    risk_multiplier: float = 1.0
    state: str = "NORMAL"
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "approved": self.approved,
            "risk_multiplier": round(self.risk_multiplier, 2),
            "state": self.state,
            "reason": self.reason,
        }


class AdaptiveRiskGovernor:
    """
    Adapts risk based on session performance AND historical learning data.

    Per Master Directive:
        Three losses → Reduce position size.
        Five losses → Pause new entries.
        Strong recovery → Gradually restore risk.

    Per Executive Assessment v2 (Adaptive Risk):
        "Risk = Expected Profit × Symbol PF × Session PF × Recent Strategy PF"
        Prevents allocating the same capital during weak regimes.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self._state = RiskState()
        self._trade_history: List[Dict] = []
        # Learning-based adjustments (injected by Continuous Learning Layer)
        self._symbol_pf: float = 0.0
        self._session_pf: float = 0.0
        self._strategy_pf: float = 1.0
        self._symbol_adj: float = 1.0
        self._session_adj: float = 1.0

    def set_learning_data(
        self,
        symbol_pf: float = 0.0,
        session_pf: float = 0.0,
        strategy_pf: float = 1.0,
        symbol_adj: float = 1.0,
        session_adj: float = 1.0,
    ) -> None:
        """
        Inject learning data from Continuous Learning Layer.

        Called before evaluate() to incorporate historical performance
        into risk decisions.

        Args:
            symbol_pf: Historical profit factor for this symbol
            session_pf: Historical profit factor for this session
            strategy_pf: Recent strategy-wide profit factor
            symbol_adj: Symbol size adjustment (0.0 = blocked, 1.0 = normal)
            session_adj: Session size adjustment (0.0 = blocked, 1.0 = normal)
        """
        self._symbol_pf = symbol_pf
        self._session_pf = session_pf
        self._strategy_pf = strategy_pf
        self._symbol_adj = symbol_adj
        self._session_adj = session_adj

    def record_trade(self, pnl: float, balance: float = 10_000.0) -> None:
        """
        Record a completed trade outcome.

        Args:
            pnl: Trade PnL in USD
            balance: Current account balance
        """
        self._trade_history.append({
            "pnl": pnl,
            "balance": balance,
            "timestamp": time.time(),
        })

        # Update daily PnL
        self._state.daily_pnl += pnl
        self._state.daily_pnl_pct = self._state.daily_pnl / balance * 100 if balance > 0 else 0

        # Update consecutive counts
        if pnl < 0:
            self._state.consecutive_losses += 1
            self._state.consecutive_wins = 0
        elif pnl > 0:
            self._state.consecutive_wins += 1
            self._state.consecutive_losses = 0
        else:
            # Breakeven — don't change counts
            pass

        # Transition state
        self._transition_state()

    def evaluate(
        self,
        balance: float = 10_000.0,
    ) -> RiskDecision:
        """
        Evaluate current risk state and return decision.

        Incorporates both reactive risk (consecutive losses) and
        proactive risk (historical learning data).

        Args:
            balance: Current account balance

        Returns:
            RiskDecision with approval and risk multiplier
        """
        now = time.time()

        # Check if pause cooldown has elapsed
        if self._state.state == "PAUSED" and now < self._state.pause_until:
            remaining = self._state.pause_until - now
            return RiskDecision(
                approved=False,
                risk_multiplier=0.0,
                state="PAUSED",
                reason=f"paused — cooldown {remaining/60:.0f}min remaining",
            )

        # Check daily loss limit
        if self._state.daily_pnl_pct <= -DAILY_LOSS_LIMIT_PCT:
            self._state.state = "PAUSED"
            self._state.risk_multiplier = 0.0
            self._state.reason = (
                f"daily loss limit {self._state.daily_pnl_pct:.1f}% "
                f">= {DAILY_LOSS_LIMIT_PCT}%"
            )
            return RiskDecision(
                approved=False,
                risk_multiplier=0.0,
                state="PAUSED",
                reason=self._state.reason,
            )

        # ── Base risk multiplier from state machine ──
        base_mult = self._state.risk_multiplier

        # ── Learning-based adjustment (Proactive Risk) ──
        # Risk = base × symbol_adj × session_adj × strategy_pf_factor
        learning_mult = 1.0

        # Symbol performance adjustment
        if self._symbol_adj != 1.0:
            learning_mult *= max(0.0, min(2.0, self._symbol_adj))

        # Session performance adjustment
        if self._session_adj != 1.0:
            learning_mult *= max(0.0, min(2.0, self._session_adj))

        # Strategy PF adjustment: reduce risk when strategy is losing
        if self._strategy_pf > 0:
            if self._strategy_pf >= 1.3:
                strategy_factor = 1.2   # Strategy winning — slightly more risk
            elif self._strategy_pf >= 1.0:
                strategy_factor = 1.0   # Strategy neutral
            elif self._strategy_pf >= 0.8:
                strategy_factor = 0.7   # Strategy losing — reduce risk
            else:
                strategy_factor = 0.4   # Strategy badly losing — heavy reduction
            learning_mult *= strategy_factor

        # ── Combined risk multiplier ──
        final_mult = base_mult * learning_mult
        final_mult = max(0.0, min(1.5, final_mult))  # Cap at 1.5x

        # Build reason string
        reasons = []
        if self._state.state != "NORMAL":
            reasons.append(f"state={self._state.state}")
        if self._symbol_adj != 1.0:
            reasons.append(f"sym_adj={self._symbol_adj:.2f}")
        if self._session_adj != 1.0:
            reasons.append(f"sess_adj={self._session_adj:.2f}")
        if self._strategy_pf < 1.0:
            reasons.append(f"strat_pf={self._strategy_pf:.2f}")

        reason = ", ".join(reasons) if reasons else f"consecutive_losses={self._state.consecutive_losses}"

        return RiskDecision(
            approved=final_mult > 0,
            risk_multiplier=final_mult,
            state=self._state.state,
            reason=reason,
        )

    def _transition_state(self) -> None:
        """Transition risk state based on current conditions."""
        old_state = self._state.state
        now = time.time()

        # Check PAUSED conditions
        if self._state.consecutive_losses >= PAUSE_THRESHOLD:
            if self._state.state != "PAUSED":
                self._state.state = "PAUSED"
                self._state.risk_multiplier = 0.0
                self._state.pause_until = now + PAUSE_COOLDOWN_SEC
                self._state.reason = (
                    f"paused: {self._state.consecutive_losses} consecutive losses"
                )
                logger.warning(
                    "RISK PAUSED: {} consecutive losses — pausing for {}min",
                    self._state.consecutive_losses, PAUSE_COOLDOWN_SEC / 60,
                )
            return

        # Check daily loss limit
        if self._state.daily_pnl_pct <= -DAILY_LOSS_LIMIT_PCT:
            if self._state.state != "PAUSED":
                self._state.state = "PAUSED"
                self._state.risk_multiplier = 0.0
                self._state.pause_until = now + PAUSE_COOLDOWN_SEC
                self._state.reason = (
                    f"paused: daily loss {self._state.daily_pnl_pct:.1f}%"
                )
            return

        # Check RECOVERY transition (from PAUSED after a win)
        if old_state == "PAUSED" and self._state.consecutive_wins >= RECOVERY_WIN_THRESHOLD:
            self._state.state = "RECOVERY"
            self._state.risk_multiplier = RISK_MULTIPLIERS["RECOVERY"]
            self._state.reason = f"recovering after {self._state.consecutive_wins} wins"
            logger.info("RISK RECOVERY: {} wins — restoring risk", self._state.consecutive_wins)
            return

        # Check NORMAL transition (from RECOVERY after sustained wins)
        if old_state == "RECOVERY" and self._state.consecutive_wins >= NORMAL_WIN_THRESHOLD:
            self._state.state = "NORMAL"
            self._state.risk_multiplier = RISK_MULTIPLIERS["NORMAL"]
            self._state.consecutive_losses = 0
            self._state.reason = "normal — sustained recovery"
            logger.info("RISK NORMAL: {} consecutive wins — full risk restored", self._state.consecutive_wins)
            return

        # Check loss-based transitions
        if self._state.consecutive_losses >= REDUCED_THRESHOLD:
            if self._state.state != "REDUCED":
                self._state.state = "REDUCED"
                self._state.risk_multiplier = RISK_MULTIPLIERS["REDUCED"]
                self._state.reason = f"reduced: {self._state.consecutive_losses} consecutive losses"
                logger.warning("RISK REDUCED: {} consecutive losses", self._state.consecutive_losses)
        elif self._state.consecutive_losses >= CAUTION_THRESHOLD:
            if self._state.state not in ("CAUTION", "REDUCED", "PAUSED"):
                self._state.state = "CAUTION"
                self._state.risk_multiplier = RISK_MULTIPLIERS["CAUTION"]
                self._state.reason = f"caution: {self._state.consecutive_losses} consecutive losses"
                logger.info("RISK CAUTION: {} consecutive losses", self._state.consecutive_losses)
        else:
            # No losses — could be normal
            if self._state.state in ("CAUTION", "REDUCED") and self._state.consecutive_losses == 0:
                self._state.state = "NORMAL"
                self._state.risk_multiplier = RISK_MULTIPLIERS["NORMAL"]
                self._state.reason = "normal — losses cleared"

        # Log state change
        if self._state.state != old_state:
            self._state.last_state_change = time.time()

    def reset_daily(self) -> None:
        """Reset daily counters (call at start of new day)."""
        self._state.daily_pnl = 0.0
        self._state.daily_pnl_pct = 0.0
        # Don't reset consecutive counts — they carry over

    def get_state(self) -> RiskState:
        """Get current risk state."""
        return self._state

    def force_state(self, state: str) -> None:
        """Force a specific risk state (for testing/manual override)."""
        if state in RISK_MULTIPLIERS:
            self._state.state = state
            self._state.risk_multiplier = RISK_MULTIPLIERS[state]
            self._state.reason = f"forced to {state}"
