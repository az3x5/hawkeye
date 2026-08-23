"""Enrolment idempotency, tested against in-memory fakes.

Fakes rather than a database, so these tests pin the *rules* of enrolment
without depending on any storage technology.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

import pytest

from app.connectors.filesystem.object_store import sha256_bytes
from app.domain.jobs import EmbeddingJob, ProcessingState
from app.domain.models import DomainValidationError, ExternalIdentifier, FaceSample, Person
from app.domain.repositories import ConflictError
from app.services.enrolment import (
    EnrolmentError,
    EnrolmentRequest,
    EnrolmentService,
    SampleReader,
)

IMAGE = b"\xff\xd8\xff-pretend-jpeg-bytes"
OTHER_IMAGE = b"\xff\xd8\xff-a-different-photograph"


class FakePeople:
    def __init__(self) -> None:
        self.people: dict[UUID, Person] = {}
        self.links: dict[str, UUID] = {}

    async def add(self, person: Person) -> Person:
        self.people[person.person_uuid] = person
        return person

    async def get(self, person_uuid: UUID) -> Person | None:
        return self.people.get(person_uuid)

    async def link_external_identifier(
        self, person_uuid: UUID, identifier: ExternalIdentifier
    ) -> None:
        owner = self.links.get(str(identifier))
        if owner is not None and owner != person_uuid:
            raise ConflictError("already linked elsewhere")
        self.links[str(identifier)] = person_uuid

    async def find_by_external_identifier(self, identifier: ExternalIdentifier) -> Person | None:
        owner = self.links.get(str(identifier))
        return self.people.get(owner) if owner else None

    async def list_external_identifiers(self, person_uuid: UUID) -> Sequence[ExternalIdentifier]:
        return []

    async def discard_if_unused(self, person_uuid: UUID) -> bool:
        if any(owner == person_uuid for owner in self.links.values()):
            return False
        return self.people.pop(person_uuid, None) is not None


class FakeSamples:
    def __init__(self) -> None:
        self.samples: dict[UUID, FaceSample] = {}
        self.conflict_once = False

    async def add(self, sample: FaceSample) -> FaceSample:
        if self.conflict_once:
            # Model losing a race: another writer's row lands first, then ours
            # conflicts against it.
            self.conflict_once = False
            winner = FaceSample(
                person_uuid=sample.person_uuid,
                image_sha256=sample.image_sha256,
                source=sample.source,
            )
            self.samples[winner.face_sample_uuid] = winner
            raise ConflictError("duplicate")
        key = (sample.person_uuid, sample.image_sha256)
        for existing in self.samples.values():
            if (existing.person_uuid, existing.image_sha256) == key:
                raise ConflictError("duplicate")
        self.samples[sample.face_sample_uuid] = sample
        return sample

    async def get(self, face_sample_uuid: UUID) -> FaceSample | None:
        return self.samples.get(face_sample_uuid)

    async def list_for_person(self, person_uuid: UUID) -> Sequence[FaceSample]:
        return [s for s in self.samples.values() if s.person_uuid == person_uuid]

    async def find_by_content_hash(self, person_uuid: UUID, image_sha256: str) -> FaceSample | None:
        for sample in self.samples.values():
            if sample.person_uuid == person_uuid and sample.image_sha256 == image_sha256:
                return sample
        return None

    async def mark_processed(self, face_sample_uuid: UUID) -> None:
        raise NotImplementedError

    async def mark_failed(self, face_sample_uuid: UUID, reason: str) -> None:
        raise NotImplementedError


class FakeObjects:
    def __init__(self) -> None:
        self.stored: dict[str, bytes] = {}

    async def put(self, digest: str, data: bytes) -> None:
        self.stored[digest] = data

    async def get(self, digest: str) -> bytes | None:
        return self.stored.get(digest)

    async def delete(self, digest: str) -> bool:
        return self.stored.pop(digest, None) is not None

    async def list_digests(self, *, older_than: datetime | None = None) -> list[str]:
        return list(self.stored)


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[EmbeddingJob] = []

    async def enqueue(self, job: EmbeddingJob) -> None:
        self.jobs.append(job)

    async def reserve(self, *, timeout_seconds: int) -> EmbeddingJob | None:
        return self.jobs.pop() if self.jobs else None

    async def complete(self, job: EmbeddingJob) -> None:
        return None

    async def fail(self, job: EmbeddingJob, reason: str) -> None:
        return None

    async def depth(self) -> int:
        return len(self.jobs)


@pytest.fixture
def people() -> FakePeople:
    return FakePeople()


@pytest.fixture
def samples() -> FakeSamples:
    return FakeSamples()


@pytest.fixture
def objects() -> FakeObjects:
    return FakeObjects()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def service(
    people: FakePeople, samples: FakeSamples, objects: FakeObjects, queue: FakeQueue
) -> EnrolmentService:
    return EnrolmentService(people=people, samples=samples, objects=objects, queue=queue)


def _request(**overrides: object) -> EnrolmentRequest:
    values: dict[str, object] = {"source": "crm", "image": IMAGE, "external_id": "42"}
    values.update(overrides)
    return EnrolmentRequest(**values)  # type: ignore[arg-type]


class TestFirstEnrolment:
    async def test_creates_a_person_and_a_sample(self, service: EnrolmentService) -> None:
        result = await service.enrol(_request())
        assert result.created is True
        assert result.sample.person_uuid == result.person.person_uuid
        assert result.sample.processing_state is ProcessingState.PENDING

    async def test_stores_the_image_under_its_content_hash(
        self, service: EnrolmentService, objects: FakeObjects
    ) -> None:
        await service.enrol(_request())
        assert objects.stored == {sha256_bytes(IMAGE): IMAGE}

    async def test_enqueues_exactly_one_job(
        self, service: EnrolmentService, queue: FakeQueue
    ) -> None:
        result = await service.enrol(_request())
        assert len(queue.jobs) == 1
        assert queue.jobs[0].face_sample_uuid == result.sample.face_sample_uuid

    async def test_the_job_carries_no_image_bytes(
        self, service: EnrolmentService, queue: FakeQueue
    ) -> None:
        await service.enrol(_request())
        payload = queue.jobs[0].to_payload()
        assert IMAGE.hex() not in str(payload)
        assert set(payload) == {
            "face_sample_uuid",
            "person_uuid",
            "image_sha256",
            "enqueued_at",
        }


class TestIdempotency:
    async def test_the_same_submission_twice_converges(
        self, service: EnrolmentService, queue: FakeQueue
    ) -> None:
        first = await service.enrol(_request())
        second = await service.enrol(_request())

        assert second.created is False
        assert second.person.person_uuid == first.person.person_uuid
        assert second.sample.face_sample_uuid == first.sample.face_sample_uuid
        assert len(queue.jobs) == 1, "a repeat must not schedule the work again"

    async def test_repeating_many_times_stays_stable(
        self, service: EnrolmentService, queue: FakeQueue
    ) -> None:
        first = await service.enrol(_request())
        for _ in range(5):
            repeat = await service.enrol(_request())
            assert repeat.sample.face_sample_uuid == first.sample.face_sample_uuid
        assert len(queue.jobs) == 1

    async def test_a_second_image_adds_a_sample_to_the_same_person(
        self, service: EnrolmentService, queue: FakeQueue
    ) -> None:
        first = await service.enrol(_request())
        second = await service.enrol(_request(image=OTHER_IMAGE))

        assert second.created is True
        assert second.person.person_uuid == first.person.person_uuid
        assert second.sample.face_sample_uuid != first.sample.face_sample_uuid
        assert len(queue.jobs) == 2

    async def test_the_same_image_for_two_people_is_two_samples(
        self, service: EnrolmentService
    ) -> None:
        first = await service.enrol(_request(external_id="1"))
        second = await service.enrol(_request(external_id="2"))
        assert first.person.person_uuid != second.person.person_uuid
        assert first.sample.face_sample_uuid != second.sample.face_sample_uuid

    async def test_a_concurrent_duplicate_converges_rather_than_failing(
        self, service: EnrolmentService, samples: FakeSamples, queue: FakeQueue
    ) -> None:
        samples.conflict_once = True
        result = await service.enrol(_request())

        # The caller gets the row that won, not an error.
        assert result.created is False
        assert result.sample.image_sha256 == sha256_bytes(IMAGE)
        assert len(samples.samples) == 1, "the loser must not leave a second row"
        assert queue.jobs == [], "the winner schedules the work, not the loser"


class TestIdentifiers:
    async def test_local_id_alone_is_enough(self, service: EnrolmentService) -> None:
        result = await service.enrol(_request(external_id=None, local_id="L-1"))
        assert result.created is True

    async def test_at_least_one_identifier_is_required(self, service: EnrolmentService) -> None:
        with pytest.raises(DomainValidationError, match="external_id or local_id"):
            await service.enrol(_request(external_id=None, local_id=None))

    async def test_identifiers_are_scoped_by_source(self, service: EnrolmentService) -> None:
        first = await service.enrol(_request(source="crm"))
        second = await service.enrol(_request(source="hr"))
        assert first.person.person_uuid != second.person.person_uuid

    async def test_both_identifiers_resolve_to_one_person(self, service: EnrolmentService) -> None:
        first = await service.enrol(_request(external_id="42", local_id="L-42"))
        by_id = await service.enrol(_request(external_id="42", image=OTHER_IMAGE))
        assert by_id.person.person_uuid == first.person.person_uuid

    async def test_identifiers_denoting_two_people_are_refused(
        self, service: EnrolmentService
    ) -> None:
        first = await service.enrol(_request(external_id="1"))
        second = await service.enrol(_request(local_id="2", external_id=None, image=OTHER_IMAGE))
        assert first.person.person_uuid != second.person.person_uuid

        # One request now names an identifier belonging to each of them.
        with pytest.raises(EnrolmentError, match="two different people"):
            await service.enrol(_request(external_id="1", local_id="2"))


class TestValidation:
    async def test_an_empty_image_is_refused(self, service: EnrolmentService) -> None:
        with pytest.raises(Exception, match="image is required"):
            await service.enrol(_request(image=b""))


class TestSampleReader:
    async def test_reads_back_an_enrolled_sample(
        self, service: EnrolmentService, samples: FakeSamples
    ) -> None:
        result = await service.enrol(_request())
        found = await SampleReader(samples).get(result.sample.face_sample_uuid)
        assert found is not None
        assert found.face_sample_uuid == result.sample.face_sample_uuid

    async def test_unknown_sample_is_none(self, samples: FakeSamples) -> None:
        assert await SampleReader(samples).get(uuid4()) is None
