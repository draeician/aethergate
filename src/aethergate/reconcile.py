"""Development/internal reconciliation of ``outcome_unknown`` requests.

When a dispatched request's lease expires (worker death/loss), it is marked
``outcome_unknown`` and its physical slot is held. Only an explicit operator
action may resolve it. This CLI is the development path for that action; the
full admin API/UI is a later milestone.

Usage:
    python -m aethergate.reconcile list
    python -m aethergate.reconcile resolve <request_id> \\
        --disposition failed|cancelled|succeeded --by <operator>

It never prints prompt/completion content.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from aethergate.scheduler.runtime import build_scheduling_service


async def _list() -> None:
    service = build_scheduling_service()
    rows = await service.list_outcome_unknown()
    if not rows:
        print("no outcome_unknown requests")
        return
    for row in rows:
        print(
            "  request_id={request_id} endpoint_id={endpoint_id} "
            "worker_id={worker_id} started_at={started_at} "
            "lease_expires_at={lease_expires_at}".format(**row)
        )


async def _resolve(request_id: str, disposition: str, operator: str) -> None:
    service = build_scheduling_service()
    ok = await service.reconcile(request_id, disposition, operator)
    if not ok:
        raise SystemExit(f"request {request_id!r} is not outcome_unknown or does not exist")
    print(f"reconciled {request_id} -> {disposition} (by {operator})")


def main() -> None:
    host = os.environ.get("AETHERGATE_INSPECT_DB_HOST")
    if host:
        os.environ["POSTGRES_HOST"] = host

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list outcome_unknown requests (no content)")

    resolve = sub.add_parser("resolve", help="reconcile one outcome_unknown request")
    resolve.add_argument("request_id")
    resolve.add_argument(
        "--disposition", required=True, choices=["failed", "cancelled", "succeeded"]
    )
    resolve.add_argument("--by", required=True, help="operator identity for the audit record")

    args = parser.parse_args()
    if args.command == "list":
        asyncio.run(_list())
    else:
        asyncio.run(_resolve(args.request_id, args.disposition, args.by))


if __name__ == "__main__":
    main()
