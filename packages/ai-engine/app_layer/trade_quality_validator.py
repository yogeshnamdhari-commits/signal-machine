"""
Trade Quality Validator — Pre-eligibility historical expectancy filter.

Per Executive Assessment v3:
    "I would insert one more stage:
        Raw Signals → Trade Quality Validation → Eligibility → Portfolio Ranking → Execution

     This validator should reject signals that historically have poor
     expectancy even if they satisfy all technical conditions."

Key Insight:
    The current pipeline has:
        Stage 1: Trade Quality Score (technical signal quality)
        Stage 9.5: Execution Eligibility (9-dimension composite)

    But there's NO validation of whether signals with these characteristics
    have historically been profitable. A signal can score well technically
    but still be from a symbol/session/regime combination that loses money.

    This engine adds that missing validation layer.

Components:
    1. Symbol Expectancy — Historical EV for this symbol (rolling 40-60 trades)
    2. Session Expectancy — Historical EV for this session
    3. Regime Expectancy — Historical EV for this market regime
    4. Side Bias — Is this symbol historically better LONG or SHORT?
    5. Recent Momentum — Is this symbol's PF improving or declining?
    6. Confidence-Expectancy Alignment — Does confidence predict actual outcomes?

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
# VALIDATION CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Minimum trades for reliable rolling statistics
MIN_TRADES_FOR_VALIDATION = 15

# Rolling window size (number of recent trades)
ROLLING_WINDOW = 50

# Component weights (must sum to 1.0)
VALIDATION_WEIGHTS = {
    "symbol_expectancy":   0.30,  # Historical EV for this symbol
    "session_expectancy":  0.15,  # Historical EV for this session
    "regime_expectancy":   0.15,  # Historical EV for this regime
    "side_bias":           0.10,  # Is this side historically profitable?
    "recent_momentum":     0.15,  # Is PF improving or declining?
    "confidence_alignment": 0.15, # Does confidence predict outcomes?
}

# Minimum validation score to pass
MIN_VALIDATION_SCORE = 60

# Block thresholds
BLOCK_PF_THRESHOLD = 0.8    # Block symbols with rolling PF < 0.8
BLOCK_EV_THRESHOLD = -0.3   # Block if rolling EV < -0.3R


@dataclass
class RollingStats:
    """Rolling window statistics for a specific dimension."""
    dimension: str = ""
    sample_size: int = 0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    avg_r: float = 0.0
    trend: str = ""          # IMPROVING / STABLE / DECLINING
    trend_strength: float = 0.0  # -1.0 to 1.0

    def to_dict(self) -> Dict:
        return {
            "dimension": self.dimension,
            "sample_size": self.sample_size,
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "trend": self.trend,
            "trend_strength": round(self.trend_strength, 3),
        }


@dataclass
class ValidationComponent:
    """Score for a single validation component."""
    name: str
    raw_value: float
    score: float        # 0-100
    weight: float
    weighted: float     # score * weight
    stats: Optional[RollingStats] = None
    detail: str = ""


@dataclass
class ValidationResult:
    """Result from the Trade Quality Validator."""
    symbol: str = ""
    side: str = ""
    session: str = ""
    regime: str = ""
    valid: bool = False
    validation_score: float = 0.0
    components: List[ValidationComponent] = field(default_factory=list)
    rejection_reason: str = ""
    blocking_reason: str = ""   # If symbol/session is blocked
    is_blocked: bool = False

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "session": self.session,
            "regime": self.regime,
            "valid": self.valid,
            "validation_score": round(self.validation_score, 1),
            "rejection_reason": self.rejection_reason,
            "blocking_reason": self.blocking_reason,
            "is_blocked": self.is_blocked,
            "components": {
                c.name: {
                    "raw": round(c.raw_value, 3),
                    "score": round(c.score, 1),
                    "weighted": round(c.weighted, 2),
                    "detail": c.detail,
                }
                for c in self.components
            },
        }


class TradeQualityValidator:
    """
    Validates signals against historical performance BEFORE eligibility.

    Per Executive Assessment v3:
        "Reject signals that historically have poor expectancy even if
         they satisfy all technical conditions."

    This engine:
        1. Loads rolling statistics from positions_archive
        2. Validates symbol/session/regime expectancy
        3. Checks side bias (some symbols are better LONG or SHORT)
        4. Detects declining performance trends
        5. Validates confidence-expectancy alignment

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._all_trades: List[Dict] = []
        self._last_load = 0.0

        # Cached rolling stats
        self._symbol_rolling: Dict[str, RollingStats] = {}
        self._session_rolling: Dict[str, RollingStats] = {}
        self._regime_rolling: Dict[str, RollingStats] = {}
        self._symbol_side_rolling: Dict[str, Dict[str, RollingStats]] = {}
        self._confidence_buckets: Dict[str, RollingStats] = {}

    # ═══════════════════════════════════════════════════════════════
    # DATA LOADING
    # ═══════════════════════════════════════════════════════════════

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale (> 5 minutes)."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades and compute rolling statistics."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, entry_price, pnl, fees, hold_minutes,
                       exit_reason, mfe_pct, mae_pct, highest_pnl,
                       realized_r, session, opened_at, closed_at,
                       confidence, regime, institutional_score, risk_reward
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._all_trades = [dict(r) for r in rows]

            if not self._all_trades:
                logger.info("📊 No trades for quality validation")
                return

            # Compute rolling statistics
            self._compute_rolling_stats()

            self._last_load = time.time()

            logger.info(
                "📊 Quality Validator loaded: {} trades, {} symbols, {} sessions",
                len(self._all_trades),
                len(self._symbol_rolling),
                len(self._session_rolling),
            )

        except Exception as e:
            logger.warning("Could not load quality validation stats: {}", e)

    def _compute_rolling_stats(self) -> None:
        """Compute rolling statistics for all dimensions."""
        # ── By Symbol ──
        by_symbol = defaultdict(list)
        for t in self._all_trades:
            by_symbol[t.get("symbol", "")].append(t)

        self._symbol_rolling = {}
        self._symbol_side_rolling = {}
        for sym, trades in by_symbol.items():
            self._symbol_rolling[sym] = self._calc_rolling(trades, f"symbol:{sym}")
            # By side
            by_side = defaultdict(list)
            for t in trades:
                by_side[t.get("side", "LONG")].append(t)
            self._symbol_side_rolling[sym] = {}
            for side, side_trades in by_side.items():
                self._symbol_side_rolling[sym][side] = self._calc_rolling(
                    side_trades, f"symbol:{sym}:side:{side}"
                )

        # ── By Session ──
        by_session = defaultdict(list)
        for t in self._all_trades:
            by_session[t.get("session", "unknown")].append(t)

        self._session_rolling = {}
        for sess, trades in by_session.items():
            self._session_rolling[sess] = self._calc_rolling(trades, f"session:{sess}")

        # ── By Regime ──
        by_regime = defaultdict(list)
        for t in self._all_trades:
            by_regime[t.get("regime", t.get("at_open_regime", "unknown"))].append(t)

        self._regime_rolling = {}
        for regime, trades in by_regime.items():
            self._regime_rolling[regime] = self._calc_rolling(trades, f"regime:{regime}")

        # ── Confidence Buckets ──
        conf_buckets = defaultdict(list)
        for t in self._all_trades:
            conf = t.get("confidence", 0)
            if conf >= 95:
                bucket = "95+"
            elif conf >= 90:
                bucket = "90-95"
            elif conf >= 85:
                bucket = "85-90"
            else:
                bucket = "<85"
            conf_buckets[bucket].append(t)

        self._confidence_buckets = {}
        for bucket, trades in conf_buckets.items():
            self._confidence_buckets[bucket] = self._calc_rolling(
                trades, f"confidence:{bucket}"
            )

    def _calc_rolling(self, trades: List[Dict], dimension: str) -> RollingStats:
        """Calculate rolling statistics for a list of trades."""
        stats = RollingStats(dimension=dimension, sample_size=len(trades))

        if not trades:
            return stats

        # Take most recent ROLLING_WINDOW trades
        recent = trades[:ROLLING_WINDOW]

        wins = []
        losses = []
        for t in recent:
            r = t.get("realized_r", 0) or 0
            if r > 0:
                wins.append(r)
            else:
                losses.append(abs(r))

        stats.win_rate = len(wins) / max(1, len(recent))
        stats.avg_r = sum(r for r in [t.get("realized_r", 0) or 0 for t in recent]) / max(1, len(recent))

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        stats.profit_factor = gross_profit / max(0.01, gross_loss)
        stats.expectancy_r = stats.avg_r

        # ── Trend Detection ──
        if len(recent) >= 20:
            first_half = recent[len(recent)//2:]
            second_half = recent[:len(recent)//2]

            first_r = sum(t.get("realized_r", 0) or 0 for t in first_half) / max(1, len(first_half))
            second_r = sum(t.get("realized_r", 0) or 0 for t in second_half) / max(1, len(second_half))

            diff = second_r - first_r
            stats.trend_strength = max(-1.0, min(1.0, diff * 2))

            if diff > 0.1:
                stats.trend = "IMPROVING"
            elif diff < -0.1:
                stats.trend = "DECLINING"
            else:
                stats.trend = "STABLE"

        return stats

    # ═══════════════════════════════════════════════════════════════
    # VALIDATION
    # ═══════════════════════════════════════════════════════════════

    def validate(
        self,
        signal: Dict[str, Any],
    ) -> ValidationResult:
        """
        Validate a signal against historical performance.

        Args:
            signal: Live Sheet signal dict

        Returns:
            ValidationResult with score and decision
        """
        self._ensure_loaded()

        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        session = signal.get("session", signal.get("at_open_session", "unknown"))
        regime = signal.get("regime", signal.get("market_regime", "unknown"))
        confidence = signal.get("confidence", 0)

        result = ValidationResult(
            symbol=symbol, side=side, session=session, regime=regime,
        )

        # ── Check if symbol is blocked ──
        sym_stats = self._symbol_rolling.get(symbol)
        if sym_stats and sym_stats.sample_size >= MIN_TRADES_FOR_VALIDATION:
            if sym_stats.profit_factor < BLOCK_PF_THRESHOLD:
                result.is_blocked = True
                result.blocking_reason = (
                    f"symbol_blocked: PF={sym_stats.profit_factor:.2f} "
                    f"< {BLOCK_PF_THRESHOLD} ({sym_stats.sample_size} trades)"
                )
                result.valid = False
                result.rejection_reason = result.blocking_reason
                return result

            if sym_stats.expectancy_r < BLOCK_EV_THRESHOLD:
                result.is_blocked = True
                result.blocking_reason = (
                    f"symbol_blocked: EV={sym_stats.expectancy_r:.3f}R "
                    f"< {BLOCK_EV_THRESHOLD}R ({sym_stats.sample_size} trades)"
                )
                result.valid = False
                result.rejection_reason = result.blocking_reason
                return result

        # ── Component 1: Symbol Expectancy (30%) ──
        sym_rolling = self._symbol_rolling.get(symbol)
        sym_score = self._score_expectancy(sym_rolling)
        result.components.append(ValidationComponent(
            name="symbol_expectancy",
            raw_value=sym_rolling.expectancy_r if sym_rolling else 0,
            score=sym_score,
            weight=VALIDATION_WEIGHTS["symbol_expectancy"],
            weighted=sym_score * VALIDATION_WEIGHTS["symbol_expectancy"],
            stats=sym_rolling,
            detail=f"PF={sym_rolling.profit_factor:.2f} EV={sym_rolling.expectancy_r:.3f}R n={sym_rolling.sample_size}" if sym_rolling else "no data",
        ))

        # ── Component 2: Session Expectancy (15%) ──
        sess_rolling = self._session_rolling.get(session)
        sess_score = self._score_expectancy(sess_rolling)
        result.components.append(ValidationComponent(
            name="session_expectancy",
            raw_value=sess_rolling.expectancy_r if sess_rolling else 0,
            score=sess_score,
            weight=VALIDATION_WEIGHTS["session_expectancy"],
            weighted=sess_score * VALIDATION_WEIGHTS["session_expectancy"],
            stats=sess_rolling,
            detail=f"PF={sess_rolling.profit_factor:.2f} n={sess_rolling.sample_size}" if sess_rolling else "no data",
        ))

        # ── Component 3: Regime Expectancy (15%) ──
        regime_rolling = self._regime_rolling.get(regime)
        regime_score = self._score_expectancy(regime_rolling)
        result.components.append(ValidationComponent(
            name="regime_expectancy",
            raw_value=regime_rolling.expectancy_r if regime_rolling else 0,
            score=regime_score,
            weight=VALIDATION_WEIGHTS["regime_expectancy"],
            weighted=regime_score * VALIDATION_WEIGHTS["regime_expectancy"],
            stats=regime_rolling,
            detail=f"PF={regime_rolling.profit_factor:.2f} n={regime_rolling.sample_size}" if regime_rolling else "no data",
        ))

        # ── Component 4: Side Bias (10%) ──
        side_rolling = self._symbol_side_rolling.get(symbol, {}).get(side)
        opp_rolling = self._symbol_side_rolling.get(symbol, {}).get(
            "SHORT" if side == "LONG" else "LONG"
        )
        side_score = self._score_side_bias(side_rolling, opp_rolling)
        result.components.append(ValidationComponent(
            name="side_bias",
            raw_value=side_rolling.expectancy_r if side_rolling else 0,
            score=side_score,
            weight=VALIDATION_WEIGHTS["side_bias"],
            weighted=side_score * VALIDATION_WEIGHTS["side_bias"],
            stats=side_rolling,
            detail=f"side={side} EV={side_rolling.expectancy_r:.3f}R" if side_rolling else f"side={side} no data",
        ))

        # ── Component 5: Recent Momentum (15%) ──
        momentum_score = self._score_recent_momentum(sym_rolling)
        result.components.append(ValidationComponent(
            name="recent_momentum",
            raw_value=sym_rolling.trend_strength if sym_rolling else 0,
            score=momentum_score,
            weight=VALIDATION_WEIGHTS["recent_momentum"],
            weighted=momentum_score * VALIDATION_WEIGHTS["recent_momentum"],
            stats=sym_rolling,
            detail=f"trend={sym_rolling.trend} strength={sym_rolling.trend_strength:.2f}" if sym_rolling else "no data",
        ))

        # ── Component 6: Confidence Alignment (15%) ──
        conf = confidence if confidence <= 100 else confidence * 100
        if conf >= 95:
            conf_bucket = "95+"
        elif conf >= 90:
            conf_bucket = "90-95"
        elif conf >= 85:
            conf_bucket = "85-90"
        else:
            conf_bucket = "<85"

        conf_rolling = self._confidence_buckets.get(conf_bucket)
        conf_score = self._score_confidence_alignment(conf_rolling, conf)
        result.components.append(ValidationComponent(
            name="confidence_alignment",
            raw_value=conf_rolling.expectancy_r if conf_rolling else 0,
            score=conf_score,
            weight=VALIDATION_WEIGHTS["confidence_alignment"],
            weighted=conf_score * VALIDATION_WEIGHTS["confidence_alignment"],
            stats=conf_rolling,
            detail=f"bucket={conf_bucket} EV={conf_rolling.expectancy_r:.3f}R" if conf_rolling else f"bucket={conf_bucket} no data",
        ))

        # ── Composite Score ──
        result.validation_score = sum(c.weighted for c in result.components)

        # ── Validation Decision ──
        result.valid = result.validation_score >= MIN_VALIDATION_SCORE

        if not result.valid:
            weakest = min(result.components, key=lambda c: c.score)
            result.rejection_reason = (
                f"score={result.validation_score:.1f} < {MIN_VALIDATION_SCORE} "
                f"(weakest: {weakest.name}={weakest.score:.1f})"
            )

        logger.debug(
            "VALIDATOR: {} {} {} → {:.1f}/100 {}",
            symbol, side, session, result.validation_score,
            "✓ VALID" if result.valid else "✗ REJECTED",
        )

        return result

    # ═══════════════════════════════════════════════════════════════
    # SCORING FUNCTIONS (0-100)
    # ═══════════════════════════════════════════════════════════════

    def _score_expectancy(self, rolling: Optional[RollingStats]) -> float:
        """Score based on rolling expectancy: EV > 0.3R = 100, < -0.2R = 0."""
        if not rolling or rolling.sample_size < MIN_TRADES_FOR_VALIDATION:
            return 50  # No data — neutral

        ev = rolling.expectancy_r
        if ev >= 0.5:
            return 100
        elif ev >= 0.3:
            return 80 + (ev - 0.3) * 100  # 80-100
        elif ev >= 0.1:
            return 60 + (ev - 0.1) * 100  # 60-80
        elif ev >= 0.0:
            return 50 + ev * 100  # 50-60
        elif ev >= -0.1:
            return 30 + (ev + 0.1) * 200  # 30-50
        elif ev >= -0.3:
            return 10 + (ev + 0.3) * 100  # 10-30
        return max(0, 10 + ev * 33)

    def _score_side_bias(self, side_rolling: Optional[RollingStats], opp_rolling: Optional[RollingStats]) -> float:
        """Score based on whether this side is historically profitable."""
        if not side_rolling or side_rolling.sample_size < 5:
            return 50  # No data — neutral

        side_ev = side_rolling.expectancy_r

        # Compare to opposite side
        if opp_rolling and opp_rolling.sample_size >= 5:
            opp_ev = opp_rolling.expectancy_r
            if side_ev > opp_ev + 0.2:
                return 90  # This side is significantly better
            elif side_ev > opp_ev:
                return 70  # This side is slightly better
            elif side_ev > opp_ev - 0.2:
                return 50  # Roughly equal
            else:
                return 25  # This side is worse

        # No opposite side data — score by absolute EV
        return self._score_expectancy(side_rolling)

    def _score_recent_momentum(self, rolling: Optional[RollingStats]) -> float:
        """Score based on whether performance is improving or declining."""
        if not rolling or rolling.sample_size < 20:
            return 50  # No data — neutral

        if rolling.trend == "IMPROVING":
            return 70 + rolling.trend_strength * 30  # 70-100
        elif rolling.trend == "DECLINING":
            return 30 + rolling.trend_strength * 30  # 0-30 (trend_strength is negative)
        return 60  # Stable — slightly positive

    def _score_confidence_alignment(self, rolling: Optional[RollingStats], confidence: float) -> float:
        """Score based on whether confidence historically predicts outcomes."""
        if not rolling or rolling.sample_size < 10:
            return 50  # No data — neutral

        # High confidence bucket should have better EV
        ev = rolling.expectancy_r
        wr = rolling.win_rate

        if confidence >= 90:
            # High confidence — should have strong positive EV
            if ev >= 0.3 and wr >= 0.4:
                return 90  # Confident AND profitable
            elif ev >= 0.1:
                return 60  # Confident but marginal
            else:
                return 20  # Confident but losing — misaligned
        elif confidence >= 85:
            if ev >= 0.1:
                return 70
            elif ev >= -0.1:
                return 50
            else:
                return 30
        else:
            # Lower confidence — more lenient
            if ev >= 0:
                return 60
            else:
                return 40

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════

    def get_symbol_stats(self, symbol: str) -> Optional[RollingStats]:
        """Get rolling stats for a symbol."""
        self._ensure_loaded()
        return self._symbol_rolling.get(symbol)

    def get_session_stats(self, session: str) -> Optional[RollingStats]:
        """Get rolling stats for a session."""
        self._ensure_loaded()
        return self._session_rolling.get(session)

    def get_blocked_symbols(self) -> List[str]:
        """Get list of symbols that should be blocked."""
        self._ensure_loaded()
        blocked = []
        for sym, stats in self._symbol_rolling.items():
            if stats.sample_size >= MIN_TRADES_FOR_VALIDATION:
                if stats.profit_factor < BLOCK_PF_THRESHOLD:
                    blocked.append(sym)
                elif stats.expectancy_r < BLOCK_EV_THRESHOLD:
                    blocked.append(sym)
        return blocked

    def get_summary(self) -> Dict[str, Any]:
        """Get complete validation summary."""
        self._ensure_loaded()
        return {
            "total_trades": len(self._all_trades),
            "symbols_tracked": len(self._symbol_rolling),
            "sessions_tracked": len(self._session_rolling),
            "regimes_tracked": len(self._regime_rolling),
            "blocked_symbols": self.get_blocked_symbols(),
            "top_symbols": sorted(
                [(s, r.profit_factor) for s, r in self._symbol_rolling.items()
                 if r.sample_size >= MIN_TRADES_FOR_VALIDATION],
                key=lambda x: x[1], reverse=True,
            )[:10],
            "worst_symbols": sorted(
                [(s, r.profit_factor) for s, r in self._symbol_rolling.items()
                 if r.sample_size >= MIN_TRADES_FOR_VALIDATION],
                key=lambda x: x[1],
            )[:10],
        }
