"""
Shadow Confidence Tracker — Records candidates and their market outcomes
without changing the live publication threshold.

For each candidate that reaches the confidence stage, records:
- Confidence score
- Symbol, side, regime
- What happened to price after 1h, 4h, 24h
- Whether it would have hit TP or SL

This data is used to calibrate the optimal publication threshold.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional
from loguru import logger


class ShadowConfidenceTracker:
    """Records confidence candidates and tracks their market outcomes."""

    def __init__(self) -> None:
        self._log_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "shadow_confidence.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._pending: Dict[str, Dict] = {}  # symbol → candidate data
        self._recorded_count = 0

    def record_candidate(
        self,
        symbol: str,
        side: str,
        confidence: float,
        regime: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        session: str = "",
        components: Optional[Dict] = None,
    ) -> None:
        """Record a candidate that reached the confidence stage."""
        key = f"{symbol}_{int(time.time())}"
        self._pending[key] = {
            "timestamp": time.time(),
            "symbol": symbol,
            "side": side,
            "confidence": round(confidence, 2),
            "regime": regime,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "session": session,
            "components": components or {},
            "published": False,
            "price_1h": None,
            "price_4h": None,
            "price_24h": None,
            "outcome": None,  # "tp_hit", "sl_hit", "neutral"
        }
        self._recorded_count += 1
        logger.debug("📊 SHADOW: Recorded {} conf={:.1f} {}", symbol, confidence, side)

    def mark_published(self, symbol: str) -> None:
        """Mark a candidate as published."""
        for key, data in self._pending.items():
            if data["symbol"] == symbol and not data["published"]:
                data["published"] = True
                break

    def update_outcomes(self, price_map: Dict[str, float]) -> None:
        """Update pending candidates with current prices."""
        now = time.time()
        to_flush = []

        for key, data in list(self._pending.items()):
            sym = data["symbol"]
            age_hours = (now - data["timestamp"]) / 3600

            if sym in price_map:
                price = price_map[sym]
                entry = data["entry_price"]

                # Record price at different time horizons
                if data["price_1h"] is None and age_hours >= 1:
                    data["price_1h"] = price
                if data["price_4h"] is None and age_hours >= 4:
                    data["price_4h"] = price
                if data["price_24h"] is None and age_hours >= 24:
                    data["price_24h"] = price

                # Determine outcome
                if data["outcome"] is None:
                    if data["side"] == "LONG":
                        if price <= data["stop_loss"]:
                            data["outcome"] = "sl_hit"
                        elif price >= data["take_profit"]:
                            data["outcome"] = "tp_hit"
                    else:  # SHORT
                        if price >= data["stop_loss"]:
                            data["outcome"] = "sl_hit"
                        elif price <= data["take_profit"]:
                            data["outcome"] = "tp_hit"

            # Flush after 24 hours
            if age_hours >= 24:
                to_flush.append(key)

        # Write completed entries to log
        for key in to_flush:
            data = self._pending.pop(key)
            self._write_entry(data)

    def _write_entry(self, data: Dict) -> None:
        """Write a completed entry to the shadow log."""
        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(data) + "\n")
            logger.info(
                "📊 SHADOW: {} conf={:.1f} {} outcome={} published={}",
                data["symbol"], data["confidence"], data["side"],
                data["outcome"] or "pending", data["published"]
            )
        except Exception as e:
            logger.debug("Shadow write failed: {}", e)

    def get_stats(self) -> Dict:
        """Return current tracking statistics."""
        return {
            "pending": len(self._pending),
            "recorded_total": self._recorded_count,
            "log_file": str(self._log_path),
        }


# Singleton
_shadow_tracker = None

def get_shadow_tracker() -> ShadowConfidenceTracker:
    global _shadow_tracker
    if _shadow_tracker is None:
        _shadow_tracker = ShadowConfidenceTracker()
    return _shadow_tracker
