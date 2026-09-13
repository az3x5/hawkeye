"""Read-only contract tests for the BlackGlass AWS source."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

import pytest

from app.connectors.s3 import (
    BlackGlassS3Config,
    BlackGlassS3Error,
    BlackGlassS3Source,
)


class RecordingS3Client:
    """Minimal synchronous boto client used behind the async adapter."""

    def __init__(self, data: bytes = b"image") -> None:
        self.data = data
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_objects_v2(self, **arguments: object) -> dict[str, object]:
        self.calls.append(("list", arguments))
        return {
            "Contents": [
                {
                    "Key": "persons/42/front.jpg",
                    "Size": len(self.data),
                    "ETag": '"abc"',
                    "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                }
            ],
            "IsTruncated": True,
            "NextContinuationToken": "next-page",
        }

    def get_object(self, **arguments: object) -> dict[str, object]:
        self.calls.append(("get", arguments))
        return {
            "Body": BytesIO(self.data),
            "ContentLength": len(self.data),
            "ContentType": "image/jpeg",
        }


def source(client: RecordingS3Client) -> BlackGlassS3Source:
    return BlackGlassS3Source(
        BlackGlassS3Config(
            bucket="blackglass-test",
            region="ap-south-1",
            access_key="read-only",
            secret_key="secret",
            prefix="persons/",
        ),
        client=client,
    )


async def test_listing_is_prefix_bounded_and_resumable() -> None:
    client = RecordingS3Client()
    page = await source(client).list_page(limit=20, cursor="previous-page")

    assert page.objects[0].key == "persons/42/front.jpg"
    assert page.objects[0].etag == "abc"
    assert page.next_cursor == "next-page"
    assert client.calls[0] == (
        "list",
        {
            "Bucket": "blackglass-test",
            "Prefix": "persons/",
            "MaxKeys": 20,
            "ContinuationToken": "previous-page",
        },
    )


async def test_download_is_size_bounded() -> None:
    client = RecordingS3Client(b"12345")
    downloaded = await source(client).read("persons/42/front.jpg", max_bytes=5)
    assert downloaded.data == b"12345"
    assert downloaded.content_type == "image/jpeg"


async def test_oversized_download_is_rejected_before_reading() -> None:
    client = RecordingS3Client(b"123456")
    with pytest.raises(BlackGlassS3Error, match="exceeds"):
        await source(client).read("persons/42/front.jpg", max_bytes=5)


async def test_key_outside_configured_prefix_is_rejected() -> None:
    with pytest.raises(BlackGlassS3Error, match="outside"):
        await source(RecordingS3Client()).read("incoming/photo.jpg", max_bytes=100)
