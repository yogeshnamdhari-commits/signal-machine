"""
Market Regime Filter — Phase 2: Trading rules per regime.

Maps the existing 6-regime system to 5 institutional categories:
  - TRENDING     (trending_bull + trending_bear) → Allow trend-following trades
  - RANGING      (range) → Block breakout trades
  - COMPRESSION  (compression) → Wait for expansion
  - VOLATILE     (volatile) → Reduce position sizing
  - BREAKOUT     (breakout) → Allow with confirmation

Each regime defines:
  - Allowed trade types (trend-following, breakout, etc.)
  - Blocked trade types
  - Position sizing multiplier
  - Risk adjustments
  - Regime-specific SL/TP modifiers
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from loguru import logger


# ── Regime Categories (user spec) ────────────────────────────
class RegimeCategory:
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    COMPRESSION = "COMPRESSION"
    VOLATILE = "VOLATILE"
    BREAKOUT = "BREAKOUT"


# ── Map existing 6-regime → 5 institutional categories ──────
_REGIME_MAP = {
    "trending_bull": RegimeCategory.TRENDING,
    "trending_bear": RegimeCategory.TRENDING,
    "range":         RegimeCategory.RANGING,
    "compression":   RegimeCategory.COMPRESSION,
    "volatile":      RegimeCategory.VOLATILE,
    "breakout":      RegimeCategory.BREAKOUT,
    # Legacy aliases
    "trending_up":   RegimeCategory.TRENDING,
    "trending_down": RegimeCategory.TRENDING,
    "ranging":       RegimeCategory.RANGING,
    "quiet":         RegimeCategory.RANGING,
    "reversal":      RegimeCategory.BREAKOUT,
}

# ── Regime icons for global display ─────────────────────────
REGIME_ICONS = {
    RegimeCategory.TRENDING:    "📈",
    RegimeCategory.RANGING:     "↔️",
    RegimeCategory.COMPRESSION: "🔍",
    RegimeCategory.VOLATILE:    "⚡",
    RegimeCategory.BREAKOUT:    "🚀",
}


@dataclass
class RegimeRules:
    """Trading rules for a specific regime category."""
    category: str
    allow_trend_following: bool
    allow_breakout: bool
    allow_mean_reversion: bool
    position_sizing_mult: float   # Multiplier for position size (0-1)
    sl_mult: float                # SL distance multiplier (1.0 = normal)
    tp_mult: float                # TP distance multiplier (1.0 = normal)
    max_rr_allowed: float         # Maximum R:R allowed in this regime
    min_confidence: float         # Minimum confidence to trade in this regime
    description: str


# ── Per-Regime Rules ─────────────────────────────────────────
REGIME_RULES: Dict[str, RegimeRules] = {
    RegimeCategory.TRENDING: RegimeRules(
        category=RegimeCategory.TRENDING,
        allow_trend_following=True,     # ✅ Allow trend-following
        allow_breakout=False,           # ❌ Don't chase breakouts in trend
        allow_mean_reversion=False,     # ❌ Don't fade the trend
        position_sizing_mult=1.0,       # Full size for trend trades
        sl_mult=1.0,                    # Normal SL
        tp_mult=1.2,                    # Extended TP (let profits run)
        max_rr_allowed=10.0,            # Wide R:R for trending
        min_confidence=70,              # Phase 1 minimum (was 85, lowered for signal flow)
        description="TRENDING — Allow trend-following trades",
    ),
    RegimeCategory.RANGING: RegimeRules(
        category=RegimeCategory.RANGING,
        allow_trend_following=True,      # Phase 10: ENABLED — allow quality range trades with regime filter safety
        allow_breakout=False,           # BLOCKED: no breakout in range
        allow_mean_reversion=True,      # ONLY: mean reversion at range boundaries
        position_sizing_mult=0.5,       # Half size in range
        sl_mult=0.8,                    # Tighter SL (range is noisy)
        tp_mult=0.8,                    # Closer TP (limited range)
        max_rr_allowed=3.0,             # Conservative R:R
        min_confidence=75,              # Higher bar for range trades
        description="RANGING — Only mean reversion at range boundaries, no trend/breakout",
    ),
    RegimeCategory.COMPRESSION: RegimeRules(
        category=RegimeCategory.COMPRESSION,
        allow_trend_following=False,    # ❌ Wait for expansion (user spec)
        allow_breakout=False,           # ❌ Wait for confirmed breakout
        allow_mean_reversion=False,     # ❌ No trades in compression
        position_sizing_mult=0.0,       # NO POSITIONS (wait for expansion)
        sl_mult=1.5,                    # Wider SL if somehow traded
        tp_mult=1.5,                    # Wider TP
        max_rr_allowed=5.0,
        min_confidence=78,              # Very high bar (was 95)
        description="COMPRESSION — Wait for expansion, no new positions",
    ),
    RegimeCategory.VOLATILE: RegimeRules(
        category=RegimeCategory.VOLATILE,
        allow_trend_following=True,     # ✅ Can follow volatile trends
        allow_breakout=True,            # ✅ Breakouts in volatile = momentum
        allow_mean_reversion=False,     # ❌ Too dangerous in volatility
        position_sizing_mult=0.5,       # Half size (user spec: reduce sizing)
        sl_mult=1.5,                    # Wider SL (more noise)
        tp_mult=1.0,                    # Normal TP
        max_rr_allowed=5.0,
        min_confidence=72,              # Higher bar for volatile (was 88)
        description="VOLATILE — Reduce position sizing, wider stops",
    ),
    RegimeCategory.BREAKOUT: RegimeRules(
        category=RegimeCategory.BREAKOUT,
        allow_trend_following=True,     # ✅ Breakouts start trends
        allow_breakout=True,            # ✅ Confirm with volume
        allow_mean_reversion=False,     # ❌ Don't fade breakouts
        position_sizing_mult=0.7,       # 70% size (needs confirmation)
        sl_mult=1.2,                    # Slightly wider SL (breakout volatility)
        tp_mult=1.5,                    # Extended TP (breakout target)
        max_rr_allowed=8.0,
        min_confidence=70,
        description="BREAKOUT — Confirm with volume, ride the breakout",
    ),
}


class MarketRegimeFilter:
    """
    Phase 2: Applies regime-based trading rules to signals.
    
    For each signal, determines:
    1. Current regime category (mapped from raw regime)
    2. Whether the trade type is allowed
    3. Position sizing adjustment
    4. SL/TP multipliers
    5. Global regime status for dashboard display
    """

    def __init__(self) -> None:
        self._global_status: Dict[str, Dict] = {}  # symbol → regime info

    def get_regime_category(self, raw_regime: str) -> str:
        """Map raw regime string to institutional category."""
        return _REGIME_MAP.get(raw_regime, RegimeCategory.RANGING)

    def get_rules(self, raw_regime: str) -> RegimeRules:
        """Get trading rules for a raw regime type."""
        category = self.get_regime_category(raw_regime)
        return REGIME_RULES.get(category, REGIME_RULES[RegimeCategory.RANGING])

    def evaluate_signal(
        self,
        symbol: str,
        side: str,
        raw_regime: str,
        regime_confidence: float,
        signal_type: str = "trend_following",
        confidence_100: float = 0,
        adaptive_min_confidence: float = 0,
    ) -> Dict:
        """
        Evaluate whether a signal should be allowed based on regime rules.
        
        Parameters
        ----------
        symbol : str
        side : str — "LONG" or "SHORT"
        raw_regime : str — from regime detector ("trending_bull", "range", etc.)
        regime_confidence : float — 0-1
        signal_type : str — "trend_following", "breakout", "mean_reversion"
        confidence_100 : float — signal confidence (0-100)
        adaptive_min_confidence : float — if > 0, override hardcoded min_confidence
        
        Returns
        -------
        dict with:
            allowed : bool
            reason : str
            position_sizing_mult : float
            sl_mult : float
            tp_mult : float
            regime_category : str
            regime_icon : str
        """
        category = self.get_regime_category(raw_regime)
        rules = REGIME_RULES.get(category, REGIME_RULES[RegimeCategory.RANGING])
        icon = REGIME_ICONS.get(category, "❓")

        # Check if signal type is allowed
        allowed = True
        reason = ""

        if signal_type == "trend_following" and not rules.allow_trend_following:
            allowed = False
            reason = f"{category}: Trend-following not allowed"
        elif signal_type == "breakout" and not rules.allow_breakout:
            allowed = False
            reason = f"{category}: Breakout trades blocked"
        elif signal_type == "mean_reversion" and not rules.allow_mean_reversion:
            allowed = False
            reason = f"{category}: Mean reversion not allowed"

        # Check position sizing
        if rules.position_sizing_mult == 0:
            allowed = False
            reason = f"{category}: No new positions (wait for expansion)"

        # Check minimum confidence — use adaptive if provided, else hardcoded
        # For RANGING regime, always require higher confidence
        min_conf = adaptive_min_confidence if adaptive_min_confidence > 0 else rules.min_confidence
        if category == RegimeCategory.RANGING:
            min_conf = max(min_conf, 60)  # Phase 10: lowered from 75 — market is ranging, need to allow quality range trades
        if confidence_100 < min_conf:
            allowed = False
            reason = f"{category}: Confidence {confidence_100:.0f} < {min_conf:.0f} required"

        # Store global status for dashboard
        self._global_status[symbol] = {
            "symbol": symbol,
            "regime_category": category,
            "regime_raw": raw_regime,
            "regime_icon": icon,
            "confidence_pct": round(regime_confidence * 100, 1),
            "rules": {
                "allow_trend_following": rules.allow_trend_following,
                "allow_breakout": rules.allow_breakout,
                "allow_mean_reversion": rules.allow_mean_reversion,
                "position_sizing_mult": rules.position_sizing_mult,
                "sl_mult": rules.sl_mult,
                "tp_mult": rules.tp_mult,
            },
            "allowed": allowed,
            "reason": reason,
        }

        return {
            "allowed": allowed,
            "reason": reason,
            "position_sizing_mult": rules.position_sizing_mult,
            "sl_mult": rules.sl_mult,
            "tp_mult": rules.tp_mult,
            "regime_category": category,
            "regime_icon": icon,
        }

    def get_global_status(self) -> Dict[str, Dict]:
        """Get regime status for all symbols — for dashboard global display."""
        return self._global_status

    def get_regime_summary(self) -> Dict:
        """Get aggregate regime distribution across all symbols."""
        if not self._global_status:
            return {"total": 0, "distribution": {}}

        distribution = {}
        for sym, info in self._global_status.items():
            cat = info.get("regime_category", "UNKNOWN")
            distribution[cat] = distribution.get(cat, 0) + 1

        total = len(self._global_status)
        return {
            "total": total,
            "distribution": distribution,
            "dominant_regime": max(distribution, key=distribution.get) if distribution else "UNKNOWN",
            "symbols_by_regime": {
                cat: [s for s, i in self._global_status.items() if i["regime_category"] == cat]
                for cat in distribution
            },
        }
