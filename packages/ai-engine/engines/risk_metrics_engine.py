"""
Risk Metrics Engine — Pre-trade risk analysis for every signal.

Computes before a signal becomes a trade:
  - Kelly %          — optimal position size from win rate and avg win/loss
  - Position Size    — USD risk amount and quantity based on account + confidence
  - Risk %           — percentage of account risked on this trade
  - Max Daily Loss   — remaining daily loss budget
  - Portfolio Exposure — total notional exposure as % of account
  - Correlation Exposure — overlap with existing open positions

Feeds into dashboard signal cards for honest risk assessment.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


class RiskMetricsEngine:
    """
    Computes pre-trade risk metrics for a signal.

    Usage:
        engine = RiskMetricsEngine()
        metrics = engine.compute(
            signal=sig,
            balance=10000,
            open_positions=[...],
            backtest_stats={...},
        )
    """

    def __init__(self) -> None:
        pass

    def compute(
        self,
        signal: Dict[str, Any],
        balance: float,
        open_positions: List[Dict[str, Any]],
        backtest_stats: Optional[Dict[str, Any]] = None,
        risk_per_trade_pct: float = 0.5,
        max_daily_loss_pct: float = 5.0,
        max_position_pct: float = 2.0,
        max_open_positions: int = 10,
        daily_pnl: float = 0.0,
        peak_balance: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Compute full risk metrics for a signal.

        Returns dict with:
            kelly_pct, kelly_raw, kelly_half,
            position_size_usd, position_size_pct, position_qty,
            risk_pct, risk_usd, risk_distance_pct,
            max_daily_loss_usd, daily_loss_remaining, daily_loss_used_pct,
            portfolio_exposure_usd, portfolio_exposure_pct, portfolio_utilization,
            correlation_exposure, correlation_symbols, overlap_count,
            rr_ratio, expected_value,
        """
        entry = signal.get("entry_price", 0)
        sl = signal.get("stop_loss", 0)
        tp = signal.get("take_profit", 0)
        inst_score = signal.get("institutional_score", 0)
        confidence = signal.get("confidence", 0)
        sig_type = signal.get("type", "LONG")
        symbol = signal.get("symbol", "")

        # ── Backtest-derived metrics ──
        bt_wr = backtest_stats.get("win_rate", 50) if backtest_stats else 50
        bt_sample = backtest_stats.get("sample_size", 0) if backtest_stats else 0
        bt_er = backtest_stats.get("expected_r", 0) if backtest_stats else 0
        bt_avg_win = backtest_stats.get("avg_win", 0) if backtest_stats else 0
        bt_avg_loss = backtest_stats.get("avg_loss", 1) if backtest_stats else 1

        # ── 1. Kelly Criterion ──
        # Kelly % = W - ((1 - W) / R)
        # W = win rate (0-1), R = avg_win / avg_loss
        wr = bt_wr / 100 if bt_wr else 0.5
        avg_win = bt_avg_win if bt_avg_win > 0 else 0.02  # default 2%
        avg_loss = bt_avg_loss if bt_avg_loss > 0 else 0.01  # default 1%
        rr_ratio = avg_win / avg_loss if avg_loss > 0 else 2.0

        kelly_raw = wr - ((1 - wr) / rr_ratio) if rr_ratio > 0 else 0
        kelly_raw = max(-0.5, min(kelly_raw, 0.5))  # clamp
        kelly_half = kelly_raw / 2  # half-Kelly for safety

        # ── 2. Position Size ──
        # Confidence-scaled sizing multiplier
        effective_score = inst_score if inst_score > 0 else confidence * 100
        if effective_score >= 75:
            size_mult = 1.15
        elif effective_score >= 55:
            size_mult = 1.05
        elif effective_score >= 35:
            size_mult = 0.90
        else:
            size_mult = 0.70

        risk_per_trade = risk_per_trade_pct / 100 * balance * size_mult
        risk_distance = abs(entry - sl) if entry and sl else entry * 0.01
        risk_distance_pct = (risk_distance / entry * 100) if entry else 0

        position_qty = risk_per_trade / risk_distance if risk_distance > 0 else 0
        position_usd = position_qty * entry
        position_pct = (position_usd / balance * 100) if balance > 0 else 0

        # Cap at max position
        max_pos_usd = balance * max_position_pct / 100
        if position_usd > max_pos_usd:
            position_usd = max_pos_usd
            position_qty = max_pos_usd / entry if entry > 0 else 0
            position_pct = max_position_pct

        # ── 3. Risk % ──
        risk_usd = min(risk_per_trade, risk_distance * position_qty)
        risk_pct = (risk_usd / balance * 100) if balance > 0 else 0

        # ── 4. Max Daily Loss ──
        max_daily_usd = balance * max_daily_loss_pct / 100
        daily_remaining = max_daily_usd + daily_pnl  # daily_pnl is negative when losing
        daily_used_pct = ((max_daily_usd - daily_remaining) / max_daily_usd * 100) if max_daily_usd > 0 else 0

        # ── 5. Portfolio Exposure ──
        total_exposure = sum(
            abs(p.get("entry_price", 0) * p.get("quantity", 0))
            for p in open_positions
        )
        new_exposure = total_exposure + position_usd
        exposure_pct = (new_exposure / balance * 100) if balance > 0 else 0
        utilization = (len(open_positions) + 1) / max_open_positions * 100

        # ── 6. Correlation Exposure ──
        # Check overlap with existing positions (same side = additive risk)
        same_side_count = 0
        same_symbols = []
        for p in open_positions:
            p_side = p.get("side", "")
            p_sym = p.get("symbol", "").replace("USDT", "")
            if p_side == sig_type:
                same_side_count += 1
                same_symbols.append(p_sym)
        correlation_pct = (same_side_count / max(len(open_positions), 1) * 100) if open_positions else 0

        # ── 7. Expected Value ──
        # EV = (WR * AvgWin) - ((1-WR) * AvgLoss)
        ev = (wr * avg_win) - ((1 - wr) * avg_loss)

        return {
            # Kelly
            "kelly_pct": round(kelly_half * 100, 1),
            "kelly_raw_pct": round(kelly_raw * 100, 1),
            "kelly_confidence": "high" if bt_sample >= 30 else "medium" if bt_sample >= 10 else "low",
            # Position
            "position_size_usd": round(position_usd, 2),
            "position_size_pct": round(position_pct, 2),
            "position_qty": round(position_qty, 6),
            "sizing_multiplier": round(size_mult, 2),
            # Risk
            "risk_pct": round(risk_pct, 2),
            "risk_usd": round(risk_usd, 2),
            "risk_distance_pct": round(risk_distance_pct, 2),
            # Daily loss
            "max_daily_loss_usd": round(max_daily_usd, 2),
            "daily_loss_remaining": round(max(0, daily_remaining), 2),
            "daily_loss_used_pct": round(min(100, daily_used_pct), 1),
            # Portfolio
            "portfolio_exposure_usd": round(new_exposure, 2),
            "portfolio_exposure_pct": round(exposure_pct, 1),
            "portfolio_utilization": round(utilization, 0),
            # Correlation
            "correlation_exposure": round(correlation_pct, 0),
            "correlation_symbols": same_symbols,
            "correlation_overlap": same_side_count,
            # Derived
            "rr_ratio": round(rr_ratio, 2),
            "expected_value": round(ev, 4),
            "breakeven_required_wr": round(1 / (1 + rr_ratio) * 100, 1) if rr_ratio > 0 else 50,
        }
