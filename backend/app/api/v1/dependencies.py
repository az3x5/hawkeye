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
    SqlAlchemyPersonRepository,
)
from app.connectors.postgres.audit import SqlAlchemyAuditLog, SqlAlchemyIdentificationStore
from app.connectors.qdrant import QdrantVectorRepository
from app.connectors.redis import RedisJobQueue
from app.core.config import Settings
from app.core.errors import ServiceUnavailableError
from app.domain.identity import DecisionThresholds
from app.services.enrolment import EnrolmentService, SampleReader
from app.services.identification import IdentificationService


def _postgres(request: Request) -> PostgresConnector:
    connector = getattr(request.app.state, "postgres", None)
    if connector is None:
        raise ServiceUnavailableError("the metadata store is not available")
    return connector  # type: ignore[no-any-return]


async def get_enrolment_service(request: Request) -> AsyncIterator[EnrolmentService]:
    """Build an enrolment service bound to one database transaction."""
    objects = getattr(request.app.state, "objects", None)
    queue = getattr(request.app.state, "queue", None)
    if not isinstance(objects, FilesystemObjectStore) or not isinstance(queue, RedisJobQueue):
        raise ServiceUnavailableError("enrolment is not available")

    async with _postgres(request).session() as session:
        yield EnrolmentService(
            people=SqlAlchemyPersonRepository(session),
            samples=SqlAlchemyFaceSampleRepository(session),
            objects=objects,
            queue=queue,
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


def get_object_store(request: Request) -> FilesystemObjectStore:
    """Return the object store holding source images."""
    objects = getattr(request.app.state, "objects", None)
    if not isinstance(objects, FilesystemObjectStore):
        raise ServiceUnavailableError("the object store is not available")
    return objects


async def get_sample_reader(request: Request) -> AsyncIterator[SampleReader]:
    """Build a sample reader bound to one database transaction."""
    async with _postgres(request).session() as session:
        yield SampleReader(SqlAlchemyFaceSampleRepository(session))
