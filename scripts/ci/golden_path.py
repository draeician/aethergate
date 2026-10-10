"""Deterministic, offline Alpha golden-path acceptance.

Runs the real AetherGate stack end-to-end with **no Internet, no model download,
and no provider credentials**:

1. fresh disposable PostgreSQL database;
2. migrate empty -> head;
3. start the deterministic fake Ollama upstream + DevOidcIdp + real API + worker;
4. bootstrap the initial admin, complete a real OIDC browser login (httpx);
5. create SecretRef/catalog/provider account/endpoint/alias/route to the fake
   Ollama upstream;
6. create an inference credential;
7. run the official OpenAI Python SDK non-stream + stream;
8. PostgreSQL backup;
9. restore into a fresh disposable database;
10. verify pre-backup state and run post-restore SDK inference.

This is CI/golden-path tooling only (see docs/operations/alpha-runbook.md). The
live nomnom acceptance with real Ollama is a separate pre-release gate.

Two phases are provided so the CI workflow can take a backup, destroy/restore the
database, and restart the API between them:

- ``--phase main``         : setup + SDK proofs; persists safe IDs/counts and the
                             raw keys into a mode-0600 file in ``--workdir``
                             (never printed).
- ``--phase post-restore`` : authenticate with the SAME keys (proving credential
                             hash preservation), assert recorded state, run SDK.

Usage:
    python scripts/ci/golden_path.py --phase main --base-url http://127.0.0.1:8000
    python scripts/ci/golden_path.py --phase post-restore --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from pathlib import Path

import httpx

STATE_FILE = "golden_state.json"
SECRETS_FILE = "golden_secrets.env"


def _log(message: str) -> None:
    print(f"[golden-path] {message}", flush=True)


def _fail(message: str) -> None:
    print(f"[golden-path] FAIL: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def _wait_ready(base_url: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health/ready", timeout=5.0)
            if resp.status_code == 200:
                return
            last = f"HTTP {resp.status_code} {resp.text[:120]}"
        except Exception as exc:  # noqa: BLE001
            last = exc.__class__.__name__
        time.sleep(1.0)
    _fail(f"readiness did not pass: {last}")


def _post(client: httpx.Client, path: str, body: dict) -> dict:
    resp = client.post(path, json=body)
    if resp.status_code not in (200, 201):
        _fail(f"POST {path} failed: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


def _bootstrap(client: httpx.Client, bootstrap_token: str) -> str:
    resp = client.post(
        "/admin/v1/bootstrap",
        headers={"Authorization": f"Bearer {bootstrap_token}"},
    )
    if resp.status_code != 201:
        _fail(f"bootstrap failed: HTTP {resp.status_code}")
    return resp.json()["raw_key"]


def _oidc_login(base_url: str) -> None:
    """Complete the real OIDC authorization-code + PKCE flow via httpx cookies."""
    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True) as browser:
        resp = browser.get("/admin/v1/auth/oidc/login")
        if resp.status_code != 200:
            _fail(f"OIDC login failed: HTTP {resp.status_code}")
        session = browser.get("/admin/v1/auth/session")
        if session.status_code != 200:
            _fail(f"session not established after login: HTTP {session.status_code}")
        if "browser_session" not in session.text:
            _fail("session kind is not a browser session")


def _create_catalog(
    client: httpx.Client, upstream_base: str, upstream_model: str
) -> dict[str, str]:
    # A local Ollama-compatible upstream needs no provider secret, so the account
    # carries no SecretRef (metadata only; never a raw secret).
    provider = _post(
        client,
        "/admin/v1/providers",
        {"kind": "ollama", "name": "golden-provider", "capabilities": ["text"]},
    )
    account = _post(
        client,
        "/admin/v1/provider-accounts",
        {
            "provider_id": provider["id"],
            "name": "golden-account",
        },
    )
    endpoint = _post(
        client,
        "/admin/v1/endpoints",
        {
            "provider_account_id": account["id"],
            "name": "golden-endpoint",
            "base_destination": upstream_base,
            "max_concurrency": 2,
        },
    )
    alias = _post(
        client,
        "/admin/v1/model-aliases",
        {"name": "golden-model", "capabilities": ["text"]},
    )
    route = _post(
        client,
        "/admin/v1/route-bindings",
        {
            "model_alias_id": alias["id"],
            "endpoint_id": endpoint["id"],
            "provider_account_id": account["id"],
            "upstream_model": upstream_model,
            "is_active": True,
        },
    )
    # A request-priced policy so a completed inference produces a UsageRecord and
    # a signed LedgerEntry (proving accounting identity survives backup/restore).
    policy = _post(
        client,
        "/admin/v1/price-policies",
        {
            "route_binding_id": route["id"],
            "billing_unit": "request",
            "currency": "USD",
            "request_price": "0.000000000123",
            "enabled": True,
        },
    )
    return {
        "provider_id": provider["id"],
        "account_id": account["id"],
        "endpoint_id": endpoint["id"],
        "alias_id": alias["id"],
        "route_id": route["id"],
        "price_policy_id": policy["id"],
    }


def _inference_credential(client: httpx.Client, project_id: str, name: str) -> tuple[str, str]:
    principals = client.get(
        f"/admin/v1/projects/{project_id}/principals?limit=200"
    ).json()
    if not principals["items"]:
        created_principal = _post(
            client,
            f"/admin/v1/projects/{project_id}/principals",
            {"kind": "service_account", "name": "golden-principal"},
        )
        principal_id = created_principal["id"]
    else:
        principal_id = principals["items"][0]["id"]
    created = _post(
        client,
        "/admin/v1/credentials",
        {
            "project_id": project_id,
            "principal_id": principal_id,
            "name": name,
            "audience": "inference",
        },
    )
    return created["raw_key"], created["credential"]["id"]


def _link_admin_oidc_identity(
    client: httpx.Client, project_id: str, issuer: str, subject: str
) -> None:
    """Link the deterministic IdP subject to a durable system_admin USER principal.

    The bootstrap credential's principal is a service account (not linkable to a
    human OIDC identity), so create a dedicated USER principal, grant it a
    deployment-scoped system_admin role, and link the deterministic subject.
    """
    user = _post(
        client,
        f"/admin/v1/projects/{project_id}/principals",
        {"kind": "user", "name": "golden-oidc-admin"},
    )
    _post(
        client,
        "/admin/v1/role-assignments",
        {
            "principal_id": user["id"],
            "role": "system_admin",
            "resource_scope_type": "deployment",
        },
    )
    resp = client.post(
        "/admin/v1/oidc/identities",
        json={"principal_id": user["id"], "issuer": issuer, "subject": subject},
    )
    # 201 created; 400 duplicate is an idempotent re-run.
    if resp.status_code not in (201, 400):
        _fail(f"link OIDC identity failed: HTTP {resp.status_code} {resp.text[:200]}")
    _log(f"identity link status={resp.status_code} principal={user['id']}")


def _sdk_inference(base_url: str, key: str, model: str, *, stream: bool) -> str:
    from openai import OpenAI

    client = OpenAI(base_url=f"{base_url}/v1", api_key=key, timeout=60.0)
    messages = [{"role": "user", "content": "golden path prompt"}]
    if stream:
        chunks = client.chat.completions.create(
            model=model, messages=messages, stream=True
        )
        collected = [
            c.choices[0].delta.content
            for c in chunks
            if c.choices and c.choices[0].delta and c.choices[0].delta.content
        ]
        if not collected:
            _fail("stream produced no content")
        return "ok_stream"
    completion = client.chat.completions.create(model=model, messages=messages)
    if not completion.choices[0].message.content:
        _fail("non-stream produced empty content")
    return "ok"


def _write_secrets(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _read_secrets(path: Path) -> dict[str, str]:
    secrets: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            secrets[key] = value
    return secrets


def _admin_client(base_url: str, key: str) -> httpx.Client:
    client = httpx.Client(base_url=base_url, timeout=60.0)
    client.headers["Authorization"] = f"Bearer {key}"
    return client


def phase_main(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    bootstrap_token = os.environ.get(args.bootstrap_token_env)
    if not bootstrap_token:
        _fail(f"{args.bootstrap_token_env} is not set")

    _log("waiting for readiness")
    _wait_ready(args.base_url)

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        _log("bootstrap admin (one-use)")
        admin_key = _bootstrap(client, bootstrap_token)

    with _admin_client(args.base_url, admin_key) as client:
        _log("create catalog + route to fake Ollama")
        catalog = _create_catalog(client, args.upstream_base, args.upstream_model)
        projects = client.get("/admin/v1/projects?limit=200").json()
        project_id = projects["items"][0]["id"]
        _log("create inference credential")
        key, credential_id = _inference_credential(client, project_id, "golden-cred")
        _log("link deterministic OIDC subject to a system_admin user principal")
        _link_admin_oidc_identity(
            client, project_id, args.oidc_issuer, args.oidc_subject
        )

    _log("verify deterministic OIDC browser login")
    _oidc_login(args.base_url)

    _log("official SDK non-stream")
    if _sdk_inference(args.base_url, key, "golden-model", stream=False) != "ok":
        _fail("non-stream inference did not return ok")
    _log("official SDK stream")
    if _sdk_inference(args.base_url, key, "golden-model", stream=True) != "ok_stream":
        _fail("stream inference did not return ok_stream")

    # Record safe state (IDs/counts only; no secrets).
    with _admin_client(args.base_url, admin_key) as client:
        usage = client.get("/admin/v1/usage-records?limit=200").json()
        ledger = client.get("/admin/v1/ledger-entries?limit=200").json()
    state = {
        "base_url": args.base_url,
        "project_id": project_id,
        "credential_id": credential_id,
        "catalog": catalog,
        "usage_records": usage.get("total", 0),
        "ledger_entries": ledger.get("total", 0),
        "sdk_non_stream": "ok",
        "sdk_stream": "ok_stream",
    }
    (workdir / STATE_FILE).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    _write_secrets(
        workdir / SECRETS_FILE,
        {"AETHERGATE_GOLDEN_ADMIN_KEY": admin_key, "AETHERGATE_GOLDEN_INFERENCE_KEY": key},
    )
    _log("golden path main phase complete")
    print(json.dumps({k: v for k, v in state.items() if k != "catalog"}, indent=2))
    return 0


def phase_post_restore(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    state = json.loads((workdir / STATE_FILE).read_text(encoding="utf-8"))
    secrets = _read_secrets(workdir / SECRETS_FILE)
    admin_key = secrets["AETHERGATE_GOLDEN_ADMIN_KEY"]
    inference_key = secrets["AETHERGATE_GOLDEN_INFERENCE_KEY"]

    _log("waiting for readiness after restore")
    _wait_ready(args.base_url)

    # The SAME admin credential must still authenticate (verifier preserved).
    with _admin_client(args.base_url, admin_key) as client:
        whoami = client.get("/admin/v1/whoami")
        if whoami.status_code != 200:
            _fail(f"restored admin credential rejected: HTTP {whoami.status_code}")
        usage = client.get("/admin/v1/usage-records?limit=200").json()
        ledger = client.get("/admin/v1/ledger-entries?limit=200").json()
        # Catalog must be preserved.
        routes = client.get("/admin/v1/route-bindings?limit=200").json()
        route_ids = {r["id"] for r in routes.get("items", [])}

    if state["catalog"]["route_id"] not in route_ids:
        _fail("restored catalog route binding not found")
    if usage.get("total", 0) != state["usage_records"]:
        _fail(
            f"usage record count changed across restore: "
            f"{state['usage_records']} -> {usage.get('total', 0)} (possible duplicate settlement)"
        )
    if ledger.get("total", 0) != state["ledger_entries"]:
        _fail(
            f"ledger entry count changed across restore: "
            f"{state['ledger_entries']} -> {ledger.get('total', 0)} (possible duplicate settlement)"
        )

    # The SAME inference credential must still authenticate and produce inference.
    _log("post-restore official SDK non-stream")
    if _sdk_inference(args.base_url, inference_key, "golden-model", stream=False) != "ok":
        _fail("post-restore non-stream inference failed")
    _log("post-restore official SDK stream")
    if _sdk_inference(args.base_url, inference_key, "golden-model", stream=True) != "ok_stream":
        _fail("post-restore stream inference failed")

    _log("golden path post-restore verification complete")
    print(
        json.dumps(
            {
                "usage_records": usage.get("total", 0),
                "ledger_entries": ledger.get("total", 0),
                "post_restore_sdk": "ok",
            },
            indent=2,
        )
    )
    return 0


def phase_sdk_only(args: argparse.Namespace) -> int:
    """Verify inference still works (restart checks); no state-count assertions."""
    workdir = Path(args.workdir)
    secrets = _read_secrets(workdir / SECRETS_FILE)
    inference_key = secrets["AETHERGATE_GOLDEN_INFERENCE_KEY"]
    _log("waiting for readiness")
    _wait_ready(args.base_url)
    _log("restart-check official SDK non-stream")
    if _sdk_inference(args.base_url, inference_key, "golden-model", stream=False) != "ok":
        _fail("restart-check non-stream inference failed")
    _log("restart-check official SDK stream")
    if _sdk_inference(args.base_url, inference_key, "golden-model", stream=True) != "ok_stream":
        _fail("restart-check stream inference failed")
    _log("restart-check SDK inference OK")
    print(json.dumps({"post_restart_sdk": "ok"}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", choices=["main", "post-restore", "sdk-only"], default="main"
    )
    parser.add_argument("--base-url", required=True, help="running API base URL")
    parser.add_argument("--workdir", default="/tmp/ag-golden", help="scratch directory")
    parser.add_argument(
        "--bootstrap-token-env",
        default="AETHERGATE_BOOTSTRAP_TOKEN",
        help="env var holding the bootstrap token",
    )
    parser.add_argument("--upstream-base", default="http://localhost:11434")
    parser.add_argument(
        "--upstream-model",
        default="fixture",
        help="upstream model name (fake Ollama: 'fixture'; nomnom: a real model)",
    )
    parser.add_argument(
        "--oidc-issuer",
        default=os.environ.get("OIDC_ISSUER", "http://127.0.0.1:8090"),
        help="deterministic IdP issuer (must match the API's configured issuer)",
    )
    parser.add_argument(
        "--oidc-subject",
        default=os.environ.get("AETHERGATE_OIDC_SUBJECT", "dev-user"),
        help="deterministic IdP subject to link to the admin principal",
    )
    args = parser.parse_args()
    if args.phase == "main":
        return phase_main(args)
    if args.phase == "sdk-only":
        return phase_sdk_only(args)
    return phase_post_restore(args)


if __name__ == "__main__":
    sys.exit(main())
