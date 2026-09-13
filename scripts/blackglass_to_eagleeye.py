#!/usr/bin/env python3
"""Deliver BlackGlass exports to EagleEye's versioned ingestion API.

The EagleEye token is read from ``EAGLEEYE_TOKEN`` and is never accepted as a
command-line argument, which keeps it out of shell history and process lists.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

import httpx


def analyses(value: str) -> list[str]:
    """Return a normalized comma-separated capability list."""
    return [item.strip() for item in value.split(",") if item.strip()]


def deliver_media(client: httpx.Client, args: argparse.Namespace) -> dict[str, Any]:
    """Send one exported binary object."""
    path = Path(args.file)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = {
        "external_object_id": args.object_id,
        "external_object_type": args.object_type,
        "source_system": args.source_system,
        "classification": args.classification,
        "requested_analyses": json.dumps(analyses(args.analyses)),
    }
    for field in ("source_url", "collected_at", "published_at", "collector_version"):
        value = getattr(args, field)
        if value:
            data[field] = value
    with path.open("rb") as handle:
        response = client.post(
            "/api/v1/integrations/blackglass/media",
            data=data,
            files={"file": (path.name, handle, content_type)},
        )
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def deliver_text(client: httpx.Client, args: argparse.Namespace) -> dict[str, Any]:
    """Send one text object from a file or literal value."""
    text = (
        Path(args.text_file).read_text(encoding="utf-8")
        if args.text_file
        else args.text
    )
    source = {
        "system": args.source_system,
        "object_type": args.object_type,
        "object_id": args.object_id,
        "source_url": args.source_url,
        "collected_at": args.collected_at,
        "published_at": args.published_at,
        "collector_version": args.collector_version,
    }
    response = client.post(
        "/api/v1/integrations/blackglass/text",
        json={
            "schema_version": "1.0",
            "source": {
                key: value for key, value in source.items() if value is not None
            },
            "title": args.title,
            "text": text,
            "language_hint": args.language_hint,
            "attributes": {},
            "requested_analyses": analyses(args.analyses),
        },
    )
    response.raise_for_status()
    return response.json()  # type: ignore[no-any-return]


def parser() -> argparse.ArgumentParser:
    """Build the explicit media/text delivery CLI."""
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--eagleeye-url", required=True)
    root.add_argument("--source-system", default="blackglass-prod")
    root.add_argument("--timeout", type=float, default=120)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--object-id", required=True)
    common.add_argument("--object-type", required=True)
    common.add_argument("--source-url")
    common.add_argument("--collected-at")
    common.add_argument("--published-at")
    common.add_argument("--collector-version", default="blackglass-export/1")

    commands = root.add_subparsers(dest="kind", required=True)
    media = commands.add_parser("media", parents=[common])
    media.add_argument("--file", required=True)
    media.add_argument(
        "--classification",
        choices=("public", "internal", "restricted", "biometric"),
        default="internal",
    )
    media.add_argument(
        "--analyses",
        default="",
        help="Comma-separated overrides; omit for MIME-appropriate EagleEye defaults",
    )

    text = commands.add_parser("text", parents=[common])
    text_source = text.add_mutually_exclusive_group(required=True)
    text_source.add_argument("--text")
    text_source.add_argument("--text-file")
    text.add_argument("--title", required=True)
    text.add_argument("--language-hint")
    text.add_argument("--analyses", default="semantic_embedding")
    return root


def main() -> int:
    """Deliver one object and print its machine-readable acknowledgement."""
    args = parser().parse_args()
    token = os.environ.get("EAGLEEYE_TOKEN")
    if not token:
        print("EAGLEEYE_TOKEN is required", file=sys.stderr)
        return 2
    try:
        with httpx.Client(
            base_url=args.eagleeye_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=args.timeout,
        ) as client:
            result = (
                deliver_media(client, args)
                if args.kind == "media"
                else deliver_text(client, args)
            )
    except (OSError, httpx.HTTPError) as exc:
        print(f"delivery failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
