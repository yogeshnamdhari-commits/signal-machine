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
        pattern: str = "",
        regime_detail: str = "",
        pullback_level: str = "",
        candle_score: float = 0,
        trend_score: float = 0,
        volume_score: float = 0,
        rr_1: float = 0,
        rr_2: float = 0,
        rr_3: float = 0,
    ) -> None:
        """Record a candidate that reached the confidence stage."""
        key = f"{symbol}_{int(time.time())}"
        
        # Tag which virtual thresholds this candidate would pass
        thresholds = {}
        for t in [40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60, 65, 70]:
            thresholds[f"pass_{t}"] = confidence >= t
        
        self._pending[key] = {
            "timestamp": time.time(),
            "symbol": symbol,
            "side": side,
            "confidence": round(confidence, 2),
            "regime": regime,
            "regime_detail": regime_detail,
            "pattern": pattern,
            "pullback_level": pullback_level,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "rr_1": round(rr_1, 2),
            "rr_2": round(rr_2, 2),
            "rr_3": round(rr_3, 2),
            "session": session,
            "trend_score": trend_score,
            "candle_score": candle_score,
            "volume_score": volume_score,
            "components": components or {},
            "published": False,
            "price_1h": None,
            "price_4h": None,
            "price_24h": None,
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
            "outcome": None,
            "exit_reason": None,
            "hold_minutes": 0,
            "virtual_thresholds": thresholds,
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
        """Update pending candidates with current prices and compute MFE/MAE."""
        now = time.time()
        to_flush = []

        for key, data in list(self._pending.items()):
            sym = data["symbol"]
            age_hours = (now - data["timestamp"]) / 3600
            age_minutes = age_hours * 60

            if sym in price_map:
                price = price_map[sym]
                entry = data["entry_price"]

                if entry <= 0:
                    continue

                # Record price at different time horizons
                if data["price_1h"] is None and age_hours >= 1:
                    data["price_1h"] = price
                if data["price_4h"] is None and age_hours >= 4:
                    data["price_4h"] = price
                if data["price_24h"] is None and age_hours >= 24:
                    data["price_24h"] = price

                # Compute MFE/MAE as percentage
                if data["side"] == "LONG":
                    pnl_pct = (price - entry) / entry * 100
                else:
                    pnl_pct = (entry - price) / entry * 100

                data["mfe_pct"] = max(data["mfe_pct"], pnl_pct)
                data["mae_pct"] = min(data["mae_pct"], pnl_pct)
                data["hold_minutes"] = round(age_minutes, 1)

                # Determine outcome
                if data["outcome"] is None:
                    if data["side"] == "LONG":
                        if price <= data["stop_loss"]:
                            data["outcome"] = "sl_hit"
                            data["exit_reason"] = "stop_loss"
                        elif price >= data["take_profit"]:
                            data["outcome"] = "tp_hit"
                            data["exit_reason"] = "take_profit"
                    else:  # SHORT
                        if price >= data["stop_loss"]:
                            data["outcome"] = "sl_hit"
                            data["exit_reason"] = "stop_loss"
                        elif price <= data["take_profit"]:
                            data["outcome"] = "tp_hit"
                            data["exit_reason"] = "take_profit"

            # Flush after 24 hours or if outcome determined
            if age_hours >= 24 or (data["outcome"] and age_hours >= 1):
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
