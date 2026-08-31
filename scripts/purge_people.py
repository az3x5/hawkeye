#!/usr/bin/env python3
"""Erase every Hawkeye person through the audited HTTP API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor


class ApiError(Exception):
    """A Hawkeye request failed after retries."""


def request(url: str, token: str, *, method: str = "GET") -> dict:
    """Send one authenticated request and retry transient responses."""
    for attempt in range(7):
        call = urllib.request.Request(
            url,
            method=method,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(call, timeout=120) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            raw = error.read()
            if method == "DELETE" and error.code == 404:
                return {}
            if error.code not in {429, 500, 502, 503, 504} or attempt == 6:
                raise ApiError(
                    f"HTTP {error.code}: {raw.decode(errors='replace')[:200]}"
                ) from None
            retry_after = error.headers.get("Retry-After")
            delay = (
                int(retry_after)
                if retry_after and retry_after.isdigit()
                else min(2**attempt, 30)
            )
        except urllib.error.URLError as error:
            if attempt == 6:
                raise ApiError(str(error.reason)) from None
            delay = min(2**attempt, 30)
        time.sleep(delay)
    raise ApiError("request remained unavailable")


def people(api: str, token: str) -> list[str]:
    """Read the first page repeatedly because deletion shrinks the collection."""
    result = request(f"{api.rstrip('/')}/api/v1/persons?limit=200&offset=0", token)
    return [str(item["person_uuid"]) for item in result["items"]]


def erase(api: str, token: str, person_uuid: str, reason: str) -> None:
    """Erase one person and all biometric material attributed to them."""
    encoded_reason = urllib.parse.urlencode({"reason": reason})
    request(
        f"{api.rstrip('/')}/api/v1/persons/{person_uuid}?{encoded_reason}",
        token,
        method="DELETE",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=os.environ.get("HAWKEYE_API"))
    parser.add_argument("--token", default=os.environ.get("HAWKEYE_TOKEN"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--reason", default="full Dheni all-photo re-enrolment")
    args = parser.parse_args()
    if not args.api or not args.token:
        parser.error("HAWKEYE_API and HAWKEYE_TOKEN are required")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    erased = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while batch := people(args.api, args.token):
            try:
                list(
                    pool.map(
                        lambda person: erase(args.api, args.token, person, args.reason),
                        batch,
                    )
                )
            except ApiError as error:
                print(f"erasure stopped after {erased}: {error}", file=sys.stderr)
                return 1
            erased += len(batch)
            print(f"erased {erased}", flush=True)
    print(f"erasure complete: {erased} people")
    return 0


if __name__ == "__main__":
    sys.exit(main())
