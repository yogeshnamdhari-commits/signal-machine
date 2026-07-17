"""
EMA_V5 Final User Documentation — Complete user documentation.
Isolated from existing user documentation systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5FinalUserDocs:
    """Generates complete user documentation."""

    def generate(self) -> Dict[str, Any]:
        """Generate complete user manual."""
        return {
            "title": "EMA V5 Strategy — Final User Manual",
            "version": "1.0.0",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "introduction": self._introduction(),
            "getting_started": self._getting_started(),
            "dashboard": self._dashboard(),
            "signals": self._signals(),
            "analytics": self._analytics(),
            "reports": self._reports(),
            "settings": self._settings(),
            "faq": self._faq(),
        }

    def _introduction(self) -> Dict[str, Any]:
        """Introduction section."""
        return {
            "title": "Introduction",
            "overview": "The EMA V5 Strategy is an institutional-grade trading strategy that uses EMA chain alignment, multi-timeframe analysis, and candlestick patterns to generate high-confidence trading signals.",
            "key_concepts": [
                "EMA Chain: EMA20 > EMA50 > EMA144 > EMA200 (bullish) or reverse (bearish)",
                "Pullback: Price retraces to EMA20 or EMA50 before continuing trend",
                "Confirmation: Candlestick pattern confirms the pullback",
                "Confidence: Weighted score from all components (minimum 90%)",
            ],
        }

    def _getting_started(self) -> Dict[str, Any]:
        """Getting started section."""
        return {
            "title": "Getting Started",
            "prerequisites": [
                "Python 3.10+",
                "Required packages installed",
                "Database initialized",
                "Configuration configured",
            ],
            "steps": [
                {
                    "step": 1,
                    "title": "Install Dependencies",
                    "command": "pip install -r requirements.txt",
                },
                {
                    "step": 2,
                    "title": "Configure Environment",
                    "command": "cp .env.ema_v5.example .env.ema_v5",
                },
                {
                    "step": 3,
                    "title": "Initialize Database",
                    "command": "python -c 'from scanner.ema_v5.storage.database import EMAv5Database; EMAv5Database()'",
                },
                {
                    "step": 4,
                    "title": "Start Engine",
                    "command": "python -m scanner.ema_v5.integration.unified_entry",
                },
                {
                    "step": 5,
                    "title": "Access Dashboard",
                    "command": "streamlit run dashboard/app.py",
                },
            ],
        }

    def _dashboard(self) -> Dict[str, Any]:
        """Dashboard guide section."""
        return {
            "title": "Dashboard Guide",
            "sections": {
                "summary_cards": {
                    "description": "Top-level metrics at a glance",
                    "cards": [
                        "Scanner Status: Running/Stopped",
                        "Symbols Scanned: Total symbols evaluated",
                        "BUY MODE: Symbols in bullish trend",
                        "SELL MODE: Symbols in bearish trend",
                        "BUY Signals: Active long signals",
                        "SELL Signals: Active short signals",
                        "Average Confidence: Mean confidence score",
                        "Last Scan: Timestamp of last scan",
                    ],
                },
                "signal_table": {
                    "description": "Live signal table with all signal data",
                    "features": [
                        "Real-time updates",
                        "Filter by side, state, confidence",
                        "Search by symbol",
                        "Click for details",
                    ],
                },
                "filters": {
                    "description": "Available filters",
                    "filters": [
                        "Side: ALL/LONG/SHORT",
                        "State: ALL/ACTIVE/CLOSED/WAITING",
                        "Confidence: ALL/≥90/≥95",
                        "Time: ALL/Today/This Week",
                        "Symbol: Search by symbol name",
                    ],
                },
            },
        }

    def _signals(self) -> Dict[str, Any]:
        """Signal guide section."""
        return {
            "title": "Understanding Signals",
            "signal_structure": {
                "entry": "The price at which to enter the trade",
                "stop_loss": "The price at which to exit if the trade goes against you",
                "take_profit_1": "First profit target (35% of position)",
                "take_profit_2": "Second profit target (40% of position)",
                "take_profit_3": "Third profit target (25% of position)",
                "confidence": "How confident the strategy is in this signal (90%+)",
                "regime": "The market regime (BUY_MODE or SELL_MODE)",
            },
            "signal_quality": {
                "description": "Signals are verified through 12 checks",
                "checks": [
                    "EMA Alignment: Chain must be properly aligned",
                    "Trend Direction: Must match signal side",
                    "EMA Slopes: Must be positive/negative as expected",
                    "Pullback: Price must have pulled back to EMA",
                    "Candlestick: Pattern must confirm the direction",
                    "Volume: Must be above average",
                    "Confidence: Must meet minimum threshold",
                    "State: State machine must allow transition",
                    "Duplicate: No recent duplicate signals",
                    "R:R: Risk/reward must be favorable",
                    "Price: All prices must be valid",
                    "Lifecycle: Signal metadata must be complete",
                ],
            },
        }

    def _analytics(self) -> Dict[str, Any]:
        """Analytics section."""
        return {
            "title": "Analytics",
            "metrics": {
                "performance": [
                    "Win Rate: Percentage of winning trades",
                    "Profit Factor: Gross profit / Gross loss",
                    "Expectancy: Average profit per trade",
                    "Max Drawdown: Maximum peak-to-trough decline",
                ],
                "risk": [
                    "Sharpe Ratio: Risk-adjusted return",
                    "Sortino Ratio: Downside risk-adjusted return",
                    "Calmar Ratio: Annual return / Max drawdown",
                    "Kelly Criterion: Optimal position sizing",
                ],
            },
        }

    def _reports(self) -> Dict[str, Any]:
        """Reports section."""
        return {
            "title": "Reports",
            "report_types": {
                "daily": "Day-level performance report",
                "weekly": "Week-level performance report",
                "monthly": "Month-level performance report",
                "custom": "Flexible date range and filter-based reports",
            },
            "formats": ["Text", "Markdown", "HTML", "JSON"],
        }

    def _settings(self) -> Dict[str, Any]:
        """Settings section."""
        return {
            "title": "Settings",
            "configurable_parameters": {
                "ema_periods": "EMA20, EMA50, EMA144, EMA200",
                "min_confidence": "Minimum confidence threshold (default: 90%)",
                "sl_atr_mult": "Stop loss ATR multiplier (default: 1.5)",
                "tp1_rr": "TP1 risk/reward ratio (default: 1.5)",
                "tp2_rr": "TP2 risk/reward ratio (default: 3.0)",
                "tp3_rr": "TP3 risk/reward ratio (default: 5.0)",
                "max_positions": "Maximum concurrent positions (default: 3)",
                "risk_per_trade": "Risk per trade as % of balance (default: 1%)",
            },
        }

    def _faq(self) -> Dict[str, Any]:
        """FAQ section."""
        return {
            "title": "Frequently Asked Questions",
            "questions": [
                {
                    "q": "What is the minimum confidence for a signal?",
                    "a": "90% — signals below this threshold are not generated",
                },
                {
                    "q": "How many positions can I have open?",
                    "a": "Maximum 3 concurrent positions (configurable)",
                },
                {
                    "q": "What happens if the engine restarts?",
                    "a": "All state, signals, and trades are recovered automatically",
                },
                {
                    "q": "How do I access the dashboard?",
                    "a": "Open the Streamlit app and navigate to the EMA V5 Scanner page",
                },
                {
                    "q": "Can I customize the strategy parameters?",
                    "a": "Yes, all parameters are in scanner/ema_v5/config.py",
                },
                {
                    "q": "How do I enable Telegram notifications?",
                    "a": "Configure EMAv5TelegramConfig with valid bot token and chat ID",
                },
                {
                    "q": "How do I export data?",
                    "a": "Use the API endpoints /api/v1/export/csv or /api/v1/export/json",
                },
            ],
        }
