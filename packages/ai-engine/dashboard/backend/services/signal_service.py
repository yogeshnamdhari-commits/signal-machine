"""
Signal Service — Signal intelligence data aggregation.

Integrates with:
- DeltaTerminalEngine (AI scorer, institutional engine)
- DataBridge (signals.json)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger


class SignalService:
    """
    Aggregates signal data for the dashboard panel.
    Tracks signal quality, accuracy, and performance.
    """

    def __init__(self) -> None:
        self._active_signals: List[Dict[str, Any]] = []
        self._historical_signals: List[Dict[str, Any]] = []
        self._total_signals: int = 0
        self._buy_signals: int = 0
        self._sell_signals: int = 0
        self._signal_wins: int = 0
        self._signal_losses: int = 0
        self._total_confidence: float = 0.0
        self._total_quality: float = 0.0
        self._market_regime: str = "unknown"
        self._max_history = 5000

    def record_signal(self, signal: Dict[str, Any]) -> None:
        """Record a new signal."""
        self._total_signals += 1

        side = signal.get("side", "").upper()
        if side == "BUY" or side == "LONG":
            self._buy_signals += 1
        elif side == "SELL" or side == "SHORT":
            self._sell_signals += 1

        confidence = signal.get("confidence", 0)
        self._total_confidence += confidence
        quality = signal.get("quality_score", signal.get("institutional_score", 0))
        self._total_quality += quality

        enriched = {
            **signal,
            "recorded_at": time.time(),
        }
        self._active_signals.append(enriched)
        if len(self._active_signals) > 200:
            self._active_signals = self._active_signals[-100:]

    def close_signal(self, signal_id: str, result: Dict[str, Any]) -> None:
        """Close a signal with result."""
        pnl = result.get("pnl", 0)
        if pnl > 0:
            self._signal_wins += 1
        elif pnl < 0:
            self._signal_losses += 1

        # Move from active to historical
        for sig in self._active_signals:
            if sig.get("signal_id") == signal_id:
                sig.update(result)
                sig["closed_at"] = time.time()
                self._historical_signals.append(sig)
                break

        self._active_signals = [
            s for s in self._active_signals if s.get("signal_id") != signal_id
        ]

        if len(self._historical_signals) > self._max_history:
            self._historical_signals = self._historical_signals[-self._max_history // 2:]

    def set_market_regime(self, regime: str) -> None:
        """Set current market regime."""
        self._market_regime = regime

    def get_signal_panel(self) -> Dict[str, Any]:
        """Get data for the signal intelligence panel."""
        total_closed = self._signal_wins + self._signal_losses
        win_rate = (self._signal_wins / max(total_closed, 1)) * 100
        avg_confidence = (
            self._total_confidence / max(self._total_signals, 1)
        )
        avg_quality = (
            self._total_quality / max(self._total_signals, 1)
        )

        # Signal profit factor
        gross_profit = sum(
            s.get("pnl", 0) for s in self._historical_signals if s.get("pnl", 0) > 0
        )
        gross_loss = abs(sum(
            s.get("pnl", 0) for s in self._historical_signals if s.get("pnl", 0) < 0
        ))
        pf = gross_profit / max(gross_loss, 0.01) if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0
        )

        # Expectancy
        avg_win = gross_profit / max(self._signal_wins, 1)
        avg_loss = gross_loss / max(self._signal_losses, 1)
        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

        return {
            "active_signals": self._active_signals[-20:],
            "recent_signals": self._historical_signals[-50:],
            "total_signals": self._total_signals,
            "buy_signals": self._buy_signals,
            "sell_signals": self._sell_signals,
            "avg_confidence": round(avg_confidence, 4),
            "avg_quality": round(avg_quality, 2),
            "market_regime": self._market_regime,
            "signal_accuracy": round(win_rate, 2),
            "signal_win_rate": round(win_rate, 2),
            "signal_pf": round(pf, 4) if pf != float("inf") else 999.99,
            "signal_expectancy": round(expectancy, 4),
            "timestamp": time.time(),
        }

    def get_signal_sources(self) -> Dict[str, int]:
        """Get signal counts by source."""
        sources: Dict[str, int] = {}
        for sig in self._active_signals + self._historical_signals:
            source = sig.get("source", sig.get("signal_source", "unknown"))
            sources[source] = sources.get(source, 0) + 1
        return sources
