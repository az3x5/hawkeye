"""Read-side queries.

Separate from the repositories because these serve *browsing* rather than the
write paths: they aggregate, paginate and join in ways the domain repositories
deliberately do not. Nothing here mutates anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.tables import (
    audit_events,
    face_embeddings,
    face_samples,
    identifications,
    language_documents,
    person_external_identifiers,
    persons,
)

#: Nothing may ask for more than this in one page, however it asks.
MAX_PAGE = 200


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of results, and how many there are in total."""

    items: list[T]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class PersonSummary:
    """A person as they appear in a list."""

    person_uuid: UUID
    created_at: datetime
    sample_count: int
    processed_count: int
    identifiers: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One audit event, flattened for reading."""

    audit_uuid: UUID
    occurred_at: datetime
    action: str
    actor_identifier: str
    actor_kind: str
    person_uuid: UUID | None
    face_sample_uuid: UUID | None
    identification_uuid: UUID | None
    policy_version: str | None
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Statistics:
    """Counts the dashboard reports. Every figure is a real query."""

    persons: int
    face_samples: int
    samples_by_state: dict[str, int]
    embeddings: int
    identifications: int
    identifications_by_outcome: dict[str, int]
    awaiting_review: int
    reviews_recorded: int
    audit_events: int
    language_documents: int
    language_documents_by_state: dict[str, int]


@dataclass(frozen=True, slots=True)
class LanguageDocumentSummary:
    """One imported text record without returning its potentially sensitive body."""

    document_uuid: UUID
    source_id: str
    source_type: str
    profile_id: str | None
    title: str
    source: str
    primary_script: str
    processing_state: str
    attributes: dict[str, Any]
    created_at: datetime
    processed_at: datetime | None


def _clamp(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, MAX_PAGE)), max(0, offset)


class ReadQueries:
    """Browsing queries over the metadata store."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the queries to an open session."""
        self._session = session

    # -- people -------------------------------------------------------------

    async def list_persons(
        self, *, limit: int = 50, offset: int = 0, search: str | None = None
    ) -> Page[PersonSummary]:
        """People, newest first, with how many samples each one has.

        ``search`` matches a person's uuid or any of their external
        identifiers, since those are the two ways anyone actually refers to
        someone.
        """
        limit, offset = _clamp(limit, offset)
        identifiers = person_external_identifiers

        matching = select(persons.c.person_uuid)
        if search:
            needle = f"%{search.strip().lower()}%"
            matching = matching.where(
                or_(
                    func.cast(persons.c.person_uuid, String).ilike(needle),
                    persons.c.person_uuid.in_(
                        select(identifiers.c.person_uuid).where(
                            or_(
                                func.lower(identifiers.c.value).like(needle),
                                func.lower(identifiers.c.source).like(needle),
                            )
                        )
                    ),
                )
            )

        total = await self._session.scalar(select(func.count()).select_from(matching.subquery()))

        rows = await self._session.execute(
            select(
                persons.c.person_uuid,
                persons.c.created_at,
                func.count(face_samples.c.face_sample_uuid).label("sample_count"),
                func.count(face_samples.c.face_sample_uuid)
                .filter(face_samples.c.processing_state == "processed")
                .label("processed_count"),
            )
            .select_from(
                persons.outerjoin(face_samples, face_samples.c.person_uuid == persons.c.person_uuid)
            )
            .where(persons.c.person_uuid.in_(matching))
            .group_by(persons.c.person_uuid, persons.c.created_at)
            .order_by(persons.c.created_at.desc(), persons.c.person_uuid)
            .limit(limit)
            .offset(offset)
        )
        found = rows.all()
        if not found:
            return Page(items=[], total=int(total or 0), limit=limit, offset=offset)

        # One query for the identifiers of everyone on this page, rather than
        # one per person.
        owned = await self._session.execute(
            select(identifiers).where(
                identifiers.c.person_uuid.in_([row.person_uuid for row in found])
            )
        )
        by_person: dict[UUID, list[dict[str, str]]] = {}
        for row in owned.all():
            by_person.setdefault(row.person_uuid, []).append(
                {"source": row.source, "kind": row.kind, "value": row.value}
            )

        return Page(
            items=[
                PersonSummary(
                    person_uuid=row.person_uuid,
                    created_at=row.created_at,
                    sample_count=int(row.sample_count),
                    processed_count=int(row.processed_count),
                    identifiers=by_person.get(row.person_uuid, []),
                )
                for row in found
            ],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def get_person(self, person_uuid: UUID) -> PersonSummary | None:
        """One person, or None."""
        row = await self._session.execute(
            select(persons.c.person_uuid, persons.c.created_at).where(
                persons.c.person_uuid == person_uuid
            )
        )
        found = row.one_or_none()
        if found is None:
            return None

        counts = await self._session.execute(
            select(
                func.count(face_samples.c.face_sample_uuid).label("sample_count"),
                func.count(face_samples.c.face_sample_uuid)
                .filter(face_samples.c.processing_state == "processed")
                .label("processed_count"),
            ).where(face_samples.c.person_uuid == person_uuid)
        )
        totals = counts.one()

        owned = await self._session.execute(
            select(person_external_identifiers).where(
                person_external_identifiers.c.person_uuid == person_uuid
            )
        )
        return PersonSummary(
            person_uuid=found.person_uuid,
            created_at=found.created_at,
            sample_count=int(totals.sample_count),
            processed_count=int(totals.processed_count),
            identifiers=[
                {"source": row.source, "kind": row.kind, "value": row.value} for row in owned.all()
            ],
        )

    async def samples_for_person(self, person_uuid: UUID) -> list[dict[str, Any]]:
        """A person's face samples, oldest first."""
        rows = await self._session.execute(
            select(face_samples)
            .where(face_samples.c.person_uuid == person_uuid)
            .order_by(face_samples.c.created_at)
        )
        return [dict(row._mapping) for row in rows.all()]

    # -- audit --------------------------------------------------------------

    async def list_identifications(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        outcome: str | None = None,
        reviewed: bool | None = None,
        person_uuid: UUID | None = None,
    ) -> Page[dict[str, Any]]:
        """Identification history, most recent first.

        Distinct from the review queue, which deliberately returns only
        unreviewed proposals: this is the record of what was decided.
        """
        limit, offset = _clamp(limit, offset)

        conditions = []
        if outcome:
            conditions.append(identifications.c.outcome == outcome)
        if reviewed is True:
            conditions.append(identifications.c.review_outcome.is_not(None))
        if reviewed is False:
            conditions.append(identifications.c.review_outcome.is_(None))
        if person_uuid is not None:
            conditions.append(identifications.c.best_person_uuid == person_uuid)
        where = and_(*conditions) if conditions else None

        counting = select(func.count()).select_from(identifications)
        listing = select(identifications)
        if where is not None:
            counting = counting.where(where)
            listing = listing.where(where)

        total = await self._session.scalar(counting)
        rows = await self._session.execute(
            listing.order_by(identifications.c.created_at.desc()).limit(limit).offset(offset)
        )
        return Page(
            items=[dict(row._mapping) for row in rows.all()],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def list_audit_events(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        action: str | None = None,
        actor: str | None = None,
        person_uuid: UUID | None = None,
    ) -> Page[AuditRecord]:
        """Audit events, most recent first."""
        limit, offset = _clamp(limit, offset)

        conditions = []
        if action:
            conditions.append(audit_events.c.action == action)
        if actor:
            conditions.append(
                func.lower(audit_events.c.actor_identifier).like(f"%{actor.lower()}%")
            )
        if person_uuid is not None:
            conditions.append(audit_events.c.person_uuid == person_uuid)
        where = and_(*conditions) if conditions else None

        counting = select(func.count()).select_from(audit_events)
        listing = select(audit_events)
        if where is not None:
            counting = counting.where(where)
            listing = listing.where(where)

        total = await self._session.scalar(counting)
        rows = await self._session.execute(
            listing.order_by(audit_events.c.occurred_at.desc()).limit(limit).offset(offset)
        )
        return Page(
            items=[
                AuditRecord(
                    audit_uuid=row.audit_uuid,
                    occurred_at=row.occurred_at,
                    action=row.action,
                    actor_identifier=row.actor_identifier,
                    actor_kind=row.actor_kind,
                    person_uuid=row.person_uuid,
                    face_sample_uuid=row.face_sample_uuid,
                    identification_uuid=row.identification_uuid,
                    policy_version=row.policy_version,
                    details=row.details,
                )
                for row in rows.all()
            ],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def audit_actions(self) -> list[str]:
        """Which actions actually appear in the log, for a filter control."""
        rows = await self._session.execute(
            select(audit_events.c.action).distinct().order_by(audit_events.c.action)
        )
        return [row.action for row in rows.all()]

    # -- statistics ---------------------------------------------------------

    async def list_language_documents(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        profile_id: str | None = None,
        processing_state: str | None = None,
    ) -> Page[LanguageDocumentSummary]:
        """List imported text records using indexed identity and state fields."""
        limit, offset = _clamp(limit, offset)
        conditions = []
        if profile_id:
            conditions.append(language_documents.c.profile_id == profile_id)
        if processing_state:
            conditions.append(language_documents.c.processing_state == processing_state)
        where = and_(*conditions) if conditions else None

        counting = select(func.count()).select_from(language_documents)
        listing = select(language_documents)
        if where is not None:
            counting = counting.where(where)
            listing = listing.where(where)

        total = await self._session.scalar(counting)
        rows = await self._session.execute(
            listing.order_by(language_documents.c.created_at.desc()).limit(limit).offset(offset)
        )
        return Page(
            items=[
                LanguageDocumentSummary(
                    document_uuid=row.document_uuid,
                    source_id=row.source_id,
                    source_type=row.source_type,
                    profile_id=row.profile_id,
                    title=row.title,
                    source=row.source,
                    primary_script=row.primary_script,
                    processing_state=row.processing_state,
                    attributes=dict(row.attributes),
                    created_at=row.created_at,
                    processed_at=row.processed_at,
                )
                for row in rows.all()
            ],
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def statistics(self) -> Statistics:
        """Counts for the dashboard. Every figure comes from a real query."""
        persons_total = await self._session.scalar(select(func.count()).select_from(persons))
        samples_total = await self._session.scalar(select(func.count()).select_from(face_samples))
        embeddings_total = await self._session.scalar(
            select(func.count()).select_from(face_embeddings)
        )
        identifications_total = await self._session.scalar(
            select(func.count()).select_from(identifications)
        )
        audit_total = await self._session.scalar(select(func.count()).select_from(audit_events))
        language_total = await self._session.scalar(
            select(func.count()).select_from(language_documents)
        )

        by_state = await self._session.execute(
            select(face_samples.c.processing_state, func.count()).group_by(
                face_samples.c.processing_state
            )
        )
        by_outcome = await self._session.execute(
            select(identifications.c.outcome, func.count()).group_by(identifications.c.outcome)
        )
        language_by_state = await self._session.execute(
            select(language_documents.c.processing_state, func.count()).group_by(
                language_documents.c.processing_state
            )
        )
        awaiting = await self._session.scalar(
            select(func.count())
            .select_from(identifications)
            .where(
                identifications.c.outcome == "review",
                identifications.c.review_outcome.is_(None),
            )
        )
        reviewed = await self._session.scalar(
            select(func.count())
            .select_from(identifications)
            .where(identifications.c.review_outcome.is_not(None))
        )

        return Statistics(
            persons=int(persons_total or 0),
            face_samples=int(samples_total or 0),
            samples_by_state={row[0]: int(row[1]) for row in by_state.all()},
            embeddings=int(embeddings_total or 0),
            identifications=int(identifications_total or 0),
            identifications_by_outcome={row[0]: int(row[1]) for row in by_outcome.all()},
            awaiting_review=int(awaiting or 0),
            reviews_recorded=int(reviewed or 0),
            audit_events=int(audit_total or 0),
            language_documents=int(language_total or 0),
            language_documents_by_state={row[0]: int(row[1]) for row in language_by_state.all()},
        )
