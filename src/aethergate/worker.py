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
from pathlib import Path

from aethergate.config import get_settings
from aethergate.logging_config import configure_logging_from_settings, log_event
from aethergate.scheduler.runtime import build_scheduling_service
from aethergate.scheduler.service import ClaimedWork

logger = logging.getLogger("aethergate.worker")


async def _run_once(worker_id: str, service) -> str:
    """Run a single claim/dispatch/settle cycle; return the outcome keyword."""
    await service.recover()
    outcome = await service.claim_and_reserve(worker_id)
    if isinstance(outcome, ClaimedWork):
        # Safe correlation only: gateway request id + attempt/endpoint IDs, and
        # the upstream provider request ID is deliberately NOT logged (distinct
        # from the gateway request id and never persisted as correlation).
        log_event(
            logger,
            "worker.dispatch",
            request_id=outcome.request_id,
            attempt_id=outcome.attempt_id,
            endpoint_id=outcome.endpoint_id,
            stream=outcome.stream,
            worker_id=worker_id,
        )
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
    if outcome == "paused":
        return "paused"
    return "idle"


async def _heartbeat_loop(
    worker_id: str,
    heartbeat_file: str,
    heartbeat_seconds: float,
) -> None:
    """Refresh the liveness heartbeat file on a fixed cadence.

    Runs as a separate event-loop task so a long-running dispatch — or a graceful
    shutdown while an in-flight request drains — cannot make a busy but healthy
    worker appear stale. Because this task is scheduled on the same event loop as
    the dispatch, a genuinely hung worker (CPU-blocked, not awaiting I/O) also
    starves it, so a real stall is still detected by the container healthcheck.
    """
    Path(heartbeat_file).parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            Path(heartbeat_file).touch()
        except OSError:
            logger.warning("worker %s could not update heartbeat file", worker_id)
        await asyncio.sleep(heartbeat_seconds)


async def run(
    worker_id: str,
    *,
    once: bool = False,
    poll_interval: float = 0.2,
    shutdown_event: asyncio.Event | None = None,
    heartbeat_file: str | None = None,
    heartbeat_seconds: float = 30.0,
) -> None:
    service = build_scheduling_service()
    heartbeat_task: asyncio.Task | None = None
    if heartbeat_file:
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(worker_id, heartbeat_file, heartbeat_seconds)
        )
    try:
        while True:
            if shutdown_event is not None and shutdown_event.is_set():
                logger.info("worker %s received shutdown; no longer claiming", worker_id)
                return
            try:
                result = await _run_once(worker_id, service)
                if once:
                    return
                if result in ("idle", "full", "quota", "budget", "paused"):
                    await asyncio.sleep(poll_interval)
                # "dispatched"/"processed" loop again immediately to drain the queue.
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must survive transient failures
                logger.exception("worker cycle failed")
                await asyncio.sleep(poll_interval)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass


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

    configure_logging_from_settings()
    worker_id = args.worker_id or uuid.uuid4().hex
    settings = get_settings()

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
                heartbeat_file=settings.worker_heartbeat_file,
                heartbeat_seconds=settings.worker_heartbeat_seconds,
            )
        )
    finally:
        loop.close()


if __name__ == "__main__":
    main()
