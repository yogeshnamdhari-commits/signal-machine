"""
EMA_V5 Statistics — Quality metrics tracking for signal verification.
Tracks accuracy, false positives, false negatives, duplicate rate, etc.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .diagnostics import EMAv5Diagnostics, SignalDiagnostics


class EMAv5Statistics:
    """Tracks quality metrics for EMA_V5 signals."""

    def __init__(self, diagnostics: Optional[EMAv5Diagnostics] = None) -> None:
        self._diagnostics = diagnostics or EMAv5Diagnostics()
        self._outcomes: List[Dict] = []  # signal_uuid → outcome tracking
        self._cooldown_events: List[Dict] = []

    def record_outcome(
        self,
        signal_uuid: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float = 0.0,
        pnl: float = 0.0,
        result: str = "",  # win, loss, pending
        hold_minutes: float = 0.0,
    ) -> None:
        """Record signal outcome for accuracy tracking."""
        self._outcomes.append({
            "signal_uuid": signal_uuid,
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "result": result,
            "hold_minutes": hold_minutes,
            "recorded_at": time.time(),
        })

    def record_cooldown_event(self, symbol: str, blocked: bool, reason: str) -> None:
        """Record a cooldown event."""
        self._cooldown_events.append({
            "symbol": symbol,
            "blocked": blocked,
            "reason": reason,
            "timestamp": time.time(),
        })

    def compute_quality_metrics(self) -> Dict[str, Any]:
        """Compute all quality metrics."""
        diag_list = self._diagnostics.get_recent(10000)
        outcomes = self._outcomes

        if not diag_list:
            return self._empty_metrics()

        # ── Signal Accuracy ──
        total_signals = len(outcomes)
        wins = sum(1 for o in outcomes if o.get("result") == "win")
        losses = sum(1 for o in outcomes if o.get("result") == "loss")
        pending = sum(1 for o in outcomes if o.get("result") in ("", "pending"))
        accuracy = (wins / max(total_signals - pending, 1)) * 100 if total_signals > pending else 0

        # ── False Positives (PASS verdict but losing trade) ──
        pass_signals = [d for d in diag_list if d.verdict == "PASS"]
        pass_uuids = {d.signal_uuid for d in pass_signals}
        false_positives = sum(
            1 for o in outcomes
            if o.get("signal_uuid") in pass_uuids and o.get("result") == "loss"
        )
        fp_rate = (false_positives / max(len(pass_signals), 1)) * 100

        # ── False Negatives (FAIL verdict but would have been winning trade) ──
        fail_signals = [d for d in diag_list if d.verdict == "FAIL"]
        fail_uuids = {d.signal_uuid for d in fail_signals}
        false_negatives = sum(
            1 for o in outcomes
            if o.get("signal_uuid") in fail_uuids and o.get("result") == "win"
        )
        fn_rate = (false_negatives / max(len(fail_signals), 1)) * 100

        # ── Duplicate Rate ──
        duplicate_checks = [d for d in diag_list if any(c.name == "duplicate" for c in d.checks)]
        dup_blocked = sum(
            1 for d in duplicate_checks
            for c in d.checks if c.name == "duplicate" and not c.passed
        )
        dup_rate = (dup_blocked / max(len(duplicate_checks), 1)) * 100

        # ── Cooldown Efficiency ──
        cooldown_blocked = sum(1 for e in self._cooldown_events if e.get("blocked"))
        cooldown_total = len(self._cooldown_events)
        cooldown_efficiency = (cooldown_blocked / max(cooldown_total, 1)) * 100

        # ── Average Confidence ──
        confidences = [d.confidence_score for d in diag_list if d.confidence_score > 0]
        avg_confidence = (sum(confidences) / len(confidences)) * 100 if confidences else 0

        # ── Verdict Distribution ──
        verdict_counts = {}
        for d in diag_list:
            verdict_counts[d.verdict] = verdict_counts.get(d.verdict, 0) + 1

        # ── Check Failure Rates ──
        check_failures = self._compute_check_failures(diag_list)

        # ── PnL by Verdict ──
        pnl_by_verdict = self._pnl_by_verdict(diag_list, outcomes)

        return {
            "signal_accuracy": round(accuracy, 1),
            "total_signals": total_signals,
            "wins": wins,
            "losses": losses,
            "pending": pending,
            "false_positives": false_positives,
            "false_positive_rate": round(fp_rate, 1),
            "false_negatives": false_negatives,
            "false_negative_rate": round(fn_rate, 1),
            "duplicate_rate": round(dup_rate, 1),
            "cooldown_efficiency": round(cooldown_efficiency, 1),
            "avg_confidence": round(avg_confidence, 1),
            "verdict_distribution": verdict_counts,
            "check_failures": check_failures,
            "pnl_by_verdict": pnl_by_verdict,
        }

    def _compute_check_failures(self, diag_list: List[SignalDiagnostics]) -> Dict[str, int]:
        """Compute failure count per check type."""
        failures: Dict[str, int] = {}
        for d in diag_list:
            for c in d.checks:
                if not c.passed:
                    failures[c.name] = failures.get(c.name, 0) + 1
        return dict(sorted(failures.items(), key=lambda x: -x[1]))

    def _pnl_by_verdict(self, diag_list: List[SignalDiagnostics],
                        outcomes: List[Dict]) -> Dict[str, float]:
        """Compute average PnL by verdict."""
        uuid_to_verdict = {d.signal_uuid: d.verdict for d in diag_list}
        pnl_by_verdict: Dict[str, List[float]] = {}
        for o in outcomes:
            verdict = uuid_to_verdict.get(o.get("signal_uuid", ""), "UNKNOWN")
            pnl = o.get("pnl", 0)
            pnl_by_verdict.setdefault(verdict, []).append(pnl)

        return {
            v: round(sum(pnls) / len(pnls), 4) if pnls else 0
            for v, pnls in pnl_by_verdict.items()
        }

    def get_accuracy_trend(self, window: int = 100) -> List[Dict]:
        """Get accuracy trend over rolling window."""
        if len(self._outcomes) < window:
            return []

        trend = []
        for i in range(window, len(self._outcomes) + 1):
            window_outcomes = self._outcomes[i - window:i]
            completed = [o for o in window_outcomes if o.get("result") in ("win", "loss")]
            if completed:
                wins = sum(1 for o in completed if o.get("result") == "win")
                trend.append({
                    "index": i,
                    "accuracy": round(wins / len(completed) * 100, 1),
                    "sample_size": len(completed),
                })

        return trend

    def get_top_failing_checks(self, n: int = 5) -> List[Dict]:
        """Get top N failing checks."""
        metrics = self.compute_quality_metrics()
        failures = metrics.get("check_failures", {})
        return [{"check": k, "failures": v} for k, v in list(failures.items())[:n]]

    def _empty_metrics(self) -> Dict[str, Any]:
        """Return zeroed metrics."""
        return {
            "signal_accuracy": 0, "total_signals": 0, "wins": 0, "losses": 0,
            "pending": 0, "false_positives": 0, "false_positive_rate": 0,
            "false_negatives": 0, "false_negative_rate": 0,
            "duplicate_rate": 0, "cooldown_efficiency": 0, "avg_confidence": 0,
            "verdict_distribution": {}, "check_failures": {}, "pnl_by_verdict": {},
        }
