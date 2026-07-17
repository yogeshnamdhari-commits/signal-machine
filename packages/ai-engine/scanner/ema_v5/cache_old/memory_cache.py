"""
EMA_V5 Memory Cache — In-memory LRU cache with TTL.
Isolated from existing caching systems.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from loguru import logger


@dataclass
class CacheEntry:
    """Single cache entry."""
    key: str
    value: Any
    created_at: float = 0.0
    expires_at: float = 0.0
    access_count: int = 0
    last_accessed: float = 0.0
    size_bytes: int = 0


class EMAv5MemoryCache:
    """In-memory LRU cache with TTL support."""

    def __init__(self, max_size: int = 1000, default_ttl: int = 300) -> None:
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache."""
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None

        # Check TTL
        if entry.expires_at > 0 and time.time() > entry.expires_at:
            self._delete(key)
            self._misses += 1
            return None

        # Update access stats
        entry.access_count += 1
        entry.last_accessed = time.time()

        # Move to end (most recently used)
        self._cache.move_to_end(key)
        self._hits += 1

        return entry.value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set a value in cache."""
        # Delete existing if present
        if key in self._cache:
            del self._cache[key]

        # Evict if at capacity
        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)  # Remove oldest

        # Estimate size
        try:
            size_bytes = len(str(value)) * 2  # rough estimate
        except Exception:
            size_bytes = 0

        now = time.time()
        ttl_val = ttl if ttl is not None else self._default_ttl

        entry = CacheEntry(
            key=key,
            value=value,
            created_at=now,
            expires_at=now + ttl_val if ttl_val > 0 else 0,
            access_count=0,
            last_accessed=now,
            size_bytes=size_bytes,
        )

        self._cache[key] = entry

    def delete(self, key: str) -> bool:
        """Delete a key from cache."""
        return self._delete(key)

    def _delete(self, key: str) -> bool:
        """Internal delete."""
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> int:
        """Clear all cache entries. Returns number of entries cleared."""
        count = len(self._cache)
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        return count

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        entry = self._cache.get(key)
        if entry is None:
            return False
        if entry.expires_at > 0 and time.time() > entry.expires_at:
            self._delete(key)
            return False
        return True

    def get_or_set(self, key: str, factory, ttl: Optional[int] = None) -> Any:
        """Get from cache or compute and store."""
        value = self.get(key)
        if value is not None:
            return value
        value = factory()
        self.set(key, value, ttl)
        return value

    def size(self) -> int:
        """Current cache size."""
        return len(self._cache)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_requests = self._hits + self._misses
        hit_rate = (self._hits / total_requests * 100) if total_requests > 0 else 0

        total_size = sum(e.size_bytes for e in self._cache.values())

        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 1),
            "total_size_bytes": total_size,
            "total_size_kb": round(total_size / 1024, 1),
        }

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns number removed."""
        now = time.time()
        expired = [
            key for key, entry in self._cache.items()
            if entry.expires_at > 0 and now > entry.expires_at
        ]
        for key in expired:
            del self._cache[key]
        return len(expired)
