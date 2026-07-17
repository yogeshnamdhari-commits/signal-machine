"""
Capital Deployment Tiers — Graduated Capital Release
=====================================================
Tier 0: Simulation    → $0, 30 days
Tier 1: Micro Capital → $500-$2K, 0.25%/trade, 30 days
Tier 2: Small Capital → $2K-$10K, 0.50%/trade, 30 days
Tier 3: Moderate      → $10K-$50K, 1.00%/trade
Tier 4: Production    → Portfolio model
"""

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "capital"


# ─── Enums ───────────────────────────────────────────────────────────────────
class DeploymentTier(Enum):
    TIER_0_SIMULATION = 0
    TIER_1_MICRO = 1
    TIER_2_SMALL = 2
    TIER_3_MODERATE = 3
    TIER_4_PRODUCTION = 4


# ─── Tier Configuration ─────────────────────────────────────────────────────
@dataclass
class TierConfig:
    """Configuration for a deployment tier."""
    tier: int
    name: str
    min_capital: float
    max_capital: float
    risk_per_trade: float       # As fraction (0.0025 = 0.25%)
    max_portfolio_risk: float
    max_drawdown: float
    max_positions: int
    max_leverage: int
    min_duration_days: int      # Minimum time in tier
    promotion_criteria: dict    # Requirements to advance
    demotion_triggers: dict     # Triggers to fall back


TIER_CONFIGS = {
    0: TierConfig(
        tier=0, name="Simulation Only",
        min_capital=0, max_capital=0,
        risk_per_trade=0.0, max_portfolio_risk=0.0,
        max_drawdown=0.10, max_positions=10,
        max_leverage=20, min_duration_days=30,
        promotion_criteria={
            "min_trades": 50,
            "min_win_rate": 0.48,
            "min_profit_factor": 1.3,
            "max_drawdown": 0.10,
            "min_uptime": 0.99,
            "zero_risk_breaches": True,
            "zero_position_mismatches": True,
        },
        demotion_triggers={},
    ),
    1: TierConfig(
        tier=1, name="Micro Capital",
        min_capital=500, max_capital=2000,
        risk_per_trade=0.0025, max_portfolio_risk=0.025,
        max_drawdown=0.05, max_positions=3,
        max_leverage=5, min_duration_days=30,
        promotion_criteria={
            "min_trades": 30,
            "min_win_rate": 0.48,
            "min_profit_factor": 1.3,
            "max_drawdown": 0.05,
            "min_sharpe": 1.0,
            "zero_risk_breaches": True,
        },
        demotion_triggers={
            "max_drawdown_breach": 0.05,
            "consecutive_loss_days": 5,
            "risk_breach_count": 2,
        },
    ),
    2: TierConfig(
        tier=2, name="Small Capital",
        min_capital=2000, max_capital=10000,
        risk_per_trade=0.005, max_portfolio_risk=0.035,
        max_drawdown=0.08, max_positions=5,
        max_leverage=10, min_duration_days=30,
        promotion_criteria={
            "min_trades": 50,
            "min_win_rate": 0.50,
            "min_profit_factor": 1.5,
            "max_drawdown": 0.08,
            "min_sharpe": 1.2,
            "zero_risk_breaches": True,
        },
        demotion_triggers={
            "max_drawdown_breach": 0.08,
            "consecutive_loss_days": 7,
            "risk_breach_count": 1,
        },
    ),
    3: TierConfig(
        tier=3, name="Moderate Capital",
        min_capital=10000, max_capital=50000,
        risk_per_trade=0.01, max_portfolio_risk=0.05,
        max_drawdown=0.10, max_positions=8,
        max_leverage=15, min_duration_days=0,
        promotion_criteria={
            "min_trades": 100,
            "min_win_rate": 0.50,
            "min_profit_factor": 1.5,
            "max_drawdown": 0.10,
            "min_sharpe": 1.5,
            "zero_risk_breaches": True,
        },
        demotion_triggers={
            "max_drawdown_breach": 0.10,
            "consecutive_loss_days": 10,
            "risk_breach_count": 1,
        },
    ),
    4: TierConfig(
        tier=4, name="Production Capital",
        min_capital=50000, max_capital=float('inf'),
        risk_per_trade=0.01, max_portfolio_risk=0.05,
        max_drawdown=0.10, max_positions=10,
        max_leverage=20, min_duration_days=0,
        promotion_criteria={},
        demotion_triggers={
            "max_drawdown_breach": 0.10,
            "consecutive_loss_days": 14,
            "risk_breach_count": 1,
            "monthly_loss_breach": 0.10,
        },
    ),
}


# ─── Tier State ──────────────────────────────────────────────────────────────
@dataclass
class TierState:
    """Current tier deployment state."""
    current_tier: int
    capital_allocated: float
    tier_start_date: str
    days_in_tier: int
    trades_in_tier: int
    tier_win_rate: float
    tier_profit_factor: float
    tier_max_drawdown: float
    tier_sharpe: float
    risk_breaches: int
    consecutive_loss_days: int
    ready_for_promotion: bool
    promotion_blockers: list
    last_evaluation: str


# ─── Capital Deployment Tier Manager ─────────────────────────────────────────
class CapitalTierManager:
    """
    Manages graduated capital deployment across tiers.

    Usage:
        manager = CapitalTierManager(initial_tier=0)
        config = manager.get_current_config()
        can_promote, reasons = manager.evaluate_promotion(performance)
    """

    def __init__(self, initial_tier: int = 0, initial_capital: float = 0):
        self._current_tier = initial_tier
        self._capital = initial_capital
        self._tier_start = datetime.now(timezone.utc)
        self._trades = 0
        self._wins = 0
        self._losses = 0
        self._total_pnl = 0.0
        self._peak_pnl = 0.0
        self._max_drawdown = 0.0
        self._risk_breaches = 0
        self._consecutive_loss_days = 0
        self._daily_pnls: list[float] = []
        self._history: list[dict] = []
        self._load_state()
        logger.info("CapitalTierManager: Tier %d, Capital=$%.2f", self._current_tier, self._capital)

    def start_tier(self, tier_level: int, capital: float = 0) -> dict:
        """Start a new tier"""
        if tier_level < 0 or tier_level > 4:
            raise ValueError(f"Invalid tier level: {tier_level}")
        
        config = TIER_CONFIGS[tier_level]
        if capital > 0:
            if not (config.min_capital <= capital <= config.max_capital):
                raise ValueError(f"Capital {capital} outside bounds for tier {tier_level}")
        
        self._current_tier = tier_level
        self._capital = capital
        self._tier_start = datetime.now(timezone.utc)
        self._trades = 0
        self._wins = 0
        self._losses = 0
        self._total_pnl = 0.0
        self._peak_pnl = 0.0
        self._max_drawdown = 0.0
        self._risk_breaches = 0
        self._consecutive_loss_days = 0
        self._daily_pnls = []
        self._save_state()
        
        return {
            "current_tier": tier_level,
            "capital_deployed": capital,
            "tier_start_date": self._tier_start.isoformat(),
        }

    # ── Current Config ───────────────────────────────────────────────────────
    def get_current_config(self) -> TierConfig:
        """Get configuration for current tier."""
        return TIER_CONFIGS[self._current_tier]

    def get_current_tier(self) -> int:
        """Get current tier number."""
        return self._current_tier

    def get_state(self) -> TierState:
        """Get current tier state."""
        config = self.get_current_config()
        days = (datetime.now(timezone.utc) - self._tier_start).days
        win_rate = self._wins / self._trades if self._trades > 0 else 0

        # Calculate profit factor
        gross_profit = sum(p for p in self._daily_pnls if p > 0)
        gross_loss = abs(sum(p for p in self._daily_pnls if p < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Calculate Sharpe
        sharpe = self._calculate_sharpe()

        # Check promotion readiness
        ready, blockers = self._check_promotion_criteria(
            win_rate, pf, self._max_drawdown, sharpe
        )

        return TierState(
            current_tier=self._current_tier,
            capital_allocated=self._capital,
            tier_start_date=self._tier_start.isoformat(),
            days_in_tier=days,
            trades_in_tier=self._trades,
            tier_win_rate=round(win_rate, 4),
            tier_profit_factor=round(pf, 2),
            tier_max_drawdown=round(self._max_drawdown, 4),
            tier_sharpe=round(sharpe, 2),
            risk_breaches=self._risk_breaches,
            consecutive_loss_days=self._consecutive_loss_days,
            ready_for_promotion=ready,
            promotion_blockers=blockers,
            last_evaluation=datetime.now(timezone.utc).isoformat(),
        )

    # ── Record Trade ─────────────────────────────────────────────────────────
    def record_trade(self, pnl: float, is_win: bool):
        """Record a trade result for tier evaluation."""
        self._trades += 1
        if is_win:
            self._wins += 1
            self._consecutive_loss_days = 0
        else:
            self._losses += 1

        self._total_pnl += pnl
        self._daily_pnls.append(pnl)
        self._peak_pnl = max(self._peak_pnl, self._total_pnl)

        # Update max drawdown
        if self._peak_pnl > 0:
            dd = (self._peak_pnl - self._total_pnl) / self._peak_pnl
            self._max_drawdown = max(self._max_drawdown, dd)

        self._save_state()

    def record_risk_breach(self):
        """Record a risk breach event."""
        self._risk_breaches += 1
        self._save_state()

    def record_loss_day(self):
        """Record a losing day."""
        self._consecutive_loss_days += 1
        self._save_state()

    # ── Promotion ────────────────────────────────────────────────────────────
    def evaluate_promotion(self, performance: Optional[dict] = None) -> tuple[bool, list[str]]:
        """Check if ready to advance to next tier."""
        if self._current_tier >= 4:
            return False, ["Already at maximum tier"]

        config = self.get_current_config()
        days = (datetime.now(timezone.utc) - self._tier_start).days

        # Check minimum duration
        if days < config.min_duration_days:
            return False, [f"Need {config.min_duration_days - days} more days in tier"]

        # Use provided performance or calculate from state
        if performance:
            win_rate = performance.get("win_rate", 0)
            pf = performance.get("profit_factor", 0)
            dd = performance.get("max_drawdown", 0)
            sharpe = performance.get("sharpe", 0)
            trades = performance.get("trades", 0)
        else:
            state = self.get_state()
            win_rate = state.tier_win_rate
            pf = state.tier_profit_factor
            dd = state.tier_max_drawdown
            sharpe = state.tier_sharpe
            trades = state.trades_in_tier

        ready, blockers = self._check_promotion_criteria(win_rate, pf, dd, sharpe, trades)
        return ready, blockers
    
    def can_proceed_to_tier(self, tier_level: int, metrics: dict) -> tuple[bool, list[str]]:
        """Check if can proceed to specified tier based on metrics"""
        if tier_level < 0 or tier_level > 4:
            return False, ["Invalid tier level"]
        
        if tier_level <= self._current_tier:
            return False, ["Cannot go to lower or equal tier"]
        
        failures = []
        
        # Profit factor > 1.3
        if metrics.get("pf", 0) <= 1.3:
            failures.append(f"PF = {metrics.get('pf', 0):.2f} (need > 1.3)")
        
        # Win rate > 48%
        wr_pct = metrics.get("wr", 0) * 100 if metrics.get("wr", 0) < 1 else metrics.get("wr", 0)
        if wr_pct <= 48:
            failures.append(f"WR = {wr_pct:.2f}% (need > 48%)")
        
        # Max drawdown < 10%
        dd_pct = metrics.get("dd", 0) * 100 if metrics.get("dd", 0) < 1 else metrics.get("dd", 0)
        if dd_pct >= 10:
            failures.append(f"DD = {dd_pct:.2f}% (need < 10%)")
        
        # Risk breaches = 0
        if metrics.get("risk_breaches", 0) > 0:
            failures.append(f"Risk breaches = {metrics.get('risk_breaches', 0)} (need 0)")
        
        # Position mismatches = 0
        if metrics.get("position_mismatches", 0) > 0:
            failures.append(f"Position mismatches = {metrics.get('position_mismatches', 0)} (need 0)")
        
        # Recovery success = 100%
        if metrics.get("recovery_success_rate", 0) < 100:
            failures.append(f"Recovery success = {metrics.get('recovery_success_rate', 0):.1f}% (need 100%)")
        
        # Uptime > 99%
        if metrics.get("uptime_pct", 0) < 99:
            failures.append(f"Uptime = {metrics.get('uptime_pct', 0):.2f}% (need > 99%)")
        
        return len(failures) == 0, failures

    def promote(self, new_capital: float) -> bool:
        """Promote to next tier."""
        if self._current_tier >= 4:
            return False

        ready, blockers = self.evaluate_promotion()
        if not ready:
            logger.warning("Cannot promote: %s", blockers)
            return False

        self._history.append({
            "from_tier": self._current_tier,
            "to_tier": self._current_tier + 1,
            "capital": self._capital,
            "trades": self._trades,
            "win_rate": self._wins / self._trades if self._trades > 0 else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        self._current_tier += 1
        self._capital = new_capital
        self._tier_start = datetime.now(timezone.utc)
        self._trades = 0
        self._wins = 0
        self._losses = 0
        self._total_pnl = 0.0
        self._peak_pnl = 0.0
        self._max_drawdown = 0.0
        self._risk_breaches = 0
        self._consecutive_loss_days = 0
        self._daily_pnls = []
        self._save_state()

        logger.info("Promoted to Tier %d with $%.2f", self._current_tier, self._capital)
        return True

    # ── Demotion ──────────────────────────────────────────────────────────────
    def check_demotion(self) -> tuple[bool, list[str]]:
        """Check if should be demoted to previous tier."""
        if self._current_tier <= 0:
            return False, []

        config = self.get_current_config()
        triggers = config.demotion_triggers
        reasons = []

        # Max drawdown breach
        if self._max_drawdown > triggers.get("max_drawdown_breach", 1.0):
            reasons.append(f"Drawdown {self._max_drawdown:.1%} exceeded {triggers['max_drawdown_breach']:.1%}")

        # Consecutive loss days
        if self._consecutive_loss_days >= triggers.get("consecutive_loss_days", 999):
            reasons.append(f"{self._consecutive_loss_days} consecutive loss days")

        # Risk breaches
        if self._risk_breaches >= triggers.get("risk_breach_count", 999):
            reasons.append(f"{self._risk_breaches} risk breaches")

        # Monthly loss
        monthly_pnl = sum(self._daily_pnls[-30:]) if self._daily_pnls else 0
        if self._capital > 0 and abs(monthly_pnl) / self._capital > triggers.get("monthly_loss_breach", 1.0):
            reasons.append(f"Monthly loss {abs(monthly_pnl)/self._capital:.1%} exceeded limit")

        should_demote = len(reasons) > 0
        return should_demote, reasons

    def demote(self) -> bool:
        """Demote to previous tier."""
        should, reasons = self.check_demotion()
        if not should:
            return False

        self._history.append({
            "from_tier": self._current_tier,
            "to_tier": self._current_tier - 1,
            "reason": reasons,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        self._current_tier -= 1
        prev_config = TIER_CONFIGS[self._current_tier]
        self._capital = min(self._capital, prev_config.max_capital)
        self._tier_start = datetime.now(timezone.utc)
        self._risk_breaches = 0
        self._consecutive_loss_days = 0
        self._save_state()

        logger.warning("Demoted to Tier %d: %s", self._current_tier, reasons)
        return True

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _check_promotion_criteria(
        self, win_rate: float, pf: float, dd: float, sharpe: float, trades: int = 0
    ) -> tuple[bool, list[str]]:
        """Check promotion criteria against tier requirements."""
        config = self.get_current_config()
        criteria = config.promotion_criteria
        blockers = []

        if trades < criteria.get("min_trades", 0):
            blockers.append(f"Need {criteria['min_trades'] - trades} more trades")

        if win_rate < criteria.get("min_win_rate", 0):
            blockers.append(f"Win rate {win_rate:.1%} < {criteria['min_win_rate']:.1%}")

        if pf < criteria.get("min_profit_factor", 0):
            blockers.append(f"Profit factor {pf:.2f} < {criteria['min_profit_factor']:.2f}")

        if dd > criteria.get("max_drawdown", 1.0):
            blockers.append(f"Drawdown {dd:.1%} > {criteria['max_drawdown']:.1%}")

        if sharpe < criteria.get("min_sharpe", 0):
            blockers.append(f"Sharpe {sharpe:.2f} < {criteria['min_sharpe']:.2f}")

        if criteria.get("zero_risk_breaches") and self._risk_breaches > 0:
            blockers.append(f"{self._risk_breaches} risk breaches (need 0)")

        return len(blockers) == 0, blockers

    def _calculate_sharpe(self) -> float:
        """Calculate Sharpe ratio from daily PnLs."""
        if len(self._daily_pnls) < 2:
            return 0.0
        avg = sum(self._daily_pnls) / len(self._daily_pnls)
        variance = sum((p - avg) ** 2 for p in self._daily_pnls) / (len(self._daily_pnls) - 1)
        std = math.sqrt(variance) if variance > 0 else 0
        return (avg / std) * math.sqrt(365) if std > 0 else 0

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save tier state."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "current_tier": self._current_tier,
            "capital": self._capital,
            "tier_start": self._tier_start.isoformat(),
            "trades": self._trades,
            "wins": self._wins,
            "losses": self._losses,
            "total_pnl": self._total_pnl,
            "peak_pnl": self._peak_pnl,
            "max_drawdown": self._max_drawdown,
            "risk_breaches": self._risk_breaches,
            "consecutive_loss_days": self._consecutive_loss_days,
            "daily_pnls": self._daily_pnls[-30:],
            "history": self._history,
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "tier_state.json").write_text(json.dumps(state, indent=2, default=str))

    def _load_state(self):
        """Load persisted tier state."""
        path = DATA_DIR / "tier_state.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            self._current_tier = state.get("current_tier", self._current_tier)
            self._capital = state.get("capital", self._capital)
            self._tier_start = datetime.fromisoformat(state.get("tier_start", self._tier_start.isoformat()))
            self._trades = state.get("trades", 0)
            self._wins = state.get("wins", 0)
            self._losses = state.get("losses", 0)
            self._total_pnl = state.get("total_pnl", 0)
            self._peak_pnl = state.get("peak_pnl", 0)
            self._max_drawdown = state.get("max_drawdown", 0)
            self._risk_breaches = state.get("risk_breaches", 0)
            self._consecutive_loss_days = state.get("consecutive_loss_days", 0)
            self._daily_pnls = state.get("daily_pnls", [])
            self._history = state.get("history", [])
        except Exception as e:
            logger.error("Failed to load tier state: %s", e)

    def get_all_tiers(self) -> dict:
        """Get configuration for all tiers."""
        return {k: asdict(v) for k, v in TIER_CONFIGS.items()}

    def get_stats(self) -> dict:
        """Get tier manager statistics."""
        state = self.get_state()
        return {
            "current_tier": state.current_tier,
            "tier_name": TIER_CONFIGS[state.current_tier].name,
            "capital": self._capital,
            "days_in_tier": state.days_in_tier,
            "trades": state.trades_in_tier,
            "win_rate": state.tier_win_rate,
            "profit_factor": state.tier_profit_factor,
            "max_drawdown": state.tier_max_drawdown,
            "sharpe": state.tier_sharpe,
            "ready_for_promotion": state.ready_for_promotion,
            "promotion_blockers": state.promotion_blockers,
            "risk_breaches": state.risk_breaches,
        }
