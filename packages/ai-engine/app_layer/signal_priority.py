"""
Signal Priority Classifier — Elite/High/Medium/Low/Reject classification.

READ-ONLY with respect to upstream data. Never modifies signals.

Per Master Directive:
    "Every signal must receive: Priority Score — Elite, High, Medium, Low, Reject.
     Only Elite and High may execute."

Classification Rules:
    ELITE:   TQ >= 85 AND Institution Agreement >= 70% AND R:R >= 2.5
    HIGH:    TQ >= 75 AND Institution Agreement >= 60% AND R:R >= 2.0
    MEDIUM:  TQ >= 60 AND Institution Agreement >= 50% (informational only)
    LOW:     TQ >= 45 (informational only)
    REJECT:  Everything else
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

from scanner.ema_v5.rr_audit import get_rr_audit


@dataclass
class PriorityResult:
    """Priority classification result."""
    symbol: str = ""
    side: str = ""
    priority: str = "REJECT"
    trade_quality_score: float = 0.0
    institution_agreement: float = 0.0
    regime_approved: bool = False
    reward_approved: bool = False
    portfolio_approved: bool = False
    sizing_approved: bool = False
    executable: bool = False    # Only ELITE and HIGH are executable
    rejection_reasons: list = None

    def __init__(self, **kwargs):
        super().__init__()
        self.rejection_reasons = []
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "priority": self.priority,
            "trade_quality_score": round(self.trade_quality_score, 2),
            "institution_agreement": round(self.institution_agreement, 3),
            "regime_approved": self.regime_approved,
            "reward_approved": self.reward_approved,
            "portfolio_approved": self.portfolio_approved,
            "sizing_approved": self.sizing_approved,
            "executable": self.executable,
            "rejection_reasons": self.rejection_reasons,
        }


class SignalPriorityClassifier:
    """
    Classifies signals into priority buckets based on all engine outputs.

    Per Master Directive: Only ELITE and HIGH may execute.
    MEDIUM and LOW are informational only.
    REJECT is blocked.

    READ-ONLY: never modifies upstream data.
    """

    def classify(
        self,
        signal: Dict[str, Any],
        tq_score: float = 0.0,
        inst_agreement: float = 0.0,
        regime_approved: bool = False,
        reward_approved: bool = False,
        portfolio_approved: bool = False,
        sizing_approved: bool = False,
    ) -> PriorityResult:
        """
        Classify a signal into priority bucket.

        Args:
            signal: Original signal dict
            tq_score: Trade Quality composite score (0-100)
            inst_agreement: Institution agreement ratio (0-1)
            regime_approved: Whether regime filter passed
            reward_approved: Whether reward filter passed
            portfolio_approved: Whether portfolio manager approved
            sizing_approved: Whether position sizing approved

        Returns:
            PriorityResult with classification
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        rr = signal.get("risk_reward", 0)

        result = PriorityResult(
            symbol=symbol,
            side=side,
            trade_quality_score=tq_score,
            institution_agreement=inst_agreement,
            regime_approved=regime_approved,
            reward_approved=reward_approved,
            portfolio_approved=portfolio_approved,
            sizing_approved=sizing_approved,
        )

        # ── Collect Rejection Reasons ──
        if not regime_approved:
            result.rejection_reasons.append("regime_blocked")
        if not reward_approved:
            result.rejection_reasons.append("reward_insufficient")
            # ── RR AUDIT: Record reward rejection ──
            try:
                rr_audit = get_rr_audit()
                entry = signal.get("entry_price", signal.get("entry", 0))
                sl = signal.get("stop_loss", signal.get("sl", 0))
                tp1 = signal.get("take_profit", signal.get("take_profit_1", 0))
                # Determine required RR based on priority tier
                rr_required = 2.0  # HIGH tier minimum
                if tq_score >= 85 and inst_agreement >= 0.70:
                    rr_required = 2.5  # ELITE tier minimum
                rr_audit.record_rejection(
                    symbol=symbol,
                    side=side,
                    entry=entry,
                    stop_loss=sl,
                    tp1=tp1,
                    tp2=signal.get("take_profit_2", 0),
                    tp3=signal.get("take_profit_3", 0),
                    atr_value=signal.get("atr", 0),
                    sl_atr_mult=signal.get("sl_atr_mult", 1.5),
                    tp1_rr_mult=signal.get("tp1_rr", 1.5),
                    session=signal.get("session", ""),
                    regime=signal.get("regime", ""),
                    confidence=signal.get("confidence", signal.get("confidence_100", 0)),
                    rr_required=rr_required,
                    rejection_source="signal_priority",
                    rejection_reason=f"reward_insufficient: RR={rr:.2f} < required {rr_required:.1f} (TQ={tq_score:.0f} IA={inst_agreement:.2f})",
                )
            except Exception as e:
                logger.debug("RR_AUDIT: Failed to record priority rejection: {}", e)
        if not portfolio_approved:
            result.rejection_reasons.append("portfolio_blocked")
        if not sizing_approved:
            result.rejection_reasons.append("sizing_rejected")

        # ── Classify ──

        # ELITE: All gates pass + high scores
        if (tq_score >= 85
            and inst_agreement >= 0.70
            and rr >= 2.5
            and regime_approved
            and reward_approved
            and portfolio_approved
            and sizing_approved):
            result.priority = "ELITE"
            result.executable = True

        # HIGH: Most gates pass + good scores
        elif (tq_score >= 75
              and inst_agreement >= 0.60
              and rr >= 2.0
              and regime_approved
              and reward_approved
              and portfolio_approved
              and sizing_approved):
            result.priority = "HIGH"
            result.executable = True

        # MEDIUM: Decent scores but not all gates pass (informational)
        elif tq_score >= 60 and inst_agreement >= 0.50:
            result.priority = "MEDIUM"
            result.executable = False

        # LOW: Below threshold (informational)
        elif tq_score >= 45:
            result.priority = "LOW"
            result.executable = False

        # REJECT
        else:
            result.priority = "REJECT"
            result.executable = False
            if not result.rejection_reasons:
                result.rejection_reasons.append(
                    f"TQ={tq_score:.1f} below minimum threshold"
                )

        logger.info(
            "PRIORITY: {} {} → {} (TQ={:.1f} inst={:.0%} rr={:.2f} exec={})",
            symbol, side, result.priority, tq_score, inst_agreement, rr,
            result.executable,
        )

        return result
