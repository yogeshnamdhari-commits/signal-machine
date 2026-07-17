"""
EMA_V5 Log Aggregator — Aggregates logs from multiple sources.
Isolated from existing log aggregation systems.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5LogAggregator:
    """Aggregates logs from multiple sources."""

    def __init__(self) -> None:
        self._sources: Dict[str, List[Dict]] = {}
        self._aggregated: List[Dict] = []

    def add_source(self, name: str, log_file: str) -> bool:
        """Add a log file source."""
        filepath = Path(log_file)
        if not filepath.exists():
            logger.warning("EMAv5 log source not found: {}", log_file)
            return False

        try:
            entries = []
            with open(filepath) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            entry["_source"] = name
                            entries.append(entry)
                        except json.JSONDecodeError:
                            # Non-JSON line, treat as plain text
                            entries.append({
                                "message": line,
                                "_source": name,
                                "level": "INFO",
                                "timestamp": time.time(),
                            })

            self._sources[name] = entries
            self._rebuild_aggregated()
            logger.info("EMAv5 log source added: {} ({} entries)", name, len(entries))
            return True
        except Exception as e:
            logger.error("EMAv5 log source failed: {}", e)
            return False

    def add_entries(self, source: str, entries: List[Dict]) -> None:
        """Add entries directly."""
        for entry in entries:
            entry["_source"] = source
        self._sources[source] = entries
        self._rebuild_aggregated()

    def _rebuild_aggregated(self) -> None:
        """Rebuild aggregated log from all sources."""
        self._aggregated = []
        for source_entries in self._sources.values():
            self._aggregated.extend(source_entries)

        # Sort by timestamp
        self._aggregated.sort(key=lambda e: e.get("timestamp", 0))

    def query(self, level: Optional[str] = None, source: Optional[str] = None,
              start_time: Optional[float] = None, end_time: Optional[float] = None,
              search: Optional[str] = None, limit: int = 1000) -> List[Dict]:
        """Query aggregated logs."""
        results = self._aggregated

        if level:
            results = [e for e in results if e.get("level", "").upper() == level.upper()]

        if source:
            results = [e for e in results if e.get("_source") == source]

        if start_time:
            results = [e for e in results if e.get("timestamp", 0) >= start_time]

        if end_time:
            results = [e for e in results if e.get("timestamp", 0) <= end_time]

        if search:
            search_lower = search.lower()
            results = [
                e for e in results
                if search_lower in str(e.get("message", "")).lower()
                or search_lower in json.dumps(e.get("data", {})).lower()
            ]

        return results[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregated log statistics."""
        total = len(self._aggregated)
        if total == 0:
            return {"total": 0, "by_level": {}, "by_source": {}}

        by_level: Dict[str, int] = {}
        by_source: Dict[str, int] = {}

        for entry in self._aggregated:
            level = entry.get("level", "UNKNOWN")
            source = entry.get("_source", "unknown")
            by_level[level] = by_level.get(level, 0) + 1
            by_source[source] = by_source.get(source, 0) + 1

        return {
            "total": total,
            "by_level": by_level,
            "by_source": by_source,
            "sources": list(self._sources.keys()),
        }

    def clear(self) -> None:
        """Clear all sources."""
        self._sources.clear()
        self._aggregated.clear()
