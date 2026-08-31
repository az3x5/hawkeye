"""One contract, two backends.

The value of the storage seam is that the filesystem and S3 implementations
are interchangeable. That is only true if the same tests pass against both, so
these are written once and parametrised over the backends: the filesystem
always, MinIO/S3 when an endpoint is configured.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.connectors.filesystem import FilesystemBlobStore
from app.connectors.s3 import S3BlobStore, S3Config
from app.domain.storage import BlobStore, BlobStoreError, ObjectLocation

S3_ENDPOINT = os.environ.get("FACEID_TEST_S3_ENDPOINT_URL")
S3_ACCESS_KEY = os.environ.get("FACEID_TEST_S3_ACCESS_KEY")
S3_SECRET_KEY = os.environ.get("FACEID_TEST_S3_SECRET_KEY")

_S3_CONFIGURED = bool(S3_ENDPOINT and S3_ACCESS_KEY and S3_SECRET_KEY)


class TestObjectLocation:
    """Addresses are validated where they are built, not at each call site."""

    def test_a_plain_address_is_accepted(self) -> None:
        assert str(ObjectLocation("eagleeye-media", "ab/abc.jpg")) == "eagleeye-media/ab/abc.jpg"

    @pytest.mark.parametrize(
        "key",
        [
            "../../etc/passwd",
            "ab/../../../etc/passwd",
            "/etc/passwd",
            "ab//cd",
            "",
        ],
    )
    def test_traversal_and_empty_segments_are_refused(self, key: str) -> None:
        with pytest.raises(BlobStoreError):
            ObjectLocation("eagleeye-media", key)

    @pytest.mark.parametrize("bucket", ["A", "x", "Bucket", "bucket_name", "b" * 64])
    def test_invalid_bucket_names_are_refused(self, bucket: str) -> None:
        with pytest.raises(BlobStoreError):
            ObjectLocation(bucket, "key")

    def test_an_overlong_key_is_refused(self) -> None:
        with pytest.raises(BlobStoreError, match="1-512 characters"):
            ObjectLocation("eagleeye-media", "a" * 513)


@pytest.fixture
def filesystem_store(tmp_path: Path) -> FilesystemBlobStore:
    return FilesystemBlobStore(tmp_path / "buckets")


@pytest.fixture
def s3_store() -> Iterator[S3BlobStore]:
    if not _S3_CONFIGURED:
        pytest.skip("set FACEID_TEST_S3_ENDPOINT_URL/ACCESS_KEY/SECRET_KEY to run S3 tests")
    assert S3_ACCESS_KEY is not None
    assert S3_SECRET_KEY is not None
    yield S3BlobStore(
        S3Config(
            endpoint_url=S3_ENDPOINT,
            region="us-east-1",
            access_key=S3_ACCESS_KEY,
            secret_key=S3_SECRET_KEY,
            use_path_style=True,
        )
    )


@pytest.fixture(params=["filesystem", "s3"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[BlobStore]:
    """Each contract test runs against every configured backend."""
    built: BlobStore = request.getfixturevalue(f"{request.param}_store")
    yield built


@pytest.fixture
async def bucket(store: BlobStore) -> AsyncIterator[str]:
    """A bucket unique to this test, so backends can share one endpoint.

    Torn down afterwards: a shared local MinIO would otherwise accumulate a
    bucket per test run until somebody wondered what they all were.
    """
    name = f"eagleeye-test-{uuid4().hex[:12]}"
    await store.ensure_bucket(name)
    yield name
    for key in await store.list_keys(name):
        await store.delete(ObjectLocation(name, key))
    await _discard_bucket(store, name)


async def _discard_bucket(store: BlobStore, name: str) -> None:
    """Remove an emptied test bucket where the backend can."""
    if isinstance(store, S3BlobStore):
        await store._call("delete_bucket", Bucket=name)  # noqa: SLF001 - test cleanup
    elif isinstance(store, FilesystemBlobStore):
        shutil.rmtree(store.root / name, ignore_errors=True)


class TestBlobStoreContract:
    async def test_stored_bytes_come_back_unchanged(self, store: BlobStore, bucket: str) -> None:
        location = ObjectLocation(bucket, "ab/object.bin")
        await store.put(location, b"the payload", content_type="application/octet-stream")
        assert await store.get(location) == b"the payload"

    async def test_a_missing_object_reads_as_none_rather_than_raising(
        self, store: BlobStore, bucket: str
    ) -> None:
        assert await store.get(ObjectLocation(bucket, "nothing/here.bin")) is None

    async def test_stat_reports_size_and_content_type(self, store: BlobStore, bucket: str) -> None:
        location = ObjectLocation(bucket, "cd/image.jpg")
        await store.put(location, b"\xff\xd8\xff" + b"x" * 100, content_type="image/jpeg")
        stored = await store.stat(location)
        assert stored is not None
        assert stored.size_bytes == 103
        assert stored.content_type == "image/jpeg"

    async def test_stat_of_a_missing_object_is_none(self, store: BlobStore, bucket: str) -> None:
        assert await store.stat(ObjectLocation(bucket, "ef/absent.bin")) is None

    async def test_a_range_read_returns_only_that_window(
        self, store: BlobStore, bucket: str
    ) -> None:
        location = ObjectLocation(bucket, "ab/ranged.bin")
        await store.put(location, b"0123456789", content_type="application/octet-stream")
        assert await store.get_range(location, 2, 4) == b"2345"

    async def test_a_range_beyond_the_object_returns_what_exists(
        self, store: BlobStore, bucket: str
    ) -> None:
        location = ObjectLocation(bucket, "ab/short.bin")
        await store.put(location, b"abc", content_type="application/octet-stream")
        assert await store.get_range(location, 1, 100) == b"bc"

    async def test_an_invalid_range_is_refused(self, store: BlobStore, bucket: str) -> None:
        location = ObjectLocation(bucket, "ab/x.bin")
        with pytest.raises(BlobStoreError):
            await store.get_range(location, -1, 10)

    async def test_rewriting_the_same_location_is_not_an_error(
        self, store: BlobStore, bucket: str
    ) -> None:
        location = ObjectLocation(bucket, "ab/repeat.bin")
        await store.put(location, b"same", content_type="text/plain")
        await store.put(location, b"same", content_type="text/plain")
        assert await store.get(location) == b"same"

    async def test_delete_removes_the_object_and_reports_it(
        self, store: BlobStore, bucket: str
    ) -> None:
        location = ObjectLocation(bucket, "ab/gone.bin")
        await store.put(location, b"bytes", content_type="application/octet-stream")
        assert await store.delete(location) is True
        assert await store.get(location) is None

    async def test_deleting_what_is_not_there_reports_false(
        self, store: BlobStore, bucket: str
    ) -> None:
        assert await store.delete(ObjectLocation(bucket, "ab/never.bin")) is False

    async def test_listing_returns_keys_and_honours_a_prefix(
        self, store: BlobStore, bucket: str
    ) -> None:
        for key in ("ab/one.bin", "ab/two.bin", "cd/three.bin"):
            await store.put(
                ObjectLocation(bucket, key), b"x", content_type="application/octet-stream"
            )
        assert await store.list_keys(bucket) == ["ab/one.bin", "ab/two.bin", "cd/three.bin"]
        assert await store.list_keys(bucket, prefix="ab/") == ["ab/one.bin", "ab/two.bin"]

    async def test_listing_can_exclude_recent_objects(self, store: BlobStore, bucket: str) -> None:
        """Reconciliation must not sweep an object whose row is still in flight."""
        await store.put(
            ObjectLocation(bucket, "ab/fresh.bin"), b"x", content_type="application/octet-stream"
        )
        cutoff = datetime.now(UTC) - timedelta(minutes=5)
        assert await store.list_keys(bucket, older_than=cutoff) == []

    async def test_listing_an_unknown_bucket_is_empty_rather_than_an_error(
        self, store: BlobStore
    ) -> None:
        assert await store.list_keys(f"eagleeye-absent-{uuid4().hex[:8]}") == []

    async def test_ensure_bucket_is_idempotent(self, store: BlobStore, bucket: str) -> None:
        await store.ensure_bucket(bucket)
        await store.ensure_bucket(bucket)

    async def test_ping_succeeds_against_a_reachable_store(self, store: BlobStore) -> None:
        await store.ping()


class TestFilesystemSpecificSafety:
    """The filesystem backend turns strings into paths, so it gets extra care."""

    async def test_a_key_cannot_escape_the_storage_root(
        self, filesystem_store: FilesystemBlobStore, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside.txt"
        outside.write_text("untouched")
        # ObjectLocation refuses traversal, which is the first line of defence.
        with pytest.raises(BlobStoreError):
            ObjectLocation("eagleeye-media", "../../outside.txt")
        assert outside.read_text() == "untouched"

    async def test_the_metadata_sidecar_is_not_listed_as_an_object(
        self, filesystem_store: FilesystemBlobStore
    ) -> None:
        await filesystem_store.ensure_bucket("eagleeye-media")
        await filesystem_store.put(
            ObjectLocation("eagleeye-media", "ab/one.jpg"), b"x", content_type="image/jpeg"
        )
        assert await filesystem_store.list_keys("eagleeye-media") == ["ab/one.jpg"]
