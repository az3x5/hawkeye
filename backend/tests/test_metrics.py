"""Contract and collector tests for system resource metrics."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Never
from uuid import uuid4

import pytest

from app.api.v1 import metrics
from app.core.config import Settings
from app.domain.auth import Principal
from app.domain.processing import JobStatus, ProcessingMetrics


def test_memory_usage_reads_available_host_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "meminfo").write_text(
        "MemTotal:       1000 kB\nMemAvailable:    250 kB\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(metrics, "_PROC", tmp_path)

    usage = metrics._memory_usage()

    assert usage.total_bytes == 1000 * 1024
    assert usage.used_bytes == 750 * 1024
    assert usage.percent == 75.0


@pytest.mark.asyncio
async def test_missing_nvidia_tools_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing(*_args: Any, **_kwargs: Any) -> Never:
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing)

    gpus, error = await metrics._gpu_usage()

    assert gpus == []
    assert error == "NVIDIA driver tools are not available"


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_declared_schema(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    response = metrics.SystemMetricsResponse(
        collected_at=datetime.now(UTC),
        cpu_percent=42.5,
        cpu_count=16,
        memory=metrics.CapacityUsage(used_bytes=60, total_bytes=100, percent=60),
        disk=metrics.CapacityUsage(used_bytes=25, total_bytes=100, percent=25),
        gpus=[],
        gpu_error="NVIDIA driver tools are not available",
    )

    async def collect(_settings: Settings) -> metrics.SystemMetricsResponse:
        return response

    monkeypatch.setattr(metrics, "collect_system_metrics", collect)
    principal = Principal(
        token_uuid=uuid4(),
        subject="operator@example.com",
        kind="user",
        scopes=frozenset(),
    )

    processing = ProcessingMetrics(
        by_status={JobStatus.QUEUED: 4, JobStatus.RETRY: 1},
        queue_depth=5,
        oldest_queued_age_seconds=12.5,
        active_leases=2,
        expired_leases=0,
        completed_last_minute=7,
        attempts_last_minute=9,
        live_workers=3,
    )

    result = await metrics.system_metrics(principal, processing)

    body = metrics.SystemMetricsResponse.model_validate(result)
    assert body.cpu_percent == 42.5
    assert body.cpu_count == 16
    assert body.gpus == []
    assert body.processing is not None
    assert body.processing.counts == {"queued": 4, "retry": 1}
    assert body.processing.queue_depth == 5
    assert body.processing.completed_last_minute == 7
    assert body.processing.live_workers == 3
