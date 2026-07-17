"""
EMA_V5 Disk Cache — Persistent JSON-based disk cache.
Isolated from existing caching systems.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5DiskCache:
    """Persistent JSON-based disk cache."""

    def __init__(self, cache_dir: str = "data/cache", max_size_mb: int = 100) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._max_size_mb = max_size_mb
        self._index_file = self._cache_dir / "_index.json"
        self._index: Dict[str, Dict] = self._load_index()

    def _load_index(self) -> Dict[str, Any]:
        """Load cache index."""
        if self._index_file.exists():
            try:
                with open(self._index_file) as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_index(self) -> None:
        """Save cache index."""
        try:
            with open(self._index_file, "w") as f:
                json.dump(self._index, f, default=str)
        except Exception as e:
            logger.error("EMAv5 disk cache index save failed: {}", e)

    def get(self, key: str) -> Optional[Any]:
        """Get a value from disk cache."""
        if key not in self._index:
            return None

        entry_info = self._index[key]
        ttl = entry_info.get("ttl", 0)
        if ttl > 0 and time.time() - entry_info.get("created_at", 0) > ttl:
            self.delete(key)
            return None

        filepath = self._cache_dir / f"{key}.json"
        if not filepath.exists():
            return None

        try:
            with open(filepath) as f:
                return json.load(f)
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        """Set a value in disk cache."""
        filepath = self._cache_dir / f"{key}.json"

        try:
            with open(filepath, "w") as f:
                json.dump(value, f, default=str, indent=2)

            file_size = filepath.stat().st_size

            self._index[key] = {
                "created_at": time.time(),
                "ttl": ttl,
                "size_bytes": file_size,
            }
            self._save_index()

            # Check total size
            self._check_size()

        except Exception as e:
            logger.error("EMAv5 disk cache set failed for {}: {}", key, e)

    def delete(self, key: str) -> bool:
        """Delete a key from disk cache."""
        filepath = self._cache_dir / f"{key}.json"
        try:
            if filepath.exists():
                filepath.unlink()
            if key in self._index:
                del self._index[key]
                self._save_index()
            return True
        except Exception:
            return False

    def clear(self) -> int:
        """Clear all disk cache. Returns number of entries cleared."""
        count = len(self._index)
        for key in list(self._index.keys()):
            filepath = self._cache_dir / f"{key}.json"
            if filepath.exists():
                filepath.unlink()
        self._index.clear()
        self._save_index()
        return count

    def exists(self, key: str) -> bool:
        """Check if key exists in disk cache."""
        if key not in self._index:
            return False
        filepath = self._cache_dir / f"{key}.json"
        return filepath.exists()

    def get_or_set(self, key: str, factory, ttl: int = 3600) -> Any:
        """Get from disk cache or compute and store."""
        value = self.get(key)
        if value is not None:
            return value
        value = factory()
        self.set(key, value, ttl)
        return value

    def size(self) -> int:
        """Current cache size."""
        return len(self._index)

    def get_stats(self) -> Dict[str, Any]:
        """Get disk cache statistics."""
        total_size = sum(e.get("size_bytes", 0) for e in self._index.values())
        return {
            "size": len(self._index),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "max_size_mb": self._max_size_mb,
            "cache_dir": str(self._cache_dir),
        }

    def _check_size(self) -> None:
        """Check and enforce size limit."""
        total_size = sum(e.get("size_bytes", 0) for e in self._index.values())
        max_bytes = self._max_size_mb * 1024 * 1024

        if total_size > max_bytes:
            # Remove oldest entries until under limit
            sorted_entries = sorted(
                self._index.items(),
                key=lambda x: x[1].get("created_at", 0)
            )
            for key, _ in sorted_entries:
                if total_size <= max_bytes * 0.8:  # Remove to 80%
                    break
                filepath = self._cache_dir / f"{key}.json"
                if filepath.exists():
                    file_size = filepath.stat().st_size
                    filepath.unlink()
                    total_size -= file_size
                del self._index[key]

            self._save_index()
            logger.info("EMAv5 disk cache: pruned to {:.1f}MB", total_size / (1024 * 1024))

    def list_keys(self) -> List[str]:
        """List all cache keys."""
        return list(self._index.keys())
