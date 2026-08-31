"""Media endpoints.

Uploads are read in bounded chunks rather than with ``UploadFile.read()``.
The difference matters: reading the whole body and then checking its length
means a caller controls how much memory the API allocates before the ceiling
is applied, which is a denial of service the size limit appears to prevent but
does not. Here the limit is enforced while reading, so an oversized body is
refused after one chunk past the ceiling.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_media_service
from app.api.v1.security import require
from app.core.errors import ErrorResponse, FaceIdError
from app.domain.auth import Principal, Scope
from app.domain.content_types import (
    MediaTooLargeError,
    MediaType,
    UnsupportedMediaError,
)
from app.domain.media import (
    AssetStatus,
    Classification,
    MediaError,
    RetentionHoldError,
    SourceType,
)
from app.domain.storage import StorageDomain
from app.services.media import IngestRequest, MediaService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["media"])

#: Chunk size while streaming an upload. Large enough that the read loop is
#: not the bottleneck, small enough that the ceiling is not badly overshot.
_CHUNK_BYTES = 1024 * 1024

#: ``bytes=start-end``. Only the single-range form is supported; multipart
#: ranges buy nothing for the media this serves.
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

#: Ceiling on one range response, so a range request cannot be used to pull an
#: arbitrarily large object into memory in a single call.
_MAX_RANGE_BYTES = 8 * 1024 * 1024


class InvalidMediaError(FaceIdError):
    """The submitted media cannot be accepted."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_media"


class MediaTooLargeResponseError(FaceIdError):
    """The submitted media exceeds a configured ceiling."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "media_too_large"


class MediaNotFoundError(FaceIdError):
    """No such media asset, or its bytes are no longer stored."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "media_not_found"


class MediaHeldError(FaceIdError):
    """The operation is blocked by a retention hold."""

    status_code = status.HTTP_409_CONFLICT
    code = "media_retention_hold"


class MediaAssetResponse(BaseModel):
    """One stored media asset."""

    media_uuid: UUID = Field(description="Internal identifier of this asset.")
    sha256: str = Field(description="Content hash. Identical bytes are one asset.")
    byte_size: int = Field(description="Size of the stored object in bytes.")
    media_type: MediaType = Field(description="Modality, used to route processing.")
    mime_type: str = Field(description="Type detected from the bytes, not from the caller.")
    classification: Classification = Field(description="How sensitive this asset is.")
    status: AssetStatus = Field(description="Whether the bytes are still stored.")
    storage_domain: StorageDomain = Field(description="Which storage domain holds it.")
    width: int | None = Field(default=None, description="Pixel width, when known.")
    height: int | None = Field(default=None, description="Pixel height, when known.")
    duration_ms: int | None = Field(default=None, description="Duration in ms, when known.")
    created_at: datetime = Field(description="When the asset was first stored.")
    erased_at: datetime | None = Field(default=None, description="When its bytes were erased.")


class AssetSourceResponse(BaseModel):
    """One recorded arrival of an asset."""

    source_uuid: UUID
    source_type: SourceType = Field(description="The kind of system it came from.")
    source_system: str = Field(description="Which system delivered it.")
    external_source_id: str | None = None
    source_url: str | None = None
    collected_at: datetime | None = None
    published_at: datetime | None = None
    ingested_at: datetime
    collector_version: str | None = None
    submitted_by: str | None = None


class IngestResponse(BaseModel):
    """Outcome of a media submission."""

    asset: MediaAssetResponse
    source: AssetSourceResponse
    created: bool = Field(
        description=(
            "False when these exact bytes were already held. Not an error: the "
            "new arrival is still recorded as a separate source."
        )
    )
    status: Literal["stored", "already_held"] = Field(
        description="Whether this submission created a new asset."
    )


class MediaPageResponse(BaseModel):
    """A page of media assets."""

    items: list[MediaAssetResponse]
    total: int = Field(description="How many assets match the filters.")
    limit: int
    offset: int


class RetentionHoldResponse(BaseModel):
    """A standing instruction that an asset must not be erased."""

    hold_uuid: UUID
    media_uuid: UUID
    reason: str
    placed_by: str
    placed_at: datetime
    released_at: datetime | None = None
    released_by: str | None = None


async def read_bounded_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an upload, refusing it as soon as it passes ``max_bytes``.

    Returns the bytes; the caller identifies them from their content. Nothing
    here consults ``upload.content_type`` — that is the caller's claim, and
    it is checked against the real type where the bytes are identified.

    Raises the domain errors rather than HTTP ones, because the two callers
    map them differently: media ingestion answers 413 for an oversized body,
    while enrolment has always answered 422 and keeps doing so.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise MediaTooLargeError(f"the upload exceeds the {max_bytes} byte limit")
        chunks.append(chunk)
    if total == 0:
        raise UnsupportedMediaError("the uploaded file is empty")
    return b"".join(chunks)


def _to_asset_response(asset: object) -> MediaAssetResponse:
    return MediaAssetResponse.model_validate(asset, from_attributes=True)


@router.post(
    "/media",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit media for storage",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def ingest_media(
    file: Annotated[UploadFile, File()],
    source_type: Annotated[SourceType, Form()],
    source_system: Annotated[str, Form(min_length=1, max_length=128)],
    service: Annotated[MediaService, Depends(get_media_service)],
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_WRITE))],
    classification: Annotated[Classification, Form()] = Classification.INTERNAL,
    external_source_id: Annotated[str | None, Form(max_length=256)] = None,
    source_url: Annotated[str | None, Form(max_length=2048)] = None,
    collected_at: Annotated[datetime | None, Form()] = None,
    published_at: Annotated[datetime | None, Form()] = None,
    collector_version: Annotated[str | None, Form(max_length=128)] = None,
) -> IngestResponse:
    """Store media and record where it came from.

    The type is decided by the file's magic bytes. A declared content type
    that disagrees with the contents is refused rather than corrected.

    Idempotent on content: resubmitting identical bytes returns the original
    asset and records the new arrival as an additional source.
    """
    try:
        data = await read_bounded_upload(file, service.max_bytes)
        result = await service.ingest(
            IngestRequest(
                data=data,
                source_type=source_type,
                source_system=source_system,
                declared_content_type=file.content_type,
                classification=classification,
                external_source_id=external_source_id,
                source_url=source_url,
                collected_at=collected_at,
                published_at=published_at,
                collector_version=collector_version,
                submitted_by=principal.subject,
            )
        )
    except MediaTooLargeError as exc:
        raise MediaTooLargeResponseError(str(exc), field="file") from exc
    except UnsupportedMediaError as exc:
        raise InvalidMediaError(str(exc), field="file") from exc
    except MediaError as exc:
        raise InvalidMediaError(str(exc)) from exc

    return IngestResponse(
        asset=_to_asset_response(result.asset),
        source=AssetSourceResponse.model_validate(result.source, from_attributes=True),
        created=result.created,
        status="stored" if result.created else "already_held",
    )


@router.get(
    "/media",
    response_model=MediaPageResponse,
    summary="List media assets",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_media(
    service: Annotated[MediaService, Depends(get_media_service)],
    _principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
    media_type: Annotated[MediaType | None, Query()] = None,
    classification: Annotated[Classification | None, Query()] = None,
    limit: Annotated[int, Query(ge=1)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MediaPageResponse:
    """Return a page of media assets, newest first.

    ``limit`` is clamped to the server's ceiling rather than rejected, so a
    caller asking for too much gets the maximum instead of an error.
    """
    effective = min(limit, service.page_size_limit)
    items, total = await service.list_assets(
        media_type=media_type, classification=classification, limit=effective, offset=offset
    )
    return MediaPageResponse(
        items=[_to_asset_response(asset) for asset in items],
        total=total,
        limit=effective,
        offset=offset,
    )


@router.get(
    "/media/{media_uuid}",
    response_model=MediaAssetResponse,
    summary="Read one media asset",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def read_media(
    media_uuid: UUID,
    service: Annotated[MediaService, Depends(get_media_service)],
    _principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
) -> MediaAssetResponse:
    """Return one asset's metadata, including erased assets."""
    asset = await service.get(media_uuid)
    if asset is None:
        raise MediaNotFoundError(f"no media asset {media_uuid}")
    return _to_asset_response(asset)


@router.get(
    "/media/{media_uuid}/sources",
    response_model=list[AssetSourceResponse],
    summary="Read where a media asset came from",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def read_media_sources(
    media_uuid: UUID,
    service: Annotated[MediaService, Depends(get_media_service)],
    _principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
) -> list[AssetSourceResponse]:
    """Return every recorded arrival of this asset, oldest first."""
    asset = await service.get(media_uuid)
    if asset is None:
        raise MediaNotFoundError(f"no media asset {media_uuid}")
    sources = await service.list_sources(media_uuid)
    return [AssetSourceResponse.model_validate(s, from_attributes=True) for s in sources]


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """Resolve a Range header to an inclusive (start, end), or None if unusable."""
    match = _RANGE_RE.match(header.strip())
    if match is None:
        return None
    raw_start, raw_end = match.group(1), match.group(2)
    if not raw_start and not raw_end:
        return None
    if not raw_start:
        # A suffix range: the last N bytes.
        length = min(int(raw_end), size)
        if length == 0:
            return None
        return size - length, size - 1
    start = int(raw_start)
    if start >= size:
        return None
    end = min(int(raw_end), size - 1) if raw_end else size - 1
    if end < start:
        return None
    return start, min(end, start + _MAX_RANGE_BYTES - 1)


@router.get(
    "/media/{media_uuid}/content",
    summary="Fetch the stored bytes of a media asset",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}},
        206: {"description": "Partial content"},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def read_media_content(
    media_uuid: UUID,
    response: Response,
    service: Annotated[MediaService, Depends(get_media_service)],
    principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    """Return an asset's bytes, honouring a single-range request.

    Never served by content hash: a caller must name an asset that exists, so
    holding a digest is not on its own enough to pull bytes out of the store.
    """
    asset = await service.get(media_uuid)
    if asset is None or not asset.readable:
        raise MediaNotFoundError(f"no readable media asset {media_uuid}")

    headers = {
        "Cache-Control": "private, no-store",
        "Content-Disposition": "attachment",
        "Accept-Ranges": "bytes",
        # The bytes are caller-supplied; nothing should sniff or execute them.
        "X-Content-Type-Options": "nosniff",
    }

    await service.record_view(asset, principal)

    if range_header:
        window = _parse_range(range_header, asset.byte_size)
        if window is None:
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers={**headers, "Content-Range": f"bytes */{asset.byte_size}"},
            )
        start, end = window
        found = await service.content_range(media_uuid, start, end - start + 1)
        if found is None:
            raise MediaNotFoundError(f"the bytes for {media_uuid} are no longer stored")
        _, data = found
        return Response(
            content=data,
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type=asset.mime_type,
            headers={**headers, "Content-Range": f"bytes {start}-{end}/{asset.byte_size}"},
        )

    found = await service.content(media_uuid)
    if found is None:
        raise MediaNotFoundError(f"the bytes for {media_uuid} are no longer stored")
    _, data = found
    return Response(content=data, media_type=asset.mime_type, headers=headers)


@router.delete(
    "/media/{media_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Erase a media asset's bytes",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def erase_media(
    media_uuid: UUID,
    service: Annotated[MediaService, Depends(get_media_service)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
    reason: Annotated[str, Query(min_length=1, max_length=512)],
) -> Response:
    """Erase the bytes, keeping the metadata so past decisions stay explicable.

    Refused while any retention hold is active.
    """
    try:
        erased = await service.erase(media_uuid, principal, reason=reason)
    except RetentionHoldError as exc:
        raise MediaHeldError(str(exc)) from exc
    if not erased:
        raise MediaNotFoundError(f"no media asset {media_uuid} to erase")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/media/{media_uuid}/holds",
    response_model=RetentionHoldResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Place a retention hold",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def place_hold(
    media_uuid: UUID,
    service: Annotated[MediaService, Depends(get_media_service)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
    reason: Annotated[str, Query(min_length=1, max_length=512)],
) -> RetentionHoldResponse:
    """Block erasure of this asset until the hold is released."""
    try:
        hold = await service.place_hold(media_uuid, principal, reason=reason)
    except MediaError as exc:
        raise MediaNotFoundError(str(exc)) from exc
    return RetentionHoldResponse.model_validate(hold, from_attributes=True)


@router.get(
    "/media/{media_uuid}/holds",
    response_model=list[RetentionHoldResponse],
    summary="List active retention holds",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def list_holds(
    media_uuid: UUID,
    service: Annotated[MediaService, Depends(get_media_service)],
    _principal: Annotated[Principal, Depends(require(Scope.MEDIA_READ))],
) -> list[RetentionHoldResponse]:
    """Return the holds currently blocking erasure of this asset."""
    holds = await service.active_holds(media_uuid)
    return [RetentionHoldResponse.model_validate(h, from_attributes=True) for h in holds]


@router.delete(
    "/media/holds/{hold_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Release a retention hold",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def release_hold(
    hold_uuid: UUID,
    service: Annotated[MediaService, Depends(get_media_service)],
    principal: Annotated[Principal, Depends(require(Scope.ADMIN))],
) -> Response:
    """Release a hold, allowing the asset to be erased again."""
    if not await service.release_hold(hold_uuid, principal):
        raise MediaNotFoundError(f"no active retention hold {hold_uuid}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
