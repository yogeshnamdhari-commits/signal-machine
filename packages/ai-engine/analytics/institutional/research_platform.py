"""
Institutional Research Platform — Unified Analytics Engine
==========================================================
Comprehensive trade analytics for statistical validation.

READ-ONLY — Never modifies trading logic.

Provides:
- Trade Analytics (win rate, expectancy, profit factor, Sharpe, Sortino)
- Confidence Research (bucket analysis, calibration)
- Pattern Research (per-pattern performance)
- Symbol Analytics (per-symbol performance)
- Session Analytics (Asian, London, New York, Overlap)
- Regime Analytics (Bull, Bear, Range, High/Low Vol)
- Exit Analytics (TP, SL, Trailing, Timeout)
- Rejection Analytics (per-stage rejection rates)
- Candidate Lifecycle Tracking

All queries read from existing databases. No writes. No schema changes.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """Normalized trade record for analytics."""
    id: int
    signal_id: str
    symbol: str
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    pnl: float
    fees: float
    confidence: float
    regime: str
    session: str
    risk_reward: float
    hold_minutes: float
    mfe_pct: float
    mae_pct: float
    realized_r: float
    exit_reason: str
    outcome: str  # 'win' or 'loss'
    opened_at: float
    closed_at: float
    strategy_version: str
    institutional_score: float
    at_open_regime: str
    at_open_session: str
    entry_reason: str


@dataclass
class BucketStats:
    """Statistics for a single bucket (confidence, pattern, etc.)."""
    bucket: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    avg_rr: float = 0.0
    avg_hold_minutes: float = 0.0
    avg_mfe: float = 0.0
    avg_mae: float = 0.0
    avg_realized_r: float = 0.0
    sharpe: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0


@dataclass
class ExitStats:
    """Statistics for exit reasons."""
    exit_reason: str
    count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    win_rate: float = 0.0
    expectancy: float = 0.0
    avg_hold_minutes: float = 0.0


@dataclass
class LifecycleEvent:
    """A single lifecycle transition event."""
    symbol: str
    from_state: str
    to_state: str
    timestamp: float
    duration_minutes: float = 0.0


@dataclass
class CandidateStats:
    """Candidate lifecycle statistics."""
    state: str
    count: int = 0
    avg_dwell_minutes: float = 0.0
    median_dwell_minutes: float = 0.0
    conversion_rate: float = 0.0


@dataclass
class PortfolioSummary:
    """Complete portfolio analytics summary."""
    # Overview
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    
    # Risk metrics
    avg_rr: float = 0.0
    avg_realized_r: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    recovery_factor: float = 0.0
    max_drawdown: float = 0.0
    
    # Timing
    avg_hold_minutes: float = 0.0
    max_hold_minutes: float = 0.0
    
    # Excursion
    avg_mfe: float = 0.0
    avg_mae: float = 0.0
    efficiency: float = 0.0  # realized_r / MFE
    
    # Streaks
    max_win_streak: int = 0
    max_loss_streak: int = 0
    
    # Buckets
    confidence_buckets: Dict[str, BucketStats] = field(default_factory=dict)
    pattern_buckets: Dict[str, BucketStats] = field(default_factory=dict)
    symbol_buckets: Dict[str, BucketStats] = field(default_factory=dict)
    session_buckets: Dict[str, BucketStats] = field(default_factory=dict)
    regime_buckets: Dict[str, BucketStats] = field(default_factory=dict)
    exit_stats: Dict[str, ExitStats] = field(default_factory=dict)
    
    # Lifecycle
    candidate_stats: Dict[str, CandidateStats] = field(default_factory=dict)
    live_patterns: Dict[str, Any] = field(default_factory=dict)
    
    # Time series
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    rolling_win_rate: List[Dict[str, Any]] = field(default_factory=list)
    rolling_expectancy: List[Dict[str, Any]] = field(default_factory=list)


# ─── Main Engine ─────────────────────────────────────────────────────────────

class ResearchPlatform:
    """
    Unified analytics engine for institutional trade research.
    
    READ-ONLY: Never modifies trading logic.
    Reads from:
    - positions_archive (completed trades)
    - signals (candidate signals)
    - ema_v5 states (from bridge JSON)
    """
    
    def __init__(
        self,
        trades_db: Optional[str] = None,
        signals_db: Optional[str] = None,
        bridge_path: Optional[str] = None,
    ):
        base = Path(__file__).resolve().parent.parent.parent / "data"
        self._trades_db = trades_db or str(base / "institutional_v1.db")
        self._signals_db = signals_db or str(base / "database" / "performance_tracker.db")
        self._bridge_path = bridge_path or str(
            base.parent.parent.parent / "data" / "bridge" / "ema_v5.json"
        )
    
    # ── Database ──────────────────────────────────────────────────────────
    
    def _connect_trades(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._trades_db, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _connect_signals(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._signals_db, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _load_bridge(self) -> Dict[str, Any]:
        """Load bridge JSON for state data."""
        try:
            with open(self._bridge_path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load bridge: {}", e)
            return {}
    
    # ── Trade Loading ─────────────────────────────────────────────────────
    
    def _load_trades(self, min_confidence: float = 0.0) -> List[TradeRecord]:
        """Load all completed EMA V5 trades from canonical view."""
        conn = self._connect_trades()
        trades = []
        try:
            # Use canonical view — single source of truth for EMA V5 trades
            rows = conn.execute("""
                SELECT id, signal_id, symbol, side, entry_price, stop_loss,
                       take_profit, pnl, fees, confidence, regime, session,
                       risk_reward, hold_minutes, mfe_pct, mae_pct,
                       realized_r, exit_reason, outcome, opened_at, closed_at,
                       strategy_version, institutional_score,
                       at_open_regime, at_open_session, entry_reason
                FROM ema_v5_trade_facts
            """).fetchall()
            for r in rows:
                conf = r["confidence"] or 0.0
                if conf < min_confidence:
                    continue
                trades.append(TradeRecord(
                    id=r["id"] or 0,
                    signal_id=r["signal_id"] or "",
                    symbol=r["symbol"] or "",
                    side=r["side"] or "",
                    entry_price=r["entry_price"] or 0.0,
                    stop_loss=r["stop_loss"] or 0.0,
                    take_profit=r["take_profit"] or 0.0,
                    pnl=r["pnl"] or 0.0,
                    fees=r["fees"] or 0.0,
                    confidence=conf,
                    regime=r["regime"] or "",
                    session=r["session"] or "",
                    risk_reward=r["risk_reward"] or 0.0,
                    hold_minutes=r["hold_minutes"] or 0.0,
                    mfe_pct=r["mfe_pct"] or 0.0,
                    mae_pct=r["mae_pct"] or 0.0,
                    realized_r=r["realized_r"] or 0.0,
                    exit_reason=self._normalize_exit(r["exit_reason"] or ""),
                    outcome=r["outcome"] or "",
                    opened_at=r["opened_at"] or 0.0,
                    closed_at=r["closed_at"] or 0.0,
                    strategy_version=r["strategy_version"] or "",
                    institutional_score=r["institutional_score"] or 0.0,
                    at_open_regime=r["at_open_regime"] or "",
                    at_open_session=r["at_open_session"] or "",
                    entry_reason=r["entry_reason"] or "",
                ))
            return trades
        finally:
            conn.close()
    
    def _normalize_exit(self, reason: str) -> str:
        """Normalize exit reason to standard categories."""
        reason_lower = reason.lower()
        if reason_lower.startswith("take_profit"):
            return "take_profit"
        if "trailing_stop" in reason_lower or "mfe_trailing" in reason_lower:
            return "trailing_stop"
        if "stop_loss" in reason_lower:
            return "stop_loss"
        if "time_exit" in reason_lower or "max_hold" in reason_lower:
            return "time_exit"
        if "no_progress" in reason_lower:
            return "no_progress"
        return reason_lower or "unknown"
    
    # ── Core Statistics ───────────────────────────────────────────────────
    
    def _compute_bucket_stats(
        self, trades: List[TradeRecord], bucket_key: str
    ) -> Dict[str, BucketStats]:
        """Compute statistics grouped by a key field."""
        groups: Dict[str, List[TradeRecord]] = defaultdict(list)
        for t in trades:
            key = getattr(t, bucket_key, "") or "unknown"
            groups[key].append(t)
        
        result = {}
        for key, group in groups.items():
            stats = self._compute_stats_for_group(key, group)
            result[key] = stats
        return result
    
    def _compute_stats_for_group(
        self, label: str, trades: List[TradeRecord]
    ) -> BucketStats:
        """Compute detailed statistics for a group of trades."""
        if not trades:
            return BucketStats(bucket=label)
        
        pnls = [t.pnl for t in trades]
        wins = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]
        win_pnls = [t.pnl for t in wins]
        loss_pnls = [t.pnl for t in losses]
        realized_rs = [t.realized_r for t in trades if t.realized_r != 0]
        rrs = [t.risk_reward for t in trades if t.risk_reward > 0]
        hold_mins = [t.hold_minutes for t in trades if t.hold_minutes > 0]
        mfes = [t.mfe_pct for t in trades if t.mfe_pct != 0]
        maes = [t.mae_pct for t in trades if t.mae_pct != 0]
        
        gross_profit = sum(win_pnls) if win_pnls else 0.0
        gross_loss = abs(sum(loss_pnls)) if loss_pnls else 0.0
        
        return BucketStats(
            bucket=label,
            trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            win_rate=len(wins) / len(trades) if trades else 0.0,
            total_pnl=sum(pnls),
            avg_pnl=statistics.mean(pnls) if pnls else 0.0,
            expectancy=statistics.mean(pnls) if pnls else 0.0,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else (
                float("inf") if gross_profit > 0 else 0.0
            ),
            avg_rr=statistics.mean(rrs) if rrs else 0.0,
            avg_hold_minutes=statistics.mean(hold_mins) if hold_mins else 0.0,
            avg_mfe=statistics.mean(mfes) if mfes else 0.0,
            avg_mae=statistics.mean(maes) if maes else 0.0,
            avg_realized_r=statistics.mean(realized_rs) if realized_rs else 0.0,
            sharpe=self._sharpe(pnls),
            max_win=max(pnls) if pnls else 0.0,
            max_loss=min(pnls) if pnls else 0.0,
        )
    
    @staticmethod
    def _sharpe(returns: List[float], risk_free: float = 0.0) -> float:
        """Compute Sharpe ratio."""
        if len(returns) < 2:
            return 0.0
        mean = statistics.mean(returns)
        std = statistics.stdev(returns)
        if std == 0:
            return 0.0
        return (mean - risk_free) / std
    
    @staticmethod
    def _sortino(returns: List[float], risk_free: float = 0.0) -> float:
        """Compute Sortino ratio (downside deviation only)."""
        if len(returns) < 2:
            return 0.0
        mean = statistics.mean(returns)
        downside = [r for r in returns if r < risk_free]
        if len(downside) < 2:
            return float("inf") if mean > risk_free else 0.0
        down_std = statistics.stdev(downside)
        if down_std == 0:
            return 0.0
        return (mean - risk_free) / down_std
    
    # ── Confidence Buckets ────────────────────────────────────────────────
    
    def _bucket_confidence(self, trades: List[TradeRecord]) -> Dict[str, BucketStats]:
        """Bucket trades by confidence score."""
        buckets = {
            "40-45%": [], "45-50%": [], "50-55%": [],
            "55-60%": [], "60-65%": [], "65-70%": [],
            "70-75%": [], "75-80%": [], "80-85%": [],
            "85-90%": [], "90-95%": [], "95-100%": [],
        }
        for t in trades:
            conf_pct = t.confidence * 100 if t.confidence <= 1 else t.confidence
            if conf_pct < 45:
                buckets["40-45%"].append(t)
            elif conf_pct < 50:
                buckets["45-50%"].append(t)
            elif conf_pct < 55:
                buckets["50-55%"].append(t)
            elif conf_pct < 60:
                buckets["55-60%"].append(t)
            elif conf_pct < 65:
                buckets["60-65%"].append(t)
            elif conf_pct < 70:
                buckets["65-70%"].append(t)
            elif conf_pct < 75:
                buckets["70-75%"].append(t)
            elif conf_pct < 80:
                buckets["75-80%"].append(t)
            elif conf_pct < 85:
                buckets["80-85%"].append(t)
            elif conf_pct < 90:
                buckets["85-90%"].append(t)
            elif conf_pct < 95:
                buckets["90-95%"].append(t)
            else:
                buckets["95-100%"].append(t)
        
        result = {}
        for label, group in buckets.items():
            if group:
                result[label] = self._compute_stats_for_group(label, group)
        return result
    
    # ── Pattern Buckets ───────────────────────────────────────────────────
    
    def _bucket_patterns(self, trades: List[TradeRecord]) -> Dict[str, BucketStats]:
        """Bucket trades by candle pattern."""
        groups: Dict[str, List[TradeRecord]] = defaultdict(list)
        for t in trades:
            pattern = self._extract_pattern(t)
            groups[pattern].append(t)
        
        result = {}
        for pattern, group in groups.items():
            result[pattern] = self._compute_stats_for_group(pattern, group)
        return result
    
    @staticmethod
    def _extract_pattern(trade: TradeRecord) -> str:
        """Extract pattern name from trade data."""
        entry = (trade.entry_reason or "").lower()
        patterns = {
            "hammer": "Hammer",
            "bullish_pin": "Bullish Pin Bar",
            "bearish_pin": "Bearish Pin Bar",
            "bullish_engulf": "Bullish Engulfing",
            "bearish_engulf": "Bearish Engulfing",
            "shooting_star": "Shooting Star",
            "morning_star": "Morning Star",
            "evening_star": "Evening Star",
            "inside_bar": "Inside Bar",
            "outside_bar": "Outside Bar",
            "doji": "Doji",
            "three_white": "Three White Soldiers",
            "three_black": "Three Black Crows",
            "harami": "Harami",
            "piercing": "Piercing Line",
            "dark_cloud": "Dark Cloud Cover",
        }
        for key, name in patterns.items():
            if key in entry:
                return name
        return entry.title() if entry else "Unknown"
    
    # ── Session Buckets ───────────────────────────────────────────────────
    
    def _bucket_sessions(self, trades: List[TradeRecord]) -> Dict[str, BucketStats]:
        """Bucket trades by trading session."""
        groups: Dict[str, List[TradeRecord]] = defaultdict(list)
        for t in trades:
            session = (t.session or t.at_open_session or "unknown").lower()
            if not session:
                session = "unknown"
            groups[session].append(t)
        result = {}
        for session, group in groups.items():
            result[session] = self._compute_stats_for_group(session, group)
        return result
    
    # ── Regime Buckets ────────────────────────────────────────────────────
    
    def _bucket_regimes(self, trades: List[TradeRecord]) -> Dict[str, BucketStats]:
        """Bucket trades by market regime."""
        groups: Dict[str, List[TradeRecord]] = defaultdict(list)
        for t in trades:
            regime = (t.regime or t.at_open_regime or "unknown").lower()
            if not regime:
                regime = "unknown"
            groups[regime].append(t)
        result = {}
        for regime, group in groups.items():
            result[regime] = self._compute_stats_for_group(regime, group)
        return result
    
    # ── Exit Analytics ────────────────────────────────────────────────────
    
    def _compute_exit_stats(self, trades: List[TradeRecord]) -> Dict[str, ExitStats]:
        """Compute statistics by exit reason."""
        groups: Dict[str, List[TradeRecord]] = defaultdict(list)
        for t in trades:
            groups[t.exit_reason].append(t)
        
        result = {}
        for reason, group in groups.items():
            wins = [t for t in group if t.outcome == "win"]
            pnls = [t.pnl for t in group]
            holds = [t.hold_minutes for t in group if t.hold_minutes > 0]
            
            result[reason] = ExitStats(
                exit_reason=reason,
                count=len(group),
                wins=len(wins),
                losses=len(group) - len(wins),
                total_pnl=sum(pnls),
                avg_pnl=statistics.mean(pnls) if pnls else 0.0,
                win_rate=len(wins) / len(group) if group else 0.0,
                expectancy=statistics.mean(pnls) if pnls else 0.0,
                avg_hold_minutes=statistics.mean(holds) if holds else 0.0,
            )
        return result
    
    # ── Symbol Buckets ────────────────────────────────────────────────────
    
    def _bucket_symbols(self, trades: List[TradeRecord]) -> Dict[str, BucketStats]:
        """Bucket trades by symbol."""
        groups: Dict[str, List[TradeRecord]] = defaultdict(list)
        for t in trades:
            groups[t.symbol].append(t)
        result = {}
        for symbol, group in groups.items():
            result[symbol] = self._compute_stats_for_group(symbol, group)
        return result
    
    # ── Candidate Lifecycle ───────────────────────────────────────────────
    
    def _compute_candidate_stats(self) -> Dict[str, CandidateStats]:
        """Compute candidate lifecycle statistics from bridge data."""
        bridge = self._load_bridge()
        states = bridge.get("ema_v5", {}).get("states", {})
        
        if not states:
            return {}
        
        import time
        now = time.time()
        
        state_groups: Dict[str, List[float]] = defaultdict(list)
        for sym, data in states.items():
            state = data.get("state", "NO_TREND")
            last_update = data.get("last_update", now)
            dwell_min = (now - last_update) / 60
            state_groups[state].append(dwell_min)
        
        result = {}
        for state, dwell_times in state_groups.items():
            result[state] = CandidateStats(
                state=state,
                count=len(dwell_times),
                avg_dwell_minutes=statistics.mean(dwell_times) if dwell_times else 0.0,
                median_dwell_minutes=statistics.median(dwell_times) if dwell_times else 0.0,
            )
        return result
    
    # ── Rejection Analytics ───────────────────────────────────────────────
    
    def _compute_rejection_stats(self) -> Dict[str, Any]:
        """Compute rejection statistics from bridge pipeline data."""
        bridge = self._load_bridge()
        pipe = bridge.get("ema_v5", {}).get("scanner", {}).get("pipeline", {})
        
        if not pipe:
            return {}
        
        rejected = pipe.get("stage_rejections", {})
        passed = pipe.get("stage_passed", {})
        total = pipe.get("total_candidates", 0)
        
        stages = ["fast_filter", "regime", "trend", "pullback", "candle", "volume", "confidence"]
        result = {"total_candidates": total, "stages": []}
        
        for stage in stages:
            r = rejected.get(stage, 0)
            p = passed.get(stage, 0)
            result["stages"].append({
                "stage": stage,
                "passed": p,
                "rejected": r,
                "rejection_rate": r / (p + r) if (p + r) > 0 else 0.0,
            })
        return result
    
    # ── Live Candidate Pattern Analysis ───────────────────────────────────
    
    def _compute_live_patterns(self) -> Dict[str, Any]:
        """Analyze patterns from live candidates in bridge data."""
        bridge = self._load_bridge()
        signals = bridge.get("ema_v5", {}).get("signals", [])
        
        if not signals:
            return {}
        
        patterns: Dict[str, List[Dict]] = defaultdict(list)
        for s in signals:
            components = s.get("components", {})
            candle = components.get("candle", "unknown")
            patterns[candle].append({
                "symbol": s.get("symbol"),
                "side": s.get("side"),
                "confidence": s.get("confidence", 0),
                "regime": s.get("regime"),
            })
        
        result = {}
        for pattern, candidates in sorted(patterns.items(), key=lambda x: -len(x[1])):
            confs = [c["confidence"] for c in candidates]
            result[pattern] = {
                "count": len(candidates),
                "avg_confidence": round(sum(confs) / len(confs), 1) if confs else 0,
                "symbols": [c["symbol"] for c in candidates],
            }
        return result
    
    # ── Equity Curve ──────────────────────────────────────────────────────
    
    def _compute_equity_curve(self, trades: List[TradeRecord]) -> List[Dict[str, Any]]:
        """Compute cumulative equity curve with drawdown."""
        sorted_trades = sorted(trades, key=lambda t: t.closed_at or t.opened_at)
        curve = []
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        max_abs_dd = 0.0
        
        for i, t in enumerate(sorted_trades):
            cumulative += t.pnl
            peak = max(peak, cumulative)
            abs_dd = peak - cumulative
            # Use percentage only when peak is meaningful (> $50)
            dd_pct = (abs_dd / peak * 100) if peak > 50 else 0.0
            max_dd = max(max_dd, dd_pct)
            max_abs_dd = max(max_abs_dd, abs_dd)
            
            curve.append({
                "trade_num": i + 1,
                "symbol": t.symbol,
                "pnl": t.pnl,
                "cumulative_pnl": cumulative,
                "drawdown_pct": dd_pct,
                "drawdown_abs": abs_dd,
                "timestamp": t.closed_at or t.opened_at,
            })
        return curve
    
    # ── Streaks ───────────────────────────────────────────────────────────
    
    @staticmethod
    def _compute_streaks(trades: List[TradeRecord]) -> Tuple[int, int]:
        """Compute max win and loss streaks."""
        if not trades:
            return 0, 0
        
        sorted_trades = sorted(trades, key=lambda t: t.closed_at or t.opened_at)
        max_win = 0
        max_loss = 0
        current_win = 0
        current_loss = 0
        
        for t in sorted_trades:
            if t.outcome == "win":
                current_win += 1
                current_loss = 0
                max_win = max(max_win, current_win)
            else:
                current_loss += 1
                current_win = 0
                max_loss = max(max_loss, current_loss)
        
        return max_win, max_loss
    
    # ── Rolling Metrics ───────────────────────────────────────────────────
    
    def _compute_rolling_metrics(
        self, trades: List[TradeRecord], window: int = 20
    ) -> Tuple[List[Dict], List[Dict]]:
        """Compute rolling win rate and expectancy."""
        sorted_trades = sorted(trades, key=lambda t: t.closed_at or t.opened_at)
        rolling_wr = []
        rolling_exp = []
        
        for i in range(window, len(sorted_trades) + 1):
            batch = sorted_trades[i - window:i]
            wins = sum(1 for t in batch if t.outcome == "win")
            pnls = [t.pnl for t in batch]
            
            rolling_wr.append({
                "trade_num": i,
                "win_rate": wins / len(batch),
                "timestamp": batch[-1].closed_at,
            })
            rolling_exp.append({
                "trade_num": i,
                "expectancy": statistics.mean(pnls),
                "timestamp": batch[-1].closed_at,
            })
        
        return rolling_wr, rolling_exp
    
    # ── Full Analytics ────────────────────────────────────────────────────
    
    def compute_full_analytics(self) -> PortfolioSummary:
        """Compute complete portfolio analytics."""
        trades = self._load_trades()
        
        if not trades:
            logger.warning("No trades found for analytics")
            return PortfolioSummary()
        
        # Core metrics
        pnls = [t.pnl for t in trades]
        wins = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]
        win_pnls = [t.pnl for t in wins]
        loss_pnls = [t.pnl for t in losses]
        realized_rs = [t.realized_r for t in trades if t.realized_r != 0]
        rrs = [t.risk_reward for t in trades if t.risk_reward > 0]
        hold_mins = [t.hold_minutes for t in trades if t.hold_minutes > 0]
        mfes = [t.mfe_pct for t in trades if t.mfe_pct != 0]
        maes = [t.mae_pct for t in trades if t.mae_pct != 0]
        
        gross_profit = sum(win_pnls) if win_pnls else 0.0
        gross_loss = abs(sum(loss_pnls)) if loss_pnls else 0.0
        
        # Equity curve & drawdown
        equity = self._compute_equity_curve(trades)
        max_dd_pct = max((e["drawdown_pct"] for e in equity), default=0.0)
        max_abs_dd = max((e["drawdown_abs"] for e in equity), default=0.0)
        # Use absolute drawdown as primary metric when peak equity is small
        peak_equity = max((e["cumulative_pnl"] for e in equity), default=0.0)
        max_dd = max_dd_pct if peak_equity > 50 else (max_abs_dd / max(abs(peak_equity), 1) * 100)
        
        # Streaks
        max_win_streak, max_loss_streak = self._compute_streaks(trades)
        
        # Rolling metrics
        rolling_wr, rolling_exp = self._compute_rolling_metrics(trades)
        
        # Efficiency (realized R / MFE)
        efficiencies = []
        for t in trades:
            if t.mfe_pct != 0 and t.realized_r != 0:
                eff = t.realized_r / (t.mfe_pct * 100) if t.mfe_pct < 1 else t.realized_r / t.mfe_pct
                efficiencies.append(eff)
        
        # Calmar ratio (annualized return / max drawdown)
        if trades and max_dd > 0:
            total_days = max(
                (t.closed_at - t.opened_at for t in trades if t.closed_at and t.opened_at),
                default=86400,
            ) / 86400
            if total_days < 1:
                total_days = 1
            annualized_return = (sum(pnls) / total_days) * 365
            calmar = annualized_return / (max_dd / 100) if max_dd > 0 else 0.0
        else:
            calmar = 0.0
        
        # Recovery factor
        recovery = sum(pnls) / abs(min(pnls)) if min(pnls) < 0 else (
            float("inf") if sum(pnls) > 0 else 0.0
        )
        
        summary = PortfolioSummary(
            total_trades=len(trades),
            total_wins=len(wins),
            total_losses=len(losses),
            win_rate=len(wins) / len(trades) if trades else 0.0,
            total_pnl=sum(pnls),
            avg_pnl=statistics.mean(pnls) if pnls else 0.0,
            expectancy=statistics.mean(pnls) if pnls else 0.0,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else (
                float("inf") if gross_profit > 0 else 0.0
            ),
            avg_rr=statistics.mean(rrs) if rrs else 0.0,
            avg_realized_r=statistics.mean(realized_rs) if realized_rs else 0.0,
            sharpe=self._sharpe(pnls),
            sortino=self._sortino(pnls),
            calmar=calmar,
            recovery_factor=recovery,
            max_drawdown=max_dd,
            avg_hold_minutes=statistics.mean(hold_mins) if hold_mins else 0.0,
            max_hold_minutes=max(hold_mins) if hold_mins else 0.0,
            avg_mfe=statistics.mean(mfes) if mfes else 0.0,
            avg_mae=statistics.mean(maes) if maes else 0.0,
            efficiency=statistics.mean(efficiencies) if efficiencies else 0.0,
            max_win_streak=max_win_streak,
            max_loss_streak=max_loss_streak,
            confidence_buckets=self._bucket_confidence(trades),
            pattern_buckets=self._bucket_patterns(trades),
            symbol_buckets=self._bucket_symbols(trades),
            session_buckets=self._bucket_sessions(trades),
            regime_buckets=self._bucket_regimes(trades),
            exit_stats=self._compute_exit_stats(trades),
            candidate_stats=self._compute_candidate_stats(),
            live_patterns=self._compute_live_patterns(),
            equity_curve=equity,
            rolling_win_rate=rolling_wr,
            rolling_expectancy=rolling_exp,
        )
        
        logger.info(
            "Analytics computed: {} trades, WR={:.1f}%, PF={:.2f}, Exp={:.4f}",
            summary.total_trades, summary.win_rate * 100,
            summary.profit_factor, summary.expectancy,
        )
        return summary
    
    # ── Serialization ─────────────────────────────────────────────────────
    
    def get_pipeline_reconciliation(self) -> Dict[str, Any]:
        """Get full pipeline reconciliation: scanner → session filter → positions → analyzed.
        
        Three data sources:
        1. Scanner runtime _signal_count (persisted to scanner_state.json)
        2. signals table (EMA V5 identified by metadata)
        3. positions_archive + positions (strategy_version='ema_v5')
        """
        conn = self._connect_trades()
        try:
            # Count EMA V5 signals in DB
            cur = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE metadata LIKE '%ema_v5%'"
            )
            ema_v5_signals_db = cur.fetchone()[0]
            
            # Count ALL signals
            cur = conn.execute("SELECT COUNT(*) FROM signals")
            all_signals_db = cur.fetchone()[0]
            
            # Count EMA V5 positions
            cur = conn.execute(
                "SELECT COUNT(*) FROM positions_archive WHERE strategy_version='ema_v5' AND status='closed'"
            )
            closed_trades = cur.fetchone()[0]
            
            cur = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE strategy_version='ema_v5' AND status='open'"
            )
            open_trades = cur.fetchone()[0]
            
            # Count with/without outcome
            cur = conn.execute(
                "SELECT COUNT(*) FROM positions_archive WHERE strategy_version='ema_v5' AND status='closed' AND outcome IS NOT NULL"
            )
            with_outcome = cur.fetchone()[0]
            
            cur = conn.execute(
                "SELECT COUNT(*) FROM positions_archive WHERE strategy_version='ema_v5' AND status='closed' AND outcome IS NULL"
            )
            without_outcome = cur.fetchone()[0]
            
            total_positions = open_trades + closed_trades
            
            # Scanner runtime count (persisted to ema_v5_scan_count.json)
            scanner_signal_count = 0
            try:
                state_file = Path(__file__).resolve().parent.parent.parent / "data" / "ema_v5_scan_count.json"
                if state_file.exists():
                    import json as _json
                    with open(state_file) as f:
                        state = _json.loads(f.read())
                        scanner_signal_count = state.get("signal_count", 0)
            except Exception:
                pass
            
            # Rejection tracker data
            rejection_breakdown = {}
            rejection_traces = 0
            try:
                import json as _json
                trace_file = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "signal_trace.jsonl"
                if trace_file.exists():
                    with open(trace_file) as f:
                        for line in f:
                            try:
                                trace = _json.loads(line)
                                rejection_traces += 1
                                gate = trace.get("rejection_gate", "")
                                if gate:
                                    rejection_breakdown[gate] = rejection_breakdown.get(gate, 0) + 1
                            except:
                                pass
            except Exception:
                pass
            
            # Session filter rejects = scanner signals - DB signals
            session_filtered = max(0, scanner_signal_count - ema_v5_signals_db)
            
            # Signal-to-trade conversion
            conversion_rate = (total_positions / ema_v5_signals_db * 100) if ema_v5_signals_db > 0 else 0
            filtered_out = ema_v5_signals_db - total_positions
            
            return {
                "scanner_signal_count": scanner_signal_count,
                "ema_v5_signals_db": ema_v5_signals_db,
                "session_filtered": session_filtered,
                "all_signals_db": all_signals_db,
                "open_positions": open_trades,
                "closed_positions": closed_trades,
                "total_positions": total_positions,
                "with_outcome": with_outcome,
                "without_outcome": without_outcome,
                "analyzed_trades": with_outcome,
                "excluded_trades": without_outcome,
                "conversion_rate": round(conversion_rate, 1),
                "filtered_out": filtered_out,
                "rejection_traces": rejection_traces,
                "rejection_breakdown": rejection_breakdown,
            }
        finally:
            conn.close()

    @staticmethod
    def to_dict(summary: PortfolioSummary) -> Dict[str, Any]:
        """Convert summary to JSON-serializable dict."""
        def _bucket_to_dict(b: BucketStats) -> Dict[str, Any]:
            return {
                "bucket": b.bucket, "trades": b.trades,
                "wins": b.wins, "losses": b.losses,
                "win_rate": round(b.win_rate, 4),
                "total_pnl": round(b.total_pnl, 2),
                "avg_pnl": round(b.avg_pnl, 4),
                "expectancy": round(b.expectancy, 4),
                "profit_factor": round(b.profit_factor, 4),
                "avg_rr": round(b.avg_rr, 4),
                "avg_hold_minutes": round(b.avg_hold_minutes, 1),
                "avg_mfe": round(b.avg_mfe, 4),
                "avg_mae": round(b.avg_mae, 4),
                "avg_realized_r": round(b.avg_realized_r, 4),
                "sharpe": round(b.sharpe, 4),
                "max_win": round(b.max_win, 2),
                "max_loss": round(b.max_loss, 2),
            }
        
        def _exit_to_dict(e: ExitStats) -> Dict[str, Any]:
            return {
                "exit_reason": e.exit_reason, "count": e.count,
                "wins": e.wins, "losses": e.losses,
                "total_pnl": round(e.total_pnl, 2),
                "avg_pnl": round(e.avg_pnl, 4),
                "win_rate": round(e.win_rate, 4),
                "expectancy": round(e.expectancy, 4),
                "avg_hold_minutes": round(e.avg_hold_minutes, 1),
            }
        
        def _candidate_to_dict(c: CandidateStats) -> Dict[str, Any]:
            return {
                "state": c.state, "count": c.count,
                "avg_dwell_minutes": round(c.avg_dwell_minutes, 1),
                "median_dwell_minutes": round(c.median_dwell_minutes, 1),
            }
        
        return {
            "overview": {
                "total_trades": summary.total_trades,
                "total_wins": summary.total_wins,
                "total_losses": summary.total_losses,
                "win_rate": round(summary.win_rate, 4),
                "total_pnl": round(summary.total_pnl, 2),
                "avg_pnl": round(summary.avg_pnl, 4),
                "expectancy": round(summary.expectancy, 4),
                "profit_factor": round(summary.profit_factor, 4),
            },
            "risk_metrics": {
                "avg_rr": round(summary.avg_rr, 4),
                "avg_realized_r": round(summary.avg_realized_r, 4),
                "sharpe": round(summary.sharpe, 4),
                "sortino": round(summary.sortino, 4),
                "calmar": round(summary.calmar, 4),
                "recovery_factor": round(summary.recovery_factor, 4),
                "max_drawdown_pct": round(summary.max_drawdown, 2),
            },
            "timing": {
                "avg_hold_minutes": round(summary.avg_hold_minutes, 1),
                "max_hold_minutes": round(summary.max_hold_minutes, 1),
            },
            "excursion": {
                "avg_mfe": round(summary.avg_mfe, 4),
                "avg_mae": round(summary.avg_mae, 4),
                "efficiency": round(summary.efficiency, 4),
            },
            "streaks": {
                "max_win_streak": summary.max_win_streak,
                "max_loss_streak": summary.max_loss_streak,
            },
            "confidence_analysis": {
                k: _bucket_to_dict(v)
                for k, v in sorted(summary.confidence_buckets.items())
            },
            "pattern_analysis": {
                k: _bucket_to_dict(v)
                for k, v in sorted(
                    summary.pattern_buckets.items(),
                    key=lambda x: x[1].trades, reverse=True,
                )
            },
            "symbol_analysis": {
                k: _bucket_to_dict(v)
                for k, v in sorted(
                    summary.symbol_buckets.items(),
                    key=lambda x: x[1].trades, reverse=True,
                )
            },
            "session_analysis": {
                k: _bucket_to_dict(v)
                for k, v in sorted(summary.session_buckets.items())
            },
            "regime_analysis": {
                k: _bucket_to_dict(v)
                for k, v in sorted(summary.regime_buckets.items())
            },
            "exit_analysis": {
                k: _exit_to_dict(v)
                for k, v in sorted(
                    summary.exit_stats.items(),
                    key=lambda x: x[1].count, reverse=True,
                )
            },
            "candidate_lifecycle": {
                k: _candidate_to_dict(v)
                for k, v in sorted(summary.candidate_stats.items())
            },
            "live_pattern_analysis": summary.live_patterns,
            "equity_curve": summary.equity_curve[-200:],  # Last 200 points
            "rolling_win_rate": summary.rolling_win_rate[-100:],
            "rolling_expectancy": summary.rolling_expectancy[-100:],
        }
    
    def export_json(self, path: Optional[str] = None) -> str:
        """Export full analytics to JSON."""
        summary = self.compute_full_analytics()
        data = self.to_dict(summary)
        
        json_path = path or str(
            Path(self._trades_db).parent.parent / "data" / "bridge" / "research_analytics.json"
        )
        
        with open(json_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        
        logger.info("Analytics exported to {}", json_path)
        return json_path


# ─── Quick Access ────────────────────────────────────────────────────────────

def get_research_summary() -> Dict[str, Any]:
    """Quick function to get full analytics dict."""
    platform = ResearchPlatform()
    summary = platform.compute_full_analytics()
    return platform.to_dict(summary)


if __name__ == "__main__":
    platform = ResearchPlatform()
    summary = platform.compute_full_analytics()
    data = platform.to_dict(summary)
    
    print("=" * 60)
    print("INSTITUTIONAL RESEARCH PLATFORM — ANALYTICS SUMMARY")
    print("=" * 60)
    
    o = data["overview"]
    print(f"\nTotal Trades: {o['total_trades']}")
    print(f"Win Rate: {o['win_rate']*100:.1f}%")
    print(f"Profit Factor: {o['profit_factor']:.2f}")
    print(f"Expectancy: ${o['expectancy']:.2f}")
    print(f"Total PnL: ${o['total_pnl']:.2f}")
    
    r = data["risk_metrics"]
    print(f"\nSharpe: {r['sharpe']:.2f}")
    print(f"Sortino: {r['sortino']:.2f}")
    print(f"Max Drawdown: {r['max_drawdown_pct']:.1f}%")
    print(f"Avg Realized R: {r['avg_realized_r']:.2f}")
    
    print(f"\n--- Confidence Buckets ---")
    for k, v in data["confidence_analysis"].items():
        print(f"  {k}: {v['trades']} trades, WR={v['win_rate']*100:.1f}%, "
              f"PF={v['profit_factor']:.2f}, Exp=${v['avg_pnl']:.2f}")
    
    print(f"\n--- Session Analysis ---")
    for k, v in data["session_analysis"].items():
        print(f"  {k}: {v['trades']} trades, WR={v['win_rate']*100:.1f}%, "
              f"PF={v['profit_factor']:.2f}")
    
    print(f"\n--- Regime Analysis ---")
    for k, v in data["regime_analysis"].items():
        print(f"  {k}: {v['trades']} trades, WR={v['win_rate']*100:.1f}%, "
              f"PF={v['profit_factor']:.2f}")
    
    print(f"\n--- Exit Analysis ---")
    for k, v in data["exit_analysis"].items():
        print(f"  {k}: {v['count']} exits, WR={v['win_rate']*100:.1f}%, "
              f"PnL=${v['total_pnl']:.2f}")
    
    print(f"\n--- Top Symbols ---")
    symbols = sorted(
        data["symbol_analysis"].items(),
        key=lambda x: x[1]["trades"], reverse=True,
    )[:10]
    for k, v in symbols:
        print(f"  {k}: {v['trades']} trades, WR={v['win_rate']*100:.1f}%, "
              f"PF={v['profit_factor']:.2f}")
    
    # Export
    path = platform.export_json()
    print(f"\nExported to: {path}")
