"""Request rate limiting backed by Redis.

A fixed window rather than a token bucket: the counters are trivially cheap,
survive a process restart, and are shared across API replicas. The trade-off is
that a caller can send up to twice the limit across a window boundary, which is
acceptable here — this exists to stop a stolen credential probing the gallery
at speed, not to shape traffic precisely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.connectors.redis.connector import RedisConnector

logger = logging.getLogger(__name__)

KEY_PREFIX = "faceid:ratelimit"


@dataclass(frozen=True, slots=True)
class RateLimit:
    """How many requests are allowed in a window."""

    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        """Validate the limit and window."""
        if self.limit < 1:
            raise ValueError(f"limit must be at least 1, got {self.limit}")
        if self.window_seconds < 1:
            raise ValueError(f"window_seconds must be at least 1, got {self.window_seconds}")


@dataclass(frozen=True, slots=True)
class RateLimitVerdict:
    """The outcome of one rate-limit check."""

    allowed: bool
    remaining: int
    retry_after_seconds: int


class RedisRateLimiter:
    """Counts requests per caller per window."""

    def __init__(self, connector: RedisConnector) -> None:
        """Bind the limiter to a Redis connector."""
        self._connector = connector

    async def check(self, identity: str, action: str, limit: RateLimit) -> RateLimitVerdict:
        """Count one request and report whether it is allowed.

        The counter is incremented even when the request is refused, so a
        caller cannot reset their window by hammering it — but the expiry is
        only set on first use, so the window does not slide forward forever.
        """
        key = f"{KEY_PREFIX}:{action}:{identity}"
        client = self._connector.client

        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = await pipe.execute()

        count = int(count)
        ttl = int(ttl)
        if ttl < 0:
            # First request in this window, or a key left without an expiry.
            await client.expire(key, limit.window_seconds)
            ttl = limit.window_seconds

        if count > limit.limit:
            logger.warning(
                "rate limit exceeded",
                extra={"action": action, "limit": limit.limit, "window": limit.window_seconds},
            )
            return RateLimitVerdict(allowed=False, remaining=0, retry_after_seconds=max(ttl, 1))

        return RateLimitVerdict(
            allowed=True,
            remaining=max(limit.limit - count, 0),
            retry_after_seconds=0,
        )

    async def reset(self, identity: str, action: str) -> None:
        """Clear a caller's window. Intended for tests and operator recovery."""
        await self._connector.client.delete(f"{KEY_PREFIX}:{action}:{identity}")
