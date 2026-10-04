"""AetherGate v2 execution worker.

A separate process from the API. It repeatedly:
  1. runs conservative recovery (expire overdue queued work, reclaim safely
     pre-dispatch leases, surface ambiguous post-dispatch leases);
  2. claims one eligible queued request (FIFO per endpoint, ``FOR UPDATE SKIP
     LOCKED``);
  3. reserves endpoint capacity transactionally and records dispatch intent;
  4. performs inference outside any database transaction, renewing its lease;
  5. publishes encrypted result/events and settles terminal state.

Bounded PostgreSQL polling is the wake-up mechanism for this phase.

Shutdown is cooperative: SIGINT/SIGTERM stop new claiming, but an in-flight
execution is allowed to finish (bounded by the inference timeout). A hard stop
that abandons dispatched work leaves the lease to expire, which recovery then
surfaces conservatively as ``outcome_unknown``.
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
    if outcome == "quota":
        return "quota"
    if outcome == "budget":
        return "budget"
    return "idle"


async def run(
    worker_id: str,
    *,
    once: bool = False,
    poll_interval: float = 0.2,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    service = build_scheduling_service()
    while True:
        if shutdown_event is not None and shutdown_event.is_set():
            logger.info("worker %s received shutdown; no longer claiming", worker_id)
            return
        try:
            result = await _run_once(worker_id, service)
            if once:
                return
            if result in ("idle", "full", "quota", "budget"):
                await asyncio.sleep(poll_interval)
            # "dispatched"/"processed" loop again immediately to drain the queue.
        except asyncio.CancelledError:
            raise
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
    shutdown_event = asyncio.Event()

    def _request_shutdown() -> None:
        logger.info("worker %s received termination signal", worker_id)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    try:
        loop.run_until_complete(
            run(
                worker_id,
                once=args.once,
                poll_interval=args.poll_interval,
                shutdown_event=shutdown_event,
            )
        )
    finally:
        loop.close()


if __name__ == "__main__":
    main()
