"""Repository implementations backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.tables import (
    face_embeddings,
    face_samples,
    person_external_identifiers,
    persons,
)
from app.domain.jobs import ProcessingState
from app.domain.models import ExternalIdentifier, ExternalIdentifierKind, FaceSample, Person
from app.domain.recognition import EmbeddingProvenance
from app.domain.repositories import ConflictError


def _to_person(row: Row[tuple[UUID, object, object]]) -> Person:
    return Person(
        person_uuid=row.person_uuid,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_face_sample(row: Row[tuple[object, ...]]) -> FaceSample:
    return FaceSample(
        face_sample_uuid=row.face_sample_uuid,
        person_uuid=row.person_uuid,
        image_sha256=row.image_sha256,
        source=row.source,
        captured_at=row.captured_at,
        created_at=row.created_at,
        processing_state=ProcessingState(row.processing_state),
        processed_at=row.processed_at,
        failure_reason=row.failure_reason,
    )


class SqlAlchemyPersonRepository:
    """``PersonRepository`` over the ``persons`` tables."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an open session/transaction."""
        self._session = session

    async def add(self, person: Person) -> Person:
        """Insert ``person``."""
        try:
            await self._session.execute(
                persons.insert().values(
                    person_uuid=person.person_uuid,
                    created_at=person.created_at,
                    updated_at=person.updated_at,
                )
            )
        except IntegrityError as exc:
            raise ConflictError(f"person {person.person_uuid} already exists") from exc
        return person

    async def get(self, person_uuid: UUID) -> Person | None:
        """Return the person with ``person_uuid``, or None."""
        result = await self._session.execute(
            select(persons).where(persons.c.person_uuid == person_uuid)
        )
        row = result.one_or_none()
        return _to_person(row) if row is not None else None

    async def link_external_identifier(
        self, person_uuid: UUID, identifier: ExternalIdentifier
    ) -> None:
        """Attach ``identifier`` to a person; idempotent for the same person."""
        owner = await self.find_by_external_identifier(identifier)
        if owner is not None:
            if owner.person_uuid == person_uuid:
                return
            raise ConflictError(
                f"external identifier {identifier} is already linked to a different person"
            )
        try:
            await self._session.execute(
                person_external_identifiers.insert().values(
                    person_uuid=person_uuid,
                    source=identifier.source,
                    kind=identifier.kind.value,
                    value=identifier.value,
                )
            )
        except IntegrityError as exc:
            # Lost a race with a concurrent linker, or the person is unknown.
            raise ConflictError(
                f"could not link external identifier {identifier} to person {person_uuid}"
            ) from exc

    async def find_by_external_identifier(self, identifier: ExternalIdentifier) -> Person | None:
        """Resolve a source-scoped external identifier to a person, or None."""
        table = person_external_identifiers
        result = await self._session.execute(
            select(persons)
            .join(table, table.c.person_uuid == persons.c.person_uuid)
            .where(
                table.c.source == identifier.source,
                table.c.kind == identifier.kind.value,
                table.c.value == identifier.value,
            )
        )
        row = result.one_or_none()
        return _to_person(row) if row is not None else None

    async def list_external_identifiers(self, person_uuid: UUID) -> Sequence[ExternalIdentifier]:
        """Return every external identifier attached to a person."""
        table = person_external_identifiers
        result = await self._session.execute(
            select(table.c.source, table.c.kind, table.c.value)
            .where(table.c.person_uuid == person_uuid)
            .order_by(table.c.created_at, table.c.source, table.c.value)
        )
        return [
            ExternalIdentifier(
                source=row.source, kind=ExternalIdentifierKind(row.kind), value=row.value
            )
            for row in result.all()
        ]


class SqlAlchemyFaceSampleRepository:
    """``FaceSampleRepository`` over the ``face_samples`` table."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an open session/transaction."""
        self._session = session

    async def add(self, sample: FaceSample) -> FaceSample:
        """Insert ``sample``."""
        try:
            await self._session.execute(
                face_samples.insert().values(
                    face_sample_uuid=sample.face_sample_uuid,
                    person_uuid=sample.person_uuid,
                    image_sha256=sample.image_sha256,
                    source=sample.source,
                    captured_at=sample.captured_at,
                    created_at=sample.created_at,
                    processing_state=sample.processing_state.value,
                    processed_at=sample.processed_at,
                    failure_reason=sample.failure_reason,
                )
            )
        except IntegrityError as exc:
            raise ConflictError(
                f"cannot store face sample for person {sample.person_uuid}: "
                "the person is unknown or this image is already recorded for them"
            ) from exc
        return sample

    async def get(self, face_sample_uuid: UUID) -> FaceSample | None:
        """Return the sample with ``face_sample_uuid``, or None."""
        result = await self._session.execute(
            select(face_samples).where(face_samples.c.face_sample_uuid == face_sample_uuid)
        )
        row = result.one_or_none()
        return _to_face_sample(row) if row is not None else None

    async def list_for_person(self, person_uuid: UUID) -> Sequence[FaceSample]:
        """Return every sample belonging to a person, oldest first."""
        result = await self._session.execute(
            select(face_samples)
            .where(face_samples.c.person_uuid == person_uuid)
            .order_by(face_samples.c.created_at, face_samples.c.face_sample_uuid)
        )
        return [_to_face_sample(row) for row in result.all()]

    async def mark_processed(self, face_sample_uuid: UUID) -> None:
        """Record that a sample has been embedded successfully."""
        await self._set_state(face_sample_uuid, ProcessingState.PROCESSED, None)

    async def mark_failed(self, face_sample_uuid: UUID, reason: str) -> None:
        """Record that a sample could not be embedded, and why."""
        await self._set_state(face_sample_uuid, ProcessingState.FAILED, reason)

    async def _set_state(
        self, face_sample_uuid: UUID, state: ProcessingState, reason: str | None
    ) -> None:
        result = await self._session.execute(
            face_samples.update()
            .where(face_samples.c.face_sample_uuid == face_sample_uuid)
            .values(
                processing_state=state.value,
                processed_at=datetime.now(UTC),
                failure_reason=reason,
            )
        )
        # execute() is typed as Result; an UPDATE always yields a CursorResult,
        # which is what carries rowcount.
        if cast("CursorResult[Any]", result).rowcount == 0:
            raise ConflictError(f"no face sample {face_sample_uuid} to update")

    async def find_by_content_hash(self, person_uuid: UUID, image_sha256: str) -> FaceSample | None:
        """Return the person's sample with this content hash, or None."""
        result = await self._session.execute(
            select(face_samples).where(
                face_samples.c.person_uuid == person_uuid,
                face_samples.c.image_sha256 == image_sha256,
            )
        )
        row = result.one_or_none()
        return _to_face_sample(row) if row is not None else None


class SqlAlchemyEmbeddingMetadataRepository:
    """Records which sample was embedded, under what provenance, and where.

    The vector itself lives in the vector store; this row is what makes a model
    migration observable from the metadata store, and what lets a stored vector
    be attributed to the model and preprocessing that produced it.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an open session/transaction."""
        self._session = session

    async def record(
        self,
        *,
        face_sample_uuid: UUID,
        provenance: EmbeddingProvenance,
        collection: str,
        dimension: int,
    ) -> None:
        """Record an embedding, or leave the existing row alone.

        Re-embedding the same sample under the same provenance is idempotent:
        the vector was replaced in place, so there is nothing new to record.
        """
        await self._session.execute(
            insert(face_embeddings)
            .values(
                embedding_uuid=uuid4(),
                face_sample_uuid=face_sample_uuid,
                model_name=provenance.model_name,
                model_version=provenance.model_version,
                preprocessing_version=provenance.preprocessing_version,
                vector_collection=collection,
                dimension=dimension,
            )
            .on_conflict_do_nothing(constraint="uq_face_embedding_sample_provenance")
        )
