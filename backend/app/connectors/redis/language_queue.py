"""Reliable Redis queue for language-document embedding jobs."""

from __future__ import annotations

import json
import logging

from app.connectors.redis.connector import RedisConnector
from app.domain.jobs import JobQueueError, LanguageEmbeddingJob

logger = logging.getLogger(__name__)

PENDING_KEY = "hawkeye:language:embedding:pending"
IN_FLIGHT_KEY = "hawkeye:language:embedding:in_flight"
FAILED_KEY = "hawkeye:language:embedding:failed"
ACTIVE_KEY = "hawkeye:language:embedding:active"

_ENQUEUE_ONCE = """
if redis.call('SADD', KEYS[1], ARGV[1]) == 1 then
  redis.call('LPUSH', KEYS[2], ARGV[2])
  return 1
end
return 0
"""


class RedisLanguageJobQueue:
    """Reliable queue that retains failed and interrupted language jobs."""

    def __init__(self, connector: RedisConnector) -> None:
        """Bind the queue to a Redis connector."""
        self._connector = connector

    async def enqueue(self, job: LanguageEmbeddingJob) -> None:
        """Submit a document once while it is pending or in flight."""
        await self._connector.client.eval(
            _ENQUEUE_ONCE,
            2,
            ACTIVE_KEY,
            PENDING_KEY,
            str(job.document_uuid),
            self._encode(job),
        )

    async def reserve(self, *, timeout_seconds: int) -> LanguageEmbeddingJob | None:
        """Atomically move the next job into the in-flight list."""
        if timeout_seconds >= self._connector.socket_timeout:
            raise JobQueueError("language queue block timeout exceeds the Redis socket timeout")
        raw = await self._connector.client.blmove(
            PENDING_KEY, IN_FLIGHT_KEY, timeout=timeout_seconds, src="RIGHT", dest="LEFT"
        )
        if raw is None:
            return None
        return self._decode(raw if isinstance(raw, str) else raw.decode())

    async def complete(self, job: LanguageEmbeddingJob) -> None:
        """Remove a completed job from the in-flight list."""
        async with self._connector.client.pipeline(transaction=True) as pipe:
            pipe.lrem(IN_FLIGHT_KEY, 1, self._encode(job))
            pipe.srem(ACTIVE_KEY, str(job.document_uuid))
            await pipe.execute()

    async def fail(self, job: LanguageEmbeddingJob, reason: str) -> None:
        """Retain a failed job with its reason."""
        encoded = self._encode(job)
        async with self._connector.client.pipeline(transaction=True) as pipe:
            pipe.lrem(IN_FLIGHT_KEY, 1, encoded)
            pipe.srem(ACTIVE_KEY, str(job.document_uuid))
            pipe.lpush(
                FAILED_KEY,
                json.dumps({"job": job.to_payload(), "reason": reason}, sort_keys=True),
            )
            await pipe.execute()
        logger.warning(
            "language embedding job failed",
            extra={"document_uuid": str(job.document_uuid), "reason": reason},
        )

    async def depth(self) -> int:
        """Return waiting-job count."""
        return int(await self._connector.client.llen(PENDING_KEY))

    async def in_flight(self) -> int:
        """Return reserved-job count."""
        return int(await self._connector.client.llen(IN_FLIGHT_KEY))

    @staticmethod
    def _encode(job: LanguageEmbeddingJob) -> str:
        return json.dumps(job.to_payload(), sort_keys=True)

    @staticmethod
    def _decode(raw: str) -> LanguageEmbeddingJob:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise JobQueueError(f"could not decode language job: {exc}") from exc
        if not isinstance(payload, dict):
            raise JobQueueError("expected a language job object")
        return LanguageEmbeddingJob.from_payload(payload)
