"""
Explainability Engine — Human-readable decision summaries.

Per Priority 6: Every executed trade should generate a concise decision summary.
    Trade accepted — Reason: Institution Agreement: 87%, Expectancy: +1.64R,
    Correlation: Low, Portfolio Heat: 2.4%, Execution Quality: Excellent,
    Risk: Normal, Final Score: 95.3

Every rejected trade should receive a similar explanation.

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


class ExplainabilityEngine:
    """
    Generates human-readable decision summaries.

    Per Priority 6: Every trade gets an explanation.

    READ-ONLY: never modifies upstream data.
    """

    def explain_decision(
        self,
        symbol: str,
        side: str,
        decision: str,
        priority: str,
        trade_quality_score: float = 0.0,
        expected_value_r: float = 0.0,
        institution_agreement: float = 0.0,
        regime: str = "",
        regime_approved: bool = False,
        reward_approved: bool = False,
        rr: float = 0.0,
        correlation_reduction: float = 1.0,
        portfolio_heat_pct: float = 0.0,
        execution_quality: float = 0.0,
        risk_state: str = "NORMAL",
        risk_multiplier: float = 1.0,
        rejection_reasons: Optional[List[str]] = None,
    ) -> str:
        """
        Generate a human-readable explanation for a trade decision.

        Returns:
            Formatted explanation string
        """
        if decision == "EXECUTE":
            return self._explain_acceptance(
                symbol, side, priority, trade_quality_score,
                expected_value_r, institution_agreement, regime,
                correlation_reduction, portfolio_heat_pct,
                execution_quality, risk_state,
            )
        else:
            return self._explain_rejection(
                symbol, side, priority, trade_quality_score,
                expected_value_r, institution_agreement, regime,
                rr, rejection_reasons or [],
            )

    def _explain_acceptance(
        self, symbol, side, priority, tq_score, ev_r, inst_agree,
        regime, corr_reduction, heat_pct, exec_quality, risk_state,
    ) -> str:
        """Generate acceptance explanation."""
        lines = []
        lines.append(f"✅ TRADE ACCEPTED — {symbol} {side} [{priority}]")
        lines.append("")
        lines.append("Reasons:")
        lines.append(f"  • Trade Quality Score: {tq_score:.1f}/100")
        lines.append(f"  • Institution Agreement: {inst_agree:.0%}")
        lines.append(f"  • Expected Value: {ev_r:+.3f}R")
        lines.append(f"  • Regime: {regime}")
        lines.append(f"  • Correlation: {'Low' if corr_reduction >= 0.9 else 'Medium' if corr_reduction >= 0.7 else 'High'} ({corr_reduction:.0%})")
        lines.append(f"  • Portfolio Heat: {heat_pct:.1f}%")
        lines.append(f"  • Execution Quality: {self._quality_label(exec_quality)} ({exec_quality:.1f})")
        lines.append(f"  • Risk State: {risk_state}")
        lines.append(f"  • Final Score: {tq_score:.1f}")

        return "\n".join(lines)

    def _explain_rejection(
        self, symbol, side, priority, tq_score, ev_r, inst_agree,
        regime, rr, reasons,
    ) -> str:
        """Generate rejection explanation."""
        lines = []
        lines.append(f"❌ TRADE REJECTED — {symbol} {side}")
        lines.append("")
        lines.append("Rejection Reasons:")

        for reason in reasons:
            # Parse and format reason
            formatted = self._format_reason(reason)
            lines.append(f"  • {formatted}")

        lines.append("")
        lines.append("Metrics:")
        lines.append(f"  • Trade Quality Score: {tq_score:.1f}/100")
        if ev_r != 0:
            lines.append(f"  • Expected Value: {ev_r:+.3f}R")
        lines.append(f"  • Institution Agreement: {inst_agree:.0%}")
        lines.append(f"  • Regime: {regime}")
        if rr > 0:
            lines.append(f"  • Risk/Reward: {rr:.2f}")

        return "\n".join(lines)

    @staticmethod
    def _format_reason(reason: str) -> str:
        """Format a rejection reason into human-readable text."""
        reason_map = {
            "institution_rejected": "Institutional data does not agree with trade direction",
            "negative_ev": "Expected value is negative — trade has losing expectancy",
            "regime_blocked": "Market regime is unfavorable for this trade type",
            "reward_rejected": "Risk/reward ratio below minimum threshold",
            "portfolio_rejected": "Portfolio limits exceeded (positions, correlation, or drawdown)",
            "correlation_rejected": "Too many correlated positions already open",
            "risk_governor_rejected": "Adaptive risk governor has paused trading (consecutive losses)",
            "sizing_rejected": "Position sizing below minimum threshold",
            "execution_quality_rejected": "Execution quality too poor (spread, slippage, or liquidity)",
        }

        for key, text in reason_map.items():
            if reason.startswith(key):
                # Extract detail if present
                detail = reason.split(": ", 1)[1] if ": " in reason else ""
                return f"{text}" + (f" ({detail})" if detail else "")

        return reason

    @staticmethod
    def _quality_label(score: float) -> str:
        """Convert quality score to label."""
        if score >= 80:
            return "Excellent"
        elif score >= 60:
            return "Good"
        elif score >= 40:
            return "Average"
        elif score >= 20:
            return "Poor"
        else:
            return "Very Poor"
