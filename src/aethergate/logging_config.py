"""Structured operational logging and central log redaction.

Alpha/production use JSON logs; development keeps a human-readable format. Every
request/worker lifecycle log carries only safe metadata (request/attempt/endpoint/
model/project IDs, lifecycle state, route templates, numeric statuses, durations).
Prompt/completion content, encrypted payloads, provider bodies, and all secret
material (Authorization headers, cookies, CSRF, OIDC tokens, ``agk_``/``ags_``/
bootstrap/provider keys, database URLs/passwords) are never logged by design.

:class:`RedactionFilter` is a defense-in-depth backstop: it scrubs known secret
shapes out of any record that would otherwise leak them. It is never permission
to log sensitive objects.

The gateway request ID is carried in a :class:`~contextvars.ContextVar` so logs
emitted deep in a request handler correlate with the response's request ID. The
gateway request ID is distinct from the upstream provider request ID.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Correlation context: the per-request gateway request ID (never the upstream one).
gateway_request_id_var: ContextVar[str | None] = ContextVar(
    "gateway_request_id", default=None
)

# The reserved LogRecord attribute names, so a formatter can pick out only the
# caller-supplied "extra" fields safely.
_RESERVED_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}

# Secret shapes scrubbed by the redaction backstop. Ordered most-specific first.
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization / bearer tokens.
    (re.compile(r"(?i)\b(authorization)\b\s*[:=]\s*\S+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer [REDACTED]"),
    # AetherGate credential/bootstrap prefixes.
    (re.compile(r"\bagk_[A-Za-z0-9._-]+"), "agk_[REDACTED]"),
    (re.compile(r"\bags_[A-Za-z0-9._-]+"), "ags_[REDACTED]"),
    (re.compile(r"\bagb_[A-Za-z0-9._-]+"), "agb_[REDACTED]"),
    # Session / CSRF cookies.
    (re.compile(r"\bag_session=[^;\s\"']+"), "ag_session=[REDACTED]"),
    (re.compile(r"\bag_csrf=[^;\s\"']+"), "ag_csrf=[REDACTED]"),
    # OIDC / OAuth secrets in query or key=value form.
    (
        re.compile(r"(?i)\b(id_token|access_token|refresh_token|code_verifier|client_secret|device_code)\b\s*[:=]\s*[^\s,&\"']+"),
        r"\1: [REDACTED]",
    ),
    (re.compile(r"(?i)\bcode=[A-Za-z0-9._~+/-]+"), "code=[REDACTED]"),
    (re.compile(r"(?i)\bstate=[A-Za-z0-9._~+/-]+"), "state=[REDACTED]"),
    (re.compile(r"(?i)\bnonce=[A-Za-z0-9._~+/-]+"), "nonce=[REDACTED]"),
    # Database URLs / passwords.
    (re.compile(r"(?i)(postgres(?:ql)?(?:\+asyncpg)?://)[^:\s/@]+:[^@\s]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)\b[a-z_]*password[a-z_]*\b\s*[:=]\s*\S+"), "password=[REDACTED]"),
    (re.compile(r"(?i)\b(database_url|postgres_url)\b\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
    (
        re.compile(
            r"(?i)\b(api_key|secret|token|bootstrap_token|access_key)\b\s*[:=]\s*\S+"
        ),
        r"\1=[REDACTED]",
    ),
    # PEM private keys.
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?"
            r"-----END [A-Z ]*PRIVATE KEY-----"
        ),
        "[REDACTED PRIVATE KEY]",
    ),
)


def set_gateway_request_id(request_id: str | None) -> None:
    gateway_request_id_var.set(request_id)


def get_log_gateway_request_id() -> str | None:
    return gateway_request_id_var.get()


def redact_text(value: str) -> str:
    """Apply all redaction patterns to ``value`` (defense in depth)."""
    redacted = value
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_value(value: Any) -> Any:
    """Redact strings (and recursively strings inside containers) safely."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_value(v) for v in value)
    return value


class RedactionFilter(logging.Filter):
    """Scrub known secret shapes from a log record as a safety backstop."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            elif isinstance(record.msg, (dict, list)):
                record.msg = redact_value(record.msg)
            if record.args:
                record.args = redact_value(record.args)
            for key, value in list(record.__dict__.items()):
                if key in _RESERVED_ATTRS:
                    continue
                if isinstance(value, str):
                    record.__dict__[key] = redact_text(value)
                elif isinstance(value, (dict, list, tuple)):
                    record.__dict__[key] = redact_value(value)
        except Exception:  # noqa: BLE001 - a filter must never break logging
            return True
        return True


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per record with a stable, safe field set."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        event = getattr(record, "event", None)
        if event is not None:
            payload["event"] = event
        # Human message (redacted by the filter already).
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let formatting drop a record
            message = ""
        if message:
            payload["message"] = message
        # Gateway request correlation (explicit extra wins; else contextvar).
        request_id = getattr(record, "request_id", None) or gateway_request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        # Any caller-supplied structured extras.
        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key in payload:
                continue
            if key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            # Exception text is redacted; never stringify secret-bearing objects.
            payload["exc_message"] = redact_text(str(record.exc_info[1]))
        return json.dumps(payload, default=str)


class _HumanFormatter(logging.Formatter):
    """Human-readable dev format including safe structured extras."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_ATTRS and not key.startswith("_")
        }
        request_id = getattr(record, "request_id", None) or gateway_request_id_var.get()
        if request_id and "request_id" not in extras:
            extras["request_id"] = request_id
        if extras:
            rendered = " ".join(f"{k}={v}" for k, v in extras.items())
            return f"{base} {rendered}"
        return base


_HANDLER_MARKER = "_aethergate_structured_handler"


def configure_logging(
    *,
    level: str = "info",
    json_logs: bool | None = None,
    app_env: str = "dev",
) -> logging.Handler:
    """Ensure a redacting structured handler exists on the root logger.

    Idempotent and **additive**: existing handlers (uvicorn's, pytest's ``caplog``,
    etc.) are preserved so test capture and third-party output still work. Our
    handler is tagged so repeated calls replace only our own prior handler.

    JSON logs are used in prod/alpha (``app_env == "prod"``) unless explicitly
    overridden; development keeps human-readable output.
    """
    if json_logs is None:
        json_logs = app_env == "prod"
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARKER, False):
            root.removeHandler(existing)
    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, _HANDLER_MARKER, True)
    handler.addFilter(RedactionFilter())
    if json_logs:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            _HumanFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root.addHandler(handler)
    return handler


def configure_logging_from_settings() -> None:
    """Configure logging from process settings (safe: never prints secrets)."""
    from aethergate.config import get_settings

    settings = get_settings()
    configure_logging(level=settings.log_level, app_env=settings.app_env)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a structured event with only safe ``fields``.

    Callers must pass safe metadata only (IDs, states, numeric statuses,
    durations). ``request_id`` is auto-filled from context when absent.
    """
    extra = {"event": event, **fields}
    logger.log(level, event, extra=extra)


__all__ = [
    "RedactionFilter",
    "configure_logging",
    "configure_logging_from_settings",
    "get_log_gateway_request_id",
    "log_event",
    "redact_text",
    "redact_value",
    "set_gateway_request_id",
]
