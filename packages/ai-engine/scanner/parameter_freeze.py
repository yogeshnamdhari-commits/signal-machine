"""
Parameter Freeze — Lock strategy parameters during validation.

During the validation phase (Phase 1-3), no strategy parameters should change.
This module snapshots parameters at freeze time and detects any drift.

Usage:
    from scanner.parameter_freeze import ParameterFreeze
    
    freeze = ParameterFreeze()
    
    # At freeze time (start of validation):
    freeze.freeze()
    
    # At any point (e.g., after restart):
    status = freeze.check()
    if not status["frozen"]:
        print("Parameters not frozen!")
    elif not status["clean"]:
        print(f"Parameters changed: {status['drift']}")
"""

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional

# Ensure packages/ai-engine is on the path when run as a script
_AI_ROOT = Path(__file__).resolve().parent.parent
if str(_AI_ROOT) not in sys.path:
    sys.path.insert(0, str(_AI_ROOT))

_FREEZE_PATH = _AI_ROOT / "data" / "parameter_freeze.json"


def _snapshot_parameters() -> Dict[str, Any]:
    """Capture all strategy parameters as a snapshot."""
    try:
        from scanner.ema_v5.config import (
            EMAConfig, TrendConfig, TrendMaturityConfig, PullbackConfig,
            CandleConfig, VolumeConfig, ConfidenceConfig, SignalConfig,
            TradeConfig, StateConfig, CacheConfig
        )
        from config.settings import config
        
        snapshot = {
            "timestamp": time.time(),
            "ema": {
                "fast": EMAConfig().fast,
                "medium": EMAConfig().medium,
                "institutional": EMAConfig().institutional,
                "long_term": EMAConfig().long_term,
                "slope_lookback": EMAConfig().slope_lookback,
                "min_candles": EMAConfig().min_candles,
            },
            "trend": {
                "ema_chain_tolerance": TrendConfig().ema_chain_tolerance,
                "slope_threshold": TrendConfig().slope_threshold,
                "min_confirmation_bars": TrendConfig().min_confirmation_bars,
            },
            "trend_maturity": {
                "max_ema200_distance_atr": TrendMaturityConfig().max_ema200_distance_atr,
                "max_consecutive_candles": TrendMaturityConfig().max_consecutive_candles,
                "distance_weight": TrendMaturityConfig().distance_weight,
                "slope_weight": TrendMaturityConfig().slope_weight,
                "consecutive_weight": TrendMaturityConfig().consecutive_weight,
            },
            "pullback": {
                "touch_tolerance_pct": PullbackConfig().touch_tolerance_pct,
                "max_pullback_pct": PullbackConfig().max_pullback_pct,
                "require_bounce": PullbackConfig().require_bounce,
            },
            "candle": {
                "body_ratio_min": CandleConfig().body_ratio_min,
                "wick_ratio_min": CandleConfig().wick_ratio_min,
                "confirmation_close": CandleConfig().confirmation_close,
            },
            "volume": {
                "sma_period": VolumeConfig().sma_period,
                "min_volume_ratio": VolumeConfig().min_volume_ratio,
                "volume_surge_ratio": VolumeConfig().volume_surge_ratio,
            },
            "confidence": {
                "min_confidence": ConfidenceConfig().min_confidence,
                "trend_weight": ConfidenceConfig().trend_weight,
                "pullback_weight": ConfidenceConfig().pullback_weight,
                "candle_weight": ConfidenceConfig().candle_weight,
                "volume_weight": ConfidenceConfig().volume_weight,
                "regime_weight": ConfidenceConfig().regime_weight,
                "maturity_weight": ConfidenceConfig().maturity_weight,
                "session_penalty_ny": ConfidenceConfig().session_penalty_ny,
            },
            "signal": {
                "min_rr": SignalConfig().min_rr,
                "sl_atr_mult": SignalConfig().sl_atr_mult,
                "tp1_rr": SignalConfig().tp1_rr,
                "tp2_rr": SignalConfig().tp2_rr,
                "tp3_rr": SignalConfig().tp3_rr,
                "tp1_exit_pct": SignalConfig().tp1_exit_pct,
                "tp2_exit_pct": SignalConfig().tp2_exit_pct,
                "tp3_exit_pct": SignalConfig().tp3_exit_pct,
            },
            "trade": {
                "risk_per_trade_pct": TradeConfig().risk_per_trade_pct,
                "max_positions": TradeConfig().max_positions,
                "max_hold_hours": TradeConfig().max_hold_hours,
                "breakeven_at_r": TradeConfig().breakeven_at_r,
                "trailing_atr_mult": TradeConfig().trailing_atr_mult,
            },
            "settings": {
                "max_symbols": getattr(config.scanner, "max_symbols", 250),
                "min_volume_24h": getattr(config.scanner, "min_volume_24h", 2_000_000),
            },
        }
        return snapshot
    except Exception as e:
        return {"error": str(e)}


def _compute_hash(snapshot: Dict[str, Any]) -> str:
    """Compute a stable hash of the parameter snapshot."""
    # Remove timestamp before hashing
    s = {k: v for k, v in snapshot.items() if k != "timestamp"}
    canonical = json.dumps(s, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class ParameterFreeze:
    """Manages strategy parameter freezing during validation."""
    
    def __init__(self):
        self._freeze_path = _FREEZE_PATH
    
    def freeze(self) -> Dict[str, Any]:
        """Freeze current parameters. Returns freeze record."""
        snapshot = _snapshot_parameters()
        param_hash = _compute_hash(snapshot)
        
        record = {
            "frozen": True,
            "frozen_at": time.time(),
            "frozen_at_human": time.strftime("%Y-%m-%d %H:%M:%S"),
            "param_hash": param_hash,
            "snapshot": snapshot,
            "reason": "Validation phase — parameters locked until baseline complete",
        }
        
        self._freeze_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._freeze_path, "w") as f:
            json.dump(record, f, indent=2, default=str)
        
        return record
    
    def check(self) -> Dict[str, Any]:
        """Check if parameters match the frozen snapshot."""
        if not self._freeze_path.exists():
            return {"frozen": False, "clean": False, "reason": "No freeze file found"}
        
        with open(self._freeze_path) as f:
            frozen = json.load(f)
        
        if not frozen.get("frozen"):
            return {"frozen": False, "clean": False, "reason": "Freeze not active"}
        
        current_snapshot = _snapshot_parameters()
        current_hash = _compute_hash(current_snapshot)
        frozen_hash = frozen.get("param_hash", "")
        
        if current_hash == frozen_hash:
            return {
                "frozen": True,
                "clean": True,
                "frozen_at": frozen.get("frozen_at_human", "unknown"),
                "hash": current_hash,
            }
        else:
            # Find what changed
            drift = self._find_drift(frozen.get("snapshot", {}), current_snapshot)
            return {
                "frozen": True,
                "clean": False,
                "frozen_at": frozen.get("frozen_at_human", "unknown"),
                "frozen_hash": frozen_hash,
                "current_hash": current_hash,
                "drift": drift,
            }
    
    def _find_drift(self, frozen: Dict, current: Dict) -> list:
        """Find specific parameter changes between frozen and current."""
        changes = []
        for section in frozen:
            if section == "timestamp":
                continue
            if section not in current:
                changes.append(f"Section '{section}' removed")
                continue
            if isinstance(frozen[section], dict):
                for param in frozen[section]:
                    if param not in current[section]:
                        changes.append(f"{section}.{param} removed")
                    elif frozen[section][param] != current[section][param]:
                        changes.append(
                            f"{section}.{param}: {frozen[section][param]} → {current[section][param]}"
                        )
        return changes
    
    def unfreeze(self) -> bool:
        """Unfreeze parameters (requires manual confirmation)."""
        if not self._freeze_path.exists():
            return False
        
        with open(self._freeze_path) as f:
            frozen = json.load(f)
        
        frozen["frozen"] = False
        frozen["unfrozen_at"] = time.time()
        frozen["unfrozen_at_human"] = time.strftime("%Y-%m-%d %H:%M:%S")
        
        with open(self._freeze_path, "w") as f:
            json.dump(frozen, f, indent=2, default=str)
        
        return True


if __name__ == "__main__":
    freeze = ParameterFreeze()
    
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "freeze":
        record = freeze.freeze()
        print(f"🔒 Parameters frozen at {record['frozen_at_human']}")
        print(f"   Hash: {record['param_hash']}")
    elif len(sys.argv) > 1 and sys.argv[1] == "unfreeze":
        if freeze.unfreeze():
            print("🔓 Parameters unfrozen")
        else:
            print("No freeze to unfreeze")
    else:
        status = freeze.check()
        if not status["frozen"]:
            print("⚠️  Parameters NOT frozen — run: python3 parameter_freeze.py freeze")
        elif status["clean"]:
            print(f"✅ Parameters frozen and clean (since {status['frozen_at']})")
            print(f"   Hash: {status['hash']}")
        else:
            print(f"❌ Parameters CHANGED since freeze!")
            print(f"   Frozen at: {status['frozen_at']}")
            for change in status.get("drift", []):
                print(f"   → {change}")
