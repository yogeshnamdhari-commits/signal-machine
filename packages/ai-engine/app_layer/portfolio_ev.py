"""
Portfolio EV Calculator — Predict portfolio-level outcomes, not just trade-level.

Per Executive Assessment v12:
    "The architecture predicts Trade → Outcome.
     It still does not predict Portfolio → Outcome.
     Those are different problems.

     Example:
         Trade A: Positive EV
         Trade B: Positive EV
         Trade C: Positive EV
         Together: Negative portfolio.
         Why? Correlation.

     The dashboard should compute:
         Portfolio Expected Return
         Portfolio Expected Risk
         Portfolio Variance
         Expected Drawdown
         Probability of Daily Loss"

Key Innovation:
    v16 predicted: Individual trade outcomes
    v17 predicts: Portfolio-level outcomes considering correlation

    This allows:
        - Better position sizing based on portfolio risk
        - Correlation-aware capital allocation
        - More accurate risk budgeting
        - Earlier detection of concentration risk

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
class PositionEV:
    """Expected value for a single position."""
    symbol: str = ""
    side: str = ""
    weight: float = 0.0           # Portfolio weight (0-1)
    expected_r: float = 0.0       # Expected R-multiple
    expected_pnl: float = 0.0     # Expected PnL in USD
    variance_r: float = 0.0       # Variance of R
    risk_contribution: float = 0.0  # Contribution to portfolio risk

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "weight": round(self.weight, 3),
            "expected_r": round(self.expected_r, 3),
            "expected_pnl": round(self.expected_pnl, 2),
            "variance": round(self.variance_r, 4),
            "risk_contribution": round(self.risk_contribution, 3),
        }


@dataclass
class PortfolioMetrics:
    """Portfolio-level metrics."""
    # Expected return
    expected_return_r: float = 0.0    # Portfolio expected R
    expected_return_usd: float = 0.0  # Portfolio expected PnL in USD

    # Risk
    portfolio_variance: float = 0.0
    portfolio_std_dev: float = 0.0    # Portfolio volatility
    portfolio_var_95: float = 0.0     # Value at Risk (95%)
    portfolio_var_99: float = 0.0     # Value at Risk (99%)

    # Diversification
    correlation_penalty: float = 0.0  # Risk increase from correlation
    diversification_benefit: float = 0.0  # Risk reduction from diversification
    effective_positions: float = 0.0  # Equivalent undiversified positions

    # Drawdown
    expected_max_drawdown: float = 0.0
    prob_daily_loss: float = 0.0     # Probability of daily loss > threshold

    # Efficiency
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "expected_return": {
                "r": round(self.expected_return_r, 3),
                "usd": round(self.expected_return_usd, 2),
            },
            "risk": {
                "variance": round(self.portfolio_variance, 4),
                "std_dev": round(self.portfolio_std_dev, 3),
                "var_95": round(self.portfolio_var_95, 3),
                "var_99": round(self.portfolio_var_99, 3),
            },
            "diversification": {
                "correlation_penalty": round(self.correlation_penalty, 3),
                "benefit": round(self.diversification_benefit, 3),
                "effective_positions": round(self.effective_positions, 1),
            },
            "drawdown": {
                "expected_max": round(self.expected_max_drawdown, 3),
                "prob_daily_loss": round(self.prob_daily_loss, 3),
            },
            "efficiency": {
                "sharpe": round(self.sharpe_ratio, 3),
                "sortino": round(self.sortino_ratio, 3),
                "calmar": round(self.calmar_ratio, 3),
            },
        }


@dataclass
class PortfolioEVReport:
    """Complete portfolio EV analysis."""
    timestamp: float = 0.0
    balance: float = 0.0
    positions: List[PositionEV] = field(default_factory=list)
    metrics: PortfolioMetrics = field(default_factory=PortfolioMetrics)

    # Recommendations
    recommendations: List[str] = field(default_factory=list)
    risk_budget_remaining: float = 0.0
    should_reduce_exposure: bool = False

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "balance": round(self.balance, 2),
            "positions": [p.to_dict() for p in self.positions],
            "metrics": self.metrics.to_dict(),
            "recommendations": self.recommendations,
            "risk_budget_remaining": round(self.risk_budget_remaining, 1),
            "should_reduce_exposure": self.should_reduce_exposure,
        }


class PortfolioEVCalculator:
    """
    Calculates portfolio-level expected value and risk.

    Per Executive Assessment v12:
        "The dashboard should compute Portfolio Expected Return,
         Portfolio Expected Risk, Portfolio Variance,
         Expected Drawdown, Probability of Daily Loss."

    This engine:
        1. Calculates expected return for each position
        2. Estimates correlation between positions
        3. Computes portfolio variance and VaR
        4. Estimates expected drawdown
        5. Provides risk budget recommendations

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0
        self._correlation_matrix: Dict[Tuple[str, str], float] = {}

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, realized_r, pnl
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._compute_correlations()
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load portfolio EV calculator: {}", e)

    def _compute_correlations(self) -> None:
        """Compute correlation matrix between symbols."""
        # Group R-multiples by symbol
        by_symbol: Dict[str, List[float]] = defaultdict(list)
        for t in self._trades:
            by_symbol[t.get("symbol", "")].append(t.get("realized_r", 0) or 0)

        # Compute pairwise correlations
        symbols = list(by_symbol.keys())
        for i, s1 in enumerate(symbols):
            for s2 in symbols[i+1:]:
                corr = self._calc_correlation(by_symbol[s1], by_symbol[s2])
                self._correlation_matrix[(s1, s2)] = corr
                self._correlation_matrix[(s2, s1)] = corr

    def _calc_correlation(self, x: List[float], y: List[float]) -> float:
        """Calculate correlation between two series."""
        n = min(len(x), len(y))
        if n < 10:
            return 0.3  # Default correlation for insufficient data

        x = x[:n]
        y = y[:n]

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)

        if std_x > 0 and std_y > 0:
            return cov / (std_x * std_y)
        return 0.0

    def calculate(
        self,
        open_positions: List[Dict],
        balance: float = 10_000.0,
    ) -> PortfolioEVReport:
        """
        Calculate portfolio-level expected value and risk.

        Args:
            open_positions: List of open position dicts
            balance: Account balance

        Returns:
            PortfolioEVReport with complete analysis
        """
        self._ensure_loaded()

        report = PortfolioEVReport(
            timestamp=time.time(),
            balance=balance,
        )

        if not open_positions:
            return report

        # ── Calculate position EVs ──
        total_value = sum(abs(p.get("position_value", 0) or 0) for p in open_positions)

        for pos in open_positions:
            sym = pos.get("symbol", "")
            side = pos.get("side", "")
            pos_value = abs(pos.get("position_value", 0) or 0)
            weight = pos_value / max(1, total_value)

            # Get expected R from historical data
            sym_trades = [t for t in self._trades if t.get("symbol") == sym]
            if sym_trades:
                all_r = [t.get("realized_r", 0) or 0 for t in sym_trades[:50]]
                expected_r = sum(all_r) / max(1, len(all_r))
                variance_r = sum((r - expected_r) ** 2 for r in all_r) / max(1, len(all_r))
            else:
                expected_r = 0.0
                variance_r = 1.0  # High variance for unknown

            expected_pnl = expected_r * (pos_value / 10)  # Assume 10:1 leverage

            report.positions.append(PositionEV(
                symbol=sym,
                side=side,
                weight=weight,
                expected_r=expected_r,
                expected_pnl=expected_pnl,
                variance_r=variance_r,
            ))

        # ── Portfolio metrics ──
        if report.positions:
            # Expected return (weighted average)
            report.metrics.expected_return_r = sum(
                p.expected_r * p.weight for p in report.positions
            )
            report.metrics.expected_return_usd = sum(
                p.expected_pnl for p in report.positions
            )

            # Portfolio variance (with correlation)
            n = len(report.positions)
            variance_sum = 0
            for i, p1 in enumerate(report.positions):
                for j, p2 in enumerate(report.positions):
                    if i == j:
                        variance_sum += (p1.weight ** 2) * p1.variance_r
                    else:
                        corr = self._correlation_matrix.get(
                            (p1.symbol, p2.symbol), 0.3
                        )
                        variance_sum += p1.weight * p2.weight * corr * math.sqrt(
                            p1.variance_r * p2.variance_r
                        )

            report.metrics.portfolio_variance = variance_sum
            report.metrics.portfolio_std_dev = math.sqrt(max(0, variance_sum))

            # VaR (95% and 99%)
            report.metrics.portfolio_var_95 = 1.645 * report.metrics.portfolio_std_dev
            report.metrics.portfolio_var_99 = 2.326 * report.metrics.portfolio_std_dev

            # Diversification
            # If all correlations were 0, variance would be lower
            undiversified_var = sum(p.weight ** 2 * p.variance_r for p in report.positions)
            report.metrics.correlation_penalty = variance_sum - undiversified_var
            report.metrics.diversification_benefit = max(0, undiversified_var - variance_sum)

            # Effective number of positions (Herfindahl-like)
            hhi = sum(p.weight ** 2 for p in report.positions)
            report.metrics.effective_positions = 1 / max(0.01, hhi)

            # Expected max drawdown (simplified)
            report.metrics.expected_max_drawdown = 2 * report.metrics.portfolio_std_dev

            # Probability of daily loss > 1%
            if report.metrics.portfolio_std_dev > 0:
                z_score = (0.01 - report.metrics.expected_return_r) / report.metrics.portfolio_std_dev
                report.metrics.prob_daily_loss = self._normal_cdf(-z_score)

            # Sharpe ratio (assuming risk-free rate = 0)
            if report.metrics.portfolio_std_dev > 0:
                report.metrics.sharpe_ratio = report.metrics.expected_return_r / report.metrics.portfolio_std_dev

        # ── Recommendations ──
        report.recommendations = self._generate_recommendations(report)
        report.should_reduce_exposure = any(
            "reduce" in r.lower() for r in report.recommendations
        )

        return report

    def _normal_cdf(self, x: float) -> float:
        """Approximate normal CDF."""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def _generate_recommendations(self, report: PortfolioEVReport) -> List[str]:
        """Generate portfolio recommendations."""
        recs = []

        # Check correlation
        if report.metrics.correlation_penalty > 0.1:
            recs.append("High correlation between positions — consider diversifying")

        # Check concentration
        if report.metrics.effective_positions < 2:
            recs.append("Portfolio is concentrated — reduce single-position risk")

        # Check expected return
        if report.metrics.expected_return_r < 0:
            recs.append("Portfolio has negative expected return — reduce exposure")

        # Check VaR
        if report.metrics.portfolio_var_95 > 0.5:
            recs.append("Portfolio VaR is high — consider reducing position sizes")

        # Check daily loss probability
        if report.metrics.prob_daily_loss > 0.3:
            recs.append("High probability of daily loss — reduce risk")

        return recs
