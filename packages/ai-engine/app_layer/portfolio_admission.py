"""
Portfolio Admission Engine — Rank signals, trade only the best.

Per analysis of 343 production trades:
    - Win Rate: 35.3%, PF: 0.94, Expectancy: -$0.61/trade
    - RR is acceptable (1.54), but too many low-quality trades enter
    - Need: Rank signals → Take only top 20% → Reject the rest

This is the highest-expected-impact governance change because:
    - Break-even WR for 1.54 RR ≈ 39%, actual WR = 35.3%
    - Only 3.7 percentage points below break-even
    - Eliminating the weakest 10-20% of trades can push PF above 1.0

How it works:
    1. Score every incoming signal on a composite quality metric
    2. Compare against historical score distributions
    3. Only admit signals in the top N percentile
    4. Apply an absolute quality floor (never admit garbage)

This does NOT change signal generation, EMA V5, Smart Money, or RR Audit.
It only governs which signals the App is allowed to act on.

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# ═══════════════════════════════════════════════════════════════
# ADMISSION CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Percentile cutoff — only admit top N% of signals
ADMISSION_PERCENTILE = 20         # Top 20% only

# Absolute quality floor — never admit below this score
# (even if all signals are weak, don't admit garbage)
ABSOLUTE_QUALITY_FLOOR = 55.0     # Minimum composite score

# Minimum signals for percentile calculation to be meaningful
MIN_SIGNALS_FOR_PERCENTILE = 3

# Historical lookback for score distribution
HISTORY_LOOKBACK = 200            # Last N pipeline results for distribution

# Rolling performance tracking
ROLLING_WINDOW = 20               # Last N executed trades for rolling PF
ROLLING_PF_PAUSE = 0.80           # Pause if rolling PF drops below this

# Component weights for admission composite score
ADMISSION_WEIGHTS = {
    "trade_quality":         0.25,  # TQ engine composite score
    "execution_eligibility": 0.25,  # 9-dimension eligibility score
    "expected_profit":       0.20,  # Expected profit score
    "institution_agreement": 0.15,  # Institutional data consensus
    "risk_reward":           0.15,  # R:R ratio quality
}


@dataclass
class AdmissionScore:
    """Composite admission score for a signal."""
    symbol: str = ""
    side: str = ""
    composite_score: float = 0.0
    percentile: float = 0.0       # Where this score falls in distribution
    components: Dict[str, float] = field(default_factory=dict)
    admitted: bool = False
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "composite_score": round(self.composite_score, 1),
            "percentile": round(self.percentile, 1),
            "admitted": self.admitted,
            "reason": self.reason,
            "components": {k: round(v, 1) for k, v in self.components.items()},
        }


@dataclass
class AdmissionDecision:
    """Decision from the Portfolio Admission Engine."""
    admitted: bool = True
    score: Optional[AdmissionScore] = None
    rolling_pf: float = 0.0
    rolling_win_rate: float = 0.0
    rolling_trades: int = 0
    system_paused: bool = False
    pause_reason: str = ""
    rejection_reason: str = ""

    def to_dict(self) -> Dict:
        result = {
            "admitted": self.admitted,
            "rolling_pf": round(self.rolling_pf, 2),
            "rolling_win_rate": round(self.rolling_win_rate, 3),
            "rolling_trades": self.rolling_trades,
            "system_paused": self.system_paused,
            "pause_reason": self.pause_reason,
            "rejection_reason": self.rejection_reason,
        }
        if self.score:
            result["score"] = self.score.to_dict()
        return result


class PortfolioAdmissionEngine:
    """
    Ranks signals by composite quality and only admits the top percentile.

    Per production analysis:
        "The problem is that too many low-quality trades are entering
         the portfolio. You do not need to double the strategy's edge.
         You only need to eliminate a relatively small fraction of the
         weakest trades."

    This engine:
        1. Scores each signal on 5 quality dimensions
        2. Maintains a rolling distribution of recent scores
        3. Only admits signals above the percentile cutoff
        4. Tracks rolling PF and pauses if performance degrades

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH

        # Score history for percentile calculation
        self._score_history: List[float] = []
        self._max_history = HISTORY_LOOKBACK

        # Rolling performance tracking
        self._rolling_pf: float = 0.0
        self._rolling_win_rate: float = 0.0
        self._rolling_trades: int = 0
        self._system_paused: bool = False
        self._pause_reason: str = ""

        # Cache
        self._last_performance_check: float = 0.0
        self._cache_ttl = 60

        # Stats
        self._total_evaluated: int = 0
        self._total_admitted: int = 0
        self._total_rejected: int = 0

    # ─────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────

    def evaluate(
        self,
        signal: Dict[str, Any],
        trade_quality_score: float = 0.0,
        execution_score: float = 0.0,
        expected_profit_score: float = 0.0,
        institution_agreement: float = 0.0,
        risk_reward: float = 0.0,
    ) -> AdmissionDecision:
        """
        Evaluate whether a signal should be admitted to the portfolio.

        Args:
            signal: Signal dict
            trade_quality_score: Score from TradeQualityEngine (0-100)
            execution_score: Score from ExecutionEligibilityEngine (0-100)
            expected_profit_score: Score from Expected Profit calculation
            institution_agreement: Institution agreement ratio (0-1)
            risk_reward: R:R ratio

        Returns:
            AdmissionDecision with admit/reject and scoring details
        """
        self._total_evaluated += 1
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")

        # ── Check rolling performance pause ──
        self._refresh_rolling_performance()
        if self._system_paused:
            return AdmissionDecision(
                admitted=False,
                rolling_pf=self._rolling_pf,
                rolling_win_rate=self._rolling_win_rate,
                rolling_trades=self._rolling_trades,
                system_paused=True,
                pause_reason=self._pause_reason,
                rejection_reason=f"system_paused: {self._pause_reason}",
            )

        # ── Calculate composite admission score ──
        components = {
            "trade_quality": trade_quality_score,
            "execution_eligibility": execution_score,
            "expected_profit": min(100, expected_profit_score),
            "institution_agreement": institution_agreement * 100,
            "risk_reward": min(100, risk_reward * 20),  # Scale RR to 0-100
        }

        composite = sum(
            components.get(k, 0) * w
            for k, w in ADMISSION_WEIGHTS.items()
        )

        # ── Record score for distribution tracking ──
        self._score_history.append(composite)
        if len(self._score_history) > self._max_history:
            self._score_history = self._score_history[-self._max_history:]

        # ── Calculate percentile ──
        percentile = self._calculate_percentile(composite)

        # ── Admission decision ──
        admission_score = AdmissionScore(
            symbol=symbol,
            side=side,
            composite_score=composite,
            percentile=percentile,
            components=components,
        )

        # Check absolute floor
        if composite < ABSOLUTE_QUALITY_FLOOR:
            admission_score.admitted = False
            admission_score.reason = (
                f"below_absolute_floor: {composite:.1f} < {ABSOLUTE_QUALITY_FLOOR:.1f}"
            )
            self._total_rejected += 1
            return AdmissionDecision(
                admitted=False,
                score=admission_score,
                rolling_pf=self._rolling_pf,
                rolling_win_rate=self._rolling_win_rate,
                rolling_trades=self._rolling_trades,
                rejection_reason=admission_score.reason,
            )

        # Check percentile cutoff (need enough history)
        if len(self._score_history) >= MIN_SIGNALS_FOR_PERCENTILE:
            # Only admit if in top N percentile
            # percentile=80 means this score is better than 80% of history
            # We want top 20%, so admit if percentile >= 80
            cutoff = 100 - ADMISSION_PERCENTILE

            if percentile < cutoff:
                admission_score.admitted = False
                admission_score.reason = (
                    f"below_percentile_cutoff: score={composite:.1f} "
                    f"percentile={percentile:.1f} < {cutoff:.1f} "
                    f"(top {ADMISSION_PERCENTILE}% required)"
                )
                self._total_rejected += 1
                logger.debug(
                    "ADMISSION REJECT: {} {} — score={:.1f} percentile={:.1f} < {:.1f}",
                    symbol, side, composite, percentile, cutoff,
                )
                return AdmissionDecision(
                    admitted=False,
                    score=admission_score,
                    rolling_pf=self._rolling_pf,
                    rolling_win_rate=self._rolling_win_rate,
                    rolling_trades=self._rolling_trades,
                    rejection_reason=admission_score.reason,
                )

        # Admitted
        admission_score.admitted = True
        admission_score.reason = f"admitted: score={composite:.1f} percentile={percentile:.1f}"
        self._total_admitted += 1

        logger.info(
            "✅ ADMISSION: {} {} — score={:.1f} percentile={:.1f} "
            "(TQ={:.0f} EP={:.0f} EPS={:.0f} inst={:.0%} RR={:.1f})",
            symbol, side, composite, percentile,
            trade_quality_score, execution_score, expected_profit_score,
            institution_agreement, risk_reward,
        )

        return AdmissionDecision(
            admitted=True,
            score=admission_score,
            rolling_pf=self._rolling_pf,
            rolling_win_rate=self._rolling_win_rate,
            rolling_trades=self._rolling_trades,
        )

    def record_outcome(self, pnl: float) -> None:
        """
        Record a trade outcome for rolling performance tracking.

        Args:
            pnl: Realized PnL from closed trade
        """
        # This is called after a trade closes to update rolling stats
        self._last_performance_check = 0  # Force refresh

    def get_status(self) -> Dict:
        """Get complete admission engine status."""
        self._refresh_rolling_performance()
        return {
            "total_evaluated": self._total_evaluated,
            "total_admitted": self._total_admitted,
            "total_rejected": self._total_rejected,
            "admission_rate": (
                round(self._total_admitted / max(1, self._total_evaluated) * 100, 1)
            ),
            "rolling_pf": round(self._rolling_pf, 2),
            "rolling_win_rate": round(self._rolling_win_rate, 3),
            "rolling_trades": self._rolling_trades,
            "system_paused": self._system_paused,
            "pause_reason": self._pause_reason,
            "score_history_size": len(self._score_history),
            "percentile_cutoff": 100 - ADMISSION_PERCENTILE,
            "absolute_floor": ABSOLUTE_QUALITY_FLOOR,
            "config": {
                "admission_percentile": ADMISSION_PERCENTILE,
                "absolute_quality_floor": ABSOLUTE_QUALITY_FLOOR,
                "rolling_window": ROLLING_WINDOW,
                "rolling_pf_pause": ROLLING_PF_PAUSE,
            },
        }

    # ─────────────────────────────────────────────────────────
    # INTERNAL METHODS
    # ─────────────────────────────────────────────────────────

    def _calculate_percentile(self, score: float) -> float:
        """Calculate what percentile a score falls in within the history."""
        if not self._score_history:
            return 50.0  # No history — neutral

        below = sum(1 for s in self._score_history if s < score)
        return (below / len(self._score_history)) * 100

    def _refresh_rolling_performance(self) -> None:
        """Refresh rolling PF from trade history."""
        now = time.time()
        if now - self._last_performance_check < self._cache_ttl:
            return
        self._last_performance_check = now

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Get last N closed trades
            cur.execute("""
                SELECT pnl
                FROM positions
                WHERE status = 'closed' AND pnl IS NOT NULL
                ORDER BY closed_at DESC
                LIMIT ?
            """, (ROLLING_WINDOW,))
            rows = cur.fetchall()
            conn.close()

            if not rows:
                return

            pnls = [r[0] for r in rows]
            wins = sum(1 for p in pnls if p > 0)
            self._rolling_trades = len(pnls)
            self._rolling_win_rate = wins / len(pnls) if pnls else 0

            gross_profit = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p < 0))
            self._rolling_pf = gross_profit / gross_loss if gross_loss > 0 else (
                float('inf') if gross_profit > 0 else 0
            )

            # Check pause condition
            if (self._rolling_trades >= ROLLING_WINDOW
                    and self._rolling_pf < ROLLING_PF_PAUSE):
                if not self._system_paused:
                    self._system_paused = True
                    self._pause_reason = (
                        f"Rolling {self._rolling_trades}-trade PF="
                        f"{self._rolling_pf:.2f} < {ROLLING_PF_PAUSE:.2f}"
                    )
                    logger.warning(
                        "🔴 ADMISSION PAUSED: Rolling {}-trade PF={:.2f} < {:.2f}",
                        self._rolling_trades, self._rolling_pf, ROLLING_PF_PAUSE,
                    )
            elif self._system_paused and self._rolling_pf >= 1.0:
                # Resume when PF recovers to 1.0
                self._system_paused = False
                logger.info(
                    "🟢 ADMISSION RESUMED: Rolling PF recovered to {:.2f}",
                    self._rolling_pf,
                )

        except Exception as e:
            logger.warning("Admission rolling performance check error: {}", e)
