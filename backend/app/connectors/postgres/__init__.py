"""PostgreSQL storage connector and repository implementations."""

from app.connectors.postgres.connector import PostgresConnector
from app.connectors.postgres.job_queue import PostgresJobConsumer
from app.connectors.postgres.jobs import SqlAlchemyProcessingJobRepository
from app.connectors.postgres.language import SqlAlchemyLanguageDocumentRepository
from app.connectors.postgres.processing_tables import (
    processing_job_attempts,
    processing_jobs,
    processing_worker_heartbeats,
)
from app.connectors.postgres.repositories import (
    SqlAlchemyEmbeddingMetadataRepository,
    SqlAlchemyFaceSampleRepository,
    SqlAlchemyPersonRepository,
)
from app.connectors.postgres.tables import metadata

__all__ = [
    "PostgresConnector",
    "PostgresJobConsumer",
    "SqlAlchemyEmbeddingMetadataRepository",
    "SqlAlchemyFaceSampleRepository",
    "SqlAlchemyLanguageDocumentRepository",
    "SqlAlchemyPersonRepository",
    "SqlAlchemyProcessingJobRepository",
    "metadata",
    "processing_job_attempts",
    "processing_jobs",
    "processing_worker_heartbeats",
]
