"""
Data Bridge — JSON-based shared state between AI engine and Streamlit dashboard.
Loosely coupled: engine writes, dashboard reads via atomic JSON file.
Supports: signals, metrics, alerts, status, market data, positions, equity history, trade history.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# Use absolute path relative to this file's parent (packages/ai-engine/) so that
# both the engine and the dashboard always write/read the same directory,
# regardless of the process's working directory.
_AI_ROOT = Path(__file__).resolve().parent.parent          # packages/ai-engine/
BRIDGE_DIR = _AI_ROOT / "data" / "bridge"
BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
# Ensure all required bridge subdirectories exist
for _subdir in ["market_data", "smart_money", "alerts", "logs"]:
    (BRIDGE_DIR / _subdir).mkdir(parents=True, exist_ok=True)
(_AI_ROOT / "data" / "logs").mkdir(parents=True, exist_ok=True)
SIGNALS_FILE = BRIDGE_DIR / "signals.json"
METRICS_FILE = BRIDGE_DIR / "metrics.json"
ALERTS_FILE = BRIDGE_DIR / "alerts.json"
STATUS_FILE = BRIDGE_DIR / "status.json"
MARKET_DATA_FILE = BRIDGE_DIR / "market_data.json"
POSITIONS_FILE = BRIDGE_DIR / "positions.json"
EQUITY_HISTORY_FILE = BRIDGE_DIR / "equity_history.json"
TRADE_HISTORY_FILE = BRIDGE_DIR / "trade_history.json"
MARKET_INTEL_FILE = BRIDGE_DIR / "market_intelligence.json"
DATA_QUALITY_FILE = BRIDGE_DIR / "data_quality.json"
FUNNEL_FILE = BRIDGE_DIR / "funnel.json"
EMA_V5_FILE = BRIDGE_DIR / "ema_v5.json"


@dataclass
class EngineStatus:
    running: bool = False
    symbols: int = 0
    signals: int = 0
    alerts: int = 0
    uptime: float = 0
    last_update: float = 0
    ws_connected: bool = False
    halted: bool = False
    halt_reason: str = ""
    # Data freshness (from DataFreshnessEngine)
    freshness_snapshot: Optional[Dict] = None


def _atomic_write(filepath: Path, data: Any) -> None:
    """Write JSON atomically (write to tmp, then replace). Uses os.replace for
    atomicity even when the destination already exists (handles macOS race conditions)."""
    tmp = filepath.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, default=str, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(filepath))
    except Exception as e:
        logger.error("Bridge write error {}: {}", filepath.name, e)


def _safe_read(filepath: Path) -> Any:
    """Read JSON safely, returning default on error."""
    try:
        with open(filepath) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ── Writer (called by AI engine) ────────────────────────────────

class BridgeWriter:
    """Writes engine state to shared JSON files."""

    def __init__(self) -> None:
        self._last_write = 0.0
        self._write_interval = 1.0  # min 1s between writes

    def write_signals(self, signals: List[Dict]) -> None:
        """Write active signals list."""
        _atomic_write(SIGNALS_FILE, {
            "signals": signals,
            "timestamp": time.time(),
            "count": len(signals),
        })

    def write_metrics(self, metrics: Dict) -> None:
        """Write engine metrics."""
        _atomic_write(METRICS_FILE, {
            "metrics": metrics,
            "timestamp": time.time(),
        })

    def write_alerts(self, alerts: List[Dict]) -> None:
        """Write recent alerts."""
        _atomic_write(ALERTS_FILE, {
            "alerts": alerts[-50:],  # keep last 50
            "timestamp": time.time(),
        })

    def write_market_data(self, rows: List[Dict]) -> None:
        """Write enriched market data list for the Live Sheet."""
        _atomic_write(MARKET_DATA_FILE, {
            "rows": rows,
            "timestamp": time.time(),
            "count": len(rows),
        })

    def write_status(self, status: EngineStatus) -> None:
        """Write engine status."""
        _atomic_write(STATUS_FILE, {
            "status": asdict(status),
            "timestamp": time.time(),
        })

    def write_funnel(self, funnel: Dict) -> None:
        """Write signal funnel analytics for dashboard display."""
        _atomic_write(FUNNEL_FILE, {
            "funnel": funnel,
            "timestamp": time.time(),
        })

    def write_death_report(self, death_report: Dict, adaptive_state: Dict) -> None:
        """Write signal death report + adaptive threshold state for dashboard."""
        _atomic_write(BRIDGE_DIR / "death_report.json", {
            "death_report": death_report,
            "adaptive_thresholds": adaptive_state,
            "timestamp": time.time(),
        })

    def write_positions(self, positions: List[Dict]) -> None:
        """Write open positions with live PnL."""
        _atomic_write(POSITIONS_FILE, {
            "positions": positions,
            "timestamp": time.time(),
            "count": len(positions),
        })

    def write_equity_history(self, history: List[Dict]) -> None:
        """Write equity history for charting (last 1000 points)."""
        _atomic_write(EQUITY_HISTORY_FILE, {
            "history": history[-1000:],
            "timestamp": time.time(),
        })

    def write_trade_history(self, trades: List[Dict]) -> None:
        """Write completed trade history (last 500 trades)."""
        sliced = trades[-500:]
        _atomic_write(TRADE_HISTORY_FILE, {
            "trades": sliced,
            "timestamp": time.time(),
            "count": len(sliced),
            "total_count": len(trades),
        })

    def write_market_intelligence(self, intel: Dict) -> None:
        """Write aggregated market intelligence for heatmaps/analytics."""
        _atomic_write(MARKET_INTEL_FILE, {
            "intelligence": intel,
            "timestamp": time.time(),
        })

    def write_smart_money_map(self, rows: List[Dict]) -> None:
        """Write smart money price map — institutional entry levels per symbol."""
        _atomic_write(BRIDGE_DIR / "smart_money_map.json", {
            "rows": rows,
            "timestamp": time.time(),
            "count": len(rows),
        })

    def write_data_quality(self, data: Dict) -> None:
        """Write data quality validation status for dashboard widget."""
        _atomic_write(DATA_QUALITY_FILE, {
            "data_quality": data,
            "timestamp": time.time(),
        })

    def write_engine_health(self, data: Dict) -> None:
        """Write engine health metrics for dashboard widget."""
        _atomic_write(BRIDGE_DIR / "engine_health.json", {
            "engine_health": data,
            "timestamp": time.time(),
        })

    def write_ema_v5(self, data: Dict) -> None:
        """Write EMA_V5 scanner state for dashboard."""
        _atomic_write(EMA_V5_FILE, {
            "ema_v5": data,
            "timestamp": time.time(),
        })

    def write_all(
        self,
        signals: List[Dict],
        metrics: Dict,
        alerts: List[Dict],
        status: EngineStatus,
    ) -> None:
        """Write all state at once."""
        now = time.time()
        if now - self._last_write < self._write_interval:
            return
        self._last_write = now

        self.write_signals(signals)
        self.write_metrics(metrics)
        self.write_alerts(alerts)
        self.write_status(status)


# ── Reader (called by dashboard) ─────────────────────────────────

class BridgeReader:
    """Reads engine state from shared JSON files."""

    def read_signals(self) -> List[Dict]:
        """Read active signals."""
        data = _safe_read(SIGNALS_FILE)
        if not data:
            return []
        # Expire stale data (older than 5 minutes)
        if time.time() - data.get("timestamp", 0) > 300:
            return []
        return data.get("signals", [])

    def read_metrics(self) -> Dict:
        """Read engine metrics."""
        data = _safe_read(METRICS_FILE)
        if not data:
            return {}
        if time.time() - data.get("timestamp", 0) > 300:
            return {}
        return data.get("metrics", {})

    def read_alerts(self) -> List[Dict]:
        """Read recent alerts."""
        data = _safe_read(ALERTS_FILE)
        if not data:
            return []
        if time.time() - data.get("timestamp", 0) > 300:
            return []
        return data.get("alerts", [])

    def read_status(self) -> EngineStatus:
        """Read engine status."""
        data = _safe_read(STATUS_FILE)
        if not data:
            return EngineStatus()
        status_data = data.get("status", {})
        # Mark as disconnected only if stale for 10+ minutes
        if time.time() - data.get("timestamp", 0) > 600:
            status_data["ws_connected"] = False
            status_data["running"] = False
        # Filter to known fields to avoid TypeError on unexpected keys
        known = {f.name for f in EngineStatus.__dataclass_fields__.values()}
        filtered = {k: v for k, v in status_data.items() if k in known}
        return EngineStatus(**filtered)

    def read_funnel(self) -> Dict:
        """Read signal funnel analytics."""
        data = _safe_read(FUNNEL_FILE)
        if not data:
            return {}
        if time.time() - data.get("timestamp", 0) > 120:
            return {}
        return data.get("funnel", {})

    def read_death_report(self) -> Dict:
        """Read signal death report + adaptive threshold state."""
        data = _safe_read(BRIDGE_DIR / "death_report.json")
        if not data:
            return {}
        if time.time() - data.get("timestamp", 0) > 120:
            return {}
        return {
            "death_report": data.get("death_report", {}),
            "adaptive_thresholds": data.get("adaptive_thresholds", {}),
        }

    def read_market_data(self) -> List[Dict]:
        """Read enriched market data (OI, funding, volume, exchange flow)."""
        data = _safe_read(MARKET_DATA_FILE)
        if not data:
            return []
        # Allow slightly longer staleness (5 min) since this is heavier to fetch
        if time.time() - data.get("timestamp", 0) > 600:
            return []
        return data.get("rows", [])

    def read_market_data_freshness(self) -> Dict:
        """Read market data freshness info for dashboard verification."""
        data = _safe_read(MARKET_DATA_FILE)
        if not data:
            return {"fresh": False, "age": 9999, "rows": 0}
        age = time.time() - data.get("timestamp", 0)
        return {
            "fresh": age < 60,
            "age": round(age, 1),
            "rows": len(data.get("rows", [])),
            "timestamp": data.get("timestamp", 0),
            "bridge_time": time.strftime("%H:%M:%S", time.localtime(data.get("timestamp", 0))),
        }

    def read_positions(self) -> List[Dict]:
        """Read open positions with live PnL."""
        data = _safe_read(POSITIONS_FILE)
        if not data:
            return []
        if time.time() - data.get("timestamp", 0) > 300:
            return []
        return data.get("positions", [])

    def read_equity_history(self) -> List[Dict]:
        """Read equity history for charting."""
        data = _safe_read(EQUITY_HISTORY_FILE)
        if not data:
            return []
        return data.get("history", [])

    def read_trade_history(self) -> List[Dict]:
        """Read completed trade history."""
        data = _safe_read(TRADE_HISTORY_FILE)
        if not data:
            return []
        return data.get("trades", [])

    def read_market_intelligence(self) -> Dict:
        """Read aggregated market intelligence."""
        data = _safe_read(MARKET_INTEL_FILE)
        if not data:
            return {}
        return data.get("intelligence", {})

    def read_smart_money_map(self) -> List[Dict]:
        """Read smart money price map — institutional entry levels per symbol."""
        data = _safe_read(BRIDGE_DIR / "smart_money_map.json")
        if not data:
            return []
        return data.get("rows", [])

    def read_ema_v5(self) -> Dict:
        """Read EMA_V5 scanner state."""
        data = _safe_read(EMA_V5_FILE)
        if not data:
            return {}
        # Allow longer staleness (10 min) — scanner may be paused
        if time.time() - data.get("timestamp", 0) > 600:
            return {"_stale": True}
        return data.get("ema_v5", {})

    def read_data_quality(self) -> Dict:
        """Read data quality validation status."""
        data = _safe_read(DATA_QUALITY_FILE)
        if not data:
            return {}
        if time.time() - data.get("timestamp", 0) > 120:
            return {}
        return data.get("data_quality", {})

    def read_alpha_ranking(self) -> Dict:
        """Read alpha ranking data for dashboard."""
        data = _safe_read(BRIDGE_DIR / "alpha_ranking.json")
        if not data:
            return {}
        return data

    def read_all(self) -> Dict[str, Any]:
        """Read all bridge data at once for dashboard initialization."""
        return {
            "signals": self.read_signals(),
            "metrics": self.read_metrics(),
            "alerts": self.read_alerts(),
            "status": self.read_status(),
            "market_data": self.read_market_data(),
            "positions": self.read_positions(),
            "equity_history": self.read_equity_history(),
            "trade_history": self.read_trade_history(),
            "market_intelligence": self.read_market_intelligence(),
            "smart_money_map": self.read_smart_money_map(),
            "data_quality": self.read_data_quality(),
        }


# Singleton instances
writer = BridgeWriter()
reader = BridgeReader()
