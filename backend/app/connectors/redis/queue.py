"""Embedding job queue backed by Redis lists.

Uses a reliable-queue pattern: ``BLMOVE`` atomically moves a job from the
pending list to an in-flight list, and it is only deleted once the worker
reports the outcome. A worker that dies mid-job leaves the job visible in the
in-flight list rather than losing it silently.
"""

from __future__ import annotations

import json
import logging

from app.connectors.redis.connector import RedisConnector
from app.domain.jobs import EmbeddingJob, JobQueueError

logger = logging.getLogger(__name__)

PENDING_KEY = "faceid:embedding:pending"
IN_FLIGHT_KEY = "faceid:embedding:in_flight"
FAILED_KEY = "faceid:embedding:failed"


class RedisJobQueue:
    """``JobQueue`` over Redis lists."""

    def __init__(self, connector: RedisConnector) -> None:
        """Bind the queue to a Redis connector."""
        self._connector = connector

    async def enqueue(self, job: EmbeddingJob) -> None:
        """Submit a job for processing."""
        await self._connector.client.lpush(PENDING_KEY, self._encode(job))

    async def reserve(self, *, timeout_seconds: int) -> EmbeddingJob | None:
        """Take the next job, or None if none arrives within the timeout."""
        if timeout_seconds >= self._connector.socket_timeout:
            raise JobQueueError(
                f"a block of {timeout_seconds}s needs a socket timeout above it; "
                f"the connector allows {self._connector.socket_timeout}s. Without "
                "headroom an empty queue raises instead of returning no job."
            )
        raw = await self._connector.client.blmove(
            PENDING_KEY, IN_FLIGHT_KEY, timeout=timeout_seconds, src="RIGHT", dest="LEFT"
        )
        if raw is None:
            return None
        # decode_responses=True is set on the client, so this is always str.
        return self._decode(raw if isinstance(raw, str) else raw.decode())

    async def reserve_nowait(self) -> EmbeddingJob | None:
        """Move one legacy waiting job without blocking."""
        raw = await self._connector.client.lmove(
            PENDING_KEY, IN_FLIGHT_KEY, src="RIGHT", dest="LEFT"
        )
        if raw is None:
            return None
        return self._decode(raw if isinstance(raw, str) else raw.decode())

    async def recover_in_flight(self) -> int:
        """Return legacy reservations to pending during the M1 cutover."""
        recovered = 0
        while True:
            raw = await self._connector.client.lmove(
                IN_FLIGHT_KEY, PENDING_KEY, src="RIGHT", dest="LEFT"
            )
            if raw is None:
                return recovered
            recovered += 1

    async def complete(self, job: EmbeddingJob) -> None:
        """Drop a finished job from the in-flight list."""
        await self._connector.client.lrem(IN_FLIGHT_KEY, 1, self._encode(job))

    async def fail(self, job: EmbeddingJob, reason: str) -> None:
        """Move a failed job out of flight and record why.

        Failures are kept rather than discarded: a job that could not be
        processed is evidence, not noise.
        """
        encoded = self._encode(job)
        client = self._connector.client
        async with client.pipeline(transaction=True) as pipe:
            pipe.lrem(IN_FLIGHT_KEY, 1, encoded)
            pipe.lpush(
                FAILED_KEY,
                json.dumps({"job": job.to_payload(), "reason": reason}, sort_keys=True),
            )
            await pipe.execute()
        logger.warning(
            "embedding job failed",
            extra={"face_sample_uuid": str(job.face_sample_uuid), "reason": reason},
        )

    async def depth(self) -> int:
        """Number of jobs waiting to be reserved."""
        return int(await self._connector.client.llen(PENDING_KEY))

    async def in_flight(self) -> int:
        """Number of jobs reserved but not yet reported."""
        return int(await self._connector.client.llen(IN_FLIGHT_KEY))

    @staticmethod
    def _encode(job: EmbeddingJob) -> str:
        return json.dumps(job.to_payload(), sort_keys=True)

    @staticmethod
    def _decode(raw: str) -> EmbeddingJob:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise JobQueueError(f"could not decode job from the queue: {exc}") from exc
        if not isinstance(payload, dict):
            raise JobQueueError(f"expected a job object on the queue, got {type(payload)}")
        return EmbeddingJob.from_payload(payload)
