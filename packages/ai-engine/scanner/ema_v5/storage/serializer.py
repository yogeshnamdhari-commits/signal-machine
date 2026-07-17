"""
EMA_V5 Serializer — Canonical signal serialization.
Ensures every signal has a UUID and consistent format before storage.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Dict, Optional


class EMAv5Serializer:
    """Serializes EMA_V5 signals into a canonical storage format."""

    SCHEMA_VERSION = "1.0.0"

    @staticmethod
    def generate_uuid(signal: Dict[str, Any]) -> str:
        """Generate a deterministic UUID from signal content.
        
        Uses content hash for idempotency — same signal always gets same UUID.
        Falls back to random UUID if content is insufficient.
        """
        key_parts = [
            signal.get("symbol", ""),
            signal.get("side", ""),
            str(signal.get("entry", 0)),
            str(signal.get("sl", 0)),
            str(int(signal.get("timestamp", 0))),
        ]
        content = "|".join(key_parts)
        h = hashlib.sha256(content.encode()).hexdigest()[:32]
        return f"emav5-{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    @classmethod
    def serialize_signal(cls, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a raw scanner signal into canonical storage format.
        
        Returns a flat dict matching the ema_v5_signals table schema.
        """
        uuid_val = signal.get("uuid") or cls.generate_uuid(signal)
        ts = signal.get("timestamp", time.time())
        ema = signal.get("ema_data", {})
        components = signal.get("components", {})
        conf_breakdown = components.get("confidence", {})

        return {
            "uuid": uuid_val,
            "timestamp": ts,
            "date": time.strftime("%Y-%m-%d", time.gmtime(ts)),
            "time": time.strftime("%H:%M:%S", time.gmtime(ts)),
            "exchange": "Binance",
            "symbol": signal.get("symbol", ""),
            "side": signal.get("side", ""),
            "trend": components.get("trend", ""),
            "current_state": signal.get("current_state", ""),
            "ema20": ema.get("ema20", 0),
            "ema50": ema.get("ema50", 0),
            "ema144": ema.get("ema144", 0),
            "ema200": ema.get("ema200", 0),
            "entry": signal.get("entry", signal.get("entry_price", 0)),
            "stop_loss": signal.get("sl", signal.get("stop_loss", 0)),
            "tp1": signal.get("take_profit_1", 0),
            "tp2": signal.get("take_profit_2", 0),
            "tp3": signal.get("take_profit_3", 0),
            "volume": signal.get("volume_ok", False),
            "confidence": signal.get("confidence", 0),
            "reason": components.get("regime", ""),
            "pattern": components.get("candle", ""),
            "result": signal.get("result", ""),
            "pnl": signal.get("pnl", 0),
            "hold_time": signal.get("hold_time", 0),
            "strategy_version": signal.get("strategy_version", "ema_v5"),
            "rr_1": signal.get("rr_1", 0),
            "rr_2": signal.get("rr_2", 0),
            "rr_3": signal.get("rr_3", 0),
            "regime": signal.get("regime", ""),
            "session": signal.get("session", "ema_v5"),
            "sl_dist_pct": signal.get("sl_dist_pct", 0),
            "state": signal.get("state", ""),
            "ema_chain_aligned": signal.get("ema_chain_aligned", False),
            "slope_ema20": signal.get("slope_ema20", 0),
            "slope_ema50": signal.get("slope_ema50", 0),
            "pullback_detected": signal.get("pullback_detected", False),
            "pullback_level": signal.get("pullback_level", ""),
            "candle_score": signal.get("candle_score", 0),
            "volume_score": signal.get("volume_score", 0),
            "trend_score": signal.get("trend_score", 0),
            "regime_score": signal.get("regime_score", 0),
            "schema_version": cls.SCHEMA_VERSION,
            "stored_at": time.time(),
        }

    @classmethod
    def serialize_trade_close(cls, signal_uuid: str, close_data: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize trade close data for history storage."""
        return {
            "uuid": signal_uuid,
            "closed_at": close_data.get("closed_at", time.time()),
            "exit_reason": close_data.get("exit_reason", ""),
            "pnl": close_data.get("pnl", 0),
            "hold_minutes": close_data.get("hold_minutes", 0),
            "realized_r": close_data.get("realized_r", 0),
            "mfe_pct": close_data.get("mfe_pct", 0),
            "mae_pct": close_data.get("mae_pct", 0),
            "outcome": close_data.get("outcome", ""),
        }

    @staticmethod
    def generate_signal_id() -> str:
        """Generate a unique short ID for display purposes."""
        return f"EV5-{int(time.time()) % 100000:05d}"
