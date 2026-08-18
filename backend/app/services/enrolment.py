"""Enrolment.

Records that a person has a face sample, and hands the slow work to a worker.
No model runs here: enrolment is a bookkeeping operation, and detection or
recognition failing must not make the caller's write fail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.connectors.filesystem.object_store import sha256_bytes
from app.domain.jobs import EmbeddingJob, JobQueue, ObjectStore, ProcessingState
from app.domain.models import (
    DomainValidationError,
    ExternalIdentifier,
    ExternalIdentifierKind,
    FaceSample,
    Person,
)
from app.domain.repositories import (
    ConflictError,
    FaceSampleRepository,
    PersonRepository,
)

logger = logging.getLogger(__name__)


class EnrolmentError(Exception):
    """Enrolment could not be completed."""


@dataclass(frozen=True, slots=True)
class EnrolmentRequest:
    """One enrolment submission.

    ``external_id`` and ``local_id`` are attributes scoped by ``source``; at
    least one must be present, since without an external identifier a repeated
    submission could not be recognised as a repeat.
    """

    source: str
    image: bytes
    external_id: str | None = None
    local_id: str | None = None
    captured_at: datetime | None = None

    def identifiers(self) -> list[ExternalIdentifier]:
        """Return the external identifiers carried by this request."""
        found = []
        if self.external_id is not None:
            found.append(
                ExternalIdentifier(
                    source=self.source,
                    kind=ExternalIdentifierKind.ID,
                    value=self.external_id,
                )
            )
        if self.local_id is not None:
            found.append(
                ExternalIdentifier(
                    source=self.source,
                    kind=ExternalIdentifierKind.LOCAL_ID,
                    value=self.local_id,
                )
            )
        if not found:
            raise DomainValidationError("at least one of external_id or local_id is required")
        return found


@dataclass(frozen=True, slots=True)
class EnrolmentResult:
    """What an enrolment did.

    ``created`` distinguishes the first submission from a repeat. A repeat is
    not an error: it returns the same identifiers and enqueues nothing.
    """

    person: Person
    sample: FaceSample
    created: bool

    @property
    def person_created(self) -> bool:
        """Whether this enrolment brought the person into existence."""
        return self.created and self.sample.processing_state is ProcessingState.PENDING


class SampleReader:
    """Reads face samples for the API, without exposing a repository to it."""

    def __init__(self, samples: FaceSampleRepository) -> None:
        """Bind the reader to a repository."""
        self._samples = samples

    async def get(self, face_sample_uuid: UUID) -> FaceSample | None:
        """Return one sample, or None."""
        return await self._samples.get(face_sample_uuid)


class EnrolmentService:
    """Idempotent enrolment of face samples.

    Idempotency is keyed on the source-scoped external identifier plus the
    image's content hash. Submitting the same image for the same person twice
    converges on one `person_uuid` and one `face_sample_uuid`, and enqueues the
    embedding work once.
    """

    def __init__(
        self,
        *,
        people: PersonRepository,
        samples: FaceSampleRepository,
        objects: ObjectStore,
        queue: JobQueue,
    ) -> None:
        """Wire the service to its collaborators."""
        self._people = people
        self._samples = samples
        self._objects = objects
        self._queue = queue

    async def enrol(self, request: EnrolmentRequest) -> EnrolmentResult:
        """Enrol one face sample, creating the person if this is the first sight."""
        if not request.image:
            raise EnrolmentError("an enrolment image is required")

        identifiers = request.identifiers()
        person = await self._resolve_person(identifiers)
        digest = sha256_bytes(request.image)

        existing = await self._samples.find_by_content_hash(person.person_uuid, digest)
        if existing is not None:
            # A repeat. Deliberately does not re-enqueue: the first submission
            # already scheduled the work, and duplicating it would double the
            # cost for no result.
            logger.info(
                "enrolment repeated for an existing sample",
                extra={
                    "person_uuid": str(person.person_uuid),
                    "face_sample_uuid": str(existing.face_sample_uuid),
                },
            )
            return EnrolmentResult(person=person, sample=existing, created=False)

        # Store the image before the row that points at it, so a row never
        # references an object the worker cannot read.
        await self._objects.put(digest, request.image)

        sample = FaceSample(
            person_uuid=person.person_uuid,
            image_sha256=digest,
            source=request.source,
            captured_at=request.captured_at,
        )
        try:
            await self._samples.add(sample)
        except ConflictError:
            # Lost a race with a concurrent identical enrolment; converge on
            # whichever sample won rather than failing the caller.
            won = await self._samples.find_by_content_hash(person.person_uuid, digest)
            if won is None:
                raise
            return EnrolmentResult(person=person, sample=won, created=False)

        await self._queue.enqueue(
            EmbeddingJob(
                face_sample_uuid=sample.face_sample_uuid,
                person_uuid=person.person_uuid,
                image_sha256=digest,
            )
        )
        logger.info(
            "face sample enrolled",
            extra={
                "person_uuid": str(person.person_uuid),
                "face_sample_uuid": str(sample.face_sample_uuid),
                "source": request.source,
            },
        )
        return EnrolmentResult(person=person, sample=sample, created=True)

    async def _resolve_person(self, identifiers: list[ExternalIdentifier]) -> Person:
        """Find the person these identifiers denote, or create them."""
        found: Person | None = None
        for identifier in identifiers:
            owner = await self._people.find_by_external_identifier(identifier)
            if owner is None:
                continue
            if found is not None and owner.person_uuid != found.person_uuid:
                raise EnrolmentError(
                    "the supplied identifiers already denote two different people; "
                    "merging them is a review decision, not an enrolment"
                )
            found = owner

        person = found or await self._people.add(Person())
        for identifier in identifiers:
            await self._people.link_external_identifier(person.person_uuid, identifier)
        return person
