"""
Portfolio Intelligence Engine — Evaluate capital allocation at portfolio level.

Per Executive Assessment v15:
    "Instead of:
         Signal A Good → Execute
     evaluate:
         Signal A + Current Portfolio + Correlation + Sector Exposure
         + Volatility Budget → Execute?
     This is where a lot of professional risk management gains come from."

Key Innovation:
    v20 evaluated: Trade-level expected value
    v21 evaluates: Portfolio-level capital allocation

    This allows:
        - Considering correlation between positions
        - Managing sector exposure
        - Budgeting volatility across portfolio
        - Rejecting trades that increase portfolio risk
        - Optimizing capital allocation across opportunities

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


# Sector definitions (simplified)
SECTOR_MAP = {
    "L1": {"BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "ADAUSDT", "DOTUSDT",
            "NEARUSDT", "APTUSDT", "SUIUSDT"},
    "L2": {"ARBUSDT", "OPUSDT", "MATICUSDT"},
    "DEFI": {"UNIUSDT", "AAVEUSDT", "LINKUSDT", "CRVUSDT", "MKRUSDT"},
    "MEME": {"DOGEUSDT", "SHIBUSDT", "1000PEPEUSDT", "WIFUSDT"},
    "AI": {"FETUSDT", "RENDERUSDT", "TAOUSDT", "GRTUSDT"},
    "EXCHANGE": {"BNBUSDT"},
}

# Reverse mapping
SYMBOL_TO_SECTOR = {}
for sector, symbols in SECTOR_MAP.items():
    for sym in symbols:
        SYMBOL_TO_SECTOR[sym] = sector


@dataclass
class TradeProposal:
    """A proposed trade for portfolio evaluation."""
    symbol: str = ""
    side: str = ""
    expected_r: float = 0.0
    expected_return_pct: float = 0.0  # Expected return as % of position
    uncertainty: float = 0.0    # Prediction uncertainty (std dev of R)
    sector: str = ""
    correlation_to_portfolio: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "expected_r": round(self.expected_r, 3),
            "expected_return_pct": round(self.expected_return_pct, 3),
            "uncertainty": round(self.uncertainty, 3),
            "sector": self.sector,
            "correlation": round(self.correlation_to_portfolio, 3),
        }


@dataclass
class PortfolioDecision:
    """Decision for a single trade proposal."""
    symbol: str = ""
    side: str = ""
    action: str = ""           # EXECUTE / REDUCE / REJECT
    capital_allocation_pct: float = 0.0  # % of risk budget
    position_size_usd: float = 0.0
    reasoning: str = ""
    risk_contribution: float = 0.0  # Contribution to portfolio risk

    # Adjustments
    correlation_penalty: float = 0.0
    sector_penalty: float = 0.0
    uncertainty_penalty: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "action": self.action,
            "capital_pct": round(self.capital_allocation_pct, 1),
            "position_usd": round(self.position_usd, 2) if hasattr(self, 'position_usd') else 0,
            "reasoning": self.reasoning,
            "risk_contribution": round(self.risk_contribution, 3),
            "penalties": {
                "correlation": round(self.correlation_penalty, 3),
                "sector": round(self.sector_penalty, 3),
                "uncertainty": round(self.uncertainty_penalty, 3),
            },
        }


@dataclass
class PortfolioIntelligenceReport:
    """Complete portfolio intelligence analysis."""
    timestamp: float = 0.0
    balance: float = 0.0

    # Current portfolio state
    current_positions: int = 0
    current_exposure_pct: float = 0.0
    current_sector_exposure: Dict[str, float] = field(default_factory=dict)
    current_volatility_budget_used: float = 0.0

    # Proposals evaluated
    proposals: List[PortfolioDecision] = field(default_factory=list)
    executed: int = 0
    reduced: int = 0
    rejected: int = 0

    # Portfolio metrics
    expected_portfolio_return: float = 0.0
    expected_portfolio_risk: float = 0.0
    portfolio_sharpe: float = 0.0
    diversification_score: float = 0.0  # 0-100

    # Recommendations
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "balance": round(self.balance, 2),
            "portfolio_state": {
                "positions": self.current_positions,
                "exposure_pct": round(self.current_exposure_pct, 1),
                "sector_exposure": {k: round(v, 1) for k, v in self.current_sector_exposure.items()},
                "volatility_budget_used": round(self.current_volatility_budget_used, 1),
            },
            "proposals": {
                "total": len(self.proposals),
                "executed": self.executed,
                "reduced": self.reduced,
                "rejected": self.rejected,
            },
            "portfolio_metrics": {
                "expected_return": round(self.expected_portfolio_return, 3),
                "expected_risk": round(self.expected_portfolio_risk, 3),
                "sharpe": round(self.portfolio_sharpe, 3),
                "diversification": round(self.diversification_score, 1),
            },
            "decisions": [p.to_dict() for p in self.proposals],
            "recommendations": self.recommendations,
        }


class PortfolioIntelligenceEngine:
    """
    Evaluates capital allocation at portfolio level.

    Per Executive Assessment v15:
        "Evaluate Signal A + Current Portfolio + Correlation
         + Sector Exposure + Volatility Budget → Execute?"

    This engine:
        1. Analyzes current portfolio state
        2. Evaluates each trade proposal in portfolio context
        3. Applies correlation, sector, and uncertainty penalties
        4. Allocates capital based on portfolio-optimal sizing
        5. Recommends portfolio-level adjustments

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0
        self._correlations: Dict[Tuple[str, str], float] = {}

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
                SELECT symbol, side, realized_r
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
            logger.warning("Could not load portfolio intelligence engine: {}", e)

    def _compute_correlations(self) -> None:
        """Compute correlation matrix."""
        by_symbol = defaultdict(list)
        for t in self._trades:
            by_symbol[t.get("symbol", "")].append(t.get("realized_r", 0) or 0)

        symbols = list(by_symbol.keys())
        for i, s1 in enumerate(symbols):
            for s2 in symbols[i+1:]:
                corr = self._calc_correlation(by_symbol[s1], by_symbol[s2])
                self._correlations[(s1, s2)] = corr
                self._correlations[(s2, s1)] = corr

    def _calc_correlation(self, x: List[float], y: List[float]) -> float:
        """Calculate correlation between two series."""
        n = min(len(x), len(y))
        if n < 10:
            return 0.3
        x, y = x[:n], y[:n]
        mean_x, mean_y = sum(x) / n, sum(y) / n
        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)
        return cov / (std_x * std_y) if std_x > 0 and std_y > 0 else 0.0

    def evaluate(
        self,
        proposals: List[TradeProposal],
        open_positions: List[Dict],
        balance: float = 10_000.0,
        risk_budget_pct: float = 5.0,
    ) -> PortfolioIntelligenceReport:
        """
        Evaluate trade proposals in portfolio context.

        Args:
            proposals: List of proposed trades
            open_positions: Current open positions
            balance: Account balance
            risk_budget_pct: Maximum portfolio risk as % of balance

        Returns:
            PortfolioIntelligenceReport with decisions
        """
        self._ensure_loaded()

        report = PortfolioIntelligenceReport(
            timestamp=time.time(),
            balance=balance,
        )

        # ── Analyze current portfolio ──
        report.current_positions = len(open_positions)
        total_exposure = sum(abs(p.get("position_value", 0) or 0) for p in open_positions)
        report.current_exposure_pct = (total_exposure / max(1, balance)) * 100

        # Sector exposure
        sector_exposure = defaultdict(float)
        for p in open_positions:
            sym = p.get("symbol", "")
            sector = SYMBOL_TO_SECTOR.get(sym, "OTHER")
            sector_exposure[sector] += abs(p.get("position_value", 0) or 0)
        report.current_sector_exposure = dict(sector_exposure)

        # Volatility budget used
        report.current_volatility_budget_used = min(100, report.current_exposure_pct * 2)

        # ── Evaluate each proposal ──
        for prop in proposals:
            decision = self._evaluate_proposal(
                prop, open_positions, balance, risk_budget_pct,
                report.current_sector_exposure,
            )
            report.proposals.append(decision)

            if decision.action == "EXECUTE":
                report.executed += 1
            elif decision.action == "REDUCE":
                report.reduced += 1
            else:
                report.rejected += 1

        # ── Portfolio metrics ──
        if report.proposals:
            report.expected_portfolio_return = sum(
                p.capital_allocation_pct * p.risk_contribution / 100
                for p in report.proposals if p.action != "REJECT"
            )
            report.portfolio_sharpe = (
                report.expected_portfolio_return / max(0.01, report.expected_portfolio_risk)
                if report.expected_portfolio_risk > 0 else 0
            )

        # ── Diversification score ──
        report.diversification_score = self._calc_diversification_score(
            report.current_sector_exposure
        )

        # ── Recommendations ──
        report.recommendations = self._generate_recommendations(report)

        return report

    def _evaluate_proposal(
        self,
        proposal: TradeProposal,
        open_positions: List[Dict],
        balance: float,
        risk_budget_pct: float,
        sector_exposure: Dict[str, float],
    ) -> PortfolioDecision:
        """Evaluate a single trade proposal."""
        decision = PortfolioDecision(
            symbol=proposal.symbol,
            side=proposal.side,
        )

        # ── Base allocation ──
        base_allocation = risk_budget_pct / max(1, len(open_positions) + 1)

        # ── Correlation penalty ──
        max_corr = 0
        for p in open_positions:
            sym = p.get("symbol", "")
            corr = self._correlations.get((proposal.symbol, sym), 0.3)
            max_corr = max(max_corr, abs(corr))

        decision.correlation_penalty = max_corr * 0.3  # Up to 30% reduction

        # ── Sector penalty ──
        sector = SYMBOL_TO_SECTOR.get(proposal.symbol, "OTHER")
        sector_value = sector_exposure.get(sector, 0)
        total_exposure = sum(sector_exposure.values()) if sector_exposure else 1
        sector_pct = (sector_value / max(1, total_exposure)) * 100

        if sector_pct > 40:
            decision.sector_penalty = 0.3  # Heavy penalty
        elif sector_pct > 25:
            decision.sector_penalty = 0.15
        else:
            decision.sector_penalty = 0

        # ── Uncertainty penalty ──
        if proposal.uncertainty > 2.0:
            decision.uncertainty_penalty = 0.2
        elif proposal.uncertainty > 1.5:
            decision.uncertainty_penalty = 0.1
        else:
            decision.uncertainty_penalty = 0

        # ── Risk-adjusted allocation ──
        total_penalty = decision.correlation_penalty + decision.sector_penalty + decision.uncertainty_penalty
        adjusted_allocation = base_allocation * (1 - min(0.8, total_penalty))

        # ── Final decision ──
        if proposal.expected_r <= 0:
            decision.action = "REJECT"
            decision.reasoning = f"Negative expected R ({proposal.expected_r:.3f})"
        elif total_penalty > 0.5:
            decision.action = "REDUCE"
            decision.capital_allocation_pct = adjusted_allocation
            decision.reasoning = f"High portfolio risk (penalty={total_penalty:.2f})"
        else:
            decision.action = "EXECUTE"
            decision.capital_allocation_pct = adjusted_allocation
            decision.reasoning = f"Acceptable risk (penalty={total_penalty:.2f})"

        decision.risk_contribution = proposal.expected_r * (adjusted_allocation / 100)

        return decision

    def _calc_diversification_score(self, sector_exposure: Dict[str, float]) -> float:
        """Calculate diversification score (0-100)."""
        if not sector_exposure:
            return 100.0

        total = sum(sector_exposure.values())
        if total <= 0:
            return 100.0

        # Herfindahl index (lower = more diversified)
        hhi = sum((v / total) ** 2 for v in sector_exposure.values())
        # Convert to 0-100 score
        return max(0, min(100, (1 - hhi) * 100))

    def _generate_recommendations(self, report: PortfolioIntelligenceReport) -> List[str]:
        """Generate portfolio recommendations."""
        recs = []

        if report.current_exposure_pct > 80:
            recs.append("Portfolio exposure is high — reduce before adding new positions")

        if report.diversification_score < 50:
            recs.append("Portfolio is concentrated — diversify across sectors")

        if report.rejected > report.executed:
            recs.append("Most proposals rejected — market conditions may be unfavorable")

        if report.current_volatility_budget_used > 70:
            recs.append("Volatility budget nearly used — limit new positions")

        return recs
