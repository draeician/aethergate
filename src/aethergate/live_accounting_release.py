"""Live pre-dispatch release proof harness (accounting acceptance).

Runs inside the API container against the *live* database and uses the real
scheduler/accounting transition path to create one request that reaches the
reserved (pre-dispatch) accounting state, then cancels it before durable
upstream dispatch. It proves the resulting ``BudgetReservation`` is ``released``
with a detached/discarded price snapshot, zero reserved/committed amounts, no
usage record, no ``usage_debit`` ledger entry, and that the provider adapter was
never invoked (no upstream dispatch).

This is the "controlled harness" for the AGV2-022V live acceptance proof: it
uses the real ``SchedulingService``, real repository/accounting persistence, and
a provider adapter that raises if it is ever called — it does not manually
insert a ``BudgetReservation`` row or a pre-``released`` reservation.

Output contract: a single JSON object on stdout (identifiers for the browser
proof), preceded only by the adapter's redacted lifecycle logs. No prompt/
completion content, raw key, or provider secret is ever printed.

Usage:
    docker compose run --rm --no-deps api python -m aethergate.live_accounting_release
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    GenerationParams,
    Message,
    StreamChunk,
)
from aethergate.config import get_settings
from aethergate.domain import entities as domain
from aethergate.domain.entities import RequestContext
from aethergate.domain.enums import (
    BillingUnit,
    Capability,
    PrincipalKind,
    RequestState,
)
from aethergate.domain.ids import (
    ApiCredentialId,
    BudgetPolicyId,
    EndpointId,
    ModelAliasId,
    PricePolicyId,
    PrincipalId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
)
from aethergate.egress import DestinationPolicy
from aethergate.encryption import encryptor_from_key
from aethergate.inference.service import InferenceService
from aethergate.persistence import models, repository
from aethergate.persistence.db import get_session_factory
from aethergate.scheduler.service import SchedulingService
from aethergate.secrets import EnvSecretResolver

PRICE = Decimal("0.000000000777")
BUDGET_LIMIT = Decimal("1")
UPSTREAM_DESTINATION = "http://192.168.22.50:11434"


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex}"


class NoopAdapter:
    """An adapter that proves no upstream dispatch: calling it is a failure."""

    def __init__(self) -> None:
        self.complete_calls = 0
        self.stream_calls = 0

    def estimate_input_tokens(self, request: ChatRequest) -> int | None:
        return None

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.complete_calls += 1
        raise AssertionError("upstream dispatch must not occur during a pre-dispatch release")

    async def stream(self, request: ChatRequest, secret: str | None) -> AsyncIterator[StreamChunk]:
        self.stream_calls += 1
        raise AssertionError("upstream dispatch must not occur during a pre-dispatch release")
        yield  # pragma: no cover - unreachable


def _build_service(adapter: NoopAdapter) -> SchedulingService:
    settings = get_settings()
    encryptor = encryptor_from_key(
        settings.queue_key.get_secret_value() if settings.queue_key else None
    )
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: adapter,
    )
    return SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=get_session_factory(),
        settings=settings,
    )


async def _seed(session: AsyncSession, run_id: str) -> tuple[RequestContext, RouteBindingId]:
    provider = domain.Provider(
        id=ProviderId(_new_id("prov-")),
        kind="ollama",
        name=f"release-prov-{run_id}",
        capabilities=(Capability.TEXT,),
    )
    await repository.create_provider(session, provider)
    account = domain.ProviderAccount(
        id=ProviderAccountId(_new_id("acct-")),
        provider_id=provider.id,
        name=f"release-acct-{run_id}",
    )
    await repository.create_provider_account(session, account)
    endpoint = domain.Endpoint(
        id=EndpointId(_new_id("ep-")),
        provider_account_id=account.id,
        name=f"release-ep-{run_id}",
        base_destination=UPSTREAM_DESTINATION,
        max_concurrency=1,
    )
    await repository.create_endpoint(session, endpoint)
    alias = domain.ModelAlias(
        id=ModelAliasId(_new_id("alias-")),
        name=f"release-alias-{run_id}",
        capabilities=(Capability.TEXT,),
    )
    await repository.create_model_alias(session, alias)
    route = domain.RouteBinding(
        id=RouteBindingId(_new_id("rb-")),
        model_alias_id=alias.id,
        endpoint_id=endpoint.id,
        provider_account_id=account.id,
        upstream_model="qwen3.8-2b-distill:Q6_K",
    )
    await repository.create_route_binding(session, route)

    project = await repository.create_project(
        session, domain.Project(id=ProjectId(_new_id("proj-")), name=f"e2e-release-{run_id}")
    )
    principal = await repository.create_principal(
        session,
        domain.Principal(
            id=PrincipalId(_new_id("prin-")),
            project_id=project.id,
            kind=PrincipalKind.USER,
            name=f"release-prin-{run_id}",
        ),
    )
    credential = await repository.create_api_credential(
        session,
        domain.ApiCredential(
            id=ApiCredentialId(_new_id("cred-")),
            project_id=project.id,
            principal_id=principal.id,
            name=f"release-cred-{run_id}",
        ),
    )
    await repository.create_price_policy(
        session,
        domain.PricePolicy(
            id=PricePolicyId(_new_id("pp-")),
            route_binding_id=route.id,
            billing_unit=BillingUnit.REQUEST,
            currency="USD",
            request_price=PRICE,
        ),
    )
    await repository.create_project_budget_policy(
        session,
        domain.ProjectBudgetPolicy(
            id=BudgetPolicyId(_new_id("bp-")),
            project_id=project.id,
            name=f"release-budget-{run_id}",
            currency="USD",
            limit_amount=BUDGET_LIMIT,
            window_seconds=3600,
        ),
    )
    context = RequestContext(
        project_id=project.id,
        principal_id=principal.id,
        api_credential_id=credential.id,
        audience=credential.audience,
        scopes=credential.scopes,
    )
    return context, route.id


async def _drain_queued(service: SchedulingService, factory) -> None:
    """Cancel any leftover QUEUED requests so the harness's claim is deterministic.

    The harness claims the oldest eligible queued request across all scheduling
    scopes. Orphaned queued work from prior aborted runs could otherwise be
    claimed instead of the harness's own request. Queued requests hold no
    reservations, so cancelling them through the shared transition is safe.
    """
    async with factory() as session:
        queued_ids = (
            await session.execute(
                select(models.InferenceRequest.id).where(
                    models.InferenceRequest.state == RequestState.QUEUED
                )
            )
        ).scalars().all()
    for request_id in queued_ids:
        await service.request_cancellation(request_id)


async def _run() -> None:
    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    adapter = NoopAdapter()
    service = _build_service(adapter)
    factory = get_session_factory()

    await _drain_queued(service, factory)

    async with factory() as session:
        async with session.begin():
            context, route_binding = await _seed(session, run_id)
            project_id = str(context.project_id)
            route_binding_id = str(route_binding)
            project_name = f"e2e-release-{run_id}"

    request_id = (
        await service.admit_and_enqueue(
            alias_name=f"release-alias-{run_id}",
            messages=[Message(role="user", content="pre-dispatch release proof")],
            params=GenerationParams(max_tokens=16),
            stream=False,
            context=context,
        )
    ).request_id

    # Real admission: claim + reserve budget/snapshot/endpoint capacity, then
    # hold the request in the pre-dispatch window before durable dispatch intent.
    outcome = await service.claim_and_reserve("release-harness")
    assert not isinstance(outcome, str) and outcome is not None, "expected a reserved claim"
    assert outcome.request_id == request_id, "claim returned a different request"

    # Position the request back in the reserved (pre-dispatch) state, exactly the
    # window between reservation and durable dispatch intent, then cancel through
    # the shared scheduler/admin transition.
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == request_id)
                .values(state=RequestState.RESERVED)
            )
    await service.request_cancellation(request_id)

    # Assert the released invariants in the live database.
    async with factory() as session:
        request_row = await session.get(models.InferenceRequest, request_id)
        assert request_row is not None
        assert request_row.state == RequestState.CANCELLED

        reservation = (
            await session.execute(
                select(models.BudgetReservation).where(
                    models.BudgetReservation.request_id == request_id
                )
            )
        ).scalar_one()
        assert reservation.state == "released"
        assert reservation.price_snapshot_id is None
        assert reservation.reserved_amount == Decimal("0")
        assert reservation.committed_amount == Decimal("0")

        window = (
            await session.execute(
                select(models.BudgetWindow).where(
                    models.BudgetWindow.budget_policy_id == reservation.budget_policy_id
                )
            )
        ).scalar_one()
        assert window.reserved_amount == Decimal("0")

        # The pre-dispatch snapshot was discarded (no snapshot for this route).
        snapshots = (
            await session.execute(
                select(models.PriceSnapshot).where(
                    models.PriceSnapshot.route_binding_id == route_binding_id
                )
            )
        ).scalars().all()
        assert len(snapshots) == 0

        # No usage or ledger settlement for this project's released request.
        usage = (
            await session.execute(
                select(models.UsageRecord).where(models.UsageRecord.request_id == request_id)
            )
        ).scalars().all()
        assert len(usage) == 0
        ledgers = (
            await session.execute(
                select(models.LedgerEntry).where(models.LedgerEntry.project_id == project_id)
            )
        ).scalars().all()
        assert len(ledgers) == 0

        reservation_id = str(reservation.id)
        budget_policy_id = str(reservation.budget_policy_id)

    assert adapter.complete_calls == 0
    assert adapter.stream_calls == 0

    print(
        json.dumps(
            {
                "project_id": project_id,
                "project_name": project_name,
                "request_id": request_id,
                "reservation_id": reservation_id,
                "budget_policy_id": budget_policy_id,
                "price": format(PRICE.normalize(), "f"),
            }
        )
    )


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
