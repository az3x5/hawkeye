"""Table definitions for the metadata store.

Core-style tables rather than declarative mappings: the domain entities stay
plain dataclasses with no ORM base class, and the repository layer does the
translation explicitly.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID

metadata = MetaData()

persons = Table(
    "persons",
    metadata,
    Column("person_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    comment="People. person_uuid is the sole internal key.",
)

person_external_identifiers = Table(
    "person_external_identifiers",
    metadata,
    Column(
        "person_uuid",
        PgUUID(as_uuid=True),
        ForeignKey("persons.person_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("source", String(128), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("value", String(256), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    # An external identifier is unique within its source, and denotes exactly
    # one person there. It is never a primary key and never a foreign key.
    UniqueConstraint("source", "kind", "value", name="uq_external_identifier_scope"),
    CheckConstraint("kind IN ('id', 'local_id')", name="ck_external_identifier_kind"),
    Index("ix_external_identifiers_person", "person_uuid"),
    comment="Upstream identifiers (id, local_id) as source-scoped attributes of a person.",
)

face_samples = Table(
    "face_samples",
    metadata,
    Column("face_sample_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "person_uuid",
        PgUUID(as_uuid=True),
        ForeignKey("persons.person_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("image_sha256", String(64), nullable=False),
    Column("source", String(128), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    # Many samples per person is the norm; the same *image* twice is not.
    UniqueConstraint("person_uuid", "image_sha256", name="uq_face_sample_person_content"),
    CheckConstraint("image_sha256 ~ '^[0-9a-f]{64}$'", name="ck_face_sample_sha256"),
    Index("ix_face_samples_person", "person_uuid"),
    comment="Face captures. A person has many; no sample is privileged over another.",
)

face_embeddings = Table(
    "face_embeddings",
    metadata,
    Column("embedding_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column(
        "face_sample_uuid",
        PgUUID(as_uuid=True),
        ForeignKey("face_samples.face_sample_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("model_name", String(128), nullable=False),
    Column("model_version", String(128), nullable=False),
    Column("preprocessing_version", String(128), nullable=False),
    Column("vector_collection", String(255), nullable=False),
    Column("dimension", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    # One embedding per sample per provenance. Re-embedding under a new model
    # version adds a row rather than overwriting the old one, so a migration
    # between models is observable instead of destructive.
    UniqueConstraint(
        "face_sample_uuid",
        "model_name",
        "model_version",
        "preprocessing_version",
        name="uq_face_embedding_sample_provenance",
    ),
    Index("ix_face_embeddings_collection", "vector_collection"),
    CheckConstraint("dimension > 0", name="ck_face_embedding_dimension"),
    comment=(
        "Metadata for embeddings held in the vector store. The vectors "
        "themselves live in Qdrant; this table records what produced them."
    ),
)
