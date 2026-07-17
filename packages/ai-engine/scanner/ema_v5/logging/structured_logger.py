"""
EMA_V5 Structured Logger — JSON-formatted structured logging.
Isolated from existing logging systems.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class LogEntry:
    """Single structured log entry."""
    timestamp: float = 0.0
    level: str = "INFO"
    module: str = ""
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(self.timestamp)),
            "level": self.level,
            "module": self.module,
            "message": self.message,
            "data": self.data,
            "context": self.context,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class EMAv5StructuredLogger:
    """JSON-formatted structured logger for EMA_V5."""

    def __init__(self, log_file: str = "data/logs/ema_v5_structured.json",
                 max_entries: int = 10000) -> None:
        self._log_file = Path(log_file)
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        self._entries: List[LogEntry] = []
        self._max_entries = max_entries

    def log(self, level: str, module: str, message: str,
            data: Optional[Dict] = None, context: Optional[Dict] = None) -> None:
        """Log a structured entry."""
        entry = LogEntry(
            timestamp=time.time(),
            level=level,
            module=module,
            message=message,
            data=data or {},
            context=context or {},
        )
        self._entries.append(entry)

        # Trim if over limit
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

        # Also log to file
        self._write_to_file(entry)

        # Log to loguru
        log_msg = f"[{module}] {message}"
        if data:
            log_msg += f" {json.dumps(data, default=str)}"
        logger.log(level, log_msg)

    def info(self, module: str, message: str, **kwargs) -> None:
        """Log INFO level."""
        self.log("INFO", module, message, kwargs)

    def warning(self, module: str, message: str, **kwargs) -> None:
        """Log WARNING level."""
        self.log("WARNING", module, message, kwargs)

    def error(self, module: str, message: str, **kwargs) -> None:
        """Log ERROR level."""
        self.log("ERROR", module, message, kwargs)

    def debug(self, module: str, message: str, **kwargs) -> None:
        """Log DEBUG level."""
        self.log("DEBUG", module, message, kwargs)

    def signal(self, symbol: str, side: str, entry: float, **kwargs) -> None:
        """Log a signal event."""
        self.log("INFO", "signal", f"{side} {symbol} @ {entry}",
                 data={"symbol": symbol, "side": side, "entry": entry, **kwargs})

    def trade(self, symbol: str, action: str, pnl: float = 0, **kwargs) -> None:
        """Log a trade event."""
        self.log("INFO", "trade", f"{action} {symbol} pnl={pnl}",
                 data={"symbol": symbol, "action": action, "pnl": pnl, **kwargs})

    def error_event(self, module: str, error: str, context: Optional[Dict] = None) -> None:
        """Log an error event."""
        self.log("ERROR", module, error, context=context)

    def _write_to_file(self, entry: LogEntry) -> None:
        """Append entry to log file."""
        try:
            with open(self._log_file, "a") as f:
                f.write(entry.to_json() + "\n")
        except Exception:
            pass  # Don't crash on log write failure

    def get_entries(self, n: int = 100, level: Optional[str] = None,
                    module: Optional[str] = None) -> List[Dict]:
        """Get recent log entries with optional filters."""
        entries = self._entries

        if level:
            entries = [e for e in entries if e.level == level]
        if module:
            entries = [e for e in entries if e.module == module]

        return [e.to_dict() for e in entries[-n:]]

    def get_count(self, level: Optional[str] = None) -> int:
        """Get log entry count."""
        if level:
            return sum(1 for e in self._entries if e.level == level)
        return len(self._entries)

    def clear(self) -> int:
        """Clear all entries. Returns count cleared."""
        count = len(self._entries)
        self._entries.clear()
        return count
