"""Application settings.

All configuration is read from the environment. Nothing sensitive is
defaulted in code: connection URLs for stateful services are required and
the application refuses to start without them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
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
    service_name: str = "faceid"
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

    @property
    def debug(self) -> bool:
        """True only in local development, where docs and debug logs are on."""
        return self.environment == "local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]
