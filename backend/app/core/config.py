"""Application settings.

All configuration is read from the environment. Nothing sensitive is
defaulted in code: connection URLs for stateful services are required and
the application refuses to start without them.
"""

from __future__ import annotations

from functools import lru_cache
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

    @property
    def debug(self) -> bool:
        """True only in local development, where docs and debug logs are on."""
        return self.environment == "local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]
