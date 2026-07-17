"""
EMA_V5 Message Formatter — Formats messages for Telegram notification.
Isolated from existing formatters. Uses HTML formatting.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger


class EMAv5MessageFormatter:
    """Formats EMA_V5 messages for Telegram."""

    @staticmethod
    def signal_message(signal: Dict[str, Any]) -> str:
        """Format a signal notification message."""
        side = signal.get("side", "?")
        symbol = signal.get("symbol", "?")
        entry = signal.get("entry", 0)
        sl = signal.get("sl", 0)
        tp1 = signal.get("take_profit_1", 0)
        tp2 = signal.get("take_profit_2", 0)
        tp3 = signal.get("take_profit_3", 0)
        confidence = signal.get("confidence", 0) * 100
        regime = signal.get("regime", "?")
        rr = signal.get("rr_1", 0)

        # Emoji
        side_emoji = "🟢" if side == "LONG" else "🔴"
        conf_emoji = "🟢" if confidence >= 95 else "🟡" if confidence >= 90 else "🔴"

        # Price formatting
        def _p(v):
            if v is None or v == 0: return "—"
            if v >= 100: return f"{v:.2f}"
            if v >= 1: return f"{v:.4f}"
            if v >= 0.01: return f"{v:.5f}"
            return f"{v:.6f}"

        text = f"""<b>{side_emoji} EMA_V5 {side} SIGNAL</b>

<b>📊 Symbol:</b> {symbol}
<b>💰 Entry:</b> {_p(entry)}
<b>🛑 Stop Loss:</b> {_p(sl)}
<b>🎯 TP1:</b> {_p(tp1)}
<b>🎯 TP2:</b> {_p(tp2)}
<b>🎯 TP3:</b> {_p(tp3)}

<b>📈 R:R:</b> {rr:.2f}
<b>{conf_emoji} Confidence:</b> {confidence:.1f}%
<b>📊 Regime:</b> {regime}

<b>⏰ Time:</b> {datetime.now(timezone.utc).strftime("%H:%M:%S UTC")}"""

        # Add EMA data if available
        ema = signal.get("ema_data", {})
        if ema:
            text += f"\n\n<b>📐 EMA:</b> 20={_p(ema.get('ema20', 0))} 50={_p(ema.get('ema50', 0))}"

        text += "\n\n<i>EMA_V5 Strategy • DeltaTerminal</i>"
        return text

    @staticmethod
    def exit_message(exit_data: Dict[str, Any]) -> str:
        """Format an exit notification message."""
        symbol = exit_data.get("symbol", "?")
        side = exit_data.get("side", "?")
        entry = exit_data.get("entry_price", 0)
        exit_price = exit_data.get("exit_price", 0)
        pnl = exit_data.get("pnl", 0)
        reason = exit_data.get("reason", "?")
        hold = exit_data.get("hold_minutes", 0)

        # Emoji
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        reason_emoji = {
            "take_profit_1": "🎯", "take_profit_2": "🎯", "take_profit_3": "🎯",
            "stop_loss": "🛑", "max_hold": "⏰", "breakeven": "⚖️",
        }.get(reason, "📊")

        def _p(v):
            if v is None or v == 0: return "—"
            if v >= 100: return f"{v:.2f}"
            if v >= 1: return f"{v:.4f}"
            return f"{v:.6f}"

        text = f"""<b>{pnl_emoji} EMA_V5 {side} EXIT</b>

<b>📊 Symbol:</b> {symbol}
<b>💰 Entry:</b> {_p(entry)}
<b>📤 Exit:</b> {_p(exit_price)}
<b>💵 PnL:</b> ${pnl:.2f}

<b>{reason_emoji} Reason:</b> {reason.replace('_', ' ').title()}
<b>⏱️ Hold:</b> {hold:.0f} minutes

<b>⏰ Time:</b> {datetime.now(timezone.utc).strftime("%H:%M:%S UTC")}"""
        return text

    @staticmethod
    def error_message(error: str, context: str = "") -> str:
        """Format an error notification message."""
        text = f"""<b>⚠️ EMA_V5 Error</b>

<b>❌ Error:</b> {error}"""

        if context:
            text += f"\n<b>📍 Context:</b> {context}"

        text += f"\n\n<b>⏰ Time:</b> {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        return text

    @staticmethod
    def daily_summary(summary: Dict[str, Any]) -> str:
        """Format a daily summary message."""
        date = summary.get("date", "Today")
        total_trades = summary.get("total_trades", 0)
        wins = summary.get("wins", 0)
        losses = summary.get("losses", 0)
        win_rate = summary.get("win_rate", 0)
        total_pnl = summary.get("total_pnl", 0)
        profit_factor = summary.get("profit_factor", 0)

        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

        text = f"""<b>📊 EMA_V5 Daily Summary</b>
<b>📅 Date:</b> {date}

<b>📈 Performance:</b>
  Trades: {total_trades} ({wins}W / {losses}L)
  Win Rate: {win_rate:.1f}%
  {pnl_emoji} PnL: ${total_pnl:.2f}
  Profit Factor: {profit_factor:.2f}"""

        # Side breakdown
        sides = summary.get("sides", {})
        if sides:
            text += f"\n\n<b>📊 Sides:</b>"
            text += f"\n  LONG: {sides.get('long_trades', 0)} trades, ${sides.get('long_pnl', 0):.2f}"
            text += f"\n  SHORT: {sides.get('short_trades', 0)} trades, ${sides.get('short_pnl', 0):.2f}"

        text += f"\n\n<i>EMA_V5 • DeltaTerminal</i>"
        return text

    @staticmethod
    def weekly_summary(summary: Dict[str, Any]) -> str:
        """Format a weekly summary message."""
        period = summary.get("period", {})
        start = period.get("start", "")
        end = period.get("end", "")
        total_trades = summary.get("total_trades", 0)
        win_rate = summary.get("win_rate", 0)
        total_pnl = summary.get("total_pnl", 0)
        profit_factor = summary.get("profit_factor", 0)

        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

        text = f"""<b>📊 EMA_V5 Weekly Summary</b>
<b>📅 Period:</b> {start} → {end}

<b>📈 Performance:</b>
  Trades: {total_trades}
  Win Rate: {win_rate:.1f}%
  {pnl_emoji} PnL: ${total_pnl:.2f}
  Profit Factor: {profit_factor:.2f}"""

        # Daily breakdown
        daily = summary.get("daily_breakdown", {})
        if daily:
            text += f"\n\n<b>📅 Daily:</b>"
            for day in sorted(daily.keys())[-5:]:
                d = daily[day]
                day_emoji = "🟢" if d.get("pnl", 0) >= 0 else "🔴"
                text += f"\n  {day_emoji} {day}: {d.get('trades', 0)} trades, ${d.get('pnl', 0):.2f}"

        text += f"\n\n<i>EMA_V5 • DeltaTerminal</i>"
        return text

    @staticmethod
    def verification_alert(diag_data: Dict[str, Any]) -> str:
        """Format a verification alert message."""
        verdict = diag_data.get("verdict", "FAIL")
        symbol = diag_data.get("symbol", "?")
        failed = diag_data.get("reasons_failed", [])

        verdict_emoji = {"PASS": "✅", "WARNING": "⚠️", "FAIL": "❌"}.get(verdict, "❓")

        text = f"""<b>{verdict_emoji} EMA_V5 Verification: {verdict}</b>

<b>📊 Symbol:</b> {symbol}"""

        if failed:
            text += f"\n\n<b>❌ Failed Checks:</b>"
            for f in failed[:5]:
                text += f"\n  • {f}"

        text += f"\n\n<b>⏰ Time:</b> {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        return text
