#!/usr/bin/env python3
"""
Validation Integration — Hooks into the live EMA_V5 scanner.

This module provides async functions that can be called from the engine
to automatically track paper trades. It does NOT modify any existing code.

Usage (in engine scan loop):
    from validation.integration import ValidationHooks
    hooks = ValidationHooks()
    
    # After scanner emits a signal:
    await hooks.on_signal(signal)
    
    # On price update:
    await hooks.on_price_update(symbol, bid, ask)
    
    # When trade closes:
    await hooks.on_trade_closed(symbol, exit_price, reason)
    
    # Daily report (call at midnight or manually):
    await hooks.daily_report()
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Any
from loguru import logger

from .validation_engine import ValidationEngine, ReportGenerator


class ValidationHooks:
    """
    Async integration hooks for the live scanner.
    
    Drop-in replacement: call these from the engine without modifying
    any signal logic, indicator calculations, or exit management.
    """

    _instance: Optional["ValidationHooks"] = None

    def __new__(cls) -> "ValidationHooks":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self.engine = ValidationEngine()
        self.trader = self.engine.trader
        self.reporter = self.engine.reporter
        self._last_report_date = ""
        self._enabled = True
        logger.info("✅ ValidationHooks initialized (paper trading active)")

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable paper trading (e.g., during maintenance)."""
        self._enabled = enabled
        logger.info("Paper trading {}", "ENABLED" if enabled else "DISABLED")

    # ── Signal Hook ──

    async def on_signal(self, signal: Dict[str, Any]) -> Optional[int]:
        """
        Called when the EMA_V5 scanner emits a signal.
        
        signal dict must contain: symbol, side, entry, sl, tp1/tp, rr, conf/confidence, regime, score
        Returns: signal_id (for tracking) or None if skipped.
        """
        if not self._enabled:
            return None

        try:
            # Run in thread pool to avoid blocking async loop
            signal_id = await asyncio.get_event_loop().run_in_executor(
                None, self.trader.on_signal, signal
            )
            return signal_id
        except Exception as e:
            logger.error("ValidationHooks.on_signal error: {}", e)
            return None

    # ── Price Update Hook ──

    async def on_price_update(self, symbol: str, bid: float, ask: float) -> Optional[Dict]:
        """
        Called on every price tick for active paper positions.
        Returns exit result if trade was closed, None otherwise.
        """
        if not self._enabled:
            return None

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self.trader.on_price_update, symbol, bid, ask
            )
            return result
        except Exception as e:
            logger.error("ValidationHooks.on_price_update error for {}: {}", symbol, e)
            return None

    # ── Trade Closed Hook ──

    async def on_trade_closed(self, symbol: str, exit_price: float, reason: str) -> Optional[Dict]:
        """
        Called when the signal engine closes a trade.
        reason: SL, TP1, TP2, TP3, TIME, MANUAL, etc.
        """
        if not self._enabled:
            return None

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, self.trader.on_trade_closed, symbol, exit_price, reason
            )
            return result
        except Exception as e:
            logger.error("ValidationHooks.on_trade_closed error for {}: {}", symbol, e)
            return None

    # ── Daily Report ──

    async def daily_report(self, period: str = "daily") -> Optional[Dict]:
        """
        Generate validation report for any period.
        period: daily, weekly, monthly, all
        Also checks for deviation alerts and promotions.
        """
        try:
            report = await asyncio.get_event_loop().run_in_executor(
                None, self.engine.report, period
            )

            # Check deviations
            if report.get("alerts"):
                for alert in report["alerts"]:
                    if alert["severity"] == "critical":
                        logger.warning("🔴 CRITICAL ALERT: {}", alert["message"])
                    else:
                        logger.info("🟡 WARNING: {}", alert["message"])

            # Check promotion
            await asyncio.get_event_loop().run_in_executor(
                None, self.engine.check_promotion
            )

            return report
        except Exception as e:
            logger.error("ValidationHooks.daily_report error: {}", e)
            return None

    async def weekly_report(self) -> Optional[Dict]:
        """Generate weekly validation report."""
        return await self.daily_report("weekly")

    async def monthly_report(self) -> Optional[Dict]:
        """Generate monthly validation report."""
        return await self.daily_report("monthly")

    async def portfolio_report(self) -> Optional[Dict]:
        """Generate full portfolio report (all time)."""
        return await self.daily_report("all")

    # ── Auto-daily Report ──

    async def maybe_daily_report(self) -> None:
        """
        Automatically generate daily report at midnight (or first call after midnight).
        Safe to call from the main scan loop.
        """
        today = time.strftime("%Y-%m-%d")
        if today != self._last_report_date:
            report = await self.daily_report()
            if report and report.get("status") == "OK":
                self._last_report_date = today
                logger.info("📊 Daily report generated for {}", today)

    # ── Status ──

    def get_status(self) -> Dict:
        """Get current validation status for dashboard."""
        return {
            "enabled": self._enabled,
            "phase": self.trader._phase,
            "capital": self.trader.capital,
            "equity": self.engine.get_status(),
        }
