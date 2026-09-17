"""Durable evidence analysis and optional authenticated BlackGlass delivery worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from app.connectors.postgres import PostgresConnector, SqlAlchemyProcessingJobRepository
from app.connectors.postgres.evidence_tables import events, runs
from app.connectors.postgres.media_tables import media_assets
from app.connectors.qdrant import QdrantConnector
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.domain.content_types import sniff, verify_declared
from app.domain.evidence import DELIVERY_PIPELINE, PIPELINE, SharedObject, Submission
from app.domain.processing import JobErrorCode, JobLeaseError, JobReservation, JobStatus
from app.domain.storage import ObjectLocation
from app.main import build_blob_store
from app.services.dhivehi_ai_client import DhivehiAIClient
from app.services.evidence import EvidenceRepository, content_hash
from app.services.evidence_processor import EvidenceProcessor, EvidenceSettings
from app.services.evidence_report import build_blackglass_report
from app.services.evidence_source import SharedEvidenceSource
from app.services.evidence_vectors import EvidenceVectors
from app.services.language_embeddings import MultilingualE5Embedder

logger = logging.getLogger(__name__)


def failed_stages_require_retry(result: dict[str, Any]) -> bool:
    """Retry transient failures unless a multi-batch summary has usable output.

    Re-running a long profile report from its first batch because one later
    summary batch was rejected wastes the successful, citation-validated
    batches.  Those runs are intentionally published as partial so the UI and
    receiver can show the supported findings together with the warning.
    """
    failures = [warning for warning in result.get("warnings", []) if "failed" in warning["code"]]
    if not failures:
        return False
    provenance = result.get("summary_provenance") or {}
    successful_batches = int(provenance.get("successful_batches", "0"))
    if successful_batches:
        failures = [warning for warning in failures if warning.get("stage") != "summary"]
    return bool(failures)


async def deliver_event(settings: EvidenceSettings, payload: dict[str, Any]) -> None:
    """Authenticate to a fixed receiver; retry the same event ID after lost acknowledgements."""
    url = urlsplit(settings.webhook_url or "")
    if not settings.delivery_enabled or not settings.webhook_token or not settings.webhook_owner:
        raise ValueError("BlackGlass delivery is disabled or unconfigured")
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("BlackGlass webhook requires a configured HTTPS URL")
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.post(
            settings.webhook_url or "",
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.webhook_token}",
                "Idempotency-Key": payload["event_id"],
            },
        )
        response.raise_for_status()
        ack = response.json()
        if ack.get("event_id") != payload["event_id"] or ack.get("status") not in {
            "accepted",
            "duplicate",
        }:
            raise ValueError("BlackGlass acknowledgement does not match the event")


class EvidenceWorker:
    """Run one bounded job at a time; results commit only under a valid lease."""

    def __init__(self, settings: Settings, options: EvidenceSettings, *, delivery: bool) -> None:
        """Configure connectors without opening the face pipeline."""
        self.settings = settings
        self.options = options
        self.delivery = delivery
        self.pipeline = DELIVERY_PIPELINE if delivery else PIPELINE
        self.worker_id = f"evidence:{socket.gethostname()}:{os.getpid()}"
        self.postgres = PostgresConnector(str(settings.postgres_dsn))
        self.qdrant = QdrantConnector(settings.qdrant_url, api_key=settings.qdrant_api_key)
        self.blobs = build_blob_store(settings)
        self.specialist = (
            DhivehiAIClient(
                settings.dhivehi_ai_url,
                timeout_seconds=settings.dhivehi_ai_timeout_seconds,
            )
            if settings.dhivehi_ai_url
            else None
        )
        self.processor = EvidenceProcessor(self.specialist, options)
        self.vectors: EvidenceVectors | None = None
        self.stopping = asyncio.Event()

    async def report_progress(self, reservation: JobReservation) -> None:
        """Keep worker presence fresh without extending the bounded job lease."""
        while True:
            async with self.postgres.session() as session:
                await SqlAlchemyProcessingJobRepository(session).heartbeat(
                    self.worker_id,
                    self.pipeline,
                    current_job_uuid=reservation.job.job_uuid,
                    details={"mode": "delivery" if self.delivery else "analysis"},
                )
            await asyncio.sleep(15)

    async def process_with_heartbeat(self, reservation: JobReservation) -> None:
        """Bound processing and cancel both tasks if processing or monitoring fails."""
        processing = asyncio.create_task(self.process(reservation))
        heartbeat = asyncio.create_task(self.report_progress(reservation))
        try:
            async with asyncio.timeout(self.options.run_timeout):
                done, _ = await asyncio.wait(
                    {processing, heartbeat},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    await task
        finally:
            for task in (processing, heartbeat):
                task.cancel()
            for task in (processing, heartbeat):
                with suppress(asyncio.CancelledError, Exception):
                    await task

    async def process(self, reservation: JobReservation) -> None:
        """Finish results and durable job state in the same transaction."""
        run_id = reservation.job.subject_uuid
        if self.delivery:
            async with self.postgres.session() as session:
                repository = EvidenceRepository(session)
                row = (
                    (
                        await session.execute(
                            select(events).where(
                                events.c.event_id == run_id,
                                events.c.owner == self.options.webhook_owner,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                analysis = await repository.get(row["analysis_id"], row["owner"])
            if row["delivered_at"] is None:
                payload = {**row["payload"], "report": build_blackglass_report(analysis)}
                await deliver_event(self.options, payload)
            async with self.postgres.session() as session:
                await SqlAlchemyProcessingJobRepository(session).complete(reservation)
                await session.execute(
                    events.update()
                    .where(events.c.event_id == run_id)
                    .values(
                        delivered_at=datetime.now(UTC),
                    )
                )
            return
        async with self.postgres.session() as session:
            record = (
                (
                    await session.execute(
                        select(runs).where(
                            runs.c.analysis_id == run_id,
                        )
                    )
                )
                .mappings()
                .one()
            )
            asset = None
            if record["media_uuid"]:
                asset = (
                    (
                        await session.execute(
                            select(media_assets).where(
                                media_assets.c.media_uuid == record["media_uuid"],
                                media_assets.c.status == "stored",
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
        data = None
        mime = asset["mime_type"] if asset else "text/plain"
        if record["source_object"]:
            source = SharedEvidenceSource(self.settings, self.options)
            reference = record["source_object"]
            if reference["bucket"] != source.bucket:
                raise ValueError("shared source configuration changed")
            item = SharedObject.model_validate(
                {k: v for k, v in reference.items() if k != "bucket"}
            )
            data = await source.read(item)
            detected = sniff(data)
            verify_declared(item.mime_type, detected.format)
            mime = detected.format.mime_type
        if asset:
            data = await self.blobs.get(
                ObjectLocation(
                    bucket=asset["storage_bucket"],
                    key=asset["storage_key"],
                )
            )
            if data is None or content_hash(data) != record["sha256"]:
                raise ValueError("source evidence is missing or its checksum changed")
        items, result = await self.processor.process(
            run_id,
            Submission.model_validate(record["submission"]),
            text=record["original_text"],
            data=data,
            mime=mime,
        )
        # Re-run transiently failed stages before settling on an explicit partial result.
        failed_stages = failed_stages_require_retry(result)
        if failed_stages and reservation.attempt_number < reservation.job.max_attempts:
            raise RuntimeError("one or more inference stages failed; retrying")
        if items:
            if self.vectors is None:
                raise RuntimeError("evidence embedding model is not configured")
            await self.vectors.index(run_id, record["owner"], items)
            result["vector_collection"] = self.vectors.collection
        status = "failed" if not items else "partial" if result["warnings"] else "completed"
        async with self.postgres.session() as session:
            await SqlAlchemyProcessingJobRepository(session).complete(reservation)
            await EvidenceRepository(session).publish(
                run_id,
                items,
                result,
                status=status,
                delivery_owner=self.options.webhook_owner
                if self.options.delivery_enabled
                else None,
            )

    async def run(self) -> None:
        """Claim bounded leases and retain failed jobs for inspection/retry."""
        await self.postgres.ping()
        if self.delivery and not (
            self.options.delivery_enabled
            and self.options.webhook_owner
            and self.options.webhook_url
            and self.options.webhook_token
        ):
            raise ValueError("delivery remains disabled until a receiver is configured and tested")
        if not self.delivery:
            if not self.settings.language_embedding_model:
                raise ValueError("language embedding model is required for evidence indexing")
            embedder = await asyncio.to_thread(
                MultilingualE5Embedder,
                self.settings.language_embedding_model,
                model_version=self.settings.language_embedding_version,
                device=self.settings.language_embedding_device,
                batch_size=self.settings.language_embedding_batch_size,
                max_tokens=self.settings.language_embedding_max_tokens,
            )
            self.vectors = EvidenceVectors(self.qdrant, embedder)
        while not self.stopping.is_set():
            async with self.postgres.session() as session:
                jobs = SqlAlchemyProcessingJobRepository(session)
                if self.delivery and self.options.webhook_owner:
                    await EvidenceRepository(session).enqueue_pending_events(
                        self.options.webhook_owner,
                    )
                await jobs.heartbeat(self.worker_id, self.pipeline, current_job_uuid=None)
                reservation = await jobs.claim(
                    self.pipeline,
                    worker_id=self.worker_id,
                    lease_seconds=self.options.run_timeout + 120,
                )
                if reservation and not self.delivery:
                    await session.execute(
                        runs.update()
                        .where(
                            runs.c.analysis_id == reservation.job.subject_uuid,
                        )
                        .values(status="running", updated_at=datetime.now(UTC))
                    )
            if reservation is None:
                await asyncio.sleep(2)
                continue
            try:
                await self.process_with_heartbeat(reservation)
            except JobLeaseError:
                logger.warning("evidence worker lost its lease; uncommitted results discarded")
            except Exception as exc:  # noqa: BLE001 - jobs must survive adapter failures
                # Transport exception messages can contain configured URLs; persist codes only.
                logger.warning("evidence job failed (%s)", type(exc).__name__)
                try:
                    async with self.postgres.session() as session:
                        status = await SqlAlchemyProcessingJobRepository(session).fail(
                            reservation,
                            error_code=JobErrorCode.INTERNAL_ERROR,
                            detail=type(exc).__name__,
                            retryable=not isinstance(exc, ValueError),
                        )
                        if not self.delivery:
                            if status == JobStatus.RETRY:
                                await session.execute(
                                    runs.update()
                                    .where(
                                        runs.c.analysis_id == reservation.job.subject_uuid,
                                    )
                                    .values(status="retry", updated_at=datetime.now(UTC))
                                )
                            else:
                                await EvidenceRepository(session).publish(
                                    reservation.job.subject_uuid,
                                    [],
                                    {
                                        "findings": [],
                                        "contradictions": [],
                                        "warnings": [
                                            {"stage": "pipeline", "code": type(exc).__name__}
                                        ],
                                    },
                                    status="failed",
                                    delivery_owner=self.options.webhook_owner
                                    if self.options.delivery_enabled
                                    else None,
                                )
                except JobLeaseError:
                    logger.warning("failed job lease was already reassigned")

    async def close(self) -> None:
        """Release pooled connections on shutdown."""
        if self.specialist:
            await self.specialist.close()
        await self.postgres.close()
        await self.qdrant.close()


async def main() -> None:
    """Start the selected worker without enrolling reference images."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery", action="store_true")
    args = parser.parse_args()
    configure_logging()
    worker = EvidenceWorker(get_settings(), EvidenceSettings(), delivery=args.delivery)
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, worker.stopping.set)
    try:
        await worker.run()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
