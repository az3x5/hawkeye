"""Evidence provenance, isolation, replay, and failed-citation regression tests."""

from __future__ import annotations

import io
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select, text

from app.api.v1.evidence import router, search_router
from app.connectors.filesystem import FilesystemBlobStore
from app.connectors.postgres import PostgresConnector, SqlAlchemyProcessingJobRepository, metadata
from app.connectors.postgres.evidence_tables import events, runs
from app.connectors.postgres.processing_tables import processing_jobs
from app.core.errors import install_error_handlers
from app.domain.auth import Scope
from app.domain.evidence import (
    PIPELINE,
    AnalysisOptions,
    Citation,
    Finding,
    Findings,
    Locator,
    SharedObject,
    StreamSegment,
    TextSubmission,
    UnifiedSubmission,
    normalize,
    submission_key,
    validate_citations,
)
from app.domain.processing import JobLeaseError
from app.services.evidence import EvidenceNotFound, EvidenceRepository, content_hash
from app.services.evidence_processor import EvidenceProcessor, EvidenceSettings
from app.services.evidence_report import SECTIONS, build_blackglass_report
from app.services.evidence_source import SharedEvidenceSource
from app.services.evidence_vectors import hybrid_search

from .conftest import INTEGRATION_DSN, authenticate
from .test_media_api import PNG_UPLOAD


def submission(object_id: str = "record-1") -> TextSubmission:
    return TextSubmission(
        source_id=object_id,
        source_type="post",
        attributes={"source_system": "blackglass-prod"},
        text="ދިވެހި text\nThe meeting is at nine.",
        options=AnalysisOptions(summarize=False),
    )


def test_manifest_and_locator_validation() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Locator(start_ms=100, end_ms=99)
    with pytest.raises(ValidationError):
        Locator(char_start=1)
    with pytest.raises(ValidationError):
        AnalysisOptions(max_frames=100000)
    with pytest.raises(ValidationError):
        StreamSegment(stream_id="s", segment_id="c", sequence=1, started_at=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        TextSubmission(**submission().model_dump(), ignored_processing_flag=True)
    with pytest.raises(ValidationError):
        UnifiedSubmission.model_validate(
            {
                **submission().model_dump(exclude={"text"}),
                "report_request_id": "report-1",
                "subject": {"subject_type": "social_profile", "subject_id": "profile-1"},
                "text": "   ",
            }
        )


def test_record_and_owner_are_part_of_idempotency() -> None:
    original = submission()
    assert submission_key("a", original, "f" * 64) == submission_key("a", original, "f" * 64)
    assert submission_key("a", original, "f" * 64) != submission_key("b", original, "f" * 64)
    assert submission_key("a", original, "f" * 64) != submission_key(
        "a", submission("other"), "f" * 64
    )
    replay = original.model_copy(update={"ingest_request_id": "retry-2"})
    assert submission_key("a", original, "f" * 64) == submission_key("a", replay, "f" * 64)


def test_legacy_source_is_flattened_to_canonical_contract() -> None:
    body = TextSubmission.model_validate(
        {
            "schema_version": "1.0",
            "source": {
                "system": "blackglass",
                "object_id": "POST-LEGACY-1",
                "object_type": "post",
                "source_url": "https://blackglass.live/posts/POST-LEGACY-1",
            },
            "text": "legacy payload",
        }
    )

    payload = body.model_dump(mode="json")
    assert payload["schema_version"] == "1.1"
    assert payload["source_id"] == "POST-LEGACY-1"
    assert payload["source_type"] == "post"
    assert payload["attributes"]["source_system"] == "blackglass"
    assert "source" not in payload


def test_blackglass_report_preserves_citations_and_marks_gaps() -> None:
    analysis_id = uuid4()
    evidence_id = uuid4()
    now = datetime.now(UTC)
    report = build_blackglass_report(
        {
            "analysis_id": analysis_id,
            "status": "completed",
            "revision": 2,
            "created_at": now,
            "updated_at": now,
            "source_id": "BG-1",
            "source_type": "post",
            "attributes": {"source_system": "blackglass-prod"},
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "kind": "text",
                    "original_text": "Source quote",
                    "normalized_text": "Source quote",
                    "locator": {"precision": "source"},
                    "provenance": {"extractor": "original-text"},
                    "review_status": "unreviewed",
                }
            ],
            "findings": [
                {
                    "statement": "Cited finding",
                    "section": "osp-associates",
                    "confidence": 0.8,
                    "basis": "association",
                    "review_status": "unreviewed",
                    "citations": [{"evidence_id": evidence_id, "quote": "Source quote"}],
                }
            ],
            "contradictions": [],
            "warnings": [{"stage": "ocr", "code": "not_run"}],
        }
    )
    data = report["data"]
    assert data["document"]["schema"] == 2
    assert len(data["blocks"]) == len(SECTIONS) == 31
    assert data["document"]["evidenceRefs"][0]["evidenceId"] == str(evidence_id)
    key_findings = next(block for block in data["blocks"] if block["type"] == "osp-associates")
    assert key_findings["rows"][0][0]["en"] == "Cited finding"
    unsupported = next(block for block in data["blocks"] if block["type"] == "osp-triggers")
    assert "Insufficient cited evidence" in unsupported["body"]["en"]


async def test_original_text_and_offsets_survive_normalization() -> None:
    body = submission()
    processor = EvidenceProcessor(None, EvidenceSettings())
    items, result = await processor.process(uuid4(), body, text=body.text)
    assert items[0].original_text == body.text
    assert items[0].normalized_text == normalize(body.text)
    assert items[0].locator.char_end == len(body.text)
    assert result["warnings"] == []
    assert "\n" in body.text


async def test_invented_or_cross_run_citations_rejected() -> None:
    items, _ = await EvidenceProcessor(None, EvidenceSettings()).process(
        uuid4(),
        submission(),
        text=submission().text,
    )
    valid = Findings(
        findings=[
            Finding(
                statement="The meeting is at nine.",
                citations=[
                    Citation(evidence_id=items[0].evidence_id, quote="The meeting is at nine."),
                ],
            )
        ]
    )
    validate_citations(valid, items)
    valid.findings[0].citations[0].quote = "The meeting is at ten."
    with pytest.raises(ValueError, match="verbatim"):
        validate_citations(valid, items)
    valid.findings[0].citations[0].evidence_id = uuid4()
    with pytest.raises(ValueError, match="outside"):
        validate_citations(valid, items)


async def test_llm_failure_keeps_source_and_reports_partial_stage(monkeypatch) -> None:
    body = submission().model_copy(update={"options": AnalysisOptions(summarize=True)})
    processor = EvidenceProcessor(None, EvidenceSettings())

    async def false_summary(*args: Any, **kwargs: Any) -> dict[str, str]:
        return {
            "text": '{"findings":[{"statement":"unsupported","citations":[]}]}',
            "model": "test",
            "revision": "test",
        }

    monkeypatch.setattr(processor, "ollama", false_summary)
    items, result = await processor.process(uuid4(), body, text=body.text)
    assert items[0].original_text == body.text
    assert result["findings"] == []
    assert result["warnings"][0]["code"] == "batch_1_failed_or_citations_rejected"


async def test_report_analysis_reads_every_extracted_post_chunk(monkeypatch) -> None:
    body = submission().model_copy(update={"options": AnalysisOptions(summarize=True)})
    body.text = "\n".join(f"post {index}: " + "evidence " * 180 for index in range(15))
    processor = EvidenceProcessor(None, EvidenceSettings())
    batch_sizes = []

    async def summary(*args: Any, **kwargs: Any) -> dict[str, str]:
        messages = args[1]
        batch_sizes.append(len(__import__("json").loads(messages[1]["content"])))
        return {"text": '{"findings":[],"contradictions":[]}', "model": "test", "revision": "1"}

    monkeypatch.setattr(processor, "ollama", summary)
    items, result = await processor.process(uuid4(), body, text=body.text)
    assert len(items) > 12
    assert sum(batch_sizes) == len(items)
    assert result["summary_provenance"]["pieces_considered"] == str(len(items))


async def test_shared_source_is_read_only_and_validates_hash() -> None:
    class S3:
        calls: list[str] = []

        def head_object(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append("head")
            return {"ContentLength": 3}

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append("get")
            assert kwargs["VersionId"] == "v1"
            return {"ContentLength": 3, "Body": io.BytesIO(b"abc")}

    source = object.__new__(SharedEvidenceSource)
    source.bucket = "retained-evidence"
    source.prefix = "blackglass/"
    source.client = S3()
    item = SharedObject(
        key="blackglass/one.pdf",
        version_id="v1",
        sha256=content_hash(b"abc"),
        byte_size=3,
        mime_type="application/pdf",
    )
    await source.inspect(item)
    assert await source.read(item) == b"abc"
    with pytest.raises(ValueError, match="checksum"):
        await source.read(item.model_copy(update={"sha256": "f" * 64}))
    with pytest.raises(ValueError, match="outside"):
        await source.read(item.model_copy(update={"key": "another-prefix/file.pdf"}))
    assert source.client.calls == ["head", "get", "get"]


@pytest_asyncio.fixture
async def db() -> AsyncIterator[PostgresConnector]:
    if INTEGRATION_DSN is None:
        pytest.skip("requires a disposable PostgreSQL database ending in _test")
    connector = PostgresConnector(INTEGRATION_DSN)
    async with connector.engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
        await connection.execute(text("TRUNCATE evidence.runs CASCADE"))
        await connection.execute(text("TRUNCATE processing.jobs CASCADE"))
    yield connector
    await connector.close()


async def test_deduplication_keeps_distinct_blackglass_correlations(db) -> None:
    body = submission()
    async with db.session() as session:
        repo = EvidenceRepository(session)
        first = await repo.submit(
            "owner", body, sha256=content_hash(body.text.encode()), text=body.text
        )
        duplicate = await repo.submit(
            "owner", body, sha256=content_hash(body.text.encode()), text=body.text
        )
        other = await repo.submit(
            "owner", submission("record-2"), sha256=content_hash(body.text.encode()), text=body.text
        )
        assert first["created"] and not duplicate["created"]
        assert first["analysis_id"] == duplicate["analysis_id"]
        assert first["analysis_id"] != other["analysis_id"]
        assert (
            await session.execute(select(func.count()).select_from(processing_jobs))
        ).scalar_one() == 2


async def test_results_search_and_events_are_owner_scoped(db) -> None:
    body = submission()
    async with db.session() as session:
        repo = EvidenceRepository(session)
        run = await repo.submit(
            "owner-a", body, sha256=content_hash(body.text.encode()), text=body.text
        )
    items, result = await EvidenceProcessor(None, EvidenceSettings()).process(
        run["analysis_id"],
        body,
        text=body.text,
    )
    async with db.session() as session:
        repo = EvidenceRepository(session)
        await repo.publish(run["analysis_id"], items, result, status="completed")
    async with db.session() as session:
        repo = EvidenceRepository(session)
        found = await repo.get(run["analysis_id"], "owner-a")
        assert found["evidence"][0]["original_text"] == body.text
        with pytest.raises(EvidenceNotFound):
            await repo.get(run["analysis_id"], "owner-b")
        assert (await repo.event_page("owner-b", 0, 10))["events"] == []
        assert len((await repo.event_page("owner-a", 0, 10))["events"]) == 1
        assert (
            await hybrid_search(
                session, "owner-b", "meeting", limit=10, semantic_ids=[items[0].evidence_id]
            )
            == []
        )
        assert len(await hybrid_search(session, "owner-a", "ދިވެހި", limit=10)) == 1


async def test_result_commit_rejected_after_lease_expires(db) -> None:
    body = submission()
    async with db.session() as session:
        run = await EvidenceRepository(session).submit(
            "owner", body, sha256="a" * 64, text=body.text
        )
        reservation = await SqlAlchemyProcessingJobRepository(session).claim(
            PIPELINE,
            worker_id="test",
            lease_seconds=30,
        )
    assert reservation
    async with db.session() as session:
        await session.execute(
            processing_jobs.update().values(
                leased_until=datetime.now(UTC) - timedelta(seconds=5),
            )
        )
    with pytest.raises(JobLeaseError):
        async with db.session() as session:
            await EvidenceRepository(session).publish(
                run["analysis_id"], [], {}, status="completed"
            )
            await SqlAlchemyProcessingJobRepository(session).complete(reservation)
    async with db.session() as session:
        assert (await session.execute(select(func.count()).select_from(events))).scalar_one() == 0


async def test_http_ingestion_authentication_and_ownership(db, settings) -> None:
    app = FastAPI()
    app.state.postgres = db
    app.state.settings = settings
    app.include_router(router, prefix="/api/v1")
    app.include_router(search_router, prefix="/api/v1")
    install_error_handlers(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        endpoint = "/api/v1/integrations/blackglass/evidence/text"
        assert (
            await client.post(endpoint, json=submission().model_dump(mode="json"))
        ).status_code == 401
        authenticate(app, Scope.LANGUAGE, Scope.MEDIA_READ, subject="owner-a")
        accepted = await client.post(endpoint, json=submission().model_dump(mode="json"))
        assert accepted.status_code == 202
        status_path = "/api/v1/integrations/blackglass/evidence/status"
        status = await client.get(status_path)
        assert status.status_code == 200
        assert status.json()["runs"] == {"queued": 1}
        path = accepted.json()["results_url"]
        assert (await client.get(path)).status_code == 200
        report_response = await client.get(path + "/report")
        assert report_response.status_code == 200
        assert report_response.json()["data"]["subject"] == "record-1"
        authenticate(app, Scope.MEDIA_READ, subject="owner-b")
        assert (await client.get(status_path)).json()["runs"] == {}
        assert (await client.get(path)).status_code == 404
        assert (await client.get(path + "/content")).status_code == 404


async def test_unified_ingestion_accepts_text_and_files_and_replays(db, settings, tmp_path) -> None:
    app = FastAPI()
    app.state.postgres = db
    app.state.settings = settings
    app.state.blobs = FilesystemBlobStore(tmp_path / "objects")
    app.include_router(router, prefix="/api/v1")
    install_error_handlers(app)
    authenticate(
        app,
        Scope.LANGUAGE,
        Scope.MEDIA_WRITE,
        Scope.MEDIA_READ,
        subject="blackglass-service",
        kind="service",
    )
    payload = {
        "schema_version": "1.1",
        "report_request_id": "BG-REPORT-123",
        "subject": {
            "subject_type": "social_profile",
            "subject_id": "BG-PROFILE-456",
            "display_label": "Profile under review",
        },
        "source_id": "POST-1001",
        "source_type": "post",
        "attributes": {
            "source_system": "blackglass-prod",
            "collected_at": "2026-09-15T08:00:00Z",
        },
        "text": "ދިވެހި post with an attached image",
        "options": {"language": "mixed", "summarize": False},
        "batch_id": "BG-REPORT-123-B1",
        "batch_sequence": 1,
        "final_batch": True,
    }
    endpoint = "/api/v1/integrations/blackglass/evidence/ingest"
    request = {
        "data": {"metadata": json.dumps(payload)},
        "files": [("files", PNG_UPLOAD)],
        "headers": {"Idempotency-Key": "BG-REPORT-123-B1"},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(endpoint, **request)
        assert first.status_code == 202, first.text
        body = first.json()
        assert body["accepted_items"] == 2
        assert [item["kind"] for item in body["items"]] == ["text", "file"]
        assert all(item["created"] for item in body["items"])
        assert body["report_request_id"] == "BG-REPORT-123"
        assert body["subject"]["subject_id"] == "BG-PROFILE-456"

        replay = await client.post(endpoint, **request)
        assert replay.status_code == 202, replay.text
        assert not any(item["created"] for item in replay.json()["items"])
        assert [item["analysis_id"] for item in replay.json()["items"]] == [
            item["analysis_id"] for item in body["items"]
        ]

        result = await client.get(body["items"][0]["results_url"])
        assert result.status_code == 200
        assert result.json()["report_request_id"] == "BG-REPORT-123"
        assert result.json()["subject"]["subject_id"] == "BG-PROFILE-456"
        assert result.json()["batch"]["final_batch"] is True
        report = await client.get(body["items"][0]["results_url"] + "/report")
        assert report.json()["data"]["subject_id"] == "BG-PROFILE-456"
        assert report.json()["data"]["subject"] == "Profile under review"

        empty_payload = {
            **payload,
            "text": None,
            "source_id": "POST-2",
        }
        empty = await client.post(
            endpoint,
            data={"metadata": json.dumps(empty_payload)},
            headers={"Idempotency-Key": "empty-request"},
        )
        assert empty.status_code == 422

    async with db.session() as session:
        assert (
            await session.execute(select(func.count()).select_from(processing_jobs))
        ).scalar_one() == 2


async def test_shared_manifest_does_not_create_media_copy(db) -> None:
    body = submission()
    async with db.session() as session:
        await EvidenceRepository(session).submit(
            "owner",
            body,
            sha256="b" * 64,
            source_object={"bucket": "retained-originals", "key": "blackglass/test.pdf"},
        )
        row = (await session.execute(select(runs))).mappings().one()
        assert row["media_uuid"] is None
        assert row["original_text"] is None
        assert row["source_object"]["bucket"] == "retained-originals"


async def test_worker_finishes_evidence_and_outbox_atomically(db, settings, tmp_path) -> None:
    from app.evidence_worker import EvidenceWorker

    body = submission()
    async with db.session() as session:
        accepted = await EvidenceRepository(session).submit(
            "owner",
            body,
            sha256=content_hash(body.text.encode()),
            text=body.text,
        )
        reserved = await SqlAlchemyProcessingJobRepository(session).claim(
            PIPELINE,
            worker_id="test",
            lease_seconds=60,
        )
    assert reserved
    worker = EvidenceWorker(
        settings.model_copy(
            update={
                "postgres_dsn": INTEGRATION_DSN,
                "object_store_root": tmp_path,
            }
        ),
        EvidenceSettings(),
        delivery=False,
    )

    class Vectors:
        collection = "test-evidence"
        count = 0

        async def index(self, analysis_id, owner, evidence) -> None:
            assert analysis_id == accepted["analysis_id"] and owner == "owner"
            self.count += len(evidence)

    vectors = Vectors()
    worker.vectors = vectors
    try:
        await worker.process(reserved)
    finally:
        await worker.close()
    async with db.session() as session:
        response = await EvidenceRepository(session).get(accepted["analysis_id"], "owner")
        assert response["status"] == "completed"
        assert vectors.count == 1
        assert response["vector_collection"] == "test-evidence"
        job = await SqlAlchemyProcessingJobRepository(session).get(reserved.job.job_uuid)
        assert job.status.value == "completed"
        page = await EvidenceRepository(session).event_page("owner", 0, 10)
        assert page["events"][0]["source_id"] == body.source_id


async def test_webhook_rejects_redirects_and_wrong_ack(monkeypatch) -> None:
    import json

    from app.evidence_worker import deliver_event

    payload = {"event_id": str(uuid4())}
    options = EvidenceSettings(
        delivery_enabled=True,
        webhook_owner="owner",
        webhook_url="https://blackglass.example/events",
        webhook_token="test-only",
    )
    client_type = httpx.AsyncClient
    seen = []

    def receiver(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-only"
        assert request.headers["idempotency-key"] == payload["event_id"]
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"event_id": "wrong", "status": "accepted"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(
            transport=httpx.MockTransport(receiver),
            **kwargs,
        ),
    )
    with pytest.raises(ValueError, match="acknowledgement"):
        await deliver_event(options, payload)
    assert seen == [payload]
    options.delivery_enabled = False
    with pytest.raises(ValueError, match="disabled"):
        await deliver_event(options, payload)


async def test_delivery_worker_sends_blackglass_report(db, settings, tmp_path, monkeypatch) -> None:
    from app.domain.evidence import DELIVERY_PIPELINE
    from app.evidence_worker import EvidenceWorker

    body = submission()
    async with db.session() as session:
        repository = EvidenceRepository(session)
        accepted = await repository.submit(
            "owner",
            body,
            sha256=content_hash(body.text.encode()),
            text=body.text,
        )
        evidence, result = await EvidenceProcessor(None, EvidenceSettings()).process(
            accepted["analysis_id"],
            body,
            text=body.text,
        )
        await repository.publish(accepted["analysis_id"], evidence, result, status="completed")
        await repository.enqueue_pending_events("owner")
        reservation = await SqlAlchemyProcessingJobRepository(session).claim(
            DELIVERY_PIPELINE,
            worker_id="delivery-test",
            lease_seconds=60,
        )
    assert reservation
    delivered = []

    async def capture(_options, payload) -> None:
        delivered.append(payload)

    monkeypatch.setattr("app.evidence_worker.deliver_event", capture)
    worker = EvidenceWorker(
        settings.model_copy(
            update={"postgres_dsn": INTEGRATION_DSN, "object_store_root": tmp_path}
        ),
        EvidenceSettings(
            delivery_enabled=True,
            webhook_owner="owner",
            webhook_url="https://blackglass.example/results",
            webhook_token="test-only",
        ),
        delivery=True,
    )
    try:
        await worker.process(reservation)
    finally:
        await worker.close()
    assert delivered[0]["report"]["data"]["document"]["schema"] == 2
    assert delivered[0]["report"]["data"]["subject"] == body.source_id
    async with db.session() as session:
        assert (await session.execute(select(events.c.delivered_at))).scalar_one() is not None


async def test_qdrant_owner_filter_and_replay() -> None:
    import numpy as np

    from app.connectors.qdrant import QdrantConnector
    from app.services.evidence_vectors import EvidenceVectors
    from app.services.language_embeddings import LanguageEmbedding

    url = os.environ.get("EVIDENCE_TEST_QDRANT_URL")
    if not url:
        pytest.skip("requires isolated Qdrant")

    class Embedder:
        model_name = "synthetic-test-only"
        model_version = str(uuid4())

        async def embed_documents(self, texts) -> list[LanguageEmbedding]:
            return [
                LanguageEmbedding(
                    np.array([1, 0, 0], dtype=np.float32), self.model_name, self.model_version
                )
                for _ in texts
            ]

        async def embed_query(self, query) -> LanguageEmbedding:
            return (await self.embed_documents([query]))[0]

    connector = QdrantConnector(url)
    repository = EvidenceVectors(connector, Embedder())
    run_id = uuid4()
    evidence, _ = await EvidenceProcessor(None, EvidenceSettings()).process(
        run_id,
        submission(),
        text="synthetic evidence",
    )
    try:
        await repository.index(run_id, "owner-a", evidence)
        await repository.index(run_id, "owner-a", evidence)
        assert (await connector.client.count(repository.collection)).count == 1
        assert await repository.search_ids("owner-b", "evidence", 5) == []
        assert await repository.search_ids("owner-a", "evidence", 5) == [evidence[0].evidence_id]
    finally:
        await connector.client.delete_collection(repository.collection)
        await connector.close()


async def test_delivery_backfill_is_owner_scoped_and_idempotent(db) -> None:
    from app.domain.evidence import DELIVERY_PIPELINE

    for owner in ("owner-a", "owner-b"):
        body = submission(owner)
        async with db.session() as session:
            repository = EvidenceRepository(session)
            accepted = await repository.submit(
                owner,
                body,
                sha256=content_hash(body.text.encode()),
                text=body.text,
            )
            await repository.publish(accepted["analysis_id"], [], {}, status="partial")
    async with db.session() as session:
        assert await EvidenceRepository(session).enqueue_pending_events("owner-a") == 1
    async with db.session() as session:
        assert await EvidenceRepository(session).enqueue_pending_events("owner-a") == 0
        jobs = (
            (
                await session.execute(
                    select(processing_jobs).where(
                        processing_jobs.c.pipeline == DELIVERY_PIPELINE,
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(jobs) == 1
        owner = (
            await session.execute(
                select(events.c.owner).where(
                    events.c.event_id == jobs[0]["subject_uuid"],
                )
            )
        ).scalar_one()
        assert owner == "owner-a"


async def test_active_worker_heartbeat_and_cleanup(db, settings, tmp_path) -> None:
    import asyncio

    from app.connectors.postgres.processing_tables import processing_worker_heartbeats
    from app.evidence_worker import EvidenceWorker

    body = submission()
    async with db.session() as session:
        await EvidenceRepository(session).submit(
            "owner",
            body,
            sha256=content_hash(body.text.encode()),
            text=body.text,
        )
        reservation = await SqlAlchemyProcessingJobRepository(session).claim(
            PIPELINE,
            worker_id="test-heartbeat",
            lease_seconds=60,
        )
    assert reservation
    worker = EvidenceWorker(
        settings.model_copy(
            update={
                "postgres_dsn": INTEGRATION_DSN,
                "object_store_root": tmp_path,
            }
        ),
        EvidenceSettings(),
        delivery=False,
    )
    try:
        task = asyncio.create_task(worker.report_progress(reservation))
        try:
            async with asyncio.timeout(5):
                while True:
                    async with db.session() as session:
                        current = (
                            await session.execute(
                                select(processing_worker_heartbeats.c.current_job_uuid).where(
                                    processing_worker_heartbeats.c.worker_id == worker.worker_id,
                                )
                            )
                        ).scalar_one_or_none()
                    if current == reservation.job.job_uuid:
                        break
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        await worker.close()
