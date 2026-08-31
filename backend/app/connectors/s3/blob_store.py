"""``BlobStore`` over the S3 API.

One class serves MinIO locally and S3 in AWS, because MinIO speaks the same
protocol; the only difference is an endpoint URL. That is the whole point of
the M2 storage seam — the AWS migration should be a configuration change, not
a rewrite.

boto3 is synchronous, so every call runs in a worker thread. An async S3
client would avoid the thread hop, but it would add a dependency whose failure
modes we would then own, and object storage calls here are dominated by
network time rather than by the cost of a thread.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType
from typing import Any, cast

from app.domain.storage import BlobStoreError, ObjectLocation, StoredObject

logger = logging.getLogger(__name__)

#: S3 error codes meaning "it is not there", which this seam reports as None
#: rather than as a failure.
_ABSENT_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})

#: S3 error codes meaning the bucket already exists and is ours.
_BUCKET_EXISTS_CODES = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})


@dataclass(frozen=True, slots=True)
class S3Config:
    """Connection settings for an S3-compatible endpoint."""

    endpoint_url: str | None
    region: str
    access_key: str
    secret_key: str
    #: MinIO is typically addressed with path-style URLs; AWS S3 prefers
    #: virtual-host style. Getting this wrong produces DNS errors that look
    #: nothing like a configuration mistake, so it is explicit.
    use_path_style: bool = True


def _load_boto3() -> ModuleType:
    """Import boto3, turning its absence into a clear configuration error."""
    try:
        import boto3  # noqa: PLC0415 - optional dependency, imported on demand
    except ImportError as exc:  # pragma: no cover - exercised by deployment
        raise BlobStoreError(
            "S3 object storage is configured but boto3 is not installed; "
            "install the 's3' extra or set FACEID_OBJECT_STORE_BACKEND=filesystem"
        ) from exc
    return cast("ModuleType", boto3)


class S3BlobStore:
    """``BlobStore`` backed by an S3-compatible endpoint."""

    provider_name = "s3"

    def __init__(self, config: S3Config) -> None:
        """Build a client for ``config``. No network call happens here."""
        boto3 = _load_boto3()
        from botocore.config import Config  # noqa: PLC0415 - paired with boto3

        self._config = config
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            region_name=config.region,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            config=Config(
                s3={"addressing_style": "path" if config.use_path_style else "virtual"},
                retries={"max_attempts": 3, "mode": "standard"},
                signature_version="s3v4",
            ),
        )

    @property
    def provider(self) -> str:
        """Short provider identifier."""
        return self.provider_name

    @staticmethod
    def _error_code(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return ""
        error = response.get("Error")
        if not isinstance(error, dict):
            return ""
        return str(error.get("Code", ""))

    @classmethod
    def _absent(cls, exc: Exception) -> bool:
        return cls._error_code(exc) in _ABSENT_CODES

    async def _call(self, operation: str, **kwargs: Any) -> Any:
        """Run one boto3 operation off the event loop."""
        method = getattr(self._client, operation)
        return await asyncio.to_thread(lambda: method(**kwargs))

    async def ping(self) -> None:
        """Raise if the endpoint is not reachable."""
        try:
            await self._call("list_buckets")
        except Exception as exc:
            raise BlobStoreError(f"S3 endpoint is not reachable: {exc}") from exc

    async def ensure_bucket(self, bucket: str) -> None:
        """Create ``bucket`` if it does not exist. Idempotent."""
        ObjectLocation(bucket=bucket, key="_")
        try:
            await self._call("head_bucket", Bucket=bucket)
            return
        except Exception as exc:
            if not self._absent(exc):
                raise BlobStoreError(f"cannot inspect bucket {bucket!r}: {exc}") from exc
        try:
            await self._call("create_bucket", Bucket=bucket)
        except Exception as exc:
            if self._error_code(exc) in _BUCKET_EXISTS_CODES:
                return
            raise BlobStoreError(f"cannot create bucket {bucket!r}: {exc}") from exc

    async def put(self, location: ObjectLocation, data: bytes, *, content_type: str) -> None:
        """Store ``data`` at ``location``."""
        try:
            await self._call(
                "put_object",
                Bucket=location.bucket,
                Key=location.key,
                Body=data,
                ContentType=content_type,
            )
        except Exception as exc:
            raise BlobStoreError(f"cannot store object {location}: {exc}") from exc

    async def get(self, location: ObjectLocation) -> bytes | None:
        """Return the stored bytes, or None if absent."""
        try:
            response = await self._call("get_object", Bucket=location.bucket, Key=location.key)
        except Exception as exc:
            if self._absent(exc):
                return None
            raise BlobStoreError(f"cannot read object {location}: {exc}") from exc
        body = response["Body"]
        return await asyncio.to_thread(body.read)  # type: ignore[no-any-return]

    async def get_range(self, location: ObjectLocation, start: int, length: int) -> bytes | None:
        """Return ``length`` bytes from ``start`` using an HTTP range request."""
        if start < 0 or length <= 0:
            raise BlobStoreError(f"invalid range: start={start} length={length}")
        # HTTP ranges are inclusive at both ends.
        header = f"bytes={start}-{start + length - 1}"
        try:
            response = await self._call(
                "get_object", Bucket=location.bucket, Key=location.key, Range=header
            )
        except Exception as exc:
            if self._absent(exc) or self._error_code(exc) == "InvalidRange":
                return None
            raise BlobStoreError(f"cannot read range of {location}: {exc}") from exc
        body = response["Body"]
        return await asyncio.to_thread(body.read)  # type: ignore[no-any-return]

    async def stat(self, location: ObjectLocation) -> StoredObject | None:
        """Return size, content type and modification time, or None if absent."""
        try:
            response = await self._call("head_object", Bucket=location.bucket, Key=location.key)
        except Exception as exc:
            if self._absent(exc):
                return None
            raise BlobStoreError(f"cannot stat object {location}: {exc}") from exc
        modified = response.get("LastModified")
        return StoredObject(
            location=location,
            size_bytes=int(response.get("ContentLength", 0)),
            content_type=str(response.get("ContentType", "application/octet-stream")),
            modified_at=modified if modified is not None else datetime.now(UTC),
        )

    async def delete(self, location: ObjectLocation) -> bool:
        """Remove the object. Returns whether anything was there to remove.

        S3 deletion is idempotent and reports nothing about prior existence,
        so this checks first. The check and the delete are not atomic; the
        return value is a courtesy for reconciliation logs, not a lock.
        """
        existed = await self.stat(location) is not None
        try:
            await self._call("delete_object", Bucket=location.bucket, Key=location.key)
        except Exception as exc:
            raise BlobStoreError(f"cannot delete object {location}: {exc}") from exc
        return existed

    async def list_keys(
        self, bucket: str, *, prefix: str = "", older_than: datetime | None = None
    ) -> list[str]:
        """Return keys in ``bucket``, optionally filtered by prefix and age."""
        ObjectLocation(bucket=bucket, key="_")
        keys: list[str] = []
        token: str | None = None
        while True:
            arguments: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token is not None:
                arguments["ContinuationToken"] = token
            try:
                response = await self._call("list_objects_v2", **arguments)
            except Exception as exc:
                if self._absent(exc):
                    return []
                raise BlobStoreError(f"cannot list bucket {bucket!r}: {exc}") from exc
            for entry in response.get("Contents", []):
                if older_than is not None and entry.get("LastModified", older_than) >= older_than:
                    continue
                keys.append(str(entry["Key"]))
            if not response.get("IsTruncated"):
                return sorted(keys)
            token = response.get("NextContinuationToken")
            if token is None:
                return sorted(keys)
