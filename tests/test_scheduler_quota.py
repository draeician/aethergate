"""Shared provider-account request/token quota scheduler tests (DB-gated)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    GenerationParams,
    Message,
    StreamChunk,
    Usage,
)
from aethergate.config import Settings
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability, QuotaMetric, RequestState
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    QuotaLimitId,
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


class QuotaMockAdapter:
    def __init__(
        self,
        *,
        input_tokens: int = 1,
        total_tokens: int = 3,
        error: ProviderError | None = None,
    ) -> None:
        self.input_tokens = input_tokens
        self.total_tokens = total_tokens
        self.error = error
        self.complete_calls = 0

    def estimate_input_tokens(self, request: ChatRequest) -> int:
        return self.input_tokens

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.complete_calls += 1
        if self.error is not None:
            raise self.error
        return CompletionResult(
            content="ok",
            finish_reason="stop",
            usage=Usage(
                prompt_tokens=self.input_tokens,
                completion_tokens=self.total_tokens - self.input_tokens,
                total_tokens=self.total_tokens,
            ),
            upstream_request_id="u1",
        )

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(
            content="ok",
            finish_reason="stop",
            usage=Usage(total_tokens=self.total_tokens),
        )


def _settings(**overrides) -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        upstream_allowlist="192.168.22.50",
        worker_lease_seconds=120.0,
        queue_max_requests=1000,
        **overrides,
    )


async def _seed_provider_account(session) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-ollama"),
            kind="ollama",
            name="ollama",
            capabilities=(Capability.TEXT,),
        ),
    )
    await repository.create_provider_account(
        session,
        domain.ProviderAccount(
            id=ProviderAccountId("acct-ollama"),
            provider_id=ProviderId("prov-ollama"),
            name="ollama-account",
        ),
    )


async def _seed_endpoint_alias_route(
    session,
    *,
    alias_id: str,
    alias_name: str,
    endpoint_id: str,
    group_id: str | None = None,
    default_output_tokens: int | None = None,
    max_concurrency: int = 8,
) -> None:
    endpoint = await repository.get_endpoint(session, EndpointId(endpoint_id))
    if endpoint is None:
        await repository.create_endpoint(
            session,
            domain.Endpoint(
                id=EndpointId(endpoint_id),
                provider_account_id=ProviderAccountId("acct-ollama"),
                name=f"endpoint-{endpoint_id}",
                base_destination="http://192.168.22.50:11434",
                max_concurrency=max_concurrency,
            ),
        )
    alias = await repository.get_model_alias(session, ModelAliasId(alias_id))
    if alias is None:
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
            quota_group_id=QuotaGroupId(group_id) if group_id else None,
            default_output_tokens=default_output_tokens,
        ),
    )


async def _seed_group(
    session,
    *,
    group_id: str,
    request_limits: list[tuple[int, int]] | None = None,
    token_limits: list[tuple[int, int]] | None = None,
) -> None:
    await repository.create_quota_group(
        session,
        domain.QuotaGroup(
            id=QuotaGroupId(group_id),
            provider_account_id=ProviderAccountId("acct-ollama"),
            name=f"group-{group_id}",
        ),
    )
    for i, (limit_units, window_seconds) in enumerate(request_limits or []):
        await repository.create_quota_limit(
            session,
            domain.QuotaLimit(
                id=QuotaLimitId(f"{group_id}-r{i}"),
                quota_group_id=QuotaGroupId(group_id),
                metric=QuotaMetric.REQUESTS,
                limit_units=limit_units,
                window_seconds=window_seconds,
            ),
        )
    for i, (limit_units, window_seconds) in enumerate(token_limits or []):
        await repository.create_quota_limit(
            session,
            domain.QuotaLimit(
                id=QuotaLimitId(f"{group_id}-t{i}"),
                quota_group_id=QuotaGroupId(group_id),
                metric=QuotaMetric.TOKENS,
                limit_units=limit_units,
                window_seconds=window_seconds,
            ),
        )


async def _build(sched_engine, mock, context):
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = _settings()
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: mock,
    )
    factory = async_sessionmaker(sched_engine, expire_on_commit=False)
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )
    return service, factory, encryptor, context


async def _enqueue(service, context, *, alias="gpt-4", max_tokens=None, content="hi"):
    enq = await service.admit_and_enqueue(
        alias_name=alias,
        messages=[Message(role="user", content=content)],
        params=GenerationParams(max_tokens=max_tokens),
        stream=False,
        context=context,
    )
    return enq.request_id


async def _windows(factory, quota_limit_id):
    async with factory() as session:
        return (
            await session.execute(
                select(models.QuotaWindow).where(
                    models.QuotaWindow.quota_limit_id == quota_limit_id
                )
            )
        ).scalars().all()


async def _reservations(factory, request_id):
    async with factory() as session:
        return (
            await session.execute(
                select(models.QuotaReservation).where(
                    models.QuotaReservation.request_id == request_id
                )
            )
        ).scalars().all()


# --- pure fixed-window boundary ---------------------------------------------


def test_fixed_window_start_boundary():
    assert sched_repo.fixed_window_start(
        datetime(2026, 10, 4, 12, 0, 30, tzinfo=UTC), 60
    ) == datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
    assert sched_repo.fixed_window_start(
        datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC), 60
    ) == datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
    assert sched_repo.fixed_window_start(
        datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC), 86400
    ) == datetime(2026, 10, 4, 0, 0, 0, tzinfo=UTC)


# --- route / group scope consistency ----------------------------------------


async def test_route_cannot_reference_other_accounts_quota_group(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-a", request_limits=[(1, 60)])
            await repository.create_provider_account(
                session,
                domain.ProviderAccount(
                    id=ProviderAccountId("acct-other"),
                    provider_id=ProviderId("prov-ollama"),
                    name="other-account",
                ),
            )
            await repository.create_endpoint(
                session,
                domain.Endpoint(
                    id=EndpointId("ep-b"),
                    provider_account_id=ProviderAccountId("acct-other"),
                    name="endpoint-ep-b",
                    base_destination="http://192.168.22.50:11434",
                    max_concurrency=1,
                ),
            )
            await repository.create_model_alias(
                session,
                domain.ModelAlias(
                    id=ModelAliasId("alias-b"),
                    name="b",
                    capabilities=(Capability.TEXT,),
                ),
            )
            # qg-a belongs to acct-ollama, but this route targets acct-other.
            with pytest.raises(ValueError, match="provider account"):
                await repository.create_route_binding(
                    session,
                    domain.RouteBinding(
                        id=RouteBindingId("rb-b"),
                        model_alias_id=ModelAliasId("alias-b"),
                        endpoint_id=EndpointId("ep-b"),
                        provider_account_id=ProviderAccountId("acct-other"),
                        upstream_model="qwen3.8-2b-distill:Q6_K",
                        quota_group_id=QuotaGroupId("qg-a"),
                    ),
                )


# --- request quota ----------------------------------------------------------


async def test_request_quota_limits_shared_across_aliases(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", request_limits=[(2, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-1"
            )
            await _seed_endpoint_alias_route(
                session, alias_id="alias-b", alias_name="b", endpoint_id="ep-a", group_id="qg-1"
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)

    ids = []
    for alias in ("a", "b", "a", "b"):
        ids.append(await _enqueue(service, context, alias=alias))

    first = await service.claim_and_reserve("w1")
    second = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None
    assert not isinstance(second, str) and second is not None
    third = await service.claim_and_reserve("w1")
    assert third == "quota"

    await service.run_complete(first)
    await service.run_complete(second)

    # Request units remain committed after completion; quota stays exhausted.
    assert await service.claim_and_reserve("w1") == "quota"

    windows = await _windows(factory, "qg-1-r0")
    assert windows[0].committed_units == 2


async def test_quota_exhaustion_does_not_reserve_endpoint_slot(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", request_limits=[(1, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-1"
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    await _enqueue(service, context, alias="a")
    await _enqueue(service, context, alias="a")

    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None
    second = await service.claim_and_reserve("w1")
    assert second == "quota"

    async with factory() as session:
        active = await sched_repo.count_active_reservations(session, "ep-a")
    assert active == 1  # only the first request holds a physical slot


async def test_multiple_request_windows_reserved_together(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(
                session, group_id="qg-1", request_limits=[(1, 60), (1, 86400)]
            )
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-1"
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    await _enqueue(service, context, alias="a")
    await _enqueue(service, context, alias="a")

    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None
    # Both the 60s and 86400s request windows are reserved; exhausting the short
    # window alone blocks the second request.
    assert await service.claim_and_reserve("w1") == "quota"

    # First request created a reservation against both limits.
    res = await _reservations(factory, first.request_id)
    assert {r.quota_limit_id for r in res} == {"qg-1-r0", "qg-1-r1"}


async def test_two_workers_cannot_exceed_request_limit(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", request_limits=[(2, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-1"
            )
            context = await ensure_dev_identity(session)

    service, _factory, _enc, context = await _build(sched_engine, mock, context)
    for _ in range(6):
        await _enqueue(service, context, alias="a")

    seen: list[str] = []

    async def worker(wid):
        while True:
            outcome = await service.claim_and_reserve(wid)
            if isinstance(outcome, str) or outcome is None:
                return
            seen.append(outcome.request_id)

    await asyncio.gather(worker("w1"), worker("w2"), worker("w3"))
    assert len(seen) == 2


# --- token quota ------------------------------------------------------------


async def test_token_reservation_before_dispatch(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5, total_tokens=9)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", token_limits=[(1000, 60)])
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    request_id = await _enqueue(service, context, alias="a")

    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    res = await _reservations(factory, request_id)
    token = [r for r in res if r.metric == QuotaMetric.TOKENS][0]
    # input(5) + default output(16) = 21 reserved before dispatch
    assert token.reserved_units == 21
    assert token.committed_units == 0


async def test_successful_token_settlement_to_actual(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5, total_tokens=9)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", token_limits=[(1000, 60)])
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    request_id = await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    res = await _reservations(factory, request_id)
    token = [r for r in res if r.metric == QuotaMetric.TOKENS][0]
    assert token.state == "committed"
    assert token.committed_units == 9  # settled to actual total usage

    windows = await _windows(factory, "qg-1-t0")
    assert windows[0].committed_units == 9
    assert windows[0].reserved_units == 0


async def test_over_reservation_releases_unused_units(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5, total_tokens=6)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", token_limits=[(1000, 60)])
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    request_id = await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    res = await _reservations(factory, request_id)
    token = [r for r in res if r.metric == QuotaMetric.TOKENS][0]
    assert token.committed_units == 6  # actual < reserved(21); unused released
    windows = await _windows(factory, "qg-1-t0")
    assert windows[0].committed_units == 6
    assert windows[0].reserved_units == 0


async def test_actual_usage_greater_than_reservation_recorded_honestly(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=1, total_tokens=500)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", token_limits=[(100, 60)])
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    windows = await _windows(factory, "qg-1-t0")
    # 500 actual exceeds limit 100 and is recorded honestly (not truncated).
    assert windows[0].committed_units == 500


async def test_dispatched_failure_unknown_usage_commits_reservation(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(
        input_tokens=5, error=ProviderError("upstream failed", status_code=500)
    )
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", token_limits=[(1000, 60)])
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    request_id = await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    res = await _reservations(factory, request_id)
    token = [r for r in res if r.metric == QuotaMetric.TOKENS][0]
    # Unknown usage: reserved amount (21) committed conservatively, not released.
    assert token.committed_units == 21
    windows = await _windows(factory, "qg-1-t0")
    assert windows[0].committed_units == 21
    assert windows[0].reserved_units == 0


async def test_pre_dispatch_cancellation_releases_token(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", token_limits=[(1000, 60)])
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    request_id = await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    # Simulate the pre-dispatch window: dispatch intent not yet durable, but the
    # token reservation is still held.
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == request_id)
                .values(state=RequestState.RESERVED)
            )

    await service.request_cancellation(request_id)

    res = await _reservations(factory, request_id)
    token = [r for r in res if r.metric == QuotaMetric.TOKENS][0]
    assert token.state == "released"
    assert token.reserved_units == 0
    windows = await _windows(factory, "qg-1-t0")
    assert windows[0].reserved_units == 0


async def test_outcome_unknown_keeps_token_reservation(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", token_limits=[(1000, 60)])
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    request_id = await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    # Simulate dispatch-then-lease-expiry (post-dispatch).
    async with factory() as session:
        async with session.begin():
            row = await session.get(models.InferenceRequest, outcome.request_id)
            row.state = RequestState.DISPATCHED
            row.lease_expires_at = datetime(2020, 1, 1, tzinfo=UTC)

    await service.recover()

    async with factory() as session:
        row = await sched_repo.get_request(session, request_id)
        assert row.state == RequestState.OUTCOME_UNKNOWN

    # Token reservation is retained (still reserved), not freed.
    res = await _reservations(factory, request_id)
    token = [r for r in res if r.metric == QuotaMetric.TOKENS][0]
    assert token.state == "reserved"
    windows = await _windows(factory, "qg-1-t0")
    assert windows[0].reserved_units == 21


async def test_request_larger_than_empty_window_fails(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            # Token limit 10 cannot fit input(5) + output(16) = 21.
            await _seed_group(session, group_id="qg-1", token_limits=[(10, 60)])
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    request_id = await _enqueue(service, context, alias="a")

    outcome = await service.claim_and_reserve("w1")
    assert outcome == "processed"

    async with factory() as session:
        row = await sched_repo.get_request(session, request_id)
        assert row.state == RequestState.FAILED
        assert row.error_code == "quota_request_too_large"


async def test_unbounded_output_rejected(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", token_limits=[(1000, 60)])
            # No default_output_tokens on the route.
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-1"
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    request_id = await _enqueue(service, context, alias="a", max_tokens=None)

    outcome = await service.claim_and_reserve("w1")
    assert outcome == "processed"

    async with factory() as session:
        row = await sched_repo.get_request(session, request_id)
        assert row.state == RequestState.FAILED
        assert row.error_code == "quota_unbounded_output"


# --- provider 429 cooldown --------------------------------------------------


async def test_429_cooldown_sets_group(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(
        error=ProviderError(
            "rate limited", status_code=429, retry_after_seconds=120.0
        )
    )
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", request_limits=[(10, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-1"
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    async with factory() as session:
        group = await session.get(models.QuotaGroup, "qg-1")
        assert group.cooldown_until is not None

    # Future work for the group stays queued during cooldown.
    await _enqueue(service, context, alias="a")
    assert await service.claim_and_reserve("w1") == "quota"


async def test_cooldown_expiry(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", request_limits=[(10, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-1"
            )
            context = await ensure_dev_identity(session)
            # Cooldown already expired.
            group = await session.get(models.QuotaGroup, "qg-1")
            group.cooldown_until = datetime(2020, 1, 1, tzinfo=UTC)

    service, _factory, _enc, context = await _build(sched_engine, mock, context)
    await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None


async def test_saturated_quota_group_does_not_block_unrelated_group(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-a", request_limits=[(1, 60)])
            await _seed_group(session, group_id="qg-b", request_limits=[(1, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-a"
            )
            await _seed_endpoint_alias_route(
                session, alias_id="alias-b", alias_name="b", endpoint_id="ep-b", group_id="qg-b"
            )
            context = await ensure_dev_identity(session)

    service, _factory, _enc, context = await _build(sched_engine, mock, context)
    # Exhaust group A with one request.
    await _enqueue(service, context, alias="a")
    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None

    # A second A request is quota-blocked, but an unrelated B request dispatches.
    await _enqueue(service, context, alias="a")
    b_id = await _enqueue(service, context, alias="b")

    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    assert outcome.request_id == b_id
