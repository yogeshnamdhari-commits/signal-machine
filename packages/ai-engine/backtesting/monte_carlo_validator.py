"""
Monte Carlo Robustness Validator — Phase 2
==========================================
Institutional-grade Monte Carlo robustness testing for trading strategies.

Validates that profitability does NOT depend on:
- Trade ordering
- Lucky streaks
- A small subset of trades

Methodology:
- 5000 simulations with randomly reshuffled trade sequences
- Trade outcomes (PnL per trade) remain UNCHANGED
- Only the ORDER of trades changes per simulation

Success Criteria:
- Worst Profit Factor > 1.10
- Worst Drawdown < 10%
- Expected Profit Factor > 1.30
- Expected Return > 0
- Risk of Ruin < 5%
- 95% CI Lower Bound PF > 1.00
"""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


# ─── Data Classes ────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """A single completed trade from trade_log.csv."""
    timestamp: str
    symbol: str
    side: str
    entry: float
    exit_price: float
    pnl: float
    score: float
    confidence: float
    market_regime: str
    hold_time_min: float
    exit_reason: str


@dataclass
class SimResult:
    """Result of a single Monte Carlo simulation."""
    simulation_id: int
    net_profit: float
    profit_factor: float
    win_rate: float
    max_drawdown_pct: float
    longest_losing_streak: int
    longest_winning_streak: int
    sharpe_ratio: float
    return_pct: float
    return_dd_ratio: float
    final_equity: float


@dataclass
class DistributionStats:
    """Statistical summary of a distribution."""
    mean: float
    median: float
    best: float
    worst: float
    std_dev: float
    p5: float
    p25: float
    p75: float
    p95: float


@dataclass
class ConfidenceInterval:
    """95% confidence interval."""
    lower: float
    upper: float


@dataclass
class MonteCarloReport:
    """Complete Monte Carlo robustness report."""
    # Metadata
    n_simulations: int
    n_trades: int
    initial_capital: float
    execution_time_sec: float

    # Profit Factor Distribution
    pf_stats: DistributionStats
    pf_ci_95: ConfidenceInterval

    # Drawdown Distribution
    dd_stats: DistributionStats
    dd_ci_95: ConfidenceInterval

    # Return Distribution
    return_stats: DistributionStats
    return_ci_95: ConfidenceInterval

    # Risk Metrics
    risk_of_ruin_pct: float
    longest_losing_streak: int
    longest_winning_streak: int

    # Sharpe
    sharpe_stats: DistributionStats

    # Edge Stability Score (0-100)
    edge_stability_score: float
    edge_stability_rating: str

    # Pass/Fail
    criteria: Dict[str, Dict[str, Any]]
    overall_pass: bool

    # All simulation results (for export)
    simulations: List[SimResult]

    # Original trade stats
    original_pf: float
    original_wr: float
    original_return: float
    original_dd: float


# ─── Monte Carlo Validator ──────────────────────────────────────

class MonteCarloValidator:
    """
    Institutional-grade Monte Carlo robustness validator.

    Loads actual completed trades, reshuffles their order 5000 times,
    and validates that the strategy remains profitable regardless of
    trade sequencing.

    Usage:
        validator = MonteCarloValidator()
        validator.load_trades("data/reports/trade_log.csv")
        validator.run_simulations(n_simulations=5000)
        report = validator.calculate_metrics()
        validator.generate_report(report)
        validator.export_results(report)
    """

    RUIN_THRESHOLD = 20.0  # 20% drawdown = ruin (stored as 0-100)
    INITIAL_CAPITAL = 10_000.0
    RISK_PER_TRADE = 0.005  # 0.5%

    # Edge Stability Score weights
    W_WORST_PF = 0.40
    W_WORST_DD = 0.30
    W_RISK_OF_RUIN = 0.20
    W_RETURN_CONSISTENCY = 0.10

    def __init__(self, reports_dir: Optional[str] = None, seed: Optional[int] = None):
        """Initialize the Monte Carlo validator."""
        self.trades: List[TradeRecord] = []
        self.simulations: List[SimResult] = []
        self._seed = seed

        # Resolve reports directory
        if reports_dir:
            self._reports_dir = Path(reports_dir)
        else:
            # Default: data/reports relative to ai-engine
            base = Path(__file__).resolve().parent.parent / "data" / "reports"
            self._reports_dir = base
        self._reports_dir.mkdir(parents=True, exist_ok=True)

        logger.info("MonteCarloValidator initialized — reports dir: {}", self._reports_dir)

    # ─── 1. Load Trades ─────────────────────────────────────────

    def load_trades(self, csv_path: str) -> int:
        """
        Load actual completed trades from trade_log.csv.

        Args:
            csv_path: Path to trade_log.csv

        Returns:
            Number of trades loaded

        Raises:
            FileNotFoundError: If CSV doesn't exist
            ValueError: If CSV has no valid trades
        """
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"Trade log not found: {csv_path}")

        self.trades = []
        with open(path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    trade = TradeRecord(
                        timestamp=row.get("timestamp", ""),
                        symbol=row.get("symbol", ""),
                        side=row.get("side", ""),
                        entry=float(row.get("entry", 0)),
                        exit_price=float(row.get("exit", 0)),
                        pnl=float(row.get("pnl", 0)),
                        score=float(row.get("score", 0)),
                        confidence=float(row.get("confidence", 0)),
                        market_regime=row.get("market_regime", ""),
                        hold_time_min=float(row.get("hold_time_min", 0)),
                        exit_reason=row.get("exit_reason", ""),
                    )
                    self.trades.append(trade)
                except (ValueError, KeyError) as e:
                    logger.warning("Skipping malformed trade row: {}", e)
                    continue

        if not self.trades:
            raise ValueError(f"No valid trades found in {csv_path}")

        # Sort by timestamp
        self.trades.sort(key=lambda t: t.timestamp)

        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total_pnl = sum(pnls)
        pf = sum(wins) / abs(sum(losses)) if losses else float("inf")
        wr = len(wins) / len(pnls) * 100

        logger.info(
            "Loaded {} trades — WR={:.1f}%, PF={:.2f}, Net=${:.2f}",
            len(self.trades), wr, pf, total_pnl,
        )
        return len(self.trades)

    # ─── 2. Run Simulations ─────────────────────────────────────

    def run_simulations(self, n_simulations: int = 5000) -> List[SimResult]:
        """
        Run Monte Carlo simulations by reshuffling trade order.

        For each simulation:
        1. Randomly shuffle the sequence of trades
        2. Build equity curve from shuffled PnLs
        3. Calculate all metrics (PF, DD, WR, Sharpe, etc.)

        Trade OUTCOMES are unchanged — only the ORDER changes.

        Args:
            n_simulations: Number of simulations to run (default 5000)

        Returns:
            List of SimResult for each simulation
        """
        if not self.trades:
            raise ValueError("No trades loaded. Call load_trades() first.")

        if self._seed is not None:
            np.random.seed(self._seed)

        pnls = np.array([t.pnl for t in self.trades])
        n_trades = len(pnls)
        self.simulations = []

        t0 = time.time()
        logger.info("Running {} Monte Carlo simulations ({} trades each)...", n_simulations, n_trades)

        for sim_id in range(n_simulations):
            # Randomly reshuffle trade order
            shuffled_pnls = np.random.permutation(pnls)

            # Build equity curve
            equity = np.cumsum(shuffled_pnls) + self.INITIAL_CAPITAL
            equity = np.insert(equity, 0, self.INITIAL_CAPITAL)

            # Calculate metrics for this simulation
            result = self._compute_sim_metrics(sim_id, equity, shuffled_pnls)
            self.simulations.append(result)

            if (sim_id + 1) % 1000 == 0:
                elapsed = time.time() - t0
                logger.info("  {}/{} simulations completed ({:.1f}s)", sim_id + 1, n_simulations, elapsed)

        elapsed = time.time() - t0
        logger.info("All {} simulations completed in {:.2f}s", n_simulations, elapsed)
        return self.simulations

    # ─── 3. Calculate Metrics ────────────────────────────────────

    def calculate_metrics(self) -> MonteCarloReport:
        """
        Calculate comprehensive metrics across all simulations.

        Returns:
            MonteCarloReport with all statistics, distributions, and pass/fail
        """
        if not self.simulations:
            raise ValueError("No simulations run. Call run_simulations() first.")

        n_sims = len(self.simulations)
        n_trades = len(self.trades)
        elapsed = 0.0  # Set externally if needed

        # Extract arrays
        pfs = np.array([s.profit_factor for s in self.simulations])
        dds = np.array([s.max_drawdown_pct for s in self.simulations])
        returns = np.array([s.return_pct for s in self.simulations])
        sharpes = np.array([s.sharpe_ratio for s in self.simulations])
        win_rates = np.array([s.win_rate for s in self.simulations])

        # Handle infinite PFs for stats (cap at 100 for practical purposes)
        pfs_finite = np.where(np.isfinite(pfs), pfs, 100.0)

        # ── Profit Factor Distribution ──────────────────────────
        pf_stats = DistributionStats(
            mean=float(np.mean(pfs_finite)),
            median=float(np.median(pfs_finite)),
            best=float(np.max(pfs_finite)),
            worst=float(np.min(pfs_finite)),
            std_dev=float(np.std(pfs_finite)),
            p5=float(np.percentile(pfs_finite, 5)),
            p25=float(np.percentile(pfs_finite, 25)),
            p75=float(np.percentile(pfs_finite, 75)),
            p95=float(np.percentile(pfs_finite, 95)),
        )
        pf_ci_95 = ConfidenceInterval(
            lower=float(np.percentile(pfs_finite, 2.5)),
            upper=float(np.percentile(pfs_finite, 97.5)),
        )

        # ── Drawdown Distribution ───────────────────────────────
        dd_stats = DistributionStats(
            mean=float(np.mean(dds)),
            median=float(np.median(dds)),
            best=float(np.min(dds)),
            worst=float(np.max(dds)),
            std_dev=float(np.std(dds)),
            p5=float(np.percentile(dds, 5)),
            p25=float(np.percentile(dds, 25)),
            p75=float(np.percentile(dds, 75)),
            p95=float(np.percentile(dds, 95)),
        )
        dd_ci_95 = ConfidenceInterval(
            lower=float(np.percentile(dds, 2.5)),
            upper=float(np.percentile(dds, 97.5)),
        )

        # ── Return Distribution ─────────────────────────────────
        return_stats = DistributionStats(
            mean=float(np.mean(returns)),
            median=float(np.median(returns)),
            best=float(np.max(returns)),
            worst=float(np.min(returns)),
            std_dev=float(np.std(returns)),
            p5=float(np.percentile(returns, 5)),
            p25=float(np.percentile(returns, 25)),
            p75=float(np.percentile(returns, 75)),
            p95=float(np.percentile(returns, 95)),
        )
        return_ci_95 = ConfidenceInterval(
            lower=float(np.percentile(returns, 2.5)),
            upper=float(np.percentile(returns, 97.5)),
        )

        # ── Sharpe Distribution ─────────────────────────────────
        sharpe_stats = DistributionStats(
            mean=float(np.mean(sharpes)),
            median=float(np.median(sharpes)),
            best=float(np.max(sharpes)),
            worst=float(np.min(sharpes)),
            std_dev=float(np.std(sharpes)),
            p5=float(np.percentile(sharpes, 5)),
            p25=float(np.percentile(sharpes, 25)),
            p75=float(np.percentile(sharpes, 75)),
            p95=float(np.percentile(sharpes, 95)),
        )

        # ── Risk of Ruin ────────────────────────────────────────
        ruin_count = np.sum(dds > self.RUIN_THRESHOLD)
        risk_of_ruin_pct = float(ruin_count / n_sims * 100)

        # ── Streak Analysis (across all sims) ───────────────────
        worst_losing_streak = max(s.longest_losing_streak for s in self.simulations)
        best_winning_streak = max(s.longest_winning_streak for s in self.simulations)

        # ── Edge Stability Score ────────────────────────────────
        edge_score, edge_rating = self._compute_edge_stability(
            worst_pf=pf_stats.worst,
            worst_dd=dd_stats.worst,
            risk_of_ruin=risk_of_ruin_pct,
            return_std=return_stats.std_dev,
            return_mean=return_stats.mean,
        )

        # ── Original Trade Stats ────────────────────────────────
        orig_pnls = [t.pnl for t in self.trades]
        orig_wins = [p for p in orig_pnls if p > 0]
        orig_losses = [p for p in orig_pnls if p <= 0]
        original_pf = sum(orig_wins) / abs(sum(orig_losses)) if orig_losses else float("inf")
        original_wr = len(orig_wins) / len(orig_pnls) * 100
        original_return = (sum(orig_pnls) / self.INITIAL_CAPITAL) * 100

        # Original drawdown
        orig_equity = np.cumsum(orig_pnls) + self.INITIAL_CAPITAL
        orig_equity = np.insert(orig_equity, 0, self.INITIAL_CAPITAL)
        orig_peak = np.maximum.accumulate(orig_equity)
        orig_dd = float(np.max((orig_peak - orig_equity) / orig_peak) * 100)

        # ── Pass/Fail Criteria ──────────────────────────────────
        criteria = {
            "worst_pf_gt_1.10": {
                "label": "Worst PF > 1.10",
                "value": pf_stats.worst,
                "threshold": 1.10,
                "pass": pf_stats.worst > 1.10,
            },
            "worst_dd_lt_10pct": {
                "label": "Worst DD < 10%",
                "value": dd_stats.worst,
                "threshold": 10.0,
                "pass": dd_stats.worst < 10.0,
            },
            "expected_pf_gt_1.30": {
                "label": "Expected PF > 1.30",
                "value": pf_stats.mean,
                "threshold": 1.30,
                "pass": pf_stats.mean > 1.30,
            },
            "expected_return_gt_0": {
                "label": "Expected Return > 0",
                "value": return_stats.mean,
                "threshold": 0.0,
                "pass": return_stats.mean > 0.0,
            },
            "risk_of_ruin_lt_5pct": {
                "label": "Risk of Ruin < 5%",
                "value": risk_of_ruin_pct,
                "threshold": 5.0,
                "pass": risk_of_ruin_pct < 5.0,
            },
            "ci_lower_pf_gt_1.00": {
                "label": "95% CI Lower PF > 1.00",
                "value": pf_ci_95.lower,
                "threshold": 1.00,
                "pass": pf_ci_95.lower > 1.00,
            },
        }
        overall_pass = all(c["pass"] for c in criteria.values())

        report = MonteCarloReport(
            n_simulations=n_sims,
            n_trades=n_trades,
            initial_capital=self.INITIAL_CAPITAL,
            execution_time_sec=elapsed,
            pf_stats=pf_stats,
            pf_ci_95=pf_ci_95,
            dd_stats=dd_stats,
            dd_ci_95=dd_ci_95,
            return_stats=return_stats,
            return_ci_95=return_ci_95,
            risk_of_ruin_pct=risk_of_ruin_pct,
            longest_losing_streak=worst_losing_streak,
            longest_winning_streak=best_winning_streak,
            sharpe_stats=sharpe_stats,
            edge_stability_score=edge_score,
            edge_stability_rating=edge_rating,
            criteria=criteria,
            overall_pass=overall_pass,
            simulations=self.simulations,
            original_pf=original_pf,
            original_wr=original_wr,
            original_return=original_return,
            original_dd=orig_dd,
        )

        return report

    # ─── 4. Generate Report ──────────────────────────────────────

    def generate_report(self, report: MonteCarloReport) -> str:
        """
        Generate and print the full Monte Carlo Robustness Report.

        Args:
            report: MonteCarloReport from calculate_metrics()

        Returns:
            Report string
        """
        pass_icon = "✅"
        fail_icon = "❌"

        lines = []
        lines.append("")
        lines.append("=" * 60)
        lines.append("  MONTE CARLO ROBUSTNESS REPORT")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"  Simulations:  {report.n_simulations:,}")
        lines.append(f"  Trades:       {report.n_trades}")
        lines.append(f"  Capital:      ${report.initial_capital:,.0f}")
        lines.append(f"  Runtime:      {report.execution_time_sec:.1f}s")
        lines.append("")
        lines.append("  NOTE: PF and Return are INVARIANT under shuffling")
        lines.append("  (total wins/losses don't change with reordering).")
        lines.append("  Monte Carlo tests PATH-DEPENDENT metrics:")
        lines.append("  drawdowns, streaks, and sequence risk.")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  ORIGINAL STRATEGY")
        lines.append("-" * 60)
        lines.append(f"  Profit Factor:  {report.original_pf:.2f}")
        lines.append(f"  Win Rate:       {report.original_wr:.1f}%")
        lines.append(f"  Return:         {report.original_return:+.2f}%")
        lines.append(f"  Max Drawdown:   {report.original_dd:.2f}%")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  PROFIT FACTOR DISTRIBUTION")
        lines.append("-" * 60)
        s = report.pf_stats
        lines.append(f"  Expected PF:      {s.mean:.2f}")
        lines.append(f"  Median PF:        {s.median:.2f}")
        lines.append(f"  Best PF:          {s.best:.2f}")
        lines.append(f"  Worst PF:         {s.worst:.2f}")
        lines.append(f"  Std Dev:          {s.std_dev:.2f}")
        lines.append(f"  5th Percentile:   {s.p5:.2f}")
        lines.append(f"  95th Percentile:  {s.p95:.2f}")
        lines.append(f"  95% CI:           [{report.pf_ci_95.lower:.2f}, {report.pf_ci_95.upper:.2f}]")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  DRAWDOWN DISTRIBUTION")
        lines.append("-" * 60)
        d = report.dd_stats
        lines.append(f"  Expected DD:      {d.mean:.2f}%")
        lines.append(f"  Median DD:        {d.median:.2f}%")
        lines.append(f"  Best DD:          {d.best:.2f}%")
        lines.append(f"  Worst DD:         {d.worst:.2f}%")
        lines.append(f"  5th Percentile:   {d.p5:.2f}%")
        lines.append(f"  95th Percentile:  {d.p95:.2f}%")
        lines.append(f"  95% CI:           [{report.dd_ci_95.lower:.2f}%, {report.dd_ci_95.upper:.2f}%]")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  RETURN DISTRIBUTION")
        lines.append("-" * 60)
        r = report.return_stats
        lines.append(f"  Expected Return:  {r.mean:+.2f}%")
        lines.append(f"  Median Return:    {r.median:+.2f}%")
        lines.append(f"  Best Return:      {r.best:+.2f}%")
        lines.append(f"  Worst Return:     {r.worst:+.2f}%")
        lines.append(f"  Std Dev:          {r.std_dev:.2f}%")
        lines.append(f"  95% CI:           [{report.return_ci_95.lower:+.2f}%, {report.return_ci_95.upper:+.2f}%]")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  RISK METRICS")
        lines.append("-" * 60)
        lines.append(f"  Risk of Ruin:       {report.risk_of_ruin_pct:.2f}%")
        lines.append(f"  Longest Win Streak: {report.longest_winning_streak}")
        lines.append(f"  Longest Loss Streak:{report.longest_losing_streak}")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  SHARPE RATIO DISTRIBUTION")
        lines.append("-" * 60)
        sh = report.sharpe_stats
        lines.append(f"  Expected Sharpe:  {sh.mean:.2f}")
        lines.append(f"  Median Sharpe:    {sh.median:.2f}")
        lines.append(f"  Best Sharpe:      {sh.best:.2f}")
        lines.append(f"  Worst Sharpe:     {sh.worst:.2f}")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  EDGE STABILITY SCORE")
        lines.append("-" * 60)
        lines.append(f"  Score:   {report.edge_stability_score:.0f}/100")
        lines.append(f"  Rating:  {report.edge_stability_rating}")
        lines.append("")
        lines.append("-" * 60)
        lines.append("  PASS / FAIL CRITERIA")
        lines.append("-" * 60)
        for key, c in report.criteria.items():
            icon = pass_icon if c["pass"] else fail_icon
            if "dd" in key.lower():
                lines.append(f"  {icon} {c['label']:30s}  → {c['value']:.2f}%  (threshold: {'<' if '<' in c['label'] else '>'} {c['threshold']:.1f}%)")
            elif "return" in key.lower() and "pf" not in key.lower():
                lines.append(f"  {icon} {c['label']:30s}  → {c['value']:+.2f}%  (threshold: > {c['threshold']:.1f}%)")
            elif "ruin" in key.lower():
                lines.append(f"  {icon} {c['label']:30s}  → {c['value']:.2f}%  (threshold: < {c['threshold']:.1f}%)")
            else:
                lines.append(f"  {icon} {c['label']:30s}  → {c['value']:.2f}  (threshold: > {c['threshold']:.2f})")
        lines.append("")
        lines.append("=" * 60)
        if report.overall_pass:
            lines.append("  ✅ OVERALL RESULT: PASS")
            lines.append("  Monte Carlo robustness validation PASSED")
            lines.append("  Strategy edge is NOT dependent on trade ordering")
        else:
            lines.append("  ❌ OVERALL RESULT: FAIL")
            lines.append("  Monte Carlo robustness validation FAILED")
            lines.append("  Strategy may be dependent on specific trade ordering")
        lines.append("=" * 60)
        lines.append("")

        # Institutional Readiness Assessment
        lines.append("-" * 60)
        lines.append("  INSTITUTIONAL READINESS ASSESSMENT")
        lines.append("-" * 60)
        score = report.edge_stability_score
        if score >= 90:
            readiness = "READY for capital deployment"
            recommendation = "Strategy passes institutional-grade robustness testing"
        elif score >= 80:
            readiness = "READY for paper trading + small capital"
            recommendation = "Strong edge stability — proceed with caution"
        elif score >= 70:
            readiness = "READY for paper trading only"
            recommendation = "Acceptable edge — requires more validation before capital"
        elif score >= 60:
            readiness = "REQUIRES additional validation"
            recommendation = "Weak edge stability — significant risk of performance decay"
        else:
            readiness = "NOT READY — unstable edge"
            recommendation = "Strategy edge is unreliable — do not deploy"

        lines.append(f"  Status:         {readiness}")
        lines.append(f"  Recommendation: {recommendation}")
        lines.append(f"  Edge Score:     {score:.0f}/100")
        lines.append(f"  Worst-case PF:  {report.pf_stats.worst:.2f}")
        lines.append(f"  Worst-case DD:  {report.dd_stats.worst:.2f}%")
        lines.append(f"  Risk of Ruin:   {report.risk_of_ruin_pct:.2f}%")
        lines.append("=" * 60)
        lines.append("")

        report_text = "\n".join(lines)
        print(report_text)
        return report_text

    # ─── 5. Export Results ───────────────────────────────────────

    def export_results(self, report: MonteCarloReport) -> Dict[str, str]:
        """
        Export all Monte Carlo results to files.

        Creates:
        - monte_carlo_results.csv: All simulation results
        - monte_carlo_summary.json: Complete report summary
        - monte_carlo_distribution.csv: PF/DD/Return distributions

        Args:
            report: MonteCarloReport from calculate_metrics()

        Returns:
            Dict of {filename: absolute_path} for all exported files
        """
        exports = {}

        # ── 1. Simulation Results CSV ───────────────────────────
        results_path = self._reports_dir / "monte_carlo_results.csv"
        with open(results_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "simulation_id", "net_profit", "profit_factor", "win_rate",
                "max_drawdown_pct", "longest_losing_streak", "longest_winning_streak",
                "sharpe_ratio", "return_pct", "return_dd_ratio", "final_equity",
            ])
            for sim in report.simulations:
                writer.writerow([
                    sim.simulation_id,
                    f"{sim.net_profit:.2f}",
                    f"{sim.profit_factor:.4f}",
                    f"{sim.win_rate:.4f}",
                    f"{sim.max_drawdown_pct:.4f}",
                    sim.longest_losing_streak,
                    sim.longest_winning_streak,
                    f"{sim.sharpe_ratio:.4f}",
                    f"{sim.return_pct:.4f}",
                    f"{sim.return_dd_ratio:.4f}",
                    f"{sim.final_equity:.2f}",
                ])
        exports["monte_carlo_results.csv"] = str(results_path)
        logger.info("Exported simulation results → {}", results_path)

        # ── 2. Summary JSON ─────────────────────────────────────
        summary_path = self._reports_dir / "monte_carlo_summary.json"
        summary = {
            "metadata": {
                "n_simulations": report.n_simulations,
                "n_trades": report.n_trades,
                "initial_capital": report.initial_capital,
                "execution_time_sec": round(report.execution_time_sec, 2),
            },
            "original_strategy": {
                "profit_factor": round(report.original_pf, 4),
                "win_rate_pct": round(report.original_wr, 2),
                "return_pct": round(report.original_return, 2),
                "max_drawdown_pct": round(report.original_dd, 2),
            },
            "profit_factor": {
                "mean": round(report.pf_stats.mean, 4),
                "median": round(report.pf_stats.median, 4),
                "best": round(report.pf_stats.best, 4),
                "worst": round(report.pf_stats.worst, 4),
                "std_dev": round(report.pf_stats.std_dev, 4),
                "p5": round(report.pf_stats.p5, 4),
                "p95": round(report.pf_stats.p95, 4),
                "ci_95_lower": round(report.pf_ci_95.lower, 4),
                "ci_95_upper": round(report.pf_ci_95.upper, 4),
            },
            "drawdown": {
                "mean_pct": round(report.dd_stats.mean, 4),
                "median_pct": round(report.dd_stats.median, 4),
                "best_pct": round(report.dd_stats.best, 4),
                "worst_pct": round(report.dd_stats.worst, 4),
                "p5_pct": round(report.dd_stats.p5, 4),
                "p95_pct": round(report.dd_stats.p95, 4),
                "ci_95_lower_pct": round(report.dd_ci_95.lower, 4),
                "ci_95_upper_pct": round(report.dd_ci_95.upper, 4),
            },
            "returns": {
                "mean_pct": round(report.return_stats.mean, 4),
                "median_pct": round(report.return_stats.median, 4),
                "best_pct": round(report.return_stats.best, 4),
                "worst_pct": round(report.return_stats.worst, 4),
                "std_dev_pct": round(report.return_stats.std_dev, 4),
                "ci_95_lower_pct": round(report.return_ci_95.lower, 4),
                "ci_95_upper_pct": round(report.return_ci_95.upper, 4),
            },
            "risk": {
                "risk_of_ruin_pct": round(report.risk_of_ruin_pct, 4),
                "longest_losing_streak": report.longest_losing_streak,
                "longest_winning_streak": report.longest_winning_streak,
            },
            "sharpe": {
                "mean": round(report.sharpe_stats.mean, 4),
                "median": round(report.sharpe_stats.median, 4),
                "best": round(report.sharpe_stats.best, 4),
                "worst": round(report.sharpe_stats.worst, 4),
            },
            "edge_stability": {
                "score": round(report.edge_stability_score, 1),
                "rating": report.edge_stability_rating,
            },
            "criteria": {
                key: {
                    "label": c["label"],
                    "value": round(c["value"], 4),
                    "threshold": c["threshold"],
                    "pass": c["pass"],
                }
                for key, c in report.criteria.items()
            },
            "overall_pass": report.overall_pass,
        }
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)
        exports["monte_carlo_summary.json"] = str(summary_path)
        logger.info("Exported summary → {}", summary_path)

        # ── 3. Distribution CSV ─────────────────────────────────
        dist_path = self._reports_dir / "monte_carlo_distribution.csv"
        pfs = [s.profit_factor for s in report.simulations]
        dds = [s.max_drawdown_pct for s in report.simulations]
        rets = [s.return_pct for s in report.simulations]

        with open(dist_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["simulation_id", "profit_factor", "max_drawdown_pct", "return_pct"])
            for i, sim in enumerate(report.simulations):
                writer.writerow([
                    sim.simulation_id,
                    f"{sim.profit_factor:.4f}",
                    f"{sim.max_drawdown_pct:.4f}",
                    f"{sim.return_pct:.4f}",
                ])
        exports["monte_carlo_distribution.csv"] = str(dist_path)
        logger.info("Exported distribution → {}", dist_path)

        # ── 4. Visualization Charts ─────────────────────────────
        chart_paths = self._generate_charts(report)
        exports.update(chart_paths)

        return exports

    # ─── Chart Generation ────────────────────────────────────────

    def _generate_charts(self, report: MonteCarloReport) -> Dict[str, str]:
        """Generate all visualization charts."""
        charts = {}
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.ticker as mticker

            plt.style.use("dark_background")
            fig_dir = self._reports_dir / "figures"
            fig_dir.mkdir(exist_ok=True)

            pfs = np.array([s.profit_factor for s in report.simulations])
            pfs_finite = np.where(np.isfinite(pfs), pfs, 100.0)
            dds = np.array([s.max_drawdown_pct for s in report.simulations])
            returns = np.array([s.return_pct for s in report.simulations])

            # ── 1. PF Distribution Histogram ────────────────────
            fig, ax = plt.subplots(figsize=(12, 6))
            pf_range = pfs_finite.max() - pfs_finite.min()
            n_bins = 80 if pf_range > 0.01 else 5
            ax.hist(pfs_finite, bins=n_bins, color="#00d4aa", alpha=0.85, edgecolor="#00ffcc", linewidth=0.3)
            ax.axvline(report.pf_stats.mean, color="#ff6b6b", linewidth=2, linestyle="--", label=f"Mean: {report.pf_stats.mean:.2f}")
            ax.axvline(report.pf_stats.worst, color="#ffd93d", linewidth=2, linestyle="-.", label=f"Worst: {report.pf_stats.worst:.2f}")
            ax.axvline(1.10, color="#ff4444", linewidth=2, linestyle=":", label="Threshold: 1.10")
            ax.axvline(report.pf_ci_95.lower, color="#a855f7", linewidth=1.5, linestyle="--", label=f"95% CI Lower: {report.pf_ci_95.lower:.2f}")
            ax.set_xlabel("Profit Factor", fontsize=12)
            ax.set_ylabel("Frequency", fontsize=12)
            ax.set_title(f"Profit Factor Distribution ({report.n_simulations:,} Simulations)", fontsize=14, fontweight="bold")
            ax.legend(fontsize=10)
            ax.grid(True, alpha=0.2)
            path = fig_dir / "pf_distribution.png"
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0d1117")
            plt.close(fig)
            charts["pf_distribution.png"] = str(path)

            # ── 2. Drawdown Distribution Histogram ──────────────
            fig, ax = plt.subplots(figsize=(12, 6))
            dd_vals = dds  # Already stored as 0-100 percentage
            dd_range = dd_vals.max() - dd_vals.min()
            n_bins_dd = 80 if dd_range > 0.5 else 10
            ax.hist(dd_vals, bins=n_bins_dd, color="#ff6b6b", alpha=0.85, edgecolor="#ff8888", linewidth=0.3)
            ax.axvline(np.mean(dd_vals), color="#00d4aa", linewidth=2, linestyle="--", label=f"Mean: {np.mean(dd_vals):.2f}%")
            ax.axvline(np.max(dd_vals), color="#ffd93d", linewidth=2, linestyle="-.", label=f"Worst: {np.max(dd_vals):.2f}%")
            ax.axvline(10.0, color="#ff4444", linewidth=2, linestyle=":", label="Threshold: 10%")
            ax.axvline(20.0, color="#ff0000", linewidth=2, linestyle=":", label="Ruin: 20%")
            ax.set_xlabel("Maximum Drawdown (%)", fontsize=12)
            ax.set_ylabel("Frequency", fontsize=12)
            ax.set_title(f"Drawdown Distribution ({report.n_simulations:,} Simulations)", fontsize=14, fontweight="bold")
            ax.legend(fontsize=10)
            ax.grid(True, alpha=0.2)
            path = fig_dir / "dd_distribution.png"
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0d1117")
            plt.close(fig)
            charts["dd_distribution.png"] = str(path)

            # ── 3. Return Distribution Histogram ────────────────
            fig, ax = plt.subplots(figsize=(12, 6))
            ret_range = returns.max() - returns.min()
            n_bins_ret = 80 if ret_range > 0.1 else 5
            ax.hist(returns, bins=n_bins_ret, color="#4ecdc4", alpha=0.85, edgecolor="#6ee7de", linewidth=0.3)
            ax.axvline(report.return_stats.mean, color="#ff6b6b", linewidth=2, linestyle="--", label=f"Mean: {report.return_stats.mean:+.2f}%")
            ax.axvline(0, color="#ffffff", linewidth=1.5, linestyle="-", alpha=0.5, label="Breakeven")
            ax.axvline(report.return_ci_95.lower, color="#a855f7", linewidth=1.5, linestyle="--", label=f"95% CI: [{report.return_ci_95.lower:+.1f}%, {report.return_ci_95.upper:+.1f}%]")
            ax.set_xlabel("Return (%)", fontsize=12)
            ax.set_ylabel("Frequency", fontsize=12)
            ax.set_title(f"Return Distribution ({report.n_simulations:,} Simulations)", fontsize=14, fontweight="bold")
            ax.legend(fontsize=10)
            ax.grid(True, alpha=0.2)
            path = fig_dir / "return_distribution.png"
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0d1117")
            plt.close(fig)
            charts["return_distribution.png"] = str(path)

            # ── 4. Equity Curve Fan Chart ───────────────────────
            fig, ax = plt.subplots(figsize=(14, 7))
            # Sample 200 equity curves for the fan chart
            sample_indices = np.random.choice(len(report.simulations), min(200, len(report.simulations)), replace=False)
            sample_equities = []
            for idx in sample_indices:
                eq = report.simulations[idx]
                # Rebuild equity curve from PnLs
                sim_pnls = np.random.permutation(np.array([t.pnl for t in self.trades]))
                equity = np.cumsum(sim_pnls) + self.INITIAL_CAPITAL
                equity = np.insert(equity, 0, self.INITIAL_CAPITAL)
                sample_equities.append(equity)

            eq_array = np.array(sample_equities)
            x = np.arange(eq_array.shape[1])

            # Percentile bands
            p5 = np.percentile(eq_array, 5, axis=0)
            p25 = np.percentile(eq_array, 25, axis=0)
            p50 = np.percentile(eq_array, 50, axis=0)
            p75 = np.percentile(eq_array, 75, axis=0)
            p95 = np.percentile(eq_array, 95, axis=0)

            ax.fill_between(x, p5, p95, alpha=0.15, color="#00d4aa", label="5th-95th Percentile")
            ax.fill_between(x, p25, p75, alpha=0.3, color="#00d4aa", label="25th-75th Percentile")
            ax.plot(x, p50, color="#00ffcc", linewidth=2, label="Median")
            ax.axhline(self.INITIAL_CAPITAL, color="#ffffff", linewidth=1, linestyle=":", alpha=0.3)
            ax.set_xlabel("Trade Number", fontsize=12)
            ax.set_ylabel("Equity ($)", fontsize=12)
            ax.set_title("Equity Curve Fan Chart (Monte Carlo)", fontsize=14, fontweight="bold")
            ax.legend(fontsize=10)
            ax.grid(True, alpha=0.2)
            path = fig_dir / "equity_fan_chart.png"
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0d1117")
            plt.close(fig)
            charts["equity_fan_chart.png"] = str(path)

            # ── 5. Risk of Ruin Chart ───────────────────────────
            fig, axes = plt.subplots(1, 2, figsize=(14, 6))

            # Left: DD vs PF scatter
            ax = axes[0]
            scatter = ax.scatter(
                pfs_finite, dds,
                c=returns, cmap="RdYlGn", alpha=0.4, s=8, edgecolors="none",
            )
            ax.axhline(20, color="#ff4444", linewidth=2, linestyle=":", label="Ruin Threshold (20%)")
            ax.axhline(10, color="#ffd93d", linewidth=1.5, linestyle="--", label="DD Threshold (10%)")
            ax.axvline(1.10, color="#ff4444", linewidth=1.5, linestyle="--", label="PF Threshold (1.10)")
            ax.set_xlabel("Profit Factor", fontsize=12)
            ax.set_ylabel("Max Drawdown (%)", fontsize=12)
            ax.set_title("Risk Landscape: PF vs DD", fontsize=13, fontweight="bold")
            ax.legend(fontsize=9)
            ax.grid(True, alpha=0.2)
            plt.colorbar(scatter, ax=ax, label="Return (%)")

            # Right: Edge Stability Score gauge
            ax = axes[1]
            score = report.edge_stability_score
            colors = ["#ff4444", "#ffd93d", "#4ecdc4", "#00d4aa"]
            thresholds = [0, 60, 70, 80, 100]
            rating_colors = []
            for i in range(len(thresholds) - 1):
                rating_colors.append(colors[i])

            # Simple bar representation
            bar_segments = []
            for i, (lo, hi, c) in enumerate(zip(thresholds[:-1], thresholds[1:], rating_colors)):
                bar_segments.append((lo, hi, c))

            for lo, hi, c in bar_segments:
                ax.barh(0, hi - lo, left=lo, height=0.5, color=c, alpha=0.3, edgecolor=c)

            ax.barh(0, score, left=0, height=0.5, color="#00ffcc", alpha=0.8, edgecolor="#00ffcc")
            ax.plot(score, 0, "D", color="#ffffff", markersize=15, zorder=5)
            ax.set_xlim(0, 100)
            ax.set_ylim(-0.5, 0.5)
            ax.set_xlabel("Score", fontsize=12)
            ax.set_title(f"Edge Stability: {score:.0f}/100 ({report.edge_stability_rating})", fontsize=13, fontweight="bold")
            ax.set_yticks([])
            ax.grid(True, alpha=0.2, axis="x")

            # Add threshold labels
            for t, label in [(60, "Weak"), (70, "Acceptable"), (80, "Strong"), (90, "Institutional")]:
                ax.axvline(t, color="#ffffff", linewidth=0.5, linestyle=":", alpha=0.3)
                ax.text(t, -0.35, label, ha="center", fontsize=8, color="#888888")

            fig.tight_layout()
            path = fig_dir / "risk_ruin_chart.png"
            fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0d1117")
            plt.close(fig)
            charts["risk_ruin_chart.png"] = str(path)

            logger.info("Generated {} charts in {}", len(charts), fig_dir)

        except ImportError as e:
            logger.warning("matplotlib not available — skipping charts: {}", e)
        except Exception as e:
            logger.warning("Chart generation failed: {}", e)

        return charts

    # ─── Internal Helpers ────────────────────────────────────────

    def _compute_sim_metrics(self, sim_id: int, equity: np.ndarray, pnls: np.ndarray) -> SimResult:
        """Compute all metrics for a single simulation."""
        n_trades = len(pnls)

        # Net profit
        net_profit = float(equity[-1] - self.INITIAL_CAPITAL)

        # Return %
        return_pct = float((equity[-1] - self.INITIAL_CAPITAL) / self.INITIAL_CAPITAL * 100)

        # Max drawdown
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        max_dd_pct = float(np.max(drawdown) * 100)

        # Win rate
        wins = np.sum(pnls > 0)
        win_rate = float(wins / n_trades) if n_trades > 0 else 0.0

        # Profit factor
        gross_profit = float(np.sum(pnls[pnls > 0]))
        gross_loss = float(np.abs(np.sum(pnls[pnls < 0])))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Sharpe ratio (annualized, assuming ~1 trade per 4h on avg)
        if n_trades > 1:
            avg_return = np.mean(pnls) / self.INITIAL_CAPITAL
            std_return = np.std(pnls) / self.INITIAL_CAPITAL
            if std_return > 0:
                # Annualize: ~2190 trades/year (365 * 6 trades/day at 4h each)
                sharpe = float(avg_return / std_return * np.sqrt(min(n_trades, 2190)))
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # Longest losing streak
        losing_streak = 0
        max_losing = 0
        for p in pnls:
            if p <= 0:
                losing_streak += 1
                max_losing = max(max_losing, losing_streak)
            else:
                losing_streak = 0

        # Longest winning streak
        winning_streak = 0
        max_winning = 0
        for p in pnls:
            if p > 0:
                winning_streak += 1
                max_winning = max(max_winning, winning_streak)
            else:
                winning_streak = 0

        # Return/DD ratio
        return_dd_ratio = return_pct / max_dd_pct if max_dd_pct > 0 else float("inf")

        return SimResult(
            simulation_id=sim_id,
            net_profit=net_profit,
            profit_factor=pf,
            win_rate=win_rate,
            max_drawdown_pct=max_dd_pct,
            longest_losing_streak=max_losing,
            longest_winning_streak=max_winning,
            sharpe_ratio=sharpe,
            return_pct=return_pct,
            return_dd_ratio=return_dd_ratio,
            final_equity=float(equity[-1]),
        )

    def _compute_edge_stability(
        self,
        worst_pf: float,
        worst_dd: float,
        risk_of_ruin: float,
        return_std: float,
        return_mean: float,
    ) -> Tuple[float, str]:
        """
        Compute Edge Stability Score (0-100).

        Formula:
        - 40% Worst PF score
        - 30% Worst DD score
        - 20% Risk of Ruin score
        - 10% Return Consistency score
        """
        # Worst PF score: 100 if PF >= 1.5, 0 if PF <= 0.8
        pf_score = max(0, min(100, (worst_pf - 0.8) / (1.5 - 0.8) * 100))

        # Worst DD score: 100 if DD <= 2%, 0 if DD >= 25%
        dd_score = max(0, min(100, (25 - worst_dd) / (25 - 2) * 100))

        # Risk of Ruin score: 100 if 0%, 0 if >= 10%
        ruin_score = max(0, min(100, (10 - risk_of_ruin) / 10 * 100))

        # Return consistency: 100 if CV < 0.5, 0 if CV > 3.0
        if return_mean > 0:
            cv = return_std / abs(return_mean)
            consistency_score = max(0, min(100, (3.0 - cv) / (3.0 - 0.5) * 100))
        else:
            consistency_score = 0

        score = (
            self.W_WORST_PF * pf_score
            + self.W_WORST_DD * dd_score
            + self.W_RISK_OF_RUIN * ruin_score
            + self.W_RETURN_CONSISTENCY * consistency_score
        )

        # Rating
        if score >= 90:
            rating = "Institutional Grade"
        elif score >= 80:
            rating = "Strong"
        elif score >= 70:
            rating = "Acceptable"
        elif score >= 60:
            rating = "Weak"
        else:
            rating = "Unstable"

        return round(score, 1), rating


# ─── Standalone Runner ───────────────────────────────────────────

def run_monte_carlo_validation(
    trade_csv: str = "data/reports/trade_log.csv",
    n_simulations: int = 5000,
    seed: Optional[int] = None,
) -> MonteCarloReport:
    """
    Run complete Monte Carlo robustness validation.

    Args:
        trade_csv: Path to trade_log.csv
        n_simulations: Number of simulations (default 5000)
        seed: Random seed for reproducibility (None = random)

    Returns:
        MonteCarloReport with all results
    """
    validator = MonteCarloValidator(seed=seed)

    # 1. Load trades
    n_trades = validator.load_trades(trade_csv)
    print(f"Loaded {n_trades} actual trades from {trade_csv}")

    # 2. Run simulations
    t0 = time.time()
    validator.run_simulations(n_simulations=n_simulations)
    elapsed = time.time() - t0

    # 3. Calculate metrics
    report = validator.calculate_metrics()
    report.execution_time_sec = elapsed

    # 4. Generate report
    validator.generate_report(report)

    # 5. Export results
    exports = validator.export_results(report)
    print(f"\nExported {len(exports)} files:")
    for name, path in exports.items():
        print(f"  {name}: {path}")

    return report


if __name__ == "__main__":
    run_monte_carlo_validation()
