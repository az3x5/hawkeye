"""Contract tests for the media endpoints.

Authorisation gets as much attention as the happy path here. Media is the
first surface that will hold arbitrary collected material, so "who may fetch
these bytes" is a load-bearing question rather than a formality.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import get_media_service
from app.api.v1.media import router as media_router
from app.connectors.filesystem import FilesystemBlobStore
from app.core.errors import ErrorResponse, install_error_handlers
from app.domain.auth import Scope
from app.services.media import MediaService

from .conftest import authenticate
from .test_media_service import InMemoryMediaRepository, RecordingAuditLog, png

PNG_UPLOAD = ("photo.png", png(32, 24), "image/png")


@pytest.fixture
def repository() -> InMemoryMediaRepository:
    return InMemoryMediaRepository()


@pytest.fixture
def audit() -> RecordingAuditLog:
    return RecordingAuditLog()


def build_client(
    repository: InMemoryMediaRepository,
    audit: RecordingAuditLog,
    tmp_path: Path,
    *scopes: Scope,
) -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(media_router, prefix="/api/v1")

    service = MediaService(
        repository=repository,
        blobs=FilesystemBlobStore(tmp_path / "buckets"),
        audit=audit,
        max_bytes=1024 * 1024,
        page_size_limit=25,
    )

    async def _service() -> MediaService:
        return service

    app.dependency_overrides[get_media_service] = _service
    authenticate(app, *scopes)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(
    settings: object,
    repository: InMemoryMediaRepository,
    audit: RecordingAuditLog,
    tmp_path: Path,
) -> Iterator[TestClient]:
    yield from build_client(
        repository, audit, tmp_path, Scope.MEDIA_READ, Scope.MEDIA_WRITE, Scope.ADMIN
    )


def ingest(
    client: TestClient, upload: tuple[str, bytes, str] = PNG_UPLOAD, **form: str
) -> httpx2.Response:
    fields = {"source_type": "upload", "source_system": "console"}
    fields.update(form)
    return client.post("/api/v1/media", data=fields, files={"file": upload})


class TestIngestEndpoint:
    def test_submitting_media_returns_the_stored_asset(self, client: TestClient) -> None:
        response = ingest(client)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "stored"
        assert body["created"] is True
        assert body["asset"]["mime_type"] == "image/png"
        assert body["asset"]["media_type"] == "image"
        assert (body["asset"]["width"], body["asset"]["height"]) == (32, 24)

    def test_resubmitting_the_same_bytes_reports_already_held(self, client: TestClient) -> None:
        first = ingest(client).json()
        second = ingest(client, external_source_id="second-arrival").json()
        assert second["status"] == "already_held"
        assert second["created"] is False
        assert second["asset"]["media_uuid"] == first["asset"]["media_uuid"]

    def test_the_declared_type_does_not_decide_acceptance(self, client: TestClient) -> None:
        """A PDF wearing a .jpg name and an image/jpeg claim is still a PDF."""
        response = ingest(
            client, upload=("payload.jpg", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", "image/jpeg")
        )
        assert response.status_code == 422
        assert "declares image/jpeg" in response.json()["error"]["message"]

    def test_unrecognised_bytes_are_refused(self, client: TestClient) -> None:
        response = ingest(client, upload=("notes.txt", b"just prose, no magic", "text/plain"))
        assert response.status_code == 422
        assert ErrorResponse.model_validate(response.json()).error.code == "invalid_media"

    def test_an_empty_upload_is_refused(self, client: TestClient) -> None:
        response = ingest(client, upload=("empty.png", b"", "image/png"))
        assert response.status_code == 422

    def test_an_oversized_upload_is_refused_with_413(self, client: TestClient) -> None:
        oversized = png(8, 8, salt=b"x" * (1024 * 1024 + 10))
        response = ingest(client, upload=("big.png", oversized, "image/png"))
        assert response.status_code == 413
        assert ErrorResponse.model_validate(response.json()).error.code == "media_too_large"

    def test_a_decompression_bomb_is_refused(self, client: TestClient) -> None:
        """Small on the wire, ruinous in a decoder."""
        bomb = png(60_000, 60_000)
        assert len(bomb) < 100
        response = ingest(client, upload=("bomb.png", bomb, "image/png"))
        assert response.status_code == 413

    def test_provenance_is_recorded_from_the_request(self, client: TestClient) -> None:
        response = ingest(
            client,
            source_type="blackglass",
            source_system="blackglass-prod",
            external_source_id="bg-4471",
            source_url="https://example.invalid/photo.png",
        )
        source = response.json()["source"]
        assert source["source_type"] == "blackglass"
        assert source["external_source_id"] == "bg-4471"

    def test_an_unknown_source_type_is_refused(self, client: TestClient) -> None:
        assert ingest(client, source_type="telepathy").status_code == 422


class TestReadEndpoints:
    def test_metadata_can_be_read_back(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        response = client.get(f"/api/v1/media/{media_uuid}")
        assert response.status_code == 200
        assert response.json()["media_uuid"] == media_uuid

    def test_content_returns_the_original_bytes(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        response = client.get(f"/api/v1/media/{media_uuid}/content")
        assert response.status_code == 200
        assert response.content == PNG_UPLOAD[1]
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_a_range_request_returns_partial_content(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        response = client.get(f"/api/v1/media/{media_uuid}/content", headers={"Range": "bytes=0-7"})
        assert response.status_code == 206
        assert response.content == PNG_UPLOAD[1][:8]
        assert response.headers["content-range"] == f"bytes 0-7/{len(PNG_UPLOAD[1])}"

    def test_an_unsatisfiable_range_returns_416(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        response = client.get(
            f"/api/v1/media/{media_uuid}/content", headers={"Range": "bytes=99999-"}
        )
        assert response.status_code == 416

    def test_sources_list_every_arrival(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        ingest(client, source_type="news", source_system="mihaaru", external_source_id="a1")
        response = client.get(f"/api/v1/media/{media_uuid}/sources")
        assert {s["source_type"] for s in response.json()} == {"upload", "news"}

    def test_listing_reports_the_total_and_the_applied_limit(self, client: TestClient) -> None:
        for index in range(3):
            ingest(client, upload=("p.png", png(8, 8, salt=bytes([index])), "image/png"))
        body = client.get("/api/v1/media", params={"limit": 2}).json()
        assert body["total"] == 3
        assert len(body["items"]) == 2

    def test_an_excessive_page_size_is_clamped_not_rejected(self, client: TestClient) -> None:
        ingest(client)
        body = client.get("/api/v1/media", params={"limit": 10_000}).json()
        assert body["limit"] == 25

    def test_an_unknown_asset_is_a_404(self, client: TestClient) -> None:
        assert client.get(f"/api/v1/media/{uuid4()}").status_code == 404

    def test_a_malformed_uuid_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/v1/media/not-a-uuid").status_code == 422


class TestErasureAndHolds:
    def test_erasure_removes_the_bytes(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        response = client.request(
            "DELETE", f"/api/v1/media/{media_uuid}", params={"reason": "expired"}
        )
        assert response.status_code == 204
        assert client.get(f"/api/v1/media/{media_uuid}/content").status_code == 404
        # The metadata survives, so a past decision citing it stays explicable.
        assert client.get(f"/api/v1/media/{media_uuid}").json()["status"] == "erased"

    def test_erasure_requires_a_reason(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        assert client.delete(f"/api/v1/media/{media_uuid}").status_code == 422

    def test_a_hold_blocks_erasure_with_409(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        client.post(f"/api/v1/media/{media_uuid}/holds", params={"reason": "litigation"})
        response = client.request(
            "DELETE", f"/api/v1/media/{media_uuid}", params={"reason": "routine"}
        )
        assert response.status_code == 409
        assert ErrorResponse.model_validate(response.json()).error.code == ("media_retention_hold")

    def test_releasing_a_hold_permits_erasure(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        hold = client.post(
            f"/api/v1/media/{media_uuid}/holds", params={"reason": "litigation"}
        ).json()
        assert client.delete(f"/api/v1/media/holds/{hold['hold_uuid']}").status_code == 204
        assert (
            client.request(
                "DELETE", f"/api/v1/media/{media_uuid}", params={"reason": "cleared"}
            ).status_code
            == 204
        )

    def test_active_holds_are_listed(self, client: TestClient) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        client.post(f"/api/v1/media/{media_uuid}/holds", params={"reason": "litigation"})
        holds = client.get(f"/api/v1/media/{media_uuid}/holds").json()
        assert [h["reason"] for h in holds] == ["litigation"]


class TestAuthorisation:
    """A scope a caller does not hold must close the door, not narrow it."""

    @pytest.fixture
    def reader_only(
        self,
        settings: object,
        repository: InMemoryMediaRepository,
        audit: RecordingAuditLog,
        tmp_path: Path,
    ) -> Iterator[TestClient]:
        yield from build_client(repository, audit, tmp_path, Scope.MEDIA_READ)

    @pytest.fixture
    def writer_only(
        self,
        settings: object,
        repository: InMemoryMediaRepository,
        audit: RecordingAuditLog,
        tmp_path: Path,
    ) -> Iterator[TestClient]:
        yield from build_client(repository, audit, tmp_path, Scope.MEDIA_WRITE)

    @pytest.fixture
    def unrelated_scope(
        self,
        settings: object,
        repository: InMemoryMediaRepository,
        audit: RecordingAuditLog,
        tmp_path: Path,
    ) -> Iterator[TestClient]:
        yield from build_client(repository, audit, tmp_path, Scope.LANGUAGE)

    def test_reading_does_not_grant_writing(self, reader_only: TestClient) -> None:
        assert ingest(reader_only).status_code == 403

    def test_writing_does_not_grant_reading(
        self, writer_only: TestClient, client: TestClient
    ) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        assert writer_only.get(f"/api/v1/media/{media_uuid}").status_code == 403
        assert writer_only.get(f"/api/v1/media/{media_uuid}/content").status_code == 403

    def test_neither_read_nor_write_grants_erasure(
        self, writer_only: TestClient, client: TestClient
    ) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        response = writer_only.request(
            "DELETE", f"/api/v1/media/{media_uuid}", params={"reason": "x"}
        )
        assert response.status_code == 403

    def test_a_non_admin_cannot_place_a_hold(
        self, reader_only: TestClient, client: TestClient
    ) -> None:
        media_uuid = ingest(client).json()["asset"]["media_uuid"]
        response = reader_only.post(
            f"/api/v1/media/{media_uuid}/holds", params={"reason": "litigation"}
        )
        assert response.status_code == 403

    def test_an_unrelated_scope_reaches_nothing(self, unrelated_scope: TestClient) -> None:
        assert unrelated_scope.get("/api/v1/media").status_code == 403
        assert ingest(unrelated_scope).status_code == 403


class TestSensitiveReadsAreAudited:
    def test_fetching_biometric_media_leaves_a_record(
        self, client: TestClient, audit: RecordingAuditLog
    ) -> None:
        media_uuid = ingest(client, classification="biometric").json()["asset"]["media_uuid"]
        client.get(f"/api/v1/media/{media_uuid}/content")
        assert "media_viewed" in [e.action.value for e in audit.events]

    def test_reading_metadata_alone_is_not_a_view_of_the_material(
        self, client: TestClient, audit: RecordingAuditLog
    ) -> None:
        media_uuid = ingest(client, classification="biometric").json()["asset"]["media_uuid"]
        client.get(f"/api/v1/media/{media_uuid}")
        assert [e.action.value for e in audit.events] == []
