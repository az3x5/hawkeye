"""Domain invariants. No database, no HTTP."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.models import (
    DomainValidationError,
    ExternalIdentifier,
    ExternalIdentifierKind,
    FaceSample,
    Person,
)

DIGEST = "a" * 64


def test_person_uuid_is_generated_and_unique() -> None:
    first, second = Person(), Person()
    assert isinstance(first.person_uuid, UUID)
    assert first.person_uuid != second.person_uuid


def test_external_identifier_is_scoped_by_source() -> None:
    same_value_two_sources = (
        ExternalIdentifier(source="crm", kind=ExternalIdentifierKind.ID, value="42"),
        ExternalIdentifier(source="hr", kind=ExternalIdentifierKind.ID, value="42"),
    )
    assert same_value_two_sources[0] != same_value_two_sources[1]


def test_external_identifier_kinds_are_id_and_local_id() -> None:
    assert {k.value for k in ExternalIdentifierKind} == {"id", "local_id"}


@pytest.mark.parametrize("blank", ["", "   "])
def test_external_identifier_rejects_blank_parts(blank: str) -> None:
    with pytest.raises(DomainValidationError):
        ExternalIdentifier(source=blank, kind=ExternalIdentifierKind.ID, value="42")
    with pytest.raises(DomainValidationError):
        ExternalIdentifier(source="crm", kind=ExternalIdentifierKind.ID, value=blank)


def test_external_identifier_trims_whitespace() -> None:
    identifier = ExternalIdentifier(
        source="  crm ", kind=ExternalIdentifierKind.LOCAL_ID, value=" 42  "
    )
    assert (identifier.source, identifier.value) == ("crm", "42")


def test_external_identifier_rejects_a_foreign_kind() -> None:
    with pytest.raises(DomainValidationError):
        ExternalIdentifier(source="crm", kind="id", value="42")  # type: ignore[arg-type]


def test_a_person_can_hold_many_face_samples() -> None:
    person = Person()
    samples = [
        FaceSample(person_uuid=person.person_uuid, image_sha256=d * 64, source="upload")
        for d in "abc"
    ]
    assert len({s.face_sample_uuid for s in samples}) == 3
    assert {s.person_uuid for s in samples} == {person.person_uuid}


def test_face_sample_normalises_its_content_hash() -> None:
    sample = FaceSample(person_uuid=uuid4(), image_sha256="A" * 64, source="upload")
    assert sample.image_sha256 == "a" * 64


@pytest.mark.parametrize("bad", ["", "abc", "z" * 64, "a" * 63, "a" * 65])
def test_face_sample_rejects_a_malformed_content_hash(bad: str) -> None:
    with pytest.raises(DomainValidationError):
        FaceSample(person_uuid=uuid4(), image_sha256=bad, source="upload")


def test_face_sample_requires_timezone_aware_capture_time() -> None:
    with pytest.raises(DomainValidationError):
        FaceSample(
            person_uuid=uuid4(),
            image_sha256=DIGEST,
            source="upload",
            captured_at=datetime(2026, 1, 1),  # noqa: DTZ001 - the point of the test
        )
    aware = FaceSample(
        person_uuid=uuid4(),
        image_sha256=DIGEST,
        source="upload",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert aware.captured_at is not None
