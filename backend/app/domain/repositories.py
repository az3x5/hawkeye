"""Repository interfaces.

Services depend on these protocols, never on a database driver or ORM. The
Postgres implementations live in ``app.connectors.postgres``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.domain.models import ExternalIdentifier, FaceSample, Person


class ConflictError(Exception):
    """The requested write conflicts with data that already exists."""


@runtime_checkable
class PersonRepository(Protocol):
    """Persistence of people and their external identifiers."""

    async def add(self, person: Person) -> Person:
        """Insert ``person``. Raises ConflictError if the uuid already exists."""
        ...

    async def get(self, person_uuid: UUID) -> Person | None:
        """Return the person with ``person_uuid``, or None."""
        ...

    async def link_external_identifier(
        self, person_uuid: UUID, identifier: ExternalIdentifier
    ) -> None:
        """Attach ``identifier`` to a person.

        Re-linking the same identifier to the same person is a no-op, so that
        repeated upstream deliveries converge. Linking it to a *different*
        person raises ConflictError: one external identifier denotes one
        person within its source.
        """
        ...

    async def find_by_external_identifier(self, identifier: ExternalIdentifier) -> Person | None:
        """Resolve a source-scoped external identifier to a person, or None."""
        ...

    async def list_external_identifiers(self, person_uuid: UUID) -> Sequence[ExternalIdentifier]:
        """Return every external identifier attached to a person."""
        ...


@runtime_checkable
class FaceSampleRepository(Protocol):
    """Persistence of face samples. A person may have arbitrarily many."""

    async def add(self, sample: FaceSample) -> FaceSample:
        """Insert ``sample``.

        Raises ConflictError if the same person already has a sample with the
        same ``image_sha256``, or if the person does not exist.
        """
        ...

    async def get(self, face_sample_uuid: UUID) -> FaceSample | None:
        """Return the sample with ``face_sample_uuid``, or None."""
        ...

    async def list_for_person(self, person_uuid: UUID) -> Sequence[FaceSample]:
        """Return every sample belonging to a person, oldest first."""
        ...

    async def find_by_content_hash(self, person_uuid: UUID, image_sha256: str) -> FaceSample | None:
        """Return the person's sample with this content hash, or None."""
        ...
