"""Typed, environment-driven runtime configuration for AetherGate v2.

Database URLs and passwords are treated as secrets: they are stored as
``SecretStr`` so their values are masked in ``repr`` and logs.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["dev", "test", "prod"]


class Settings(BaseSettings):
    """Runtime settings. Environment variables are read case-insensitively."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_env: AppEnv = Field(default="dev", validation_alias="AETHERGATE_ENV")

    database_url: SecretStr | None = Field(default=None, validation_alias="DATABASE_URL")
    postgres_host: str | None = None
    postgres_port: int = 5432
    postgres_db: str | None = None
    postgres_user: str | None = None
    postgres_password: SecretStr | None = None

    log_level: str = "info"

    @model_validator(mode="after")
    def _require_database_config(self) -> Settings:
        if self.database_url is not None:
            return self
        missing = [
            name
            for name, value in (
                ("POSTGRES_HOST", self.postgres_host),
                ("POSTGRES_DB", self.postgres_db),
                ("POSTGRES_USER", self.postgres_user),
                ("POSTGRES_PASSWORD", self.postgres_password),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                "incomplete database configuration: set DATABASE_URL or "
                + ", ".join(missing)
            )
        return self

    @property
    def resolved_database_url(self) -> str:
        """The async SQLAlchemy URL, assembled from parts if needed."""
        if self.database_url is not None:
            return self.database_url.get_secret_value()
        assert self.postgres_password is not None  # guaranteed by validator
        assert self.postgres_host is not None
        assert self.postgres_db is not None
        assert self.postgres_user is not None
        return (
            f"postgresql+asyncpg://{quote_plus(self.postgres_user)}:"
            f"{quote_plus(self.postgres_password.get_secret_value())}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide settings, constructing them on first use."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
