"""AetherGate v2 execution worker.

A separate process from the API. It repeatedly:
  1. runs conservative recovery (expire overdue queued work, reclaim safely
     pre-dispatch leases, surface ambiguous post-dispatch leases);
  2. claims one eligible queued request (FIFO, ``FOR UPDATE SKIP LOCKED``);
  3. reserves endpoint capacity transactionally and records dispatch intent;
  4. performs inference outside any database transaction;
  5. publishes encrypted result/events and settles terminal state.

Bounded PostgreSQL polling is the wake-up mechanism for this phase.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import uuid

from aethergate.scheduler.runtime import build_scheduling_service
from aethergate.scheduler.service import ClaimedWork

logger = logging.getLogger("aethergate.worker")


async def _run_once(worker_id: str, service) -> str:
    """Run a single claim/dispatch/settle cycle; return the outcome keyword."""
    await service.recover()
    outcome = await service.claim_and_reserve(worker_id)
    if isinstance(outcome, ClaimedWork):
        if outcome.stream:
            await service.run_stream(outcome)
        else:
            await service.run_complete(outcome)
        return "dispatched"
    if outcome == "full":
        return "full"
    if outcome == "processed":
        return "processed"
    return "idle"


async def run(worker_id: str, *, once: bool = False, poll_interval: float = 0.2) -> None:
    service = build_scheduling_service()
    while True:
        try:
            result = await _run_once(worker_id, service)
            if once:
                return
            if result == "idle":
                await asyncio.sleep(poll_interval)
            elif result == "full":
                await asyncio.sleep(poll_interval)
            # "dispatched"/"processed" loop again immediately to drain the queue.
        except Exception:  # noqa: BLE001 - the worker must survive transient failures
            logger.exception("worker cycle failed")
            await asyncio.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="AetherGate v2 execution worker")
    parser.add_argument(
        "--worker-id",
        default=None,
        help="stable worker identifier (defaults to a generated UUID)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="process at most one eligible request then exit",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.2,
        help="seconds to sleep when the queue is idle/full",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    worker_id = args.worker_id or uuid.uuid4().hex

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)

    try:
        loop.run_until_complete(
            run(worker_id, once=args.once, poll_interval=args.poll_interval)
        )
    finally:
        loop.close()


if __name__ == "__main__":
    main()
