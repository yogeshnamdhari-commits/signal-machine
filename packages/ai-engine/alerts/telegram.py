"""
Telegram Alerts — signal, ranking, position, system notifications.
Rich entry/exit alerts with full trade details.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import httpx
from loguru import logger

# Load .env manually if TELEGRAM env vars not set
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class TelegramAlerts:
    def __init__(self) -> None:
        self.enabled = os.environ.get("TELEGRAM_ENABLED", "false").lower() in ("true", "1", "yes")
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.min_conf = float(os.environ.get("TELEGRAM_MIN_CONFIDENCE", "0.7"))
        self._last_send_time = 0.0
        self._min_interval = 1.0  # 1 second between messages
        self._retry_after = 0.0  # Retry-after timestamp from 429 errors
        self._queue: list = []
        if self.enabled and (not self.token or self.token == "YOUR_BOT_TOKEN_HERE"):
            logger.warning("Telegram enabled but bot_token not set — disabling")
            self.enabled = False
        if self.enabled:
            logger.info("✅ Telegram alerts enabled — chat_id={}", self.chat_id)

    async def send_message(self, text: str) -> None:
        if not self.enabled:
            return
        # Rate limiting: respect minimum interval
        now = time.time()
        if now < self._retry_after:
            logger.debug("Telegram rate limited, retry after {:.0f}s", self._retry_after - now)
            return
        wait = self._min_interval - (now - self._last_send_time)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
                )
                self._last_send_time = time.time()
                if resp.status_code == 429:
                    # Rate limited — extract retry_after
                    try:
                        data = resp.json()
                        retry = data.get("parameters", {}).get("retry_after", 30)
                        self._retry_after = time.time() + retry
                        logger.warning("Telegram 429: retry after {}s", retry)
                    except Exception:
                        self._retry_after = time.time() + 30
                elif resp.status_code != 200:
                    logger.error("Telegram API error {}: {}", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("Telegram send failed: {}", exc)

    # ── Entry Alert ──
    async def send_position_opened(self, pos: Dict) -> None:
        side = pos.get("side", "LONG")
        icon = "🟢" if side == "LONG" else "🔴"
        entry = pos.get("entry_price", 0)
        sl = pos.get("stop_loss", 0)
        tp = pos.get("take_profit", 0)
        qty = pos.get("quantity", 0)
        leverage = pos.get("leverage", 1)
        score = pos.get("score", 0)
        risk_dist = abs(entry - sl) if sl else entry * 0.02
        risk_usd = risk_dist * qty
        pos_val = entry * qty
        rr = abs(tp - entry) / risk_dist if risk_dist > 0 and tp else 0

        msg = (
            f"{'━'*30}\n"
            f"{icon} *NEW POSITION: {side} {pos.get('symbol', '?')}*\n"
            f"{'━'*30}\n\n"
            f"📌 *Entry:* `${entry:,.4f}`\n"
            f"🛑 *Stop Loss:* `${sl:,.4f}` ({risk_dist / entry * 100:.2f}%)\n"
            f"🎯 *Take Profit:* `${tp:,.4f}` ({abs(tp - entry) / entry * 100:.2f}%)\n"
            f"⚖️ *R:R:* `{rr:.1f}x`\n\n"
            f"📦 *Qty:* `{qty:.4f}`\n"
            f"💰 *Position Value:* `${pos_val:,.2f}`\n"
            f"🛡️ *Risk:* `${risk_usd:,.2f}`\n"
            f"📊 *Leverage:* `{leverage}x`\n"
            f"🎯 *Score:* `{score:.0f}`\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        await self.send_message(msg)

    async def send_signal_alert(self, signal: Dict) -> None:
        """Send signal alert — lower threshold to show all signals."""
        s = "🟢" if signal.get("type", signal.get("side", "LONG")) == "LONG" else "🔴"
        side = signal.get("type", signal.get("side", "LONG"))
        symbol = signal.get("symbol", "?")
        confidence = signal.get("confidence", 0)
        inst_score = signal.get("institutional_score", 0)
        entry = signal.get("entry_price", 0)
        sl = signal.get("stop_loss", 0)
        tp = signal.get("take_profit", 0)
        rr = signal.get("risk_reward", 0)
        regime = signal.get("regime", "N/A")
        entry_type = signal.get("entry_type", "market")
        qty = signal.get("quantity", 0)
        leverage = signal.get("leverage", 1)
        # R:R calculation
        risk = abs(entry - sl) if entry and sl else 0
        reward = abs(tp - entry) if tp and entry else 0
        calc_rr = round(reward / risk, 2) if risk > 0 else rr
        # Session / intraday info
        intraday = signal.get("intraday", {})
        quality_tier = intraday.get("quality_tier", "")
        session = intraday.get("session", "")
        session_labels = {"overlap": "🌍 EU/US", "us": "🇺🇸 US", "european": "🇬🇧 EU", "asian": "🇯🇵 Asian", "off": "🌙 Off"}
        session_str = session_labels.get(session, session)
        tier_icons = {"A": "⭐", "B": "🔶", "C": ""}
        tier_str = f"{tier_icons.get(quality_tier, '')} {quality_tier}-TIER" if quality_tier else ""
        # Build message
        msg = (
            f"{'━'*30}\n"
            f"{s} *NEW {side} — {symbol}*\n"
            f"{'━'*30}\n\n"
            f"💰 *Entry:* `${entry:,.4f}`\n"
            f"🛑 *SL:* `${sl:,.4f}`\n"
            f"🎯 *TP:* `${tp:,.4f}`\n"
            f"⚖️ *R:R:* `{calc_rr:.1f}x`\n\n"
            f"📊 Score: `{inst_score:.1f}/100` | Conf: `{confidence:.0%}`\n"
            f"📍 Type: `{entry_type}` | Leverage: `{leverage}x`\n"
        )
        if qty > 0:
            risk_usd = abs(entry - sl) * qty if risk > 0 else 0
            msg += f"📐 Qty: `{qty:.4f}` | Risk: `${risk_usd:,.2f}`\n"
        msg += (
            f"🌀 Regime: `{regime}`\n"
        )
        if tier_str or session_str:
            msg += f"{tier_str} {session_str}\n"
        msg += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        await self.send_message(msg)

    async def send_ranking_update(self, signals: List[Dict]) -> None:
        if not signals:
            return
        lines = ["📊 *TOP SIGNALS*\n"]
        for i, s in enumerate(signals[:10], 1):
            e = "🥇🥈🥉"[i - 1] if i <= 3 else f"#{i}"
            icon = "🟢" if s.get("type") == "LONG" else "🔴"
            lines.append(f"{e} {icon} *{s['symbol']}* — {s['confidence']:.0%}")
        await self.send_message("\n".join(lines))

    async def send_position_closed(self, pos: Dict, exit_price: float, pnl: float, reason: str) -> None:
        """Send detailed exit alert with PnL breakdown."""
        icon = "✅" if pnl > 0 else "❌"
        symbol = pos.get("symbol", "?")
        side = pos.get("side", "LONG")
        s = "🟢" if side == "LONG" else "🔴"
        entry = pos.get("entry_price", 0)
        qty = pos.get("quantity", 0)
        sl = pos.get("stop_loss", 0)
        tp = pos.get("take_profit", 0)
        leverage = pos.get("leverage", 1)
        # Compute P&L %
        pos_value = entry * qty if entry and qty else 1
        pnl_pct = (pnl / pos_value * 100) if pos_value > 0 else 0
        # R-multiple
        risk_dist = abs(entry - sl) if entry and sl else 0
        if risk_dist > 0:
            if side == "LONG":
                r_mult = (exit_price - entry) / risk_dist
            else:
                r_mult = (entry - exit_price) / risk_dist
        else:
            r_mult = 0
        # Reason emoji
        reason_emojis = {
            "stop_loss": "🛑", "take_profit": "🎯",
            "trailing_stop": "📉", "breakeven_stop": "🛡️",
            "partial_profit": "💰", "time_exit": "⏰",
            "time_exit_4h": "⏰", "end_of_data": "📊",
        }
        reason_icon = reason_emojis.get(reason, "📋")
        # Hold time
        opened_at = pos.get("opened_at", 0)
        if opened_at:
            hold_sec = time.time() - opened_at
            if hold_sec < 60:
                hold_str = f"{int(hold_sec)}s"
            elif hold_sec < 3600:
                hold_str = f"{int(hold_sec / 60)}m"
            else:
                hold_str = f"{int(hold_sec / 3600)}h {int((hold_sec % 3600) / 60)}m"
        else:
            hold_str = "?"
        msg = (
            f"{'━'*30}\n"
            f"{icon} *POSITION CLOSED — {symbol}*\n"
            f"{'━'*30}\n\n"
            f"{s} *{side}* | {leverage}x Leverage\n\n"
            f"💰 *Entry:* `${entry:,.4f}`\n"
            f"📍 *Exit:* `${exit_price:,.4f}`\n"
            f"🛑 *SL:* `${sl:,.4f}` | 🎯 *TP:* `${tp:,.4f}`\n\n"
            f"{'─'*30}\n"
            f"💰 *PnL:* `${pnl:+,.2f}` ({pnl_pct:+.2f}%)\n"
            f"📐 *R-Multiple:* `{r_mult:+.2f}R`\n"
            f"{reason_icon} *Reason:* `{reason}`\n"
            f"⏱️ *Hold:* {hold_str}\n"
            f"{'─'*30}\n\n"
        )
        if pnl > 0:
            msg += f"🟢 *WINNER* — Great trade!"
        else:
            msg += f"🔴 *LOSS* — Review and learn."
        msg += f"\n\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        await self.send_message(msg)

    async def send_system_alert(self, title: str, message: str, level: str = "info") -> None:
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "🚨", "success": "✅"}.get(level, "📢")
        await self.send_message(f"{icon} *{title}*\n{message}")

    async def send_position_summary(self, positions: List[Dict], metrics: Dict) -> None:
        """Send periodic position summary."""
        if not positions:
            return
        total_unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
        n_long = sum(1 for p in positions if p.get("side") == "LONG")
        n_short = len(positions) - n_long
        icon = "🟢" if total_unrealized >= 0 else "🔴"
        msg = (
            f"📊 *POSITION SUMMARY*\n\n"
            f"📍 Open: {len(positions)} (🟢{n_long} / 🔴{n_short})\n"
            f"{icon} *Unrealized:* `${total_unrealized:+,.2f}`\n\n"
        )
        for p in positions[:5]:  # top 5
            sym = p.get("symbol", "?")
            side = p.get("side", "LONG")
            pnl = p.get("unrealized_pnl", 0)
            s_icon = "🟢" if side == "LONG" else "🔴"
            p_icon = "✅" if pnl >= 0 else "❌"
            msg += f"{s_icon} {sym}: `{pnl:+,.2f}` {p_icon}\n"
        if len(positions) > 5:
            msg += f"\n...and {len(positions) - 5} more"
        msg += f"\n⏰ {datetime.now().strftime('%H:%M:%S')}"
        await self.send_message(msg)
