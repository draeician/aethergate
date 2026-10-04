"""Accounting foundation tests: money, pricing, budgets, usage, and ledger.

DB-gated scheduler tests reuse the mock-adapter pattern from the quota suite;
pure domain/money tests run everywhere.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aethergate.accounting import repository as accounting_repo
from aethergate.accounting import service as accounting_service
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
from aethergate.domain.enums import BillingUnit, Capability, RequestState
from aethergate.domain.ids import (
    BudgetPolicyId,
    EndpointId,
    ModelAliasId,
    PricePolicyId,
    ProjectId,
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


# ---------------------------------------------------------------------------
# Pure monetary computation / domain validation (no DB)
# ---------------------------------------------------------------------------


def test_money_math_uses_decimal_and_quantizes():
    from aethergate.domain.value_objects import quantize_money

    price = domain.PricePolicy(
        id=PricePolicyId("pp-1"),
        route_binding_id=RouteBindingId("rb-1"),
        billing_unit=BillingUnit.TOKEN,
        currency="usd",  # normalized to USD
        unit_scale=1_000_000,
        input_price=Decimal("0.5"),
        output_price=Decimal("1.5"),
    )
    assert price.currency == "USD"
    amount = accounting_service.token_amount(
        price, input_units=5, output_units=16
    )
    assert amount == Decimal("0.000026500000")  # (2.5 + 24) / 1e6
    assert quantize_money(Decimal("0.0000000000005")) == Decimal("0.000000000001")


def test_request_price_validation():
    with pytest.raises(ValidationError):
        domain.PricePolicy(
            id=PricePolicyId("pp-2"),
            route_binding_id=RouteBindingId("rb-1"),
            billing_unit=BillingUnit.REQUEST,
            currency="USD",
            request_price=Decimal("-1"),
        )
    # Float rejected outright.
    with pytest.raises(ValidationError):
        domain.PricePolicy(
            id=PricePolicyId("pp-3"),
            route_binding_id=RouteBindingId("rb-1"),
            billing_unit=BillingUnit.REQUEST,
            currency="USD",
            request_price=0.05,  # type: ignore[arg-type]
        )


def test_token_price_unit_scale_positive():
    with pytest.raises(ValidationError):
        domain.PricePolicy(
            id=PricePolicyId("pp-4"),
            route_binding_id=RouteBindingId("rb-1"),
            billing_unit=BillingUnit.TOKEN,
            currency="USD",
            unit_scale=0,
        )


def test_currency_validation():
    with pytest.raises(ValidationError):
        domain.PricePolicy(
            id=PricePolicyId("pp-5"),
            route_binding_id=RouteBindingId("rb-1"),
            billing_unit=BillingUnit.REQUEST,
            currency="us",  # not 3 letters
            request_price=Decimal("0.05"),
        )
    with pytest.raises(ValidationError):
        domain.ProjectBudgetPolicy(
            id=BudgetPolicyId("bp-1"),
            project_id=ProjectId("proj-1"),
            name="budget",
            currency="123",
            limit_amount=Decimal("1"),
            window_seconds=60,
        )


def test_budget_limit_positive_and_window_positive():
    with pytest.raises(ValidationError):
        domain.ProjectBudgetPolicy(
            id=BudgetPolicyId("bp-2"),
            project_id=ProjectId("proj-1"),
            name="budget",
            currency="USD",
            limit_amount=Decimal("0"),
            window_seconds=60,
        )
    with pytest.raises(ValidationError):
        domain.ProjectBudgetPolicy(
            id=BudgetPolicyId("bp-3"),
            project_id=ProjectId("proj-1"),
            name="budget",
            currency="USD",
            limit_amount=Decimal("1"),
            window_seconds=0,
        )


def test_budget_fixed_window_boundary():
    start = sched_repo.fixed_window_start(
        datetime(2026, 10, 4, 12, 0, 30, tzinfo=UTC), 60
    )
    assert start == datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Scheduler integration
# ---------------------------------------------------------------------------


class AccountingMockAdapter:
    def __init__(
        self,
        *,
        input_tokens: int = 5,
        total_tokens: int = 6,
        error: ProviderError | None = None,
        estimate_available: bool = True,
    ) -> None:
        self.input_tokens = input_tokens
        self.total_tokens = total_tokens
        self.error = error
        self.estimate_available = estimate_available

    def estimate_input_tokens(self, request: ChatRequest) -> int | None:
        return self.input_tokens if self.estimate_available else None

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
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


async def _seed_route(
    session,
    *,
    alias_id: str,
    alias_name: str,
    endpoint_id: str,
    max_concurrency: int = 8,
) -> RouteBindingId:
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
        ),
    )
    return RouteBindingId(f"rb-{alias_id}")


async def _seed_price_policy(
    session,
    *,
    route_binding_id: RouteBindingId,
    billing_unit: BillingUnit,
    request_price: Decimal | None = None,
    input_price: Decimal | None = None,
    output_price: Decimal | None = None,
    unit_scale: int = 1,
) -> None:
    await repository.create_price_policy(
        session,
        domain.PricePolicy(
            id=PricePolicyId(f"pp-{route_binding_id}"),
            route_binding_id=route_binding_id,
            billing_unit=billing_unit,
            currency="USD",
            unit_scale=unit_scale,
            request_price=request_price,
            input_price=input_price,
            output_price=output_price,
        ),
    )


async def _seed_budget_policy(
    session,
    *,
    project_id,
    name: str,
    limit_amount: Decimal,
    window_seconds: int,
) -> None:
    await repository.create_project_budget_policy(
        session,
        domain.ProjectBudgetPolicy(
            id=BudgetPolicyId(f"bp-{name}"),
            project_id=project_id,
            name=name,
            currency="USD",
            limit_amount=limit_amount,
            window_seconds=window_seconds,
        ),
    )


async def _build(sched_engine, mock):
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
    return service, factory


async def _enqueue(service, context, *, alias="gpt-4", max_tokens=None):
    return (
        await service.admit_and_enqueue(
            alias_name=alias,
            messages=[Message(role="user", content="hi")],
            params=GenerationParams(max_tokens=max_tokens),
            stream=False,
            context=context,
        )
    ).request_id


async def _count(factory, model) -> int:
    async with factory() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


# --- request-priced budget --------------------------------------------------


async def test_request_priced_budget_reserves_and_queues_exhausted(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("0.10"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    for _ in range(3):
        await _enqueue(service, context, alias="a")

    first = await service.claim_and_reserve("w1")
    second = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None
    assert not isinstance(second, str) and second is not None

    # Budget limit 0.10 / request 0.05 = exactly 2; third is budget-blocked.
    third = await service.claim_and_reserve("w1")
    assert third == "budget"

    # No endpoint/quota capacity is held by the budget-blocked work: only 2 active.
    async with factory() as session:
        active = await sched_repo.count_active_reservations(session, "ep-a")
    assert active == 2

    await service.run_complete(first)
    await service.run_complete(second)

    async with factory() as session:
        windows = (await session.execute(select(models.BudgetWindow))).scalars().all()
        assert len(windows) == 1
        assert windows[0].committed_amount == Decimal("0.100000000000")
        assert windows[0].reserved_amount == Decimal("0.000000000000")

    # Exactly two snapshots, two usage records, two usage ledger debits.
    assert await _count(factory, models.PriceSnapshot) == 2
    assert await _count(factory, models.UsageRecord) == 2
    assert await _count(factory, models.LedgerEntry) == 2

    async with factory() as session:
        blocked = (await session.execute(
            select(models.InferenceRequest).where(
                models.InferenceRequest.wait_reason == "budget_window_exhausted"
            )
        )).scalars().all()
        assert len(blocked) == 1


async def test_two_workers_cannot_oversubscribe_budget(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("0.10"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    for _ in range(8):
        await _enqueue(service, context, alias="a")

    seen: list[str] = []

    async def worker(wid):
        while True:
            outcome = await service.claim_and_reserve(wid)
            if isinstance(outcome, str) or outcome is None:
                return
            seen.append(outcome.request_id)

    await asyncio.gather(worker("w1"), worker("w2"), worker("w3"))
    assert len(seen) == 2  # exactly the budget capacity, never more


async def test_request_too_expensive_for_empty_budget_fails(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("0.01"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    request_id = await _enqueue(service, context, alias="a")

    assert await service.claim_and_reserve("w1") == "processed"
    async with factory() as session:
        row = await sched_repo.get_request(session, request_id)
        assert row.state == RequestState.FAILED
        assert row.error_code == "budget_request_too_large"


async def test_no_budget_project_not_monetarily_blocked(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)

    service, factory = await _build(sched_engine, mock)
    await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    # Priced usage is still recorded, but no budget reservation exists.
    assert await _count(factory, models.UsageRecord) == 1
    assert await _count(factory, models.LedgerEntry) == 1
    assert await _count(factory, models.BudgetReservation) == 0


async def test_no_snapshot_for_never_dispatched_request(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)

    service, factory = await _build(sched_engine, mock)
    await _enqueue(service, context, alias="a")

    # Never claimed/dispatched -> no snapshot, no usage, no ledger.
    assert await _count(factory, models.PriceSnapshot) == 0
    assert await _count(factory, models.UsageRecord) == 0
    assert await _count(factory, models.LedgerEntry) == 0


async def test_price_change_does_not_alter_historical_snapshot(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)

    service, factory = await _build(sched_engine, mock)
    first_id = await _enqueue(service, context, alias="a")
    first = await service.claim_and_reserve("w1")
    assert not isinstance(first, str) and first is not None
    await service.run_complete(first)

    # Change the mutable price config to a different amount.
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.PricePolicy)
                .where(models.PricePolicy.route_binding_id == str(rb))
                .values(request_price=Decimal("0.99"))
            )

    second_id = await _enqueue(service, context, alias="a")
    second = await service.claim_and_reserve("w1")
    assert not isinstance(second, str) and second is not None
    await service.run_complete(second)

    async with factory() as session:
        snapshots = (
            await session.execute(
                select(models.PriceSnapshot).order_by(models.PriceSnapshot.captured_at)
            )
        ).scalars().all()
        assert len(snapshots) == 2
        assert snapshots[0].request_price == Decimal("0.050000000000")
        assert snapshots[1].request_price == Decimal("0.990000000000")
        usages = {
            u.request_id: u.amount
            for u in (await session.execute(select(models.UsageRecord))).scalars().all()
        }
        assert usages[first_id] == Decimal("0.050000000000")
        assert usages[second_id] == Decimal("0.990000000000")


# --- token-priced budget ----------------------------------------------------


async def test_token_budget_fails_closed_without_estimator(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter(estimate_available=False)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.TOKEN,
                input_price=Decimal("0.5"), output_price=Decimal("1.5"), unit_scale=1_000_000,
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("1"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    request_id = await _enqueue(service, context, alias="a", max_tokens=16)

    assert await service.claim_and_reserve("w1") == "processed"
    async with factory() as session:
        row = await sched_repo.get_request(session, request_id)
        assert row.state == RequestState.FAILED
        assert row.error_code == "budget_token_estimator_unavailable"


async def test_token_budget_reservation_with_trusted_estimator(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter(input_tokens=5, total_tokens=6)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.TOKEN,
                input_price=Decimal("0.5"), output_price=Decimal("1.5"), unit_scale=1_000_000,
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("1"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    await _enqueue(service, context, alias="a", max_tokens=16)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    # Reserved = (5 * 0.5 + 16 * 1.5) / 1e6 = 0.0000265.
    async with factory() as session:
        reservation = (
            await session.execute(select(models.BudgetReservation))
        ).scalar_one()
        assert reservation.reserved_amount == Decimal("0.000026500000")


async def test_token_actual_below_reserve_releases_headroom(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter(input_tokens=5, total_tokens=6)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.TOKEN,
                input_price=Decimal("0.5"), output_price=Decimal("1.5"), unit_scale=1_000_000,
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("1"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    await _enqueue(service, context, alias="a", max_tokens=16)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    async with factory() as session:
        window = (await session.execute(select(models.BudgetWindow))).scalar_one()
        # Actual = (5*0.5 + 1*1.5)/1e6 = 0.000004, below reserved 0.0000265.
        assert window.committed_amount == Decimal("0.000004000000")
        assert window.reserved_amount == Decimal("0.000000000000")


async def test_token_actual_above_reserve_recorded_honestly(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter(input_tokens=1, total_tokens=500)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.TOKEN,
                input_price=Decimal("0.5"), output_price=Decimal("1.5"), unit_scale=1_000_000,
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("0.0001"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    await _enqueue(service, context, alias="a", max_tokens=16)
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    async with factory() as session:
        window = (await session.execute(select(models.BudgetWindow))).scalar_one()
        # Actual = (1*0.5 + 499*1.5)/1e6 = 0.000749 > limit 0.0001, kept honestly.
        assert window.committed_amount == Decimal("0.000749000000")


# --- settlement / idempotency ----------------------------------------------


async def test_failed_unknown_usage_conservatively_commits_no_usage(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter(
        error=ProviderError("upstream failed", status_code=500)
    )
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("1"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    async with factory() as session:
        window = (await session.execute(select(models.BudgetWindow))).scalar_one()
        assert window.committed_amount == Decimal("0.050000000000")  # conservative
        reservation = (
            await session.execute(select(models.BudgetReservation))
        ).scalar_one()
        assert reservation.state == "committed"
        assert reservation.settlement_reason == "failed"
    assert await _count(factory, models.UsageRecord) == 0
    assert await _count(factory, models.LedgerEntry) == 0


async def test_usage_record_and_ledger_idempotent(sched_engine):
    await reset_schema(sched_engine)
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)

    service, factory = await _build(sched_engine, AccountingMockAdapter())
    request_id = await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None
    await service.run_complete(outcome)

    # A duplicate UsageRecord insert with the same request_id is a no-op.
    async with factory() as session:
        async with session.begin():
            row = await sched_repo.get_request(session, request_id)
            snapshot_id = row.price_snapshot_id
            await accounting_repo.create_usage_record(
                session,
                request_id=request_id,
                execution_attempt_id=outcome.attempt_id,
                project_id=row.project_id,
                principal_id=row.principal_id,
                api_credential_id=row.api_credential_id,
                model_alias_id=row.model_alias_id,
                route_binding_id=str(rb),
                provider_account_id="acct-ollama",
                price_snapshot_id=snapshot_id,
                billing_unit="request",
                input_units=0,
                output_units=0,
                request_units=1,
                amount=Decimal("0.05"),
                currency="USD",
                recorded_at=datetime.now(UTC),
                upstream_request_id=None,
            )
            await accounting_repo.create_ledger_entry(
                session,
                project_id=row.project_id,
                usage_record_id=None,
                entry_type="usage_debit",
                amount=Decimal("0.05"),
                currency="USD",
                created_at=datetime.now(UTC),
                idempotency_key="usage:dup",
            )
            await accounting_repo.create_ledger_entry(
                session,
                project_id=row.project_id,
                usage_record_id=None,
                entry_type="usage_debit",
                amount=Decimal("0.05"),
                currency="USD",
                created_at=datetime.now(UTC),
                idempotency_key="usage:dup",
            )

    # Still exactly one usage record and (the duplicate ledger key collapses) totals.
    assert await _count(factory, models.UsageRecord) == 1


async def test_pre_dispatch_cancel_releases_budget(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("1"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    request_id = await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    # Back to reserved (pre-dispatch) then cancel.
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == request_id)
                .values(state=RequestState.RESERVED)
            )
    await service.request_cancellation(request_id)

    async with factory() as session:
        reservation = (
            await session.execute(select(models.BudgetReservation))
        ).scalar_one()
        assert reservation.state == "released"
        assert reservation.reserved_amount == Decimal("0.000000000000")
        window = (await session.execute(select(models.BudgetWindow))).scalar_one()
        assert window.reserved_amount == Decimal("0.000000000000")
    assert await _count(factory, models.UsageRecord) == 0


async def test_outcome_unknown_keeps_budget_then_reconcile_commits(sched_engine):
    await reset_schema(sched_engine)
    mock = AccountingMockAdapter()
    async with async_sessionmaker(sched_engine, expire_on_commit=False)() as session:
        async with session.begin():
            await _seed_provider_account(session)
            rb = await _seed_route(session, alias_id="alias-a", alias_name="a", endpoint_id="ep-a")
            await _seed_price_policy(
                session, route_binding_id=rb, billing_unit=BillingUnit.REQUEST,
                request_price=Decimal("0.05"),
            )
            context = await ensure_dev_identity(session)
            await _seed_budget_policy(
                session, project_id=context[0], name="usd-budget",
                limit_amount=Decimal("1"), window_seconds=60,
            )

    service, factory = await _build(sched_engine, mock)
    request_id = await _enqueue(service, context, alias="a")
    outcome = await service.claim_and_reserve("w1")
    assert not isinstance(outcome, str) and outcome is not None

    # Simulate post-dispatch lease expiry -> outcome_unknown.
    async with factory() as session:
        async with session.begin():
            row = await session.get(models.InferenceRequest, outcome.request_id)
            row.state = RequestState.DISPATCHED
            row.lease_expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    await service.recover()

    async with factory() as session:
        row = await sched_repo.get_request(session, request_id)
        assert row.state == RequestState.OUTCOME_UNKNOWN
        reservation = (
            await session.execute(select(models.BudgetReservation))
        ).scalar_one()
        assert reservation.state == "reserved"  # held, not released

    # Explicit reconciliation conservatively commits the held budget.
    assert await service.reconcile(request_id, "failed", "operator-1") is True

    async with factory() as session:
        reservation = (
            await session.execute(select(models.BudgetReservation))
        ).scalar_one()
        assert reservation.state == "committed"
        window = (await session.execute(select(models.BudgetWindow))).scalar_one()
        assert window.committed_amount == Decimal("0.050000000000")
    assert await _count(factory, models.UsageRecord) == 0
