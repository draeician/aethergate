"""The ``aethergate`` command: a thin HTTP client over ``/admin/v1``.

Run ``aethergate --help`` for the full command tree. The CLI never imports
persistence/repository modules; every product command uses the admin HTTP API.
See ``docs/cli.md`` for profiles, token storage, and the exit-code contract.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import click

from aethergate import __version__
from aethergate.cli import output, profiles
from aethergate.cli.client import Client
from aethergate.cli.errors import (
    AuthError,
    CliError,
    UsageError,
)
from aethergate.cli.tokenstore import TokenStore, default_token_store

_PROFILE_KEY = "aethergate_profile"
_TOKEN_ENV = "AETHERGATE_TOKEN"

# Prefixes distinguishing a human CLI session token from a service credential.
_CLI_SESSION_PREFIX = "ags_"
_SERVICE_CREDENTIAL_PREFIX = "agk_"


def _json_opt(func):
    return click.option(
        "--json",
        "as_json",
        is_flag=True,
        default=False,
        help="Emit machine-readable JSON on stdout.",
    )(func)


def _build_token_store() -> TokenStore:
    return default_token_store()


def _resolve_profile(ctx: click.Context) -> profiles.Profile:
    store = profiles.load()
    return profiles.get(store, ctx.obj.get(_PROFILE_KEY) if ctx.obj else None)


def _resolve_token(profile_name: str) -> tuple[str | None, str]:
    """Return ``(token, source)`` for a profile.

    ``source`` is one of ``"env"`` (``AETHERGATE_TOKEN`` override), ``"store"``
    (persisted in the protected token store), or ``"none"``. Callers use the
    source to decide whether a 401 may clear persistent storage.
    """
    env_token = os.environ.get(_TOKEN_ENV)
    if env_token:
        return env_token, "env"
    token = _build_token_store().get(profile_name)
    if token:
        return token, "store"
    return None, "none"


def _maybe_clear_stale_session(profile_name: str, token: str | None, source: str) -> bool:
    """Clear a persisted human CLI session token after an authentication 401.

    Only a persisted ``ags_...`` human session is cleared. Service credentials
    (``agk_...``) are retained, and ``AETHERGATE_TOKEN`` env overrides never
    mutate persistent storage. Returns ``True`` when a token was removed.
    """
    if source != "store" or not token or not token.startswith(_CLI_SESSION_PREFIX):
        return False
    _build_token_store().delete(profile_name)
    return True


@contextmanager
def _client_ctx(ctx: click.Context) -> Iterator[tuple[profiles.Profile, Client]]:
    profile = _resolve_profile(ctx)
    token, source = _resolve_token(profile.name)
    client = Client(base_url=profile.base_url, token=token, verify=profile.verify)
    try:
        yield profile, client
    except AuthError:
        if _maybe_clear_stale_session(profile.name, token, source):
            raise AuthError(
                "Authentication is required or has expired. "
                "Run `aethergate auth login` to sign in again."
            ) from None
        raise
    finally:
        client.close()


def _emit_data(as_json: bool, data: Any) -> None:
    output.emit(data, as_json=as_json)


@click.group()
@click.option("--profile", "-p", "profile_name", help="Profile name to use.")
@click.version_option(__version__, prog_name="aethergate")
@click.pass_context
def cli(ctx: click.Context, profile_name: str | None) -> None:
    ctx.ensure_object(dict)
    ctx.obj[_PROFILE_KEY] = profile_name


# --- profile ----------------------------------------------------------------


@cli.group()
def profile() -> None:
    """Manage CLI profiles (base URL + trusted CA; no secrets)."""


@profile.command("create")
@click.argument("name")
@click.argument("base_url")
@click.option("--ca-bundle", help="Path to a trusted CA bundle (PEM).")
@click.option("--insecure", is_flag=True, help="Allow plain http (local/test only).")
@click.option("--default", "set_default", is_flag=True, help="Set as the default profile.")
def profile_create(
    name: str, base_url: str, ca_bundle: str | None, insecure: bool, set_default: bool
) -> None:
    store = profiles.load()
    created = profiles.add(
        store,
        name,
        base_url,
        ca_bundle=ca_bundle,
        allow_insecure=insecure,
        set_default=set_default,
    )
    output.info(f"created profile {created.name!r} -> {created.base_url}")


@profile.command("list")
@_json_opt
def profile_list(as_json: bool) -> None:
    store = profiles.load()
    data = {
        "default_profile": store.default_profile,
        "profiles": [
            {"name": p.name, "base_url": p.base_url, "ca_bundle": p.ca_bundle}
            for p in store.profiles.values()
        ],
    }
    _emit_data(as_json, data)


@profile.command("show")
@click.argument("name", required=False)
@_json_opt
def profile_show(name: str | None, as_json: bool) -> None:
    store = profiles.load()
    p = profiles.get(store, name)
    _emit_data(
        as_json,
        {"name": p.name, "base_url": p.base_url, "ca_bundle": p.ca_bundle},
    )


@profile.command("delete")
@click.argument("name")
def profile_delete(name: str) -> None:
    store = profiles.load()
    profiles.delete(store, name)
    _build_token_store().delete(name)
    output.info(f"deleted profile {name!r}")


@profile.command("set-default")
@click.argument("name")
def profile_set_default(name: str) -> None:
    store = profiles.load()
    profiles.set_default(store, name)
    output.info(f"default profile is now {name!r}")


# --- auth -------------------------------------------------------------------


@cli.group()
def auth() -> None:
    """Authenticate via OAuth device flow or a service credential."""


@auth.command("login")
@_json_opt
@click.pass_context
def auth_login(ctx: click.Context, as_json: bool) -> None:
    profile = _resolve_profile(ctx)
    client = Client(base_url=profile.base_url, token=None, verify=profile.verify)
    try:
        started = client.post("/admin/v1/auth/device/start")
        device_code = started["device_code"]
        user_code = started["user_code"]
        verification_uri = (
            started.get("verification_uri_complete") or started["verification_uri"]
        )
        interval = int(started.get("interval", 5))
    except CliError:
        client.close()
        raise

    output.info(f"Open this URL and enter the code: {verification_uri}")
    output.info(f"Code: {user_code}")

    try:
        while True:
            time.sleep(interval)
            result = client.post(
                "/admin/v1/auth/device/poll", {"device_code": device_code}
            )
            status = result.get("status")
            if status == "pending":
                continue
            if status == "slow_down":
                interval += 5
                continue
            if status == "success":
                token = result.get("token")
                if not token:
                    raise UsageError("device login succeeded but returned no token")
                _build_token_store().set(profile.name, token)
                _emit_data(as_json, _safe_login_result(result))
                return
            raise UsageError(f"unexpected device poll status {status!r}")
    finally:
        client.close()


def _safe_login_result(result: dict[str, Any]) -> dict[str, Any]:
    """Strip the raw bearer token before any output is produced.

    The server returns the one-time ``ags_...`` token exactly once; the CLI
    consumes it for protected storage and must never emit it. Only safe session
    metadata is returned for human/JSON rendering.
    """
    session = result.get("session") or {}
    return {
        "status": "success",
        "stored": True,
        "authentication_kind": session.get("authentication_kind"),
        "principal_id": session.get("principal_id"),
        "roles": session.get("roles", []),
        "expires_in": result.get("expires_in"),
    }


@auth.command("logout")
@_json_opt
@click.pass_context
def auth_logout(ctx: click.Context, as_json: bool) -> None:
    profile = _resolve_profile(ctx)
    token, source = _resolve_token(profile.name)
    if source == "none" or not token:
        raise AuthError("not logged in; no session token is available")
    if token.startswith(_SERVICE_CREDENTIAL_PREFIX):
        raise UsageError(
            "a service-account credential is stored, not a human CLI session. "
            "Run `aethergate auth clear-token` to remove it."
        )

    client = Client(base_url=profile.base_url, token=token, verify=profile.verify)
    try:
        try:
            result = client.post("/admin/v1/auth/cli/logout")
        except AuthError:
            # Already expired/revoked server-side: still clear the stale local token.
            result = {"revoked": False}
    finally:
        client.close()

    if source == "store":
        _build_token_store().delete(profile.name)
    _emit_data(as_json, result)


@auth.command("clear-token")
@_json_opt
@click.pass_context
def auth_clear_token(ctx: click.Context, as_json: bool) -> None:
    """Remove the locally stored token without contacting the server."""
    profile = _resolve_profile(ctx)
    _build_token_store().delete(profile.name)
    _emit_data(as_json, {"cleared": True})


@auth.command("whoami")
@_json_opt
@click.pass_context
def auth_whoami(ctx: click.Context, as_json: bool) -> None:
    with _client_ctx(ctx) as (_profile, client):
        result = client.get("/admin/v1/whoami")
    _emit_data(as_json, result)


@auth.command("set-token")
@click.option("--stdin", "from_stdin", is_flag=True, help="Read the token from stdin.")
@_json_opt
@click.pass_context
def auth_set_token(ctx: click.Context, from_stdin: bool, as_json: bool) -> None:
    profile = _resolve_profile(ctx)
    if from_stdin:
        token = sys.stdin.read().strip()
    else:
        token = click.prompt("Token", hide_input=True).strip()
    if not token:
        raise UsageError("no token supplied")
    client = Client(base_url=profile.base_url, token=token, verify=profile.verify)
    try:
        whoami = client.get("/admin/v1/whoami")
    finally:
        client.close()
    _build_token_store().set(profile.name, token)
    _emit_data(as_json, {"stored": True, "whoami": whoami})


# --- generic list/show helpers ----------------------------------------------


def _list(as_json: bool, ctx: click.Context, path: str, params: dict | None = None) -> None:
    with _client_ctx(ctx) as (_profile, client):
        data = client.get(path, params=params)
    _emit_data(as_json, data)


def _show(as_json: bool, ctx: click.Context, path: str) -> None:
    with _client_ctx(ctx) as (_profile, client):
        data = client.get(path)
    _emit_data(as_json, data)


# --- projects ---------------------------------------------------------------


@cli.group()
def projects() -> None:
    """Project CRUD."""


@projects.command("list")
@_json_opt
@click.option("--limit", type=int, default=50)
@click.option("--offset", type=int, default=0)
@click.pass_context
def projects_list(ctx: click.Context, as_json: bool, limit: int, offset: int) -> None:
    _list(as_json, ctx, "/admin/v1/projects", {"limit": limit, "offset": offset})


@projects.command("show")
@click.argument("project_id")
@_json_opt
@click.pass_context
def projects_show(ctx: click.Context, project_id: str, as_json: bool) -> None:
    _show(as_json, ctx, f"/admin/v1/projects/{project_id}")


@projects.command("create")
@click.argument("name")
@_json_opt
@click.pass_context
def projects_create(ctx: click.Context, name: str, as_json: bool) -> None:
    with _client_ctx(ctx) as (_profile, client):
        data = client.post("/admin/v1/projects", {"name": name})
    _emit_data(as_json, data)


@projects.command("update")
@click.argument("project_id")
@click.option("--name")
@click.option("--active/--inactive", "is_active", default=None)
@_json_opt
@click.pass_context
def projects_update(
    ctx: click.Context, project_id: str, name: str | None, is_active: bool | None, as_json: bool
) -> None:
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if is_active is not None:
        body["is_active"] = is_active
    with _client_ctx(ctx) as (_profile, client):
        data = client.patch(f"/admin/v1/projects/{project_id}", body)
    _emit_data(as_json, data)


# --- principals -------------------------------------------------------------


@cli.group()
def principals() -> None:
    """Principal identity management."""


@principals.command("list")
@click.option("--project", "project_id", required=True)
@_json_opt
@click.option("--limit", type=int, default=50)
@click.option("--offset", type=int, default=0)
@click.pass_context
def principals_list(
    ctx: click.Context, project_id: str, as_json: bool, limit: int, offset: int
) -> None:
    _list(
        as_json,
        ctx,
        f"/admin/v1/projects/{project_id}/principals",
        {"limit": limit, "offset": offset},
    )


@principals.command("show")
@click.argument("principal_id")
@_json_opt
@click.pass_context
def principals_show(ctx: click.Context, principal_id: str, as_json: bool) -> None:
    _show(as_json, ctx, f"/admin/v1/principals/{principal_id}")


@principals.command("create")
@click.option("--project", "project_id", required=True)
@click.option("--kind", required=True)
@click.argument("name")
@_json_opt
@click.pass_context
def principals_create(
    ctx: click.Context, project_id: str, kind: str, name: str, as_json: bool
) -> None:
    with _client_ctx(ctx) as (_profile, client):
        data = client.post(
            f"/admin/v1/projects/{project_id}/principals", {"kind": kind, "name": name}
        )
    _emit_data(as_json, data)


# --- credentials ------------------------------------------------------------


@cli.group()
def credentials() -> None:
    """API credentials (metadata only; raw keys are never listed)."""


@credentials.command("list")
@click.option("--project", "project_id", required=True)
@_json_opt
@click.option("--limit", type=int, default=50)
@click.option("--offset", type=int, default=0)
@click.pass_context
def credentials_list(
    ctx: click.Context, project_id: str, as_json: bool, limit: int, offset: int
) -> None:
    _list(
        as_json,
        ctx,
        f"/admin/v1/projects/{project_id}/credentials",
        {"limit": limit, "offset": offset},
    )


@credentials.command("show")
@click.argument("credential_id")
@_json_opt
@click.pass_context
def credentials_show(ctx: click.Context, credential_id: str, as_json: bool) -> None:
    _show(as_json, ctx, f"/admin/v1/credentials/{credential_id}")


# --- role assignments -------------------------------------------------------


@cli.group()
def role_assignments() -> None:
    """Role-assignment listing."""


@role_assignments.command("list")
@_json_opt
@click.option("--limit", type=int, default=50)
@click.option("--offset", type=int, default=0)
@click.pass_context
def role_assignments_list(ctx: click.Context, as_json: bool, limit: int, offset: int) -> None:
    _list(as_json, ctx, "/admin/v1/role-assignments", {"limit": limit, "offset": offset})


@role_assignments.command("show")
@click.argument("assignment_id")
@_json_opt
@click.pass_context
def role_assignments_show(ctx: click.Context, assignment_id: str, as_json: bool) -> None:
    _show(as_json, ctx, f"/admin/v1/role-assignments/{assignment_id}")


# --- catalog ----------------------------------------------------------------


def _catalog_group(name: str, help_text: str, path: str) -> click.Group:
    @cli.group(name)
    def grp() -> None:
        pass

    grp.__doc__ = help_text

    @grp.command("list")
    @_json_opt
    @click.option("--limit", type=int, default=50)
    @click.option("--offset", type=int, default=0)
    @click.pass_context
    def list_(ctx: click.Context, as_json: bool, limit: int, offset: int) -> None:
        _list(as_json, ctx, f"/admin/v1/{path}", {"limit": limit, "offset": offset})

    @grp.command("show")
    @click.argument("resource_id")
    @_json_opt
    @click.pass_context
    def show_(ctx: click.Context, resource_id: str, as_json: bool) -> None:
        _show(as_json, ctx, f"/admin/v1/{path}/{resource_id}")

    return grp


_catalog_group("providers", "Provider catalog.", "providers")
_catalog_group("provider-accounts", "Provider accounts.", "provider-accounts")
_catalog_group("endpoints", "Upstream endpoints.", "endpoints")
_catalog_group("model-aliases", "Public model aliases.", "model-aliases")
_catalog_group("route-bindings", "Model -> endpoint route bindings.", "route-bindings")
_catalog_group("price-policies", "Route price policies.", "price-policies")


# --- providers create -------------------------------------------------------


@cli.command("create-provider")
@click.option("--kind", required=True)
@click.argument("name")
@_json_opt
@click.pass_context
def create_provider(ctx: click.Context, kind: str, name: str, as_json: bool) -> None:
    with _client_ctx(ctx) as (_profile, client):
        data = client.post("/admin/v1/providers", {"kind": kind, "name": name})
    _emit_data(as_json, data)


# --- accounting -------------------------------------------------------------


@cli.group()
def budgets() -> None:
    """Project budget policies and status."""


@budgets.command("list")
@_json_opt
@click.option("--limit", type=int, default=50)
@click.option("--offset", type=int, default=0)
@click.pass_context
def budgets_list(ctx: click.Context, as_json: bool, limit: int, offset: int) -> None:
    _list(as_json, ctx, "/admin/v1/project-budget-policies", {"limit": limit, "offset": offset})


@budgets.command("show")
@click.argument("policy_id")
@_json_opt
@click.pass_context
def budgets_show(ctx: click.Context, policy_id: str, as_json: bool) -> None:
    _show(as_json, ctx, f"/admin/v1/project-budget-policies/{policy_id}")


@budgets.command("status")
@click.option("--project", "project_id", required=True)
@_json_opt
@click.pass_context
def budgets_status(ctx: click.Context, project_id: str, as_json: bool) -> None:
    _show(as_json, ctx, f"/admin/v1/projects/{project_id}/budget-status")


_catalog_group("usage", "Usage records.", "usage-records")
_catalog_group("ledger", "Ledger entries.", "ledger-entries")
_catalog_group("audit", "Administrative audit events.", "audit-events")


# --- queue / operator -------------------------------------------------------


@cli.group()
def queue() -> None:
    """Queue and operator control plane."""


@queue.command("list")
@_json_opt
@click.option("--state")
@click.option("--project", "project_id")
@click.option("--limit", type=int, default=50)
@click.option("--offset", type=int, default=0)
@click.pass_context
def queue_list(
    ctx: click.Context,
    as_json: bool,
    state: str | None,
    project_id: str | None,
    limit: int,
    offset: int,
) -> None:
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if state:
        params["state"] = state
    if project_id:
        params["project_id"] = project_id
    _list(as_json, ctx, "/admin/v1/queue/requests", params)


@queue.command("show")
@click.argument("request_id")
@_json_opt
@click.pass_context
def queue_show(ctx: click.Context, request_id: str, as_json: bool) -> None:
    _show(as_json, ctx, f"/admin/v1/queue/requests/{request_id}")


@queue.command("summary")
@_json_opt
@click.pass_context
def queue_summary(ctx: click.Context, as_json: bool) -> None:
    _show(as_json, ctx, "/admin/v1/queue/summary")


@queue.command("cancel")
@click.argument("request_id")
@_json_opt
@click.pass_context
def queue_cancel(ctx: click.Context, request_id: str, as_json: bool) -> None:
    with _client_ctx(ctx) as (_profile, client):
        data = client.post(f"/admin/v1/queue/requests/{request_id}/cancel")
    _emit_data(as_json, data)


@queue.command("outcome-unknown")
@_json_opt
@click.option("--limit", type=int, default=50)
@click.option("--offset", type=int, default=0)
@click.pass_context
def queue_outcome_unknown(ctx: click.Context, as_json: bool, limit: int, offset: int) -> None:
    _list(as_json, ctx, "/admin/v1/queue/outcome-unknown", {"limit": limit, "offset": offset})


@queue.command("reconcile")
@click.argument("request_id")
@click.option("--disposition", required=True, type=click.Choice(["failed", "cancelled"]))
@_json_opt
@click.pass_context
def queue_reconcile(
    ctx: click.Context, request_id: str, disposition: str, as_json: bool
) -> None:
    with _client_ctx(ctx) as (_profile, client):
        data = client.post(
            f"/admin/v1/queue/requests/{request_id}/reconcile",
            {"disposition": disposition},
        )
    _emit_data(as_json, data)


@queue.command("quota-status")
@_json_opt
@click.pass_context
def queue_quota_status(ctx: click.Context, as_json: bool) -> None:
    _show(as_json, ctx, "/admin/v1/queue/quota-status")


# --- endpoint runtime -------------------------------------------------------


@cli.group()
def endpoint() -> None:
    """Endpoint runtime status and operational state."""


@endpoint.command("list")
@_json_opt
@click.pass_context
def endpoint_list(ctx: click.Context, as_json: bool) -> None:
    _show(as_json, ctx, "/admin/v1/queue/endpoints")


@endpoint.command("show")
@click.argument("endpoint_id")
@_json_opt
@click.pass_context
def endpoint_show(ctx: click.Context, endpoint_id: str, as_json: bool) -> None:
    _show(as_json, ctx, f"/admin/v1/queue/endpoints/{endpoint_id}")


def _endpoint_op(op: str) -> Any:
    @click.argument("endpoint_id")
    @_json_opt
    @click.pass_context
    def cmd(ctx: click.Context, endpoint_id: str, as_json: bool) -> None:
        with _client_ctx(ctx) as (_profile, client):
            data = client.post(f"/admin/v1/queue/endpoints/{endpoint_id}/{op}")
        _emit_data(as_json, data)

    return cmd


for _op in ("pause", "drain", "resume"):
    endpoint.add_command(click.command(f"{_op}")(_endpoint_op(_op)))


# --- completion -------------------------------------------------------------


@cli.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion(shell: str) -> None:
    from click.shell_completion import get_completion_class

    cls = get_completion_class(shell)
    if cls is None:
        raise UsageError(f"no completion support for shell {shell!r}")
    complete = cls(cli, {}, "aethergate", "_AETHERGATE_COMPLETE")
    click.echo(complete.source(), nl=False)


def main() -> None:
    try:
        cli()
    except CliError as exc:
        output.info(f"error: {exc.message}")
        raise SystemExit(exc.exit_code) from None


if __name__ == "__main__":
    main()


__all__ = ["cli", "main"]
