"""Unit contracts for durable processing envelopes."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.domain.processing import JobPriority, ProcessingJob, ProcessingJobStore


def _job(**overrides: object) -> ProcessingJob:
    values = {
        "pipeline": "face_embedding",
        "pipeline_version": "1",
        "subject_type": "face_sample",
        "subject_uuid": uuid4(),
        "idempotency_key": str(uuid4()),
        "payload": {"image_sha256": "a" * 64},
    }
    values.update(overrides)
    return ProcessingJob(**values)  # type: ignore[arg-type]


def test_processing_job_has_safe_defaults() -> None:
    job = _job()
    assert job.priority is JobPriority.NORMAL
    assert job.max_attempts == 3
    assert job.attempt_count == 0
    assert job.queued_at.tzinfo is not None


@pytest.mark.parametrize("binary", [b"image", bytearray(b"image"), memoryview(b"image")])
def test_binary_payloads_are_rejected_at_any_depth(binary: object) -> None:
    with pytest.raises(ValueError, match="binary"):
        _job(payload={"nested": [{"content": binary}]})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pipeline", ""),
        ("pipeline_version", " "),
        ("subject_type", ""),
        ("idempotency_key", "\t"),
    ],
)
def test_routing_fields_must_not_be_empty(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        _job(**{field: value})


def test_attempt_budget_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        _job(max_attempts=0)


def test_repository_protocol_is_runtime_checkable() -> None:
    assert hasattr(ProcessingJobStore, "__protocol_attrs__") or getattr(
        ProcessingJobStore, "_is_runtime_protocol", False
    )
