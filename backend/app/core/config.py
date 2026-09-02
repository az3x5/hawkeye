"""Application settings.

All configuration is read from the environment. Nothing sensitive is
defaulted in code: connection URLs for stateful services are required and
the application refuses to start without them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Face ID service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="FACEID_",
        extra="ignore",
        frozen=True,
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    service_name: str = "hawkeye"
    api_v1_prefix: str = "/api/v1"

    # Stateful dependencies. Required — no in-code fallbacks, no embedded
    # credentials. Qdrant is reachable only on the internal network.
    postgres_dsn: PostgresDsn
    redis_dsn: RedisDsn
    qdrant_url: str = Field(min_length=1)
    qdrant_api_key: str | None = None

    # Source images. Held by the object store so the worker can read what the
    # API wrote; a deployment swaps the connector for S3 or GCS.
    object_store_root: Path = Field(
        default=Path("/srv/objects"),
        description="Directory the filesystem object store writes beneath.",
    )

    # Media object storage. The filesystem backend is the local default and
    # needs no credentials; the s3 backend serves both MinIO locally and S3 in
    # AWS, which is the whole point of the seam. Switching backends does not
    # move existing objects — that is a deliberate migration, not a restart.
    object_store_backend: Literal["filesystem", "s3"] = Field(
        default="filesystem",
        description="Which BlobStore implementation serves media.",
    )
    s3_endpoint_url: str | None = Field(
        default=None,
        description="S3 endpoint. Set for MinIO; leave unset for real AWS S3.",
    )
    s3_region: str = Field(default="us-east-1", min_length=1)
    s3_access_key: str | None = Field(default=None, description="S3 access key id.")
    s3_secret_key: str | None = Field(default=None, description="S3 secret access key.")
    s3_use_path_style: bool = Field(
        default=True,
        description="Path-style addressing. True for MinIO, false for AWS S3.",
    )

    # Upload ceiling for media ingestion. Enforced while reading the request
    # body, so an oversized upload is refused without first being buffered.
    media_max_upload_bytes: int = Field(
        default=256 * 1024 * 1024,
        ge=1024,
        le=8 * 1024 * 1024 * 1024,
        description="Largest single media upload accepted, in bytes.",
    )
    media_page_size_limit: int = Field(
        default=200, ge=1, le=1000, description="Server ceiling on media listing page size."
    )

    # Durable processing policy. PostgreSQL owns state and leases; workers poll
    # briefly so a lost transport notification can never strand work.
    job_lease_seconds: int = Field(
        default=300, ge=30, le=3600, description="Worker lease duration per attempt."
    )
    job_poll_interval_seconds: float = Field(
        default=0.5, ge=0.1, le=30, description="Idle durable-queue polling interval."
    )
    job_max_attempts: int = Field(
        default=3, ge=1, le=20, description="Automatic attempts before dead letter."
    )
    job_retry_base_seconds: int = Field(
        default=5, ge=1, le=3600, description="Initial retry delay."
    )
    job_retry_max_seconds: int = Field(
        default=300, ge=1, le=86400, description="Maximum retry delay."
    )

    # Identity decision policy. Deliberately has NO defaults: a matching
    # threshold that ships as a constant is a hard-coded production threshold,
    # and every deployment must state its own and be able to explain it.
    decision_accept_threshold: float = Field(
        ge=-1.0, le=1.0, description="At or above this similarity, propose a match."
    )
    decision_review_threshold: float = Field(
        ge=-1.0, le=1.0, description="At or above this similarity, ask a human."
    )
    decision_policy_version: str = Field(
        min_length=1,
        description="Name of the threshold policy, recorded with every decision.",
    )
    decision_candidate_limit: int = Field(
        default=10, ge=1, le=100, description="Neighbours fetched per identification."
    )

    # Rate limits, per credential per window. Configuration rather than
    # constants: the right ceiling depends on how a deployment is used.
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_identify: int = Field(
        default=30, ge=1, description="Identifications allowed per credential per window."
    )
    rate_limit_enrol: int = Field(
        default=120, ge=1, description="Enrolments allowed per credential per window."
    )
    rate_limit_sign_in: int = Field(
        default=10,
        ge=1,
        description="Sign-in attempts allowed per email per window.",
    )

    # How long a password sign-in stays valid. Shorter than an issued token,
    # because a browser session is a different risk from a service credential.
    session_lifetime_seconds: int = Field(default=12 * 3600, ge=60)

    # Credential lifetime applied when issuing a token without an explicit one.
    token_lifetime_days: int = Field(
        default=90, ge=1, description="Default lifetime for newly issued credentials."
    )

    # Retention of submitted query images. The identification record and its
    # content hash outlive the image, so a decision stays auditable after the
    # biometric material is gone.
    query_image_retention_days: int = Field(
        default=30, ge=1, description="Days a submitted query image is retained."
    )

    # Face detection. Weights are supplied per environment as a mounted file
    # and verified against a checksum; they are never committed or baked in.
    scrfd_model_path: Path | None = Field(
        default=None, description="Filesystem path to the SCRFD ONNX weights."
    )
    scrfd_model_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Expected SHA-256 of the weights. Loading warns when unset.",
    )
    # Detection thresholds are configuration, not constants: they are policy,
    # they differ per deployment and population, and a value baked into code
    # cannot be reviewed or explained after a contested decision.
    scrfd_score_threshold: float = Field(default=0.5, gt=0.0, le=1.0)
    scrfd_nms_iou_threshold: float = Field(default=0.4, gt=0.0, le=1.0)
    scrfd_input_size: int = Field(default=640, multiple_of=32, ge=32)
    # Execution providers and thread pools are host policy. The CPU provider is
    # the only one guaranteed to exist; a GPU deployment sets this explicitly
    # and is told loudly if the provider is missing from the image.
    scrfd_providers: tuple[str, ...] = Field(
        default=("CPUExecutionProvider",),
        min_length=1,
        description="onnxruntime execution providers, most preferred first.",
    )
    scrfd_intra_op_threads: int | None = Field(
        default=None,
        ge=1,
        description="Threads within one detection op. Unset means every core.",
    )
    scrfd_inter_op_threads: int | None = Field(
        default=None, ge=1, description="Threads across detection ops."
    )

    # Face recognition. Supplied and verified exactly like the detector's.
    # There is deliberately no threshold here: recognition reports similarity
    # scores, and the thresholds that turn scores into identity decisions
    # belong to the identity-decision layer.
    adaface_model_path: Path | None = Field(
        default=None, description="Filesystem path to the AdaFace weights."
    )
    adaface_model_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Expected SHA-256 of the weights. Loading warns when unset.",
    )
    adaface_device: str = Field(default="cpu", min_length=1)
    adaface_batch_size: int = Field(default=16, ge=1, le=256)
    adaface_torch_threads: int | None = Field(
        default=None,
        ge=1,
        description="Torch intra-op threads. Unset means every core on the host.",
    )

    # Language semantics. The model may be a Hugging Face identifier during
    # development or a mounted local directory in a controlled deployment.
    # Its operator-supplied version is written to every indexed document.
    language_embedding_model: str | None = Field(
        default=None,
        description="SentenceTransformers model identifier or local model directory.",
    )
    language_embedding_version: str = Field(default="unconfigured", min_length=1)
    language_embedding_device: str = Field(default="cpu", min_length=1)
    language_embedding_batch_size: int = Field(default=16, ge=1, le=256)
    language_embedding_max_tokens: int = Field(default=512, ge=32, le=8192)

    # Specialist Dhivehi inference is isolated in its own process. The API
    # never imports or loads these large models, which keeps face recognition
    # responsive while a translation or OCR model is swapped into memory.
    dhivehi_ai_url: str | None = Field(
        default=None,
        description="Internal URL of the specialist Dhivehi inference service.",
    )
    dhivehi_ai_timeout_seconds: float = Field(default=300.0, ge=1.0, le=1800.0)
    dhivehi_ai_max_text_chars: int = Field(default=20_000, ge=1, le=100_000)
    dhivehi_ai_max_audio_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    dhivehi_ai_max_image_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)

    @model_validator(mode="after")
    def validate_object_store_backend(self) -> Settings:
        """Refuse to start with an S3 backend that has no credentials.

        Failing here is far kinder than failing on the first upload, when the
        media is already in flight and the caller has no way to tell a
        configuration mistake from an outage.
        """
        if self.object_store_backend == "s3" and not (self.s3_access_key and self.s3_secret_key):
            raise ValueError(
                "object_store_backend='s3' requires FACEID_S3_ACCESS_KEY and FACEID_S3_SECRET_KEY"
            )
        return self

    @model_validator(mode="after")
    def validate_job_retry_window(self) -> Settings:
        """Reject a retry ceiling smaller than the initial delay."""
        if self.job_retry_max_seconds < self.job_retry_base_seconds:
            raise ValueError("job_retry_max_seconds must be at least job_retry_base_seconds")
        return self

    @property
    def debug(self) -> bool:
        """True only in local development, where docs and debug logs are on."""
        return self.environment == "local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]
