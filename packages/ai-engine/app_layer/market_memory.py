"""
Market Memory Engine — Learn from recurring market environments.

Per Executive Assessment v8:
    "Everything now learns from closed trades.
     I would add market memory.

     Not Smart Money.
     Not EMA.
     Execution memory.

     Example:
         Current market → Matches March 2025 → PF historically 1.42
         → Increase confidence

     or

         Matches August 2025 → PF 0.73
         → Reduce exposure

     Instead of learning only from individual trades, the engine learns
     from recurring market environments.

     This is a different level of adaptation and stays entirely within
     the execution layer."

Key Features:
    1. Market Fingerprinting — characterize market conditions
    2. Environment Matching — find similar historical periods
    3. Performance Lookup — what happened in similar conditions
    4. Confidence Adjustment — scale confidence by historical precedent
    5. Regime Memory — remember which regimes worked when

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class MarketFingerprint:
    """Characterization of current market conditions."""
    # Volatility
    atr_pct: float = 0.0          # ATR as % of price
    volatility_regime: str = ""   # HIGH / NORMAL / LOW

    # Trend
    trend_strength: float = 0.0   # -1 to 1
    trend_regime: str = ""        # TRENDING / RANGING / REVERSAL

    # Volume
    volume_ratio: float = 0.0     # Current vs average
    volume_regime: str = ""       # HIGH / NORMAL / LOW

    # Momentum
    momentum_score: float = 0.0   # -1 to 1
    momentum_regime: str = ""     # STRONG_UP / WEAK_UP / NEUTRAL / WEAK_DOWN / STRONG_DOWN

    # Session
    session: str = ""             # asia / london / new_york / off_hours

    # Composite
    fingerprint_hash: str = ""    # Unique identifier for this market state

    def to_dict(self) -> Dict:
        return {
            "atr_pct": round(self.atr_pct, 4),
            "volatility_regime": self.volatility_regime,
            "trend_strength": round(self.trend_strength, 3),
            "trend_regime": self.trend_regime,
            "volume_ratio": round(self.volume_ratio, 3),
            "volume_regime": self.volume_regime,
            "momentum_score": round(self.momentum_score, 3),
            "momentum_regime": self.momentum_regime,
            "session": self.session,
            "fingerprint_hash": self.fingerprint_hash,
        }


@dataclass
class EnvironmentMatch:
    """A match between current and historical market conditions."""
    historical_period: str = ""      # e.g., "2025-03"
    match_score: float = 0.0         # 0-1, how similar
    historical_pf: float = 0.0       # PF during that period
    historical_ev_r: float = 0.0     # EV during that period
    historical_win_rate: float = 0.0
    trade_count: int = 0
    recommendation: str = ""         # INCREASE / MAINTAIN / REDUCE

    def to_dict(self) -> Dict:
        return {
            "period": self.historical_period,
            "match_score": round(self.match_score, 3),
            "historical_pf": round(self.historical_pf, 2),
            "historical_ev_r": round(self.historical_ev_r, 3),
            "historical_win_rate": round(self.historical_win_rate, 3),
            "trades": self.trade_count,
            "recommendation": self.recommendation,
        }


@dataclass
class MarketMemoryResult:
    """Complete market memory analysis."""
    timestamp: float = 0.0
    current_fingerprint: Optional[MarketFingerprint] = None
    matches: List[EnvironmentMatch] = field(default_factory=list)

    # Recommendation
    confidence_adjustment: float = 1.0  # Multiplier for confidence
    risk_adjustment: float = 1.0        # Multiplier for risk
    recommendation: str = ""            # INCREASE / MAINTAIN / REDUCE
    reasoning: str = ""

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "fingerprint": self.current_fingerprint.to_dict() if self.current_fingerprint else {},
            "matches": [m.to_dict() for m in self.matches],
            "adjustments": {
                "confidence_multiplier": round(self.confidence_adjustment, 3),
                "risk_multiplier": round(self.risk_adjustment, 3),
                "recommendation": self.recommendation,
                "reasoning": self.reasoning,
            },
        }


class MarketMemoryEngine:
    """
    Learns from recurring market environments.

    Per Executive Assessment v8:
        "Instead of learning only from individual trades, the engine
         learns from recurring market environments."

    This engine:
        1. Creates market fingerprints from current conditions
        2. Matches against historical periods
        3. Looks up performance in similar conditions
        4. Adjusts confidence/risk based on historical precedent

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0
        self._period_stats: Dict[str, Dict] = {}

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades and compute period statistics."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, realized_r, pnl, exit_reason, regime,
                       session, closed_at, hold_minutes, confidence,
                       institutional_score
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._compute_period_stats()
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load market memory engine: {}", e)

    def _compute_period_stats(self) -> None:
        """Compute performance statistics by time period."""
        by_period: Dict[str, List[Dict]] = defaultdict(list)

        for t in self._trades:
            closed_at = t.get("closed_at", 0) or 0
            if closed_at > 0:
                # Group by month
                import datetime
                dt = datetime.datetime.fromtimestamp(closed_at)
                period = dt.strftime("%Y-%m")
                by_period[period].append(t)

        for period, trades in by_period.items():
            wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
            losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(losses) if losses else 0.01

            all_r = [t.get("realized_r", 0) or 0 for t in trades]

            self._period_stats[period] = {
                "trade_count": len(trades),
                "pf": gross_profit / max(0.01, gross_loss),
                "ev_r": sum(all_r) / max(1, len(all_r)),
                "win_rate": len(wins) / max(1, len(trades)),
                "regimes": defaultdict(list),
                "sessions": defaultdict(list),
            }

            # Compute regime/session breakdown
            for t in trades:
                regime = t.get("regime", "unknown")
                session = t.get("session", "unknown")
                self._period_stats[period]["regimes"][regime].append(t)
                self._period_stats[period]["sessions"][session].append(t)

    def evaluate(
        self,
        current_fingerprint: Optional[MarketFingerprint] = None,
        current_regime: str = "unknown",
        current_session: str = "unknown",
    ) -> MarketMemoryResult:
        """
        Evaluate market memory for current conditions.

        Args:
            current_fingerprint: Current market fingerprint (optional)
            current_regime: Current market regime
            current_session: Current trading session

        Returns:
            MarketMemoryResult with adjustments
        """
        self._ensure_loaded()

        result = MarketMemoryResult(timestamp=time.time())

        if not current_fingerprint:
            current_fingerprint = MarketFingerprint(
                session=current_session,
                trend_regime=current_regime,
            )
        result.current_fingerprint = current_fingerprint

        # ── Find matching historical periods ──
        matches = self._find_matches(current_regime, current_session)
        result.matches = matches

        # ── Calculate adjustments ──
        if matches:
            # Weight by match score
            total_weight = sum(m.match_score for m in matches)
            if total_weight > 0:
                weighted_pf = sum(m.historical_pf * m.match_score for m in matches) / total_weight
                weighted_ev = sum(m.historical_ev_r * m.match_score for m in matches) / total_weight

                # Adjust confidence based on historical performance
                if weighted_pf > 1.2:
                    result.confidence_adjustment = 1.2
                    result.recommendation = "INCREASE"
                    result.reasoning = f"Strong historical PF {weighted_pf:.2f} in similar conditions"
                elif weighted_pf > 1.0:
                    result.confidence_adjustment = 1.1
                    result.recommendation = "MAINTAIN"
                    result.reasoning = f"Positive historical PF {weighted_pf:.2f} in similar conditions"
                elif weighted_pf > 0.8:
                    result.confidence_adjustment = 0.9
                    result.recommendation = "REDUCE"
                    result.reasoning = f"Weak historical PF {weighted_pf:.2f} in similar conditions"
                else:
                    result.confidence_adjustment = 0.7
                    result.recommendation = "REDUCE"
                    result.reasoning = f"Poor historical PF {weighted_pf:.2f} in similar conditions"

                # Risk adjustment
                result.risk_adjustment = result.confidence_adjustment * 0.9
        else:
            result.confidence_adjustment = 1.0
            result.risk_adjustment = 1.0
            result.recommendation = "MAINTAIN"
            result.reasoning = "No matching historical periods found"

        return result

    def _find_matches(
        self,
        current_regime: str,
        current_session: str,
    ) -> List[EnvironmentMatch]:
        """Find historical periods with similar conditions."""
        matches = []

        for period, stats in self._period_stats.items():
            if stats["trade_count"] < 5:
                continue

            # Calculate match score based on regime and session similarity
            match_score = 0.0

            # Regime match
            if current_regime in stats["regimes"]:
                regime_trades = stats["regimes"][current_regime]
                if len(regime_trades) >= 3:
                    match_score += 0.5

            # Session match
            if current_session in stats["sessions"]:
                session_trades = stats["sessions"][current_session]
                if len(session_trades) >= 3:
                    match_score += 0.3

            # Sample size bonus
            if stats["trade_count"] >= 20:
                match_score += 0.2
            elif stats["trade_count"] >= 10:
                match_score += 0.1

            if match_score > 0.3:
                # Get regime-specific stats
                regime_pf = stats["pf"]
                regime_ev = stats["ev_r"]
                regime_wr = stats["win_rate"]

                if current_regime in stats["regimes"]:
                    regime_trades = stats["regimes"][current_regime]
                    if len(regime_trades) >= 3:
                        r_wins = [t.get("realized_r", 0) or 0 for t in regime_trades if (t.get("realized_r", 0) or 0) > 0]
                        r_losses = [abs(t.get("realized_r", 0) or 0) for t in regime_trades if (t.get("realized_r", 0) or 0) < 0]
                        r_all = [t.get("realized_r", 0) or 0 for t in regime_trades]

                        regime_pf = sum(r_wins) / max(0.01, sum(r_losses))
                        regime_ev = sum(r_all) / max(1, len(r_all))
                        regime_wr = len(r_wins) / max(1, len(regime_trades))

                # Recommendation
                if regime_pf > 1.2:
                    rec = "INCREASE"
                elif regime_pf > 1.0:
                    rec = "MAINTAIN"
                else:
                    rec = "REDUCE"

                matches.append(EnvironmentMatch(
                    historical_period=period,
                    match_score=match_score,
                    historical_pf=regime_pf,
                    historical_ev_r=regime_ev,
                    historical_win_rate=regime_wr,
                    trade_count=stats["trade_count"],
                    recommendation=rec,
                ))

        # Sort by match score
        matches.sort(key=lambda m: m.match_score, reverse=True)
        return matches[:5]  # Top 5 matches

    def get_period_stats(self, period: str) -> Optional[Dict]:
        """Get stats for a specific period."""
        self._ensure_loaded()
        return self._period_stats.get(period)

    def get_all_periods(self) -> Dict[str, Dict]:
        """Get stats for all periods."""
        self._ensure_loaded()
        return dict(self._period_stats)

    def get_summary(self) -> Dict[str, Any]:
        """Get market memory summary."""
        self._ensure_loaded()
        return {
            "total_periods": len(self._period_stats),
            "periods": list(self._period_stats.keys()),
        }
