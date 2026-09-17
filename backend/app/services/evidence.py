"""Transactional submission, result publication and ownership-filtered retrieval."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.evidence_tables import events, pieces, runs
from app.connectors.postgres.jobs import SqlAlchemyProcessingJobRepository
from app.connectors.postgres.processing_tables import processing_jobs
from app.core.errors import FaceIdError, RateLimitedError
from app.domain.evidence import (
    DELIVERY_PIPELINE,
    PIPELINE,
    PIPELINE_VERSION,
    EvidencePiece,
    Submission,
    submission_key,
)
from app.domain.processing import ProcessingJob


def _source_fields(submission: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Read canonical flat identity while remaining able to serve pre-1.1 rows."""
    if submission.get("source_id") and submission.get("source_type"):
        return (
            str(submission["source_id"]),
            str(submission["source_type"]),
            dict(submission.get("attributes") or {}),
        )
    legacy = dict(submission.get("source") or {})
    attributes = dict(submission.get("attributes") or {})
    for old, new in {
        "system": "source_system",
        "source_url": "source_url",
        "collected_at": "collected_at",
        "published_at": "published_at",
        "collector_version": "collector_version",
    }.items():
        if legacy.get(old) is not None:
            attributes.setdefault(new, legacy[old])
    return str(legacy["object_id"]), str(legacy["object_type"]), attributes


class EvidenceNotFound(FaceIdError):
    """Unknown and inaccessible records deliberately share one response."""

    status_code = 404
    code = "evidence_not_found"


class EvidenceRepository:
    """Persist each BlackGlass delivery separately, even when bytes are shared."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind one transaction, shared with job enqueue and result outbox."""
        self.session = session

    async def submit(
        self,
        owner: str,
        submission: Submission,
        *,
        sha256: str,
        text: str | None = None,
        media_uuid: UUID | None = None,
        source_object: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist correlation and durable work, without inline inference."""
        key = submission_key(owner, submission, sha256)
        row = (
            await self.session.execute(
                insert(runs)
                .values(
                    analysis_id=uuid4(),
                    owner=owner,
                    idempotency_key=key,
                    source_id=submission.source_id,
                    source_type=submission.source_type,
                    submission=submission.model_dump(mode="json", exclude={"text"}),
                    sha256=sha256,
                    original_text=text,
                    media_uuid=media_uuid,
                    source_object=source_object,
                )
                .on_conflict_do_nothing(index_elements=[runs.c.idempotency_key])
                .returning(runs.c.analysis_id)
            )
        ).scalar_one_or_none()
        created = row is not None
        record = dict(
            (await self.session.execute(select(runs).where(runs.c.idempotency_key == key)))
            .mappings()
            .one()
        )
        if created:
            # Serialize admission for this owner to keep concurrent bulk clients bounded.
            from sqlalchemy import func

            admission_lock = int(hashlib.sha256(owner.encode()).hexdigest()[:15], 16)
            await self.session.execute(select(func.pg_advisory_xact_lock(admission_lock)))
            pending = (
                await self.session.execute(
                    select(func.count())
                    .select_from(runs)
                    .where(
                        runs.c.owner == owner,
                        runs.c.status.in_(["queued", "running", "retry"]),
                    )
                )
            ).scalar_one()
            if pending > 1000:
                raise RateLimitedError(
                    "evidence queue is full; retry after workers catch up", retry_after_seconds=60
                )
            await SqlAlchemyProcessingJobRepository(self.session).enqueue(
                ProcessingJob(
                    pipeline=PIPELINE,
                    pipeline_version=PIPELINE_VERSION,
                    subject_type="evidence_analysis",
                    subject_uuid=record["analysis_id"],
                    idempotency_key=key,
                    payload={"analysis_id": str(record["analysis_id"])},
                )
            )
        return {
            "schema_version": "1.1",
            "analysis_id": record["analysis_id"],
            "report_request_id": submission.report_request_id,
            "subject": submission.subject.model_dump(mode="json") if submission.subject else None,
            "source_id": submission.source_id,
            "source_type": submission.source_type,
            "attributes": submission.attributes,
            "status": record["status"],
            "created": created,
            "results_url": f"/api/v1/integrations/blackglass/evidence/{record['analysis_id']}",
        }

    async def get(self, analysis_id: UUID, owner: str | None) -> dict[str, Any]:
        """Read an owned run, or any run after an API-layer admin check."""
        conditions = [runs.c.analysis_id == analysis_id]
        if owner is not None:
            conditions.append(runs.c.owner == owner)
        record = (
            (await self.session.execute(select(runs).where(*conditions))).mappings().one_or_none()
        )
        if record is None:
            raise EvidenceNotFound("analysis not found")
        source_id, source_type, attributes = _source_fields(record["submission"])
        items = (
            (
                await self.session.execute(
                    select(pieces.c.body)
                    .where(
                        pieces.c.analysis_id == analysis_id,
                    )
                    .order_by(pieces.c.evidence_id)
                )
            )
            .scalars()
            .all()
        )
        return {
            "schema_version": "1.1",
            "analysis_id": analysis_id,
            "report_request_id": record["submission"].get("report_request_id"),
            "subject": record["submission"].get("subject"),
            "batch": {
                "batch_id": record["submission"].get("batch_id"),
                "batch_sequence": record["submission"].get("batch_sequence"),
                "final_batch": record["submission"].get("final_batch", False),
            },
            "source_id": source_id,
            "source_type": source_type,
            "attributes": attributes,
            "stream": record["submission"].get("stream"),
            "media_uuid": record["media_uuid"],
            "source_sha256": record["sha256"],
            "storage_mode": "shared_original" if record["source_object"] else "eagleeye_stored",
            "status": record["status"],
            "revision": record["revision"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "evidence": list(items),
            **record["result"],
        }

    async def publish(
        self,
        analysis_id: UUID,
        items: list[EvidencePiece],
        result: dict[str, Any],
        *,
        status: str,
        delivery_owner: str | None = None,
    ) -> None:
        """Write a complete revision and its event in the finishing job transaction."""
        row = (
            (
                await self.session.execute(
                    select(runs)
                    .where(
                        runs.c.analysis_id == analysis_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        revision = row["revision"] + 1
        now = datetime.now(UTC)
        await self.session.execute(delete(pieces).where(pieces.c.analysis_id == analysis_id))
        for item in items:
            searchable = " ".join(
                [item.normalized_text]
                + [translation.get("text", "") for translation in item.translations]
            )
            await self.session.execute(
                insert(pieces).values(
                    evidence_id=item.evidence_id,
                    analysis_id=analysis_id,
                    body=item.model_dump(mode="json"),
                    search_text=searchable,
                )
            )
        await self.session.execute(
            runs.update()
            .where(runs.c.analysis_id == analysis_id)
            .values(
                status=status,
                revision=revision,
                result=result,
                updated_at=now,
            )
        )
        event_id = uuid4()
        source_id, source_type, attributes = _source_fields(row["submission"])
        payload = {
            "schema_version": "1.1",
            "event_id": str(event_id),
            "event_type": "analysis.updated",
            "emitted_at": now.isoformat(),
            "analysis_id": str(analysis_id),
            "report_request_id": row["submission"].get("report_request_id"),
            "subject": row["submission"].get("subject"),
            "batch": {
                "batch_id": row["submission"].get("batch_id"),
                "batch_sequence": row["submission"].get("batch_sequence"),
                "final_batch": row["submission"].get("final_batch", False),
            },
            "revision": revision,
            "status": status,
            "source_id": source_id,
            "source_type": source_type,
            "attributes": attributes,
            "stream": row["submission"].get("stream"),
            "media_uuid": str(row["media_uuid"]) if row["media_uuid"] else None,
            "source_sha256": row["sha256"],
            "evidence": [item.model_dump(mode="json") for item in items],
            **result,
        }
        await self.session.execute(
            insert(events).values(
                event_id=event_id,
                analysis_id=analysis_id,
                owner=row["owner"],
                revision=revision,
                payload=payload,
            )
        )
        if delivery_owner and row["owner"] == delivery_owner:
            await SqlAlchemyProcessingJobRepository(self.session).enqueue(
                ProcessingJob(
                    pipeline=DELIVERY_PIPELINE,
                    pipeline_version="1.0",
                    subject_type="evidence_event",
                    subject_uuid=event_id,
                    idempotency_key=str(event_id),
                    payload={"event_id": str(event_id)},
                    max_attempts=8,
                )
            )

    async def enqueue_pending_events(self, owner: str, limit: int = 100) -> int:
        """Backfill undelivered events once; never reset exhausted delivery attempts."""
        queued = (
            select(processing_jobs.c.job_uuid)
            .where(
                processing_jobs.c.pipeline == DELIVERY_PIPELINE,
                processing_jobs.c.subject_uuid == events.c.event_id,
            )
            .exists()
        )
        ids = (
            (
                await self.session.execute(
                    select(events.c.event_id)
                    .where(
                        events.c.owner == owner,
                        events.c.delivered_at.is_(None),
                        ~queued,
                    )
                    .order_by(events.c.sequence)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for event_id in ids:
            await SqlAlchemyProcessingJobRepository(self.session).enqueue(
                ProcessingJob(
                    pipeline=DELIVERY_PIPELINE,
                    pipeline_version="1.0",
                    subject_type="evidence_event",
                    subject_uuid=event_id,
                    idempotency_key=str(event_id),
                    payload={"event_id": str(event_id)},
                    max_attempts=8,
                )
            )
        return len(ids)

    async def event_page(self, owner: str, cursor: int, limit: int) -> dict[str, Any]:
        """Return a stable, owner-scoped replay cursor over committed events."""
        rows = (
            (
                await self.session.execute(
                    select(events.c.sequence, events.c.payload)
                    .where(
                        events.c.owner == owner,
                        events.c.sequence > cursor,
                    )
                    .order_by(events.c.sequence)
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return {
            "events": [row["payload"] for row in rows],
            "next_cursor": rows[-1]["sequence"] if rows else cursor,
        }


def content_hash(content: bytes) -> str:
    """Stable address of original evidence."""
    return hashlib.sha256(content).hexdigest()
