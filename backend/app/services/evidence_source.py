"""Read-only access to one retained BlackGlass original; never copy or delete it."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings
from app.domain.evidence import SharedObject
from app.services.evidence import content_hash
from app.services.evidence_processor import EvidenceSettings


class SharedEvidenceSource:
    """Use only the operator-configured bucket and prefix, never request credentials."""

    def __init__(self, settings: Settings, options: EvidenceSettings) -> None:
        """Keep AWS access scoped separately from EagleEye's writable media store."""
        import boto3

        self.bucket = options.source_bucket or settings.blackglass_s3_bucket
        self.prefix = options.source_prefix
        if not self.bucket or not self.prefix or not self.prefix.endswith("/"):
            raise ValueError("shared evidence bucket and slash-terminated prefix are required")
        self.client = boto3.client(
            "s3",
            region_name=settings.blackglass_aws_region,
            aws_access_key_id=settings.blackglass_aws_access_key_id,
            aws_secret_access_key=settings.blackglass_aws_secret_access_key,
        )

    def arguments(self, item: SharedObject) -> dict[str, Any]:
        """Reject references outside the configured input boundary."""
        if not item.key.startswith(self.prefix):
            raise ValueError("object is outside the configured evidence prefix")
        args: dict[str, Any] = {"Bucket": self.bucket, "Key": item.key}
        if item.version_id:
            args["VersionId"] = item.version_id
        return args

    async def inspect(self, item: SharedObject) -> None:
        """Verify existence and bounded size without downloading original bytes."""
        args = self.arguments(item)
        response = await asyncio.to_thread(self.client.head_object, **args)
        if response["ContentLength"] != item.byte_size:
            raise ValueError("original object size does not match the manifest")

    async def read(self, item: SharedObject) -> bytes:
        """Read the exact object version and verify original evidence integrity."""
        args = self.arguments(item)
        response = await asyncio.to_thread(self.client.get_object, **args)
        body = response["Body"]
        try:
            if response["ContentLength"] != item.byte_size:
                raise ValueError("original object size changed")
            data = await asyncio.to_thread(body.read, item.byte_size + 1)
        finally:
            body.close()
        if len(data) != item.byte_size or content_hash(data) != item.sha256:
            raise ValueError("original evidence checksum does not match")
        return bytes(data)
