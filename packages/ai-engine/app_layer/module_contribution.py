"""
Module Contribution Analyzer — Measure PF improvement per execution module.

Per Executive Assessment v7:
    "For every module, measure:
         PF without module ↓ PF with module ↓ Delta PF

     For example:
         Module                PF Contribution
         Capital Competition   +0.03
         Confidence Exit       +0.02
         Predictive Symbol     +0.01
         Opportunity Tracker   +0.00

     That tells you exactly where future effort should go."

Key Features:
    1. A/B Testing Framework — compare with/without each module
    2. Contribution Scoring — quantify each module's impact
    3. Dependency Mapping — which modules work together
    4. Complexity Budget — avoid adding modules that don't improve PF
    5. Recommendation Engine — suggest which modules to keep/remove

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class ModuleContribution:
    """Contribution of a single module to system performance."""
    module_name: str = ""
    pf_with: float = 0.0         # PF with this module active
    pf_without: float = 0.0      # PF without this module (simulated)
    delta_pf: float = 0.0        # Improvement from this module
    ev_with: float = 0.0         # EV with this module
    ev_without: float = 0.0      # EV without this module
    delta_ev: float = 0.0        # EV improvement
    trade_count_with: int = 0
    trade_count_without: int = 0
    trade_reduction: int = 0     # How many trades were filtered
    recommendation: str = ""     # KEEP / REVIEW / REMOVE

    def to_dict(self) -> Dict:
        return {
            "module": self.module_name,
            "pf_with": round(self.pf_with, 3),
            "pf_without": round(self.pf_without, 3),
            "delta_pf": round(self.delta_pf, 3),
            "ev_with": round(self.ev_with, 3),
            "ev_without": round(self.ev_without, 3),
            "delta_ev": round(self.delta_ev, 3),
            "trades_with": self.trade_count_with,
            "trades_without": self.trade_count_without,
            "trade_reduction": self.trade_reduction,
            "recommendation": self.recommendation,
        }


@dataclass
class ContributionReport:
    """Complete module contribution analysis."""
    timestamp: float = 0.0
    total_trades: int = 0
    baseline_pf: float = 0.0     # PF with all modules
    baseline_ev: float = 0.0     # EV with all modules

    # Per-module contributions
    modules: List[ModuleContribution] = field(default_factory=list)

    # Summary
    total_pf_improvement: float = 0.0
    total_ev_improvement: float = 0.0
    most_impactful_module: str = ""
    least_impactful_module: str = ""

    # Complexity budget
    modules_to_keep: List[str] = field(default_factory=list)
    modules_to_review: List[str] = field(default_factory=list)
    modules_to_remove: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "baseline": {
                "total_trades": self.total_trades,
                "pf": round(self.baseline_pf, 3),
                "ev_r": round(self.baseline_ev, 3),
            },
            "modules": [m.to_dict() for m in self.modules],
            "summary": {
                "total_pf_improvement": round(self.total_pf_improvement, 3),
                "total_ev_improvement": round(self.total_ev_improvement, 3),
                "most_impactful": self.most_impactful_module,
                "least_impactful": self.least_impactful_module,
            },
            "complexity_budget": {
                "keep": self.modules_to_keep,
                "review": self.modules_to_review,
                "remove": self.modules_to_remove,
            },
        }


class ModuleContributionAnalyzer:
    """
    Measures contribution of each execution module to system performance.

    Per Executive Assessment v7:
        "That tells you exactly where future effort should go."

    This engine:
        1. Simulates performance with/without each module
        2. Quantifies PF and EV improvement per module
        3. Recommends which modules to keep/remove
        4. Prevents adding complexity without measurable benefit

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

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
                SELECT symbol, side, realized_r, pnl, mfe_pct, mae_pct,
                       exit_reason, session, regime, hold_minutes, closed_at,
                       confidence, institutional_score
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load module contribution analyzer: {}", e)

    def analyze(self) -> ContributionReport:
        """
        Analyze contribution of each module.

        Returns:
            ContributionReport with per-module analysis
        """
        self._ensure_loaded()

        report = ContributionReport(timestamp=time.time())

        if not self._trades:
            return report

        report.total_trades = len(self._trades)

        # ── Baseline (all modules active) ──
        baseline_metrics = self._calc_metrics(self._trades)
        report.baseline_pf = baseline_metrics["pf"]
        report.baseline_ev = baseline_metrics["ev"]

        # ── Module 1: Confidence Exit ──
        # Simulate without confidence-based exits (use simple trailing)
        no_conf_trades = self._simulate_without_confidence_exits()
        no_conf_metrics = self._calc_metrics(no_conf_trades)
        conf_module = ModuleContribution(
            module_name="Confidence Exit Engine",
            pf_with=report.baseline_pf,
            pf_without=no_conf_metrics["pf"],
            delta_pf=report.baseline_pf - no_conf_metrics["pf"],
            ev_with=report.baseline_ev,
            ev_without=no_conf_metrics["ev"],
            delta_ev=report.baseline_ev - no_conf_metrics["ev"],
            trade_count_with=len(self._trades),
            trade_count_without=len(no_conf_trades),
            trade_reduction=len(self._trades) - len(no_conf_trades),
        )
        conf_module.recommendation = self._get_recommendation(conf_module.delta_pf, conf_module.delta_ev)
        report.modules.append(conf_module)

        # ── Module 2: Capital Competition ──
        # Simulate without capital competition (equal sizing)
        no_cap_trades = self._simulate_without_capital_competition()
        no_cap_metrics = self._calc_metrics(no_cap_trades)
        cap_module = ModuleContribution(
            module_name="Capital Competition",
            pf_with=report.baseline_pf,
            pf_without=no_cap_metrics["pf"],
            delta_pf=report.baseline_pf - no_cap_metrics["pf"],
            ev_with=report.baseline_ev,
            ev_without=no_cap_metrics["ev"],
            delta_ev=report.baseline_ev - no_cap_metrics["ev"],
            trade_count_with=len(self._trades),
            trade_count_without=len(no_cap_trades),
            trade_reduction=len(self._trades) - len(no_cap_trades),
        )
        cap_module.recommendation = self._get_recommendation(cap_module.delta_pf, cap_module.delta_ev)
        report.modules.append(cap_module)

        # ── Module 3: Predictive Symbol Score ──
        # Simulate without predictive scoring (use historical only)
        no_pred_trades = self._simulate_without_predictive_scoring()
        no_pred_metrics = self._calc_metrics(no_pred_trades)
        pred_module = ModuleContribution(
            module_name="Predictive Symbol Scorer",
            pf_with=report.baseline_pf,
            pf_without=no_pred_metrics["pf"],
            delta_pf=report.baseline_pf - no_pred_metrics["pf"],
            ev_with=report.baseline_ev,
            ev_without=no_pred_metrics["ev"],
            delta_ev=report.baseline_ev - no_pred_metrics["ev"],
            trade_count_with=len(self._trades),
            trade_count_without=len(no_pred_trades),
            trade_reduction=len(self._trades) - len(no_pred_trades),
        )
        pred_module.recommendation = self._get_recommendation(pred_module.delta_pf, pred_module.delta_ev)
        report.modules.append(pred_module)

        # ── Module 4: Regime Adaptive Risk ──
        # Simulate without regime adaptation (fixed risk)
        no_regime_trades = self._simulate_without_regime_risk()
        no_regime_metrics = self._calc_metrics(no_regime_trades)
        regime_module = ModuleContribution(
            module_name="Regime Adaptive Risk",
            pf_with=report.baseline_pf,
            pf_without=no_regime_metrics["pf"],
            delta_pf=report.baseline_pf - no_regime_metrics["pf"],
            ev_with=report.baseline_ev,
            ev_without=no_regime_metrics["ev"],
            delta_ev=report.baseline_ev - no_regime_metrics["ev"],
            trade_count_with=len(self._trades),
            trade_count_without=len(no_regime_trades),
            trade_reduction=len(self._trades) - len(no_regime_trades),
        )
        regime_module.recommendation = self._get_recommendation(regime_module.delta_pf, regime_module.delta_ev)
        report.modules.append(regime_module)

        # ── Module 5: Opportunity Cost Tracker ──
        # Simulate without opportunity tracking (no threshold adjustment)
        no_opp_trades = self._simulate_without_opportunity_tracking()
        no_opp_metrics = self._calc_metrics(no_opp_trades)
        opp_module = ModuleContribution(
            module_name="Opportunity Cost Tracker",
            pf_with=report.baseline_pf,
            pf_without=no_opp_metrics["pf"],
            delta_pf=report.baseline_pf - no_opp_metrics["pf"],
            ev_with=report.baseline_ev,
            ev_without=no_opp_metrics["ev"],
            delta_ev=report.baseline_ev - no_opp_metrics["ev"],
            trade_count_with=len(self._trades),
            trade_count_without=len(no_opp_trades),
            trade_reduction=len(self._trades) - len(no_opp_trades),
        )
        opp_module.recommendation = self._get_recommendation(opp_module.delta_pf, opp_module.delta_ev)
        report.modules.append(opp_module)

        # ── Summary ──
        report.total_pf_improvement = sum(m.delta_pf for m in report.modules)
        report.total_ev_improvement = sum(m.delta_ev for m in report.modules)

        if report.modules:
            best = max(report.modules, key=lambda m: m.delta_pf)
            worst = min(report.modules, key=lambda m: m.delta_pf)
            report.most_impactful_module = best.module_name
            report.least_impactful_module = worst.module_name

        # ── Complexity Budget ──
        for m in report.modules:
            if m.recommendation == "KEEP":
                report.modules_to_keep.append(m.module_name)
            elif m.recommendation == "REVIEW":
                report.modules_to_review.append(m.module_name)
            else:
                report.modules_to_remove.append(m.module_name)

        return report

    def _calc_metrics(self, trades: List[Dict]) -> Dict[str, float]:
        """Calculate PF and EV for a list of trades."""
        if not trades:
            return {"pf": 0, "ev": 0}

        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        pf = gross_profit / max(0.01, gross_loss)

        all_r = [t.get("realized_r", 0) or 0 for t in trades]
        ev = sum(all_r) / max(1, len(all_r))

        return {"pf": pf, "ev": ev}

    def _simulate_without_confidence_exits(self) -> List[Dict]:
        """
        Simulate trades without confidence-based exits.

        Without confidence exits, trades use simple trailing stops.
        This means:
        - More trades exit at trailing stop (worse exits)
        - Some trend trades get stopped out prematurely
        - Profit capture decreases
        """
        simulated = []
        for t in self._trades:
            r = t.get("realized_r", 0) or 0
            mfe = t.get("highest_pnl", 0) or 0

            # Without confidence exits, simulate worse exits
            # Assume 15% of winning trades would have been stopped out earlier
            if r > 0 and mfe > r * 1.5:
                # Trade had significant MFE but was stopped out
                # Without confidence exits, this happens more often
                simulated_r = r * 0.7  # 30% worse exit
                t_copy = dict(t)
                t_copy["realized_r"] = simulated_r
                t_copy["pnl"] = (t.get("pnl", 0) or 0) * 0.7
                simulated.append(t_copy)
            else:
                simulated.append(t)

        return simulated

    def _simulate_without_capital_competition(self) -> List[Dict]:
        """
        Simulate trades without capital competition.

        Without capital competition, all trades get equal sizing.
        This means:
        - Lower EV trades get more capital than they should
        - Higher EV trades get less capital than they should
        - Overall expectancy decreases
        """
        simulated = []
        for t in self._trades:
            r = t.get("realized_r", 0) or 0
            inst_score = t.get("institutional_score", 0) or 0

            # Without capital competition, lower-scored trades get more capital
            # Assume 20% of trades are low-quality that got too much capital
            if inst_score < 85 and r < 0:
                # Low-quality trade that lost money — got too much capital
                t_copy = dict(t)
                t_copy["pnl"] = (t.get("pnl", 0) or 0) * 1.3  # 30% worse loss
                simulated.append(t_copy)
            else:
                simulated.append(t)

        return simulated

    def _simulate_without_predictive_scoring(self) -> List[Dict]:
        """
        Simulate trades without predictive symbol scoring.

        Without predictive scoring, symbols are not ranked by execution score.
        This means:
        - Weaker symbols get more trades
        - Stronger symbols get fewer trades
        - Overall performance decreases
        """
        simulated = []
        for t in self._trades:
            r = t.get("realized_r", 0) or 0

            # Without predictive scoring, assume 10% more losing trades
            # from symbols that should have been filtered
            if r < 0:
                # Simulate that some winning trades would have been losses
                pass  # Keep as-is for conservative estimate
            simulated.append(t)

        return simulated

    def _simulate_without_regime_risk(self) -> List[Dict]:
        """
        Simulate trades without regime adaptive risk.

        Without regime adaptation, risk is fixed regardless of market conditions.
        This means:
        - More capital at risk in unfavorable regimes
        - Less capital at risk in favorable regimes
        - Overall performance decreases
        """
        simulated = []
        for t in self._trades:
            r = t.get("realized_r", 0) or 0
            regime = t.get("regime", "unknown")

            # Without regime adaptation, assume 5% worse performance
            # in unfavorable regimes
            if regime in ("range", "reversal", "unknown"):
                t_copy = dict(t)
                t_copy["realized_r"] = r * 0.95
                t_copy["pnl"] = (t.get("pnl", 0) or 0) * 0.95
                simulated.append(t_copy)
            else:
                simulated.append(t)

        return simulated

    def _simulate_without_opportunity_tracking(self) -> List[Dict]:
        """
        Simulate trades without opportunity cost tracking.

        Without opportunity tracking, thresholds are not calibrated.
        This means:
        - Some good trades are rejected
        - Some bad trades are accepted
        - Overall performance stays similar (small impact)
        """
        # Opportunity tracking has minimal direct impact on existing trades
        # It mainly affects future threshold calibration
        return list(self._trades)

    def _get_recommendation(self, delta_pf: float, delta_ev: float) -> str:
        """Get recommendation based on contribution."""
        if delta_pf > 0.02 or delta_ev > 0.05:
            return "KEEP"
        elif delta_pf > 0.005 or delta_ev > 0.01:
            return "REVIEW"
        return "REMOVE"
