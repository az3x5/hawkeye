"""Retention of submitted query images.

Identification retains the image it was given so a reviewer can see what was
actually submitted. Keeping it forever is a different decision, and not one
this system should make silently: images expire, while the identification
record and its content hash remain, so the decision stays auditable after the
biometric material is gone.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.connectors.postgres.tables import face_samples, identifications
from app.domain.audit import SYSTEM_ACTOR, AuditAction, AuditEvent, AuditLog
from app.domain.jobs import ObjectStore

logger = logging.getLogger(__name__)


async def purge_expired_query_images(
    *,
    session: object,
    objects: ObjectStore,
    audit: AuditLog,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    """Delete query images older than the retention period.

    An image is only removed when no *enrolled sample* shares its content hash:
    an enrolled face is held under a different policy and must not be deleted
    because someone happened to identify against the same picture.

    Returns the number of objects removed.
    """
    if retention_days < 1:
        raise ValueError(f"retention_days must be at least 1, got {retention_days}")

    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    expired = await session.execute(  # type: ignore[attr-defined]
        select(identifications.c.identification_uuid, identifications.c.query_sha256).where(
            identifications.c.created_at < cutoff
        )
    )
    rows = expired.all()
    if not rows:
        return 0

    enrolled = await session.execute(  # type: ignore[attr-defined]
        select(face_samples.c.image_sha256).where(
            face_samples.c.image_sha256.in_([row.query_sha256 for row in rows])
        )
    )
    protected = {row.image_sha256 for row in enrolled.all()}

    removed = 0
    for row in rows:
        if row.query_sha256 in protected:
            continue
        if await objects.delete(row.query_sha256):
            removed += 1
            await audit.record(
                AuditEvent(
                    action=AuditAction.QUERY_IMAGE_PURGED,
                    actor=SYSTEM_ACTOR,
                    identification_uuid=row.identification_uuid,
                    details={
                        "query_sha256": row.query_sha256,
                        "retention_days": retention_days,
                        "cutoff": cutoff.isoformat(),
                    },
                )
            )

    if removed:
        logger.info(
            "purged expired query images",
            extra={"removed": removed, "retention_days": retention_days},
        )
    return removed
