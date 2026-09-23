"""Contracts for the OAuth-backed Dheni photo importer."""

from __future__ import annotations

import json
import sys
from email.message import Message
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import import_dheni  # noqa: E402


class Response:
    """Small urllib response double with headers and context management."""

    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self, limit: int | None = None) -> bytes:
        return self.body if limit is None else self.body[:limit]

    def __enter__(self) -> Response:
        """Return this response from the test context manager."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the no-op test context manager."""
        return None


def test_oauth_photo_source_caches_token_and_fetches_specific_photo() -> None:
    requests = []
    responses = iter(
        [
            Response(
                json.dumps(
                    {
                        "access_token": "test-token",
                        "token_type": "Bearer",
                        "expires_in": 900,
                    }
                ).encode()
            ),
            Response(
                json.dumps(
                    {
                        "count": 1,
                        "photos": [
                            {
                                "id": 42,
                                "file": {"realname": "portrait.png"},
                            }
                        ],
                    }
                ).encode()
            ),
            Response(b"image-bytes", "image/png"),
        ]
    )

    def fake_open(request: object) -> Response:
        requests.append(request)
        return next(responses)

    tokens = import_dheni.OAuthTokenProvider(
        "https://identity.test/token", "client", "secret", "minio:person-photos", "minio"
    )
    source = import_dheni.OAuthPhotoSource("https://photos.test/api", tokens)

    with patch.object(import_dheni, "_open_with_retry", side_effect=fake_open):
        photos = source.list_photos("A/B")
        content, filename, content_type = source.download_photo("A/B", photos[0], 1024)

    assert len([request for request in requests if request.get_method() == "POST"]) == 1
    assert requests[1].full_url.endswith("/v2/person/A%2FB/photos?format=metadata")
    assert requests[2].full_url.endswith("/v2/person/A%2FB/photos/42")
    assert requests[1].get_header("Authorization") == "Bearer test-token"
    assert requests[2].get_header("Authorization") == "Bearer test-token"
    assert (content, filename, content_type) == (b"image-bytes", "dheni-photo.png", "image/png")


def test_oauth_photo_source_refreshes_once_after_unauthorized() -> None:
    requests = []
    outcomes = iter(
        [
            Response(b'{"access_token":"first","expires_in":900}'),
            import_dheni.DheniError(401, "expired"),
            Response(b'{"access_token":"second","expires_in":900}'),
            Response(b'{"count":0,"photos":[]}'),
        ]
    )

    def fake_open(request: object) -> Response:
        requests.append(request)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    tokens = import_dheni.OAuthTokenProvider(
        "https://identity.test/token", "client", "secret", "minio:person-photos", "minio"
    )
    source = import_dheni.OAuthPhotoSource("https://photos.test/api", tokens)

    with patch.object(import_dheni, "_open_with_retry", side_effect=fake_open):
        assert source.list_photos("123") == []

    token_requests = [request for request in requests if request.get_method() == "POST"]
    assert len(token_requests) == 2
    assert requests[-1].get_header("Authorization") == "Bearer second"
