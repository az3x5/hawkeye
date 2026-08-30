"""Authenticated host-resource metrics for the operations dashboard."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_processing_metrics
from app.api.v1.security import get_principal
from app.core.config import Settings, get_settings
from app.core.errors import ErrorResponse
from app.domain.auth import Principal
from app.domain.processing import ProcessingMetrics

router = APIRouter(tags=["system"])

_PROC = Path("/proc")
_CPU_SAMPLE_SECONDS = 0.1


class CapacityUsage(BaseModel):
    """Used and total capacity in bytes."""

    used_bytes: int = Field(ge=0)
    total_bytes: int = Field(gt=0)
    percent: float = Field(ge=0, le=100)


class GpuUsage(BaseModel):
    """One GPU reported by NVIDIA's management interface."""

    name: str
    utilization_percent: float = Field(ge=0, le=100)
    memory: CapacityUsage


class ProcessingMetricsResponse(BaseModel):
    """Durable job pressure, lease health, and recent throughput."""

    counts: dict[str, int]
    queue_depth: int = Field(ge=0)
    oldest_queued_age_seconds: float | None = Field(default=None, ge=0)
    active_leases: int = Field(ge=0)
    expired_leases: int = Field(ge=0)
    completed_last_minute: int = Field(ge=0)
    attempts_last_minute: int = Field(ge=0)
    live_workers: int = Field(ge=0)


class SystemMetricsResponse(BaseModel):
    """Current resource pressure as seen by the serving host."""

    collected_at: datetime
    cpu_percent: float = Field(ge=0, le=100)
    cpu_count: int = Field(gt=0)
    memory: CapacityUsage
    disk: CapacityUsage
    gpus: list[GpuUsage] = Field(default_factory=list)
    gpu_error: str | None = None
    processing: ProcessingMetricsResponse | None = None


def _percent(used: int, total: int) -> float:
    return round(min(max(used / total * 100, 0.0), 100.0), 1)


def _processing_response(value: ProcessingMetrics) -> ProcessingMetricsResponse:
    """Translate domain metrics without leaking connector-specific types."""
    return ProcessingMetricsResponse(
        counts={status.value: count for status, count in value.by_status.items()},
        queue_depth=value.queue_depth,
        oldest_queued_age_seconds=value.oldest_queued_age_seconds,
        active_leases=value.active_leases,
        expired_leases=value.expired_leases,
        completed_last_minute=value.completed_last_minute,
        attempts_last_minute=value.attempts_last_minute,
        live_workers=value.live_workers,
    )


def _cpu_times() -> tuple[int, int]:
    """Return aggregate CPU total and idle ticks from Linux procfs."""
    fields = (_PROC / "stat").read_text(encoding="utf-8").splitlines()[0].split()
    if not fields or fields[0] != "cpu" or len(fields) < 5:
        raise RuntimeError("/proc/stat has an unexpected shape")
    ticks = [int(value) for value in fields[1:]]
    idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
    return sum(ticks), idle


async def _cpu_usage() -> float:
    before_total, before_idle = _cpu_times()
    await asyncio.sleep(_CPU_SAMPLE_SECONDS)
    after_total, after_idle = _cpu_times()
    elapsed = after_total - before_total
    if elapsed <= 0:
        return 0.0
    busy = elapsed - (after_idle - before_idle)
    return round(min(max(busy / elapsed * 100, 0.0), 100.0), 1)


def _memory_usage() -> CapacityUsage:
    """Read host memory rather than Python-process resident memory."""
    values: dict[str, int] = {}
    for line in (_PROC / "meminfo").read_text(encoding="utf-8").splitlines():
        name, raw = line.split(":", 1)
        fields = raw.split()
        if fields:
            values[name] = int(fields[0]) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0:
        raise RuntimeError("/proc/meminfo does not report total memory")
    used = max(total - available, 0)
    return CapacityUsage(used_bytes=used, total_bytes=total, percent=_percent(used, total))


def _disk_usage(path: Path) -> CapacityUsage:
    usage = shutil.disk_usage(path)
    return CapacityUsage(
        used_bytes=usage.used,
        total_bytes=usage.total,
        percent=_percent(usage.used, usage.total),
    )


async def _gpu_usage() -> tuple[list[GpuUsage], str | None]:
    """Ask NVIDIA's supported management tool; absence is a real status."""
    command = (
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    )
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return [], "NVIDIA driver tools are not available"

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.communicate()
        return [], "NVIDIA metrics timed out"
    if process.returncode != 0:
        reason = stderr.decode(errors="replace").strip().splitlines()
        return [], (reason[0][:160] if reason else "NVIDIA GPU is not available")

    gpus: list[GpuUsage] = []
    try:
        for line in stdout.decode().splitlines():
            name, utilization, used_mib, total_mib = (part.strip() for part in line.rsplit(",", 3))
            used = int(float(used_mib) * 1024 * 1024)
            total = int(float(total_mib) * 1024 * 1024)
            gpus.append(
                GpuUsage(
                    name=name,
                    utilization_percent=float(utilization),
                    memory=CapacityUsage(
                        used_bytes=used,
                        total_bytes=total,
                        percent=_percent(used, total),
                    ),
                )
            )
    except (ValueError, TypeError):
        return [], "NVIDIA metrics have an unexpected format"
    return gpus, None


async def collect_system_metrics(settings: Settings) -> SystemMetricsResponse:
    """Collect independent metrics concurrently where doing so saves latency."""
    cpu_task = asyncio.create_task(_cpu_usage())
    gpu_task = asyncio.create_task(_gpu_usage())
    memory = _memory_usage()
    disk = _disk_usage(settings.object_store_root)
    cpu, (gpus, gpu_error) = await asyncio.gather(cpu_task, gpu_task)
    return SystemMetricsResponse(
        collected_at=datetime.now(UTC),
        cpu_percent=cpu,
        cpu_count=os.cpu_count() or 1,
        memory=memory,
        disk=disk,
        gpus=gpus,
        gpu_error=gpu_error,
    )


@router.get(
    "/system/metrics",
    response_model=SystemMetricsResponse,
    summary="Current host resource usage",
    responses={401: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def system_metrics(
    _principal: Annotated[Principal, Depends(get_principal)],
    processing: Annotated[ProcessingMetrics, Depends(get_processing_metrics)],
) -> SystemMetricsResponse:
    """Report host resources together with durable processing pressure."""
    resources = await collect_system_metrics(get_settings())
    return resources.model_copy(update={"processing": _processing_response(processing)})
