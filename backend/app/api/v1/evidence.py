"""Authenticated evidence intake, cited results and event replay for BlackGlass."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.v1.dependencies import _postgres
from app.api.v1.media import InvalidMediaError, MediaTooLargeResponseError, read_bounded_upload
from app.api.v1.security import require
from app.connectors.postgres.audit import SqlAlchemyAuditLog
from app.connectors.postgres.evidence_tables import events, runs
from app.connectors.postgres.media import SqlAlchemyMediaRepository
from app.connectors.postgres.media_tables import media_assets
from app.connectors.postgres.processing_tables import processing_worker_heartbeats
from app.core.errors import ServiceUnavailableError
from app.domain.auth import Principal, Scope
from app.domain.content_types import MediaTooLargeError, UnsupportedMediaError
from app.domain.evidence import (
    DELIVERY_PIPELINE,
    PIPELINE,
    ObjectBatch,
    ObjectSubmission,
    SharedObject,
    Submission,
    TextSubmission,
)
from app.domain.media import MediaError, SourceType
from app.domain.storage import ObjectLocation
from app.services.evidence import EvidenceRepository, content_hash
from app.services.evidence_processor import EvidenceSettings
from app.services.evidence_report import build_blackglass_report
from app.services.evidence_source import SharedEvidenceSource
from app.services.evidence_vectors import EvidenceVectors, hybrid_search
from app.services.media import IngestRequest, MediaService

router = APIRouter(prefix="/integrations/blackglass/evidence", tags=["evidence"])


@router.get("/status")
async def workflow_status(
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
) -> dict[str, Any]:
    """Report real worker presence and owner-scoped backlog, not model readiness."""
    measured_at = datetime.now(UTC)
    async with _postgres(request).session() as session:
        count_rows = (
            await session.execute(
                select(runs.c.status, func.count())
                .where(
                    runs.c.owner == principal.subject,
                )
                .group_by(runs.c.status)
            )
        ).all()
        worker_rows = (
            await session.execute(
                select(processing_worker_heartbeats.c.pipeline, func.count())
                .where(
                    processing_worker_heartbeats.c.pipeline.in_([PIPELINE, DELIVERY_PIPELINE]),
                    processing_worker_heartbeats.c.last_seen_at
                    >= measured_at - timedelta(seconds=60),
                )
                .group_by(processing_worker_heartbeats.c.pipeline)
            )
        ).all()
        pending = (
            await session.execute(
                select(func.count())
                .select_from(events)
                .where(
                    events.c.owner == principal.subject,
                    events.c.delivered_at.is_(None),
                )
            )
        ).scalar_one()
    return {
        "measured_at": measured_at,
        "runs": {str(row[0]): int(row[1]) for row in count_rows},
        "analysis_workers": next((int(row[1]) for row in worker_rows if row[0] == PIPELINE), 0),
        "delivery_workers": next(
            (int(row[1]) for row in worker_rows if row[0] == DELIVERY_PIPELINE),
            0,
        ),
        "undelivered_events": pending,
        "model_accuracy": "not_established_by_worker_presence",
    }


@router.post("/text", status_code=202)
async def submit_text(
    body: TextSubmission,
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.LANGUAGE))],
) -> dict[str, Any]:
    """Preserve original text and queue its complete evidence workflow."""
    if not body.text.strip():
        raise InvalidMediaError("text must contain non-whitespace content")
    async with _postgres(request).session() as session:
        return await EvidenceRepository(session).submit(
            principal.subject,
            body,
            sha256=content_hash(body.text.encode()),
            text=body.text,
        )


@router.post("/media", status_code=202)
async def submit_media(
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_WRITE))],
    metadata: Annotated[str, Form(max_length=16000)],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Store bytes, source mapping and evidence job in one database transaction."""
    try:
        body = Submission.model_validate_json(metadata)
    except ValidationError as exc:
        raise InvalidMediaError("invalid evidence metadata", field="metadata") from exc
    blobs = getattr(request.app.state, "blobs", None)
    if blobs is None:
        raise ServiceUnavailableError("media storage is not available")
    settings = request.app.state.settings
    try:
        data = await read_bounded_upload(file, min(settings.media_max_upload_bytes, 50 * 1024**2))
        async with _postgres(request).session() as session:
            stored = await MediaService(
                repository=SqlAlchemyMediaRepository(session),
                blobs=blobs,
                audit=SqlAlchemyAuditLog(session),
                max_bytes=50 * 1024**2,
            ).ingest(
                IngestRequest(
                    data=data,
                    source_type=SourceType.BLACKGLASS,
                    source_system=body.source.system,
                    external_source_id=body.source.object_id,
                    declared_content_type=file.content_type,
                    source_url=body.source.source_url,
                    collected_at=body.source.collected_at,
                    submitted_by=principal.subject,
                )
            )
            return await EvidenceRepository(session).submit(
                principal.subject,
                body,
                sha256=stored.asset.sha256,
                media_uuid=stored.asset.media_uuid,
            )
    except MediaTooLargeError as exc:
        raise MediaTooLargeResponseError(str(exc)) from exc
    except (UnsupportedMediaError, MediaError) as exc:
        raise InvalidMediaError(str(exc)) from exc


@router.get("/events")
async def event_page(
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """Replay only this principal's committed result events."""
    async with _postgres(request).session() as session:
        return await EvidenceRepository(session).event_page(principal.subject, cursor, limit)


@router.post("/objects", status_code=202)
async def submit_object(
    body: ObjectSubmission,
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_WRITE))],
) -> dict[str, Any]:
    """Register one existing original in AWS without uploading or copying its bytes."""
    try:
        source = SharedEvidenceSource(request.app.state.settings, EvidenceSettings())
        await source.inspect(body.object)
    except Exception as exc:  # noqa: BLE001 - do not expose SDK credentials or transport URLs
        raise InvalidMediaError("shared original is unavailable or manifest is invalid") from exc
    metadata = Submission.model_validate(body.model_dump(exclude={"object"}))
    async with _postgres(request).session() as session:
        return await EvidenceRepository(session).submit(
            principal.subject,
            metadata,
            sha256=body.object.sha256,
            source_object={"bucket": source.bucket, **body.object.model_dump(mode="json")},
        )


@router.post("/objects/batch", status_code=202)
async def submit_object_batch(
    body: ObjectBatch,
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_WRITE))],
) -> dict[str, Any]:
    """Accept up to 50 references without downloading files or duplicating originals."""
    outcomes = []
    for index, item in enumerate(body.items):
        try:
            accepted = await submit_object(item, request, principal)
            outcomes.append({"index": index, "accepted": True, **accepted})
        except InvalidMediaError:
            outcomes.append(
                {
                    "index": index,
                    "accepted": False,
                    "source": item.source.model_dump(mode="json"),
                    "error": "original_unavailable_or_manifest_invalid",
                }
            )
    return {"schema_version": "1.0", "items": outcomes}


@router.get("/{analysis_id}")
async def get_result(
    analysis_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
) -> dict[str, Any]:
    """Return source-linked excerpts and unreviewed findings for one owned run."""
    async with _postgres(request).session() as session:
        return await EvidenceRepository(session).get(analysis_id, principal.subject)


@router.get("/{analysis_id}/report")
async def generated_report(
    analysis_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
) -> dict[str, Any]:
    """Return a BlackGlass schema-2 report derived from committed evidence."""
    async with _postgres(request).session() as session:
        analysis = await EvidenceRepository(session).get(analysis_id, principal.subject)
    return build_blackglass_report(analysis)


@router.get("/{analysis_id}/content")
async def original_content(
    analysis_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
) -> Response:
    """Serve original bytes through the analysis ownership boundary."""
    from app.connectors.postgres.evidence_tables import runs
    from app.services.evidence import EvidenceNotFound

    async with _postgres(request).session() as session:
        result = await EvidenceRepository(session).get(analysis_id, principal.subject)
        source_object = (
            await session.execute(
                select(runs.c.source_object).where(
                    runs.c.analysis_id == analysis_id,
                )
            )
        ).scalar_one()
        if source_object:
            source = SharedEvidenceSource(request.app.state.settings, EvidenceSettings())
            if source.bucket != source_object["bucket"]:
                raise EvidenceNotFound("original bucket configuration changed")
            item = SharedObject.model_validate(
                {k: v for k, v in source_object.items() if k != "bucket"}
            )
            try:
                content = await source.read(item)
            except Exception as exc:  # noqa: BLE001
                raise EvidenceNotFound(
                    "original evidence unavailable or checksum mismatch"
                ) from exc
            return Response(
                content,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": "attachment",
                    "Cache-Control": "private, no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        if result["media_uuid"] is None:
            original = (
                await session.execute(
                    select(runs.c.original_text).where(
                        runs.c.analysis_id == analysis_id,
                    )
                )
            ).scalar_one()
            return Response(
                original,
                media_type="text/plain; charset=utf-8",
                headers={"Cache-Control": "private, no-store"},
            )
        asset = (
            (
                await session.execute(
                    select(media_assets).where(
                        media_assets.c.media_uuid == result["media_uuid"],
                        media_assets.c.status == "stored",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if asset is None:
            raise EvidenceNotFound("original evidence is unavailable")
    content = await request.app.state.blobs.get(
        ObjectLocation(
            bucket=asset["storage_bucket"],
            key=asset["storage_key"],
        )
    )
    if content is None or content_hash(content) != result["source_sha256"]:
        raise EvidenceNotFound("original evidence is unavailable or failed checksum validation")
    return Response(
        content,
        media_type=asset["mime_type"],
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
        },
    )


# Separate prefix avoids /{analysis_id} interpreting the search path as a UUID.
search_router = APIRouter(prefix="/integrations/blackglass", tags=["evidence"])


@search_router.get("/evidence-search")
async def search_evidence(
    request: Request,
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
    q: Annotated[str, Query(min_length=1, max_length=1000)],
    limit: Annotated[int, Query(ge=1, le=30)] = 10,
) -> dict[str, Any]:
    """Return authorized evidence with lexical fallback if semantic search is unavailable."""
    semantic_ids = None
    mode = "lexical"
    embedder = getattr(request.app.state, "language_embedder", None)
    qdrant = getattr(request.app.state, "qdrant", None)
    if embedder and qdrant:
        try:
            semantic_ids = await EvidenceVectors(qdrant, embedder).search_ids(
                principal.subject,
                q,
                limit * 3,
            )
            mode = "hybrid"
        except Exception:  # noqa: BLE001 - expose explicit fallback, never cross ownership boundaries
            mode = "lexical_semantic_unavailable"
    async with _postgres(request).session() as session:
        return {
            "mode": mode,
            "items": await hybrid_search(
                session,
                principal.subject,
                q,
                limit=limit,
                semantic_ids=semantic_ids,
            ),
        }
