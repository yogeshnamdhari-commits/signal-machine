"""
Statistical Validation Engine
==============================
Comprehensive statistical validation for trading configurations.

Computes:
- Sample Size, Win Rate, Profit Factor, Expectancy
- Average R, Median R, Maximum Drawdown, Recovery Factor
- Sharpe, Sortino, Ulcer Index, Kelly Fraction
- Confidence Interval, Rolling Expectancy, Rolling Profit Factor
- Rolling Win Rate, Bootstrap Confidence, Monte Carlo Stability
- Parameter Stability, Cross Validation, Walk Forward Validation
- Out-of-Sample Validation, Overfitting Score, Regime Stability
- Configuration Drift

READ-ONLY — Never modifies trading logic.
"""
from __future__ import annotations

import math
import random
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


@dataclass
class StatisticalMetrics:
    """Complete statistical metrics for a configuration."""
    # Core metrics
    sample_size: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    
    # R-multiples
    average_r: float = 0.0
    median_r: float = 0.0
    
    # Risk metrics
    max_drawdown: float = 0.0
    recovery_factor: float = 0.0
    ulcer_index: float = 0.0
    kelly_fraction: float = 0.0
    
    # Risk-adjusted returns
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    
    # Confidence
    confidence_interval_lower: float = 0.0
    confidence_interval_upper: float = 0.0
    confidence_level: float = 0.95
    
    # Rolling metrics
    rolling_expectancy: List[float] = field(default_factory=list)
    rolling_profit_factor: List[float] = field(default_factory=list)
    rolling_win_rate: List[float] = field(default_factory=list)
    
    # Advanced validation
    bootstrap_confidence: float = 0.0
    monte_carlo_stability: float = 0.0
    parameter_stability: float = 0.0
    cross_validation_score: float = 0.0
    walk_forward_score: float = 0.0
    out_of_sample_score: float = 0.0
    overfitting_score: float = 0.0
    
    # Regime stability
    regime_stability: Dict[str, float] = field(default_factory=dict)
    
    # Drift detection
    configuration_drift: float = 0.0
    drift_detected: bool = False


class StatisticalValidationEngine:
    """
    Comprehensive statistical validation engine.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"
    
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or self.DB_PATH
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_trades(self, strategy_filter: Optional[str] = None) -> List[Dict]:
        """Fetch all closed trades."""
        conn = self._connect()
        try:
            query = """
                SELECT id, signal_id, symbol, side, entry_price, quantity, leverage,
                       stop_loss, take_profit, pnl, fees, status, opened_at, closed_at,
                       take_profit_2, take_profit_3, exit_reason, strategy_version,
                       confidence, regime, institutional_score, risk_reward,
                       hold_minutes, session, mfe_pct, mae_pct, realized_r,
                       planned_rr, volatility_score, highest_pnl
                FROM positions WHERE status = 'closed'
            """
            params = []
            if strategy_filter:
                query += " AND strategy_version = ?"
                params.append(strategy_filter)
            query += " ORDER BY closed_at ASC"
            
            cur = conn.cursor()
            cur.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]
            
            # Also get from archive
            query2 = query.replace("FROM positions", "FROM positions_archive")
            cur.execute(query2, params)
            rows.extend([dict(r) for r in cur.fetchall()])
            
            return rows
        finally:
            conn.close()
    
    def compute_metrics(self, trades: List[Dict]) -> StatisticalMetrics:
        """Compute all statistical metrics for a set of trades."""
        if not trades:
            return StatisticalMetrics()
        
        pnls = [t.get("pnl", 0) or 0 for t in trades]
        n = len(pnls)
        
        if n == 0:
            return StatisticalMetrics()
        
        # Core metrics
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / n * 100 if n else 0
        
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
            float('inf') if gross_profit > 0 else 0
        )
        
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        expectancy = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss)
        
        # R-multiples
        r_multiples = [t.get("realized_r", 0) for t in trades
                       if t.get("realized_r") is not None and t["realized_r"] != 0]
        average_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0
        median_r = sorted(r_multiples)[len(r_multiples) // 2] if r_multiples else 0
        
        # Max Drawdown
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        dd_durations = []
        current_dd_start = 0
        in_drawdown = False
        
        for i, p in enumerate(pnls):
            cum += p
            if cum > peak:
                if in_drawdown:
                    dd_durations.append(i - current_dd_start)
                    in_drawdown = False
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
                if not in_drawdown:
                    current_dd_start = i
                    in_drawdown = True
        
        # Recovery Factor
        total_pnl = sum(pnls)
        recovery_factor = total_pnl / max_dd if max_dd > 0 else (
            float('inf') if total_pnl > 0 else 0
        )
        
        # Sharpe Ratio (annualized)
        if n > 1:
            mean_pnl = sum(pnls) / n
            std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1))
            sharpe = (mean_pnl / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0
        else:
            sharpe = 0
        
        # Sortino Ratio
        downside = [p for p in pnls if p < 0]
        if len(downside) > 1:
            mean_pnl = sum(pnls) / n
            downside_std = math.sqrt(sum(p ** 2 for p in downside) / len(downside))
            sortino = (mean_pnl / downside_std) * math.sqrt(252) if downside_std > 0 else 0
        else:
            sortino = 0
        
        # Ulcer Index
        cum = 0.0
        peak = 0.0
        dd_squared_sum = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            dd = (peak - cum) / peak * 100 if peak > 0 else 0
            dd_squared_sum += dd ** 2
        ulcer_index = math.sqrt(dd_squared_sum / n) if n > 0 else 0
        
        # Kelly Fraction
        if avg_loss > 0 and win_rate > 0:
            kelly = (win_rate / 100 * avg_win - (1 - win_rate / 100) * avg_loss) / avg_win
            kelly_fraction = max(0, min(kelly, 1))
        else:
            kelly_fraction = 0
        
        # Confidence Interval (95%)
        if n > 1:
            mean_pnl = sum(pnls) / n
            std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1))
            se = std_pnl / math.sqrt(n)
            ci_lower = mean_pnl - 1.96 * se
            ci_upper = mean_pnl + 1.96 * se
        else:
            ci_lower = 0
            ci_upper = 0
        
        # Rolling metrics
        rolling_window = min(20, n // 2) if n > 10 else 5
        rolling_exp = self._compute_rolling_expectancy(pnls, rolling_window)
        rolling_pf = self._compute_rolling_profit_factor(pnls, rolling_window)
        rolling_wr = self._compute_rolling_win_rate(pnls, rolling_window)
        
        # Bootstrap confidence
        bootstrap_conf = self._compute_bootstrap_confidence(pnls, n_iterations=1000)
        
        # Monte Carlo stability
        mc_stability = self._compute_monte_carlo_stability(pnls, n_simulations=500)
        
        # Overfitting score
        overfitting_score = self._compute_overfitting_score(trades)
        
        # Regime stability
        regime_stability = self._compute_regime_stability(trades)
        
        # Configuration drift
        drift = self._compute_configuration_drift(pnls)
        
        return StatisticalMetrics(
            sample_size=n,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 2),
            expectancy=round(expectancy, 4),
            average_r=round(average_r, 2),
            median_r=round(median_r, 2),
            max_drawdown=round(max_dd, 2),
            recovery_factor=round(recovery_factor, 2),
            ulcer_index=round(ulcer_index, 2),
            kelly_fraction=round(kelly_fraction, 4),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            confidence_interval_lower=round(ci_lower, 4),
            confidence_interval_upper=round(ci_upper, 4),
            confidence_level=0.95,
            rolling_expectancy=rolling_exp,
            rolling_profit_factor=rolling_pf,
            rolling_win_rate=rolling_wr,
            bootstrap_confidence=round(bootstrap_conf, 4),
            monte_carlo_stability=round(mc_stability, 4),
            parameter_stability=round(self._compute_parameter_stability(trades), 4),
            cross_validation_score=round(self._compute_cross_validation(pnls), 4),
            walk_forward_score=round(self._compute_walk_forward(pnls), 4),
            out_of_sample_score=round(self._compute_out_of_sample(pnls), 4),
            overfitting_score=round(overfitting_score, 4),
            regime_stability=regime_stability,
            configuration_drift=round(drift, 4),
            drift_detected=drift > 0.5,
        )
    
    def _compute_rolling_expectancy(self, pnls: List[float], window: int) -> List[float]:
        """Compute rolling expectancy."""
        if len(pnls) < window:
            return []
        
        results = []
        for i in range(window, len(pnls) + 1):
            window_pnls = pnls[i - window:i]
            n = len(window_pnls)
            wins = [p for p in window_pnls if p > 0]
            losses = [abs(p) for p in window_pnls if p < 0]
            
            wr = len(wins) / n * 100 if n else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)
            results.append(round(exp, 4))
        
        return results
    
    def _compute_rolling_profit_factor(self, pnls: List[float], window: int) -> List[float]:
        """Compute rolling profit factor."""
        if len(pnls) < window:
            return []
        
        results = []
        for i in range(window, len(pnls) + 1):
            window_pnls = pnls[i - window:i]
            gp = sum(p for p in window_pnls if p > 0)
            gl = sum(abs(p) for p in window_pnls if p < 0)
            pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
            results.append(round(min(pf, 10), 2))  # Cap at 10 for display
        
        return results
    
    def _compute_rolling_win_rate(self, pnls: List[float], window: int) -> List[float]:
        """Compute rolling win rate."""
        if len(pnls) < window:
            return []
        
        results = []
        for i in range(window, len(pnls) + 1):
            window_pnls = pnls[i - window:i]
            n = len(window_pnls)
            wins = sum(1 for p in window_pnls if p > 0)
            wr = wins / n * 100 if n else 0
            results.append(round(wr, 1))
        
        return results
    
    def _compute_bootstrap_confidence(self, pnls: List[float], n_iterations: int = 1000) -> float:
        """Compute bootstrap confidence interval."""
        if len(pnls) < 10:
            return 0.0
        
        n = len(pnls)
        means = []
        
        for _ in range(n_iterations):
            sample = random.choices(pnls, k=n)
            means.append(sum(sample) / n)
        
        # Calculate confidence that mean is positive
        positive_count = sum(1 for m in means if m > 0)
        return positive_count / n_iterations
    
    def _compute_monte_carlo_stability(self, pnls: List[float], n_simulations: int = 500) -> float:
        """Compute Monte Carlo stability score."""
        if len(pnls) < 20:
            return 0.0
        
        n = len(pnls)
        results = []
        
        for _ in range(n_simulations):
            # Shuffle and compute metrics
            shuffled = pnls.copy()
            random.shuffle(shuffled)
            
            # Compute profit factor for shuffled
            gp = sum(p for p in shuffled if p > 0)
            gl = sum(abs(p) for p in shuffled if p < 0)
            pf = gp / gl if gl > 0 else 0
            results.append(pf)
        
        # Stability = consistency of profit factor
        if not results:
            return 0.0
        
        mean_pf = sum(results) / len(results)
        std_pf = math.sqrt(sum((x - mean_pf) ** 2 for x in results) / len(results))
        cv = std_pf / mean_pf if mean_pf > 0 else 1
        
        # Lower CV = higher stability
        return max(0, 1 - cv)
    
    def _compute_parameter_stability(self, trades: List[Dict]) -> float:
        """Compute parameter stability across different conditions."""
        if len(trades) < 20:
            return 0.0
        
        # Split into halves and compare
        mid = len(trades) // 2
        first_half = trades[:mid]
        second_half = trades[mid:]
        
        def calc_pf(ts):
            pnls = [t.get("pnl", 0) or 0 for t in ts]
            gp = sum(p for p in pnls if p > 0)
            gl = sum(abs(p) for p in pnls if p < 0)
            return gp / gl if gl > 0 else 0
        
        pf1 = calc_pf(first_half)
        pf2 = calc_pf(second_half)
        
        # Stability = how similar the two halves are
        if pf1 == 0 and pf2 == 0:
            return 1.0
        
        diff = abs(pf1 - pf2) / max(pf1, pf2) if max(pf1, pf2) > 0 else 1
        return max(0, 1 - diff)
    
    def _compute_cross_validation(self, pnls: List[float], n_folds: int = 5) -> float:
        """Compute cross-validation score."""
        if len(pnls) < n_folds * 5:
            return 0.0
        
        fold_size = len(pnls) // n_folds
        scores = []
        
        for i in range(n_folds):
            start = i * fold_size
            end = start + fold_size
            fold_pnls = pnls[start:end]
            
            n = len(fold_pnls)
            wins = sum(1 for p in fold_pnls if p > 0)
            wr = wins / n * 100 if n else 0
            
            gp = sum(p for p in fold_pnls if p > 0)
            gl = sum(abs(p) for p in fold_pnls if p < 0)
            pf = gp / gl if gl > 0 else 0
            
            # Score = combination of WR and PF
            score = (wr / 100) * min(pf, 3) / 3
            scores.append(score)
        
        return sum(scores) / len(scores) if scores else 0
    
    def _compute_walk_forward(self, pnls: List[float], train_pct: float = 0.7) -> float:
        """Compute walk-forward validation score."""
        if len(pnls) < 20:
            return 0.0
        
        split = int(len(pnls) * train_pct)
        train = pnls[:split]
        test = pnls[split:]
        
        def calc_exp(pnls_list):
            n = len(pnls_list)
            wins = [p for p in pnls_list if p > 0]
            losses = [abs(p) for p in pnls_list if p < 0]
            wr = len(wins) / n * 100 if n else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            return (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)
        
        train_exp = calc_exp(train)
        test_exp = calc_exp(test)
        
        # Walk-forward score = how well test matches train
        if train_exp == 0:
            return 0.0
        
        ratio = test_exp / train_exp if train_exp > 0 else 0
        return min(max(ratio, 0), 1)
    
    def _compute_out_of_sample(self, pnls: List[float]) -> float:
        """Compute out-of-sample validation score."""
        # Use last 20% as out-of-sample
        if len(pnls) < 20:
            return 0.0
        
        oos_start = int(len(pnls) * 0.8)
        oos_pnls = pnls[oos_start:]
        
        n = len(oos_pnls)
        wins = sum(1 for p in oos_pnls if p > 0)
        wr = wins / n * 100 if n else 0
        
        gp = sum(p for p in oos_pnls if p > 0)
        gl = sum(abs(p) for p in oos_pnls if p < 0)
        pf = gp / gl if gl > 0 else 0
        
        # Score based on profitability
        return min(pf / 2, 1) if pf > 0 else 0
    
    def _compute_overfitting_score(self, trades: List[Dict]) -> float:
        """Compute overfitting score (0 = no overfitting, 1 = severe overfitting)."""
        if len(trades) < 20:
            return 0.5  # Insufficient data
        
        # Split into multiple periods and check consistency
        n_periods = 4
        period_size = len(trades) // n_periods
        
        pfs = []
        for i in range(n_periods):
            start = i * period_size
            end = start + period_size
            period_trades = trades[start:end]
            
            pnls = [t.get("pnl", 0) or 0 for t in period_trades]
            gp = sum(p for p in pnls if p > 0)
            gl = sum(abs(p) for p in pnls if p < 0)
            pf = gp / gl if gl > 0 else 0
            pfs.append(pf)
        
        # Overfitting = high variance in profit factor
        if not pfs:
            return 0.5
        
        mean_pf = sum(pfs) / len(pfs)
        std_pf = math.sqrt(sum((x - mean_pf) ** 2 for x in pfs) / len(pfs))
        cv = std_pf / mean_pf if mean_pf > 0 else 1
        
        return min(cv, 1)
    
    def _compute_regime_stability(self, trades: List[Dict]) -> Dict[str, float]:
        """Compute stability across different regimes."""
        regimes = defaultdict(list)
        for t in trades:
            regime = t.get("regime", "unknown") or "unknown"
            regimes[regime].append(t)
        
        stability = {}
        for regime, regime_trades in regimes.items():
            if len(regime_trades) < 3:
                stability[regime] = 0.0
                continue
            
            pnls = [t.get("pnl", 0) or 0 for t in regime_trades]
            gp = sum(p for p in pnls if p > 0)
            gl = sum(abs(p) for p in pnls if p < 0)
            pf = gp / gl if gl > 0 else 0
            
            # Normalize to 0-1
            stability[regime] = round(min(pf / 3, 1), 2)
        
        return stability
    
    def _compute_configuration_drift(self, pnls: List[float]) -> float:
        """Compute configuration drift over time."""
        if len(pnls) < 20:
            return 0.0
        
        # Compare first half to second half
        mid = len(pnls) // 2
        first = pnls[:mid]
        second = pnls[mid:]
        
        def calc_metrics(pnls_list):
            n = len(pnls_list)
            wins = [p for p in pnls_list if p > 0]
            losses = [abs(p) for p in pnls_list if p < 0]
            wr = len(wins) / n * 100 if n else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0
            return wr, avg_win, avg_loss
        
        wr1, aw1, al1 = calc_metrics(first)
        wr2, aw2, al2 = calc_metrics(second)
        
        # Drift = change in key metrics
        wr_drift = abs(wr1 - wr2) / max(wr1, 1)
        aw_drift = abs(aw1 - aw2) / max(aw1, 0.001)
        al_drift = abs(al1 - al2) / max(al1, 0.001)
        
        return (wr_drift + aw_drift + al_drift) / 3
    
    def generate_report(self, trades: List[Dict]) -> str:
        """Generate a comprehensive statistical validation report."""
        metrics = self.compute_metrics(trades)
        
        lines = []
        lines.append("=" * 80)
        lines.append("📊 STATISTICAL VALIDATION REPORT")
        lines.append("=" * 80)
        
        lines.append(f"\n📈 CORE METRICS:")
        lines.append(f"   Sample Size: {metrics.sample_size}")
        lines.append(f"   Win Rate: {metrics.win_rate:.1f}%")
        lines.append(f"   Profit Factor: {metrics.profit_factor:.2f}")
        lines.append(f"   Expectancy: {metrics.expectancy:.4f}")
        lines.append(f"   Average R: {metrics.average_r:.2f}")
        lines.append(f"   Median R: {metrics.median_r:.2f}")
        
        lines.append(f"\n📉 RISK METRICS:")
        lines.append(f"   Max Drawdown: ${metrics.max_drawdown:.2f}")
        lines.append(f"   Recovery Factor: {metrics.recovery_factor:.2f}")
        lines.append(f"   Ulcer Index: {metrics.ulcer_index:.2f}")
        lines.append(f"   Kelly Fraction: {metrics.kelly_fraction:.4f}")
        
        lines.append(f"\n📊 RISK-ADJUSTED RETURNS:")
        lines.append(f"   Sharpe Ratio: {metrics.sharpe_ratio:.2f}")
        lines.append(f"   Sortino Ratio: {metrics.sortino_ratio:.2f}")
        
        lines.append(f"\n🎯 CONFIDENCE:")
        lines.append(f"   95% CI: [{metrics.confidence_interval_lower:.4f}, {metrics.confidence_interval_upper:.4f}]")
        lines.append(f"   Bootstrap Confidence: {metrics.bootstrap_confidence:.4f}")
        
        lines.append(f"\n🔬 VALIDATION:")
        lines.append(f"   Monte Carlo Stability: {metrics.monte_carlo_stability:.4f}")
        lines.append(f"   Parameter Stability: {metrics.parameter_stability:.4f}")
        lines.append(f"   Cross-Validation: {metrics.cross_validation_score:.4f}")
        lines.append(f"   Walk-Forward: {metrics.walk_forward_score:.4f}")
        lines.append(f"   Out-of-Sample: {metrics.out_of_sample_score:.4f}")
        lines.append(f"   Overfitting Score: {metrics.overfitting_score:.4f}")
        
        if metrics.regime_stability:
            lines.append(f"\n🌍 REGIME STABILITY:")
            for regime, score in sorted(metrics.regime_stability.items()):
                lines.append(f"   {regime}: {score:.2f}")
        
        lines.append(f"\n📉 DRIFT DETECTION:")
        lines.append(f"   Configuration Drift: {metrics.configuration_drift:.4f}")
        lines.append(f"   Drift Detected: {'⚠️ YES' if metrics.drift_detected else '✅ NO'}")
        
        lines.append("\n" + "=" * 80)
        return "\n".join(lines)


# Global singleton
_engine: Optional[StatisticalValidationEngine] = None

def get_statistical_engine() -> StatisticalValidationEngine:
    """Get or create the global statistical validation engine."""
    global _engine
    if _engine is None:
        _engine = StatisticalValidationEngine()
    return _engine
