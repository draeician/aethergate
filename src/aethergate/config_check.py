"""Safe configuration preflight for AetherGate v2.

``python -m aethergate.config_check`` parses the selected environment's Settings
and reports whether the deployment configuration is internally valid and
operation-safe, without ever printing secret values.

It is a *syntax/shape* check only: it never contacts a provider endpoint or the
database. Exit code 0 means valid; nonzero means invalid.

Checks:
- database configuration is present and parseable;
- queue encryption key is a valid Fernet key;
- production inference-auth bypass is rejected (defense in depth over Settings);
- OIDC is coherently configured, and in prod the issuer uses https://;
- bootstrap token (when set) has a non-placeholder, sufficient-length shape;
- upstream allowlist is reported (empty = deny-all);
- intentionally disabled optional features produce non-fatal warnings.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from aethergate.config import MIN_BOOTSTRAP_TOKEN_LENGTH, Settings, get_settings
from aethergate.encryption import QueueEncryptor
from aethergate.errors import QueueKeyError


@dataclass
class CheckResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _check_database(settings: Settings, result: CheckResult) -> None:
    try:
        url = settings.resolved_database_url
    except Exception:  # noqa: BLE001 - shape validation only
        result.errors.append("database configuration is missing or unparseable")
        return
    if not url.startswith("postgresql"):
        result.errors.append("database_url must be a postgresql URL")


def _check_queue_key(settings: Settings, result: CheckResult) -> None:
    if settings.queue_key is None:
        if settings.app_env == "prod":
            result.errors.append("queue encryption key is required in prod")
        else:
            result.warnings.append(
                "queue encryption key is unset (required for the scheduler to run)"
            )
        return
    try:
        QueueEncryptor(settings.queue_key.get_secret_value())
    except QueueKeyError:
        result.errors.append("queue encryption key is not a valid Fernet key")


def _check_auth_bypass(settings: Settings, result: CheckResult) -> None:
    if settings.app_env == "prod" and settings.allow_inference_auth_bypass:
        result.errors.append("inference auth bypass must not be enabled in prod")


def _check_oidc(settings: Settings, result: CheckResult) -> None:
    if not settings.oidc_enabled:
        result.warnings.append("OIDC is disabled (human browser/CLI login unavailable)")
        return
    for name, value in (
        ("AETHERGATE_OIDC_ISSUER", settings.oidc_issuer),
        ("AETHERGATE_OIDC_CLIENT_ID", settings.oidc_client_id),
        ("AETHERGATE_OIDC_REDIRECT_URI", settings.oidc_redirect_uri),
    ):
        if not value:
            result.errors.append(f"OIDC enabled but {name} is unset")
    if settings.app_env == "prod" and settings.oidc_issuer and not settings.oidc_issuer.startswith(
        "https://"
    ):
        result.errors.append("OIDC issuer must use https:// in prod")


def _check_bootstrap_token(settings: Settings, result: CheckResult) -> None:
    token = settings.bootstrap_token
    if token is None:
        result.warnings.append("bootstrap token is unset (bootstrap disabled)")
        return
    raw = token.get_secret_value().strip()
    if not raw:
        result.warnings.append("bootstrap token is empty (bootstrap disabled)")
        return
    if len(raw) < MIN_BOOTSTRAP_TOKEN_LENGTH:
        result.errors.append(
            f"bootstrap token must be at least {MIN_BOOTSTRAP_TOKEN_LENGTH} characters"
        )


def _check_allowlist(settings: Settings, result: CheckResult) -> None:
    if not settings.upstream_allowlist_hosts:
        result.warnings.append(
            "upstream allowlist is empty: all outbound provider destinations are denied "
            "(set AETHERGATE_UPSTREAM_ALLOWLIST for a working deployment)"
        )
    elif settings.app_env == "prod" and not settings.egress_require_literal_ip:
        result.warnings.append(
            "prod egress requires literal-IP upstream hosts; DNS-name upstreams are "
            "disabled until transport-level address pinning exists"
        )


def _check_secret_backend(settings: Settings, result: CheckResult) -> None:
    """Report provider-secret backend state without printing any secret material."""
    if settings.secret_backend == "env":
        if settings.app_env == "prod":
            result.errors.append(
                "secret backend 'env' is not permitted in prod "
                "(set AETHERGATE_SECRET_BACKEND=file)"
            )
        else:
            result.warnings.append(
                "secret backend is 'env' (development/test only; not a production backend)"
            )
        return
    directory = settings.secret_directory
    if not directory:
        result.errors.append(
            "secret backend 'file' requires AETHERGATE_SECRET_DIRECTORY"
        )
    elif not os.path.isabs(directory):
        result.errors.append(
            "AETHERGATE_SECRET_DIRECTORY must be an absolute path"
        )
    elif not os.path.isdir(directory):
        result.errors.append(
            "AETHERGATE_SECRET_DIRECTORY does not exist or is not a directory"
        )


def _check_optional_features(settings: Settings, result: CheckResult) -> None:
    if not settings.device_flow_enabled:
        result.warnings.append("OIDC device flow is disabled (Linux CLI login unavailable)")
    if not settings.web_console_enabled:
        result.warnings.append(
            "web-console callback mode is disabled (OIDC callback returns JSON)"
        )
    if settings.oidc_jit_provisioning:
        result.warnings.append(
            "OIDC JIT provisioning is enabled (unknown identities are auto-created)"
        )


def run_checks(settings: Settings) -> CheckResult:
    result = CheckResult(ok=True)
    _check_database(settings, result)
    _check_queue_key(settings, result)
    _check_auth_bypass(settings, result)
    _check_oidc(settings, result)
    _check_bootstrap_token(settings, result)
    _check_allowlist(settings, result)
    _check_secret_backend(settings, result)
    _check_optional_features(settings, result)
    result.ok = not result.errors
    return result


def _render(settings: Settings, result: CheckResult) -> str:
    lines = [f"aethergate config_check (env={settings.app_env})"]
    for warning in result.warnings:
        lines.append(f"  WARN: {warning}")
    for error in result.errors:
        lines.append(f"  ERROR: {error}")
    lines.append("RESULT: " + ("VALID" if result.ok else "INVALID"))
    return "\n".join(lines)


def main() -> int:
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 - config parse failure is a config error
        # Never echo the exception's raw string if it could carry a value; the
        # Settings validators only name fields/missing vars, never values.
        print(f"aethergate config_check: INVALID\n  ERROR: {exc.__class__.__name__}")
        return 1
    result = run_checks(settings)
    print(_render(settings, result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
