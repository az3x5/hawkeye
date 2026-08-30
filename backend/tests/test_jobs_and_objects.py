"""Job queue and object store.

The object store is exercised against a temporary directory; the queue needs a
real Redis and is skipped without one.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from app.connectors.filesystem.object_store import (
    FilesystemObjectStore,
    ObjectStoreError,
    sha256_bytes,
)
from app.connectors.redis import RedisConnector, RedisJobQueue
from app.connectors.redis.queue import FAILED_KEY, IN_FLIGHT_KEY, PENDING_KEY
from app.domain.jobs import EmbeddingJob, JobQueue, JobQueueError, ObjectStore

REDIS_DSN = os.environ.get("FACEID_TEST_REDIS_DSN")

requires_redis = pytest.mark.skipif(
    REDIS_DSN is None, reason="set FACEID_TEST_REDIS_DSN to run job queue tests"
)

PAYLOAD = b"an image, for the purposes of this test"


def _job() -> EmbeddingJob:
    return EmbeddingJob(
        face_sample_uuid=uuid4(),
        person_uuid=uuid4(),
        image_sha256=sha256_bytes(PAYLOAD),
    )


class TestObjectStore:
    @pytest.fixture
    def store(self, tmp_path: Path) -> FilesystemObjectStore:
        return FilesystemObjectStore(tmp_path / "objects")

    def test_satisfies_the_object_store_protocol(self, store: FilesystemObjectStore) -> None:
        assert isinstance(store, ObjectStore)

    async def test_an_object_round_trips(self, store: FilesystemObjectStore) -> None:
        digest = sha256_bytes(PAYLOAD)
        await store.put(digest, PAYLOAD)
        assert await store.get(digest) == PAYLOAD

    async def test_storing_the_same_object_twice_is_a_no_op(
        self, store: FilesystemObjectStore
    ) -> None:
        digest = sha256_bytes(PAYLOAD)
        await store.put(digest, PAYLOAD)
        await store.put(digest, PAYLOAD)
        assert await store.get(digest) == PAYLOAD

    async def test_a_mismatched_address_is_refused(self, store: FilesystemObjectStore) -> None:
        with pytest.raises(ObjectStoreError, match="hash to"):
            await store.put(sha256_bytes(b"something else"), PAYLOAD)

    @pytest.mark.parametrize("bad", ["", "abc", "z" * 64, "A" * 64])
    async def test_a_malformed_address_is_refused(
        self, store: FilesystemObjectStore, bad: str
    ) -> None:
        with pytest.raises(ObjectStoreError, match="64 lowercase hex"):
            await store.get(bad)

    async def test_an_absent_object_is_none(self, store: FilesystemObjectStore) -> None:
        assert await store.get(sha256_bytes(b"never stored")) is None

    async def test_deletion_reports_whether_it_removed_anything(
        self, store: FilesystemObjectStore
    ) -> None:
        digest = sha256_bytes(PAYLOAD)
        assert await store.delete(digest) is False
        await store.put(digest, PAYLOAD)
        assert await store.delete(digest) is True
        assert await store.get(digest) is None

    async def test_ping_checks_the_root_is_writable(self, store: FilesystemObjectStore) -> None:
        await store.ping()

    async def test_no_partial_files_are_left_behind(self, store: FilesystemObjectStore) -> None:
        await store.put(sha256_bytes(PAYLOAD), PAYLOAD)
        assert list(store.root.rglob("*.partial")) == []


class TestJobSerialisation:
    def test_a_job_round_trips_through_its_payload(self) -> None:
        job = _job()
        restored = EmbeddingJob.from_payload(job.to_payload())
        assert restored == job

    def test_the_payload_carries_no_biometric_material(self) -> None:
        payload = _job().to_payload()
        assert set(payload) == {
            "face_sample_uuid",
            "person_uuid",
            "image_sha256",
            "enqueued_at",
        }

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"face_sample_uuid": "not-a-uuid"},
            {"face_sample_uuid": str(uuid4()), "person_uuid": str(uuid4())},
        ],
    )
    def test_a_malformed_payload_is_refused(self, payload: dict[str, str]) -> None:
        with pytest.raises(JobQueueError, match="malformed"):
            EmbeddingJob.from_payload(payload)

    def test_enqueued_at_is_timezone_aware(self) -> None:
        assert _job().enqueued_at.tzinfo is not None


@requires_redis
class TestRedisJobQueue:
    @pytest_asyncio.fixture
    async def queue(self) -> AsyncIterator[RedisJobQueue]:
        assert REDIS_DSN is not None
        connector = RedisConnector(REDIS_DSN)
        await connector.client.delete(PENDING_KEY, IN_FLIGHT_KEY, FAILED_KEY)
        yield RedisJobQueue(connector)
        await connector.client.delete(PENDING_KEY, IN_FLIGHT_KEY, FAILED_KEY)
        await connector.close()

    async def test_satisfies_the_job_queue_protocol(self, queue: RedisJobQueue) -> None:
        assert isinstance(queue, JobQueue)

    async def test_a_job_round_trips_through_redis(self, queue: RedisJobQueue) -> None:
        job = _job()
        await queue.enqueue(job)
        assert await queue.depth() == 1
        reserved = await queue.reserve(timeout_seconds=1)
        assert reserved == job

    async def test_reserving_from_an_empty_queue_times_out(self, queue: RedisJobQueue) -> None:
        assert await queue.reserve(timeout_seconds=1) is None

    async def test_jobs_are_delivered_in_order(self, queue: RedisJobQueue) -> None:
        first, second = _job(), _job()
        await queue.enqueue(first)
        await queue.enqueue(second)
        assert await queue.reserve(timeout_seconds=1) == first
        assert await queue.reserve(timeout_seconds=1) == second

    async def test_a_reserved_job_is_held_until_reported(self, queue: RedisJobQueue) -> None:
        job = _job()
        await queue.enqueue(job)
        await queue.reserve(timeout_seconds=1)
        # Still in flight: a worker that dies here has not lost the job.
        assert await queue.in_flight() == 1
        await queue.complete(job)
        assert await queue.in_flight() == 0

    async def test_a_failed_job_is_kept_with_its_reason(self, queue: RedisJobQueue) -> None:
        job = _job()
        await queue.enqueue(job)
        await queue.reserve(timeout_seconds=1)
        await queue.fail(job, "no face was detected")

        assert await queue.in_flight() == 0
        recorded = await queue._connector.client.lrange(FAILED_KEY, 0, -1)
        assert len(recorded) == 1
        assert "no face was detected" in recorded[0]

    async def test_a_completed_job_does_not_come_back(self, queue: RedisJobQueue) -> None:
        job = _job()
        await queue.enqueue(job)
        await queue.reserve(timeout_seconds=1)
        await queue.complete(job)
        assert await queue.depth() == 0
        assert await queue.reserve(timeout_seconds=1) is None

    async def test_a_corrupt_entry_is_reported_not_silently_skipped(
        self, queue: RedisJobQueue
    ) -> None:
        await queue._connector.client.lpush(PENDING_KEY, "{not json")
        with pytest.raises(JobQueueError, match="could not decode"):
            await queue.reserve(timeout_seconds=1)

    async def test_cutover_recovers_in_flight_work_without_loss(self, queue: RedisJobQueue) -> None:
        job = _job()
        await queue.enqueue(job)
        assert await queue.reserve(timeout_seconds=1) == job
        assert await queue.in_flight() == 1
        assert await queue.recover_in_flight() == 1
        assert await queue.in_flight() == 0
        assert await queue.depth() == 1
        migrated = await queue.reserve_nowait()
        assert migrated == job
        await queue.complete(job)


def test_embedding_job_equality_ignores_nothing() -> None:
    now = datetime.now(UTC)
    sample, person = uuid4(), uuid4()
    first = EmbeddingJob(sample, person, "a" * 64, now)
    second = EmbeddingJob(sample, person, "a" * 64, now)
    assert first == second


@requires_redis
class TestBlockingReadHeadroom:
    """A block window must sit inside the socket read deadline.

    Without headroom, redis-py derives the read deadline from the blocking
    command's own timeout and races the server: an empty queue raises a
    connection timeout instead of reporting that no job arrived.
    """

    async def test_a_long_block_on_an_empty_queue_returns_none(self) -> None:
        assert REDIS_DSN is not None
        connector = RedisConnector(REDIS_DSN)
        queue = RedisJobQueue(connector)
        await connector.client.delete(PENDING_KEY)
        try:
            assert await queue.reserve(timeout_seconds=5) is None
        finally:
            await connector.close()

    async def test_a_block_exceeding_the_socket_timeout_is_refused(self) -> None:
        assert REDIS_DSN is not None
        connector = RedisConnector(REDIS_DSN, socket_timeout=2.0)
        queue = RedisJobQueue(connector)
        try:
            with pytest.raises(JobQueueError, match="socket timeout"):
                await queue.reserve(timeout_seconds=5)
        finally:
            await connector.close()
