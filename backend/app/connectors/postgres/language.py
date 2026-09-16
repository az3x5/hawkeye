"""PostgreSQL repository for language documents."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.postgres.tables import language_documents
from app.domain.jobs import ProcessingState
from app.domain.language import LanguageDocument, PrimaryScript


def _to_document(row: Row[tuple[object, ...]]) -> LanguageDocument:
    return LanguageDocument(
        document_uuid=row.document_uuid,
        title=row.title,
        source=row.source,
        original_text=row.original_text,
        normalized_text=row.normalized_text,
        primary_script=PrimaryScript(row.primary_script),
        content_sha256=row.content_sha256,
        attributes=dict(row.attributes),
        processing_state=ProcessingState(row.processing_state),
        embedding_model=row.embedding_model,
        embedding_version=row.embedding_version,
        vector_collection=row.vector_collection,
        failure_reason=row.failure_reason,
        created_at=row.created_at,
        processed_at=row.processed_at,
    )


class SqlAlchemyLanguageDocumentRepository:
    """Store searchable documents and their embedding provenance."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to one transaction."""
        self._session = session

    async def add(self, document: LanguageDocument) -> tuple[LanguageDocument, bool]:
        """Insert a document, converging repeated source/content submissions."""
        source_id = str(
            document.attributes.get("source_id")
            or document.attributes.get("platform_object_id")
            or document.content_sha256
        )
        source_type = str(
            document.attributes.get("source_type")
            or document.attributes.get("content_model")
            or "text"
        )
        profile_id = document.attributes.get("profile_id")
        result = await self._session.execute(
            insert(language_documents)
            .values(
                document_uuid=document.document_uuid,
                title=document.title,
                source=document.source,
                source_id=source_id,
                source_type=source_type,
                profile_id=str(profile_id) if profile_id is not None else None,
                original_text=document.original_text,
                normalized_text=document.normalized_text,
                primary_script=document.primary_script.value,
                content_sha256=document.content_sha256,
                attributes=document.attributes,
                processing_state=document.processing_state.value,
                created_at=document.created_at,
            )
            .on_conflict_do_nothing(constraint="uq_language_document_source_content")
        )
        if cast("CursorResult[Any]", result).rowcount > 0:
            return document, True
        existing = await self.find_by_content(document.source, document.content_sha256)
        if existing is None:
            raise RuntimeError("language document conflict could not be resolved")
        return existing, False

    async def commit(self) -> None:
        """Make a new document visible before its queue job is published."""
        await self._session.commit()

    async def get(self, document_uuid: UUID) -> LanguageDocument | None:
        """Return one document."""
        result = await self._session.execute(
            select(language_documents).where(language_documents.c.document_uuid == document_uuid)
        )
        row = result.one_or_none()
        return _to_document(row) if row is not None else None

    async def get_many(self, document_uuids: list[UUID]) -> dict[UUID, LanguageDocument]:
        """Return documents keyed by identifier."""
        if not document_uuids:
            return {}
        result = await self._session.execute(
            select(language_documents).where(language_documents.c.document_uuid.in_(document_uuids))
        )
        documents = [_to_document(row) for row in result.all()]
        return {document.document_uuid: document for document in documents}

    async def find_by_content(self, source: str, digest: str) -> LanguageDocument | None:
        """Return a repeated source/content submission."""
        result = await self._session.execute(
            select(language_documents).where(
                language_documents.c.source == source,
                language_documents.c.content_sha256 == digest,
            )
        )
        row = result.one_or_none()
        return _to_document(row) if row is not None else None

    async def mark_processed(
        self,
        document_uuid: UUID,
        *,
        model: str,
        version: str,
        collection: str,
    ) -> None:
        """Record successful indexing and its exact provenance."""
        await self._set_state(
            document_uuid,
            state=ProcessingState.PROCESSED,
            failure_reason=None,
            embedding_model=model,
            embedding_version=version,
            vector_collection=collection,
        )

    async def mark_failed(self, document_uuid: UUID, reason: str) -> None:
        """Record a terminal processing failure."""
        await self._set_state(
            document_uuid,
            state=ProcessingState.FAILED,
            failure_reason=reason,
        )

    async def _set_state(
        self,
        document_uuid: UUID,
        *,
        state: ProcessingState,
        failure_reason: str | None,
        embedding_model: str | None = None,
        embedding_version: str | None = None,
        vector_collection: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "processing_state": state.value,
            "processed_at": datetime.now(UTC),
            "failure_reason": failure_reason,
        }
        if embedding_model is not None:
            values.update(
                embedding_model=embedding_model,
                embedding_version=embedding_version,
                vector_collection=vector_collection,
            )
        result = await self._session.execute(
            language_documents.update()
            .where(language_documents.c.document_uuid == document_uuid)
            .values(**values)
        )
        if cast("CursorResult[Any]", result).rowcount == 0:
            raise KeyError(f"no language document {document_uuid}")
