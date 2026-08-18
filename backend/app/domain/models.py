"""Core domain entities.

Deliberately free of any persistence or HTTP concern: these types know the
rules of the domain, not how they are stored or transported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DomainValidationError(ValueError):
    """A domain invariant was violated."""


def _require_text(value: str, field_name: str, *, max_length: int) -> str:
    """Normalise and validate a required free-text attribute."""
    cleaned = value.strip()
    if not cleaned:
        raise DomainValidationError(f"{field_name} must not be empty")
    if len(cleaned) > max_length:
        raise DomainValidationError(f"{field_name} must be at most {max_length} characters")
    return cleaned


class ExternalIdentifierKind(StrEnum):
    """The external identifier namespaces this platform accepts.

    Upstream systems supply ``id`` and ``local_id``. Neither is ever used as an
    internal key; both are attributes of a person, scoped by their source.
    """

    ID = "id"
    LOCAL_ID = "local_id"


@dataclass(frozen=True, slots=True)
class ExternalIdentifier:
    """An identifier owned by an upstream system, scoped by that system.

    ``source`` is what makes the identifier meaningful: the same ``value`` from
    two different systems denotes two different things. Uniqueness is therefore
    always over ``(source, kind, value)``, never over ``value`` alone.
    """

    source: str
    kind: ExternalIdentifierKind
    value: str

    def __post_init__(self) -> None:
        """Normalise and validate the identifier's parts."""
        object.__setattr__(self, "source", _require_text(self.source, "source", max_length=128))
        object.__setattr__(self, "value", _require_text(self.value, "value", max_length=256))
        if not isinstance(self.kind, ExternalIdentifierKind):
            raise DomainValidationError(
                f"kind must be an ExternalIdentifierKind, got {self.kind!r}"
            )

    def __str__(self) -> str:
        """Render as ``source:kind:value`` for logs and error messages."""
        return f"{self.source}:{self.kind.value}:{self.value}"


@dataclass(frozen=True, slots=True)
class Person:
    """A person. ``person_uuid`` is the sole internal key.

    The value is generated here and never derived from external input, so that
    upstream systems reissuing or re-scoping their identifiers can never
    invalidate our keys or our foreign keys.
    """

    person_uuid: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True, slots=True)
class FaceSample:
    """One face capture belonging to a person.

    A person has many samples — different captures, poses, ages and sources.
    ``image_sha256`` is the content hash of the source image; it makes repeated
    submission of the same capture detectable without comparing image bytes.
    """

    person_uuid: UUID
    image_sha256: str
    source: str
    face_sample_uuid: UUID = field(default_factory=uuid4)
    captured_at: datetime | None = None
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        """Normalise the content hash and validate the sample's attributes."""
        digest = self.image_sha256.strip().lower()
        if not _SHA256_RE.match(digest):
            raise DomainValidationError("image_sha256 must be 64 lowercase hexadecimal characters")
        object.__setattr__(self, "image_sha256", digest)
        object.__setattr__(self, "source", _require_text(self.source, "source", max_length=128))
        if self.captured_at is not None and self.captured_at.tzinfo is None:
            raise DomainValidationError("captured_at must be timezone-aware")
