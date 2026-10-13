"""Worker cooperative-shutdown and liveness-heartbeat tests (no database)."""

from __future__ import annotations

import asyncio

import pytest

from aethergate.scheduler.service import ClaimedWork
from aethergate.worker import run


class FakeService:
    def __init__(self) -> None:
        self.recover_calls = 0
        self.claim_calls = 0

    async def recover(self) -> None:
        self.recover_calls += 1

    async def claim_and_reserve(self, worker_id: str):
        self.claim_calls += 1
        return None


class SlowDispatchService:
    """A service that blocks inside a real dispatch for ``block_seconds``.

    The first claim returns a :class:`ClaimedWork` (so ``run`` enters the
    non-stream ``run_complete`` path); subsequent claims return ``None`` so the
    worker drains to idle after the single blocking dispatch finishes.
    """

    def __init__(self, block_seconds: float) -> None:
        self.block_seconds = block_seconds
        self.dispatches = 0

    async def recover(self) -> None:
        pass

    async def claim_and_reserve(self, worker_id: str):
        self.dispatches += 1
        if self.dispatches > 1:
            return None
        return ClaimedWork(
            request_id="r1",
            attempt_id="a1",
            reservation_id="rsv1",
            worker_id=worker_id,
            fencing_token=1,
            endpoint_id="ep1",
            public_alias="gpt-4",
            stream=False,
            operation="chat",
            prepared=None,
        )

    async def run_complete(self, outcome) -> None:
        await asyncio.sleep(self.block_seconds)


@pytest.fixture
def fake_service(monkeypatch):
    svc = FakeService()
    monkeypatch.setattr("aethergate.worker.build_scheduling_service", lambda: svc)
    return svc


async def test_shutdown_event_stops_new_claiming(fake_service):
    event = asyncio.Event()
    event.set()
    await run("w-shutdown", shutdown_event=event, poll_interval=0.01)
    assert fake_service.claim_calls == 0
    assert fake_service.recover_calls == 0


async def test_once_mode_processes_a_single_cycle(fake_service):
    await run("w-once", once=True, poll_interval=0.01)
    assert fake_service.claim_calls == 1
    assert fake_service.recover_calls == 1


async def test_heartbeat_refreshes_during_long_dispatch(monkeypatch, tmp_path):
    hb = tmp_path / "worker-heartbeat"
    svc = SlowDispatchService(block_seconds=0.5)
    monkeypatch.setattr("aethergate.worker.build_scheduling_service", lambda: svc)

    shutdown = asyncio.Event()
    task = asyncio.create_task(
        run(
            "w-heartbeat",
            shutdown_event=shutdown,
            poll_interval=0.01,
            heartbeat_file=str(hb),
            heartbeat_seconds=0.02,
        )
    )
    # Wait until the worker has claimed and entered the blocking dispatch.
    for _ in range(200):
        if svc.dispatches >= 1:
            break
        await asyncio.sleep(0.005)
    assert svc.dispatches >= 1

    # While the dispatch is still blocked, the heartbeat file must keep advancing
    # (a separate event-loop task, not gated on the claim/dispatch loop).
    mtime_start = hb.stat().st_mtime_ns
    await asyncio.sleep(0.2)
    assert svc.dispatches == 1, "dispatch finished before the liveness window elapsed"
    assert hb.stat().st_mtime_ns > mtime_start, "heartbeat did not refresh during dispatch"

    shutdown.set()
    await asyncio.wait_for(task, timeout=5.0)


async def test_heartbeat_clean_stop_and_restart_without_leak(monkeypatch, tmp_path):
    hb = tmp_path / "worker-heartbeat"
    monkeypatch.setattr("aethergate.worker.build_scheduling_service", lambda: FakeService())

    baseline = set(asyncio.all_tasks())
    for _ in range(2):
        shutdown = asyncio.Event()
        task = asyncio.create_task(
            run(
                "w-hb-stop",
                shutdown_event=shutdown,
                poll_interval=0.01,
                heartbeat_file=str(hb),
                heartbeat_seconds=0.01,
            )
        )
        await asyncio.sleep(0.03)
        assert hb.exists()
        shutdown.set()
        await asyncio.wait_for(task, timeout=5.0)

    await asyncio.sleep(0)
    leftover = set(asyncio.all_tasks()) - baseline
    assert not leftover, "heartbeat task leaked after clean shutdown/restart"
