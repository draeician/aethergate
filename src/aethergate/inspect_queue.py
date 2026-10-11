"""Development queue-inspection command.

Prints a scheduler queue-state summary (counts by state, endpoints, active
reservations). It never decrypts or prints prompt/completion content.

Usage:
    python -m aethergate.inspect_queue
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from sqlalchemy import func, select

from aethergate.persistence import models
from aethergate.persistence.db import get_session_factory


async def _run() -> None:
    async with get_session_factory()() as session:
        counts = (
            await session.execute(
                select(models.InferenceRequest.state, func.count())
                .group_by(models.InferenceRequest.state)
            )
        ).all()
        active_reservations = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(models.Reservation)
                    .where(models.Reservation.released_at.is_(None))
                )
            ).scalar_one()
        )
        endpoints = (
            await session.execute(
                select(models.Endpoint.name, models.Endpoint.max_concurrency)
                .order_by(models.Endpoint.name)
            )
        ).all()
        quota_windows = (
            await session.execute(
                select(
                    models.QuotaLimit.metric,
                    models.QuotaLimit.limit_units,
                    models.QuotaLimit.window_seconds,
                    models.QuotaWindow.window_start,
                    models.QuotaWindow.committed_units,
                    models.QuotaWindow.reserved_units,
                    models.QuotaGroup.name,
                    models.QuotaGroup.cooldown_until,
                )
                .join(models.QuotaLimit, models.QuotaWindow.quota_limit_id == models.QuotaLimit.id)
                .join(models.QuotaGroup, models.QuotaLimit.quota_group_id == models.QuotaGroup.id)
                .order_by(models.QuotaGroup.name, models.QuotaLimit.metric)
            )
        ).all()
        budget_windows = (
            await session.execute(
                select(
                    models.ProjectBudgetPolicy.name,
                    models.ProjectBudgetPolicy.currency,
                    models.ProjectBudgetPolicy.limit_amount,
                    models.ProjectBudgetPolicy.window_seconds,
                    models.BudgetWindow.window_start,
                    models.BudgetWindow.committed_amount,
                    models.BudgetWindow.reserved_amount,
                )
                .join(
                    models.BudgetWindow,
                    models.BudgetWindow.budget_policy_id == models.ProjectBudgetPolicy.id,
                )
                .order_by(models.ProjectBudgetPolicy.name, models.BudgetWindow.window_start)
            )
        ).all()
        waiting = (
            await session.execute(
                select(
                    models.InferenceRequest.id,
                    models.InferenceRequest.state,
                    models.InferenceRequest.wait_reason,
                    models.InferenceRequest.wait_limit_id,
                    models.InferenceRequest.wait_limit_metric,
                )
                .where(
                    models.InferenceRequest.state == "queued",
                    models.InferenceRequest.wait_reason.is_not(None),
                )
                .order_by(models.InferenceRequest.queued_at)
            )
        ).all()
        recent_requests = (
            await session.execute(
                select(
                    models.InferenceRequest.id,
                    models.InferenceRequest.state,
                    models.InferenceRequest.price_snapshot_id,
                    models.UsageRecord.id,
                )
                .outerjoin(
                    models.UsageRecord,
                    models.UsageRecord.request_id == models.InferenceRequest.id,
                )
                .order_by(models.InferenceRequest.created_at.desc())
                .limit(50)
            )
        ).all()

    print("inference request states:")
    if not counts:
        print("  (none)")
    for state, count in sorted(counts, key=lambda r: r[0] or ""):
        print(f"  {state}: {count}")

    print("\nendpoints (name / max_concurrency):")
    for name, max_concurrency in endpoints:
        print(f"  {name}: {max_concurrency}")

    print(f"\nactive reservations: {active_reservations}")

    print("\nquota windows (group / metric / used / limit / start / cooldown):")
    if not quota_windows:
        print("  (none)")
    for metric, limit, _window, start, committed, reserved, group, cooldown in quota_windows:
        cd = cooldown.isoformat() if cooldown is not None else "-"
        print(
            f"  {group} {metric} {committed + reserved}/{limit} "
            f"start={start.isoformat()} committed={committed} "
            f"reserved={reserved} cooldown_until={cd}"
        )

    print("\nproject budgets (name / committed / reserved / headroom / limit / reset):")
    if not budget_windows:
        print("  (none)")
    for name, _currency, limit, window_seconds, start, committed, reserved in budget_windows:
        headroom = limit - committed - reserved
        reset = start + timedelta(seconds=window_seconds)
        print(
            f"  {name} committed={committed} reserved={reserved} "
            f"headroom={headroom} limit={limit} reset={reset.isoformat()}"
        )

    print("\nqueued requests with a limiting reason:")
    if not waiting:
        print("  (none)")
    for request_id, _state, reason, limit_id, metric in waiting:
        detail = f" limit={limit_id}/{metric}" if limit_id else ""
        print(f"  {request_id}: {reason}{detail}")

    print("\nrecent requests (id / state / snapshot / usage_record):")
    if not recent_requests:
        print("  (none)")
    for request_id, state, snapshot_id, usage_record_id in recent_requests:
        print(
            f"  {request_id}: {state} snapshot={snapshot_id or '-'} "
            f"usage_record={usage_record_id or '-'}"
        )


def main() -> None:
    host = os.environ.get("AETHERGATE_INSPECT_DB_HOST")
    if host:
        os.environ["POSTGRES_HOST"] = host
    asyncio.run(_run())


if __name__ == "__main__":
    main()
