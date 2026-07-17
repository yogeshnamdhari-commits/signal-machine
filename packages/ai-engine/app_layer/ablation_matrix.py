"""
Ablation Matrix Analyzer — Measure joint contribution of modules.

Per Executive Assessment v8:
    "The framework measures direct effects only.
     It does not measure:
         - interaction effects
         - dependency effects
         - reinforcement between modules

     Example:
         Module A + Module B + Module C → PF 0.95
         Remove A → PF 0.94 (looks insignificant)
         Remove A+B+C → PF 0.82 (huge difference)

     That means the modules are complementary.
     The validation framework should estimate joint contribution,
     not just isolated contribution."

Key Innovation:
    v12 measured: PF with module - PF without module (direct effect)
    v13 measures: SHAP-style marginal contribution across all combinations

    This identifies:
        - Which modules work together (synergy)
        - Which modules are redundant (overlap)
        - Which modules are essential (critical)
        - Joint contribution of all modules

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import itertools
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# Module definitions — each module has a simulation function
MODULES = [
    "confidence_exit",
    "capital_competition",
    "predictive_scorer",
    "regime_risk",
    "opportunity_tracker",
]


@dataclass
class AblationResult:
    """Result of a single ablation test."""
    enabled_modules: Set[str] = field(default_factory=set)
    pf: float = 0.0
    ev_r: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "modules": sorted(self.enabled_modules),
            "pf": round(self.pf, 3),
            "ev_r": round(self.ev_r, 3),
            "trades": self.trade_count,
            "win_rate": round(self.win_rate, 3),
        }


@dataclass
class ModuleMarginalContribution:
    """SHAP-style marginal contribution for a single module."""
    module_name: str = ""
    marginal_pf: float = 0.0     # Average improvement across all combinations
    marginal_ev: float = 0.0     # Average EV improvement across all combinations
    importance_rank: int = 0     # Rank by importance (1 = most important)
    is_synergistic: bool = False # Works better with other modules
    is_redundant: bool = False   # Overlaps with other modules
    is_critical: bool = False    # System fails without this module
    synergy_pairs: List[str] = field(default_factory=list)  # Modules it synergizes with

    def to_dict(self) -> Dict:
        return {
            "module": self.module_name,
            "marginal_pf": round(self.marginal_pf, 4),
            "marginal_ev": round(self.marginal_ev, 4),
            "importance_rank": self.importance_rank,
            "is_synergistic": self.is_synergistic,
            "is_redundant": self.is_redundant,
            "is_critical": self.is_critical,
            "synergy_pairs": self.synergy_pairs,
        }


@dataclass
class AblationMatrixReport:
    """Complete ablation matrix analysis."""
    timestamp: float = 0.0

    # Test results
    total_combinations: int = 0
    results: List[AblationResult] = field(default_factory=list)

    # Marginal contributions
    marginal_contributions: List[ModuleMarginalContribution] = field(default_factory=list)

    # Key findings
    baseline_pf: float = 0.0         # PF with all modules
    baseline_ev: float = 0.0         # EV with all modules
    no_modules_pf: float = 0.0       # PF with no modules
    no_modules_ev: float = 0.0       # EV with no modules
    joint_contribution_pf: float = 0.0  # Total improvement from all modules
    joint_contribution_ev: float = 0.0

    # Synergy analysis
    synergistic_pairs: List[Dict] = field(default_factory=list)
    redundant_pairs: List[Dict] = field(default_factory=list)
    critical_modules: List[str] = field(default_factory=list)

    # Recommendations
    optimal_subset: List[str] = field(default_factory=list)
    modules_to_remove: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "summary": {
                "total_combinations": self.total_combinations,
                "baseline_pf": round(self.baseline_pf, 3),
                "baseline_ev": round(self.baseline_ev, 3),
                "no_modules_pf": round(self.no_modules_pf, 3),
                "no_modules_ev": round(self.no_modules_ev, 3),
                "joint_contribution_pf": round(self.joint_contribution_pf, 3),
                "joint_contribution_ev": round(self.joint_contribution_ev, 3),
            },
            "marginal_contributions": [m.to_dict() for m in self.marginal_contributions],
            "synergistic_pairs": self.synergistic_pairs,
            "redundant_pairs": self.redundant_pairs,
            "critical_modules": self.critical_modules,
            "recommendations": {
                "optimal_subset": self.optimal_subset,
                "modules_to_remove": self.modules_to_remove,
            },
        }


class AblationMatrixAnalyzer:
    """
    Measures joint contribution of modules using SHAP-style analysis.

    Per Executive Assessment v8:
        "The validation framework should estimate joint contribution,
         not just isolated contribution."

    This engine:
        1. Tests all meaningful module combinations (2^N where feasible)
        2. Calculates marginal contribution for each module
        3. Identifies synergistic pairs (work better together)
        4. Identifies redundant pairs (overlap heavily)
        5. Identifies critical modules (system fails without them)
        6. Recommends optimal module subset

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
            logger.warning("Could not load ablation matrix analyzer: {}", e)

    def analyze(self, max_combinations: int = 32) -> AblationMatrixReport:
        """
        Run full ablation matrix analysis.

        Args:
            max_combinations: Maximum number of combinations to test
                             (2^5 = 32 is manageable)

        Returns:
            AblationMatrixReport with complete analysis
        """
        self._ensure_loaded()

        report = AblationMatrixReport(timestamp=time.time())

        if not self._trades:
            return report

        # ── Generate all combinations ──
        all_combinations = self._generate_combinations(max_combinations)
        report.total_combinations = len(all_combinations)

        # ── Test each combination ──
        for combo in all_combinations:
            result = self._test_combination(combo)
            report.results.append(result)

        # ── Calculate baseline and no-modules ──
        all_modules = set(MODULES)
        no_modules = set()

        for r in report.results:
            if r.enabled_modules == all_modules:
                report.baseline_pf = r.pf
                report.baseline_ev = r.ev_r
            elif r.enabled_modules == no_modules:
                report.no_modules_pf = r.pf
                report.no_modules_ev = r.ev_r

        report.joint_contribution_pf = report.baseline_pf - report.no_modules_pf
        report.joint_contribution_ev = report.baseline_ev - report.no_modules_ev

        # ── Calculate marginal contributions ──
        report.marginal_contributions = self._calc_marginal_contributions(report.results)

        # ── Identify synergies and redundancies ──
        report.synergistic_pairs = self._find_synergistic_pairs(report.results)
        report.redundant_pairs = self._find_redundant_pairs(report.results)
        report.critical_modules = self._find_critical_modules(report.results)

        # ── Recommend optimal subset ──
        report.optimal_subset = self._find_optimal_subset(report.results)
        report.modules_to_remove = self._find_removable_modules(report.marginal_contributions)

        return report

    def _generate_combinations(self, max_combinations: int) -> List[Set[str]]:
        """Generate all meaningful module combinations."""
        combinations = []

        # Generate all subsets up to max_combinations
        for r in range(len(MODULES) + 1):
            for combo in itertools.combinations(MODULES, r):
                combinations.append(set(combo))
                if len(combinations) >= max_combinations:
                    break
            if len(combinations) >= max_combinations:
                break

        return combinations

    def _test_combination(self, enabled_modules: Set[str]) -> AblationResult:
        """Test performance with a specific set of modules enabled."""
        result = AblationResult(enabled_modules=enabled_modules)

        if not self._trades:
            return result

        # Simulate trades with this combination of modules
        simulated = self._simulate_trades(enabled_modules)

        if not simulated:
            return result

        # Calculate metrics
        wins = [t.get("realized_r", 0) or 0 for t in simulated if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in simulated if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        result.pf = gross_profit / max(0.01, gross_loss)

        all_r = [t.get("realized_r", 0) or 0 for t in simulated]
        result.ev_r = sum(all_r) / max(1, len(all_r))
        result.trade_count = len(simulated)
        result.win_rate = len(wins) / max(1, len(simulated))

        return result

    def _simulate_trades(self, enabled_modules: Set[str]) -> List[Dict]:
        """
        Simulate trades with specific modules enabled.

        Each module affects trades differently:
        - confidence_exit: improves exits for trend trades
        - capital_competition: sizes positions by EV
        - predictive_scorer: filters weak symbols
        - regime_risk: adjusts risk by regime
        - opportunity_tracker: calibrates thresholds
        """
        simulated = []

        for t in self._trades:
            r = t.get("realized_r", 0) or 0
            mfe = t.get("highest_pnl", 0) or 0
            inst_score = t.get("institutional_score", 0) or 0
            regime = t.get("regime", "unknown")

            # Start with base trade
            adj_r = r
            adj_pnl = t.get("pnl", 0) or 0

            # Module effects (compounding)
            if "confidence_exit" in enabled_modules:
                # Confidence exits improve profit capture
                if r > 0 and mfe > r * 1.5:
                    adj_r = r * 1.15  # 15% better exit
                    adj_pnl = adj_pnl * 1.15

            if "capital_competition" in enabled_modules:
                # Capital competition sizes by EV — better for high-quality trades
                if inst_score >= 90:
                    adj_r = adj_r * 1.10  # 10% more capital on best trades
                    adj_pnl = adj_pnl * 1.10
                elif inst_score < 80:
                    adj_r = adj_r * 0.80  # 20% less capital on weak trades
                    adj_pnl = adj_pnl * 0.80

            if "predictive_scorer" in enabled_modules:
                # Predictive scorer filters weak symbols
                if inst_score < 75:
                    # Skip this trade entirely
                    continue

            if "regime_risk" in enabled_modules:
                # Regime risk adjusts by market condition
                if regime in ("range", "reversal", "unknown"):
                    adj_r = adj_r * 0.90  # 10% less risk in weak regimes
                    adj_pnl = adj_pnl * 0.90
                elif regime in ("trending_bull", "BUY_MODE"):
                    adj_r = adj_r * 1.05  # 5% more risk in strong regimes
                    adj_pnl = adj_pnl * 1.05

            if "opportunity_tracker" in enabled_modules:
                # Opportunity tracker has minimal direct effect on existing trades
                pass

            # Create adjusted trade
            t_copy = dict(t)
            t_copy["realized_r"] = adj_r
            t_copy["pnl"] = adj_pnl
            simulated.append(t_copy)

        return simulated

    def _calc_marginal_contributions(
        self,
        results: List[AblationResult],
    ) -> List[ModuleMarginalContribution]:
        """
        Calculate SHAP-style marginal contribution for each module.

        Marginal contribution = Average improvement when module is added
        across all possible subsets.
        """
        contributions = []

        for module in MODULES:
            # Find all results where this module is enabled vs disabled
            with_module = [r for r in results if module in r.enabled_modules]
            without_module = [r for r in results if module not in r.enabled_modules]

            if not with_module or not without_module:
                continue

            # For each subset without this module, find the matching subset with it
            marginal_pfs = []
            marginal_evs = []

            for without in without_module:
                # Find the matching subset with this module added
                with_added = without.enabled_modules | {module}
                for with_m in with_module:
                    if with_m.enabled_modules == with_added:
                        marginal_pfs.append(with_m.pf - without.pf)
                        marginal_evs.append(with_m.ev_r - without.ev_r)
                        break

            if marginal_pfs:
                avg_marginal_pf = sum(marginal_pfs) / len(marginal_pfs)
                avg_marginal_ev = sum(marginal_evs) / len(marginal_evs)
            else:
                avg_marginal_pf = 0
                avg_marginal_ev = 0

            contributions.append(ModuleMarginalContribution(
                module_name=module,
                marginal_pf=avg_marginal_pf,
                marginal_ev=avg_marginal_ev,
            ))

        # Rank by importance
        contributions.sort(key=lambda c: c.marginal_pf, reverse=True)
        for i, c in enumerate(contributions):
            c.importance_rank = i + 1

        # Identify synergies, redundancies, critical modules
        self._classify_modules(contributions, results)

        return contributions

    def _classify_modules(
        self,
        contributions: List[ModuleMarginalContribution],
        results: List[AblationResult],
    ) -> None:
        """Classify modules as synergistic, redundant, or critical."""
        for contrib in contributions:
            module = contrib.module_name

            # Check if critical (system fails without it)
            without = [r for r in results if module not in r.enabled_modules]
            with_all = [r for r in results if len(r.enabled_modules) == len(MODULES)]

            if without and with_all:
                max_without = max(r.pf for r in without) if without else 0
                baseline = with_all[0].pf if with_all else 0
                if baseline - max_without > 0.05:  # >5% PF drop without this module
                    contrib.is_critical = True

            # Check synergies (works better with specific other modules)
            for other in MODULES:
                if other == module:
                    continue

                # PF with both modules
                with_both = [r for r in results if module in r.enabled_modules and other in r.enabled_modules]
                # PF with only this module
                with_only_this = [r for r in results if module in r.enabled_modules and other not in r.enabled_modules]
                # PF with only other module
                with_only_other = [r for r in results if module not in r.enabled_modules and other in r.enabled_modules]

                if with_both and with_only_this and with_only_other:
                    pf_both = sum(r.pf for r in with_both) / len(with_both)
                    pf_this = sum(r.pf for r in with_only_this) / len(with_only_this)
                    pf_other = sum(r.pf for r in with_only_other) / len(with_only_other)

                    # Synergy: together > sum of parts
                    if pf_both > pf_this + pf_other - 0.01:
                        contrib.is_synergistic = True
                        contrib.synergy_pairs.append(other)

            # Check redundancy (removing this has minimal effect)
            if abs(contrib.marginal_pf) < 0.005:
                contrib.is_redundant = True

    def _find_synergistic_pairs(self, results: List[AblationResult]) -> List[Dict]:
        """Find pairs of modules that work better together."""
        pairs = []

        for i, m1 in enumerate(MODULES):
            for m2 in MODULES[i+1:]:
                # PF with both
                with_both = [r for r in results if m1 in r.enabled_modules and m2 in r.enabled_modules]
                # PF with only m1
                with_m1 = [r for r in results if m1 in r.enabled_modules and m2 not in r.enabled_modules]
                # PF with only m2
                with_m2 = [r for r in results if m1 not in r.enabled_modules and m2 in r.enabled_modules]

                if with_both and with_m1 and with_m2:
                    pf_both = sum(r.pf for r in with_both) / len(with_both)
                    pf_m1 = sum(r.pf for r in with_m1) / len(with_m1)
                    pf_m2 = sum(r.pf for r in with_m2) / len(with_m2)

                    synergy = pf_both - (pf_m1 + pf_m2) / 2
                    if synergy > 0.01:
                        pairs.append({
                            "module_1": m1,
                            "module_2": m2,
                            "synergy_pf": round(synergy, 4),
                        })

        return sorted(pairs, key=lambda p: p["synergy_pf"], reverse=True)

    def _find_redundant_pairs(self, results: List[AblationResult]) -> List[Dict]:
        """Find pairs of modules that overlap heavily."""
        pairs = []

        for i, m1 in enumerate(MODULES):
            for m2 in MODULES[i+1:]:
                # PF with both
                with_both = [r for r in results if m1 in r.enabled_modules and m2 in r.enabled_modules]
                # PF with only m1
                with_m1 = [r for r in results if m1 in r.enabled_modules and m2 not in r.enabled_modules]
                # PF with only m2
                with_m2 = [r for r in results if m1 not in r.enabled_modules and m2 in r.enabled_modules]

                if with_both and with_m1 and with_m2:
                    pf_both = sum(r.pf for r in with_both) / len(with_both)
                    pf_m1 = sum(r.pf for r in with_m1) / len(with_m1)
                    pf_m2 = sum(r.pf for r in with_m2) / len(with_m2)

                    # Redundancy: removing one has minimal effect when other is present
                    redundancy = pf_both - max(pf_m1, pf_m2)
                    if abs(redundancy) < 0.005:
                        pairs.append({
                            "module_1": m1,
                            "module_2": m2,
                            "redundancy_pf": round(redundancy, 4),
                        })

        return pairs

    def _find_critical_modules(self, results: List[AblationResult]) -> List[str]:
        """Find modules that are critical (system fails without them)."""
        critical = []
        all_modules = set(MODULES)

        for module in MODULES:
            without = [r for r in results if module not in r.enabled_modules]
            with_all = [r for r in results if r.enabled_modules == all_modules]

            if without and with_all:
                max_without = max(r.pf for r in without)
                baseline = with_all[0].pf
                if baseline - max_without > 0.05:
                    critical.append(module)

        return critical

    def _find_optimal_subset(self, results: List[AblationResult]) -> List[str]:
        """Find the optimal subset of modules."""
        if not results:
            return []

        # Find the combination with highest PF
        best = max(results, key=lambda r: r.pf)
        return sorted(best.enabled_modules)

    def _find_removable_modules(
        self,
        contributions: List[ModuleMarginalContribution],
    ) -> List[str]:
        """Find modules that can be removed without significant PF loss."""
        removable = []
        for c in contributions:
            if c.marginal_pf < 0.005 and not c.is_critical:
                removable.append(c.module_name)
        return removable
