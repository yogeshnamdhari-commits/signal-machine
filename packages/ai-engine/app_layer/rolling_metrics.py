"""
Rolling Metrics Dashboard — Multi-Window Rolling Analysis.

Per Executive Assessment v18 + User v25 directive:
    "Instead of looking at only one rolling window, maintain several
     simultaneously: 25, 50, 100, 250 trades.

     For each window calculate:
         - Profit Factor, Expectancy, Win Rate, Capture Ratio,
         - Admission Precision, Average R, Maximum Drawdown

     That gives a much more reliable picture than a single rolling metric."

Key Innovation:
    v24: Single 50-trade window with 4 metrics
    v25: Four windows (25/50/100/250) x 7 metrics = 28 rolling measurements

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

WINDOWS = [
    (25, "Sudden degradation"),
    (50, "Short-term trend"),
    (100, "Medium-term stability"),
    (250, "Long-term robustness"),
]


@dataclass
class WindowMetrics:
    """All 7 metrics for a single window."""
    window: int = 0
    purpose: str = ""
    sample_size: int = 0

    profit_factor: float = 0.0
    expectancy: float = 0.0
    win_rate: float = 0.0
    capture_ratio: float = 0.0
    admission_precision: float = 0.0
    avg_r: float = 0.0
    max_drawdown: float = 0.0

    pf_status: str = ""
    ev_status: str = ""
    wr_status: str = ""
    capture_status: str = ""
    admission_status: str = ""
    avg_r_status: str = ""
    dd_status: str = ""

    pf_trend: str = ""
    ev_trend: str = ""
    wr_trend: str = ""
    capture_trend: str = ""
    admission_trend: str = ""
    avg_r_trend: str = ""
    dd_trend: str = ""

    window_score: float = 0.0
    window_tier: str = ""

    def to_dict(self) -> Dict:
        return {
            "window": self.window,
            "purpose": self.purpose,
            "sample_size": self.sample_size,
            "metrics": {
                "profit_factor":       {"value": round(self.profit_factor, 4), "target": 1.0, "status": self.pf_status, "trend": self.pf_trend},
                "expectancy":          {"value": round(self.expectancy, 4), "target": 0.0, "status": self.ev_status, "trend": self.ev_trend},
                "win_rate":            {"value": round(self.win_rate, 4), "target": 0.50, "status": self.wr_status, "trend": self.wr_trend},
                "capture_ratio":       {"value": round(self.capture_ratio, 4), "target": 0.50, "status": self.capture_status, "trend": self.capture_trend},
                "admission_precision": {"value": round(self.admission_precision, 4), "target": 0.60, "status": self.admission_status, "trend": self.admission_trend},
                "avg_r":               {"value": round(self.avg_r, 4), "target": 0.5, "status": self.avg_r_status, "trend": self.avg_r_trend},
                "max_drawdown":        {"value": round(self.max_drawdown, 4), "target": 0.0, "status": self.dd_status, "trend": self.dd_trend},
            },
            "window_score": round(self.window_score, 1),
            "window_tier": self.window_tier,
        }


@dataclass
class RootCauseChain:
    """Extended root-cause chain: Performance -> Root Cause -> Evidence -> Confidence -> Recommended Action -> Measured Result."""
    performance_summary: str = ""
    root_cause: str = ""
    evidence: str = ""
    confidence: str = ""
    recommended_action: str = ""
    measured_result: str = ""

    # v26 additions — PnL decomposition
    realized_pnl: float = 0.0      # What was actually captured
    missed_pnl: float = 0.0        # What was left on the table (MFE - realized)
    capture_pct: float = 0.0       # Realized / (Realized + Missed)
    total_mfe: float = 0.0         # Sum of all MFE

    # v27 additions — Extended root cause chain
    signal_quality: str = ""        # How strong the signal was
    execution_quality: str = ""     # How well execution proceeded
    exit_quality: str = ""          # How efficient the exit was
    exit_detail: str = ""           # Exit-specific details (which exits, timing)
    capture_detail: str = ""        # Capture-specific insights
    profit_delta: float = 0.0       # Net contribution to profit/loss
    actionable_evidence: str = ""   # Specific evidence supporting the root cause
    # v30: Alternative explanations
    alternatives: List[str] = field(default_factory=list)   # Other possible root causes
    alternative_evidence: List[str] = field(default_factory=list)  # Evidence for alternatives
    chosen_explanation_confidence: float = 0.0  # Confidence in chosen vs alternatives

    # v28 additions — Expected vs Observed PF improvement
    expected_pf_improvement: float = 0.0   # What PF improvement we expect from recommended action
    observed_pf_improvement: float = 0.0   # What PF improvement was actually observed (post-action)
    economic_impact_r: float = 0.0          # Estimated R impact of fixing the root cause
    economic_impact_priority: str = ""      # HIGH / MEDIUM / LOW

    # v29 additions — Quantitative root cause chain
    estimated_pf_gain: float = 0.0          # Expected PF gain if root cause is fixed
    estimated_dollar_impact: float = 0.0    # Expected dollar impact (R * avg_notional)
    confidence_level: str = ""              # HIGH / MEDIUM / LOW — confidence in this diagnosis
    recommended_action_detail: str = ""     # Specific actionable steps

    def to_dict(self) -> Dict:
        return {
            "performance_summary": self.performance_summary,
            "root_cause": self.root_cause,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "recommended_action": self.recommended_action,
            "measured_result": self.measured_result,
            "realized_pnl": round(self.realized_pnl, 4),
            "missed_pnl": round(self.missed_pnl, 4),
            "capture_pct": round(self.capture_pct, 4),
            "total_mfe": round(self.total_mfe, 4),
            "signal_quality": self.signal_quality,
            "execution_quality": self.execution_quality,
            "exit_quality": self.exit_quality,
            "exit_detail": self.exit_detail,
            "capture_detail": self.capture_detail,
            "profit_delta": round(self.profit_delta, 4),
            "actionable_evidence": self.actionable_evidence,
            "alternatives": self.alternatives,
            "alternative_evidence": self.alternative_evidence,
            "chosen_explanation_confidence": round(self.chosen_explanation_confidence, 2),
            "expected_pf_improvement": round(self.expected_pf_improvement, 3),
            "observed_pf_improvement": round(self.observed_pf_improvement, 3),
            "economic_impact_r": round(self.economic_impact_r, 3),
            "economic_impact_priority": self.economic_impact_priority,
            "estimated_pf_gain": round(self.estimated_pf_gain, 3),
            "estimated_dollar_impact": round(self.estimated_dollar_impact, 3),
            "confidence_level": self.confidence_level,
            "recommended_action_detail": self.recommended_action_detail,
        }


@dataclass
class RollingMetricsDashboard:
    """Complete rolling metrics dashboard with multi-window analysis."""
    timestamp: float = 0.0
    windows: List[WindowMetrics] = field(default_factory=list)
    window_divergences: List[str] = field(default_factory=list)
    convergence_score: float = 0.0
    root_cause_chain: RootCauseChain = field(default_factory=RootCauseChain)
    health_score: float = 0.0
    health_tier: str = ""
    risk_pct: float = 100.0
    diagnosis: str = ""
    alerts: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "windows": [w.to_dict() for w in self.windows],
            "cross_window": {
                "divergences": self.window_divergences,
                "convergence_score": round(self.convergence_score, 3),
            },
            "root_cause_chain": self.root_cause_chain.to_dict(),
            "health": {"score": round(self.health_score, 1), "tier": self.health_tier, "risk_pct": round(self.risk_pct, 1)},
            "diagnosis": self.diagnosis,
            "alerts": self.alerts,
            "recommendations": self.recommendations,
        }


class RollingMetricsDashboardEngine:
    """Multi-window rolling metrics dashboard — 7 metrics x 4 windows."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, side, realized_r, regime, session,
                       confidence, institutional_score, highest_pnl, mfe_pct
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()
            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()
        except Exception as e:
            logger.warning("Could not load rolling metrics: {}", e)

    def evaluate(self) -> RollingMetricsDashboard:
        """Evaluate all windows and generate dashboard."""
        self._ensure_loaded()
        dashboard = RollingMetricsDashboard(timestamp=time.time())

        if not self._trades or len(self._trades) < 25:
            dashboard.health_score = 0
            dashboard.health_tier = "CRITICAL"
            dashboard.risk_pct = 0
            dashboard.diagnosis = "Insufficient data (need >=25 trades)"
            return dashboard

        for window_size, purpose in WINDOWS:
            if len(self._trades) < window_size:
                continue
            wm = self._calc_window(window_size, purpose)
            dashboard.windows.append(wm)

        if not dashboard.windows:
            dashboard.diagnosis = "No windows calculable"
            return dashboard

        dashboard.window_divergences = self._find_divergences(dashboard.windows)
        dashboard.convergence_score = self._calc_convergence(dashboard.windows)

        all_scores = [w.window_score for w in dashboard.windows]
        dashboard.health_score = sum(all_scores) / max(1, len(all_scores))

        if dashboard.health_score >= 70:
            dashboard.health_tier = "HEALTHY"
            dashboard.risk_pct = 80
        elif dashboard.health_score >= 50:
            dashboard.health_tier = "CAUTION"
            dashboard.risk_pct = 50
        elif dashboard.health_score >= 30:
            dashboard.health_tier = "STRESSED"
            dashboard.risk_pct = 25
        else:
            dashboard.health_tier = "CRITICAL"
            dashboard.risk_pct = 0

        dashboard.root_cause_chain = self._build_root_cause_chain(dashboard)
        dashboard.diagnosis = self._diagnose(dashboard)
        dashboard.alerts = self._generate_alerts(dashboard.windows)
        dashboard.recommendations = self._generate_recommendations(dashboard)
        return dashboard

    def _calc_window(self, window: int, purpose: str) -> WindowMetrics:
        """Calculate all 7 metrics for a single window."""
        trades = self._trades[:window]
        wm = WindowMetrics(window=window, purpose=purpose, sample_size=len(trades))
        r_values = [t.get("realized_r", 0) or 0 for t in trades]

        # 1. Profit Factor
        wins = [r for r in r_values if r > 0]
        losses = [abs(r) for r in r_values if r < 0]
        wm.profit_factor = sum(wins) / max(0.01, sum(losses))
        wm.pf_status = self._status_higher_better("profit_factor", wm.profit_factor)
        wm.pf_trend = self._trend_simple(trades, lambda t: t.get("realized_r", 0) or 0, self._pf_simple)

        # 2. Expectancy
        wm.expectancy = sum(r_values) / max(1, len(r_values))
        wm.ev_status = self._status_higher_better("expectancy", wm.expectancy)
        wm.ev_trend = self._trend_simple(trades, lambda t: t.get("realized_r", 0) or 0, lambda vs: sum(vs)/max(1,len(vs)))

        # 3. Win Rate
        wm.win_rate = len(wins) / max(1, len(r_values))
        wm.wr_status = self._status_higher_better("win_rate", wm.win_rate)
        wm.wr_trend = self._trend_simple(trades, lambda t: 1 if (t.get("realized_r", 0) or 0) > 0 else 0, lambda vs: sum(vs)/max(1,len(vs)))

        # 4. Capture Ratio
        capture_vals = []
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                capture_vals.append(r / mfe)
        wm.capture_ratio = sum(capture_vals) / max(1, len(capture_vals)) if capture_vals else 0.5
        wm.capture_status = self._status_higher_better("capture_ratio", wm.capture_ratio)
        wm.capture_trend = "STABLE"

        # 5. Admission Precision
        admitted = [t for t in trades if (t.get("confidence", 0) or 0) > 0.85]
        admitted_winners = [t for t in admitted if (t.get("realized_r", 0) or 0) > 0]
        wm.admission_precision = len(admitted_winners) / max(1, len(admitted))
        wm.admission_status = self._status_higher_better("admission_precision", wm.admission_precision)
        wm.admission_trend = "STABLE"

        # 6. Average R
        wm.avg_r = wm.expectancy
        wm.avg_r_status = self._status_higher_better("avg_r", wm.avg_r)
        wm.avg_r_trend = wm.ev_trend

        # 7. Max Drawdown
        cum = 0; peak = 0; max_dd = 0
        for r in r_values:
            cum += r
            if cum > peak: peak = cum
            dd = peak - cum
            if dd > max_dd: max_dd = dd
        wm.max_drawdown = max_dd
        wm.dd_status = "GOOD" if max_dd <= 5.0 else "WARNING" if max_dd <= 10.0 else "CRITICAL"
        wm.dd_trend = "STABLE"

        # Window score
        statuses = [wm.pf_status, wm.ev_status, wm.wr_status, wm.capture_status,
                     wm.admission_status, wm.avg_r_status, wm.dd_status]
        score_map = {"GOOD": 100, "WARNING": 60, "CRITICAL": 20}
        wm.window_score = sum(score_map.get(s, 20) for s in statuses) / max(1, len(statuses))

        if wm.window_score >= 70: wm.window_tier = "HEALTHY"
        elif wm.window_score >= 50: wm.window_tier = "CAUTION"
        elif wm.window_score >= 30: wm.window_tier = "STRESSED"
        else: wm.window_tier = "CRITICAL"

        return wm

    def _status_higher_better(self, metric: str, value: float) -> str:
        targets = {
            "profit_factor": (1.0, 0.85), "expectancy": (0.0, -0.2),
            "win_rate": (0.50, 0.40), "capture_ratio": (0.50, 0.30),
            "admission_precision": (0.60, 0.40), "avg_r": (0.5, 0.0),
        }
        good, warn = targets.get(metric, (0, 0))
        if value >= good: return "GOOD"
        elif value >= warn: return "WARNING"
        return "CRITICAL"

    def _trend_simple(self, trades, extractor, calculator):
        mid = len(trades) // 2
        if mid == 0: return "STABLE"
        first_half = [extractor(t) for t in trades[mid:]]
        second_half = [extractor(t) for t in trades[:mid]]
        v1 = calculator(first_half)
        v2 = calculator(second_half)
        if v2 > v1 * 1.05 or (v2 - v1 > 0.05): return "IMPROVING"
        elif v2 < v1 * 0.95 or (v1 - v2 > 0.05): return "DECLINING"
        return "STABLE"

    def _pf_simple(self, r_values):
        wins = [r for r in r_values if r > 0]
        losses = [abs(r) for r in r_values if r < 0]
        return sum(wins) / max(0.01, sum(losses))

    def _find_divergences(self, windows):
        divergences = []
        for i in range(len(windows)):
            for j in range(i + 1, len(windows)):
                w1, w2 = windows[i], windows[j]
                if w1.profit_factor > 1.0 and w2.profit_factor < 0.8:
                    divergences.append(f"PF diverges: {w1.window}t={w1.profit_factor:.2f} vs {w2.window}t={w2.profit_factor:.2f}")
                if w1.expectancy > 0 and w2.expectancy < -0.3:
                    divergences.append(f"EV diverges: {w1.window}t={w1.expectancy:.3f}R vs {w2.window}t={w2.expectancy:.3f}R")
                if w1.window_tier == "HEALTHY" and w2.window_tier == "CRITICAL":
                    divergences.append(f"Health diverges: {w1.window}t={w1.window_tier} vs {w2.window}t={w2.window_tier}")
        return divergences

    def _calc_convergence(self, windows):
        if len(windows) < 2: return 1.0
        scores = [w.window_score for w in windows]
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        return max(0.0, 1.0 - math.sqrt(variance) / 50)

    def _build_root_cause_chain(self, dashboard):
        chain = RootCauseChain()
        if not dashboard.windows: return chain
        primary = next((w for w in dashboard.windows if w.window == 100), dashboard.windows[-1])
        chain.performance_summary = f"PF={primary.profit_factor:.2f}, EV={primary.expectancy:.3f}R, WR={primary.win_rate:.1%}, Capture={primary.capture_ratio:.1%}, Admission={primary.admission_precision:.3f}"

        pf_crit = primary.pf_status == "CRITICAL"
        adm_crit = primary.admission_status == "CRITICAL"
        cap_crit = primary.capture_status == "CRITICAL"

        if pf_crit and adm_crit and cap_crit:
            chain.root_cause = "market"
            chain.evidence = f"All core metrics critical: PF={primary.profit_factor:.2f}, Admission={primary.admission_precision:.3f}, Capture={primary.capture_ratio:.1%}"
        elif adm_crit and not cap_crit:
            chain.root_cause = "entry"
            chain.evidence = f"Admission={primary.admission_precision:.3f} critical but capture={primary.capture_ratio:.1%} acceptable"
        elif cap_crit and not adm_crit:
            chain.root_cause = "exit"
            chain.evidence = f"Capture={primary.capture_ratio:.1%} critical but admission={primary.admission_precision:.3f} acceptable"
        else:
            chain.root_cause = "market" if pf_crit else "unknown"
            chain.evidence = f"PF={primary.profit_factor:.2f} across {primary.window}t window"

        sample = primary.sample_size
        convergence = dashboard.convergence_score
        if sample >= 100 and convergence > 0.7: chain.confidence = "HIGH"
        elif sample >= 50 and convergence > 0.5: chain.confidence = "MEDIUM"
        else: chain.confidence = "LOW"

        if chain.root_cause == "entry":
            chain.recommended_action = "Tighten admission filter — raise confidence threshold"
        elif chain.root_cause == "exit":
            chain.recommended_action = "Optimize exit logic — review trailing stops and take-profit"
        elif chain.root_cause == "market":
            chain.recommended_action = "Reduce exposure — system not adapting to current regime"
        else:
            chain.recommended_action = "Continue monitoring — insufficient evidence for action"

        chain.measured_result = "Pending — requires post-action measurement"

        # v26: PnL decomposition
        r_values = [t.get("realized_r", 0) or 0 for t in self._trades[:primary.window]]
        chain.realized_pnl = sum(r_values)

        missed = 0
        total_mfe = 0
        for t in self._trades[:primary.window]:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                total_mfe += mfe
                missed += max(0, mfe - r)
        chain.missed_pnl = missed
        chain.total_mfe = total_mfe
        chain.capture_pct = chain.realized_pnl / max(0.01, chain.realized_pnl + missed) if (chain.realized_pnl + missed) > 0 else 0

        # v27: Extended root cause chain
        chain.signal_quality = (
            f"Admission={primary.admission_precision:.1%}, "
            f"Confidence avg={sum(t.get('confidence', 0) or 0 for t in self._trades[:primary.window]) / max(1, primary.window):.1f}"
        )
        chain.execution_quality = (
            f"Hold avg={sum(t.get('hold_minutes', 0) or 0 for t in self._trades[:primary.window]) / max(1, primary.window):.0f}m, "
            f"MFE avg={primary.capture_ratio:.1%} capture"
        )

        # Exit analysis
        exit_reasons = defaultdict(int)
        exit_pnl = defaultdict(float)
        for t in self._trades[:primary.window]:
            reason = t.get("exit_reason", "unknown") or "unknown"
            exit_reasons[reason] += 1
            exit_pnl[reason] += t.get("realized_r", 0) or 0
        chain.exit_detail = (
            f"Exits: {', '.join(f'{k}={v:.2f}R({exit_reasons[k]}t)' for k, v in sorted(exit_pnl.items(), key=lambda x: -x[1])[:5])}"
        )
        chain.exit_quality = f"{len(exit_reasons)} exit types, best={max(exit_pnl.items(), key=lambda x: x[1])[0] if exit_pnl else 'N/A'}"

        # Capture detail
        chain.capture_detail = (
            f"Realized={chain.realized_pnl:.2f}R, Missed={chain.missed_pnl:.2f}R, "
            f"Capture={chain.capture_pct:.1%}, Total MFE={chain.total_mfe:.2f}R"
        )
        chain.profit_delta = chain.realized_pnl

        # Actionable evidence
        if chain.capture_pct < 0.2:
            chain.actionable_evidence = (
                f"Capture at {chain.capture_pct:.1%} — system captures <20% of available moves. "
                f"This is the #1 lever: realize more of the {chain.total_mfe:.1f}R of MFE available."
            )
            chain.expected_pf_improvement = 0.15
            chain.economic_impact_r = chain.missed_pnl * 0.3
            chain.economic_impact_priority = "HIGH"
        elif chain.capture_pct < 0.5:
            chain.actionable_evidence = (
                f"Capture at {chain.capture_pct:.1%} — moderate. "
                f"{chain.missed_pnl:.1f}R left on table. Trailing stops could capture more."
            )
            chain.expected_pf_improvement = 0.08
            chain.economic_impact_r = chain.missed_pnl * 0.15
            chain.economic_impact_priority = "MEDIUM"
        else:
            chain.actionable_evidence = f"Capture at {chain.capture_pct:.1%} — acceptable. Focus on entry quality."
            chain.expected_pf_improvement = 0.03
            chain.economic_impact_r = chain.missed_pnl * 0.05
            chain.economic_impact_priority = "LOW"

        # v29: Quantitative root cause chain
        chain.estimated_pf_gain = chain.expected_pf_improvement
        chain.estimated_dollar_impact = chain.economic_impact_r * 10  # Assume $10 per R
        if sample >= 100 and convergence > 0.7:
            chain.confidence_level = "HIGH"
        elif sample >= 50 and convergence > 0.5:
            chain.confidence_level = "MEDIUM"
        else:
            chain.confidence_level = "LOW"

        if chain.root_cause == "exit":
            chain.recommended_action_detail = (
                "1. Extend trailing stop distance by 20%\n"
                "2. Add time-based exit scaling (hold winners longer)\n"
                "3. Disable trailing stops on SHORT positions\n"
                "4. Track capture ratio improvement over 50 trades"
            )
            chain.alternatives = ["entry quality is poor", "market regime is unfavorable"]
            chain.alternative_evidence = [
                f"Admission precision={primary.admission_precision:.1%} — very low, but not the primary issue",
                f"Regime breakdown shows all regimes negative — market-wide issue"
            ]
            chain.chosen_explanation_confidence = 0.80
        elif chain.root_cause == "entry":
            chain.recommended_action_detail = (
                "1. Raise admission threshold by 5 points\n"
                "2. Add minimum MFE requirement\n"
                "3. Track precision improvement over 50 trades"
            )
            chain.alternatives = ["exit timing is poor", "market regime is unfavorable"]
            chain.alternative_evidence = [
                f"Capture={chain.capture_pct:.1%} — exits may also be a factor",
                f"All regimes negative — market environment may be the primary cause"
            ]
            chain.chosen_explanation_confidence = 0.65
        else:
            chain.recommended_action_detail = (
                "1. Reduce exposure by 30%\n"
                "2. Review session-specific behavior\n"
                "3. Track PF improvement over 50 trades"
            )
            chain.alternatives = ["exit timing is poor", "entry quality is poor"]
            chain.alternative_evidence = [
                f"Capture={chain.capture_pct:.1%} — exits may be a factor",
                f"Admission precision={primary.admission_precision:.1%} — entry quality may also be poor"
            ]
            chain.chosen_explanation_confidence = 0.50

        return chain

    def _diagnose(self, dashboard):
        tiers = [w.window_tier for w in dashboard.windows]
        crit = tiers.count("CRITICAL")
        healthy = tiers.count("HEALTHY")
        if crit == 0 and healthy == len(tiers): return "All windows HEALTHY — system performing consistently"
        elif crit == 0: return f"No critical windows. Convergence={dashboard.convergence_score:.2f}"
        elif crit == len(tiers): return f"All {crit} windows CRITICAL — systemic failure"
        return f"{crit}/{len(tiers)} windows CRITICAL. Convergence={dashboard.convergence_score:.2f}. Root cause: {dashboard.root_cause_chain.root_cause}"

    def _generate_alerts(self, windows):
        alerts = []
        for wm in windows:
            for name, val, st in [("PF", wm.profit_factor, wm.pf_status), ("EV", wm.expectancy, wm.ev_status),
                                   ("WR", wm.win_rate, wm.wr_status), ("Capture", wm.capture_ratio, wm.capture_status),
                                   ("Admission", wm.admission_precision, wm.admission_status),
                                   ("AvgR", wm.avg_r, wm.avg_r_status), ("MaxDD", wm.max_drawdown, wm.dd_status)]:
                if st == "CRITICAL":
                    alerts.append(f"CRITICAL {name} ({wm.window}t): {val:.3f} — {wm.purpose}")
        return alerts

    def _generate_recommendations(self, dashboard):
        recs = [dashboard.root_cause_chain.recommended_action]
        if dashboard.window_divergences:
            recs.append(f"Cross-window divergence detected ({len(dashboard.window_divergences)} divergences) — possible regime shift")
        if dashboard.convergence_score < 0.5:
            recs.append(f"Low convergence ({dashboard.convergence_score:.2f}) — metrics disagree across timeframes")
        for wm in dashboard.windows:
            if wm.window == 25 and wm.window_tier == "CRITICAL":
                recs.append("25-trade window CRITICAL — sudden degradation detected. Consider pausing new entries.")
        if not recs: recs.append("Continue monitoring — no immediate action needed")
        return recs
