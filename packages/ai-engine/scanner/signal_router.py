"""
Signal Router — Handles filtering and distribution of signals across delivery channels.
"""
from __future__ import annotations

from typing import Dict, Any
from loguru import logger
from database.signal_repository import repo
from alerts.telegram import TelegramAlerts
from core.event_bus import bus


class SignalRouter:
    def __init__(self):
        self.telegram = TelegramAlerts()

    async def route_signal(self, sig: Dict[str, Any]) -> None:
        """
        Processes a raw signal, determines if it's 'Elite', persists it,
        broadcasts the alert, and publishes only elite signals to the live
        execution bus. The execution bus receives a persisted signal id.
        """
        try:
            is_elite = (
                sig.get("confidence", 0) >= 0.85 and
                sig.get("risk_reward", 0) >= 2.0 and
                sig.get("mtf_alignment", 0) >= 4 and
                sig.get("institutional_score", 0) >= 85
            )

            sig_id = await repo.save_signal(sig, is_elite=is_elite)
            sig["id"] = sig_id

            if is_elite:
                logger.info("🔥 ELITE SIGNAL DETECTED: {} {}", sig["symbol"], sig["type"])
                await self.telegram.send_elite_alert(sig)
                await bus.publish("execution_signal", sig)
        except Exception as e:
            logger.error("Failed to route signal: {}", e)

    def _format_telegram_elite(self, sig: Dict[str, Any]) -> str:
        return (
            f"🚨 *ELITE SIGNAL*\n\n"
            f"*Symbol:* {sig['symbol']}\n"
            f"*Side:* {sig['type']}\n"
            f"*Confidence:* {sig['confidence']:.0%}\n"
            f"*Institutional Score:* {sig['institutional_score']:.1f}\n"
            f"*Entry:* {sig['entry_price']}\n"
            f"*Stop:* {sig['stop_loss']}\n"
            f"*Target:* {sig['take_profit']}\n"
            f"*RR:* {sig.get('risk_reward', 0):.2f}\n\n"
            f"*Reasons:*\n"
            f"- Delta Velocity: {sig.get('delta_velocity', 'Stable')}\n"
            f"- CVD Trend: {sig.get('cvd_trend', 'Neutral')}\n"
            f"- OI Flow: {sig.get('oi_direction', 'Mixed')}"
        )