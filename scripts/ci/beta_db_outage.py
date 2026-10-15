#!/usr/bin/env python3
"""Deterministic PostgreSQL outage/recovery proof for the Beta 1 release gate.

``scripts/ci/beta_release_gate.sh`` orchestrates the disposable-stack container
lifecycle (stop/start the worker and PostgreSQL) while this helper performs the
durable-state observations and assertions over the admin API. It reuses the
already-provisioned, file-stored Beta credentials
(``$WORKDIR/beta_secrets.env``) and never prints a secret value.

Stages (each is a separate short-lived invocation; state is persisted to
``$WORKDIR/beta_db_outage_state.json``):

  prepare          wait for readiness and snapshot the pre-outage baseline
                   (queue/usage/ledger/attempt counts) into the state file.
  wait-queued      poll until ``queued_total`` reaches ``--target`` (a
                   deterministic barrier: the worker has been stopped, so a
                   durably-admitted request stays QUEUED).
  verify-persisted assert that, after PostgreSQL is restored, the single queued
                   request survived the outage (no loss) and that the request
                   denied during the outage created **no** queue/usage/ledger/
                   attempt records.
  wait-drained     poll until the restored worker drains the queue.
  verify-drained   assert the queued request settled exactly once (usage and
                   ledger each +1, exactly one attempt, zero ``outcome_unknown``).

Usage:
    python scripts/ci/beta_db_outage.py --stage prepare \
        --base-url http://127.0.0.1:<port> --workdir <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import golden_path as gp  # noqa: E402

SECRETS_FILE = "beta_secrets.env"
STATE_FILE = "beta_db_outage_state.json"


def _read_secrets(workdir: Path) -> dict[str, str]:
    return gp._read_secrets(workdir / SECRETS_FILE)


def _admin_client(base_url: str, workdir: Path) -> httpx.Client:
    secrets = _read_secrets(workdir)
    admin_key = secrets.get("AETHERGATE_BETA_ADMIN_KEY")
    if not admin_key:
        gp._fail("missing persisted beta admin key (run --phase main first)")
    return gp._admin_client(base_url, admin_key)


def _snapshot(client: httpx.Client) -> dict:
    """Observe durable queue/settlement/attempt counts (no content/secrets)."""
    requests_total = client.get("/admin/v1/queue/requests?limit=1").json().get("total", 0)
    summary = client.get("/admin/v1/queue/summary").json()
    usage_total = client.get("/admin/v1/usage-records?limit=1").json().get("total", 0)
    ledger_total = client.get("/admin/v1/ledger-entries?limit=1").json().get("total", 0)
    obs = client.get("/admin/v1/observability/summary?window_seconds=3600").json()
    retry = obs["retry"]
    return {
        "requests_total": requests_total,
        "queued_total": summary.get("queued_total", 0),
        "in_flight_total": summary.get("in_flight_total", 0),
        "outcome_unknown_total": summary.get("outcome_unknown_total", 0),
        "usage_total": usage_total,
        "ledger_total": ledger_total,
        "attempts_total": retry["attempted_requests"] + retry["retry_attempts"],
    }


def _wait_queued(client: httpx.Client, target: int, timeout: float) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _snapshot(client)
        if last["queued_total"] >= target:
            return last
        time.sleep(0.5)
    gp._fail(
        f"queued_total never reached {target} within {timeout}s "
        f"(last={json.dumps(last or {})})"
    )


def _wait_drained(client: httpx.Client, timeout: float) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _snapshot(client)
        if last["queued_total"] == 0 and last["in_flight_total"] == 0:
            return last
        time.sleep(0.5)
    gp._fail(
        f"queue did not drain within {timeout}s (last={json.dumps(last or {})})"
    )


def _load_state(workdir: Path) -> dict:
    return json.loads((workdir / STATE_FILE).read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        gp._fail(message)


def stage_prepare(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    gp._wait_ready(args.base_url)
    with _admin_client(args.base_url, workdir) as client:
        snapshot = _snapshot(client)
    (workdir / STATE_FILE).write_text(
        json.dumps({"baseline": snapshot}, indent=2) + "\n", encoding="utf-8"
    )
    gp._log(f"outage baseline snapshot: {json.dumps(snapshot)}")
    return 0


def stage_wait_queued(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    with _admin_client(args.base_url, workdir) as client:
        snapshot = _wait_queued(client, args.target, args.timeout)
    gp._log(f"queued request durably admitted: {json.dumps(snapshot)}")
    return 0


def stage_verify_persisted(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    baseline = _load_state(workdir)["baseline"]
    with _admin_client(args.base_url, workdir) as client:
        snapshot = _snapshot(client)
    _require(
        snapshot["requests_total"] == baseline["requests_total"] + 1,
        "the queued request was not persisted across the outage (or a denied "
        "request was durably admitted)",
    )
    _require(
        snapshot["queued_total"] == 1,
        "persisted queued request was lost during the outage",
    )
    _require(
        snapshot["outcome_unknown_total"] == 0,
        "outage produced an ambiguous outcome_unknown request (double-dispatch risk)",
    )
    _require(
        snapshot["usage_total"] == baseline["usage_total"],
        "denied request created a usage record during the outage",
    )
    _require(
        snapshot["ledger_total"] == baseline["ledger_total"],
        "denied request created a ledger entry during the outage",
    )
    _require(
        snapshot["attempts_total"] == baseline["attempts_total"],
        "denied request created an execution attempt during the outage",
    )
    gp._log(f"outage persisted state verified: {json.dumps(snapshot)}")
    return 0


def stage_wait_drained(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    with _admin_client(args.base_url, workdir) as client:
        snapshot = _wait_drained(client, args.timeout)
    gp._log(f"queue drained after recovery: {json.dumps(snapshot)}")
    return 0


def stage_verify_drained(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir)
    baseline = _load_state(workdir)["baseline"]
    with _admin_client(args.base_url, workdir) as client:
        snapshot = _snapshot(client)
    _require(
        snapshot["requests_total"] == baseline["requests_total"] + 1,
        "request count changed unexpectedly after drain (duplicate/lost request)",
    )
    _require(
        snapshot["queued_total"] == 0 and snapshot["in_flight_total"] == 0,
        "queue was not fully drained after recovery",
    )
    _require(
        snapshot["outcome_unknown_total"] == 0,
        "drained request was not reconciled cleanly (outcome_unknown)",
    )
    _require(
        snapshot["usage_total"] == baseline["usage_total"] + 1,
        "queued request did not settle exactly once (usage records)",
    )
    _require(
        snapshot["ledger_total"] == baseline["ledger_total"] + 1,
        "queued request did not settle exactly once (ledger entries)",
    )
    _require(
        snapshot["attempts_total"] == baseline["attempts_total"] + 1,
        "queued request was dispatched more than once (duplicate attempts)",
    )
    gp._log(f"outage recovery verified (no lost work, no duplicate settlement): "
            f"{json.dumps(snapshot)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        required=True,
        choices=[
            "prepare",
            "wait-queued",
            "verify-persisted",
            "wait-drained",
            "verify-drained",
        ],
    )
    parser.add_argument("--base-url", required=True, help="running API base URL")
    parser.add_argument("--workdir", default="/tmp/ag-beta1-smoke", help="scratch directory")
    parser.add_argument("--target", type=int, default=1, help="queued_total target for wait-queued")
    parser.add_argument("--timeout", type=float, default=120.0, help="barrier poll timeout seconds")
    args = parser.parse_args()
    return {
        "prepare": stage_prepare,
        "wait-queued": stage_wait_queued,
        "verify-persisted": stage_verify_persisted,
        "wait-drained": stage_wait_drained,
        "verify-drained": stage_verify_drained,
    }[args.stage](args)


if __name__ == "__main__":
    sys.exit(main())
