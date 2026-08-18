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
from app.connectors.redis import RedisJobQueue
from app.core.errors import ServiceUnavailableError
from app.services.enrolment import EnrolmentService, SampleReader


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


async def get_sample_reader(request: Request) -> AsyncIterator[SampleReader]:
    """Build a sample reader bound to one database transaction."""
    async with _postgres(request).session() as session:
        yield SampleReader(SqlAlchemyFaceSampleRepository(session))
