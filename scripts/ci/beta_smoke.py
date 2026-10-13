#!/usr/bin/env python3
"""Production-policy Beta 1 deployment smoke (bootstrap-only, no OIDC/web).

Proves a Beta production profile end-to-end with the official OpenAI Python SDK
against a disposable upstream, with **no Internet, no model downloads, and no
provider credentials**, and with inference-auth bypass off (prod).

Unlike ``golden_path.py`` (which exercises OIDC + web + the full SDK matrix), this
smoke covers the production deployment shape: one-use bootstrap, literal-IP
catalog, an inference credential, and the core Models/Chat/Embeddings SDK surface
through the same durable scheduler + accounting authority.

Reuses the tested admin/httpx helpers in ``golden_path.py`` so the two smoke
paths cannot drift.

Phases:
  --phase main  bootstrap + catalog + SDK; persists the raw admin/inference keys
                to a mode-0600 file under --workdir (never printed).
  --phase post  reuses the persisted keys after a backup/restore to prove the
                restored deployment still authenticates and serves the SDK.

Usage:
    AETHERGATE_BOOTSTRAP_TOKEN=... python scripts/ci/beta_smoke.py \
        --phase main --base-url http://127.0.0.1:<port> --upstream-base http://<ip>:11434
    python scripts/ci/beta_smoke.py --phase post --base-url http://127.0.0.1:<port>
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_path as gp  # noqa: E402

SECRETS_FILE = "beta_secrets.env"


def _write_secrets(path: Path, values: dict[str, str]) -> None:
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _read_secrets(path: Path) -> dict[str, str]:
    secrets: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            secrets[key] = value
    return secrets


def _sdk_surface(base_url: str, key: str) -> None:
    from openai import OpenAI

    gp._log("official SDK models.list")
    client = OpenAI(base_url=f"{base_url}/v1", api_key=key, timeout=60.0)
    if not client.models.list().data:
        gp._fail("models.list returned no models")

    gp._log("official SDK chat non-stream")
    if gp._sdk_inference(base_url, key, "golden-model", stream=False) != "ok":
        gp._fail("non-stream inference did not return ok")
    gp._log("official SDK chat stream")
    if gp._sdk_inference(base_url, key, "golden-model", stream=True) != "ok_stream":
        gp._fail("stream inference did not return ok_stream")
    gp._log("official SDK embeddings")
    if gp._sdk_embedding(base_url, key, "golden-embed-model") != "ok_embed":
        gp._fail("embeddings did not return ok_embed")


def phase_main(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get(args.bootstrap_token_env)
    if not token:
        gp._fail(f"{args.bootstrap_token_env} is not set")

    gp._log("waiting for readiness")
    gp._wait_ready(args.base_url)

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        gp._log("bootstrap admin (one-use)")
        admin_key = gp._bootstrap(client, token)

    with gp._admin_client(args.base_url, admin_key) as client:
        gp._log("create catalog + route to literal-IP upstream")
        gp._create_catalog(client, args.upstream_base, args.upstream_model)
        projects = client.get("/admin/v1/projects?limit=200").json()
        project_id = projects["items"][0]["id"]
        gp._log("create inference credential")
        key, _credential_id = gp._inference_credential(client, project_id, "beta-cred")

    _sdk_surface(args.base_url, key)

    _write_secrets(
        workdir / SECRETS_FILE,
        {"AETHERGATE_BETA_ADMIN_KEY": admin_key, "AETHERGATE_BETA_INFERENCE_KEY": key},
    )
    gp._log("beta production smoke main phase complete")
    return 0


def phase_post(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    secrets = _read_secrets(workdir / SECRETS_FILE)
    key = secrets.get("AETHERGATE_BETA_INFERENCE_KEY")
    admin_key = secrets.get("AETHERGATE_BETA_ADMIN_KEY")
    if not key or not admin_key:
        gp._fail("missing persisted beta secrets (run --phase main first)")

    gp._log("waiting for readiness after restore")
    gp._wait_ready(args.base_url)

    # The SAME admin credential must still authenticate (verifier preserved).
    with gp._admin_client(args.base_url, admin_key) as client:
        if client.get("/admin/v1/whoami").status_code != 200:
            gp._fail("persisted admin credential did not authenticate after restore")

    _sdk_surface(args.base_url, key)
    gp._log("beta production smoke post-restore phase complete")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["main", "post"], default="main")
    parser.add_argument("--base-url", required=True, help="running API base URL")
    parser.add_argument("--workdir", default="/tmp/ag-beta1-smoke", help="scratch directory")
    parser.add_argument(
        "--bootstrap-token-env",
        default="AETHERGATE_BOOTSTRAP_TOKEN",
        help="env var holding the one-use bootstrap token",
    )
    parser.add_argument("--upstream-base", default="http://127.0.0.1:11434")
    parser.add_argument("--upstream-model", default="fixture")
    args = parser.parse_args()
    if args.phase == "main":
        return phase_main(args)
    return phase_post(args)


if __name__ == "__main__":
    sys.exit(main())
