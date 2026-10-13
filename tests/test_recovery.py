"""Deterministic recovery / fault-injection release gates (DB-gated).

These tests prove the durable-state recovery invariants required for a Beta 1
release on top of the lease/fencing semantics already covered in
``tests/test_scheduler_hardening.py``. Each test reconstructs a *fresh*
``SchedulingService`` over the same PostgreSQL database to simulate an API or
worker process restart, proving that durable work, reservations, accounting, and
recovery survive process boundaries.

Provider failure injection uses a deterministic adapter that raises a
:class:`ProviderError` (no hidden adapter/SDK retry exists in the pinned adapter).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    GenerationParams,
    Message,
    Usage,
)
from aethergate.config import Settings
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability, RequestState
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.egress import DestinationPolicy
from aethergate.encryption import QueueEncryptor
from aethergate.errors import ProviderError
from aethergate.inference.service import InferenceService
from aethergate.persistence import models, repository
from aethergate.scheduler import repository as sched_repo
from aethergate.scheduler.service import SchedulingService
from aethergate.secrets import EnvSecretResolver
from db_helpers import TEST_DATABASE_URL, reset_schema

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)


class _ProviderFailureAdapter:
    """Deterministic adapter that raises a provider error on every completion."""

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        raise ProviderError("upstream timeout")


class _OkAdapter:
    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        return CompletionResult(
            content="ok",
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id="upstream-1",
        )


async def _seed_endpoint(
    session,
    *,
    endpoint_id: str,
    alias_id: str,
    alias_name: str,
    max_concurrency: int,
) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-ollama"), kind="ollama", name="prov-ollama",
            capabilities=(Capability.TEXT,),
        ),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId("acct-ollama"),
            provider_id=ProviderId("prov-ollama"),
            name="acct-ollama",
        ),
    )
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId(endpoint_id),
            provider_account_id=ProviderAccountId("acct-ollama"),
            name=endpoint_id,
            base_destination="http://192.168.22.50:11434",
            max_concurrency=max_concurrency,
        ),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId(alias_id), name=alias_name, capabilities=(Capability.TEXT,)
        ),
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId(f"rb-{alias_id}"),
            model_alias_id=ModelAliasId(alias_id),
            endpoint_id=EndpointId(endpoint_id),
            provider_account_id=ProviderAccountId("acct-ollama"),
            upstream_model="qwen3.8-2b-distill:Q6_K",
        ),
    )


async def _build_service(
    engine,
    *,
    adapter,
    max_concurrency: int = 2,
    **overrides,
) -> tuple[SchedulingService, object, QueueEncryptor]:
    """Create a SchedulingService over ``engine`` after seeding a fresh schema."""
    await reset_schema(engine)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_endpoint(
                session,
                endpoint_id="ep-ollama",
                alias_id="alias-gpt4",
                alias_name="gpt-4",
                max_concurrency=max_concurrency,
            )
            await ensure_dev_identity(session)

    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        upstream_allowlist="192.168.22.50",
        oidc_enabled=False,
        allow_inference_auth_bypass=False,
        bootstrap_token=None,
        **overrides,
    )
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: adapter,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )
    return service, factory, encryptor


def _new_service(
    engine, encryptor: QueueEncryptor, settings: Settings, adapter
) -> SchedulingService:
    """A fresh service instance (fresh session factory, same queue key) over the
    same database — the analogue of an API/worker process restart."""
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: adapter,
    )
    return SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        settings=settings,
    )


async def _enqueue(service, context, n: int = 1) -> list[str]:
    ids = []
    for i in range(n):
        enq = await service.admit_and_enqueue(
            alias_name="gpt-4",
            messages=[Message(role="user", content=f"msg-{i}")],
            params=GenerationParams(),
            stream=False,
            context=context,
        )
        ids.append(enq.request_id)
    return ids


# 1. worker restart preserves queued work and a new worker dispatches it


async def test_worker_restart_preserves_queued_work(sched_engine):
    service, factory, encryptor = await _build_service(sched_engine, adapter=_OkAdapter())
    async with factory() as session:
        context = await ensure_dev_identity(session)

    ids = await _enqueue(service, context, 3)

    # First worker claims one request, dispatches it, and is "killed": its lease
    # is forced into the past and never renewed, so the request goes
    # outcome_unknown on recovery (holding its slot conservatively). The other
    # two requests are never claimed.
    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None

    past = datetime(2020, 1, 1, tzinfo=UTC)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == first.request_id)
                .values(lease_expires_at=past)
            )

    await service.recover()

    # The dead worker's request is conservatively outcome_unknown; the still-queued
    # work is NOT lost.
    async with factory() as session:
        assert (await sched_repo.get_request(session, ids[0])).state == RequestState.OUTCOME_UNKNOWN
        assert (await sched_repo.get_request(session, ids[1])).state == RequestState.QUEUED
        assert (await sched_repo.get_request(session, ids[2])).state == RequestState.QUEUED

    # A "restarted" worker (fresh service instance, same queue key) drains the
    # remaining queued work.
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        upstream_allowlist="192.168.22.50",
        oidc_enabled=False,
        allow_inference_auth_bypass=False,
        bootstrap_token=None,
    )
    restarted = _new_service(sched_engine, encryptor, settings, _OkAdapter())
    await restarted.recover()

    second = await restarted.claim_and_reserve("w3")
    assert not isinstance(second, str) and second is not None
    assert second.request_id == ids[1]
    await restarted.run_complete(second)

    third = await restarted.claim_and_reserve("w3")
    assert not isinstance(third, str) and third is not None
    assert third.request_id == ids[2]
    await restarted.run_complete(third)

    async with factory() as session:
        assert (await sched_repo.get_request(session, ids[1])).state == RequestState.SUCCEEDED
        assert (await sched_repo.get_request(session, ids[2])).state == RequestState.SUCCEEDED


# 2. API restart preserves durable queued + reserved + accounting state


async def test_api_restart_preserves_durable_state(sched_engine):
    service, factory, encryptor = await _build_service(sched_engine, adapter=_OkAdapter())
    async with factory() as session:
        context = await ensure_dev_identity(session)

    ids = await _enqueue(service, context, 2)
    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None

    # Simulate an API process restart: a brand-new service over the same DB must
    # observe the same queued request and the reserved request with no phantom
    # reservations or lost work.
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        app_env="dev",
        upstream_allowlist="192.168.22.50",
        oidc_enabled=False,
        allow_inference_auth_bypass=False,
        bootstrap_token=None,
    )
    restarted = _new_service(sched_engine, encryptor, settings, _OkAdapter())

    async with factory() as session:
        assert await sched_repo.count_queued(session) == 1
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 1
        assert (await sched_repo.get_request(session, ids[0])).state == RequestState.DISPATCHED

    # The restarted worker can settle the reserved work and drain the queue.
    await restarted.run_complete(first)

    second = await restarted.claim_and_reserve("w2")
    assert not isinstance(second, str) and second is not None
    assert second.request_id == ids[1]
    await restarted.run_complete(second)

    async with factory() as session:
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 0


# 3. provider failure settles failed, releases reservation, no double settlement


async def test_provider_failure_settles_failed_without_retry(sched_engine):
    service, factory, _encryptor = await _build_service(
        sched_engine, adapter=_ProviderFailureAdapter(), max_concurrency=1
    )
    async with factory() as session:
        context = await ensure_dev_identity(session)

    ids = await _enqueue(service, context, 1)
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None

    await service.run_complete(claim)

    async with factory() as session:
        row = await sched_repo.get_request(session, ids[0])
        assert row.state == RequestState.FAILED
        assert row.error_code == "upstream timeout"
        assert await sched_repo.count_active_reservations(session, "ep-ollama") == 0
        # Exactly one attempt: the adapter has no hidden retry path.
        attempts = (
            await session.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.request_id == ids[0]
                )
            )
        ).scalars().all()
        assert len(attempts) == 1
