"""
EMA_V5 Cache Stats — Cache performance tracking and analytics.
Isolated from existing stats systems.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from loguru import logger


class EMAv5CacheStats:
    """Tracks cache performance statistics."""

    def __init__(self) -> None:
        self._hits: Dict[str, int] = {"memory": 0, "disk": 0}
        self._misses = 0
        self._total_requests = 0
        self._latencies: List[float] = []
        self._start_time = time.time()

    def record_hit(self, level: str) -> None:
        """Record a cache hit at the specified level."""
        self._hits[level] = self._hits.get(level, 0) + 1
        self._total_requests += 1

    def record_miss(self) -> None:
        """Record a cache miss."""
        self._misses += 1
        self._total_requests += 1

    def record_latency(self, latency_ms: float) -> None:
        """Record a cache operation latency."""
        self._latencies.append(latency_ms)
        # Keep last 1000 latencies
        if len(self._latencies) > 1000:
            self._latencies = self._latencies[-1000:]

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics."""
        total_hits = sum(self._hits.values())
        hit_rate = (total_hits / self._total_requests * 100) if self._total_requests > 0 else 0

        # Latency stats
        if self._latencies:
            avg_latency = sum(self._latencies) / len(self._latencies)
            max_latency = max(self._latencies)
            min_latency = min(self._latencies)
        else:
            avg_latency = 0
            max_latency = 0
            min_latency = 0

        # Level breakdown
        memory_hit_rate = (self._hits["memory"] / self._total_requests * 100) if self._total_requests > 0 else 0
        disk_hit_rate = (self._hits["disk"] / self._total_requests * 100) if self._total_requests > 0 else 0

        uptime = time.time() - self._start_time

        return {
            "total_requests": self._total_requests,
            "hits": total_hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 1),
            "memory_hits": self._hits.get("memory", 0),
            "disk_hits": self._hits.get("disk", 0),
            "memory_hit_rate": round(memory_hit_rate, 1),
            "disk_hit_rate": round(disk_hit_rate, 1),
            "avg_latency_ms": round(avg_latency, 2),
            "max_latency_ms": round(max_latency, 2),
            "min_latency_ms": round(min_latency, 2),
            "uptime_seconds": round(uptime, 0),
            "requests_per_second": round(self._total_requests / max(uptime, 1), 2),
        }

    def reset(self) -> None:
        """Reset all statistics."""
        self._hits = {"memory": 0, "disk": 0}
        self._misses = 0
        self._total_requests = 0
        self._latencies.clear()
        self._start_time = time.time()

    def get_hit_rate_trend(self, window: int = 100) -> List[Dict]:
        """Get hit rate trend over rolling window."""
        # Simplified trend - just return current stats
        stats = self.get_stats()
        return [{"timestamp": time.time(), "hit_rate": stats["hit_rate"]}]
