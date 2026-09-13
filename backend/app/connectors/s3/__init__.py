"""S3-compatible object storage (MinIO locally, S3 in AWS)."""

from app.connectors.s3.blackglass import (
    BlackGlassS3Body,
    BlackGlassS3Config,
    BlackGlassS3Error,
    BlackGlassS3Object,
    BlackGlassS3Page,
    BlackGlassS3Source,
)
from app.connectors.s3.blob_store import S3BlobStore, S3Config

__all__ = [
    "BlackGlassS3Body",
    "BlackGlassS3Config",
    "BlackGlassS3Error",
    "BlackGlassS3Object",
    "BlackGlassS3Page",
    "BlackGlassS3Source",
    "S3BlobStore",
    "S3Config",
]
