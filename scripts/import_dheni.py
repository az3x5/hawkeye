#!/usr/bin/env python3
"""Import every Dheni person photo into Hawkeye with a durable checkpoint.

The input CSV must contain ``id`` and ``local_id``. Dheni's image API uses
``local_id`` as its ``personId`` path parameter; Hawkeye stores both values as
scoped external identifiers. Production uses OAuth client credentials and the
IIMS MinIO photo API. The legacy API-key/signed-URL source remains available
for resumability during migration.

Every completed photo is committed to a local SQLite checkpoint keyed by
``(id, photoId)``. Successful photos are skipped on subsequent runs, so
restarting this command is safe and newly added photos are still discovered.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

import import_faces as hawkeye

DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
SUCCESS_STATUSES = {"enrolled", "already_enrolled"}


class DheniError(Exception):
    """The Dheni photo source could not provide a usable image."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True, slots=True)
class Person:
    """Identifiers needed by Dheni and Hawkeye."""

    external_id: str
    local_id: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    """One durable import outcome."""

    person: Person
    photo_id: str
    status: str
    person_uuid: str = ""
    sample_uuid: str = ""
    detail: str = ""


class OAuthTokenProvider:
    """Thread-safe, expiry-aware OAuth client-credentials token cache."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str,
        audience: str,
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.audience = audience
        self._token = ""
        self._refresh_at = 0.0
        self._lock = threading.Lock()

    def authorization(self, *, force_refresh: bool = False) -> str:
        """Return a cached Bearer header, refreshing once near expiry."""
        with self._lock:
            if not force_refresh and self._token and time.monotonic() < self._refresh_at:
                return f"Bearer {self._token}"

            fields = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
            if self.scope:
                fields["scope"] = self.scope
            if self.audience:
                fields["audience"] = self.audience
            request = urllib.request.Request(
                self.token_url,
                data=urllib.parse.urlencode(fields).encode(),
                headers={"Accept": "application/json"},
                method="POST",
            )
            with _open_with_retry(request) as response:
                try:
                    payload = json.loads(response.read())
                    token = payload["access_token"]
                    token_type = payload.get("token_type", "Bearer")
                    expires_in = float(payload.get("expires_in", 900))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    raise DheniError(502, "OAuth token response has an unexpected shape") from None
            if not isinstance(token, str) or not token or str(token_type).lower() != "bearer":
                raise DheniError(502, "OAuth token response has no usable Bearer token")

            self._token = token
            # The service documents 15-minute tokens. Refresh one minute early,
            # or halfway through unusually short-lived test tokens.
            safety_margin = min(60.0, max(1.0, expires_in / 2))
            self._refresh_at = time.monotonic() + max(1.0, expires_in - safety_margin)
            return f"Bearer {self._token}"


class LegacyPhotoSource:
    """Adapter for the original API-key and signed-image API."""

    def __init__(self, api_url: str, api_key: str) -> None:
        self.api_url = api_url
        self.api_key = api_key

    def list_photos(self, person_id: str) -> list[dict[str, object]]:
        return _list_legacy_photos(self.api_url, self.api_key, person_id)

    def download_photo(
        self, person_id: str, image: dict[str, object], max_image_bytes: int
    ) -> tuple[bytes, str, str]:
        del person_id
        return _download_legacy_photo(
            image, dheni_api=self.api_url, max_image_bytes=max_image_bytes
        )


class OAuthPhotoSource:
    """Adapter for the IIMS OAuth-backed person-photo API."""

    def __init__(self, api_url: str, tokens: OAuthTokenProvider) -> None:
        self.api_url = api_url.rstrip("/")
        self.tokens = tokens

    def _open(self, url: str, *, accept: str):
        for auth_attempt in range(2):
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": self.tokens.authorization(
                        force_refresh=auth_attempt == 1
                    ),
                    "Accept": accept,
                },
                method="GET",
            )
            try:
                return _open_with_retry(request)
            except DheniError as error:
                if error.status != 401 or auth_attempt == 1:
                    raise
        raise DheniError(401, "OAuth token was rejected")

    def list_photos(self, person_id: str) -> list[dict[str, object]]:
        encoded = urllib.parse.quote(person_id, safe="")
        url = f"{self.api_url}/v2/person/{encoded}/photos?format=metadata"
        with self._open(url, accept="application/json") as response:
            try:
                payload = json.loads(response.read())
                photos = payload["photos"]
            except (json.JSONDecodeError, KeyError, TypeError):
                raise DheniError(502, "photo-list response has an unexpected shape") from None
        if not isinstance(photos, list):
            raise DheniError(502, "photo-list response has no photos array")

        normalized: list[dict[str, object]] = []
        for photo in photos:
            if (
                not isinstance(photo, dict)
                or isinstance(photo.get("id"), bool)
                or not isinstance(photo.get("id"), (str, int))
            ):
                raise DheniError(502, "photo-list response contains invalid metadata")
            file_metadata = photo.get("file")
            filename = ""
            if isinstance(file_metadata, dict):
                filename = str(
                    file_metadata.get("realname") or file_metadata.get("filename") or ""
                )
            normalized.append({"photoId": str(photo["id"]), "filename": filename})
        return normalized

    def download_photo(
        self, person_id: str, image: dict[str, object], max_image_bytes: int
    ) -> tuple[bytes, str, str]:
        encoded_person = urllib.parse.quote(person_id, safe="")
        encoded_photo = urllib.parse.quote(str(image["photoId"]), safe="")
        url = f"{self.api_url}/v2/person/{encoded_person}/photos/{encoded_photo}"
        with self._open(url, accept="image/*") as response:
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise DheniError(
                    502, f"photo endpoint returned {content_type}, not an image"
                )
            content = response.read(max_image_bytes + 1)
        if len(content) > max_image_bytes:
            raise DheniError(413, f"image exceeds the {max_image_bytes}-byte safety limit")
        if not content:
            raise DheniError(502, "photo endpoint returned an empty image")
        supplied_name = Path(str(image.get("filename") or "")).name
        extension = (
            Path(supplied_name).suffix
            or mimetypes.guess_extension(content_type)
            or ".img"
        )
        return content, f"dheni-photo{extension}", content_type


def _error_message(raw: bytes) -> str:
    """Return a bounded source error without leaking signed URLs."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode(errors="replace")[:200]
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if isinstance(message, str):
            return message[:200]
    return "unexpected source response"


def _open_with_retry(request: urllib.request.Request, *, timeout: int = 120):
    """Open a source request, retrying transient transport failures."""
    for attempt in range(6):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            raw = error.read()
            if error.code not in RETRYABLE_HTTP_STATUSES or attempt == 5:
                raise DheniError(error.code, _error_message(raw)) from None
        except urllib.error.URLError as error:
            if attempt == 5:
                raise DheniError(0, str(error.reason)) from None
        time.sleep(min(2**attempt, 30))
    raise DheniError(0, "source remained unavailable after retries")


def _list_legacy_photos(
    dheni_api: str, api_key: str, local_id: str
) -> list[dict[str, object]]:
    """Fetch and validate every photo's metadata for one Dheni person."""
    encoded = urllib.parse.quote(local_id, safe="")
    request = urllib.request.Request(
        f"{dheni_api.rstrip('/')}/v1/persons/{encoded}/images",
        headers={"x-api-key": api_key, "Accept": "application/json"},
        method="GET",
    )
    with _open_with_retry(request) as response:
        try:
            payload = json.loads(response.read())
            images = payload["images"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise DheniError(
                502, "photo-list response has an unexpected shape"
            ) from None
    if not isinstance(images, list):
        raise DheniError(502, "photo-list response has no images array")
    for image in images:
        if (
            not isinstance(image, dict)
            or isinstance(image.get("photoId"), bool)
            or not isinstance(image.get("photoId"), (str, int))
            or not isinstance(image.get("url"), str)
        ):
            raise DheniError(502, "photo-list response contains invalid image metadata")
    return images


def _download_legacy_photo(
    image: dict[str, object], *, dheni_api: str, max_image_bytes: int
) -> tuple[bytes, str, str]:
    """Download one signed image URL and enforce origin and size bounds."""
    signed_url = str(image["url"])
    source_origin = urllib.parse.urlsplit(dheni_api)
    image_origin = urllib.parse.urlsplit(signed_url)
    if image_origin.scheme not in {"http", "https"} or (
        image_origin.scheme,
        image_origin.netloc,
    ) != (source_origin.scheme, source_origin.netloc):
        raise DheniError(
            502, "signed image URL points outside the configured Dheni origin"
        )

    request = urllib.request.Request(
        signed_url, headers={"Accept": "image/*"}, method="GET"
    )
    with _open_with_retry(request) as response:
        content_type = response.headers.get_content_type()
        if not content_type.startswith("image/"):
            raise DheniError(502, f"signed URL returned {content_type}, not an image")
        content = response.read(max_image_bytes + 1)
    if len(content) > max_image_bytes:
        raise DheniError(413, f"image exceeds the {max_image_bytes}-byte safety limit")
    if not content:
        raise DheniError(502, "signed URL returned an empty image")

    supplied_name = Path(str(image.get("filename") or "")).name
    extension = (
        Path(supplied_name).suffix or mimetypes.guess_extension(content_type) or ".img"
    )
    return content, f"dheni-photo{extension}", content_type


def _enrol_one(
    person: Person,
    metadata: dict[str, object],
    *,
    photo_source: LegacyPhotoSource | OAuthPhotoSource,
    hawkeye_api: str,
    hawkeye_token: str,
    source: str,
    max_image_bytes: int,
) -> ImportResult:
    """Download and enrol one photo from a person's source listing."""
    photo_id = str(metadata["photoId"])
    try:
        content, filename, _content_type = photo_source.download_photo(
            person.local_id, metadata, max_image_bytes
        )
    except DheniError as error:
        status = "source_missing" if error.status == 404 else "source_error"
        return ImportResult(
            person, photo_id, status, detail=f"HTTP {error.status}: {error.message}"
        )

    suffix = Path(filename).suffix
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as image_file:
            image_file.write(content)
            image_file.flush()
            row = hawkeye.Row(
                person.external_id, person.local_id, Path(image_file.name)
            )
            result = hawkeye.import_one(hawkeye_api, hawkeye_token, source, row)
    except OSError as error:
        return ImportResult(
            person, photo_id, "error", detail=f"temporary image: {error}"
        )

    return ImportResult(
        person,
        photo_id,
        result.status,
        person_uuid=result.person_uuid,
        sample_uuid=result.face_sample_uuid,
        detail=result.detail,
    )


def _enrol_person(
    person: Person,
    completed: set[tuple[str, str]],
    *,
    photo_source: LegacyPhotoSource | OAuthPhotoSource,
    hawkeye_api: str,
    hawkeye_token: str,
    source: str,
    max_image_bytes: int,
) -> list[ImportResult]:
    """List and enrol all not-yet-checkpointed photos for one person."""
    try:
        photos = photo_source.list_photos(person.local_id)
    except DheniError as error:
        status = "source_missing" if error.status == 404 else "source_error"
        return [
            ImportResult(
                person,
                "__list__",
                status,
                detail=f"HTTP {error.status}: {error.message}",
            )
        ]
    if not photos:
        return [
            ImportResult(
                person, "__none__", "source_missing", detail="no photos listed"
            )
        ]

    results: list[ImportResult] = []
    for photo in photos:
        photo_id = str(photo["photoId"])
        if (person.external_id, photo_id) in completed:
            continue
        results.append(
            _enrol_one(
                person,
                photo,
                photo_source=photo_source,
                hawkeye_api=hawkeye_api,
                hawkeye_token=hawkeye_token,
                source=source,
                max_image_bytes=max_image_bytes,
            )
        )
    return results


def read_people(path: Path) -> Iterator[Person]:
    """Yield valid identifiers from the Dheni export."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = {"id", "local_id"} - columns
        if missing:
            raise SystemExit(
                f"{path} is missing column(s): {', '.join(sorted(missing))}"
            )
        for number, row in enumerate(reader, start=2):
            external_id = (row["id"] or "").strip()
            local_id = (row["local_id"] or "").strip()
            if not external_id or not local_id:
                print(
                    f"row {number}: skipped because id or local_id is blank",
                    file=sys.stderr,
                )
                continue
            yield Person(external_id, local_id)


def open_checkpoint(path: Path) -> sqlite3.Connection:
    """Open the private, crash-safe progress database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS imports (
            external_id TEXT NOT NULL,
            photo_id TEXT NOT NULL,
            local_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1,
            person_uuid TEXT NOT NULL DEFAULT '',
            sample_uuid TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (external_id, photo_id)
        )
        """
    )
    connection.commit()
    return connection


def successful_photos(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    """Load photos that need no further source or enrollment traffic."""
    rows = connection.execute(
        "SELECT external_id, photo_id FROM imports WHERE status IN (?, ?)",
        tuple(sorted(SUCCESS_STATUSES)),
    )
    return {(str(row[0]), str(row[1])) for row in rows}


def save_result(connection: sqlite3.Connection, result: ImportResult) -> None:
    """Commit one outcome immediately so interruption loses no progress."""
    connection.execute(
        """
        INSERT INTO imports (
            external_id, photo_id, local_id, status, person_uuid, sample_uuid, detail
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(external_id, photo_id) DO UPDATE SET
            local_id = excluded.local_id,
            status = excluded.status,
            attempts = imports.attempts + 1,
            person_uuid = excluded.person_uuid,
            sample_uuid = excluded.sample_uuid,
            detail = excluded.detail,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            result.person.external_id,
            result.photo_id,
            result.person.local_id,
            result.status,
            result.person_uuid,
            result.sample_uuid,
            result.detail,
        ),
    )
    connection.commit()


def _submit(
    pool: ThreadPoolExecutor,
    person: Person,
    completed: set[tuple[str, str]],
    photo_source: LegacyPhotoSource | OAuthPhotoSource,
    args: argparse.Namespace,
) -> Future[list[ImportResult]]:
    return pool.submit(
        _enrol_person,
        person,
        completed,
        photo_source=photo_source,
        hawkeye_api=args.hawkeye_api,
        hawkeye_token=args.hawkeye_token,
        source=args.source,
        max_image_bytes=args.max_image_bytes,
    )


def run(args: argparse.Namespace) -> int:
    """Run a bounded concurrent import and checkpoint every result."""
    if args.dheni_client_id and args.dheni_client_secret:
        tokens = OAuthTokenProvider(
            args.dheni_token_url,
            args.dheni_client_id,
            args.dheni_client_secret,
            args.dheni_scope,
            args.dheni_audience,
        )
        photo_source: LegacyPhotoSource | OAuthPhotoSource = OAuthPhotoSource(
            args.dheni_photo_api_url, tokens
        )
    else:
        photo_source = LegacyPhotoSource(args.dheni_api, args.dheni_key)
    checkpoint = open_checkpoint(args.checkpoint)
    completed = successful_photos(checkpoint)
    people = islice(read_people(args.manifest), args.offset, None)

    submitted = 0
    finished = 0
    counts: Counter[str] = Counter()
    pending: dict[Future[list[ImportResult]], Person] = {}
    iterator = iter(people)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            while len(pending) < args.workers * 2 and (
                args.limit is None or submitted < args.limit
            ):
                try:
                    person = next(iterator)
                except StopIteration:
                    break
                pending[
                    _submit(pool, person, completed, photo_source, args)
                ] = person
                submitted += 1

            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    person = pending.pop(future)
                    try:
                        results = future.result()
                    except Exception as error:  # noqa: BLE001 - preserve the batch and checkpoint it
                        results = [
                            ImportResult(
                                person, "__worker__", "error", detail=str(error)[:300]
                            )
                        ]
                    for result in results:
                        save_result(checkpoint, result)
                        counts[result.status] += 1
                        finished += 1
                        if result.status not in SUCCESS_STATUSES:
                            print(
                                f"[{finished} photos/{submitted} people] {result.status}: {result.detail}"
                            )
                        elif finished % 25 == 0:
                            print(
                                f"[{finished} photos/{submitted} people] enrolled or already present"
                            )

                    if args.limit is None or submitted < args.limit:
                        try:
                            next_person = next(iterator)
                        except StopIteration:
                            continue
                        pending[
                            _submit(pool, next_person, completed, photo_source, args)
                        ] = next_person
                        submitted += 1
    except KeyboardInterrupt:
        print("interrupted; completed rows are checkpointed", file=sys.stderr)
        return 130
    finally:
        checkpoint.close()

    print("summary")
    for status, count in counts.most_common():
        print(f"  {status:18} {count}")
    print(f"checkpoint: {args.checkpoint}")
    return 1 if counts.keys() - SUCCESS_STATUSES else 0


def main() -> int:
    """Parse command-line settings and start the importer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, default=Path("dheni-import.sqlite"))
    parser.add_argument("--dheni-api", default=os.environ.get("DHENI_API_URL"))
    parser.add_argument("--dheni-key", default=os.environ.get("DHENI_API_KEY"))
    parser.add_argument(
        "--dheni-token-url",
        default=os.environ.get(
            "DHENI_TOKEN_URL", "https://iims.police.gov.mv/auth/api/oauth/token"
        ),
    )
    parser.add_argument(
        "--dheni-photo-api-url",
        default=os.environ.get(
            "DHENI_PHOTO_API_URL", "https://iims.police.gov.mv/minio/api"
        ),
    )
    parser.add_argument("--dheni-client-id", default=os.environ.get("DHENI_CLIENT_ID"))
    parser.add_argument(
        "--dheni-client-secret", default=os.environ.get("DHENI_CLIENT_SECRET")
    )
    parser.add_argument(
        "--dheni-scope",
        default=os.environ.get("DHENI_SCOPE", "minio:person-photos"),
    )
    parser.add_argument(
        "--dheni-audience", default=os.environ.get("DHENI_AUDIENCE", "minio")
    )
    parser.add_argument("--hawkeye-api", default=os.environ.get("HAWKEYE_API"))
    parser.add_argument("--hawkeye-token", default=os.environ.get("HAWKEYE_TOKEN"))
    parser.add_argument("--source", default="dheni")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="skip this many manifest people before resuming (checkpointing still prevents duplicates)",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-image-bytes", type=int, default=DEFAULT_MAX_IMAGE_BYTES)
    args = parser.parse_args()

    if not args.manifest.is_file():
        parser.error(f"manifest does not exist: {args.manifest}")
    for name in ("hawkeye_api", "hawkeye_token"):
        if not getattr(args, name):
            parser.error(
                f"--{name.replace('_', '-')} or its environment variable is required"
            )
    oauth_values = (args.dheni_client_id, args.dheni_client_secret)
    if any(oauth_values) and not all(oauth_values):
        parser.error("both --dheni-client-id and --dheni-client-secret are required")
    if not all(oauth_values) and not (args.dheni_api and args.dheni_key):
        parser.error(
            "OAuth client credentials or the legacy Dheni API URL and key are required"
        )
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.offset < 0:
        parser.error("--offset must be zero or greater")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
