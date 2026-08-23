#!/usr/bin/env python3
"""Bulk-import faces into Hawkeye.

Each person is identified by two upstream identifiers, both scoped by a source:
their unique id and their NID. The API stores them as `id` and `local_id`
respectively and links both to one internal person_uuid.

Two input shapes:

    # a manifest, which is the auditable option
    import_faces.py --api http://host:8000 --source registry \\
        --manifest people.csv --images ./faces

    # or filenames that carry the identifiers
    import_faces.py --api http://host:8000 --source registry \\
        --images ./faces --pattern '{id}_{nid}'

The manifest needs `id`, `image`, and the second identifier as either
`local_id` or `nid`; extra columns are ignored.
Several rows may share an id and nid, which enrols several samples for that
person — the system is designed for that and matching improves with it.

Importing is resumable. The API is idempotent on (source, identifier, image
content), so re-running skips what is already in place rather than duplicating
it; a re-run after a partial import is the intended way to finish the job.

NIDs are personal data. They are written to the report, which is created
readable only by you, and are abbreviated in anything printed to the terminal.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class Row:
    """One image to enrol, and who it belongs to."""

    person_id: str
    nid: str
    image: Path


@dataclass
class Result:
    """What happened to one row."""

    person_id: str
    nid: str
    image: str
    status: str
    person_uuid: str = ""
    face_sample_uuid: str = ""
    detail: str = ""


@dataclass
class Totals:
    """Counts for the summary."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, status: str) -> None:
        self.counts[status] = self.counts.get(status, 0) + 1


def mask(nid: str) -> str:
    """Abbreviate an NID for terminal output."""
    return f"{nid[:2]}…{nid[-2:]}" if len(nid) > 5 else "…"


# --------------------------------------------------------------------------
# HTTP, on the standard library so this runs anywhere
# --------------------------------------------------------------------------


class ApiError(Exception):
    """The API refused the request."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _request(url: str, *, data: bytes | None, headers: dict[str, str], method: str) -> dict:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            payload = json.loads(raw)["error"]
            raise ApiError(error.code, payload["code"], payload["message"]) from None
        except (json.JSONDecodeError, KeyError, TypeError):
            raise ApiError(error.code, "unexpected_error", raw.decode()[:200]) from None
    except urllib.error.URLError as error:
        raise ApiError(0, "unreachable", str(error.reason)) from None


def sign_in(api: str, email: str, password: str) -> str:
    """Exchange an email and password for a session token."""
    payload = json.dumps({"email": email, "password": password}).encode()
    result = _request(
        f"{api}/api/v1/sessions",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    return str(result["token"])


def _multipart(fields: dict[str, str], image: Path) -> tuple[bytes, str]:
    """Encode an enrolment as multipart/form-data."""
    boundary = f"----hawkeye{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    content_type = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
        f'filename="{image.name}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
    )
    parts.append(image.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def enrol(api: str, token: str, source: str, row: Row) -> dict:
    """Enrol one image, retrying while the server asks us to slow down."""
    fields = {"source": source, "external_id": row.person_id, "local_id": row.nid}
    body, content_type = _multipart(fields, row.image)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
        "Accept": "application/json",
    }

    for attempt in range(6):
        try:
            return _request(f"{api}/api/v1/enrolments", data=body, headers=headers, method="POST")
        except ApiError as error:
            # Rate limiting is the server pacing us, not a failure. Anything
            # else is the caller's problem and should surface immediately.
            if error.code != "rate_limited" or attempt == 5:
                raise
            time.sleep(min(2**attempt, 30))
    raise ApiError(429, "rate_limited", "still rate limited after retrying")


def sample_state(api: str, token: str, sample_uuid: str) -> dict:
    """Read one sample's progress through the pipeline."""
    return _request(
        f"{api}/api/v1/face-samples/{sample_uuid}",
        data=None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------


def read_manifest(manifest: Path, images: Path) -> list[Row]:
    """Read rows from a CSV with id, nid and image columns."""
    rows: list[Row] = []
    with manifest.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        # The second identifier is the API's `local_id`; manifests in the wild
        # call it either that or `nid`, and both mean the same thing here.
        nid_column = next((c for c in ("local_id", "nid") if c in columns), None)
        missing = ({"id", "image"} - columns) | ({"local_id or nid"} if nid_column is None else set())
        if missing:
            raise SystemExit(
                f"{manifest} is missing column(s): {', '.join(sorted(missing))}. "
                f"Found: {', '.join(reader.fieldnames or [])}"
            )
        for number, record in enumerate(reader, start=2):
            person_id = (record["id"] or "").strip()
            nid = (record[nid_column] or "").strip()
            name = (record["image"] or "").strip()
            if not (person_id and nid and name):
                print(f"  row {number}: skipped, missing id, {nid_column} or image", file=sys.stderr)
                continue
            path = Path(name)
            rows.append(Row(person_id, nid, path if path.is_absolute() else images / path))
    return rows


def read_pattern(images: Path, pattern: str) -> list[Row]:
    """Read rows from filenames such as `{id}_{nid}`."""
    expression = re.escape(pattern).replace(r"\{id\}", "(?P<id>[^/]+?)").replace(
        r"\{nid\}", "(?P<nid>[^/]+?)"
    )
    matcher = re.compile(f"^{expression}$")

    rows: list[Row] = []
    for path in sorted(images.rglob("*")):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        found = matcher.match(path.stem)
        if found is None:
            print(f"  {path.name}: skipped, does not match the pattern", file=sys.stderr)
            continue
        rows.append(Row(found.group("id"), found.group("nid"), path))
    return rows


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def import_one(api: str, token: str, source: str, row: Row) -> Result:
    """Enrol one row, turning every outcome into a reportable result."""
    if not row.image.is_file():
        return Result(row.person_id, row.nid, str(row.image), "missing_image")

    try:
        response = enrol(api, token, source, row)
    except ApiError as error:
        # A conflict means these identifiers already denote different people.
        # That is a data problem worth seeing, not a transport failure.
        status = "conflict" if error.code == "conflicting_identifiers" else "error"
        return Result(row.person_id, row.nid, str(row.image), status, detail=f"{error.code}: {error.message}")

    sample = response["sample"]
    return Result(
        person_id=row.person_id,
        nid=row.nid,
        image=str(row.image),
        status="enrolled" if response["created"] else "already_enrolled",
        person_uuid=response["person_uuid"],
        face_sample_uuid=sample["face_sample_uuid"],
    )


def await_processing(api: str, token: str, results: list[Result], timeout: int) -> None:
    """Follow samples until the worker has embedded them, or given up."""
    pending = {r.face_sample_uuid: r for r in results if r.status == "enrolled"}
    if not pending:
        return

    print(f"\nwaiting for {len(pending)} sample(s) to be embedded…")
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        time.sleep(2)
        for sample_uuid in list(pending):
            try:
                state = sample_state(api, token, sample_uuid)
            except ApiError:
                continue
            if state["processing_state"] == "pending":
                continue
            result = pending.pop(sample_uuid)
            result.status = state["processing_state"]
            result.detail = state.get("failure_reason") or ""

    for result in pending.values():
        result.status = "still_pending"
        result.detail = f"not embedded within {timeout}s"


def write_report(path: Path, results: list[Result]) -> None:
    """Write the per-row outcome, readable only by the owner."""
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            ["id", "nid", "image", "status", "person_uuid", "face_sample_uuid", "detail"]
        )
        for r in results:
            writer.writerow(
                [r.person_id, r.nid, r.image, r.status, r.person_uuid, r.face_sample_uuid, r.detail]
            )


def main() -> int:
    """Parse arguments and run the import."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", required=True, help="e.g. http://100.74.113.94:8000")
    parser.add_argument("--source", required=True, help="what system these identifiers come from")
    parser.add_argument("--images", type=Path, required=True, help="directory holding the images")
    parser.add_argument(
        "--manifest", type=Path, help="CSV with id, local_id (or nid) and image columns"
    )
    parser.add_argument("--pattern", help="filename pattern, e.g. '{id}_{nid}'")
    parser.add_argument("--email", default=os.environ.get("HAWKEYE_EMAIL"))
    parser.add_argument(
        "--password",
        default=os.environ.get("HAWKEYE_PASSWORD"),
        help="prefer the HAWKEYE_PASSWORD environment variable to keep it out of shell history",
    )
    parser.add_argument("--token", default=os.environ.get("HAWKEYE_TOKEN"), help="instead of email and password")
    parser.add_argument("--report", type=Path, default=Path("import-report.csv"))
    parser.add_argument("--workers", type=int, default=4, help="parallel uploads")
    parser.add_argument("--wait", type=int, default=180, help="seconds to wait for embedding; 0 to skip")
    parser.add_argument("--limit", type=int, help="import only the first N rows, for a trial run")
    parser.add_argument("--dry-run", action="store_true", help="read the input and report, upload nothing")
    args = parser.parse_args()

    if bool(args.manifest) == bool(args.pattern):
        parser.error("give exactly one of --manifest or --pattern")
    if not args.images.is_dir():
        parser.error(f"--images {args.images} is not a directory")

    print(f"reading {'manifest ' + str(args.manifest) if args.manifest else 'filenames'}…")
    rows = read_manifest(args.manifest, args.images) if args.manifest else read_pattern(args.images, args.pattern)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("nothing to import")
        return 1

    people = len({(r.person_id, r.nid) for r in rows})
    print(f"{len(rows)} image(s) for {people} person(s)")

    if args.dry_run:
        for row in rows[:10]:
            print(f"  would enrol id={row.person_id} nid={mask(row.nid)} {row.image.name}")
        if len(rows) > 10:
            print(f"  … and {len(rows) - 10} more")
        return 0

    token = args.token
    if not token:
        if not (args.email and args.password):
            parser.error("give --token, or --email and --password (or HAWKEYE_PASSWORD)")
        try:
            token = sign_in(args.api, args.email, args.password)
        except ApiError as error:
            print(f"could not sign in: {error.message}", file=sys.stderr)
            return 1

    print(f"uploading with {args.workers} worker(s)…")
    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for done, result in enumerate(
            pool.map(lambda row: import_one(args.api, token, args.source, row), rows), start=1
        ):
            results.append(result)
            if result.status in {"error", "conflict", "missing_image"}:
                print(f"  [{done}/{len(rows)}] id={result.person_id} {result.status}: {result.detail}")
            elif done % 25 == 0 or done == len(rows):
                print(f"  [{done}/{len(rows)}]")

    if args.wait:
        await_processing(args.api, token, results, args.wait)

    totals = Totals()
    for result in results:
        totals.add(result.status)

    write_report(args.report, results)
    print("\nsummary")
    for status, count in sorted(totals.counts.items(), key=lambda item: -item[1]):
        print(f"  {status:16} {count}")
    print(f"\nreport written to {args.report} (owner-readable only; it contains NIDs)")

    # A non-zero exit when anything needs a human, so a scripted import fails
    # loudly rather than looking successful.
    return 1 if {"error", "conflict", "failed", "missing_image"} & totals.counts.keys() else 0


if __name__ == "__main__":
    sys.exit(main())
