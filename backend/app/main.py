"""FastAPI application factory for the Face ID service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.adapters.factory import build_detector, build_recognizer
from app.api.v1.enrolments import router as enrolment_router
from app.api.v1.health import router as health_router
from app.api.v1.identifications import router as identification_router
from app.connectors.filesystem import FilesystemObjectStore
from app.connectors.postgres import PostgresConnector
from app.connectors.qdrant import QdrantConnector
from app.connectors.redis import RedisConnector, RedisJobQueue, RedisRateLimiter
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.readiness import clear_probes, register_probe

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open storage connections for the serving window and close them after.

    The Postgres connector registers itself as a readiness probe here, so
    ``/readyz`` reports the real state of the metadata store.
    """
    settings: Settings = app.state.settings
    # Re-assert our formatter: the ASGI server configures logging after the
    # app object is built, so this must happen at startup, not import time.
    configure_logging(logging.DEBUG if settings.debug else logging.INFO)
    logger.info(
        "faceid service starting",
        extra={"environment": settings.environment, "service": settings.service_name},
    )

    postgres = PostgresConnector(str(settings.postgres_dsn), echo=False)
    app.state.postgres = postgres
    register_probe(postgres.provider, postgres.ping)

    qdrant = QdrantConnector(settings.qdrant_url, api_key=settings.qdrant_api_key)
    app.state.qdrant = qdrant
    register_probe(qdrant.provider, qdrant.ping)

    redis = RedisConnector(str(settings.redis_dsn))
    app.state.redis = redis
    app.state.queue = RedisJobQueue(redis)
    app.state.rate_limiter = RedisRateLimiter(redis)
    register_probe(redis.provider, redis.ping)

    objects = FilesystemObjectStore(settings.object_store_root)
    app.state.objects = objects
    register_probe(objects.provider, objects.ping)

    # Identification answers in the request path, so its models live here.
    # Enrolment still hands its work to the worker; only identification pays
    # this cost, and only when weights are configured.
    app.state.detector = None
    app.state.recognizer = None
    if settings.scrfd_model_path and settings.adaface_model_path:
        detector, recognizer = build_detector(settings), build_recognizer(settings)
        detector.warmup()
        recognizer.warmup()
        app.state.detector = detector
        app.state.recognizer = recognizer
        logger.info(
            "identification models loaded",
            extra={
                "detector": detector.model_name,
                "recognizer": recognizer.model_name,
                "model_version": recognizer.model_version,
            },
        )
    else:
        logger.warning("identification models are not configured; identification will fail")

    try:
        yield
    finally:
        await redis.close()
        await qdrant.close()
        await postgres.close()
        clear_probes()
        logger.info("faceid service stopping")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Kept free of import-time configuration reads so tests can construct
    isolated instances with their own settings.
    """
    settings = settings or get_settings()
    configure_logging(logging.DEBUG if settings.debug else logging.INFO)

    app = FastAPI(
        title="Person Intelligence - Face ID",
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    install_error_handlers(app)
    app.include_router(health_router, prefix=settings.api_v1_prefix)
    app.include_router(enrolment_router, prefix=settings.api_v1_prefix)
    app.include_router(identification_router, prefix=settings.api_v1_prefix)
    return app


def get_asgi_app() -> FastAPI:
    """Entry point for ASGI servers (``uvicorn app.main:get_asgi_app --factory``)."""
    return create_app()
