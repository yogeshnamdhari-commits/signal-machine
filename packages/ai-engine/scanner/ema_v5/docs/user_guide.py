"""
EMA_V5 User Guide — End-user documentation for traders.
Isolated from existing documentation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5UserGuide:
    """Generates user guide documentation for EMA_V5."""

    def generate(self) -> Dict[str, Any]:
        """Generate complete user guide."""
        return {
            "version": "1.0.0",
            "introduction": self._introduction(),
            "getting_started": self._getting_started(),
            "dashboard": self._dashboard_guide(),
            "signals": self._signal_guide(),
            "reports": self._report_guide(),
            "settings": self._settings_guide(),
            "faq": self._faq(),
        }

    def _introduction(self) -> Dict[str, Any]:
        """Introduction section."""
        return {
            "title": "EMA V5 Strategy — User Guide",
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
            "steps": [
                {
                    "step": 1,
                    "title": "Dashboard Access",
                    "description": "Access the EMA V5 Scanner page from the Streamlit dashboard sidebar",
                },
                {
                    "step": 2,
                    "title": "Monitor Signals",
                    "description": "Watch for BUY/SELL signals in the Live Signal Table",
                },
                {
                    "step": 3,
                    "title": "Review Details",
                    "description": "Click a signal to see full details including EMA values, confidence breakdown, and reason checklist",
                },
                {
                    "step": 4,
                    "title": "Track Performance",
                    "description": "Monitor your performance in the Analytics and Reports sections",
                },
            ],
        }

    def _dashboard_guide(self) -> Dict[str, Any]:
        """Dashboard guide section."""
        return {
            "title": "Dashboard Guide",
            "sections": {
                "summary_cards": {
                    "description": "Top-level metrics",
                    "cards": [
                        "Scanner Status: Running/Stopped",
                        "Symbols Scanned: Total symbols evaluated",
                        "BUY MODE: Symbols in bullish trend",
                        "SELL MODE: Symbols in bearish trend",
                        "Waiting Pullback: Symbols waiting for pullback",
                        "Waiting Confirmation: Symbols waiting for candle confirmation",
                        "BUY Signals: Active long signals",
                        "SELL Signals: Active short signals",
                        "Average Confidence: Mean confidence score",
                        "Last Scan: Timestamp of last scan",
                    ],
                },
                "state_visualization": {
                    "description": "Visual representation of symbol states",
                    "states": [
                        "NO_TREND: No trend detected (black)",
                        "BUY_MODE: Bullish trend (green)",
                        "SELL_MODE: Bearish trend (red)",
                        "WAITING_PULLBACK: Waiting for pullback (yellow)",
                        "WAITING_CONFIRMATION: Waiting for confirmation (blue)",
                        "ACTIVE_BUY: Active long position (green check)",
                        "ACTIVE_SELL: Active short position (red check)",
                        "TRADE_CLOSED: Trade closed (gray)",
                    ],
                },
                "signal_table": {
                    "description": "Live signal table with all signal data",
                    "columns": [
                        "Time, Date, Exchange, Symbol",
                        "Side (BUY/SELL), Trend, Current State",
                        "EMA20, EMA50, EMA144, EMA200",
                        "Entry, Stop Loss, TP1, TP2, TP3",
                        "Confidence, Volume, Reason, Status",
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

    def _signal_guide(self) -> Dict[str, Any]:
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

    def _report_guide(self) -> Dict[str, Any]:
        """Report guide section."""
        return {
            "title": "Reports",
            "report_types": {
                "daily": "Day-level performance report",
                "weekly": "Week-level performance report",
                "monthly": "Month-level performance report",
                "custom": "Flexible date range and filter-based reports",
            },
            "metrics_explained": {
                "win_rate": "Percentage of winning trades",
                "profit_factor": "Gross profit / Gross loss",
                "expectancy": "Average profit per trade",
                "max_drawdown": "Maximum peak-to-trough decline",
                "sharpe_ratio": "Risk-adjusted return",
            },
        }

    def _settings_guide(self) -> Dict[str, Any]:
        """Settings guide section."""
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
            ],
        }
