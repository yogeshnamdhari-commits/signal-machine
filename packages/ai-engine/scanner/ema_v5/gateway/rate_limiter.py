"""
EMA_V5 Rate Limiter — Token bucket rate limiting for API endpoints.
Isolated from existing rate limiting systems.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from loguru import logger


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_size: int = 10  # max burst requests


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""
    tokens: float = 0.0
    last_refill: float = 0.0
    max_tokens: float = 0.0
    refill_rate: float = 0.0  # tokens per second


class EMAv5RateLimiter:
    """Token bucket rate limiter for EMA_V5 API."""

    def __init__(self, config: Optional[RateLimitConfig] = None) -> None:
        self.config = config or RateLimitConfig()
        self._buckets: Dict[str, TokenBucket] = {}  # key → bucket
        self._request_counts: Dict[str, list] = {}  # key → [timestamps]

    def check_rate_limit(self, key: str) -> Dict[str, Any]:
        """Check if a request is allowed."""
        now = time.time()

        # Get or create bucket
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(
                tokens=self.config.burst_size,
                last_refill=now,
                max_tokens=self.config.burst_size,
                refill_rate=self.config.requests_per_minute / 60.0,
            )

        bucket = self._buckets[key]

        # Refill tokens
        elapsed = now - bucket.last_refill
        bucket.tokens = min(
            bucket.max_tokens,
            bucket.tokens + elapsed * bucket.refill_rate,
        )
        bucket.last_refill = now

        # Check hourly limit
        if key not in self._request_counts:
            self._request_counts[key] = []
        self._request_counts[key] = [
            t for t in self._request_counts[key] if now - t < 3600
        ]
        if len(self._request_counts[key]) >= self.config.requests_per_hour:
            return {
                "allowed": False,
                "retry_after": 60,
                "reason": "Hourly limit exceeded",
            }

        # Check token bucket
        if bucket.tokens < 1:
            retry_after = (1 - bucket.tokens) / bucket.refill_rate
            return {
                "allowed": False,
                "retry_after": round(retry_after, 2),
                "reason": "Rate limit exceeded",
            }

        # Consume token
        bucket.tokens -= 1
        self._request_counts[key].append(now)

        return {
            "allowed": True,
            "remaining": int(bucket.tokens),
            "limit": self.config.requests_per_minute,
        }

    def reset(self, key: Optional[str] = None) -> None:
        """Reset rate limits."""
        if key:
            self._buckets.pop(key, None)
            self._request_counts.pop(key, None)
        else:
            self._buckets.clear()
            self._request_counts.clear()

    def get_usage(self, key: str) -> Dict[str, Any]:
        """Get rate limit usage for a key."""
        now = time.time()
        bucket = self._buckets.get(key)
        hourly_count = len(self._request_counts.get(key, []))

        return {
            "tokens_remaining": int(bucket.tokens) if bucket else self.config.burst_size,
            "max_tokens": self.config.burst_size,
            "hourly_count": hourly_count,
            "hourly_limit": self.config.requests_per_hour,
        }
