"""Readiness probe registry.

Components register an async probe at startup; the readiness endpoint runs
all of them. The registry starts empty, so an empty ``checks`` list means
"nothing has been wired up yet", not "everything is healthy".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

Probe = Callable[[], Awaitable[None]]


class DependencyStatus(BaseModel):
    """Outcome of a single dependency probe."""

    name: str = Field(description="Dependency identifier, e.g. ``postgres``.")
    healthy: bool = Field(description="True when the probe completed without error.")
    error: str | None = Field(default=None, description="Failure reason when ``healthy`` is false.")


_probes: dict[str, Probe] = {}


def register_probe(name: str, probe: Probe) -> None:
    """Register (or replace) the readiness probe for ``name``."""
    _probes[name] = probe


def clear_probes() -> None:
    """Drop all registered probes. Intended for tests and shutdown."""
    _probes.clear()


async def _run(name: str, probe: Probe) -> DependencyStatus:
    try:
        await probe()
    except Exception as exc:  # noqa: BLE001 - logged and surfaced in the response
        logger.warning("readiness probe failed", extra={"dependency": name}, exc_info=exc)
        return DependencyStatus(name=name, healthy=False, error=f"{type(exc).__name__}: {exc}")
    return DependencyStatus(name=name, healthy=True)


async def check_readiness() -> list[DependencyStatus]:
    """Run every registered probe concurrently, in registration order."""
    items = list(_probes.items())
    results = await asyncio.gather(*(_run(name, probe) for name, probe in items))
    return list(results)
