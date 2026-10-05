"""Shared provider-account request/token quota scheduler tests (DB-gated)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
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
from aethergate.errors import CatalogParentMismatchError, ProviderError
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
        estimate_available: bool = True,
    ) -> None:
        self.input_tokens = input_tokens
        self.total_tokens = total_tokens
        self.error = error
        self.estimate_available = estimate_available
        self.complete_calls = 0

    def estimate_input_tokens(self, request: ChatRequest) -> int | None:
        return self.input_tokens if self.estimate_available else None

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
            with pytest.raises(CatalogParentMismatchError, match="provider account"):
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


# --- all-or-nothing admission (endpoint full / inactive) --------------------


async def _snapshot_windows(factory) -> set[tuple[str, int, int]]:
    async with factory() as session:
        rows = (
            await session.execute(select(models.QuotaWindow))
        ).scalars().all()
        return {(w.quota_limit_id, w.committed_units, w.reserved_units) for w in rows}


async def test_endpoint_full_leaves_no_quota_reservation(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(
                session,
                group_id="qg-1",
                request_limits=[(10, 60)],
                token_limits=[(10000, 60)],
            )
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
                max_concurrency=1,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    await _enqueue(service, context, alias="a")
    b_id = await _enqueue(service, context, alias="a")

    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None
    before = await _snapshot_windows(factory)

    # Endpoint is full (max_concurrency=1); B must not reserve any quota.
    assert await service.claim_and_reserve("w1") == "full"
    assert await _reservations(factory, b_id) == []
    assert await _snapshot_windows(factory) == before


async def test_repeated_endpoint_full_claims_do_not_change_windows(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(
                session,
                group_id="qg-1",
                request_limits=[(10, 60)],
                token_limits=[(10000, 60)],
            )
            await _seed_endpoint_alias_route(
                session,
                alias_id="alias-a",
                alias_name="a",
                endpoint_id="ep-a",
                group_id="qg-1",
                default_output_tokens=16,
                max_concurrency=1,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)
    await _enqueue(service, context, alias="a")
    b_id = await _enqueue(service, context, alias="a")

    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None
    before = await _snapshot_windows(factory)

    for _ in range(4):
        assert await service.claim_and_reserve("w1") == "full"

    assert await _reservations(factory, b_id) == []
    assert await _snapshot_windows(factory) == before


async def test_endpoint_inactive_leaves_no_reservation(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(
                session,
                group_id="qg-1",
                request_limits=[(10, 60)],
                token_limits=[(10000, 60)],
            )
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

    # Deactivate the endpoint after enqueue: revalidation fails, nothing reserved.
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.Endpoint)
                .where(models.Endpoint.id == "ep-a")
                .values(is_active=False)
            )

    assert await service.claim_and_reserve("w1") == "processed"

    async with factory() as session:
        row = await sched_repo.get_request(session, request_id)
        assert row.state == RequestState.FAILED
    assert await _reservations(factory, request_id) == []
    async with factory() as session:
        assert await sched_repo.count_active_reservations(session, "ep-a") == 0


# --- pre-dispatch cancellation / expiry release quota -----------------------


async def test_cancellation_releases_token_but_not_committed_request_quota(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(
                session,
                group_id="qg-1",
                request_limits=[(10, 60)],
                token_limits=[(10000, 60)],
            )
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

    # Dispatch commits the request-metric reservation immediately; the token
    # reservation stays reserved until settlement. Simulate pre-settlement
    # cancellation.
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == request_id)
                .values(state=RequestState.RESERVED)
            )

    await service.request_cancellation(request_id)

    res = await _reservations(factory, request_id)
    by_metric = {r.metric: r for r in res}
    assert set(by_metric) == {"requests", "tokens"}
    # Reserved token capacity is returned to the window.
    assert by_metric["tokens"].state == "released"
    assert by_metric["tokens"].reserved_units == 0
    # Committed request quota is never refunded on cancellation.
    assert by_metric["requests"].state == "committed"
    assert by_metric["requests"].committed_units == 1


async def test_recovery_releases_token_but_not_committed_request_quota(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(input_tokens=5)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(
                session,
                group_id="qg-1",
                request_limits=[(10, 60)],
                token_limits=[(10000, 60)],
            )
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

    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == request_id)
                .values(
                    state=RequestState.RESERVED,
                    lease_expires_at=datetime(2020, 1, 1, tzinfo=UTC),
                )
            )

    await service.recover()

    async with factory() as session:
        row = await sched_repo.get_request(session, request_id)
        assert row.state == RequestState.QUEUED  # reclaimed, pre-dispatch
    res = await _reservations(factory, request_id)
    by_metric = {r.metric: r for r in res}
    assert set(by_metric) == {"requests", "tokens"}
    assert by_metric["tokens"].state == "released"
    assert by_metric["tokens"].reserved_units == 0
    assert by_metric["requests"].state == "committed"
    assert by_metric["requests"].committed_units == 1


# --- same-endpoint cross-quota-scope ordering (no head-of-line blocking) ----


async def test_same_endpoint_blocked_group_dispatches_other_group(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-a", request_limits=[(1, 60)])
            await _seed_group(session, group_id="qg-b", request_limits=[(1, 60)])
            # Both aliases route to the SAME endpoint (max_concurrency=2).
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a",
                group_id="qg-a", max_concurrency=2,
            )
            await _seed_endpoint_alias_route(
                session, alias_id="alias-b", alias_name="b", endpoint_id="ep-a",
                group_id="qg-b", max_concurrency=2,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)

    # Exhaust group A and hold its endpoint slot.
    await _enqueue(service, context, alias="a")
    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None

    # A second A request (older) is quota-blocked; a B request (newer) on the
    # same endpoint must still dispatch.
    a2_id = await _enqueue(service, context, alias="a")
    b_id = await _enqueue(service, context, alias="b")

    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    assert outcome.request_id == b_id  # B dispatches despite older blocked A

    async with factory() as session:
        a2 = await sched_repo.get_request(session, a2_id)
        assert a2.state == RequestState.QUEUED
        assert a2.wait_reason == "quota_window_exhausted"


async def test_fifo_within_group_on_shared_endpoint(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-a", request_limits=[(10, 60)])
            await _seed_group(session, group_id="qg-b", request_limits=[(10, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a",
                group_id="qg-a", max_concurrency=4,
            )
            await _seed_endpoint_alias_route(
                session, alias_id="alias-b", alias_name="b", endpoint_id="ep-a",
                group_id="qg-b", max_concurrency=4,
            )
            context = await ensure_dev_identity(session)

    service, _factory, _enc, context = await _build(sched_engine, mock, context)

    a1 = await _enqueue(service, context, alias="a")
    a2 = await _enqueue(service, context, alias="a")
    a3 = await _enqueue(service, context, alias="a")

    seen: list[str] = []
    for _ in range(3):
        outcome = await service.claim_and_reserve("w1")
        assert not isinstance(outcome, str) and outcome is not None
        seen.append(outcome.request_id)

    # FIFO within group A: a1 before a2 before a3.
    assert seen == [a1, a2, a3]


async def test_shared_endpoint_max_concurrency_across_groups(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-a", request_limits=[(10, 60)])
            await _seed_group(session, group_id="qg-b", request_limits=[(10, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a",
                group_id="qg-a", max_concurrency=1,
            )
            await _seed_endpoint_alias_route(
                session, alias_id="alias-b", alias_name="b", endpoint_id="ep-a",
                group_id="qg-b", max_concurrency=1,
            )
            context = await ensure_dev_identity(session)

    service, factory, _enc, context = await _build(sched_engine, mock, context)

    await _enqueue(service, context, alias="a")
    b_id = await _enqueue(service, context, alias="b")

    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None
    # Endpoint max_concurrency=1 is shared: B cannot dispatch while A holds it.
    assert await service.claim_and_reserve("w1") == "full"

    async with factory() as session:
        row = await sched_repo.get_request(session, b_id)
        assert row.state == RequestState.QUEUED
        assert row.wait_reason == "endpoint_full"


# --- token estimator fail-closed --------------------------------------------


async def test_token_estimator_unavailable_fails_closed(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(estimate_available=False)
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

    assert await service.claim_and_reserve("w1") == "processed"

    async with factory() as session:
        row = await sched_repo.get_request(session, request_id)
        assert row.state == RequestState.FAILED
        assert row.error_code == "quota_token_estimator_unavailable"


async def test_request_only_quota_works_without_estimator(sched_engine):
    await reset_schema(sched_engine)
    mock = QuotaMockAdapter(estimate_available=False)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", request_limits=[(10, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-1"
            )
            context = await ensure_dev_identity(session)

    service, _factory, _enc, context = await _build(sched_engine, mock, context)
    await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    # No token limit, so the unavailable estimator does not matter.
    assert not isinstance(outcome, str) and outcome is not None


# --- monotonic cooldown -----------------------------------------------------


async def test_cooldown_cannot_shorten(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", request_limits=[(10, 60)])

    now = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
    long_until = now + timedelta(seconds=600)
    short_until = now + timedelta(seconds=60)

    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await sched_repo.set_quota_group_cooldown(
                session, group_id="qg-1", cooldown_until=long_until
            )
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await sched_repo.set_quota_group_cooldown(
                session, group_id="qg-1", cooldown_until=short_until
            )

    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        group = await session.get(models.QuotaGroup, "qg-1")
        assert group.cooldown_until == long_until


async def test_concurrent_cooldown_preserves_max(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", request_limits=[(10, 60)])

    now = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
    later = now + timedelta(seconds=900)

    async def apply(delta_seconds: int) -> None:
        async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
            async with session.begin():
                await sched_repo.set_quota_group_cooldown(
                    session, group_id="qg-1", cooldown_until=now + timedelta(seconds=delta_seconds)
                )

    await asyncio.gather(
        apply(120), apply(300), apply(900), apply(30), apply(600), apply(60)
    )

    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        group = await session.get(models.QuotaGroup, "qg-1")
        assert group.cooldown_until == later  # the maximum reset time wins


# --- wait metadata ----------------------------------------------------------


async def test_wait_metadata_identifies_scope(sched_engine):
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
    assert await service.claim_and_reserve("w1") == "quota"

    async with factory() as session:
        queued = (
            await session.execute(
                select(models.InferenceRequest).where(
                    models.InferenceRequest.state == RequestState.QUEUED
                )
            )
        ).scalars().one()
        assert queued.wait_reason == "quota_window_exhausted"
        assert queued.wait_limit_id == "qg-1-r0"
        assert queued.wait_limit_metric == "requests"
        assert queued.next_eligible_at is not None
        assert queued.quota_group_id == "qg-1"


async def test_stale_wait_metadata_clears_when_eligible(sched_engine):
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
    blocked_id = await _enqueue(service, context, alias="a")

    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None
    assert await service.claim_and_reserve("w1") == "quota"

    # Force the blocked request's window to reset so it becomes eligible again.
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.QuotaWindow).values(committed_units=0, reserved_units=0)
            )
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == blocked_id)
                .values(next_eligible_at=None)
            )

    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    assert outcome.request_id == blocked_id

    async with factory() as session:
        row = await sched_repo.get_request(session, blocked_id)
        assert row.wait_reason is None
        assert row.wait_limit_id is None
        assert row.wait_limit_metric is None
        assert row.next_eligible_at is None


# --- schema invariants (DB-enforced) ----------------------------------------


async def test_quota_schema_constraints_reject_invalid(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            await _seed_group(session, group_id="qg-1", request_limits=[(1, 60)])
            await _seed_endpoint_alias_route(
                session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a", group_id="qg-1"
            )

    factory = async_sessionmaker(sched_engine, expire_on_commit=False)

    async with factory() as session:
        session.add(
            models.QuotaLimit(
                id="bad-metric",
                quota_group_id="qg-1",
                metric="bogus",
                limit_units=1,
                window_seconds=60,
                enabled=True,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with factory() as session:
        session.add(
            models.QuotaWindow(
                id="bad-window",
                quota_limit_id="qg-1-r0",
                window_start=datetime(2026, 10, 4, tzinfo=UTC),
                committed_units=-1,
                reserved_units=0,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with factory() as session:
        session.add(
            models.InferenceRequest(
                id="req-x",
                model_alias_id="alias-a",
                state="queued",
                stream=False,
                payload_encrypted=b"x",
                expires_at=datetime(2030, 1, 1, tzinfo=UTC),
            )
        )
        session.add(
            models.QuotaReservation(
                id="bad-state",
                request_id="req-x",
                quota_limit_id="qg-1-r0",
                window_start=datetime(2026, 10, 4, tzinfo=UTC),
                metric="requests",
                reserved_units=1,
                committed_units=0,
                state="bogus",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with factory() as session:
        session.add(
            models.RouteBinding(
                id="rb-bad",
                model_alias_id="alias-a",
                endpoint_id="ep-a",
                provider_account_id="acct-ollama",
                upstream_model="qwen3.8-2b-distill:Q6_K",
                default_output_tokens=0,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
