"""
EMA_V5 Log Analyzer — Analyzes logs for patterns and anomalies.
Isolated from existing log analysis systems.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5LogAnalyzer:
    """Analyzes EMA_V5 logs for patterns and anomalies."""

    def __init__(self) -> None:
        self._entries: List[Dict] = []

    def load_entries(self, entries: List[Dict]) -> None:
        """Load log entries for analysis."""
        self._entries = entries

    def analyze(self) -> Dict[str, Any]:
        """Perform complete log analysis."""
        if not self._entries:
            return {"error": "No entries to analyze"}

        return {
            "summary": self._summary(),
            "level_distribution": self._level_distribution(),
            "module_distribution": self._module_distribution(),
            "error_analysis": self._error_analysis(),
            "time_patterns": self._time_patterns(),
            "anomalies": self._detect_anomalies(),
        }

    def _summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        total = len(self._entries)
        errors = sum(1 for e in self._entries if e.get("level") == "ERROR")
        warnings = sum(1 for e in self._entries if e.get("level") == "WARNING")

        timestamps = [e.get("timestamp", 0) for e in self._entries if e.get("timestamp")]
        time_span = max(timestamps) - min(timestamps) if len(timestamps) > 1 else 0

        return {
            "total_entries": total,
            "errors": errors,
            "warnings": warnings,
            "error_rate": round(errors / max(total, 1) * 100, 1),
            "warning_rate": round(warnings / max(total, 1) * 100, 1),
            "time_span_hours": round(time_span / 3600, 1),
        }

    def _level_distribution(self) -> Dict[str, int]:
        """Get level distribution."""
        counts: Dict[str, int] = Counter()
        for entry in self._entries:
            level = entry.get("level", "UNKNOWN")
            counts[level] += 1
        return dict(counts)

    def _module_distribution(self) -> Dict[str, int]:
        """Get module distribution."""
        counts: Dict[str, int] = Counter()
        for entry in self._entries:
            module = entry.get("module", "unknown")
            counts[module] += 1
        return dict(counts.most_common(20))

    def _error_analysis(self) -> Dict[str, Any]:
        """Analyze error patterns."""
        errors = [e for e in self._entries if e.get("level") == "ERROR"]
        if not errors:
            return {"count": 0, "patterns": [], "top_errors": []}

        # Error messages
        error_msgs = [e.get("message", "") for e in errors]
        msg_counts = Counter(error_msgs)

        # Error by module
        module_counts = Counter(e.get("module", "unknown") for e in errors)

        return {
            "count": len(errors),
            "top_errors": [{"message": msg, "count": cnt}
                          for msg, cnt in msg_counts.most_common(10)],
            "errors_by_module": dict(module_counts.most_common(10)),
        }

    def _time_patterns(self) -> Dict[str, Any]:
        """Analyze time-based patterns."""
        hourly: Dict[int, int] = defaultdict(int)
        for entry in self._entries:
            ts = entry.get("timestamp", 0)
            if ts:
                hour = int(time.strftime("%H", time.gmtime(ts)))
                hourly[hour] += 1

        # Find peak hours
        if hourly:
            peak_hour = max(hourly, key=hourly.get)
            quiet_hour = min(hourly, key=hourly.get)
        else:
            peak_hour = quiet_hour = 0

        return {
            "hourly_distribution": dict(hourly),
            "peak_hour": peak_hour,
            "quiet_hour": quiet_hour,
        }

    def _detect_anomalies(self) -> List[Dict]:
        """Detect anomalies in log patterns."""
        anomalies = []

        # Detect error bursts
        errors = [e for e in self._entries if e.get("level") == "ERROR"]
        if len(errors) > 10:
            # Check for error burst (many errors in short time)
            timestamps = [e.get("timestamp", 0) for e in errors]
            if len(timestamps) > 1:
                time_span = max(timestamps) - min(timestamps)
                if time_span < 300:  # 5 minutes
                    anomalies.append({
                        "type": "error_burst",
                        "severity": "high",
                        "message": f"{len(errors)} errors in {time_span:.0f} seconds",
                        "count": len(errors),
                    })

        # Detect repeated errors
        error_msgs = [e.get("message", "") for e in errors]
        msg_counts = Counter(error_msgs)
        for msg, count in msg_counts.items():
            if count >= 5:
                anomalies.append({
                    "type": "repeated_error",
                    "severity": "medium",
                    "message": f"Error repeated {count} times: {msg[:50]}",
                    "count": count,
                })

        # Detect long gaps
        timestamps = [e.get("timestamp", 0) for e in self._entries if e.get("timestamp")]
        if len(timestamps) > 1:
            sorted_ts = sorted(timestamps)
            for i in range(1, len(sorted_ts)):
                gap = sorted_ts[i] - sorted_ts[i - 1]
                if gap > 3600:  # 1 hour gap
                    anomalies.append({
                        "type": "time_gap",
                        "severity": "low",
                        "message": f"Log gap of {gap / 3600:.1f} hours",
                        "duration_hours": round(gap / 3600, 1),
                    })
                    break  # Only report first gap

        return anomalies

    def search(self, query: str) -> List[Dict]:
        """Search logs by query."""
        query_lower = query.lower()
        return [
            e for e in self._entries
            if query_lower in str(e.get("message", "")).lower()
            or query_lower in json.dumps(e.get("data", {})).lower()
        ]
