"""Read-only BlackGlass source access over AWS S3."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import Any, cast

from app.connectors.s3.blob_store import _load_boto3


class BlackGlassS3Error(Exception):
    """The configured BlackGlass bucket could not be read safely."""


@dataclass(frozen=True, slots=True)
class BlackGlassS3Config:
    """Credentials and location for the external, read-only source bucket."""

    bucket: str
    region: str
    access_key: str
    secret_key: str
    prefix: str = "persons/"


@dataclass(frozen=True, slots=True)
class BlackGlassS3Object:
    """Bounded metadata returned by an S3 listing."""

    key: str
    size_bytes: int
    etag: str
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class BlackGlassS3Page:
    """One resumable S3 listing page."""

    objects: tuple[BlackGlassS3Object, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class BlackGlassS3Body:
    """Downloaded bytes with the source's declared content type."""

    data: bytes
    content_type: str | None


class BlackGlassS3Source:
    """Lists and downloads BlackGlass objects without write permissions."""

    def __init__(self, config: BlackGlassS3Config, *, client: Any | None = None) -> None:
        """Build a read-only source client without making a network call."""
        if not config.bucket.strip():
            raise ValueError("BlackGlass S3 bucket must not be empty")
        if not config.prefix or config.prefix.startswith("/"):
            raise ValueError("BlackGlass S3 prefix must be relative and non-empty")
        self.config = config
        self._client = client or self._build_client(_load_boto3(), config)

    @staticmethod
    def _build_client(boto3: ModuleType, config: BlackGlassS3Config) -> Any:
        return boto3.client(
            "s3",
            region_name=config.region,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
        )

    async def _call(self, operation: str, **kwargs: Any) -> Any:
        method = getattr(self._client, operation)
        return await asyncio.to_thread(lambda: method(**kwargs))

    async def ping(self) -> None:
        """Verify read access without listing the entire bucket."""
        try:
            await self._call(
                "list_objects_v2",
                Bucket=self.config.bucket,
                Prefix=self.config.prefix,
                MaxKeys=1,
            )
        except Exception as exc:
            raise BlackGlassS3Error("BlackGlass S3 source is not reachable") from exc

    async def list_page(self, *, limit: int, cursor: str | None = None) -> BlackGlassS3Page:
        """Return one source page under the configured prefix."""
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        arguments: dict[str, Any] = {
            "Bucket": self.config.bucket,
            "Prefix": self.config.prefix,
            "MaxKeys": limit,
        }
        if cursor:
            arguments["ContinuationToken"] = cursor
        try:
            response = await self._call("list_objects_v2", **arguments)
        except Exception as exc:
            raise BlackGlassS3Error("could not list the BlackGlass S3 source") from exc
        objects = tuple(
            BlackGlassS3Object(
                key=str(item["Key"]),
                size_bytes=int(item.get("Size", 0)),
                etag=str(item.get("ETag", "")).strip('"'),
                last_modified=cast("datetime", item["LastModified"]),
            )
            for item in response.get("Contents", [])
        )
        next_cursor = (
            str(response["NextContinuationToken"])
            if response.get("IsTruncated") and response.get("NextContinuationToken")
            else None
        )
        return BlackGlassS3Page(objects=objects, next_cursor=next_cursor)

    async def read(self, key: str, *, max_bytes: int) -> BlackGlassS3Body:
        """Read one listed object with a hard response-size ceiling."""
        if not key.startswith(self.config.prefix):
            raise BlackGlassS3Error("object key is outside the configured BlackGlass prefix")
        try:
            response = await self._call("get_object", Bucket=self.config.bucket, Key=key)
            declared_size = int(response.get("ContentLength", 0))
            if declared_size > max_bytes:
                response["Body"].close()
                raise BlackGlassS3Error(f"object exceeds the {max_bytes}-byte face-image limit")
            body = response["Body"]
            try:
                data = await asyncio.to_thread(body.read, max_bytes + 1)
            finally:
                body.close()
        except BlackGlassS3Error:
            raise
        except Exception as exc:
            raise BlackGlassS3Error("could not download the BlackGlass S3 object") from exc
        if len(data) > max_bytes:
            raise BlackGlassS3Error(f"object exceeds the {max_bytes}-byte face-image limit")
        return BlackGlassS3Body(
            data=bytes(data),
            content_type=(str(response["ContentType"]) if response.get("ContentType") else None),
        )
