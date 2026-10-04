"""Development queue-inspection command.

Prints a scheduler queue-state summary (counts by state, endpoints, active
reservations). It never decrypts or prints prompt/completion content.

Usage:
    python -m aethergate.inspect_queue
"""

from __future__ import annotations

import asyncio
import os

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

    print("inference request states:")
    if not counts:
        print("  (none)")
    for state, count in sorted(counts, key=lambda r: r[0] or ""):
        print(f"  {state}: {count}")

    print("\nendpoints (name / max_concurrency):")
    for name, max_concurrency in endpoints:
        print(f"  {name}: {max_concurrency}")

    print(f"\nactive reservations: {active_reservations}")


def main() -> None:
    host = os.environ.get("AETHERGATE_INSPECT_DB_HOST")
    if host:
        os.environ["POSTGRES_HOST"] = host
    asyncio.run(_run())


if __name__ == "__main__":
    main()
