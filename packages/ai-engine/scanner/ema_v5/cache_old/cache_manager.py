"""
EMA_V5 Cache Manager — Multi-level cache with automatic fallback.
Memory → Disk → Compute. Isolated from existing cache managers.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from loguru import logger

from .memory_cache import EMAv5MemoryCache
from .disk_cache import EMAv5DiskCache
from .cache_stats import EMAv5CacheStats


class EMAv5CacheManager:
    """Multi-level cache manager with automatic fallback."""

    def __init__(self, memory_size: int = 1000, memory_ttl: int = 300,
                 disk_size_mb: int = 100, disk_ttl: int = 3600) -> None:
        self._memory = EMAv5MemoryCache(max_size=memory_size, default_ttl=memory_ttl)
        self._disk = EMAv5DiskCache(max_size_mb=disk_size_mb)
        self._disk_ttl = disk_ttl
        self._stats = EMAv5CacheStats()

    def get(self, key: str) -> Optional[Any]:
        """Get from multi-level cache."""
        # Level 1: Memory
        value = self._memory.get(key)
        if value is not None:
            self._stats.record_hit("memory")
            return value

        # Level 2: Disk
        value = self._disk.get(key)
        if value is not None:
            self._stats.record_hit("disk")
            # Promote to memory
            self._memory.set(key, value)
            return value

        self._stats.record_miss()
        return None

    def set(self, key: str, value: Any, memory_ttl: Optional[int] = None,
            disk_ttl: Optional[int] = None) -> None:
        """Set in both memory and disk cache."""
        self._memory.set(key, value, ttl=memory_ttl)
        self._disk.set(key, value, ttl=disk_ttl or self._disk_ttl)

    def get_or_set(self, key: str, factory: Callable,
                   memory_ttl: Optional[int] = None,
                   disk_ttl: Optional[int] = None) -> Any:
        """Get from cache or compute and store."""
        value = self.get(key)
        if value is not None:
            return value

        value = factory()
        self.set(key, value, memory_ttl=memory_ttl, disk_ttl=disk_ttl)
        self._stats.record_miss()  # Count the miss that triggered compute
        return value

    def delete(self, key: str) -> bool:
        """Delete from both levels."""
        mem_deleted = self._memory.delete(key)
        disk_deleted = self._disk.delete(key)
        return mem_deleted or disk_deleted

    def clear(self) -> int:
        """Clear both levels."""
        mem_count = self._memory.clear()
        disk_count = self._disk.clear()
        return mem_count + disk_count

    def exists(self, key: str) -> bool:
        """Check if key exists in either level."""
        return self._memory.exists(key) or self._disk.exists(key)

    def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all keys matching a pattern."""
        count = 0
        for key in self._memory._cache.keys():
            if pattern in key:
                self._memory.delete(key)
                count += 1
        for key in self._disk.list_keys():
            if pattern in key:
                self._disk.delete(key)
                count += 1
        return count

    def get_stats(self) -> Dict[str, Any]:
        """Get combined cache statistics."""
        mem_stats = self._memory.get_stats()
        disk_stats = self._disk.get_stats()
        perf_stats = self._stats.get_stats()

        return {
            "memory": mem_stats,
            "disk": disk_stats,
            "performance": perf_stats,
        }

    def cleanup(self) -> Dict[str, int]:
        """Cleanup expired entries in both levels."""
        mem_cleaned = self._memory.cleanup_expired()
        return {"memory_cleaned": mem_cleaned}

    def warm_up(self, entries: Dict[str, Any]) -> int:
        """Warm up cache with initial entries."""
        count = 0
        for key, value in entries.items():
            self.set(key, value)
            count += 1
        return count
