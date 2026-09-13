"""Request-scoped dependency wiring.

Endpoints depend on services; services depend on repository interfaces. The
translation from application state to a concrete repository happens here and
nowhere else.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request

from app.connectors.filesystem import FilesystemObjectStore
from app.connectors.postgres import (
    PostgresConnector,
    SqlAlchemyFaceSampleRepository,
    SqlAlchemyLanguageDocumentRepository,
    SqlAlchemyPersonRepository,
    SqlAlchemyProcessingJobRepository,
)
from app.connectors.postgres.audit import SqlAlchemyAuditLog, SqlAlchemyIdentificationStore
from app.connectors.postgres.media import SqlAlchemyMediaRepository
from app.connectors.postgres.queries import ReadQueries
from app.connectors.postgres.tokens import SqlAlchemyTokenStore
from app.connectors.postgres.users import SqlAlchemyUserStore
from app.connectors.qdrant import QdrantLanguageRepository, QdrantVectorRepository
from app.connectors.s3 import BlackGlassS3Source
from app.core.config import Settings
from app.core.errors import ServiceUnavailableError
from app.domain.identity import DecisionThresholds
from app.domain.processing import ProcessingMetrics
from app.services.administration import AccountAdministration, TokenAdministration
from app.services.authentication import AuthenticationService
from app.services.dhivehi_ai_client import DhivehiAIClient
from app.services.enrolment import EnrolmentService, SampleReader
from app.services.erasure import PersonEraser
from app.services.identification import IdentificationService
from app.services.language_search import LanguageDocumentService, LanguageSearchService
from app.services.media import MediaService
from app.services.processing import (
    FaceJobSubmitter,
    LanguageJobSubmitter,
    ProcessingJobAdministration,
)


def _postgres(request: Request) -> PostgresConnector:
    connector = getattr(request.app.state, "postgres", None)
    if connector is None:
        raise ServiceUnavailableError("the metadata store is not available")
    return connector  # type: ignore[no-any-return]


def get_dhivehi_ai_client(request: Request) -> DhivehiAIClient:
    """Return the internal specialist client without exposing its URL."""
    client = getattr(request.app.state, "dhivehi_ai", None)
    if not isinstance(client, DhivehiAIClient):
        raise ServiceUnavailableError("specialist Dhivehi AI is not configured")
    return client


def get_blackglass_s3_source(request: Request) -> BlackGlassS3Source:
    """Return the optional read-only BlackGlass AWS source."""
    source = getattr(request.app.state, "blackglass_s3", None)
    if not isinstance(source, BlackGlassS3Source):
        raise ServiceUnavailableError("the BlackGlass AWS source is not configured")
    return source


async def get_enrolment_service(request: Request) -> AsyncIterator[EnrolmentService]:
    """Build an enrolment service bound to one database transaction."""
    objects = getattr(request.app.state, "objects", None)
    if not isinstance(objects, FilesystemObjectStore):
        raise ServiceUnavailableError("enrolment is not available")

    async with _postgres(request).session() as session:
        settings: Settings = request.app.state.settings
        jobs = SqlAlchemyProcessingJobRepository(session)
        yield EnrolmentService(
            people=SqlAlchemyPersonRepository(session),
            samples=SqlAlchemyFaceSampleRepository(session),
            objects=objects,
            queue=FaceJobSubmitter(jobs, max_attempts=settings.job_max_attempts),
        )


def _thresholds(request: Request) -> DecisionThresholds:
    settings: Settings = request.app.state.settings
    return DecisionThresholds(
        accept_at=settings.decision_accept_threshold,
        review_at=settings.decision_review_threshold,
        policy_version=settings.decision_policy_version,
    )


async def get_identification_store(
    request: Request,
) -> AsyncIterator[SqlAlchemyIdentificationStore]:
    """Build an identification store bound to one database transaction."""
    async with _postgres(request).session() as session:
        yield SqlAlchemyIdentificationStore(session)


async def get_identification_service(
    request: Request,
) -> AsyncIterator[IdentificationService]:
    """Build an identification service bound to one database transaction.

    The models are held on application state and loaded once, not per request.
    """
    qdrant = getattr(request.app.state, "qdrant", None)
    if qdrant is None:
        raise ServiceUnavailableError("the vector store is not available")

    settings: Settings = request.app.state.settings
    async with _postgres(request).session() as session:
        yield IdentificationService(
            vectors=QdrantVectorRepository(qdrant),
            store=SqlAlchemyIdentificationStore(session),
            audit=SqlAlchemyAuditLog(session),
            thresholds=_thresholds(request),
            candidate_limit=settings.decision_candidate_limit,
            objects=getattr(request.app.state, "objects", None),
            detector=getattr(request.app.state, "detector", None),
            recognizer=getattr(request.app.state, "recognizer", None),
        )


async def get_media_service(request: Request) -> AsyncIterator[MediaService]:
    """Build a media service bound to one database transaction."""
    blobs = getattr(request.app.state, "blobs", None)
    if blobs is None:
        raise ServiceUnavailableError("media storage is not available")

    settings: Settings = request.app.state.settings
    async with _postgres(request).session() as session:
        yield MediaService(
            repository=SqlAlchemyMediaRepository(session),
            blobs=blobs,
            audit=SqlAlchemyAuditLog(session),
            max_bytes=settings.media_max_upload_bytes,
            page_size_limit=settings.media_page_size_limit,
        )


def get_object_store(request: Request) -> FilesystemObjectStore:
    """Return the object store holding source images."""
    objects = getattr(request.app.state, "objects", None)
    if not isinstance(objects, FilesystemObjectStore):
        raise ServiceUnavailableError("the object store is not available")
    return objects


def get_default_token_lifetime(request: Request) -> int:
    """The credential lifetime applied when a request does not name one."""
    settings: Settings = request.app.state.settings
    return settings.token_lifetime_days


async def get_account_administration(
    request: Request,
) -> AsyncIterator[AccountAdministration]:
    """Build account administration bound to one database transaction."""
    async with _postgres(request).session() as session:
        yield AccountAdministration(
            users=SqlAlchemyUserStore(session),
            tokens=SqlAlchemyTokenStore(session),
            audit=SqlAlchemyAuditLog(session),
        )


async def get_token_administration(request: Request) -> AsyncIterator[TokenAdministration]:
    """Build credential administration bound to one database transaction."""
    async with _postgres(request).session() as session:
        yield TokenAdministration(
            tokens=SqlAlchemyTokenStore(session), audit=SqlAlchemyAuditLog(session)
        )


async def get_read_queries(request: Request) -> AsyncIterator[ReadQueries]:
    """Build read-side queries bound to one database transaction."""
    async with _postgres(request).session() as session:
        yield ReadQueries(session)


async def get_authentication_service(
    request: Request,
) -> AsyncIterator[AuthenticationService]:
    """Build the sign-in service bound to one database transaction."""
    settings: Settings = request.app.state.settings
    async with _postgres(request).session() as session:
        yield AuthenticationService(
            users=SqlAlchemyUserStore(session),
            tokens=SqlAlchemyTokenStore(session),
            session_lifetime_seconds=settings.session_lifetime_seconds,
        )


async def get_person_eraser(request: Request) -> AsyncIterator[PersonEraser]:
    """Build an eraser bound to one database transaction."""
    qdrant = getattr(request.app.state, "qdrant", None)
    objects = getattr(request.app.state, "objects", None)
    if qdrant is None or not isinstance(objects, FilesystemObjectStore):
        raise ServiceUnavailableError("erasure is not available")

    async with _postgres(request).session() as session:
        yield PersonEraser(
            session=session,
            vectors=QdrantVectorRepository(qdrant),
            objects=objects,
            audit=SqlAlchemyAuditLog(session),
        )


async def get_sample_reader(request: Request) -> AsyncIterator[SampleReader]:
    """Build a sample reader bound to one database transaction."""
    async with _postgres(request).session() as session:
        yield SampleReader(SqlAlchemyFaceSampleRepository(session))


async def get_language_document_service(
    request: Request,
) -> AsyncIterator[LanguageDocumentService]:
    """Build document ingestion bound to one transaction."""
    async with _postgres(request).session() as session:
        settings: Settings = request.app.state.settings
        yield LanguageDocumentService(
            SqlAlchemyLanguageDocumentRepository(session),
            LanguageJobSubmitter(
                SqlAlchemyProcessingJobRepository(session),
                max_attempts=settings.job_max_attempts,
            ),
        )


async def get_language_search_service(
    request: Request,
) -> AsyncIterator[LanguageSearchService]:
    """Build semantic search using the process-wide query embedder."""
    qdrant = getattr(request.app.state, "qdrant", None)
    embedder = getattr(request.app.state, "language_embedder", None)
    if qdrant is None or embedder is None:
        raise ServiceUnavailableError("language semantic search is not configured")
    async with _postgres(request).session() as session:
        yield LanguageSearchService(
            SqlAlchemyLanguageDocumentRepository(session),
            QdrantLanguageRepository(qdrant),
            embedder,
        )


async def get_processing_job_administration(
    request: Request,
) -> AsyncIterator[ProcessingJobAdministration]:
    """Build durable job administration in one audited transaction."""
    settings: Settings = request.app.state.settings
    async with _postgres(request).session() as session:
        yield ProcessingJobAdministration(
            SqlAlchemyProcessingJobRepository(
                session,
                retry_base_seconds=settings.job_retry_base_seconds,
                retry_max_seconds=settings.job_retry_max_seconds,
            ),
            SqlAlchemyAuditLog(session),
        )


async def get_processing_metrics(request: Request) -> ProcessingMetrics:
    """Read live durable queue pressure in a short transaction."""
    settings: Settings = request.app.state.settings
    async with _postgres(request).session() as session:
        return await SqlAlchemyProcessingJobRepository(
            session,
            retry_base_seconds=settings.job_retry_base_seconds,
            retry_max_seconds=settings.job_retry_max_seconds,
        ).metrics(live_worker_window_seconds=max(settings.job_lease_seconds * 2, 60))
