"""Safe diagnostics / support bundle.

Collects only non-sensitive operational state so an operator can debug a
deployment without risking credentials or content. It never includes:

- credentials/tokens/cookies (``agk_``/``ags_``/bootstrap/provider keys);
- prompt/completion/tool content or encrypted payload/result bytes;
- database URLs/passwords;
- raw provider bodies/headers.

Nothing is uploaded automatically: the bundle is written to an explicit path.

Usage:
    python -m aethergate.diagnostics --output bundle.json [--logs-lines N]
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

from aethergate import __version__
from aethergate.config import get_settings


def _version_info() -> dict[str, Any]:
    return {"aethergate": __version__, "python": platform.python_version()}


async def _migration_state() -> dict[str, Any]:
    from aethergate.schema_check import applied_revision, expected_head, schema_is_current

    return {
        "expected_head": expected_head(),
        "applied_revision": await applied_revision(),
        "is_current": await schema_is_current(),
    }


async def _health() -> dict[str, Any]:
    from aethergate.persistence import db

    return {"database_reachable": await db.check_database_connection()}


def _config_flags() -> dict[str, Any]:
    # Only booleans / counts / non-secret feature flags. Never URL/password/key.
    settings = get_settings()
    return {
        "app_env": settings.app_env,
        "oidc_enabled": settings.oidc_enabled,
        "device_flow_enabled": settings.device_flow_enabled,
        "web_console_enabled": settings.web_console_enabled,
        "inference_auth_bypass": settings.allow_inference_auth_bypass,
        "bootstrap_token_configured": settings.bootstrap_token is not None,
        "queue_key_configured": settings.queue_key is not None,
        "upstream_allowlist_hosts": len(settings.upstream_allowlist_hosts),
        "worker_lease_seconds": settings.worker_lease_seconds,
        "queue_max_requests": settings.queue_max_requests,
    }


async def _queue_summary() -> dict[str, Any]:
    from sqlalchemy import func, select

    from aethergate.persistence import models
    from aethergate.persistence.db import get_session_factory

    try:
        async with get_session_factory()() as session:
            rows = (
                await session.execute(
                    select(models.InferenceRequest.state, func.count())
                    .group_by(models.InferenceRequest.state)
                )
            ).all()
            counts = {state: int(count) for state, count in rows}
            unknown = counts.get("outcome_unknown", 0)
            return {"counts_by_state": counts, "outcome_unknown_total": unknown}
    except Exception:  # noqa: BLE001 - diagnostics must never crash
        return {"error": "unavailable"}


async def _observability_summary() -> dict[str, Any]:
    from aethergate.observability import service as observability
    from aethergate.persistence.db import get_session_factory

    try:
        async with get_session_factory()() as session:
            # Diagnostics calls the read-only repository aggregates directly
            # (no content, no authorization context) rather than the admin service.
            window = observability.resolve_window(None)
            qw = await observability.repository.queue_wait_percentiles(
                session,
                window_start=window.window_start,
                window_end=window.window_end,
                project_ids=None,
            )
            ttft = await observability.repository.ttft_percentiles(
                session,
                window_start=window.window_start,
                window_end=window.window_end,
                project_ids=None,
            )
            retry = await observability.repository.retry_metrics(
                session,
                window_start=window.window_start,
                window_end=window.window_end,
                project_ids=None,
            )
            return {
                "window_seconds": window.window_seconds,
                "queue_wait": {
                    "sample_count": qw.sample_count,
                    "p50_ms": qw.p50_ms,
                    "p95_ms": qw.p95_ms,
                    "p99_ms": qw.p99_ms,
                },
                "ttft": {
                    "sample_count": ttft.sample_count,
                    "p50_ms": ttft.p50_ms,
                },
                "retry": {
                    "attempted_requests": retry.attempted_requests,
                    "retried_requests": retry.retried_requests,
                    "retry_attempts": retry.retry_attempts,
                },
            }
    except Exception:  # noqa: BLE001
        return {"error": "unavailable"}


def _recent_logs(lines: int) -> list[str]:
    """Return the last ``lines`` stderr log lines, redacted (never content)."""
    # Structured logs go to stdout; diagnostics reads its own recent stream only
    # if a log file is configured. Here we return an empty list by default and
    # rely on the operator's log collector for historical lines. The function is
    # kept so the bundle schema is stable, and every returned line is redacted.
    return []


def _redact_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Final defense-in-depth redaction pass over the whole bundle.

    Redacts each string value individually (never the serialized JSON) so the
    result stays structurally valid JSON.
    """
    from aethergate.logging_config import redact_value

    return redact_value(bundle)


async def acollect(logs_lines: int = 50) -> dict[str, Any]:
    bundle = {
        "version": _version_info(),
        "migration": await _migration_state(),
        "health": await _health(),
        "config": _config_flags(),
        "queue": await _queue_summary(),
        "observability": await _observability_summary(),
        "recent_logs": _recent_logs(logs_lines),
    }
    return _redact_bundle(bundle)


def collect(logs_lines: int = 50) -> dict[str, Any]:
    import asyncio

    return asyncio.run(acollect(logs_lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="AetherGate safe diagnostics bundle")
    parser.add_argument("--output", help="explicit output path (or --stdout)")
    parser.add_argument(
        "--stdout", action="store_true", help="write the bundle to stdout"
    )
    parser.add_argument(
        "--logs-lines", type=int, default=50, help="recent redacted log lines"
    )
    args = parser.parse_args()
    if not args.output and not args.stdout:
        parser.error("one of --output or --stdout is required")

    bundle = collect(max(0, args.logs_lines))
    rendered = json.dumps(bundle, indent=2, default=str) + "\n"
    if args.stdout:
        sys.stdout.write(rendered)
        return 0
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    try:
        output.chmod(0o600)
    except OSError:
        pass
    print(f"diagnostics written to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
