"""
EMA_V5 Verification Report — Generates verification, quality, performance, and risk reports.
Isolated from existing report systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .verifier import EMAv5Verifier
from .diagnostics import EMAv5Diagnostics, SignalDiagnostics
from .statistics import EMAv5Statistics
from .quality import EMAv5Quality


class EMAv5VerificationReport:
    """Generates comprehensive verification reports."""

    def __init__(
        self,
        verifier: Optional[EMAv5Verifier] = None,
        statistics: Optional[EMAv5Statistics] = None,
        quality: Optional[EMAv5Quality] = None,
    ) -> None:
        self._verifier = verifier or EMAv5Verifier()
        self._statistics = statistics or EMAv5Statistics(self._verifier.get_diagnostics())
        self._quality = quality or EMAv5Quality()

    def verification_report(self) -> Dict[str, Any]:
        """Generate verification report — signal decision analysis."""
        diag = self._verifier.get_diagnostics()
        stats = self._verifier.get_stats()
        summary = diag.get_summary()

        # Recent diagnostics
        recent = diag.get_recent(100)
        recent_verdicts = [{"symbol": d.symbol, "verdict": d.verdict,
                           "time_ms": round(d.execution_time_ms, 1),
                           "failed": len(d.reasons_failed)}
                          for d in recent[-20:]]

        # Top failing checks
        top_fails = self._statistics.get_top_failing_checks(10)

        return {
            "report_type": "verification",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "summary": summary,
            "verification_stats": stats,
            "recent_signals": recent_verdicts,
            "top_failing_checks": top_fails,
            "diagnostics_count": diag.get_count(),
        }

    def quality_report(self) -> Dict[str, Any]:
        """Generate quality report — signal quality analysis."""
        diag = self._verifier.get_diagnostics()
        recent = diag.get_recent(1000)

        # Score all recent diagnostics
        if recent:
            batch_quality = self._quality.score_batch(recent)
            individual_scores = [
                {
                    "symbol": d.symbol,
                    "verdict": d.verdict,
                    **self._quality.score_signal(d),
                }
                for d in recent[-20:]
            ]
        else:
            batch_quality = {"avg_score": 0, "avg_grade": "F", "distribution": {}, "count": 0}
            individual_scores = []

        return {
            "report_type": "quality",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "batch_quality": batch_quality,
            "individual_scores": individual_scores,
        }

    def performance_report(self) -> Dict[str, Any]:
        """Generate performance report — verification system performance."""
        stats = self._statistics.compute_quality_metrics()
        accuracy_trend = self._statistics.get_accuracy_trend(50)

        return {
            "report_type": "performance",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "quality_metrics": stats,
            "accuracy_trend": accuracy_trend[-10:] if accuracy_trend else [],
        }

    def risk_report(self) -> Dict[str, Any]:
        """Generate risk report — signal risk analysis."""
        diag = self._verifier.get_diagnostics()
        recent = diag.get_recent(500)

        if not recent:
            return {
                "report_type": "risk",
                "generated_at": time.time(),
                "risk_level": "UNKNOWN",
                "checks": {},
            }

        # Risk indicators
        fail_rate = sum(1 for d in recent if d.verdict == "FAIL") / len(recent) * 100
        warning_rate = sum(1 for d in recent if d.verdict == "WARNING") / len(recent) * 100
        avg_confidence = sum(d.confidence_score for d in recent) / len(recent) * 100

        # Check failure patterns
        check_risk: Dict[str, Dict] = {}
        for d in recent:
            for c in d.checks:
                if c.name not in check_risk:
                    check_risk[c.name] = {"total": 0, "failed": 0}
                check_risk[c.name]["total"] += 1
                if not c.passed:
                    check_risk[c.name]["failed"] += 1

        for name in check_risk:
            r = check_risk[name]
            r["failure_rate"] = round(r["failed"] / max(r["total"], 1) * 100, 1)
            r["risk_level"] = (
                "HIGH" if r["failure_rate"] > 30 else
                "MEDIUM" if r["failure_rate"] > 15 else
                "LOW"
            )

        # Overall risk level
        if fail_rate > 50 or avg_confidence < 85:
            risk_level = "HIGH"
        elif fail_rate > 30 or avg_confidence < 90:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        return {
            "report_type": "risk",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "risk_level": risk_level,
            "fail_rate": round(fail_rate, 1),
            "warning_rate": round(warning_rate, 1),
            "avg_confidence": round(avg_confidence, 1),
            "check_risk": check_risk,
            "sample_size": len(recent),
        }

    def recommendations_report(self) -> Dict[str, Any]:
        """Generate recommendations report — improvement suggestions."""
        quality = self._quality_report_data()

        all_recommendations = []
        for score in quality.get("individual_scores", []):
            all_recommendations.extend(score.get("recommendations", []))

        # Deduplicate and count
        rec_counts: Dict[str, int] = {}
        for r in all_recommendations:
            rec_counts[r] = rec_counts.get(r, 0) + 1

        top_recommendations = sorted(rec_counts.items(), key=lambda x: -x[1])[:10]

        # Strategy recommendations
        strategy_recs = []
        batch = quality.get("batch_quality", {})
        if batch.get("avg_score", 0) < 80:
            strategy_recs.append("Overall signal quality is below 80 — review all check thresholds")
        if batch.get("avg_grade", "F") in ("D", "F"):
            strategy_recs.append("Grade distribution is poor — consider tightening entry conditions")

        check_recs = self._statistics.get_top_failing_checks(5)
        for cr in check_recs:
            if cr["failures"] > 10:
                strategy_recs.append(f"Check '{cr['check']}' failing frequently ({cr['failures']} times)")

        return {
            "report_type": "recommendations",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "top_recommendations": [{"recommendation": r, "count": c} for r, c in top_recommendations],
            "strategy_recommendations": strategy_recs,
        }

    def _quality_report_data(self) -> Dict[str, Any]:
        """Helper to get quality report data."""
        diag = self._verifier.get_diagnostics()
        recent = diag.get_recent(1000)
        if recent:
            return {
                "batch_quality": self._quality.score_batch(recent),
                "individual_scores": [
                    {"symbol": d.symbol, **self._quality.score_signal(d)}
                    for d in recent[-20:]
                ],
            }
        return {"batch_quality": {}, "individual_scores": []}

    def full_report(self) -> Dict[str, Any]:
        """Generate all reports combined."""
        return {
            "verification": self.verification_report(),
            "quality": self.quality_report(),
            "performance": self.performance_report(),
            "risk": self.risk_report(),
            "recommendations": self.recommendations_report(),
        }
