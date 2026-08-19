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
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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
    Column("processing_state", String(32), nullable=False, server_default=text("'pending'")),
    Column("processed_at", DateTime(timezone=True), nullable=True),
    Column("failure_reason", Text(), nullable=True),
    CheckConstraint(
        "processing_state IN ('pending', 'processed', 'failed')",
        name="ck_face_sample_processing_state",
    ),
    Index("ix_face_samples_processing_state", "processing_state"),
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


identifications = Table(
    "identifications",
    metadata,
    Column("identification_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column("query_sha256", String(64), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("policy_version", String(128), nullable=False),
    Column("accept_at", Float, nullable=False),
    Column("review_at", Float, nullable=False),
    # Deliberately no foreign key, for the same reason audit_events has none:
    # an identification is a historical record of a decision and must survive
    # the deletion of the person it named. The constraint also turned a
    # candidate the vector store still held, but the metadata store had
    # forgotten, into a 500 at identification time.
    Column("best_person_uuid", PgUUID(as_uuid=True), nullable=True),
    Column("best_score", Float, nullable=True),
    Column("candidates", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("review_outcome", String(16), nullable=True),
    Column("reviewed_by", String(256), nullable=True),
    Column("reviewed_at", DateTime(timezone=True), nullable=True),
    Column("review_note", Text(), nullable=True),
    CheckConstraint("outcome IN ('accept', 'review', 'reject')", name="ck_identification_outcome"),
    CheckConstraint(
        "review_outcome IS NULL OR review_outcome IN ('confirmed', 'rejected')",
        name="ck_identification_review_outcome",
    ),
    # The thresholds in force are stored with the decision, not just referenced.
    # A past decision must stay readable against the rules that produced it,
    # even after the policy changes.
    Index("ix_identifications_outcome", "outcome"),
    comment="Identification attempts, with the decision and the policy that produced it.",
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("audit_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("action", String(64), nullable=False),
    Column("actor_identifier", String(256), nullable=False),
    Column("actor_kind", String(16), nullable=False),
    Column("person_uuid", PgUUID(as_uuid=True), nullable=True),
    Column("face_sample_uuid", PgUUID(as_uuid=True), nullable=True),
    Column("identification_uuid", PgUUID(as_uuid=True), nullable=True),
    Column("policy_version", String(128), nullable=True),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    CheckConstraint("actor_kind IN ('user', 'system')", name="ck_audit_actor_kind"),
    Index("ix_audit_events_person", "person_uuid"),
    Index("ix_audit_events_identification", "identification_uuid"),
    Index("ix_audit_events_occurred_at", "occurred_at"),
    # Deliberately carries no foreign keys: an audit record must survive the
    # deletion of what it describes, or it cannot evidence that deletion.
    comment="Append-only record of administrative and review actions.",
)


api_tokens = Table(
    "api_tokens",
    metadata,
    Column("token_uuid", PgUUID(as_uuid=True), primary_key=True),
    Column("subject", String(256), nullable=False),
    Column("kind", String(16), nullable=False),
    # Only the hash is stored: a disclosure of this table must not hand over
    # working credentials.
    Column("token_sha256", String(64), nullable=False, unique=True),
    Column("scopes", ARRAY(String(32)), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column("disabled_at", DateTime(timezone=True), nullable=True),
    # Set when the credential was minted by a password login, so a password
    # change can revoke every session it produced.
    Column("user_uuid", PgUUID(as_uuid=True), nullable=True),
    # NULL means the credential never expires: permitted, but rare by design.
    Column("expires_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("kind IN ('user', 'service')", name="ck_api_token_kind"),
    Index("ix_api_tokens_subject", "subject"),
    comment="API credentials. Secrets are never stored, only their SHA-256.",
)


users = Table(
    "users",
    metadata,
    Column("user_uuid", PgUUID(as_uuid=True), primary_key=True),
    # Stored already normalised (lowercased), so the unique constraint means
    # what people expect it to mean.
    Column("email", String(320), nullable=False, unique=True),
    # Argon2id. The plaintext is never stored.
    Column("password_hash", String(255), nullable=False),
    Column("scopes", ARRAY(String(32)), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("now()")),
    Column(
        "password_changed_at", DateTime(timezone=True), nullable=False, server_default=text("now()")
    ),
    Column("disabled_at", DateTime(timezone=True), nullable=True),
    Column("last_login_at", DateTime(timezone=True), nullable=True),
    comment="Password accounts. Signing in mints a short-lived api_tokens row.",
)
