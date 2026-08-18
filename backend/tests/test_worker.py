"""End-to-end tests for the embedding worker.

Needs Postgres, Redis, Qdrant and both models. Skipped otherwise, so a partial
environment cannot look like a passing pipeline.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import cv2
import numpy as np
import pytest
import pytest_asyncio

from app.connectors.postgres import PostgresConnector, SqlAlchemyFaceSampleRepository
from app.core.config import Settings
from app.domain.jobs import EmbeddingJob, ProcessingState
from app.domain.models import FaceSample, Person
from app.services.enrolment import EnrolmentRequest, EnrolmentService
from app.worker import EmbeddingWorker

from .conftest import INTEGRATION_DSN
from .test_scrfd import WEIGHTS as SCRFD_WEIGHTS

ADAFACE_WEIGHTS = Path(
    os.environ.get(
        "FACEID_TEST_ADAFACE_MODEL_PATH",
        Path(__file__).resolve().parents[2] / "models" / "adaface_ir101_webface12m.safetensors",
    )
)
QDRANT_URL = os.environ.get("FACEID_TEST_QDRANT_URL")
REDIS_DSN = os.environ.get("FACEID_TEST_REDIS_DSN")

pytestmark = pytest.mark.skipif(
    not (
        INTEGRATION_DSN
        and QDRANT_URL
        and REDIS_DSN
        and SCRFD_WEIGHTS.is_file()
        and ADAFACE_WEIGHTS.is_file()
    ),
    reason="the worker needs Postgres, Redis, Qdrant and both model weights",
)


def _face_jpeg() -> bytes:
    skimage_data = pytest.importorskip("skimage.data")
    bgr = cv2.cvtColor(skimage_data.astronaut(), cv2.COLOR_RGB2BGR)
    ok, encoded = cv2.imencode(".jpg", bgr)
    assert ok
    return bytes(encoded.tobytes())


def _blank_jpeg() -> bytes:
    ok, encoded = cv2.imencode(".jpg", np.zeros((480, 640, 3), dtype=np.uint8))
    assert ok
    return bytes(encoded.tobytes())


@pytest.fixture
def worker_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("FACEID_ENVIRONMENT", "test")
    monkeypatch.setenv("FACEID_POSTGRES_DSN", str(INTEGRATION_DSN))
    monkeypatch.setenv("FACEID_REDIS_DSN", str(REDIS_DSN))
    monkeypatch.setenv("FACEID_QDRANT_URL", str(QDRANT_URL))
    monkeypatch.setenv("FACEID_SCRFD_MODEL_PATH", str(SCRFD_WEIGHTS))
    monkeypatch.setenv("FACEID_ADAFACE_MODEL_PATH", str(ADAFACE_WEIGHTS))
    monkeypatch.setenv("FACEID_OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    return Settings()  # type: ignore[call-arg]


@pytest_asyncio.fixture
async def worker(worker_settings: Settings) -> AsyncIterator[EmbeddingWorker]:
    instance = EmbeddingWorker(worker_settings)
    await instance.start()
    yield instance
    await instance.close()


@pytest_asyncio.fixture
async def postgres(worker_settings: Settings) -> AsyncIterator[PostgresConnector]:
    connector = PostgresConnector(str(worker_settings.postgres_dsn))
    yield connector
    await connector.close()


async def _enrol(
    worker: EmbeddingWorker, image: bytes, postgres: PostgresConnector
) -> tuple[Person, FaceSample]:
    """Enrol through the real service so the worker sees a real job."""
    from app.connectors.postgres import SqlAlchemyPersonRepository

    async with postgres.session() as session:
        service = EnrolmentService(
            people=SqlAlchemyPersonRepository(session),
            samples=SqlAlchemyFaceSampleRepository(session),
            objects=worker._objects,
            queue=worker._queue,
        )
        result = await service.enrol(
            EnrolmentRequest(source="test", image=image, external_id=os.urandom(8).hex())
        )
    return result.person, result.sample


class TestPipeline:
    async def test_a_real_face_is_embedded_and_stored(
        self, worker: EmbeddingWorker, postgres: PostgresConnector
    ) -> None:
        person, sample = await _enrol(worker, _face_jpeg(), postgres)

        job = await worker._queue.reserve(timeout_seconds=5)
        assert job is not None
        await worker.process(job)

        async with postgres.session() as session:
            stored = await SqlAlchemyFaceSampleRepository(session).get(sample.face_sample_uuid)
        assert stored is not None
        assert stored.processing_state is ProcessingState.PROCESSED
        assert stored.failure_reason is None

        embedding = await worker._vectors.get(
            sample.face_sample_uuid, worker._recognizer.provenance
        )
        assert embedding is not None
        assert embedding.dimension == 512

    async def test_the_stored_vector_is_findable_by_search(
        self, worker: EmbeddingWorker, postgres: PostgresConnector
    ) -> None:
        person, sample = await _enrol(worker, _face_jpeg(), postgres)
        job = await worker._queue.reserve(timeout_seconds=5)
        assert job is not None
        await worker.process(job)

        embedding = await worker._vectors.get(
            sample.face_sample_uuid, worker._recognizer.provenance
        )
        assert embedding is not None
        # The same photograph enrolled under different people yields identical
        # vectors, so several samples legitimately tie at 1.0. Assert ours is
        # among them rather than pretending the tie has an order.
        matches = await worker._vectors.search(embedding, limit=25)
        mine = [m for m in matches if m.face_sample_uuid == sample.face_sample_uuid]
        assert len(mine) == 1
        assert mine[0].person_uuid == person.person_uuid
        assert mine[0].score == pytest.approx(1.0, abs=1e-3)

    async def test_an_image_without_a_face_is_recorded_as_failed(
        self, worker: EmbeddingWorker, postgres: PostgresConnector
    ) -> None:
        _, sample = await _enrol(worker, _blank_jpeg(), postgres)
        job = await worker._queue.reserve(timeout_seconds=5)
        assert job is not None
        await worker.process(job)

        async with postgres.session() as session:
            stored = await SqlAlchemyFaceSampleRepository(session).get(sample.face_sample_uuid)
        assert stored is not None
        assert stored.processing_state is ProcessingState.FAILED
        assert "no face" in (stored.failure_reason or "")

    async def test_a_failed_job_leaves_no_vector(
        self, worker: EmbeddingWorker, postgres: PostgresConnector
    ) -> None:
        _, sample = await _enrol(worker, _blank_jpeg(), postgres)
        job = await worker._queue.reserve(timeout_seconds=5)
        assert job is not None
        await worker.process(job)
        assert (
            await worker._vectors.get(sample.face_sample_uuid, worker._recognizer.provenance)
            is None
        )

    async def test_a_missing_object_fails_the_job_rather_than_crashing(
        self, worker: EmbeddingWorker, postgres: PostgresConnector
    ) -> None:
        _, sample = await _enrol(worker, _face_jpeg(), postgres)
        job = await worker._queue.reserve(timeout_seconds=5)
        assert job is not None
        await worker._objects.delete(job.image_sha256)
        await worker.process(job)

        async with postgres.session() as session:
            stored = await SqlAlchemyFaceSampleRepository(session).get(sample.face_sample_uuid)
        assert stored is not None
        assert stored.processing_state is ProcessingState.FAILED
        assert "missing from the object store" in (stored.failure_reason or "")

    async def test_an_undecodable_image_fails_the_job(
        self, worker: EmbeddingWorker, postgres: PostgresConnector
    ) -> None:
        _, sample = await _enrol(worker, b"\xff\xd8\xffnot-really-a-jpeg", postgres)
        job = await worker._queue.reserve(timeout_seconds=5)
        assert job is not None
        await worker.process(job)

        async with postgres.session() as session:
            stored = await SqlAlchemyFaceSampleRepository(session).get(sample.face_sample_uuid)
        assert stored is not None
        assert stored.processing_state is ProcessingState.FAILED
        assert "could not be decoded" in (stored.failure_reason or "")

    async def test_an_image_with_two_faces_is_refused_rather_than_guessed(
        self, worker: EmbeddingWorker, postgres: PostgresConnector
    ) -> None:
        decoded = cv2.imdecode(np.frombuffer(_face_jpeg(), np.uint8), cv2.IMREAD_COLOR)
        assert decoded is not None
        face = decoded.astype(np.uint8)
        canvas = np.zeros((512, 1024, 3), dtype=np.uint8)
        canvas[:, :512], canvas[:, 512:] = face, face
        ok, encoded = cv2.imencode(".jpg", canvas)
        assert ok

        _, sample = await _enrol(worker, bytes(encoded.tobytes()), postgres)
        job = await worker._queue.reserve(timeout_seconds=5)
        assert job is not None
        await worker.process(job)

        async with postgres.session() as session:
            stored = await SqlAlchemyFaceSampleRepository(session).get(sample.face_sample_uuid)
        assert stored is not None
        assert stored.processing_state is ProcessingState.FAILED
        assert "requires exactly one" in (stored.failure_reason or "")

    async def test_processing_is_idempotent_for_a_repeated_job(
        self, worker: EmbeddingWorker, postgres: PostgresConnector
    ) -> None:
        person, sample = await _enrol(worker, _face_jpeg(), postgres)
        job = await worker._queue.reserve(timeout_seconds=5)
        assert job is not None
        await worker.process(job)
        # Replaying the same job must converge, not duplicate the vector.
        await worker.process(
            EmbeddingJob(
                face_sample_uuid=sample.face_sample_uuid,
                person_uuid=person.person_uuid,
                image_sha256=sample.image_sha256,
            )
        )
        embedding = await worker._vectors.get(
            sample.face_sample_uuid, worker._recognizer.provenance
        )
        assert embedding is not None
        matches = await worker._vectors.search(embedding, limit=10)
        assert [m.face_sample_uuid for m in matches].count(sample.face_sample_uuid) == 1
