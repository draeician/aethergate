"""Worker cooperative-shutdown tests (no database required)."""

from __future__ import annotations

import asyncio

import pytest

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
