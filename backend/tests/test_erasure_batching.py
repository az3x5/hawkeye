"""Scale regression tests for orphaned-image reconciliation."""

from __future__ import annotations

from typing import Any

from app.services.erasure import (
    ORPHANED_IMAGE_QUERY_BATCH_SIZE,
    _referenced_image_digests,
)


class _Rows:
    def all(self) -> list[Any]:
        return []


class _RecordingSession:
    def __init__(self) -> None:
        self.argument_counts: list[int] = []

    async def execute(self, statement: Any) -> _Rows:
        parameters = statement.compile().params
        values = next(value for value in parameters.values() if isinstance(value, list))
        self.argument_counts.append(len(values))
        return _Rows()


async def test_reference_queries_stay_below_asyncpg_argument_limit() -> None:
    session = _RecordingSession()
    total = ORPHANED_IMAGE_QUERY_BATCH_SIZE * 3 + 7

    referenced = await _referenced_image_digests(  # type: ignore[arg-type]
        session, [f"{index:064x}" for index in range(total)]
    )

    assert referenced == set()
    assert session.argument_counts == [
        ORPHANED_IMAGE_QUERY_BATCH_SIZE,
        ORPHANED_IMAGE_QUERY_BATCH_SIZE,
        ORPHANED_IMAGE_QUERY_BATCH_SIZE,
        ORPHANED_IMAGE_QUERY_BATCH_SIZE,
        ORPHANED_IMAGE_QUERY_BATCH_SIZE,
        ORPHANED_IMAGE_QUERY_BATCH_SIZE,
        7,
        7,
    ]
