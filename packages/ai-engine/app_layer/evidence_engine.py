"""
Evidence-Based Decision Engine — Learn from historical trades to improve future decisions.

Production analysis (343 trades, PF 0.94, expectancy -$0.61):
    - Too many trades still reaching execution
    - Need data-driven decisions, not rule-driven
    - Every closed trade should update statistics immediately
    - Before opening, ask: "How have similar setups performed?"

This engine provides:
    1. Trade Replay Database — Store complete trade snapshots
    2. Trade Quality Index (TQI) — Historical expectancy from similar trades
    3. Similarity Search — Find 30-50 most similar past trades
    4. Adaptive Thresholds — Rolling percentiles instead of fixed cutoffs
    5. Self-Learning Feedback Loop — Update all stats after every close

All within the App Layer. No changes to EMA V5, Smart Money, RR Audit,
or Research Platform.

READ-ONLY with respect to upstream data.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Similarity search
SIMILARITY_K = 40               # Number of similar trades to find
SIMILARITY_MIN_MATCHES = 10     # Minimum similar trades for reliable TQI

# TQI thresholds
TQI_ADMIT_THRESHOLD = 0.55      # Admit if expected win rate > 55%
TQI_STRONG_THRESHOLD = 0.65     # Strong admit (boost sizing)
TQI_REJECT_THRESHOLD = 0.40     # Reject if expected win rate < 40%

# Adaptive thresholds — rolling window
ADAPTIVE_WINDOW = 200           # Last N trades for percentile calculation
ADAPTIVE_REFRESH_SEC = 60       # Refresh thresholds every 60 seconds

# Feature weights for similarity calculation
FEATURE_WEIGHTS = {
    "symbol":           0.15,
    "side":             0.05,
    "session":          0.12,
    "regime":           0.12,
    "confidence_bucket": 0.15,
    "rr_bucket":        0.10,
    "volatility_bucket": 0.08,
    "hour_of_day":      0.08,
    "institutional_bucket": 0.08,
    "trend_strength_bucket": 0.07,
}

# Buckets for continuous features
CONFIDENCE_BUCKETS = [(0, 70), (70, 80), (80, 85), (85, 90), (90, 95), (95, 100)]
RR_BUCKETS = [(0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 999)]
VOLATILITY_BUCKETS = [(0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 999)]
INST_BUCKETS = [(0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
TREND_BUCKETS = [(-1, -0.5), (-0.5, 0), (0, 0.5), (0.5, 1.0)]


# ═══════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════

@dataclass
class TradeSnapshot:
    """Complete snapshot of a trade for the replay database."""
    # Identity
    trade_id: str = ""
    symbol: str = ""
    side: str = ""

    # Entry conditions
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    quantity: float = 0.0
    confidence: float = 0.0
    regime: str = ""
    session: str = ""

    # Market context at entry
    entry_flow: float = 0.0
    entry_cvd: float = 0.0
    entry_oi_change: float = 0.0
    entry_volume_ratio: float = 0.0
    entry_funding_rate: float = 0.0
    entry_institutional_score: float = 0.0
    entry_atr_pct: float = 0.0
    entry_trend_strength: float = 0.0
    entry_hour_utc: int = 0

    # Outcome
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    realized_r: float = 0.0
    hold_minutes: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0

    # App metrics
    admission_score: float = 0.0
    quality_score: float = 0.0
    peak_confidence: float = 0.0
    min_confidence: float = 0.0
    reductions: int = 0

    # Timestamps
    opened_at: float = 0.0
    closed_at: float = 0.0

    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items()}

    def get_feature_vector(self) -> Dict[str, str]:
        """Get discretized feature vector for similarity matching."""
        return {
            "symbol": self.symbol,
            "side": self.side,
            "session": self.session or "unknown",
            "regime": self.regime or "unknown",
            "confidence_bucket": _bucket(self.confidence, CONFIDENCE_BUCKETS),
            "rr_bucket": _bucket(
                abs(self.take_profit - self.entry_price) / max(0.01, abs(self.entry_price - self.stop_loss)),
                RR_BUCKETS,
            ),
            "volatility_bucket": _bucket(self.entry_atr_pct, VOLATILITY_BUCKETS),
            "hour_of_day": str(self.entry_hour_utc // 6),  # 4 buckets: 0-5, 6-11, 12-17, 18-23
            "institutional_bucket": _bucket(self.entry_institutional_score, INST_BUCKETS),
            "trend_strength_bucket": _bucket(self.entry_trend_strength, TREND_BUCKETS),
        }


@dataclass
class TQIResult:
    """Trade Quality Index result."""
    symbol: str = ""
    side: str = ""
    tqi: float = 0.0              # Expected win rate from similar trades (0-1)
    similar_count: int = 0        # Number of similar trades found
    similar_win_rate: float = 0.0
    similar_pf: float = 0.0
    similar_avg_r: float = 0.0
    similar_avg_pnl: float = 0.0
    confidence: str = "LOW"       # LOW / MEDIUM / HIGH
    admit: bool = True
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "tqi": round(self.tqi, 3),
            "similar_count": self.similar_count,
            "similar_win_rate": round(self.similar_win_rate, 3),
            "similar_pf": round(self.similar_pf, 2),
            "similar_avg_r": round(self.similar_avg_r, 3),
            "similar_avg_pnl": round(self.similar_avg_pnl, 2),
            "confidence": self.confidence,
            "admit": self.admit,
            "reason": self.reason,
        }


@dataclass
class AdaptiveThreshold:
    """An adaptive threshold based on rolling percentiles."""
    name: str = ""
    current_value: float = 0.0
    p25: float = 0.0
    p50: float = 0.0
    p75: float = 0.0
    p85: float = 0.0
    p90: float = 0.0
    sample_size: int = 0

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "current": round(self.current_value, 2),
            "p25": round(self.p25, 2),
            "p50": round(self.p50, 2),
            "p75": round(self.p75, 2),
            "p85": round(self.p85, 2),
            "p90": round(self.p90, 2),
            "sample_size": self.sample_size,
        }


@dataclass
class LearningUpdate:
    """Result of a self-learning update after trade close."""
    trade_id: str = ""
    symbol: str = ""
    session: str = ""
    updated_symbols: List[str] = field(default_factory=list)
    updated_sessions: List[str] = field(default_factory=list)
    updated_buckets: List[str] = field(default_factory=list)
    tqi_stored: bool = False

    def to_dict(self) -> Dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "session": self.session,
            "updated_symbols": self.updated_symbols,
            "updated_sessions": self.updated_sessions,
            "updated_buckets": self.updated_buckets,
            "tqi_stored": self.tqi_stored,
        }


# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def _bucket(value: float, buckets: List[Tuple[float, float]]) -> str:
    """Discretize a continuous value into a named bucket."""
    for low, high in buckets:
        if low <= value < high:
            return f"{low}-{high}"
    return f"{buckets[-1][0]}+" if buckets else "unknown"


def _percentile(sorted_vals: List[float], p: float) -> float:
    """Calculate the p-th percentile from a sorted list."""
    if not sorted_vals:
        return 0.0
    idx = (p / 100) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _similarity_score(
    feat_a: Dict[str, str],
    feat_b: Dict[str, str],
) -> float:
    """Calculate similarity between two feature vectors (0-1, 1=identical)."""
    score = 0.0
    for feature, weight in FEATURE_WEIGHTS.items():
        if feat_a.get(feature) == feat_b.get(feature):
            score += weight
    return score


# ═══════════════════════════════════════════════════════════════
# EVIDENCE-BASED DECISION ENGINE
# ═══════════════════════════════════════════════════════════════

class EvidenceBasedDecisionEngine:
    """
    Case-based decision engine that learns from historical trades.

    Before every new trade:
        1. Build feature vector for the candidate trade
        2. Find K most similar historical trades
        3. Compute expected outcome from similar trades
        4. Admit/reject based on historical evidence

    After every closed trade:
        1. Store complete trade snapshot
        2. Update symbol/session/bucket statistics
        3. Recompute adaptive thresholds
        4. Feed back into next decision

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH

        # Trade replay database (in-memory cache)
        self._snapshots: List[TradeSnapshot] = []
        self._max_snapshots = 2000
        self._loaded = False

        # Adaptive thresholds
        self._thresholds: Dict[str, AdaptiveThreshold] = {}
        self._last_threshold_refresh: float = 0.0

        # Statistics caches
        self._symbol_pf_cache: Dict[str, float] = {}
        self._session_pf_cache: Dict[str, float] = {}

        # Stats
        self._total_queries: int = 0
        self._total_admits: int = 0
        self._total_rejects: int = 0

        # Decision log for acceptance/rejection validation
        self._decision_log: List[Dict] = []

        # Shadow trades for false rejection rate
        self._shadow_trades: List[Dict] = []

        # Version history for longitudinal comparison
        self._version_history: List[Dict] = []

        # Bundle deployment history for drift tracking
        self._bundle_history: List[Dict] = []

        # Champion-challenger tracking
        self._champion_challenger: Optional[Dict] = None

        # Permanent parameter change history
        self._parameter_history: List[Dict] = []

    # ─────────────────────────────────────────────────────────
    # PUBLIC API — PRE-TRADE
    # ─────────────────────────────────────────────────────────

    def query_tqi(
        self,
        signal: Dict[str, Any],
        market_data: Optional[Dict] = None,
    ) -> TQIResult:
        """
        Query Trade Quality Index — how have similar trades performed?

        Args:
            signal: Candidate signal dict
            market_data: Optional live market data

        Returns:
            TQIResult with expected outcome from similar historical trades
        """
        self._total_queries += 1
        self._ensure_loaded()

        symbol = signal.get("symbol", "")
        side = signal.get("side", "")

        # Build feature vector for candidate
        candidate_features = self._build_feature_vector(signal, market_data)

        # Find K most similar trades
        similarities = []
        for snap in self._snapshots:
            snap_features = snap.get_feature_vector()
            score = _similarity_score(candidate_features, snap_features)
            similarities.append((score, snap))

        # Sort by similarity (highest first)
        similarities.sort(key=lambda x: x[0], reverse=True)

        # Take top K
        top_k = similarities[:SIMILARITY_K]

        if len(top_k) < SIMILARITY_MIN_MATCHES:
            return TQIResult(
                symbol=symbol,
                side=side,
                tqi=0.5,  # Neutral when insufficient data
                similar_count=len(top_k),
                confidence="LOW",
                admit=True,  # Don't reject with insufficient data
                reason=f"insufficient_similar_trades ({len(top_k)} < {SIMILARITY_MIN_MATCHES})",
            )

        # Calculate expected outcome from similar trades
        similar_trades = [s for _, s in top_k]
        wins = sum(1 for s in similar_trades if s.pnl > 0)
        losses = sum(1 for s in similar_trades if s.pnl <= 0)
        win_rate = wins / len(similar_trades)

        pnls = [s.pnl for s in similar_trades]
        r_vals = [s.realized_r for s in similar_trades]
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else (
            float('inf') if gross_profit > 0 else 0
        )
        avg_r = sum(r_vals) / len(r_vals) if r_vals else 0
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0

        # Confidence level based on sample size
        if len(similar_trades) >= 30:
            confidence = "HIGH"
        elif len(similar_trades) >= 15:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Admission decision
        admit = True
        reason = ""
        if win_rate < TQI_REJECT_THRESHOLD and len(similar_trades) >= 15:
            admit = False
            reason = (
                f"TQI reject: similar_trades WR={win_rate:.1%} < {TQI_REJECT_THRESHOLD:.0%} "
                f"(n={len(similar_trades)}, PF={pf:.2f})"
            )
        elif win_rate >= TQI_STRONG_THRESHOLD:
            reason = (
                f"TQI strong: similar_trades WR={win_rate:.1%} "
                f"(n={len(similar_trades)}, PF={pf:.2f})"
            )
        else:
            reason = (
                f"TQI neutral: similar_trades WR={win_rate:.1%} "
                f"(n={len(similar_trades)}, PF={pf:.2f})"
            )

        result = TQIResult(
            symbol=symbol,
            side=side,
            tqi=win_rate,
            similar_count=len(similar_trades),
            similar_win_rate=win_rate,
            similar_pf=pf,
            similar_avg_r=avg_r,
            similar_avg_pnl=avg_pnl,
            confidence=confidence,
            admit=admit,
            reason=reason,
        )

        if not admit:
            self._total_rejects += 1
            logger.warning(
                "🚫 TQI REJECT: {} {} — WR={:.1%} PF={:.2f} ({} similar trades)",
                symbol, side, win_rate, pf, len(similar_trades),
            )
        elif win_rate >= TQI_STRONG_THRESHOLD:
            self._total_admits += 1
            logger.info(
                "✅ TQI STRONG: {} {} — WR={:.1%} PF={:.2f} ({} similar trades)",
                symbol, side, win_rate, pf, len(similar_trades),
            )

        return result

    def get_adaptive_threshold(self, metric_name: str) -> AdaptiveThreshold:
        """Get adaptive threshold for a metric based on rolling percentiles."""
        self._refresh_thresholds()
        return self._thresholds.get(
            metric_name,
            AdaptiveThreshold(name=metric_name),
        )

    # ─────────────────────────────────────────────────────────
    # PUBLIC API — POST-TRADE (Self-Learning)
    # ─────────────────────────────────────────────────────────

    def record_trade_outcome(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: float,
        pnl: float,
        realized_r: float,
        hold_minutes: float,
        exit_reason: str = "",
        session: str = "",
        regime: str = "",
        confidence: float = 0.0,
        mfe_pct: float = 0.0,
        mae_pct: float = 0.0,
        admission_score: float = 0.0,
        quality_score: float = 0.0,
        peak_confidence: float = 0.0,
        min_confidence: float = 0.0,
        reductions: int = 0,
        opened_at: float = 0.0,
        closed_at: float = 0.0,
        # Market context at entry
        entry_flow: float = 0.0,
        entry_cvd: float = 0.0,
        entry_oi_change: float = 0.0,
        entry_volume_ratio: float = 0.0,
        entry_funding_rate: float = 0.0,
        entry_institutional_score: float = 0.0,
        entry_atr_pct: float = 0.0,
        entry_trend_strength: float = 0.0,
        entry_hour_utc: int = 0,
    ) -> LearningUpdate:
        """
        Record a completed trade and update all statistics.

        Call this immediately after every trade closes.

        Returns:
            LearningUpdate with what was updated
        """
        # Create snapshot
        snapshot = TradeSnapshot(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            confidence=confidence,
            regime=regime,
            session=session,
            entry_flow=entry_flow,
            entry_cvd=entry_cvd,
            entry_oi_change=entry_oi_change,
            entry_volume_ratio=entry_volume_ratio,
            entry_funding_rate=entry_funding_rate,
            entry_institutional_score=entry_institutional_score,
            entry_atr_pct=entry_atr_pct,
            entry_trend_strength=entry_trend_strength,
            entry_hour_utc=entry_hour_utc,
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl=pnl,
            realized_r=realized_r,
            hold_minutes=hold_minutes,
            mfe_pct=mfe_pct,
            mae_pct=mae_pct,
            admission_score=admission_score,
            quality_score=quality_score,
            peak_confidence=peak_confidence,
            min_confidence=min_confidence,
            reductions=reductions,
            opened_at=opened_at,
            closed_at=closed_at or time.time(),
        )

        # Store in replay database
        self._snapshots.append(snapshot)
        if len(self._snapshots) > self._max_snapshots:
            self._snapshots = self._snapshots[-self._max_snapshots:]

        # Build update report
        update = LearningUpdate(
            trade_id=trade_id,
            symbol=symbol,
            session=session,
        )

        # Update symbol PF cache
        self._update_symbol_cache(symbol, pnl)
        update.updated_symbols.append(symbol)

        # Update session PF cache
        if session:
            self._update_session_cache(session, pnl)
            update.updated_sessions.append(session)

        # Invalidate threshold cache
        self._last_threshold_refresh = 0

        update.tqi_stored = True

        logger.info(
            "📚 LEARNING: {} {} closed — PnL={:.2f} R={:.2f} | "
            "TQI DB now has {} snapshots",
            symbol, side, pnl, realized_r, len(self._snapshots),
        )

        return update

    def get_status(self) -> Dict:
        """Get complete evidence engine status."""
        self._ensure_loaded()
        self._refresh_thresholds()
        return {
            "total_snapshots": len(self._snapshots),
            "total_queries": self._total_queries,
            "total_admits": self._total_admits,
            "total_rejects": self._total_rejects,
            "thresholds": {
                k: v.to_dict() for k, v in self._thresholds.items()
            },
            "symbol_pf_cache": {
                k: round(v, 2) for k, v in sorted(
                    self._symbol_pf_cache.items(), key=lambda x: x[1]
                )
            },
            "session_pf_cache": {
                k: round(v, 2) for k, v in sorted(
                    self._session_pf_cache.items(), key=lambda x: x[1]
                )
            },
        }

    # ─────────────────────────────────────────────────────────
    # INTERNAL — FEATURE VECTOR CONSTRUCTION
    # ─────────────────────────────────────────────────────────

    def _build_feature_vector(
        self,
        signal: Dict[str, Any],
        market_data: Optional[Dict] = None,
    ) -> Dict[str, str]:
        """Build discretized feature vector from signal + market data."""
        entry_price = signal.get("entry_price", signal.get("entry", 0))
        stop_loss = signal.get("stop_loss", signal.get("sl", 0))
        take_profit = signal.get("take_profit", signal.get("tp1", 0))

        rr = 0
        if entry_price and stop_loss and take_profit:
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit - entry_price)
            rr = reward / risk if risk > 0 else 0

        inst_score = signal.get("institutional_score", signal.get("inst_agreement", 0.5))
        atr_pct = signal.get("atr_pct", signal.get("atr", 0))
        if entry_price and atr_pct and atr_pct > 1:
            atr_pct = atr_pct / entry_price * 100

        trend = signal.get("trend_strength", signal.get("trend_score", 0))

        md = market_data or {}
        hour = int(time.gmtime().tm_hour)

        return {
            "symbol": signal.get("symbol", ""),
            "side": signal.get("side", ""),
            "session": signal.get("session", signal.get("at_open_session", "unknown")),
            "regime": signal.get("regime", signal.get("market_regime", "unknown")),
            "confidence_bucket": _bucket(
                signal.get("confidence", 0), CONFIDENCE_BUCKETS
            ),
            "rr_bucket": _bucket(rr, RR_BUCKETS),
            "volatility_bucket": _bucket(
                atr_pct if isinstance(atr_pct, (int, float)) else 0,
                VOLATILITY_BUCKETS,
            ),
            "hour_of_day": str(hour // 6),
            "institutional_bucket": _bucket(
                inst_score if isinstance(inst_score, (int, float)) else 0.5,
                INST_BUCKETS,
            ),
            "trend_strength_bucket": _bucket(
                trend if isinstance(trend, (int, float)) else 0,
                TREND_BUCKETS,
            ),
        }

    # ─────────────────────────────────────────────────────────
    # INTERNAL — DATA LOADING
    # ─────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Load historical trades from DB if not yet loaded."""
        if self._loaded:
            return
        self._loaded = True

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Load from positions_archive (closed trades with full data)
            cur.execute("""
                SELECT symbol, side, entry_price, stop_loss,
                       take_profit, quantity, pnl, realized_r, hold_minutes,
                       exit_reason, session, regime, confidence,
                       mfe_pct, mae_pct, opened_at, closed_at,
                       institutional_score
                FROM positions_archive
                WHERE status = 'closed' AND pnl IS NOT NULL
                ORDER BY closed_at DESC
                LIMIT ?
            """, (self._max_snapshots,))

            rows = cur.fetchall()
            conn.close()

            for row in rows:
                snap = TradeSnapshot(
                    symbol=row[0] or "",
                    side=row[1] or "",
                    entry_price=row[2] or 0,
                    stop_loss=row[3] or 0,
                    take_profit=row[4] or 0,
                    quantity=row[5] or 0,
                    pnl=row[6] or 0,
                    realized_r=row[7] or 0,
                    hold_minutes=row[8] or 0,
                    exit_reason=row[9] or "",
                    session=row[10] or "",
                    regime=row[11] or "",
                    confidence=row[12] or 0,
                    mfe_pct=row[13] or 0,
                    mae_pct=row[14] or 0,
                    opened_at=row[15] or 0,
                    closed_at=row[16] or 0,
                    entry_institutional_score=row[17] or 0,
                )
                self._snapshots.append(snap)

            # Build initial caches
            for snap in self._snapshots:
                self._update_symbol_cache(snap.symbol, snap.pnl)
                if snap.session:
                    self._update_session_cache(snap.session, snap.pnl)

            logger.info(
                "📚 EVIDENCE ENGINE loaded {} historical trade snapshots",
                len(self._snapshots),
            )

        except Exception as e:
            logger.warning("Evidence engine load error: {}", e)

    def _update_symbol_cache(self, symbol: str, pnl: float) -> None:
        """Update rolling symbol PF cache."""
        # Simple incremental update — track recent trades
        if symbol not in self._symbol_pf_cache:
            self._symbol_pf_cache[symbol] = 1.0

        # Get recent trades for this symbol
        recent = [s for s in self._snapshots[-200:] if s.symbol == symbol]
        if len(recent) >= 5:
            gp = sum(s.pnl for s in recent if s.pnl > 0)
            gl = abs(sum(s.pnl for s in recent if s.pnl < 0))
            self._symbol_pf_cache[symbol] = gp / gl if gl > 0 else (
                float('inf') if gp > 0 else 0
            )

    def _update_session_cache(self, session: str, pnl: float) -> None:
        """Update rolling session PF cache."""
        if session not in self._session_pf_cache:
            self._session_pf_cache[session] = 1.0

        recent = [s for s in self._snapshots[-200:] if s.session == session]
        if len(recent) >= 5:
            gp = sum(s.pnl for s in recent if s.pnl > 0)
            gl = abs(sum(s.pnl for s in recent if s.pnl < 0))
            self._session_pf_cache[session] = gp / gl if gl > 0 else (
                float('inf') if gp > 0 else 0
            )

    # ─────────────────────────────────────────────────────────
    # INTERNAL — ADAPTIVE THRESHOLDS
    # ─────────────────────────────────────────────────────────

    def _refresh_thresholds(self) -> None:
        """Refresh adaptive thresholds from rolling window."""
        now = time.time()
        if now - self._last_threshold_refresh < ADAPTIVE_REFRESH_SEC:
            return
        self._last_threshold_refresh = now

        window = self._snapshots[-ADAPTIVE_WINDOW:]
        if len(window) < 20:
            return

        # Confidence scores
        conf_vals = sorted([s.confidence for s in window if s.confidence > 0])
        if conf_vals:
            self._thresholds["confidence"] = AdaptiveThreshold(
                name="confidence",
                p25=_percentile(conf_vals, 25),
                p50=_percentile(conf_vals, 50),
                p75=_percentile(conf_vals, 75),
                p85=_percentile(conf_vals, 85),
                p90=_percentile(conf_vals, 90),
                sample_size=len(conf_vals),
            )

        # Realized R
        r_vals = sorted([s.realized_r for s in window])
        if r_vals:
            self._thresholds["realized_r"] = AdaptiveThreshold(
                name="realized_r",
                p25=_percentile(r_vals, 25),
                p50=_percentile(r_vals, 50),
                p75=_percentile(r_vals, 75),
                p85=_percentile(r_vals, 85),
                p90=_percentile(r_vals, 90),
                sample_size=len(r_vals),
            )

        # PnL
        pnl_vals = sorted([s.pnl for s in window])
        if pnl_vals:
            self._thresholds["pnl"] = AdaptiveThreshold(
                name="pnl",
                p25=_percentile(pnl_vals, 25),
                p50=_percentile(pnl_vals, 50),
                p75=_percentile(pnl_vals, 75),
                p85=_percentile(pnl_vals, 85),
                p90=_percentile(pnl_vals, 90),
                sample_size=len(pnl_vals),
            )

        # Hold minutes
        hold_vals = sorted([s.hold_minutes for s in window if s.hold_minutes > 0])
        if hold_vals:
            self._thresholds["hold_minutes"] = AdaptiveThreshold(
                name="hold_minutes",
                p25=_percentile(hold_vals, 25),
                p50=_percentile(hold_vals, 50),
                p75=_percentile(hold_vals, 75),
                p85=_percentile(hold_vals, 85),
                p90=_percentile(hold_vals, 90),
                sample_size=len(hold_vals),
            )

        # Admission score
        adm_vals = sorted([s.admission_score for s in window if s.admission_score > 0])
        if adm_vals:
            self._thresholds["admission_score"] = AdaptiveThreshold(
                name="admission_score",
                p25=_percentile(adm_vals, 25),
                p50=_percentile(adm_vals, 50),
                p75=_percentile(adm_vals, 75),
                p85=_percentile(adm_vals, 85),
                p90=_percentile(adm_vals, 90),
                sample_size=len(adm_vals),
            )

        # Quality score
        qual_vals = sorted([s.quality_score for s in window if s.quality_score > 0])
        if qual_vals:
            self._thresholds["quality_score"] = AdaptiveThreshold(
                name="quality_score",
                p25=_percentile(qual_vals, 25),
                p50=_percentile(qual_vals, 50),
                p75=_percentile(qual_vals, 75),
                p85=_percentile(qual_vals, 85),
                p90=_percentile(qual_vals, 90),
                sample_size=len(qual_vals),
            )

    # ─────────────────────────────────────────────────────────
    # EXPECTED VALUE ENGINE
    # ─────────────────────────────────────────────────────────

    def compute_ev(
        self,
        signal: Dict[str, Any],
        market_data: Optional[Dict] = None,
        balance: float = 10_000.0,
        position_value: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Compute Expected Value in dollars for a candidate trade.

        Uses similarity search to find historical analogues, then estimates:
            - Expected profit (probability × avg winner)
            - Expected loss (probability × avg loser)
            - Net expected value in dollars
            - Expected holding time
            - Expected drawdown (MAE)

        Args:
            signal: Candidate signal
            market_data: Optional live market data
            balance: Account balance
            position_value: Position value in dollars

        Returns:
            Dict with EV components
        """
        self._ensure_loaded()

        symbol = signal.get("symbol", "")
        side = signal.get("side", "")

        # Build feature vector and find similar trades
        candidate_features = self._build_feature_vector(signal, market_data)

        similarities = []
        for snap in self._snapshots:
            snap_features = snap.get_feature_vector()
            score = _similarity_score(candidate_features, snap_features)
            similarities.append((score, snap))

        similarities.sort(key=lambda x: x[0], reverse=True)
        top_k = [s for _, s in similarities[:SIMILARITY_K]]

        if len(top_k) < SIMILARITY_MIN_MATCHES:
            return {
                "symbol": symbol,
                "side": side,
                "ev_usd": 0.0,
                "ev_r": 0.0,
                "win_probability": 0.5,
                "expected_profit": 0.0,
                "expected_loss": 0.0,
                "expected_hold_minutes": 0.0,
                "expected_mae_pct": 0.0,
                "similar_count": len(top_k),
                "reliability": "LOW",
                "positive_ev": False,
                "ev_rank_score": 0.0,
            }

        # Calculate from similar trades
        wins = [s for s in top_k if s.pnl > 0]
        losses = [s for s in top_k if s.pnl <= 0]

        win_prob = len(wins) / len(top_k)
        loss_prob = 1 - win_prob

        avg_winner_pnl = sum(s.pnl for s in wins) / len(wins) if wins else 0
        avg_loser_pnl = abs(sum(s.pnl for s in losses) / len(losses)) if losses else 0
        avg_winner_r = sum(s.realized_r for s in wins) / len(wins) if wins else 0
        avg_loser_r = abs(sum(s.realized_r for s in losses) / len(losses)) if losses else 0

        # Expected values
        expected_profit = win_prob * avg_winner_pnl
        expected_loss = loss_prob * avg_loser_pnl
        ev_usd = expected_profit - expected_loss

        expected_profit_r = win_prob * avg_winner_r
        expected_loss_r = loss_prob * avg_loser_r
        ev_r = expected_profit_r - expected_loss_r

        # Expected hold time
        avg_hold = sum(s.hold_minutes for s in top_k if s.hold_minutes > 0)
        hold_count = sum(1 for s in top_k if s.hold_minutes > 0)
        expected_hold = avg_hold / hold_count if hold_count > 0 else 0

        # Expected drawdown (MAE)
        mae_vals = [s.mae_pct for s in top_k if s.mae_pct > 0]
        expected_mae = sum(mae_vals) / len(mae_vals) if mae_vals else 0

        # Reliability based on sample size
        if len(top_k) >= 30:
            reliability = "HIGH"
        elif len(top_k) >= 15:
            reliability = "MEDIUM"
        else:
            reliability = "LOW"

        # Historical PF of similar trades
        gross_profit = sum(s.pnl for s in wins)
        gross_loss = abs(sum(s.pnl for s in losses))
        similar_pf = gross_profit / gross_loss if gross_loss > 0 else (
            float('inf') if gross_profit > 0 else 0
        )

        # EV rank score: EV × PF × Reliability ÷ Expected Drawdown
        reliability_mult = {"HIGH": 1.0, "MEDIUM": 0.8, "LOW": 0.6}.get(reliability, 0.5)
        drawdown_divisor = max(0.01, expected_mae) if expected_mae > 0 else 1.0
        ev_rank_score = (
            ev_usd
            * min(3.0, max(0.1, similar_pf))
            * reliability_mult
            / drawdown_divisor
        )

        positive_ev = ev_usd > 0

        return {
            "symbol": symbol,
            "side": side,
            "ev_usd": round(ev_usd, 2),
            "ev_r": round(ev_r, 3),
            "win_probability": round(win_prob, 3),
            "expected_profit": round(expected_profit, 2),
            "expected_loss": round(expected_loss, 2),
            "expected_hold_minutes": round(expected_hold, 0),
            "expected_mae_pct": round(expected_mae, 2),
            "similar_count": len(top_k),
            "similar_pf": round(similar_pf, 2),
            "reliability": reliability,
            "positive_ev": positive_ev,
            "ev_rank_score": round(ev_rank_score, 2),
            "avg_winner_pnl": round(avg_winner_pnl, 2),
            "avg_loser_pnl": round(avg_loser_pnl, 2),
            "avg_winner_r": round(avg_winner_r, 3),
            "avg_loser_r": round(avg_loser_r, 3),
        }

    # ─────────────────────────────────────────────────────────
    # ACCEPTANCE / REJECTION VALIDATOR
    # ─────────────────────────────────────────────────────────

    def log_decision(
        self,
        signal: Dict[str, Any],
        decision: str,
        reason: str = "",
        ev_data: Optional[Dict] = None,
        tqi_data: Optional[Dict] = None,
        modules_checked: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Log an acceptance or rejection decision for later validation.

        This creates the evidence trail needed to answer:
            "Does the Evidence Engine actually reject the trades
             that become losers?"

        Args:
            signal: The signal that was evaluated
            decision: "ACCEPT" or "REJECT"
            reason: Why the decision was made
            ev_data: EV computation results
            tqi_data: TQI computation results
            modules_checked: Dict of module_name → "PASS" or "REJECT"
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        confidence = signal.get("confidence", 0)
        session = signal.get("session", signal.get("at_open_session", ""))
        regime = signal.get("regime", signal.get("market_regime", ""))

        # Store in a lightweight log
        entry = {
            "timestamp": time.time(),
            "symbol": symbol,
            "side": side,
            "confidence": confidence,
            "session": session,
            "regime": regime,
            "decision": decision,
            "reason": reason,
            "ev_usd": ev_data.get("ev_usd", 0) if ev_data else 0,
            "ev_r": ev_data.get("ev_r", 0) if ev_data else 0,
            "tqi": tqi_data.get("tqi", 0) if tqi_data else 0,
            "similar_count": ev_data.get("similar_count", 0) if ev_data else 0,
            "modules_checked": modules_checked or {},
            "outcome_pnl": None,  # Filled when trade closes
            "outcome_r": None,
            "exit_reason": None,
        }

        self._decision_log.append(entry)
        if len(self._decision_log) > 5000:
            self._decision_log = self._decision_log[-5000:]

    def get_validation_report(self) -> Dict:
        """
        Generate acceptance/rejection validation report.

        Answers: "Does the Evidence Engine actually reject losers?"

        For accepted trades that have since closed, compares:
            - PF of accepted vs rejected
            - Win rate of accepted vs rejected
            - Expectancy of accepted vs rejected
        """
        self._ensure_loaded()

        # Group decision log by symbol+side+timestamp to match with outcomes
        accepted_outcomes = []
        rejected_outcomes = []

        # Match accepted decisions with actual trade outcomes
        for entry in self._decision_log:
            sym = entry.get("symbol", "")
            side = entry.get("side", "")
            ts = entry.get("timestamp", 0)
            decision = entry.get("decision", "")

            # Find matching trade outcome (within 1 hour window)
            matched = None
            for snap in self._snapshots:
                if (snap.symbol == sym
                        and snap.side == side
                        and abs(snap.opened_at - ts) < 3600):
                    matched = snap
                    break

            if decision == "ACCEPT":
                accepted_outcomes.append({
                    "symbol": sym,
                    "side": side,
                    "ev_usd": entry.get("ev_usd", 0),
                    "tqi": entry.get("tqi", 0),
                    "pnl": matched.pnl if matched else None,
                    "realized_r": matched.realized_r if matched else None,
                    "matched": matched is not None,
                })
            else:
                rejected_outcomes.append({
                    "symbol": sym,
                    "side": side,
                    "ev_usd": entry.get("ev_usd", 0),
                    "tqi": entry.get("tqi", 0),
                    "reason": entry.get("reason", ""),
                })

        # Calculate stats for accepted trades that have outcomes
        accepted_with_outcome = [a for a in accepted_outcomes if a.get("pnl") is not None]
        accepted_wins = [a for a in accepted_with_outcome if a["pnl"] > 0]
        accepted_losses = [a for a in accepted_with_outcome if a["pnl"] <= 0]

        accepted_pf = 0.0
        if accepted_losses:
            gp = sum(a["pnl"] for a in accepted_wins)
            gl = abs(sum(a["pnl"] for a in accepted_losses))
            accepted_pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

        accepted_wr = len(accepted_wins) / len(accepted_with_outcome) if accepted_with_outcome else 0
        accepted_ev = sum(a["pnl"] for a in accepted_with_outcome) / len(accepted_with_outcome) if accepted_with_outcome else 0

        # Calculate stats for rejected decisions
        # We can't know what would have happened, but we can track
        # the EV and TQI at rejection time
        rejected_ev_values = [r.get("ev_usd", 0) for r in rejected_outcomes]
        rejected_tqi_values = [r.get("tqi", 0) for r in rejected_outcomes]

        return {
            "total_decisions": len(self._decision_log),
            "accepted": len(accepted_outcomes),
            "rejected": len(rejected_outcomes),
            "accepted_with_outcome": len(accepted_with_outcome),
            "accepted_pf": round(accepted_pf, 2),
            "accepted_win_rate": round(accepted_wr, 3),
            "accepted_expectancy": round(accepted_ev, 2),
            "accepted_wins": len(accepted_wins),
            "accepted_losses": len(accepted_losses),
            "rejected_avg_ev": round(
                sum(rejected_ev_values) / len(rejected_ev_values), 2
            ) if rejected_ev_values else 0,
            "rejected_avg_tqi": round(
                sum(rejected_tqi_values) / len(rejected_tqi_values), 3
            ) if rejected_tqi_values else 0,
            "rejection_reasons": self._summarize_rejection_reasons(rejected_outcomes),
        }

    def _summarize_rejection_reasons(self, rejected: List[Dict]) -> Dict[str, int]:
        """Summarize rejection reasons by category."""
        reasons = defaultdict(int)
        for r in rejected:
            reason = r.get("reason", "unknown")
            # Categorize by first keyword
            if "tqi" in reason.lower():
                reasons["tqi_reject"] += 1
            elif "governance" in reason.lower() or "kill" in reason.lower():
                reasons["governance"] += 1
            elif "symbol" in reason.lower() or "blacklist" in reason.lower():
                reasons["symbol_blacklist"] += 1
            elif "session" in reason.lower():
                reasons["session_blacklist"] += 1
            elif "confidence" in reason.lower():
                reasons["confidence_calibration"] += 1
            elif "admission" in reason.lower():
                reasons["admission"] += 1
            else:
                reasons["other"] += 1
        return dict(reasons)

    def link_outcome(
        self,
        symbol: str,
        side: str,
        opened_at: float,
        pnl: float,
        realized_r: float,
        exit_reason: str = "",
    ) -> bool:
        """
        Link a closed trade outcome back to its decision log entry.

        Call this when a trade closes to complete the attribution chain.

        Returns True if a matching decision was found.
        """
        for entry in reversed(self._decision_log):
            if (entry.get("symbol") == symbol
                    and entry.get("side") == side
                    and entry.get("decision") == "ACCEPT"
                    and entry.get("outcome_pnl") is None
                    and abs(entry.get("timestamp", 0) - opened_at) < 3600):
                entry["outcome_pnl"] = pnl
                entry["outcome_r"] = realized_r
                entry["exit_reason"] = exit_reason
                return True
        return False

    def get_attribution_report(self) -> Dict:
        """
        Generate per-module attribution report.

        For each governance module, shows:
            - How many trades it passed
            - How many it rejected
            - PF of trades it passed
            - PF of trades it rejected (where outcome is known)

        This answers: "Which module actually improves Profit Factor?"
        """
        # Module-level tracking
        module_stats: Dict[str, Dict] = {}

        for entry in self._decision_log:
            modules = entry.get("modules_checked", {})
            outcome_pnl = entry.get("outcome_pnl")
            decision = entry.get("decision", "")

            for module, result in modules.items():
                if module not in module_stats:
                    module_stats[module] = {
                        "passed": {"count": 0, "wins": 0, "losses": 0,
                                   "total_pnl": 0, "pnls": []},
                        "rejected": {"count": 0},
                    }

                if result == "PASS":
                    module_stats[module]["passed"]["count"] += 1
                    if outcome_pnl is not None:
                        module_stats[module]["passed"]["pnls"].append(outcome_pnl)
                        module_stats[module]["passed"]["total_pnl"] += outcome_pnl
                        if outcome_pnl > 0:
                            module_stats[module]["passed"]["wins"] += 1
                        else:
                            module_stats[module]["passed"]["losses"] += 1
                else:
                    module_stats[module]["rejected"]["count"] += 1

        # Calculate PF per module
        report = {}
        for module, stats in module_stats.items():
            passed = stats["passed"]
            rejected = stats["rejected"]

            gp = sum(p for p in passed["pnls"] if p > 0)
            gl = abs(sum(p for p in passed["pnls"] if p < 0))
            pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

            total = passed["wins"] + passed["losses"]
            wr = passed["wins"] / total if total > 0 else 0
            avg_pnl = passed["total_pnl"] / total if total > 0 else 0

            report[module] = {
                "passed_count": passed["count"],
                "passed_with_outcome": total,
                "passed_pf": round(pf, 2),
                "passed_win_rate": round(wr, 3),
                "passed_avg_pnl": round(avg_pnl, 2),
                "passed_total_pnl": round(passed["total_pnl"], 2),
                "rejected_count": rejected["count"],
                "keep": pf >= 1.0 if total >= 10 else None,  # Need 10+ trades
            }

        # Sort by PF impact
        return dict(sorted(report.items(), key=lambda x: x[1].get("passed_pf", 0), reverse=True))

    def get_acceptance_efficiency(self) -> Dict:
        """
        Calculate Acceptance Efficiency.

        Acceptance Efficiency = PF of accepted trades / PF of all eligible trades

        If the App is adding value, this ratio should be > 1.0.
        """
        accepted_pnls = []
        all_eligible_pnls = []

        for entry in self._decision_log:
            outcome_pnl = entry.get("outcome_pnl")
            if outcome_pnl is None:
                continue

            all_eligible_pnls.append(outcome_pnl)
            if entry.get("decision") == "ACCEPT":
                accepted_pnls.append(outcome_pnl)

        def _calc_pf(pnls):
            if not pnls:
                return 0.0
            gp = sum(p for p in pnls if p > 0)
            gl = abs(sum(p for p in pnls if p < 0))
            return gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

        accepted_pf = _calc_pf(accepted_pnls)
        all_pf = _calc_pf(all_eligible_pnls)
        efficiency = accepted_pf / all_pf if all_pf > 0 else 0

        return {
            "acceptance_efficiency": round(efficiency, 3),
            "accepted_pf": round(accepted_pf, 2),
            "all_eligible_pf": round(all_pf, 2),
            "accepted_count": len(accepted_pnls),
            "all_eligible_count": len(all_eligible_pnls),
            "interpretation": (
                "App adds value (efficiency > 1.0)" if efficiency > 1.0
                else "App not yet adding value (efficiency ≤ 1.0)"
            ),
        }

    # ─────────────────────────────────────────────────────────
    # SHADOW TRADES — False Rejection Rate
    # ─────────────────────────────────────────────────────────

    def record_shadow_trade(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        opened_at: float,
        pnl: float,
        realized_r: float,
        rejection_reason: str = "",
    ) -> None:
        """
        Record what happened to a rejected trade (shadow/paper trade).

        This enables computing False Rejection Rate:
            - Rejected losers = good rejections
            - Rejected winners = bad rejections (false rejections)

        Args:
            symbol: Rejected trade symbol
            side: Rejected trade side
            entry_price: Price at rejection time
            opened_at: Time when the trade would have been opened
            pnl: What the PnL would have been
            realized_r: What the R would have been
            rejection_reason: Why it was rejected
        """
        shadow = {
            "timestamp": time.time(),
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "opened_at": opened_at,
            "pnl": pnl,
            "realized_r": realized_r,
            "rejection_reason": rejection_reason,
        }
        self._shadow_trades.append(shadow)
        if len(self._shadow_trades) > 2000:
            self._shadow_trades = self._shadow_trades[-2000:]

    def get_false_rejection_report(self) -> Dict:
        """
        Compute False Rejection Rate from shadow trades.

        Good rejections = rejected trades that would have lost
        Bad rejections = rejected trades that would have won (false rejections)

        Decision Precision = good_rejections / total_rejections
        False Rejection Rate = bad_rejections / total_rejections
        """
        if not self._shadow_trades:
            return {
                "total_shadow_trades": 0,
                "good_rejections": 0,
                "bad_rejections": 0,
                "decision_precision": 0.0,
                "false_rejection_rate": 0.0,
                "shadow_pf": 0.0,
                "shadow_expectancy": 0.0,
            }

        good = [s for s in self._shadow_trades if s["pnl"] <= 0]
        bad = [s for s in self._shadow_trades if s["pnl"] > 0]

        total = len(self._shadow_trades)
        precision = len(good) / total if total > 0 else 0
        false_rate = len(bad) / total if total > 0 else 0

        # What would the shadow portfolio have done?
        shadow_pnls = [s["pnl"] for s in self._shadow_trades]
        gp = sum(p for p in shadow_pnls if p > 0)
        gl = abs(sum(p for p in shadow_pnls if p < 0))
        shadow_pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
        shadow_ev = sum(shadow_pnls) / len(shadow_pnls) if shadow_pnls else 0

        # By rejection reason
        by_reason: Dict[str, Dict] = defaultdict(lambda: {"good": 0, "bad": 0})
        for s in self._shadow_trades:
            cat = self._categorize_reason(s.get("rejection_reason", ""))
            if s["pnl"] <= 0:
                by_reason[cat]["good"] += 1
            else:
                by_reason[cat]["bad"] += 1

        reason_breakdown = {}
        for cat, counts in sorted(by_reason.items()):
            t = counts["good"] + counts["bad"]
            reason_breakdown[cat] = {
                "total": t,
                "good_rejections": counts["good"],
                "bad_rejections": counts["bad"],
                "precision": round(counts["good"] / t, 3) if t > 0 else 0,
            }

        return {
            "total_shadow_trades": total,
            "good_rejections": len(good),
            "bad_rejections": len(bad),
            "decision_precision": round(precision, 3),
            "false_rejection_rate": round(false_rate, 3),
            "shadow_pf": round(shadow_pf, 2),
            "shadow_expectancy": round(shadow_ev, 2),
            "interpretation": (
                "App is too conservative — rejecting profitable trades"
                if false_rate > 0.40
                else "App rejection quality is acceptable"
                if precision >= 0.60
                else "App needs more data for reliable assessment"
            ),
            "by_rejection_reason": reason_breakdown,
        }

    def _categorize_reason(self, reason: str) -> str:
        """Categorize a rejection reason string."""
        r = reason.lower()
        if "tqi" in r:
            return "tqi"
        elif "ev" in r or "expected" in r:
            return "ev_engine"
        elif "governance" in r or "kill" in r:
            return "governance"
        elif "symbol" in r or "blacklist" in r:
            return "symbol_blacklist"
        elif "session" in r:
            return "session_blacklist"
        elif "confidence" in r:
            return "confidence_calibration"
        elif "daily" in r or "loss" in r:
            return "daily_loss"
        elif "exposure" in r:
            return "max_exposure"
        elif "admission" in r:
            return "admission"
        elif "quality" in r:
            return "trade_quality"
        elif "eligibility" in r:
            return "eligibility"
        elif "priority" in r:
            return "priority"
        else:
            return "other"

    # ─────────────────────────────────────────────────────────
    # VERSION TRACKING — Compare App versions over time
    # ─────────────────────────────────────────────────────────

    def log_version_snapshot(
        self,
        version: str,
        description: str = "",
    ) -> None:
        """
        Record a version snapshot for longitudinal comparison.

        Call this when deploying a new App version to track:
            - Which version produced which PF
            - Whether each version improved performance

        Args:
            version: Version identifier (e.g., "v5.0-governance")
            description: What changed in this version
        """
        # Capture current stats
        accepted_pnls = []
        for entry in self._decision_log:
            if entry.get("decision") == "ACCEPT" and entry.get("outcome_pnl") is not None:
                accepted_pnls.append(entry["outcome_pnl"])

        gp = sum(p for p in accepted_pnls if p > 0)
        gl = abs(sum(p for p in accepted_pnls if p < 0))
        pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
        wr = sum(1 for p in accepted_pnls if p > 0) / len(accepted_pnls) if accepted_pnls else 0
        ev = sum(accepted_pnls) / len(accepted_pnls) if accepted_pnls else 0

        snapshot = {
            "timestamp": time.time(),
            "version": version,
            "description": description,
            "total_snapshots": len(self._snapshots),
            "total_decisions": len(self._decision_log),
            "accepted_trades": len(accepted_pnls),
            "accepted_pf": round(pf, 2),
            "accepted_win_rate": round(wr, 3),
            "accepted_expectancy": round(ev, 2),
        }

        self._version_history.append(snapshot)
        logger.info(
            "📸 VERSION SNAPSHOT: {} — PF={:.2f} WR={:.1%} EV=${:.2f} ({} trades)",
            version, pf, wr, ev, len(accepted_pnls),
        )

    def get_version_history(self) -> List[Dict]:
        """Get all version snapshots for longitudinal comparison."""
        return list(self._version_history)

    # ─────────────────────────────────────────────────────────
    # CONTRIBUTION DASHBOARD — Multi-objective module evaluation
    # ─────────────────────────────────────────────────────────

    def get_contribution_dashboard(self) -> Dict:
        """
        Compute per-module contribution across multiple objectives.

        For each module, measures:
            - PF improvement (accepted vs what all eligible would have been)
            - Expectancy improvement
            - Drawdown reduction
            - Win rate improvement
            - Recovery factor contribution
            - Stability across symbols/sessions

        Returns a table showing each module's net effect.
        """
        self._ensure_loaded()

        # Collect all decisions with outcomes
        accepted_with_outcome = []
        rejected_with_outcome = []  # Shadow trades

        for entry in self._decision_log:
            if entry.get("outcome_pnl") is not None:
                if entry.get("decision") == "ACCEPT":
                    accepted_with_outcome.append(entry)

        for shadow in self._shadow_trades:
            rejected_with_outcome.append(shadow)

        if not accepted_with_outcome and not rejected_with_outcome:
            return {"modules": {}, "summary": "Insufficient data for contribution analysis"}

        # All eligible = accepted + rejected (what would have happened without filtering)
        all_eligible_pnls = (
            [e["outcome_pnl"] for e in accepted_with_outcome]
            + [s["pnl"] for s in rejected_with_outcome]
        )

        def _pf(pnls):
            if not pnls:
                return 0.0
            gp = sum(p for p in pnls if p > 0)
            gl = abs(sum(p for p in pnls if p < 0))
            return gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

        def _wr(pnls):
            if not pnls:
                return 0.0
            return sum(1 for p in pnls if p > 0) / len(pnls)

        def _ev(pnls):
            if not pnls:
                return 0.0
            return sum(pnls) / len(pnls)

        def _max_dd(pnls):
            if not pnls:
                return 0.0
            cumulative = 0
            peak = 0
            max_dd = 0
            for p in pnls:
                cumulative += p
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_dd:
                    max_dd = dd
            return max_dd

        def _recovery_factor(pnls):
            if not pnls:
                return 0.0
            total = sum(pnls)
            dd = _max_dd(pnls)
            return total / dd if dd > 0 else (float('inf') if total > 0 else 0)

        # Baseline: what all eligible trades would have produced
        baseline_pf = _pf(all_eligible_pnls)
        baseline_wr = _wr(all_eligible_pnls)
        baseline_ev = _ev(all_eligible_pnls)
        baseline_dd = _max_dd(all_eligible_pnls)
        baseline_rf = _recovery_factor(all_eligible_pnls)

        # Per-module contribution
        modules = {}

        for entry in accepted_with_outcome:
            mod_status = entry.get("modules_checked", {})
            for mod_name, status in mod_status.items():
                if mod_name not in modules:
                    modules[mod_name] = {
                        "accepted_pnls": [],
                        "rejected_shadow_pnls": [],
                        "passed_count": 0,
                        "rejected_count": 0,
                    }
                if status == "PASS":
                    modules[mod_name]["accepted_pnls"].append(entry["outcome_pnl"])
                    modules[mod_name]["passed_count"] += 1

        for shadow in rejected_with_outcome:
            reason = shadow.get("rejection_reason", "")
            mod_name = self._categorize_reason(reason)
            if mod_name not in modules:
                modules[mod_name] = {
                    "accepted_pnls": [],
                    "rejected_shadow_pnls": [],
                    "passed_count": 0,
                    "rejected_count": 0,
                }
            modules[mod_name]["rejected_shadow_pnls"].append(shadow["pnl"])
            modules[mod_name]["rejected_count"] += 1

        # Compute contribution for each module
        contribution = {}
        for mod_name, data in modules.items():
            passed = data["accepted_pnls"]
            rejected_shadow = data["rejected_shadow_pnls"]

            # What the module kept
            kept_pf = _pf(passed)
            kept_wr = _wr(passed)
            kept_ev = _ev(passed)
            kept_dd = _max_dd(passed)

            # What the module rejected (shadow)
            rejected_pf = _pf(rejected_shadow) if rejected_shadow else 0
            rejected_ev = _ev(rejected_shadow) if rejected_shadow else 0

            # PF improvement = kept PF vs baseline
            pf_delta = kept_pf - baseline_pf if baseline_pf > 0 else 0

            # Drawdown reduction = how much DD decreased
            dd_delta = baseline_dd - _max_dd(passed + rejected_shadow)

            # Net effect judgment
            total_trades = len(passed) + len(rejected_shadow)
            if total_trades < 10:
                net_effect = "INSUFFICIENT_DATA"
            elif kept_pf >= 1.0 and pf_delta >= 0:
                net_effect = "KEEP"
            elif kept_pf < 1.0 and pf_delta < 0:
                net_effect = "REVIEW"
            elif kept_pf >= 0.95 and dd_delta > 0:
                net_effect = "KEEP"  # Reduces drawdown even if PF slightly lower
            else:
                net_effect = "OPTIONAL"

            contribution[mod_name] = {
                "passed_count": data["passed_count"],
                "rejected_count": data["rejected_count"],
                "kept_pf": round(kept_pf, 2),
                "kept_win_rate": round(kept_wr, 3),
                "kept_expectancy": round(kept_ev, 2),
                "kept_drawdown": round(kept_dd, 2),
                "rejected_shadow_pf": round(rejected_pf, 2),
                "rejected_shadow_ev": round(rejected_ev, 2),
                "pf_improvement": round(pf_delta, 2),
                "drawdown_reduction": round(dd_delta, 2),
                "net_effect": net_effect,
            }

        # Sort by net effect (KEEP first, then by PF improvement)
        effect_order = {"KEEP": 0, "OPTIONAL": 1, "INSUFFICIENT_DATA": 2, "REVIEW": 3}
        contribution = dict(sorted(
            contribution.items(),
            key=lambda x: (effect_order.get(x[1]["net_effect"], 9), -x[1]["pf_improvement"]),
        ))

        return {
            "baseline": {
                "pf": round(baseline_pf, 2),
                "win_rate": round(baseline_wr, 3),
                "expectancy": round(baseline_ev, 2),
                "max_drawdown": round(baseline_dd, 2),
                "recovery_factor": round(baseline_rf, 2),
                "total_trades": len(all_eligible_pnls),
            },
            "accepted": {
                "pf": round(_pf([e["outcome_pnl"] for e in accepted_with_outcome]), 2),
                "win_rate": round(_wr([e["outcome_pnl"] for e in accepted_with_outcome]), 3),
                "expectancy": round(_ev([e["outcome_pnl"] for e in accepted_with_outcome]), 2),
                "count": len(accepted_with_outcome),
            },
            "modules": contribution,
            "confidence_intervals": self._compute_confidence_intervals(contribution),
            "stability": self._compute_stability_breakdown(),
        }

    def _compute_confidence_intervals(
        self, contribution: Dict[str, Dict],
    ) -> Dict[str, Dict]:
        """
        Compute confidence intervals for each module's PF improvement.

        Uses bootstrap-style estimation: larger samples → tighter intervals.
        Confidence levels:
            HIGH: 300+ trades (CI likely contains true value)
            MEDIUM: 100-299 trades
            LOW: 50-99 trades
            VERY LOW: < 50 trades (may be random variation)
        """
        intervals = {}
        for mod_name, stats in contribution.items():
            n = stats["passed_count"] + stats["rejected_count"]
            pf = stats["kept_pf"]
            pf_imp = stats["pf_improvement"]

            # Estimate standard error of PF
            # For PF, SE ≈ PF × sqrt(1/wins + 1/losses) (approximate)
            # Use sample size as proxy
            if n >= 300:
                confidence = "HIGH"
                ci_width = 0.05
            elif n >= 100:
                confidence = "MEDIUM"
                ci_width = 0.10
            elif n >= 50:
                confidence = "LOW"
                ci_width = 0.20
            else:
                confidence = "VERY_LOW"
                ci_width = 0.40

            intervals[mod_name] = {
                "sample_size": n,
                "confidence": confidence,
                "pf_improvement": round(pf_imp, 2),
                "ci_lower": round(max(0, pf_imp - ci_width), 2),
                "ci_upper": round(pf_imp + ci_width, 2),
                "ci_width": round(ci_width, 2),
                "reliable": n >= 100,
            }

        return intervals

    def _compute_stability_breakdown(self) -> Dict[str, Dict]:
        """
        Break down accepted trade performance by:
            - Symbol
            - Session
            - Regime
            - Time of day (UTC hour bucket)

        This reveals which environments the App handles well
        and which are consistently weak.
        """
        self._ensure_loaded()

        # Collect accepted trades with full context
        accepted = []
        for entry in self._decision_log:
            if (entry.get("decision") == "ACCEPT"
                    and entry.get("outcome_pnl") is not None):
                accepted.append(entry)

        if not accepted:
            return {"status": "insufficient_data"}

        def _breakdown(key: str) -> Dict:
            groups = defaultdict(list)
            for e in accepted:
                val = e.get(key, "unknown")
                if not val:
                    val = "unknown"
                groups[val].append(e["outcome_pnl"])

            result = {}
            for group, pnls in sorted(groups.items()):
                if len(pnls) < 3:
                    continue
                gp = sum(p for p in pnls if p > 0)
                gl = abs(sum(p for p in pnls if p < 0))
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
                wr = sum(1 for p in pnls if p > 0) / len(pnls)
                ev = sum(pnls) / len(pnls)

                result[group] = {
                    "trades": len(pnls),
                    "pf": round(pf, 2),
                    "win_rate": round(wr, 3),
                    "expectancy": round(ev, 2),
                    "status": (
                        "STRONG" if pf >= 1.2
                        else "ACCEPTABLE" if pf >= 1.0
                        else "WEAK" if pf >= 0.8
                        else "AVOID"
                    ),
                }
            return result

        # Build session breakdown from entry data
        session_groups = defaultdict(list)
        regime_groups = defaultdict(list)
        for entry in accepted:
            session = entry.get("session", "unknown")
            if not session:
                session = "unknown"
            session_groups[session].append(entry["outcome_pnl"])

            regime = entry.get("regime", "unknown")
            if not regime:
                regime = "unknown"
            regime_groups[regime].append(entry["outcome_pnl"])

        def _group_stats(groups):
            result = {}
            for group, pnls in sorted(groups.items()):
                if len(pnls) < 3:
                    continue
                gp = sum(p for p in pnls if p > 0)
                gl = abs(sum(p for p in pnls if p < 0))
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
                wr = sum(1 for p in pnls if p > 0) / len(pnls)
                ev = sum(pnls) / len(pnls)
                result[group] = {
                    "trades": len(pnls),
                    "pf": round(pf, 2),
                    "win_rate": round(wr, 3),
                    "expectancy": round(ev, 2),
                    "status": (
                        "STRONG" if pf >= 1.2
                        else "ACCEPTABLE" if pf >= 1.0
                        else "WEAK" if pf >= 0.8
                        else "AVOID"
                    ),
                }
            return result

        return {
            "by_symbol": _breakdown("symbol"),
            "by_session": _group_stats(session_groups),
            "by_regime": _group_stats(regime_groups),
        }

    # ─────────────────────────────────────────────────────────
    # DECISION CONFUSION MATRIX — FAR / FRR Analysis
    # ─────────────────────────────────────────────────────────

    def get_decision_confusion_matrix(self) -> Dict:
        """
        Classify every decision into one of four buckets:

            Accepted + Winner  = Correct Acceptance
            Accepted + Loser   = False Acceptance (FAR)
            Rejected + Loser   = Correct Rejection
            Rejected + Winner  = False Rejection (FRR)

        This tells you WHERE the decision engine is failing:
            - High FAR → App is accepting trades it should reject
            - High FRR → App is rejecting trades it should accept

        Returns:
            Dict with confusion matrix, FAR, FRR, and per-module breakdown
        """
        correct_accept = []
        false_accept = []
        correct_reject = []
        false_reject = []

        # Classify accepted trades
        for entry in self._decision_log:
            if entry.get("decision") != "ACCEPT":
                continue
            pnl = entry.get("outcome_pnl")
            if pnl is None:
                continue
            if pnl > 0:
                correct_accept.append(entry)
            else:
                false_accept.append(entry)

        # Classify rejected trades (shadow trades)
        for shadow in self._shadow_trades:
            if shadow["pnl"] > 0:
                false_reject.append(shadow)
            else:
                correct_reject.append(shadow)

        total = len(correct_accept) + len(false_accept) + len(correct_reject) + len(false_reject)
        if total == 0:
            return {"status": "insufficient_data"}

        # Rates
        total_accepted = len(correct_accept) + len(false_accept)
        total_rejected = len(correct_reject) + len(false_reject)

        far = len(false_accept) / total_accepted if total_accepted > 0 else 0
        frr = len(false_reject) / total_rejected if total_rejected > 0 else 0

        # PF of each bucket
        def _bucket_pf(bucket):
            if not bucket:
                return 0.0
            pnls = [e.get("outcome_pnl", e.get("pnl", 0)) for e in bucket]
            gp = sum(p for p in pnls if p > 0)
            gl = abs(sum(p for p in pnls if p < 0))
            return gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

        def _bucket_ev(bucket):
            if not bucket:
                return 0.0
            pnls = [e.get("outcome_pnl", e.get("pnl", 0)) for e in bucket]
            return sum(pnls) / len(pnls) if pnls else 0

        # Per-module false acceptance breakdown
        module_far: Dict[str, Dict] = {}
        for entry in false_accept:
            modules = entry.get("modules_checked", {})
            for mod, status in modules.items():
                if status == "PASS":
                    if mod not in module_far:
                        module_far[mod] = {"false_accepts": 0, "total_passed": 0, "pnls": []}
                    module_far[mod]["false_accepts"] += 1
                    module_far[mod]["pnls"].append(entry["outcome_pnl"])

        for entry in correct_accept:
            modules = entry.get("modules_checked", {})
            for mod, status in modules.items():
                if status == "PASS":
                    if mod not in module_far:
                        module_far[mod] = {"false_accepts": 0, "total_passed": 0, "pnls": []}
                    module_far[mod]["total_passed"] += 1
                    module_far[mod]["pnls"].append(entry["outcome_pnl"])

        module_far_report = {}
        for mod, data in module_far.items():
            total_mod = data["false_accepts"] + data["total_passed"]
            mod_far = data["false_accepts"] / total_mod if total_mod > 0 else 0
            pnls = data["pnls"]
            gp = sum(p for p in pnls if p > 0)
            gl = abs(sum(p for p in pnls if p < 0))
            pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
            module_far_report[mod] = {
                "false_acceptances": data["false_accepts"],
                "total_passed": total_mod,
                "far": round(mod_far, 3),
                "passed_pf": round(pf, 2),
            }

        # Per-reason false rejection breakdown
        frr_by_reason: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "pnls": []})
        for entry in false_reject:
            reason = self._categorize_reason(entry.get("rejection_reason", ""))
            frr_by_reason[reason]["count"] += 1
            frr_by_reason[reason]["pnls"].append(entry["pnl"])

        frr_report = {}
        for reason, data in frr_by_reason.items():
            total_reason = len(self._shadow_trades)
            frr_report[reason] = {
                "false_rejections": data["count"],
                "lost_pnl": round(sum(data["pnls"]), 2),
                "pct_of_all_rejects": round(data["count"] / total_reason, 3) if total_reason > 0 else 0,
            }

        return {
            "matrix": {
                "correct_acceptance": len(correct_accept),
                "false_acceptance": len(false_accept),
                "correct_rejection": len(correct_reject),
                "false_rejection": len(false_reject),
                "total": total,
            },
            "rates": {
                "false_acceptance_rate": round(far, 3),
                "false_rejection_rate": round(frr, 3),
                "decision_precision": round(1 - far, 3),
                "rejection_precision": round(1 - frr, 3),
            },
            "impact": {
                "correct_accept_pf": round(_bucket_pf(correct_accept), 2),
                "false_accept_pf": round(_bucket_pf(false_accept), 2),
                "correct_reject_pf": round(_bucket_pf(correct_reject), 2),
                "false_reject_pf": round(_bucket_pf(false_reject), 2),
                "false_accept_total_loss": round(
                    sum(e["outcome_pnl"] for e in false_accept), 2
                ),
                "false_reject_missed_profit": round(
                    sum(e["pnl"] for e in false_reject), 2
                ),
            },
            "module_far": module_far_report,
            "frr_by_reason": frr_report,
            "diagnosis": self._diagnose_decision_quality(far, frr, total_accepted, total_rejected),
        }

    def _diagnose_decision_quality(
        self, far: float, frr: float, total_accepted: int, total_rejected: int,
    ) -> Dict:
        """
        Diagnose decision engine quality and recommend action.
        """
        issues = []
        actions = []

        if far > 0.50:
            issues.append(f"CRITICAL: FAR={far:.0%} — majority of accepted trades lose")
            actions.append("Tighten admission threshold (reduce top-20% to top-15%)")
        elif far > 0.35:
            issues.append(f"HIGH: FAR={far:.0%} — too many false acceptances")
            actions.append("Review EV engine thresholds — raise minimum EV")

        if frr > 0.40:
            issues.append(f"HIGH: FRR={frr:.0%} — rejecting too many winners")
            actions.append("Relax TQI/EV thresholds — App may be too conservative")
        elif frr > 0.25:
            issues.append(f"MODERATE: FRR={frr:.0%} — some winners being rejected")

        if far < 0.25 and frr < 0.25:
            issues.append("GOOD: Both FAR and FRR are acceptable")
            actions.append("Continue forward testing — decision quality is strong")

        if total_accepted < 50:
            issues.append(f"LOW SAMPLE: Only {total_accepted} accepted trades — need 300+")
            actions.append("Continue collecting data before making changes")

        # Priority
        if far > frr:
            priority = "REDUCE_FALSE_ACCEPTANCES"
            priority_detail = "Focus on rejecting more losing trades, even if some winners are also rejected"
        elif frr > far:
            priority = "REDUCE_FALSE_REJECTIONS"
            priority_detail = "Focus on accepting more winning trades, even if some losers also get through"
        else:
            priority = "BALANCED"
            priority_detail = "FAR and FRR are balanced — continue monitoring"

        return {
            "issues": issues,
            "actions": actions,
            "priority": priority,
            "priority_detail": priority_detail,
        }

    def get_dollar_weighted_confusion_matrix(self) -> Dict:
        """
        Extend confusion matrix with dollar cost per error type.

        Instead of just counting errors, shows:
            - How much false acceptances cost
            - How much false rejections missed
            - Which errors are most expensive
            - Priority ranking by dollar impact

        This answers: "Which mistake costs the most money?"
        """
        correct_accept = []
        false_accept = []
        correct_reject = []
        false_reject = []

        for entry in self._decision_log:
            if entry.get("decision") != "ACCEPT":
                continue
            pnl = entry.get("outcome_pnl")
            if pnl is None:
                continue
            if pnl > 0:
                correct_accept.append(entry)
            else:
                false_accept.append(entry)

        for shadow in self._shadow_trades:
            if shadow["pnl"] > 0:
                false_reject.append(shadow)
            else:
                correct_reject.append(shadow)

        # Dollar impact
        ca_pnl = sum(e["outcome_pnl"] for e in correct_accept)
        fa_pnl = sum(e["outcome_pnl"] for e in false_accept)
        cr_saved = abs(sum(e["pnl"] for e in correct_reject))  # Money saved by rejecting losers
        fr_missed = sum(e["pnl"] for e in false_reject)  # Profit missed by rejecting winners

        # Per-module dollar cost
        module_cost: Dict[str, Dict] = {}
        for entry in false_accept:
            modules = entry.get("modules_checked", {})
            for mod, status in modules.items():
                if status == "PASS":
                    if mod not in module_cost:
                        module_cost[mod] = {"fa_cost": 0, "fa_count": 0, "ca_profit": 0, "ca_count": 0}
                    module_cost[mod]["fa_cost"] += entry["outcome_pnl"]
                    module_cost[mod]["fa_count"] += 1

        for entry in correct_accept:
            modules = entry.get("modules_checked", {})
            for mod, status in modules.items():
                if status == "PASS":
                    if mod not in module_cost:
                        module_cost[mod] = {"fa_cost": 0, "fa_count": 0, "ca_profit": 0, "ca_count": 0}
                    module_cost[mod]["ca_profit"] += entry["outcome_pnl"]
                    module_cost[mod]["ca_count"] += 1

        # Sort by dollar leakage (most expensive first)
        error_ranking = [
            {
                "type": "False Acceptance",
                "count": len(false_accept),
                "dollar_cost": round(fa_pnl, 2),
                "avg_cost_per_error": round(fa_pnl / len(false_accept), 2) if false_accept else 0,
            },
            {
                "type": "False Rejection",
                "count": len(false_reject),
                "dollar_cost": round(fr_missed, 2),
                "avg_cost_per_error": round(fr_missed / len(false_reject), 2) if false_reject else 0,
            },
        ]
        error_ranking.sort(key=lambda x: abs(x["dollar_cost"]), reverse=True)

        # Module dollar efficiency
        module_efficiency = {}
        for mod, data in module_cost.items():
            total_pnl = data["ca_profit"] + data["fa_cost"]
            total_count = data["ca_count"] + data["fa_count"]
            module_efficiency[mod] = {
                "correct_profit": round(data["ca_profit"], 2),
                "false_accept_cost": round(data["fa_cost"], 2),
                "net_dollar_impact": round(total_pnl, 2),
                "dollar_per_trade": round(total_pnl / total_count, 2) if total_count > 0 else 0,
                "false_accept_rate": round(data["fa_count"] / total_count, 3) if total_count > 0 else 0,
            }

        # Sort by net dollar impact
        module_efficiency = dict(sorted(
            module_efficiency.items(),
            key=lambda x: x[1]["net_dollar_impact"],
            reverse=True,
        ))

        return {
            "summary": {
                "correct_acceptance_profit": round(ca_pnl, 2),
                "false_acceptance_cost": round(fa_pnl, 2),
                "correct_rejection_saved": round(cr_saved, 2),
                "false_rejection_missed": round(fr_missed, 2),
                "net_decision_value": round(ca_pnl + fa_pnl + fr_missed, 2),
            },
            "error_ranking_by_cost": error_ranking,
            "module_dollar_efficiency": module_efficiency,
            "largest_leakage": error_ranking[0] if error_ranking else None,
        }

    # ─────────────────────────────────────────────────────────
    # PARAMETER OPTIMIZATION FRAMEWORK
    # ─────────────────────────────────────────────────────────

    def get_parameter_sensitivity(self) -> Dict:
        """
        Analyze how threshold changes would have affected historical results.

        For each tunable parameter, shows:
            - Current value
            - Optimal value (from historical data)
            - PF at current vs optimal
            - Trade count impact

        This enables evidence-driven parameter tuning.
        """
        self._ensure_loaded()

        # Get all accepted trades with EV and TQI data
        accepted = []
        for entry in self._decision_log:
            if entry.get("decision") == "ACCEPT":
                accepted.append(entry)

        rejected = []
        for entry in self._decision_log:
            if entry.get("decision") == "REJECT":
                rejected.append(entry)

        if not accepted:
            return {"status": "insufficient_data"}

        # EV threshold sensitivity
        ev_thresholds = [0, 1, 2, 3, 5, 8, 10]
        ev_sensitivity = {}
        for thresh in ev_thresholds:
            would_accept = [e for e in accepted if (e.get("ev_usd") or 0) >= thresh]
            would_accept += [e for e in rejected if (e.get("ev_usd") or 0) >= thresh]
            # Only count those with outcomes
            with_outcome = [e for e in would_accept if e.get("outcome_pnl") is not None]
            if len(with_outcome) >= 5:
                pnls = [e["outcome_pnl"] for e in with_outcome]
                gp = sum(p for p in pnls if p > 0)
                gl = abs(sum(p for p in pnls if p < 0))
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
                ev_sensitivity[thresh] = {
                    "threshold": thresh,
                    "trades": len(with_outcome),
                    "pf": round(pf, 2),
                    "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
                    "expectancy": round(sum(pnls) / len(pnls), 2),
                }

        # TQI threshold sensitivity
        tqi_thresholds = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
        tqi_sensitivity = {}
        for thresh in tqi_thresholds:
            would_accept = [e for e in accepted if (e.get("tqi") or 0) >= thresh]
            would_accept += [e for e in rejected if (e.get("tqi") or 0) >= thresh]
            with_outcome = [e for e in would_accept if e.get("outcome_pnl") is not None]
            if len(with_outcome) >= 5:
                pnls = [e["outcome_pnl"] for e in with_outcome]
                gp = sum(p for p in pnls if p > 0)
                gl = abs(sum(p for p in pnls if p < 0))
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
                tqi_sensitivity[thresh] = {
                    "threshold": thresh,
                    "trades": len(with_outcome),
                    "pf": round(pf, 2),
                    "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
                    "expectancy": round(sum(pnls) / len(pnls), 2),
                }

        # Confidence threshold sensitivity
        conf_thresholds = [70, 75, 80, 85, 88, 90, 92, 95]
        conf_sensitivity = {}
        for thresh in conf_thresholds:
            would_accept = [e for e in accepted if (e.get("confidence") or 0) >= thresh]
            would_accept += [e for e in rejected if (e.get("confidence") or 0) >= thresh]
            with_outcome = [e for e in would_accept if e.get("outcome_pnl") is not None]
            if len(with_outcome) >= 5:
                pnls = [e["outcome_pnl"] for e in with_outcome]
                gp = sum(p for p in pnls if p > 0)
                gl = abs(sum(p for p in pnls if p < 0))
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
                conf_sensitivity[thresh] = {
                    "threshold": thresh,
                    "trades": len(with_outcome),
                    "pf": round(pf, 2),
                    "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
                    "expectancy": round(sum(pnls) / len(pnls), 2),
                }

        # Find optimal thresholds
        def _find_optimal(sensitivity):
            if not sensitivity:
                return None
            # Maximize PF with minimum 20 trades
            candidates = {k: v for k, v in sensitivity.items() if v["trades"] >= 20}
            if not candidates:
                candidates = sensitivity
            best = max(candidates.items(), key=lambda x: x[1]["pf"])
            return {"threshold": best[0], **best[1]}

        return {
            "ev_threshold": {
                "current_analysis": ev_sensitivity,
                "optimal": _find_optimal(ev_sensitivity),
            },
            "tqi_threshold": {
                "current_analysis": tqi_sensitivity,
                "optimal": _find_optimal(tqi_sensitivity),
            },
            "confidence_threshold": {
                "current_analysis": conf_sensitivity,
                "optimal": _find_optimal(conf_sensitivity),
            },
            "instructions": (
                "Run weekly. Compare optimal vs current thresholds. "
                "Only change if optimal PF > current PF by ≥0.05 over ≥100 trades. "
                "Change ONE parameter at a time. Forward-test 50 trades before changing another."
            ),
        }

    def get_parameter_stability(self) -> Dict:
        """
        Evaluate parameter stability to prevent overfitting.

        For each tunable parameter, measures:
            - Best value (peak PF)
            - Stable operating range (plateau)
            - Sensitivity to ±10% and ±20% changes
            - Stability score (A/B/C/D)

        Only deploy parameters rated A or B.

        A = Very stable (PF changes < 0.03 with ±20% perturbation)
        B = Stable (PF changes < 0.06 with ±20% perturbation)
        C = Sensitive (PF changes < 0.10 with ±20% perturbation)
        D = High overfitting risk (PF changes ≥ 0.10 with ±20% perturbation)
        """
        self._ensure_loaded()

        # Get all decisions with outcomes
        all_decisions = []
        for entry in self._decision_log:
            if entry.get("outcome_pnl") is not None:
                all_decisions.append(entry)

        if len(all_decisions) < 30:
            return {"status": "insufficient_data", "minimum_trades": 30}

        def _evaluate_threshold(param_name, values, extractor):
            """Evaluate stability for a single parameter."""
            results = {}
            for val in values:
                matching = [d for d in all_decisions if extractor(d) >= val]
                if len(matching) < 10:
                    continue
                pnls = [d["outcome_pnl"] for d in matching]
                gp = sum(p for p in pnls if p > 0)
                gl = abs(sum(p for p in pnls if p < 0))
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
                results[val] = {
                    "pf": round(pf, 3),
                    "trades": len(matching),
                    "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
                }

            if len(results) < 3:
                return None

            # Find best value
            best_val = max(results.items(), key=lambda x: x[1]["pf"])
            best_pf = best_val[1]["pf"]
            best_threshold = best_val[0]

            # Find stable range (PF within 0.03 of best)
            stable_range = []
            for val, stats in sorted(results.items()):
                if abs(stats["pf"] - best_pf) <= 0.03:
                    stable_range.append(val)

            # Sensitivity analysis
            # ±10% perturbation
            plus_10 = best_threshold * 1.1
            minus_10 = best_threshold * 0.9
            # Find nearest values in results
            nearest_plus_10 = min(results.keys(), key=lambda x: abs(x - plus_10))
            nearest_minus_10 = min(results.keys(), key=lambda x: abs(x - minus_10))

            pf_at_plus_10 = results[nearest_plus_10]["pf"]
            pf_at_minus_10 = results[nearest_minus_10]["pf"]
            sensitivity_10 = max(abs(pf_at_plus_10 - best_pf), abs(pf_at_minus_10 - best_pf))

            # ±20% perturbation
            plus_20 = best_threshold * 1.2
            minus_20 = best_threshold * 0.8
            nearest_plus_20 = min(results.keys(), key=lambda x: abs(x - plus_20))
            nearest_minus_20 = min(results.keys(), key=lambda x: abs(x - minus_20))

            pf_at_plus_20 = results[nearest_plus_20]["pf"]
            pf_at_minus_20 = results[nearest_minus_20]["pf"]
            sensitivity_20 = max(abs(pf_at_plus_20 - best_pf), abs(pf_at_minus_20 - best_pf))

            # Stability score
            if sensitivity_20 < 0.03:
                grade = "A"
                interpretation = "Very stable — safe to deploy"
            elif sensitivity_20 < 0.06:
                grade = "B"
                interpretation = "Stable — deploy with monitoring"
            elif sensitivity_20 < 0.10:
                grade = "C"
                interpretation = "Sensitive — higher overfitting risk"
            else:
                grade = "D"
                interpretation = "High overfitting risk — do not deploy without large sample"

            return {
                "best_value": best_threshold,
                "best_pf": round(best_pf, 3),
                "stable_range": stable_range if stable_range else [best_threshold],
                "sensitivity_10pct": round(sensitivity_10, 3),
                "sensitivity_20pct": round(sensitivity_20, 3),
                "grade": grade,
                "interpretation": interpretation,
                "curve": {str(k): v for k, v in sorted(results.items())},
            }

        # Evaluate each parameter
        stability = {}

        # EV threshold
        ev_result = _evaluate_threshold(
            "ev_threshold",
            [0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 7, 10],
            lambda d: d.get("ev_usd") or 0,
        )
        if ev_result:
            stability["ev_threshold"] = ev_result

        # TQI threshold
        tqi_result = _evaluate_threshold(
            "tqi_threshold",
            [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65],
            lambda d: d.get("tqi") or 0,
        )
        if tqi_result:
            stability["tqi_threshold"] = tqi_result

        # Confidence threshold
        conf_result = _evaluate_threshold(
            "confidence_threshold",
            [70, 75, 78, 80, 82, 85, 87, 90, 92, 95],
            lambda d: d.get("confidence") or 0,
        )
        if conf_result:
            stability["confidence_threshold"] = conf_result

        # Deployment safety check
        deployable = []
        not_deployable = []
        for param, stats in stability.items():
            if stats["grade"] in ("A", "B"):
                deployable.append(param)
            else:
                not_deployable.append(param)

        return {
            "parameters": stability,
            "deployable": deployable,
            "not_deployable": not_deployable,
            "promotion_criteria": {
                "pf_improvement_min": 0.05,
                "expectancy_must_improve": True,
                "drawdown_must_not_increase": True,
                "min_trades": 100,
                "forward_sample_required": 50,
                "stability_grade_min": "B",
            },
            "instructions": (
                "Only deploy parameters with grade A or B. "
                "Before promoting: (1) PF must improve ≥0.05, "
                "(2) expectancy must improve, (3) drawdown must not increase, "
                "(4) improvement must persist over 50+ forward trades. "
                "If any condition fails, keep the previous parameter set."
            ),
        }

    # ─────────────────────────────────────────────────────────
    # PARAMETER BUNDLE OPTIMIZATION
    # ─────────────────────────────────────────────────────────

    def optimize_parameter_bundles(
        self, current_bundle: Optional[Dict[str, float]] = None,
    ) -> Dict:
        """
        Optimize parameter bundles instead of individual parameters.

        Parameters interact — EV=1.5 with Admission=65 may be optimal,
        but EV=2.0 with Admission=72 may be even better. Tuning one
        at a time misses these interactions.

        Tests combinations and ranks bundles by:
            - Profit Factor
            - Expectancy
            - Recovery Factor
            - Stability across regimes

        Args:
            current_bundle: Currently deployed parameter values

        Returns:
            Ranked bundles with performance metrics
        """
        self._ensure_loaded()

        all_decisions = []
        for entry in self._decision_log:
            if entry.get("outcome_pnl") is not None:
                all_decisions.append(entry)

        if len(all_decisions) < 50:
            return {"status": "insufficient_data", "minimum_trades": 50}

        # Define parameter grid (not too fine — avoid overfitting)
        ev_values = [0, 1, 2, 3, 5]
        tqi_values = [0.35, 0.40, 0.45, 0.50, 0.55]
        conf_values = [80, 85, 88, 90, 92]

        bundles = []
        bundle_id = 0

        for ev in ev_values:
            for tqi in tqi_values:
                for conf in conf_values:
                    # Simulate: which trades would this bundle accept?
                    would_accept = []
                    for d in all_decisions:
                        d_ev = d.get("ev_usd") or 0
                        d_tqi = d.get("tqi") or 0
                        d_conf = d.get("confidence") or 0
                        if d_ev >= ev and d_tqi >= tqi and d_conf >= conf:
                            would_accept.append(d)

                    if len(would_accept) < 20:
                        continue

                    pnls = [d["outcome_pnl"] for d in would_accept]
                    gp = sum(p for p in pnls if p > 0)
                    gl = abs(sum(p for p in pnls if p < 0))
                    pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
                    wr = sum(1 for p in pnls if p > 0) / len(pnls)
                    ev_avg = sum(pnls) / len(pnls)

                    # Max drawdown
                    cumulative = 0
                    peak = 0
                    max_dd = 0
                    for p in pnls:
                        cumulative += p
                        if cumulative > peak:
                            peak = cumulative
                        dd = peak - cumulative
                        if dd > max_dd:
                            max_dd = dd

                    recovery = sum(pnls) / max_dd if max_dd > 0 else (
                        float('inf') if sum(pnls) > 0 else 0
                    )

                    # Regime stability
                    regime_pnls: Dict[str, List[float]] = defaultdict(list)
                    for d in would_accept:
                        regime = d.get("regime", "unknown")
                        regime_pnls[regime].append(d["outcome_pnl"])

                    regime_pfs = {}
                    for regime, rp in regime_pnls.items():
                        if len(rp) >= 3:
                            rgp = sum(p for p in rp if p > 0)
                            rgl = abs(sum(p for p in rp if p < 0))
                            regime_pfs[regime] = rgp / rgl if rgl > 0 else (
                                float('inf') if rgp > 0 else 0
                            )

                    # Regime consistency (min PF across regimes)
                    min_regime_pf = min(regime_pfs.values()) if regime_pfs else 0

                    bundle_id += 1
                    bundles.append({
                        "id": bundle_id,
                        "ev_threshold": ev,
                        "tqi_threshold": tqi,
                        "confidence_threshold": conf,
                        "trades": len(would_accept),
                        "pf": round(pf, 3),
                        "win_rate": round(wr, 3),
                        "expectancy": round(ev_avg, 2),
                        "max_drawdown": round(max_dd, 2),
                        "recovery_factor": round(recovery, 2),
                        "regime_pfs": {k: round(v, 2) for k, v in regime_pfs.items()},
                        "min_regime_pf": round(min_regime_pf, 2),
                        "regime_count": len(regime_pfs),
                    })

        if not bundles:
            return {"status": "no_valid_bundles"}

        # Rank by composite score: PF × min_regime_pf × recovery / drawdown
        for b in bundles:
            dd_div = max(0.01, b["max_drawdown"])
            b["composite_score"] = round(
                b["pf"] * max(0.1, b["min_regime_pf"]) * min(10, b["recovery_factor"]) / dd_div,
                3,
            )

        bundles.sort(key=lambda x: x["composite_score"], reverse=True)

        # Find current bundle rank
        current_rank = None
        if current_bundle:
            for i, b in enumerate(bundles):
                if (b["ev_threshold"] == current_bundle.get("ev_threshold")
                        and b["tqi_threshold"] == current_bundle.get("tqi_threshold")
                        and b["confidence_threshold"] == current_bundle.get("confidence_threshold")):
                    current_rank = i + 1
                    break

        return {
            "top_bundles": bundles[:10],
            "total_bundles_tested": len(bundles),
            "current_bundle_rank": current_rank,
            "current_bundle": current_bundle,
            "best_bundle": bundles[0] if bundles else None,
            "improvement_over_current": (
                round(bundles[0]["pf"] - bundles[current_rank - 1]["pf"], 3)
                if current_rank and current_rank <= len(bundles) else None
            ),
        }

    def log_bundle_deployment(
        self, bundle: Dict[str, float], reason: str = "",
    ) -> None:
        """
        Log a parameter bundle deployment for drift tracking.

        Args:
            bundle: Parameter values deployed
            reason: Why this bundle was deployed
        """
        entry = {
            "timestamp": time.time(),
            "bundle": bundle,
            "reason": reason,
        }
        self._bundle_history.append(entry)
        if len(self._bundle_history) > 100:
            self._bundle_history = self._bundle_history[-100:]

    def get_parameter_drift(self) -> Dict:
        """
        Track how often the best parameter bundle changes.

        If the best bundle changes every week → chasing noise.
        If it stays stable for months → evidence of robustness.
        """
        if len(self._bundle_history) < 2:
            return {
                "total_deployments": len(self._bundle_history),
                "drift_rate": 0,
                "assessment": "Insufficient data — need 2+ deployments",
            }

        changes = 0
        for i in range(1, len(self._bundle_history)):
            prev = self._bundle_history[i - 1]["bundle"]
            curr = self._bundle_history[i]["bundle"]
            if prev != curr:
                changes += 1

        drift_rate = changes / (len(self._bundle_history) - 1)

        if drift_rate < 0.2:
            assessment = "STABLE — bundle rarely changes, evidence of robustness"
        elif drift_rate < 0.5:
            assessment = "MODERATE — some drift, monitor for noise chasing"
        else:
            assessment = "HIGH — bundle changes frequently, likely chasing noise"

        return {
            "total_deployments": len(self._bundle_history),
            "bundle_changes": changes,
            "drift_rate": round(drift_rate, 2),
            "assessment": assessment,
            "history": [
                {
                    "timestamp": h["timestamp"],
                    "bundle": h["bundle"],
                    "reason": h.get("reason", ""),
                }
                for h in self._bundle_history[-10:]
            ],
        }

    # ─────────────────────────────────────────────────────────
    # AUTOMATIC ROLLBACK
    # ─────────────────────────────────────────────────────────

    def check_rollback_conditions(
        self,
        current_bundle: Dict[str, float],
        baseline_pf: float = 1.0,
        max_drawdown_pct: float = 10.0,
    ) -> Dict:
        """
        Check if current bundle should be rolled back.

        Rollback triggers:
            1. PF falls below baseline
            2. Expectancy becomes negative
            3. Drawdown exceeds tolerance
            4. Performance degrades across multiple regimes

        Args:
            current_bundle: Currently deployed parameters
            baseline_pf: PF floor (rollback if below)
            max_drawdown_pct: Max drawdown % tolerance

        Returns:
            Dict with rollback decision and reason
        """
        self._ensure_loaded()

        # Get recent trades (since last bundle deployment)
        recent_cutoff = 0
        if self._bundle_history:
            recent_cutoff = self._bundle_history[-1]["timestamp"]

        recent = []
        for entry in self._decision_log:
            if (entry.get("decision") == "ACCEPT"
                    and entry.get("outcome_pnl") is not None
                    and entry.get("timestamp", 0) >= recent_cutoff):
                recent.append(entry)

        if len(recent) < 20:
            return {
                "rollback": False,
                "reason": "Insufficient recent trades for rollback evaluation",
                "recent_count": len(recent),
            }

        pnls = [e["outcome_pnl"] for e in recent]
        gp = sum(p for p in pnls if p > 0)
        gl = abs(sum(p for p in pnls if p < 0))
        pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
        ev = sum(pnls) / len(pnls)

        # Max drawdown
        cumulative = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        triggers = []

        if pf < baseline_pf:
            triggers.append(f"PF={pf:.2f} < baseline={baseline_pf:.2f}")

        if ev < 0:
            triggers.append(f"Expectancy=${ev:.2f} is negative")

        if peak > 0 and (max_dd / peak * 100) > max_drawdown_pct:
            triggers.append(
                f"Drawdown={max_dd/peak*100:.1f}% > {max_drawdown_pct:.1f}% tolerance"
            )

        # Regime degradation
        regime_pnls: Dict[str, List[float]] = defaultdict(list)
        for e in recent:
            regime = e.get("regime", "unknown")
            regime_pnls[regime].append(e["outcome_pnl"])

        losing_regimes = 0
        for regime, rp in regime_pnls.items():
            if len(rp) >= 3:
                r_ev = sum(rp) / len(rp)
                if r_ev < 0:
                    losing_regimes += 1

        if losing_regimes >= 2:
            triggers.append(f"Negative expectancy in {losing_regimes} regimes")

        rollback = len(triggers) >= 2  # Need 2+ triggers for rollback

        return {
            "rollback": rollback,
            "recent_trades": len(recent),
            "recent_pf": round(pf, 2),
            "recent_expectancy": round(ev, 2),
            "recent_max_drawdown": round(max_dd, 2),
            "triggers": triggers,
            "trigger_count": len(triggers),
            "action": "ROLLBACK" if rollback else "HOLD",
            "reason": (
                f"Rollback triggered: {'; '.join(triggers)}"
                if rollback
                else "Performance acceptable — continue with current bundle"
            ),
        }

    # ─────────────────────────────────────────────────────────
    # MULTI-OBJECTIVE OPTIMIZATION SCORE
    # ─────────────────────────────────────────────────────────

    def compute_bundle_score(self, bundle_metrics: Dict) -> float:
        """
        Compute multi-objective optimization score.

        Instead of maximizing PF alone:
            Score = 0.35 × PF + 0.25 × Expectancy + 0.15 × Recovery Factor
                  + 0.15 × Drawdown Stability + 0.10 × Trade Count Stability

        This balances profitability with robustness and sample size.

        Args:
            bundle_metrics: Dict with pf, expectancy, recovery_factor,
                           max_drawdown, trades, regime_count

        Returns:
            Composite score (higher is better)
        """
        pf = bundle_metrics.get("pf", 0)
        expectancy = bundle_metrics.get("expectancy", 0)
        recovery = bundle_metrics.get("recovery_factor", 0)
        max_dd = bundle_metrics.get("max_drawdown", 0)
        trades = bundle_metrics.get("trades", 0)
        regime_count = bundle_metrics.get("regime_count", 1)

        # Normalize PF (cap at 3.0 for scoring)
        pf_norm = min(3.0, max(0, pf)) / 3.0

        # Normalize expectancy (scale to 0-1, centered at $5)
        ev_norm = max(0, min(1, (expectancy + 5) / 10))

        # Normalize recovery factor (cap at 5.0)
        rf_norm = min(5.0, max(0, recovery)) / 5.0

        # Drawdown stability (inverse — lower DD is better)
        dd_stability = max(0, 1 - max_dd / 100) if max_dd > 0 else 1.0

        # Trade count stability (penalize too few or too many)
        # Sweet spot: 30-200 trades per evaluation window
        if trades < 20:
            trade_stability = 0.3
        elif trades < 30:
            trade_stability = 0.6
        elif trades <= 200:
            trade_stability = 1.0
        else:
            trade_stability = 0.8  # Slightly penalize very high count

        score = (
            0.35 * pf_norm
            + 0.25 * ev_norm
            + 0.15 * rf_norm
            + 0.15 * dd_stability
            + 0.10 * trade_stability
        )

        return round(score, 4)

    # ─────────────────────────────────────────────────────────
    # CHAMPION-CHALLENGER PROCESS
    # ─────────────────────────────────────────────────────────

    def setup_champion_challenger(
        self,
        champion: Dict[str, float],
        challenger: Dict[str, float],
        evaluation_window: int = 100,
    ) -> Dict:
        """
        Set up champion-challenger comparison.

        Instead of replacing the live bundle immediately, run both
        in parallel and compare after evaluation_window trades.

        Args:
            champion: Currently deployed parameter bundle
            challenger: Candidate parameter bundle
            evaluation_window: Number of comparable opportunities

        Returns:
            Setup confirmation with tracking IDs
        """
        setup = {
            "timestamp": time.time(),
            "champion": champion,
            "challenger": challenger,
            "evaluation_window": evaluation_window,
            "champion_trades": 0,
            "challenger_trades": 0,
            "champion_pnls": [],
            "challenger_pnls": [],
            "status": "ACTIVE",
        }
        self._champion_challenger = setup

        logger.info(
            "⚔️ CHAMPION-CHALLENGER: Champion={} vs Challenger={} (window={})",
            champion, challenger, evaluation_window,
        )

        return setup

    def record_champion_challenger_outcome(
        self, bundle_type: str, pnl: float,
    ) -> None:
        """
        Record an outcome for champion or challenger.

        Args:
            bundle_type: "champion" or "challenger"
            pnl: Trade PnL
        """
        if not self._champion_challenger:
            return

        if bundle_type == "champion":
            self._champion_challenger["champion_pnls"].append(pnl)
            self._champion_challenger["champion_trades"] += 1
        else:
            self._champion_challenger["challenger_pnls"].append(pnl)
            self._champion_challenger["challenger_trades"] += 1

    def evaluate_champion_challenger(self) -> Dict:
        """
        Evaluate champion vs challenger after evaluation window.

        Returns comparison with promotion recommendation.
        """
        cc = self._champion_challenger
        if not cc:
            return {"status": "no_active_comparison"}

        champ_pnls = cc["champion_pnls"]
        chall_pnls = cc["challenger_pnls"]

        def _metrics(pnls):
            if not pnls:
                return {"pf": 0, "expectancy": 0, "trades": 0, "max_dd": 0}
            gp = sum(p for p in pnls if p > 0)
            gl = abs(sum(p for p in pnls if p < 0))
            pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
            ev = sum(pnls) / len(pnls)
            cumulative = 0
            peak = 0
            max_dd = 0
            for p in pnls:
                cumulative += p
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_dd:
                    max_dd = dd
            return {"pf": round(pf, 3), "expectancy": round(ev, 2), "trades": len(pnls), "max_dd": round(max_dd, 2)}

        champ = _metrics(champ_pnls)
        chall = _metrics(chall_pnls)

        # Multi-objective scores
        champ["score"] = self.compute_bundle_score(champ)
        chall["score"] = self.compute_bundle_score(chall)

        # Decision
        window_reached = (
            cc["champion_trades"] >= cc["evaluation_window"]
            or cc["challenger_trades"] >= cc["evaluation_window"]
        )

        promote = False
        reason = ""

        if not window_reached:
            reason = f"Evaluation window not reached ({cc['champion_trades']}/{cc['evaluation_window']})"
        elif chall["score"] > champ["score"] * 1.05:  # Need 5% improvement
            promote = True
            reason = f"Challenger outperforms: score {chall['score']:.4f} vs {champ['score']:.4f}"
        else:
            reason = f"Challenger does not outperform: score {chall['score']:.4f} vs {champ['score']:.4f}"

        return {
            "status": "COMPLETE" if window_reached else "IN_PROGRESS",
            "champion": champ,
            "challenger": chall,
            "promote_challenger": promote,
            "reason": reason,
            "window_reached": window_reached,
        }

    # ─────────────────────────────────────────────────────────
    # PARAMETER HISTORY
    # ─────────────────────────────────────────────────────────

    def log_parameter_change(
        self,
        bundle: Dict[str, float],
        action: str = "DEPLOYED",
        reason: str = "",
        pf_at_change: float = 0.0,
        expectancy_at_change: float = 0.0,
    ) -> None:
        """
        Log a parameter change for permanent history.

        Creates an auditable trail of all parameter changes.

        Args:
            bundle: Parameter values
            action: DEPLOYED / ROLLED_BACK / TESTED / REJECTED
            reason: Why this change was made
            pf_at_change: PF at time of change
            expectancy_at_change: Expectancy at time of change
        """
        entry = {
            "timestamp": time.time(),
            "bundle": bundle,
            "action": action,
            "reason": reason,
            "pf_at_change": pf_at_change,
            "expectancy_at_change": expectancy_at_change,
        }
        self._parameter_history.append(entry)
        if len(self._parameter_history) > 500:
            self._parameter_history = self._parameter_history[-500:]

    def get_parameter_history(self) -> List[Dict]:
        """Get complete parameter change history."""
        return [
            {
                **h,
                "timestamp_formatted": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(h["timestamp"])
                ),
            }
            for h in self._parameter_history
        ]
