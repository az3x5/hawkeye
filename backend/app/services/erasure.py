"""Erasure of a person and their biometric material.

Deleting a row from the metadata store is not erasure. A person's face lives in
three places — metadata, vectors and stored images — and all three must go
together, or the face remains searchable through whichever one was missed.

The audit record deliberately outlives the person: a deletion that leaves no
trace cannot be shown to have happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.tables import face_samples, identifications, persons
from app.domain.audit import SYSTEM_ACTOR, Actor, AuditAction, AuditEvent, AuditLog
from app.domain.jobs import ObjectStore
from app.domain.vectors import VectorRepository

logger = logging.getLogger(__name__)


class PersonNotFoundError(Exception):
    """No such person."""


@dataclass(frozen=True, slots=True)
class ErasureReport:
    """What an erasure removed."""

    person_uuid: UUID
    samples_removed: int
    vectors_removed: int
    images_removed: int


class PersonEraser:
    """Removes a person and everything derived from their face."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        vectors: VectorRepository,
        objects: ObjectStore,
        audit: AuditLog,
    ) -> None:
        """Wire the eraser to the three stores a person occupies."""
        self._session = session
        self._vectors = vectors
        self._objects = objects
        self._audit = audit

    async def erase(self, person_uuid: UUID, *, actor: Actor, reason: str | None) -> ErasureReport:
        """Erase a person's metadata, vectors and images.

        Ordering matters. Image hashes are read before the rows are deleted,
        because the rows are what name them; vectors go before the metadata, so
        a failure part-way leaves a person whose vectors are gone rather than
        vectors nobody can attribute.
        """
        exists = await self._session.execute(
            select(persons.c.person_uuid).where(persons.c.person_uuid == person_uuid)
        )
        if exists.one_or_none() is None:
            raise PersonNotFoundError(f"no person {person_uuid}")

        owned = await self._session.execute(
            select(face_samples.c.image_sha256).where(face_samples.c.person_uuid == person_uuid)
        )
        hashes = {row.image_sha256 for row in owned.all()}

        vectors_removed = await self._vectors.delete_person_everywhere(person_uuid)

        # Samples cascade from the person; embedding metadata cascades from the
        # samples.
        await self._session.execute(persons.delete().where(persons.c.person_uuid == person_uuid))

        images_removed = 0
        for digest in hashes:
            still_used = await self._session.execute(
                select(face_samples.c.face_sample_uuid)
                .where(face_samples.c.image_sha256 == digest)
                .limit(1)
            )
            # Another person may have been enrolled from the same photograph.
            # Erasing one of them must not destroy the other's sample.
            if still_used.one_or_none() is not None:
                continue
            if await self._objects.delete(digest):
                images_removed += 1

        await self._audit.record(
            AuditEvent(
                action=AuditAction.PERSON_ERASED,
                actor=actor,
                person_uuid=person_uuid,
                details={
                    "samples_removed": len(hashes),
                    "vectors_removed": vectors_removed,
                    "images_removed": images_removed,
                    "reason": reason,
                },
            )
        )
        logger.info(
            "person erased",
            extra={
                "person_uuid": str(person_uuid),
                "vectors_removed": vectors_removed,
                "images_removed": images_removed,
            },
        )
        return ErasureReport(
            person_uuid=person_uuid,
            samples_removed=len(hashes),
            vectors_removed=vectors_removed,
            images_removed=images_removed,
        )


async def reconcile_orphaned_vectors(
    *,
    session: AsyncSession,
    vectors: VectorRepository,
    audit: AuditLog,
    dry_run: bool = False,
) -> int:
    """Remove vectors whose person no longer exists in the metadata store.

    Erasure keeps the stores in step from now on; this catches anything that
    fell out of step before, or through a failure part-way. A face with no
    person attached is the worst kind of leftover: still searchable, and no
    longer attributable to anyone who could ask for its removal.
    """
    orphaned: set[UUID] = set()
    for collection in await vectors.collections():
        referenced = await vectors.person_uuids(collection)
        if not referenced:
            continue
        known = await session.execute(
            select(persons.c.person_uuid).where(persons.c.person_uuid.in_(referenced))
        )
        orphaned |= referenced - {row.person_uuid for row in known.all()}

    if not orphaned:
        return 0
    if dry_run:
        logger.warning("orphaned vectors found", extra={"orphaned_people": len(orphaned)})
        return len(orphaned)

    removed = 0
    for person_uuid in orphaned:
        removed += await vectors.delete_person_everywhere(person_uuid)
        await audit.record(
            AuditEvent(
                action=AuditAction.ORPHANED_VECTORS_PURGED,
                actor=SYSTEM_ACTOR,
                person_uuid=person_uuid,
                details={"reason": "no such person in the metadata store"},
            )
        )
    logger.warning(
        "purged orphaned vectors",
        extra={"orphaned_people": len(orphaned), "vectors_removed": removed},
    )
    return removed


#: How long an object must have sat unreferenced before reconciliation will
#: consider it abandoned. Enrolment writes the image before the row that names
#: it, so a shorter window would race live work and delete an image the worker
#: is about to need.
ORPHANED_IMAGE_GRACE_SECONDS = 3600


async def reconcile_orphaned_images(
    *,
    session: AsyncSession,
    objects: ObjectStore,
    audit: AuditLog,
    grace_seconds: int = ORPHANED_IMAGE_GRACE_SECONDS,
    dry_run: bool = False,
    now: datetime | None = None,
) -> int:
    """Remove stored images that nothing references any more.

    The counterpart to vector reconciliation. Erasure removes a person's images
    with them, but a crash between deleting the rows and deleting the objects
    would leave a face on disk that nothing points at — still biometric data,
    and now attached to nobody who could ask for its removal.

    An object is abandoned when no face sample and no identification names its
    hash, and it has been untouched for the grace period.
    """
    if grace_seconds < 1:
        raise ValueError(f"grace_seconds must be at least 1, got {grace_seconds}")

    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=grace_seconds)
    stored = await objects.list_digests(older_than=cutoff)
    if not stored:
        return 0

    enrolled = await session.execute(
        select(face_samples.c.image_sha256).where(face_samples.c.image_sha256.in_(stored))
    )
    queried = await session.execute(
        select(identifications.c.query_sha256).where(identifications.c.query_sha256.in_(stored))
    )
    referenced = {row.image_sha256 for row in enrolled.all()} | {
        row.query_sha256 for row in queried.all()
    }

    abandoned = [digest for digest in stored if digest not in referenced]
    if not abandoned:
        return 0
    if dry_run:
        logger.warning("orphaned images found", extra={"orphaned_images": len(abandoned)})
        return len(abandoned)

    removed = 0
    for digest in abandoned:
        if not await objects.delete(digest):
            continue
        removed += 1
        await audit.record(
            AuditEvent(
                action=AuditAction.ORPHANED_IMAGE_PURGED,
                actor=SYSTEM_ACTOR,
                details={
                    "image_sha256": digest,
                    "reason": "no face sample or identification references it",
                },
            )
        )
    if removed:
        logger.warning("purged orphaned images", extra={"removed": removed})
    return removed
