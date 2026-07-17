"""
Candidate Repository — Institutional-grade rejected & accepted candidate analytics.

Reads from ema_v5_calibration.db (979+ candidates with outcome tracking).
Computes:
- Accepted vs Rejected candidate performance
- "What almost happened" — rejected candidates that would have been winners
- Filter effectiveness by stage
- Confidence calibration against actual outcomes
- Rolling validation (25/50/100-trade windows)
- Production scorecard with all key metrics

READ-ONLY — Never modifies trading logic or database.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ─── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class CandidateRecord:
    """Normalized candidate record from calibration DB."""
    id: int
    timestamp: float
    symbol: str
    direction: str
    entry_price: float
    confidence: float
    trend_score: float
    pullback_score: float
    candle_score: float
    volume_score: float
    regime_score: float
    regime: str
    candle_pattern: str
    volume_ratio: float
    passed: bool
    rejection_stage: str
    rejection_reason: str
    # Outcome data
    price_1h: Optional[float] = None
    price_4h: Optional[float] = None
    price_24h: Optional[float] = None
    mfe: Optional[float] = None
    mae: Optional[float] = None
    return_pct: Optional[float] = None
    rr_achieved: Optional[float] = None
    outcome_tracked: bool = False
    # Derived
    would_have_won: bool = False
    would_have_lost: bool = False


@dataclass
class FilterEffectiveness:
    """Effectiveness metrics for a single filter stage."""
    stage: str
    total_rejected: int = 0
    rejected_winners: int = 0
    rejected_losers: int = 0
    rejected_unknown: int = 0
    winner_rate: float = 0.0  # % of rejected that would have won
    avg_winner_return: float = 0.0
    avg_loser_return: float = 0.0
    total_opportunity_cost: float = 0.0  # sum of returns of rejected winners


@dataclass
class ComponentMarginal:
    """Marginal contribution of a single component/filter."""
    component: str  # e.g. "Volume", "Candle", "Trend"
    bucket: str     # e.g. "0-30", "85-100"
    candidates: int = 0
    winners: int = 0
    losers: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0
    avg_rr: float = 0.0
    profit_factor: float = 0.0


@dataclass
class RollingWindow:
    """Rolling validation metrics for a window of N trades."""
    window_size: int
    trade_num: int
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0


@dataclass
class ProductionScorecard:
    """Complete production scorecard with all key metrics."""
    # Core
    total_trades: int = 0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    expectancy_r: float = 0.0
    avg_r: float = 0.0
    win_rate: float = 0.0
    # Risk
    avg_mae: float = 0.0
    avg_mfe: float = 0.0
    largest_drawdown: float = 0.0
    # Best/Worst
    best_symbol: str = ""
    worst_symbol: str = ""
    best_session: str = ""
    worst_session: str = ""
    # Rolling
    rolling_25: Optional[RollingWindow] = None
    rolling_50: Optional[RollingWindow] = None
    rolling_100: Optional[RollingWindow] = None
    # Candidate stats
    accepted_signals: int = 0
    rejected_candidates: int = 0
    rejected_winners: int = 0
    rejected_losers: int = 0
    rejected_winner_pct: float = 0.0
    rejected_loser_pct: float = 0.0


@dataclass
class RepositorySummary:
    """Complete candidate repository analytics."""
    # Counts
    total_candidates: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    tracked_count: int = 0
    # Accepted performance
    accepted_winners: int = 0
    accepted_losers: int = 0
    accepted_win_rate: float = 0.0
    accepted_avg_return: float = 0.0
    # Rejected performance (what almost happened)
    rejected_with_outcome: int = 0
    rejected_winners: int = 0
    rejected_losers: int = 0
    rejected_winner_pct: float = 0.0
    rejected_avg_return: float = 0.0
    # Filter effectiveness by stage
    filter_stages: Dict[str, FilterEffectiveness] = field(default_factory=dict)
    # Component marginal contribution
    component_marginals: Dict[str, List[ComponentMarginal]] = field(default_factory=dict)
    # Confidence calibration
    confidence_calibration: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Scorecard
    scorecard: Optional[ProductionScorecard] = None
    # Rolling validation
    rolling_25: List[RollingWindow] = field(default_factory=list)
    rolling_50: List[RollingWindow] = field(default_factory=list)
    rolling_100: List[RollingWindow] = field(default_factory=list)


# ─── Main Engine ─────────────────────────────────────────────────────────────

class CandidateRepository:
    """
    Institutional candidate repository analytics.
    
    READ-ONLY: Never modifies trading logic.
    Reads from:
    - ema_v5_calibration.db (candidates with outcome tracking)
    - institutional_v1.db (completed trades for scorecard)
    """
    
    def __init__(
        self,
        calibration_db: Optional[str] = None,
        trades_db: Optional[str] = None,
    ):
        base = Path(__file__).resolve().parent.parent.parent / "data"
        self._calibration_db = calibration_db or str(base / "ema_v5_calibration.db")
        self._trades_db = trades_db or str(base / "institutional_v1.db")
    
    def _connect(self, db_path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    # ── Candidate Loading ─────────────────────────────────────────────────
    
    def _load_candidates(self) -> List[CandidateRecord]:
        """Load all candidates from calibration DB."""
        conn = self._connect(self._calibration_db)
        candidates = []
        try:
            rows = conn.execute("""
                SELECT id, timestamp, symbol, direction, entry_price,
                       confidence, trend_score, pullback_score, candle_score,
                       volume_score, regime_score, regime, candle_pattern,
                       volume_ratio, passed, rejection_stage, rejection_reason,
                       price_1h, price_4h, price_24h, mfe, mae,
                       return_pct, rr_achieved, outcome_tracked
                FROM candidates
                ORDER BY timestamp ASC
            """).fetchall()
            
            for r in rows:
                conf = r["confidence"] or 0
                entry = r["entry_price"] or 0
                ret_pct = r["return_pct"]
                
                # Determine if rejected candidate would have won
                would_win = False
                would_lose = False
                if ret_pct is not None:
                    would_win = ret_pct > 0.5  # > 0.5% return
                    would_lose = ret_pct < -0.5
                
                candidates.append(CandidateRecord(
                    id=r["id"],
                    timestamp=r["timestamp"] or 0,
                    symbol=r["symbol"] or "",
                    direction=r["direction"] or "",
                    entry_price=entry,
                    confidence=conf,
                    trend_score=r["trend_score"] or 0,
                    pullback_score=r["pullback_score"] or 0,
                    candle_score=r["candle_score"] or 0,
                    volume_score=r["volume_score"] or 0,
                    regime_score=r["regime_score"] or 0,
                    regime=r["regime"] or "",
                    candle_pattern=r["candle_pattern"] or "",
                    volume_ratio=r["volume_ratio"] or 0,
                    passed=bool(r["passed"]),
                    rejection_stage=r["rejection_stage"] or "",
                    rejection_reason=r["rejection_reason"] or "",
                    price_1h=r["price_1h"],
                    price_4h=r["price_4h"],
                    price_24h=r["price_24h"],
                    mfe=r["mfe"],
                    mae=r["mae"],
                    return_pct=ret_pct,
                    rr_achieved=r["rr_achieved"],
                    outcome_tracked=bool(r["outcome_tracked"]),
                    would_have_won=would_win,
                    would_have_lost=would_lose,
                ))
            return candidates
        finally:
            conn.close()
    
    def _load_trades(self) -> List[Dict[str, Any]]:
        """Load all completed trades from institutional DB."""
        conn = self._connect(self._trades_db)
        trades = []
        try:
            for table in ["positions_archive", "positions"]:
                rows = conn.execute(f"""
                    SELECT symbol, side, pnl, confidence, regime, session,
                           risk_reward, hold_minutes, mfe_pct, mae_pct,
                           realized_r, exit_reason, outcome, opened_at, closed_at
                    FROM {table}
                    WHERE status = 'closed' AND outcome IS NOT NULL
                    AND strategy_version = 'ema_v5'
                """).fetchall()
                trades.extend([dict(r) for r in rows])
            return trades
        finally:
            conn.close()
    
    # ── Filter Effectiveness ──────────────────────────────────────────────
    
    def _compute_filter_effectiveness(
        self, candidates: List[CandidateRecord]
    ) -> Dict[str, FilterEffectiveness]:
        """Compute effectiveness of each rejection stage."""
        # Group rejected candidates by rejection stage
        stage_groups: Dict[str, List[CandidateRecord]] = defaultdict(list)
        for c in candidates:
            if not c.passed and c.rejection_stage:
                stage_groups[c.rejection_stage].append(c)
        
        result = {}
        for stage, group in stage_groups.items():
            tracked = [c for c in group if c.outcome_tracked]
            winners = [c for c in tracked if c.would_have_won]
            losers = [c for c in tracked if c.would_have_lost]
            unknown = [c for c in tracked if not c.would_have_won and not c.would_have_lost]
            
            winner_returns = [c.return_pct for c in winners if c.return_pct is not None]
            loser_returns = [c.return_pct for c in losers if c.return_pct is not None]
            
            result[stage] = FilterEffectiveness(
                stage=stage,
                total_rejected=len(group),
                rejected_winners=len(winners),
                rejected_losers=len(losers),
                rejected_unknown=len(unknown),
                winner_rate=len(winners) / len(tracked) * 100 if tracked else 0,
                avg_winner_return=statistics.mean(winner_returns) if winner_returns else 0,
                avg_loser_return=statistics.mean(loser_returns) if loser_returns else 0,
                total_opportunity_cost=sum(winner_returns) if winner_returns else 0,
            )
        
        return result
    
    # ── Component Marginal Contribution ───────────────────────────────────
    
    def _compute_component_marginals(
        self, candidates: List[CandidateRecord]
    ) -> Dict[str, List[ComponentMarginal]]:
        """Compute per-component marginal contribution to outcomes.
        
        For each component (Volume, Candle, Trend, Regime, Pattern),
        bucket candidates by score range and compute Win %, Avg R, PF.
        """
        # Only analyze rejected candidates with tracked outcomes
        tracked = [c for c in candidates if c.outcome_tracked and not c.passed]
        if not tracked:
            return {}
        
        def _bucket_stats(
            component: str, bucket_name: str, group: List[CandidateRecord]
        ) -> Optional[ComponentMarginal]:
            if not group:
                return None
            returns = [c.return_pct for c in group if c.return_pct is not None]
            rrs = [c.rr_achieved for c in group if c.rr_achieved is not None and c.rr_achieved > 0]
            winners = [c for c in group if c.would_have_won]
            losers = [c for c in group if c.would_have_lost]
            gross_win = sum(r for r in returns if r > 0)
            gross_loss = abs(sum(r for r in returns if r < 0))
            return ComponentMarginal(
                component=component,
                bucket=bucket_name,
                candidates=len(group),
                winners=len(winners),
                losers=len(losers),
                win_rate=len(winners) / len(group) * 100 if group else 0,
                avg_return=statistics.mean(returns) if returns else 0,
                avg_rr=statistics.mean(rrs) if rrs else 0,
                profit_factor=gross_win / gross_loss if gross_loss > 0 else (
                    float("inf") if gross_win > 0 else 0
                ),
            )
        
        result: Dict[str, List[ComponentMarginal]] = {}
        
        # Volume Score buckets
        vol_buckets = [(0, 30, "0-30"), (30, 50, "30-50"), (50, 70, "50-70"), (70, 100, "70-100")]
        vol_marginals = []
        for low, high, name in vol_buckets:
            group = [c for c in tracked if low <= c.volume_score < high]
            s = _bucket_stats("Volume", name, group)
            if s:
                vol_marginals.append(s)
        if vol_marginals:
            result["Volume"] = vol_marginals
        
        # Trend Score buckets
        trend_buckets = [(0, 50, "0-50"), (50, 70, "50-70"), (70, 85, "70-85"), (85, 100, "85-100")]
        trend_marginals = []
        for low, high, name in trend_buckets:
            group = [c for c in tracked if low <= c.trend_score < high]
            s = _bucket_stats("Trend", name, group)
            if s:
                trend_marginals.append(s)
        if trend_marginals:
            result["Trend"] = trend_marginals
        
        # Candle Score buckets (most are 85-100, so finer granularity)
        candle_buckets = [(0, 70, "0-70"), (70, 85, "70-85"), (85, 95, "85-95"), (95, 100, "95-100")]
        candle_marginals = []
        for low, high, name in candle_buckets:
            group = [c for c in tracked if low <= c.candle_score < high]
            s = _bucket_stats("Candle", name, group)
            if s:
                candle_marginals.append(s)
        if candle_marginals:
            result["Candle"] = candle_marginals
        
        # Regime buckets
        regime_groups: Dict[str, List[CandidateRecord]] = defaultdict(list)
        for c in tracked:
            regime_groups[c.regime or "unknown"].append(c)
        regime_marginals = []
        for regime, group in sorted(regime_groups.items()):
            s = _bucket_stats("Regime", regime, group)
            if s:
                regime_marginals.append(s)
        if regime_marginals:
            result["Regime"] = regime_marginals
        
        # Candle Pattern buckets
        pattern_groups: Dict[str, List[CandidateRecord]] = defaultdict(list)
        for c in tracked:
            if c.candle_pattern:
                pattern_groups[c.candle_pattern].append(c)
        pattern_marginals = []
        for pattern, group in sorted(pattern_groups.items(), key=lambda x: -len(x[1])):
            s = _bucket_stats("Pattern", pattern, group)
            if s:
                pattern_marginals.append(s)
        if pattern_marginals:
            result["Pattern"] = pattern_marginals
        
        # Confidence Score buckets (for rejected candidates)
        conf_buckets = [(70, 75, "70-75%"), (75, 80, "75-80%"), (80, 85, "80-85%"), (85, 90, "85-90%")]
        conf_marginals = []
        for low, high, name in conf_buckets:
            group = [c for c in tracked if low <= c.confidence < high]
            s = _bucket_stats("Confidence", name, group)
            if s:
                conf_marginals.append(s)
        if conf_marginals:
            result["Confidence"] = conf_marginals
        
        return result
    
    # ── Confidence Calibration ────────────────────────────────────────────
    
    def _compute_confidence_calibration(
        self, candidates: List[CandidateRecord]
    ) -> Dict[str, Dict[str, Any]]:
        """Compute confidence calibration from candidates with outcomes."""
        buckets = {
            "70-75%": (70, 75), "75-80%": (75, 80), "80-85%": (80, 85),
            "85-90%": (85, 90), "90-95%": (90, 95), "95-100%": (95, 100),
        }
        
        result = {}
        for name, (low, high) in buckets.items():
            group = [
                c for c in candidates
                if c.outcome_tracked and low <= c.confidence < high
            ]
            if not group:
                continue
            
            returns = [c.return_pct for c in group if c.return_pct is not None]
            winners = [c for c in group if c.would_have_won]
            losers = [c for c in group if c.would_have_lost]
            passed = [c for c in group if c.passed]
            rejected = [c for c in group if not c.passed]
            
            # Among rejected, how many would have won?
            rej_tracked = [c for c in rejected if c.outcome_tracked]
            rej_winners = [c for c in rej_tracked if c.would_have_won]
            
            result[name] = {
                "total": len(group),
                "accepted": len(passed),
                "rejected": len(rejected),
                "winners": len(winners),
                "losers": len(losers),
                "win_rate": len(winners) / len(group) * 100 if group else 0,
                "avg_return": statistics.mean(returns) if returns else 0,
                "avg_confidence": statistics.mean([c.confidence for c in group]),
                "rejected_winners": len(rej_winners),
                "rejected_winner_pct": len(rej_winners) / len(rej_tracked) * 100 if rej_tracked else 0,
            }
        
        return result
    
    # ── Rolling Validation ────────────────────────────────────────────────
    
    def _compute_rolling_validation(
        self, trades: List[Dict[str, Any]], window: int
    ) -> List[RollingWindow]:
        """Compute rolling validation metrics for a given window size."""
        sorted_trades = sorted(trades, key=lambda t: t.get("closed_at", 0) or 0)
        results = []
        
        for i in range(window, len(sorted_trades) + 1):
            batch = sorted_trades[i - window:i]
            pnls = [t["pnl"] for t in batch if t.get("pnl") is not None]
            wins = [t for t in batch if t.get("outcome") == "win"]
            losses = [t for t in batch if t.get("outcome") == "loss"]
            realized_rs = [t["realized_r"] for t in batch if t.get("realized_r") and t["realized_r"] != 0]
            
            gross_profit = sum(t["pnl"] for t in wins if t.get("pnl"))
            gross_loss = abs(sum(t["pnl"] for t in losses if t.get("pnl")))
            
            results.append(RollingWindow(
                window_size=window,
                trade_num=i,
                win_rate=len(wins) / len(batch) if batch else 0,
                profit_factor=gross_profit / gross_loss if gross_loss > 0 else (
                    float("inf") if gross_profit > 0 else 0
                ),
                expectancy=statistics.mean(pnls) if pnls else 0,
                avg_r=statistics.mean(realized_rs) if realized_rs else 0,
                total_pnl=sum(pnls) if pnls else 0,
            ))
        
        return results
    
    # ── Production Scorecard ──────────────────────────────────────────────
    
    def _compute_scorecard(
        self,
        trades: List[Dict[str, Any]],
        candidates: List[CandidateRecord],
        rolling_25: List[RollingWindow],
        rolling_50: List[RollingWindow],
        rolling_100: List[RollingWindow],
    ) -> ProductionScorecard:
        """Compute complete production scorecard."""
        if not trades:
            return ProductionScorecard()
        
        pnls = [t["pnl"] for t in trades if t.get("pnl") is not None]
        wins = [t for t in trades if t.get("outcome") == "win"]
        losses = [t for t in trades if t.get("outcome") == "loss"]
        realized_rs = [t["realized_r"] for t in trades if t.get("realized_r") and t["realized_r"] != 0]
        mfes = [t["mfe_pct"] for t in trades if t.get("mfe_pct") and t["mfe_pct"] != 0]
        maes = [t["mae_pct"] for t in trades if t.get("mae_pct") and t["mae_pct"] != 0]
        
        gross_profit = sum(t["pnl"] for t in wins if t.get("pnl"))
        gross_loss = abs(sum(t["pnl"] for t in losses if t.get("pnl")))
        
        # Best/worst by symbol
        sym_pnl: Dict[str, float] = defaultdict(float)
        for t in trades:
            if t.get("pnl"):
                sym_pnl[t.get("symbol", "?")] += t["pnl"]
        best_sym = max(sym_pnl, key=sym_pnl.get) if sym_pnl else ""
        worst_sym = min(sym_pnl, key=sym_pnl.get) if sym_pnl else ""
        
        # Best/worst by session
        sess_pnl: Dict[str, float] = defaultdict(float)
        for t in trades:
            sess = t.get("session") or t.get("at_open_session") or "unknown"
            if t.get("pnl"):
                sess_pnl[sess] += t["pnl"]
        best_sess = max(sess_pnl, key=sess_pnl.get) if sess_pnl else ""
        worst_sess = min(sess_pnl, key=sess_pnl.get) if sess_pnl else ""
        
        # Max drawdown from equity curve
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in sorted(trades, key=lambda x: x.get("closed_at", 0) or 0):
            if t.get("pnl"):
                cumulative += t["pnl"]
                peak = max(peak, cumulative)
                dd = peak - cumulative
                max_dd = max(max_dd, dd)
        
        # Candidate stats
        accepted = [c for c in candidates if c.passed]
        rejected = [c for c in candidates if not c.passed]
        rej_tracked = [c for c in rejected if c.outcome_tracked]
        rej_winners = [c for c in rej_tracked if c.would_have_won]
        rej_losers = [c for c in rej_tracked if c.would_have_lost]
        
        return ProductionScorecard(
            total_trades=len(trades),
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else (
                float("inf") if gross_profit > 0 else 0
            ),
            expectancy=statistics.mean(pnls) if pnls else 0,
            expectancy_r=statistics.mean(realized_rs) if realized_rs else 0,
            avg_r=statistics.mean(realized_rs) if realized_rs else 0,
            win_rate=len(wins) / len(trades) * 100 if trades else 0,
            avg_mae=statistics.mean(maes) if maes else 0,
            avg_mfe=statistics.mean(mfes) if mfes else 0,
            largest_drawdown=max_dd,
            best_symbol=best_sym,
            worst_symbol=worst_sym,
            best_session=best_sess,
            worst_session=worst_sess,
            rolling_25=rolling_25[-1] if rolling_25 else None,
            rolling_50=rolling_50[-1] if rolling_50 else None,
            rolling_100=rolling_100[-1] if rolling_100 else None,
            accepted_signals=len(accepted),
            rejected_candidates=len(rejected),
            rejected_winners=len(rej_winners),
            rejected_losers=len(rej_losers),
            rejected_winner_pct=len(rej_winners) / len(rej_tracked) * 100 if rej_tracked else 0,
            rejected_loser_pct=len(rej_losers) / len(rej_tracked) * 100 if rej_tracked else 0,
        )
    
    # ── Full Analytics ────────────────────────────────────────────────────
    
    def compute_full_analytics(self) -> RepositorySummary:
        """Compute complete candidate repository analytics."""
        candidates = self._load_candidates()
        trades = self._load_trades()
        
        if not candidates:
            logger.warning("No candidates found in calibration DB")
            return RepositorySummary()
        
        # Split accepted/rejected
        accepted = [c for c in candidates if c.passed]
        rejected = [c for c in candidates if not c.passed]
        tracked = [c for c in candidates if c.outcome_tracked]
        
        # Accepted performance
        acc_tracked = [c for c in accepted if c.outcome_tracked]
        acc_winners = [c for c in acc_tracked if c.would_have_won]
        acc_losers = [c for c in acc_tracked if c.would_have_lost]
        acc_returns = [c.return_pct for c in acc_tracked if c.return_pct is not None]
        
        # Rejected performance (what almost happened)
        rej_tracked = [c for c in rejected if c.outcome_tracked]
        rej_winners = [c for c in rej_tracked if c.would_have_won]
        rej_losers = [c for c in rej_tracked if c.would_have_lost]
        rej_returns = [c.return_pct for c in rej_tracked if c.return_pct is not None]
        
        # Rolling validation
        rolling_25 = self._compute_rolling_validation(trades, 25)
        rolling_50 = self._compute_rolling_validation(trades, 50)
        rolling_100 = self._compute_rolling_validation(trades, 100)
        
        summary = RepositorySummary(
            total_candidates=len(candidates),
            accepted_count=len(accepted),
            rejected_count=len(rejected),
            tracked_count=len(tracked),
            accepted_winners=len(acc_winners),
            accepted_losers=len(acc_losers),
            accepted_win_rate=len(acc_winners) / len(acc_tracked) * 100 if acc_tracked else 0,
            accepted_avg_return=statistics.mean(acc_returns) if acc_returns else 0,
            rejected_with_outcome=len(rej_tracked),
            rejected_winners=len(rej_winners),
            rejected_losers=len(rej_losers),
            rejected_winner_pct=len(rej_winners) / len(rej_tracked) * 100 if rej_tracked else 0,
            rejected_avg_return=statistics.mean(rej_returns) if rej_returns else 0,
            filter_stages=self._compute_filter_effectiveness(candidates),
            component_marginals=self._compute_component_marginals(candidates),
            confidence_calibration=self._compute_confidence_calibration(candidates),
            scorecard=self._compute_scorecard(trades, candidates, rolling_25, rolling_50, rolling_100),
            rolling_25=rolling_25,
            rolling_50=rolling_50,
            rolling_100=rolling_100,
        )
        
        logger.info(
            "Candidate Repository: {} total, {} accepted, {} rejected, {} tracked",
            summary.total_candidates, summary.accepted_count,
            summary.rejected_count, summary.tracked_count,
        )
        return summary
    
    # ── Serialization ─────────────────────────────────────────────────────
    
    @staticmethod
    def to_dict(summary: RepositorySummary) -> Dict[str, Any]:
        """Convert summary to JSON-serializable dict."""
        def _filter_to_dict(f: FilterEffectiveness) -> Dict[str, Any]:
            return {
                "stage": f.stage,
                "total_rejected": f.total_rejected,
                "rejected_winners": f.rejected_winners,
                "rejected_losers": f.rejected_losers,
                "rejected_unknown": f.rejected_unknown,
                "winner_rate": round(f.winner_rate, 2),
                "avg_winner_return": round(f.avg_winner_return, 4),
                "avg_loser_return": round(f.avg_loser_return, 4),
                "total_opportunity_cost": round(f.total_opportunity_cost, 4),
            }
        
        def _rolling_to_dict(r: RollingWindow) -> Dict[str, Any]:
            return {
                "window_size": r.window_size,
                "trade_num": r.trade_num,
                "win_rate": round(r.win_rate, 4),
                "profit_factor": round(r.profit_factor, 4) if r.profit_factor != float("inf") else 999.99,
                "expectancy": round(r.expectancy, 4),
                "avg_r": round(r.avg_r, 4),
                "total_pnl": round(r.total_pnl, 2),
            }
        
        def _marginal_to_dict(m: ComponentMarginal) -> Dict[str, Any]:
            return {
                "component": m.component,
                "bucket": m.bucket,
                "candidates": m.candidates,
                "winners": m.winners,
                "losers": m.losers,
                "win_rate": round(m.win_rate, 2),
                "avg_return": round(m.avg_return, 4),
                "avg_rr": round(m.avg_rr, 4),
                "profit_factor": round(m.profit_factor, 4) if m.profit_factor != float("inf") else 999.99,
            }
        
        def _scorecard_to_dict(s: ProductionScorecard) -> Dict[str, Any]:
            return {
                "total_trades": s.total_trades,
                "profit_factor": round(s.profit_factor, 4) if s.profit_factor != float("inf") else 999.99,
                "expectancy": round(s.expectancy, 4),
                "expectancy_r": round(s.expectancy_r, 4),
                "avg_r": round(s.avg_r, 4),
                "win_rate": round(s.win_rate, 2),
                "avg_mae": round(s.avg_mae, 4),
                "avg_mfe": round(s.avg_mfe, 4),
                "largest_drawdown": round(s.largest_drawdown, 2),
                "best_symbol": s.best_symbol,
                "worst_symbol": s.worst_symbol,
                "best_session": s.best_session,
                "worst_session": s.worst_session,
                "rolling_25": _rolling_to_dict(s.rolling_25) if s.rolling_25 else None,
                "rolling_50": _rolling_to_dict(s.rolling_50) if s.rolling_50 else None,
                "rolling_100": _rolling_to_dict(s.rolling_100) if s.rolling_100 else None,
                "accepted_signals": s.accepted_signals,
                "rejected_candidates": s.rejected_candidates,
                "rejected_winners": s.rejected_winners,
                "rejected_losers": s.rejected_losers,
                "rejected_winner_pct": round(s.rejected_winner_pct, 2),
                "rejected_loser_pct": round(s.rejected_loser_pct, 2),
            }
        
        return {
            "overview": {
                "total_candidates": summary.total_candidates,
                "accepted_count": summary.accepted_count,
                "rejected_count": summary.rejected_count,
                "tracked_count": summary.tracked_count,
            },
            "accepted_performance": {
                "winners": summary.accepted_winners,
                "losers": summary.accepted_losers,
                "win_rate": round(summary.accepted_win_rate, 2),
                "avg_return": round(summary.accepted_avg_return, 4),
            },
            "rejected_performance": {
                "with_outcome": summary.rejected_with_outcome,
                "winners": summary.rejected_winners,
                "losers": summary.rejected_losers,
                "winner_pct": round(summary.rejected_winner_pct, 2),
                "avg_return": round(summary.rejected_avg_return, 4),
            },
            "filter_effectiveness": {
                k: _filter_to_dict(v)
                for k, v in sorted(
                    summary.filter_stages.items(),
                    key=lambda x: x[1].total_rejected, reverse=True,
                )
            },
            "component_marginals": {
                k: [_marginal_to_dict(m) for m in v]
                for k, v in summary.component_marginals.items()
            },
            "confidence_calibration": summary.confidence_calibration,
            "scorecard": _scorecard_to_dict(summary.scorecard) if summary.scorecard else {},
            "rolling_25": [_rolling_to_dict(r) for r in summary.rolling_25[-50:]],
            "rolling_50": [_rolling_to_dict(r) for r in summary.rolling_50[-50:]],
            "rolling_100": [_rolling_to_dict(r) for r in summary.rolling_100[-50:]],
        }
    
    def export_json(self, path: Optional[str] = None) -> str:
        """Export full analytics to JSON."""
        summary = self.compute_full_analytics()
        data = self.to_dict(summary)
        
        json_path = path or str(
            Path(self._calibration_db).parent / "bridge" / "candidate_repository.json"
        )
        
        Path(json_path).parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        
        logger.info("Candidate repository exported to {}", json_path)
        return json_path


# ─── Quick Access ────────────────────────────────────────────────────────────

def get_repository_summary() -> Dict[str, Any]:
    """Quick function to get candidate repository summary."""
    repo = CandidateRepository()
    summary = repo.compute_full_analytics()
    return repo.to_dict(summary)


if __name__ == "__main__":
    repo = CandidateRepository()
    summary = repo.compute_full_analytics()
    data = repo.to_dict(summary)
    
    print("\n=== Candidate Repository Summary ===")
    print(f"Total candidates: {data['overview']['total_candidates']}")
    print(f"Accepted: {data['overview']['accepted_count']}")
    print(f"Rejected: {data['overview']['rejected_count']}")
    print(f"Tracked: {data['overview']['tracked_count']}")
    
    print(f"\n=== Rejected Candidates (What Almost Happened) ===")
    rp = data["rejected_performance"]
    print(f"With outcome: {rp['with_outcome']}")
    print(f"Would-be winners: {rp['winners']} ({rp['winner_pct']:.1f}%)")
    print(f"Would-be losers: {rp['losers']} ({100 - rp['winner_pct']:.1f}%)")
    
    print(f"\n=== Filter Effectiveness ===")
    for stage, eff in data["filter_effectiveness"].items():
        print(f"  {stage}: rejected={eff['total_rejected']}, "
              f"winners={eff['rejected_winners']} ({eff['winner_rate']:.1f}%), "
              f"opportunity_cost={eff['total_opportunity_cost']:.2f}%")
    
    if data.get("scorecard"):
        sc = data["scorecard"]
        print(f"\n=== Production Scorecard ===")
        print(f"Profit Factor: {sc['profit_factor']:.2f}")
        print(f"Expectancy: ${sc['expectancy']:.2f}")
        print(f"Expectancy R: {sc['expectancy_r']:.2f}R")
        print(f"Win Rate: {sc['win_rate']:.1f}%")
        print(f"Best Symbol: {sc['best_symbol']}")
        print(f"Worst Symbol: {sc['worst_symbol']}")
    
    path = repo.export_json()
    print(f"\nExported to: {path}")
