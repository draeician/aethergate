"""Typed, environment-driven runtime configuration for AetherGate v2.

Database URLs and passwords are treated as secrets: they are stored as
``SecretStr`` so their values are masked in ``repr`` and logs.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["dev", "test", "prod"]

# Minimum configured bootstrap-token length. This is only a floor against
# trivially short/guessable operator secrets, NOT a proof of entropy; operators
# must still supply randomly generated high-entropy material (the dev helper
# uses ``secrets.token_urlsafe(32)``).
MIN_BOOTSTRAP_TOKEN_LENGTH = 32


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

    # Development-only inference auth bypass. Must never be enabled in prod.
    allow_inference_auth_bypass: bool = Field(
        default=False, validation_alias="AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS"
    )
    # One-use bootstrap secret for the initial admin bootstrap endpoint. No
    # default; an empty/unset value disables bootstrap, and placeholder values
    # are rejected so a weak or forgotten value can never silently enable
    # bootstrap with a guessable secret. Never logged.
    bootstrap_token: SecretStr | None = Field(
        default=None, validation_alias="AETHERGATE_BOOTSTRAP_TOKEN"
    )
    # Comma-separated upstream host allowlist for the egress destination guard.
    upstream_allowlist: str = Field(
        default="", validation_alias="AETHERGATE_UPSTREAM_ALLOWLIST"
    )
    inference_timeout_seconds: float = Field(
        default=120.0, validation_alias="AETHERGATE_INFERENCE_TIMEOUT_SECONDS"
    )

    # OIDC human identity (admin browser sessions).
    oidc_enabled: bool = Field(default=False, validation_alias="AETHERGATE_OIDC_ENABLED")
    oidc_issuer: str | None = Field(default=None, validation_alias="AETHERGATE_OIDC_ISSUER")
    oidc_client_id: str | None = Field(default=None, validation_alias="AETHERGATE_OIDC_CLIENT_ID")
    oidc_client_secret: SecretStr | None = Field(
        default=None, validation_alias="AETHERGATE_OIDC_CLIENT_SECRET"
    )
    oidc_redirect_uri: str | None = Field(
        default=None, validation_alias="AETHERGATE_OIDC_REDIRECT_URI"
    )
    oidc_scopes: str = Field(default="openid", validation_alias="AETHERGATE_OIDC_SCOPES")
    oidc_provider_name: str = Field(
        default="OIDC", validation_alias="AETHERGATE_OIDC_PROVIDER_NAME"
    )
    # Just-in-time provisioning of unknown external identities into a principal.
    oidc_jit_provisioning: bool = Field(
        default=False, validation_alias="AETHERGATE_OIDC_JIT_PROVISIONING"
    )
    oidc_jit_project_name: str = Field(
        default="default", validation_alias="AETHERGATE_OIDC_JIT_PROJECT_NAME"
    )
    oidc_session_idle_seconds: int = Field(
        default=3600, validation_alias="AETHERGATE_OIDC_SESSION_IDLE_SECONDS"
    )
    oidc_session_absolute_seconds: int = Field(
        default=43200, validation_alias="AETHERGATE_OIDC_SESSION_ABSOLUTE_SECONDS"
    )
    oidc_login_ttl_seconds: int = Field(
        default=600, validation_alias="AETHERGATE_OIDC_LOGIN_TTL_SECONDS"
    )
    # Web-console callback completion mode. When set (e.g. ``/auth/callback``),
    # a successful OIDC callback issues the HttpOnly session cookie plus a separate
    # JS-readable CSRF cookie and 302-redirects to this fixed relative path instead
    # of returning JSON. The path is server-configured (never user-controlled), so
    # it cannot be used as an open redirect. Unset/empty keeps the JSON-compatible
    # callback contract (CLI/tests).
    oidc_web_callback_path: str | None = Field(
        default=None, validation_alias="AETHERGATE_OIDC_WEB_CALLBACK_PATH"
    )

    # OAuth device flow (public CLI client) + durable human CLI sessions.
    # The device client is public and MUST have no embedded client secret; it is
    # distinct from the browser confidential client (``oidc_client_id``/secret).
    oidc_device_client_id: str | None = Field(
        default=None, validation_alias="AETHERGATE_OIDC_DEVICE_CLIENT_ID"
    )
    oidc_device_scopes: str = Field(
        default="openid", validation_alias="AETHERGATE_OIDC_DEVICE_SCOPES"
    )
    cli_session_ttl_seconds: int = Field(
        default=43200, validation_alias="AETHERGATE_CLI_SESSION_TTL_SECONDS"
    )

    # Scheduler queue encryption key (Fernet). Outside PostgreSQL, never committed.
    queue_key: SecretStr | None = Field(default=None, validation_alias="AETHERGATE_QUEUE_KEY")
    # Scheduler bounds / timing.
    queue_max_requests: int = Field(
        default=10000, validation_alias="AETHERGATE_QUEUE_MAX_REQUESTS"
    )
    queue_max_wait_seconds: float = Field(
        default=300.0, validation_alias="AETHERGATE_QUEUE_MAX_WAIT_SECONDS"
    )
    queue_total_lifetime_seconds: float = Field(
        default=600.0, validation_alias="AETHERGATE_QUEUE_TOTAL_LIFETIME_SECONDS"
    )
    scheduler_poll_interval_seconds: float = Field(
        default=0.2, validation_alias="AETHERGATE_SCHEDULER_POLL_INTERVAL_SECONDS"
    )
    worker_lease_seconds: float = Field(
        default=120.0, validation_alias="AETHERGATE_WORKER_LEASE_SECONDS"
    )
    worker_heartbeat_seconds: float = Field(
        default=30.0, validation_alias="AETHERGATE_WORKER_HEARTBEAT_SECONDS"
    )
    # Default shared-quota-group cooldown when a provider returns 429 without an
    # explicit Retry-After value. Applied only when the route carries a group.
    provider_429_cooldown_seconds: float = Field(
        default=60.0, validation_alias="AETHERGATE_PROVIDER_429_COOLDOWN_SECONDS"
    )

    @property
    def upstream_allowlist_hosts(self) -> set[str]:
        """Parsed, normalized set of allowlisted upstream hostnames."""
        return {
            host.strip().lower()
            for host in self.upstream_allowlist.split(",")
            if host.strip()
        }

    @property
    def oidc_scope_list(self) -> list[str]:
        """Parsed, deduplicated requested OIDC scopes (``openid`` always first).

        Accepts comma- and/or whitespace-separated input so both ``"openid,profile"``
        and ``"openid profile email"`` work.
        """
        import re

        scopes = [s for s in re.split(r"[\s,]+", self.oidc_scopes) if s]
        if "openid" not in scopes:
            scopes.insert(0, "openid")
        return list(dict.fromkeys(scopes))

    @property
    def oidc_device_scope_list(self) -> list[str]:
        """Parsed, deduplicated device-flow scopes (``openid`` always first)."""
        import re

        scopes = [s for s in re.split(r"[\s,]+", self.oidc_device_scopes) if s]
        if "openid" not in scopes:
            scopes.insert(0, "openid")
        return list(dict.fromkeys(scopes))

    @property
    def device_flow_enabled(self) -> bool:
        """True when a public device client is configured (device flow available)."""
        return bool(self.oidc_enabled and self.oidc_device_client_id)

    @property
    def web_console_enabled(self) -> bool:
        """True when the OIDC callback completes in web-console (redirect) mode."""
        return bool(self.oidc_enabled and self.oidc_web_callback_path)

    @field_validator(
        "oidc_issuer",
        "oidc_client_id",
        "oidc_redirect_uri",
        "oidc_client_secret",
        "oidc_device_client_id",
        "oidc_web_callback_path",
        mode="before",
    )
    @classmethod
    def _empty_oidc_is_none(cls, value: object) -> object:
        """Treat an empty env value (e.g. ``${VAR:-}`` from compose) as unset."""
        if value is None or value == "":
            return None
        return value

    @model_validator(mode="after")
    def _validate_oidc(self) -> Settings:
        if not self.oidc_enabled:
            return self
        missing = [
            name
            for name, value in (
                ("AETHERGATE_OIDC_ISSUER", self.oidc_issuer),
                ("AETHERGATE_OIDC_CLIENT_ID", self.oidc_client_id),
                ("AETHERGATE_OIDC_REDIRECT_URI", self.oidc_redirect_uri),
            )
            if value is None
        ]
        if missing:
            raise ValueError("oidc enabled but missing " + ", ".join(missing))
        if self.oidc_client_secret is not None and not self.oidc_client_secret.get_secret_value():
            raise ValueError("oidc_client_secret must not be empty")
        if self.oidc_session_absolute_seconds <= self.oidc_session_idle_seconds:
            raise ValueError(
                "oidc_session_absolute_seconds must exceed oidc_session_idle_seconds"
            )
        if self.oidc_login_ttl_seconds <= 0:
            raise ValueError("oidc_login_ttl_seconds must be positive")
        if self.cli_session_ttl_seconds <= 0:
            raise ValueError("cli_session_ttl_seconds must be positive")
        if self.oidc_web_callback_path is not None:
            path = self.oidc_web_callback_path
            if (
                not path.startswith("/")
                or path.startswith("//")
                or "?" in path
                or "#" in path
                or "\\" in path
                or "://" in path
            ):
                raise ValueError(
                    "oidc_web_callback_path must be a fixed relative path (e.g. /auth/callback)"
                )
        # An HTTP issuer is allowed only in dev/test; production requires HTTPS.
        if self.app_env == "prod" and self.oidc_issuer is not None:
            if not self.oidc_issuer.startswith("https://"):
                raise ValueError("oidc_issuer must use https:// in production mode")
        return self

    @field_validator("bootstrap_token", mode="after")
    @classmethod
    def _validate_bootstrap_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        placeholder = {"changeme", "change-me", "replaceme", "replace-me", "bootstrap"}
        if raw.lower() in placeholder:
            raise ValueError("bootstrap_token must not be a placeholder")
        if not raw:
            return None
        if len(raw) < MIN_BOOTSTRAP_TOKEN_LENGTH:
            raise ValueError(
                "bootstrap_token must be at least "
                f"{MIN_BOOTSTRAP_TOKEN_LENGTH} characters"
            )
        return value

    @model_validator(mode="after")
    def _forbid_insecure_auth_bypass_in_prod(self) -> Settings:
        if self.app_env == "prod" and self.allow_inference_auth_bypass:
            raise ValueError(
                "allow_inference_auth_bypass cannot be enabled in production mode"
            )
        return self

    @model_validator(mode="after")
    def _require_queue_key_in_prod(self) -> Settings:
        if self.app_env == "prod" and self.queue_key is None:
            raise ValueError("queue_key (AETHERGATE_QUEUE_KEY) is required in production mode")
        return self

    @model_validator(mode="after")
    def _validate_scheduler_timing(self) -> Settings:
        if self.worker_lease_seconds <= 0:
            raise ValueError("worker_lease_seconds must be positive")
        if self.worker_heartbeat_seconds <= 0:
            raise ValueError("worker_heartbeat_seconds must be positive")
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds:
            raise ValueError(
                "worker_heartbeat_seconds must be less than worker_lease_seconds "
                "so a healthy worker renews its lease before it expires"
            )
        if self.queue_max_wait_seconds <= 0:
            raise ValueError("queue_max_wait_seconds must be positive")
        if self.queue_total_lifetime_seconds <= 0:
            raise ValueError("queue_total_lifetime_seconds must be positive")
        return self

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
