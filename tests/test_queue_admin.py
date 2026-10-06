"""Queue/operator admin control plane tests (DB-gated).

Covers the ``/admin/v1/queue`` surface introduced in AGV2-018: safe queue
metadata reads, queue summary, endpoint runtime/slot status, endpoint
pause/drain/resume, safe cancellation, outcome_unknown reconciliation, provider
quota runtime status, and the associated RBAC/scoping. Two layers are exercised:

- Service level: ``aethergate.scheduler.admin`` is called directly with typed
  ``AdminRequestContext``s against a real scheduler service, so state-transition
  and authorization behavior is deterministic and free of HTTP serialization
  noise.
- HTTP level: the FastAPI app is exercised through ``httpx.ASGITransport`` to
  prove route wiring, response DTO safety (no content/secrets), and 403/404
  scoping.

All DB-gated tests skip when ``AETHERGATE_TEST_DATABASE_URL`` is unset.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from aethergate.domain.entities import RequestContext
from aethergate.domain.enums import (
    Capability,
    CredentialAudience,
    CredentialScope,
    PrincipalKind,
    RequestState,
    ResourceScopeType,
    Role,
)
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    RequestId,
    ReservationId,
    RouteBindingId,
)
from aethergate.egress import DestinationPolicy
from aethergate.encryption import QueueEncryptor
from aethergate.errors import (
    AdminAuthorizationError,
    AdminResourceNotFound,
    AdminValidationError,
    QueueTransitionError,
)
from aethergate.identity import admin as admin_service
from aethergate.inference.service import InferenceService
from aethergate.main import app
from aethergate.persistence import db as persistence_db
from aethergate.persistence import models, repository
from aethergate.scheduler import admin as queue_service
from aethergate.scheduler import repository as sched_repo
from aethergate.scheduler.service import SchedulingService
from aethergate.secrets import EnvSecretResolver
from db_helpers import (
    TEST_DATABASE_URL,
    ensure_database,
    reset_schema,
    url_for_database,
)

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None, reason="AETHERGATE_TEST_DATABASE_URL not set"
)

BOOTSTRAP_TOKEN = "test-bootstrap-secret-0123456789abcdefghij"
ALLOWED_HOST = "api.example.com"
ENDPOINT_ID = "ep-ollama"


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@h/db",
        allow_inference_auth_bypass=False,
        bootstrap_token=BOOTSTRAP_TOKEN,
        upstream_allowlist=ALLOWED_HOST,
        worker_lease_seconds=120.0,
        queue_max_requests=100,
    )


def _new_id() -> str:
    return uuid.uuid4().hex


class MockAdapter:
    def __init__(self, *, content: str = "Hello from mock", delay: float = 0.0) -> None:
        self._content = content
        self._delay = delay
        self.complete_calls = 0

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        self.complete_calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return CompletionResult(
            content=self._content,
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            upstream_request_id=f"upstream-{self.complete_calls}",
        )

    async def stream(self, request: ChatRequest, secret: str | None):
        yield StreamChunk(content="Hel", finish_reason=None, usage=None)
        yield StreamChunk(
            content="lo", finish_reason="stop", usage=Usage(prompt_tokens=1, completion_tokens=1)
        )


async def _seed_catalog(session, *, max_concurrency: int = 2) -> None:
    await repository.create_provider(
        session,
        domain.Provider(
            id=ProviderId("prov-ollama"), kind="ollama", name="ollama",
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
    await repository.create_endpoint(
        session,
        domain.Endpoint(
            id=EndpointId(ENDPOINT_ID),
            provider_account_id=ProviderAccountId("acct-ollama"),
            name="ollama-endpoint",
            base_destination=f"http://{ALLOWED_HOST}",
            max_concurrency=max_concurrency,
        ),
    )
    await repository.create_model_alias(
        session,
        domain.ModelAlias(
            id=ModelAliasId("alias-gpt4"), name="gpt-4", capabilities=(Capability.TEXT,)
        ),
    )
    await repository.create_route_binding(
        session,
        domain.RouteBinding(
            id=RouteBindingId("rb-gpt4"),
            model_alias_id=ModelAliasId("alias-gpt4"),
            endpoint_id=EndpointId(ENDPOINT_ID),
            provider_account_id=ProviderAccountId("acct-ollama"),
            upstream_model="mock-model",
        ),
    )


async def _project_role(
    factory,
    sa_ctx,
    *,
    role: Role,
    project_id: ProjectId,
    scopes: tuple[CredentialScope, ...],
) -> domain.AdminRequestContext:
    async with factory() as s:
        async with s.begin():
            principal = await admin_service.create_principal(
                s,
                context=sa_ctx,
                project_id=project_id,
                kind=PrincipalKind.SERVICE_ACCOUNT,
                name=f"pr-{_new_id()}",
            )
            await admin_service.create_role_assignment(
                s,
                context=sa_ctx,
                principal_id=principal.id,
                role=role,
                resource_scope_type=ResourceScopeType.PROJECT,
                resource_id=project_id,
            )
            _, raw = await admin_service.create_credential(
                s,
                context=sa_ctx,
                project_id=project_id,
                principal_id=principal.id,
                name=f"cred-{_new_id()}",
                audience=CredentialAudience.ADMIN,
                scopes=scopes,
                expires_at=None,
            )
    async with factory() as s:
        async with s.begin():
            return await admin_service.authenticate_admin(s, raw)


# ---------------------------------------------------------------------------
# Service-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def queue_env(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_queue_admin")
    await ensure_database(url)
    engine = create_async_engine(url)
    await reset_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        async with s.begin():
            await _seed_catalog(s)
    async with factory() as s:
        _, sa_raw = await admin_service.bootstrap(s, BOOTSTRAP_TOKEN)
    async with factory() as s:
        async with s.begin():
            sa_ctx = await admin_service.authenticate_admin(s, sa_raw)

    async with factory() as s:
        async with s.begin():
            proj_a = await admin_service.create_project(s, context=sa_ctx, name="project-a")
            proj_b = await admin_service.create_project(s, context=sa_ctx, name="project-b")

    async def _inference_identity(project_id: ProjectId) -> RequestContext:
        async with factory() as s:
            async with s.begin():
                principal = await admin_service.create_principal(
                    s,
                    context=sa_ctx,
                    project_id=project_id,
                    kind=PrincipalKind.USER,
                    name=f"inf-pr-{_new_id()}",
                )
                cred, _raw = await admin_service.create_credential(
                    s,
                    context=sa_ctx,
                    project_id=project_id,
                    principal_id=principal.id,
                    name=f"inf-cred-{_new_id()}",
                    audience=CredentialAudience.INFERENCE,
                    scopes=(CredentialScope.INFERENCE_INVOKE,),
                    expires_at=None,
                )
        return RequestContext(
            project_id=project_id,
            principal_id=principal.id,
            api_credential_id=cred.id,
            audience=cred.audience,
            scopes=cred.scopes,
        )

    inf_a = await _inference_identity(proj_a.id)
    inf_b = await _inference_identity(proj_b.id)

    pa_ctx = await _project_role(
        factory,
        sa_ctx,
        role=Role.PROJECT_ADMIN,
        project_id=proj_a.id,
        scopes=(CredentialScope.ADMIN_QUEUE_READ, CredentialScope.ADMIN_QUEUE_WRITE),
    )
    pv_ctx = await _project_role(
        factory,
        sa_ctx,
        role=Role.PROJECT_VIEWER,
        project_id=proj_a.id,
        scopes=(CredentialScope.ADMIN_QUEUE_READ,),
    )
    pa_b_ctx = await _project_role(
        factory,
        sa_ctx,
        role=Role.PROJECT_ADMIN,
        project_id=proj_b.id,
        scopes=(CredentialScope.ADMIN_QUEUE_READ, CredentialScope.ADMIN_QUEUE_WRITE),
    )

    mock = MockAdapter()
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = _settings()
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: mock,
    )
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )

    inf_by_project = {proj_a.id: inf_a, proj_b.id: inf_b}

    async def enqueue(project_id: ProjectId, n: int = 1) -> list[str]:
        ids = []
        for i in range(n):
            enq = await service.admit_and_enqueue(
                alias_name="gpt-4",
                messages=[Message(role="user", content=f"secret-content-{i}")],
                params=GenerationParams(),
                stream=False,
                context=inf_by_project[project_id],
            )
            ids.append(enq.request_id)
        return ids

    yield {
        "service": service,
        "factory": factory,
        "encryptor": encryptor,
        "mock": mock,
        "sa_ctx": sa_ctx,
        "pa_ctx": pa_ctx,
        "pv_ctx": pv_ctx,
        "pa_b_ctx": pa_b_ctx,
        "proj_a": proj_a.id,
        "proj_b": proj_b.id,
        "enqueue": enqueue,
    }
    await reset_schema(engine)
    await engine.dispose()


# --- endpoint runtime / operational state ------------------------------------


async def test_operational_state_defaults_active(queue_env):
    env = queue_env
    async with env["factory"]() as s:
        view = await queue_service.get_endpoint_runtime(
            s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
        )
    assert view.operational_state == "active"
    assert view.is_active is True
    assert view.max_concurrency == 2
    assert view.occupied_slots == 0
    assert view.available_slots == 2
    assert view.draining_complete is False


async def test_endpoint_runtime_slot_calculation(queue_env):
    env = queue_env
    service = env["service"]
    ids = await env["enqueue"](env["proj_a"], 6)
    a = await service.claim_and_reserve("w1")
    b = await service.claim_and_reserve("w2")
    assert not isinstance(a, str) and a is not None
    assert not isinstance(b, str) and b is not None

    async with env["factory"]() as s:
        view = await queue_service.get_endpoint_runtime(
            s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
        )
    assert view.occupied_slots == 2
    assert view.available_slots == 0

    await service.run_complete(a)
    await service.run_complete(b)

    async with env["factory"]() as s:
        view = await queue_service.get_endpoint_runtime(
            s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
        )
    assert view.occupied_slots == 0
    assert view.available_slots == 2
    assert ids  # sanity


async def test_pause_prevents_new_reserve_and_survives(queue_env):
    env = queue_env
    service = env["service"]
    ids = await env["enqueue"](env["proj_a"], 2)

    async with env["factory"]() as s:
        async with s.begin():
            view = await queue_service.pause_endpoint(
                s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
            )
    assert view.operational_state == "paused"

    # Pause persists across a fresh service/session (durable state).
    assert await service.claim_and_reserve("w-paused") == "paused"
    async with env["factory"]() as s:
        assert await sched_repo.count_active_reservations(s, ENDPOINT_ID) == 0
        row = await sched_repo.get_request(s, ids[0])
        assert row.state == RequestState.QUEUED

    # Resume restores dispatch.
    async with env["factory"]() as s:
        async with s.begin():
            await queue_service.resume_endpoint(
                s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
            )
    outcome = await service.claim_and_reserve("w-resume")
    assert not isinstance(outcome, str) and outcome is not None
    assert outcome.request_id == ids[0]
    await service.run_complete(outcome)


async def test_drain_prevents_new_reserve_until_empty(queue_env):
    env = queue_env
    service = env["service"]
    await env["enqueue"](env["proj_a"], 2)

    # Put one request in-flight (reserved).
    a = await service.claim_and_reserve("w1")
    assert not isinstance(a, str) and a is not None

    async with env["factory"]() as s:
        async with s.begin():
            view = await queue_service.drain_endpoint(
                s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
            )
    assert view.operational_state == "draining"
    assert view.draining_complete is False

    # Draining with an occupied slot admits no new reservation.
    assert await service.claim_and_reserve("w-drain") == "full"

    # Once the in-flight work completes, draining_complete flips.
    await service.run_complete(a)
    async with env["factory"]() as s:
        view = await queue_service.get_endpoint_runtime(
            s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
        )
    assert view.draining_complete is True
    assert view.occupied_slots == 0


async def test_pause_does_not_kill_inflight(queue_env):
    env = queue_env
    service = env["service"]
    await env["enqueue"](env["proj_a"], 1)
    a = await service.claim_and_reserve("w1")
    assert not isinstance(a, str) and a is not None

    async with env["factory"]() as s:
        async with s.begin():
            await queue_service.pause_endpoint(
                s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
            )

    # Already reserved work still settles normally.
    await service.run_complete(a)
    async with env["factory"]() as s:
        row = await sched_repo.get_request(s, a.request_id)
        assert row.state == RequestState.SUCCEEDED


async def test_pause_idempotent_no_duplicate_audit(queue_env):
    env = queue_env
    async with env["factory"]() as s:
        async with s.begin():
            await queue_service.pause_endpoint(
                s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
            )
    async with env["factory"]() as s:
        async with s.begin():
            await queue_service.pause_endpoint(
                s, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
            )
    async with env["factory"]() as s:
        events = (
            await s.execute(
                select(models.AuditEvent).where(
                    models.AuditEvent.action == "endpoint_dispatch.paused"
                )
            )
        ).scalars().all()
    assert len(events) == 1


async def test_pause_vs_claim_lock_race(queue_env):
    env = queue_env
    service = env["service"]
    ids = await env["enqueue"](env["proj_a"], 2)
    now = queue_service.utcnow()

    async def _pause():
        async with env["factory"]() as s2:
            async with s2.begin():
                return await queue_service.pause_endpoint(
                    s2, context=env["sa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
                )

    async with env["factory"]() as s:
        async with s.begin():
            # Simulate a claim that has already acquired the endpoint row lock
            # and is about to commit its reservation.
            await sched_repo.lock_endpoint(s, ENDPOINT_ID)
            await sched_repo.create_reservation(
                s,
                reservation_id=ReservationId(_new_id()),
                request_id=ids[0],
                endpoint_id=ENDPOINT_ID,
                acquired_at=now,
            )
            await s.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == ids[0])
                .values(state=RequestState.RESERVED, started_at=now)
            )
            pause_task = asyncio.create_task(_pause())
            # Let the pause task reach its blocking FOR UPDATE (scheduling yield
            # only; correctness is provided by the row lock, not the sleep).
            await asyncio.sleep(0.05)
            assert not pause_task.done(), "pause must be blocked on the endpoint lock"
        # Commit releases the endpoint lock; the pre-pause reservation survives.

    view = await pause_task
    assert view.operational_state == "paused"

    # After the pause boundary, no new reservation may be granted.
    assert await service.claim_and_reserve("w-after-pause") == "paused"
    async with env["factory"]() as s:
        assert await sched_repo.count_active_reservations(s, ENDPOINT_ID) == 1


# --- cancellation -------------------------------------------------------------


async def test_cancel_queued_request(queue_env):
    env = queue_env
    service = env["service"]
    ids = await env["enqueue"](env["proj_a"], 1)

    async with env["factory"]() as s:
        async with s.begin():
            outcome, state, _pid = await queue_service.cancel_request(
                s, context=env["sa_ctx"], request_id=RequestId(ids[0])
            )
    assert outcome == "cancelled_now"
    assert state == RequestState.CANCELLED

    async with env["factory"]() as s:
        row = await sched_repo.get_request(s, ids[0])
        assert row.state == RequestState.CANCELLED
        assert row.cancellation_requested is True
        assert await sched_repo.count_active_reservations(s, ENDPOINT_ID) == 0
        usage = (
            await s.execute(
                select(models.UsageRecord).where(models.UsageRecord.request_id == ids[0])
            )
        ).scalars().all()
        assert usage == []

    assert await service.claim_and_reserve("w") is None


async def test_cancel_reserved_releases_reservation(queue_env):
    env = queue_env
    ids = await env["enqueue"](env["proj_a"], 1)

    # Simulate a pre-dispatch reserved request (capacity held, not yet dispatched).
    async with env["factory"]() as s:
        async with s.begin():
            await sched_repo.create_reservation(
                s,
                reservation_id=ReservationId(_new_id()),
                request_id=ids[0],
                endpoint_id=ENDPOINT_ID,
                acquired_at=queue_service.utcnow(),
            )
            await s.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == ids[0])
                .values(state=RequestState.RESERVED)
            )

    async with env["factory"]() as s:
        assert await sched_repo.count_active_reservations(s, ENDPOINT_ID) == 1

    async with env["factory"]() as s:
        async with s.begin():
            outcome, state, _pid = await queue_service.cancel_request(
                s, context=env["sa_ctx"], request_id=RequestId(ids[0])
            )
    assert outcome == "cancelled_now"
    assert state == RequestState.CANCELLED
    async with env["factory"]() as s:
        assert await sched_repo.count_active_reservations(s, ENDPOINT_ID) == 0
        row = await sched_repo.get_request(s, ids[0])
        assert row.state == RequestState.CANCELLED


async def test_cancel_dispatched_only_requests_cancellation(queue_env):
    env = queue_env
    service = env["service"]
    ids = await env["enqueue"](env["proj_a"], 1)
    a = await service.claim_and_reserve("w1")
    assert not isinstance(a, str) and a is not None

    # Simulate durable dispatch intent (the worker has marked it dispatched).
    async with env["factory"]() as s:
        async with s.begin():
            await s.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == ids[0])
                .values(state=RequestState.DISPATCHED)
            )

    async with env["factory"]() as s:
        async with s.begin():
            outcome, state, _pid = await queue_service.cancel_request(
                s, context=env["sa_ctx"], request_id=RequestId(ids[0])
            )
    assert outcome == "cancellation_requested"
    assert state == RequestState.DISPATCHED

    async with env["factory"]() as s:
        row = await sched_repo.get_request(s, ids[0])
        assert row.cancellation_requested is True
        assert row.state == RequestState.DISPATCHED


async def test_cancel_idempotent_and_terminal_conflict(queue_env):
    env = queue_env
    ids = await env["enqueue"](env["proj_a"], 1)
    async with env["factory"]() as s:
        async with s.begin():
            await queue_service.cancel_request(
                s, context=env["sa_ctx"], request_id=RequestId(ids[0])
            )
    async with env["factory"]() as s:
        async with s.begin():
            outcome, _state, _pid = await queue_service.cancel_request(
                s, context=env["sa_ctx"], request_id=RequestId(ids[0])
            )
    assert outcome == "already_cancelled"

    # A terminal succeeded request cannot be cancelled (stable conflict).
    env2 = env
    ids2 = await env2["enqueue"](env2["proj_a"], 1)
    a = await env2["service"].claim_and_reserve("w")
    assert not isinstance(a, str) and a is not None
    await env2["service"].run_complete(a)
    async with env2["factory"]() as s:
        async with s.begin():
            outcome, state, _pid = await queue_service.cancel_request(
                s, context=env2["sa_ctx"], request_id=RequestId(ids2[0])
            )
    assert outcome == "terminal"
    assert state == RequestState.SUCCEEDED


async def test_outcome_unknown_cannot_be_cancelled(queue_env):
    env = queue_env
    service = env["service"]
    ids = await env["enqueue"](env["proj_a"], 1)
    a = await service.claim_and_reserve("w1")
    assert not isinstance(a, str) and a is not None

    async with env["factory"]() as s:
        async with s.begin():
            await s.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == ids[0])
                .values(lease_expires_at=datetime_2020())
            )
    await service.recover()

    async with env["factory"]() as s:
        row = await sched_repo.get_request(s, ids[0])
        assert row.state == RequestState.OUTCOME_UNKNOWN

    # outcome_unknown cannot be resolved by cancellation (requires reconciliation).
    async with env["factory"]() as s:
        async with s.begin():
            outcome, state, _pid = await queue_service.cancel_request(
                s, context=env["sa_ctx"], request_id=RequestId(ids[0])
            )
    assert outcome == "outcome_unknown"
    assert state == RequestState.OUTCOME_UNKNOWN


def datetime_2020():
    return datetime(2020, 1, 1, tzinfo=UTC)


# --- reconciliation -----------------------------------------------------------


async def _make_outcome_unknown(service, factory, request_id):
    async with factory() as s:
        async with s.begin():
            await s.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == request_id)
                .values(lease_expires_at=datetime_2020())
            )
    await service.recover()
    async with factory() as s:
        row = await sched_repo.get_request(s, request_id)
        assert row.state == RequestState.OUTCOME_UNKNOWN
        return row


async def test_reconcile_releases_slot_once(queue_env):
    env = queue_env
    service = env["service"]
    ids = await env["enqueue"](env["proj_a"], 1)
    a = await service.claim_and_reserve("w1")
    assert not isinstance(a, str) and a is not None
    await _make_outcome_unknown(service, env["factory"], ids[0])

    async with env["factory"]() as s:
        assert await sched_repo.count_active_reservations(s, ENDPOINT_ID) == 1

    async with env["factory"]() as s:
        async with s.begin():
            result, _pid = await queue_service.reconcile_request(
                s, context=env["sa_ctx"], request_id=RequestId(ids[0]), disposition="failed"
            )
    assert result is True

    async with env["factory"]() as s:
        assert await sched_repo.count_active_reservations(s, ENDPOINT_ID) == 0
        row = await sched_repo.get_request(s, ids[0])
        assert row.state == RequestState.FAILED
        assert row.reconciled_by == str(env["sa_ctx"].principal_id)

    # Repeat reconciliation is a stable conflict, no double release/commit.
    async with env["factory"]() as s:
        async with s.begin():
            with pytest.raises(QueueTransitionError):
                await queue_service.reconcile_request(
                    s, context=env["sa_ctx"], request_id=RequestId(ids[0]), disposition="failed"
                )
    async with env["factory"]() as s:
        assert await sched_repo.count_active_reservations(s, ENDPOINT_ID) == 0


async def test_reconcile_rejects_succeeded_and_project_role(queue_env):
    env = queue_env
    service = env["service"]
    ids = await env["enqueue"](env["proj_a"], 1)
    a = await service.claim_and_reserve("w1")
    assert not isinstance(a, str) and a is not None
    await _make_outcome_unknown(service, env["factory"], ids[0])

    async with env["factory"]() as s:
        with pytest.raises(AdminValidationError):
            await queue_service.reconcile_request(
                s, context=env["sa_ctx"], request_id=RequestId(ids[0]), disposition="succeeded"
            )
        # project_admin cannot reconcile (deployment-only).
        with pytest.raises(AdminAuthorizationError):
            await queue_service.reconcile_request(
                s, context=env["pa_ctx"], request_id=RequestId(ids[0]), disposition="failed"
            )


# --- quota runtime status -----------------------------------------------------


async def test_quota_status_zero_state_no_row_creation(queue_env):
    env = queue_env
    async with env["factory"]() as s:
        # No quota groups seeded: zero state must not fabricate rows.
        views = await queue_service.list_quota_status(s, context=env["sa_ctx"])
    assert views == []


# --- authorization / scoping --------------------------------------------------


async def test_project_rbac_scoping_and_non_enumeration(queue_env):
    env = queue_env
    ids_a = await env["enqueue"](env["proj_a"], 2)
    ids_b = await env["enqueue"](env["proj_b"], 1)

    async with env["factory"]() as s:
        # system_admin sees all three.
        views, total = await queue_service.list_requests(
            s, context=env["sa_ctx"], limit=50, offset=0
        )
        assert total == 3
        assert {v.request_id for v in views} == set(ids_a) | set(ids_b)

        # project_admin(A) sees only A.
        views, total = await queue_service.list_requests(
            s, context=env["pa_ctx"], limit=50, offset=0
        )
        assert total == 2
        assert {v.request_id for v in views} == set(ids_a)

        # project_admin(B) sees only B.
        views, total = await queue_service.list_requests(
            s, context=env["pa_b_ctx"], limit=50, offset=0
        )
        assert total == 1
        assert views[0].request_id == ids_b[0]

        # Cross-project request ID is indistinguishable from nonexistent (404).
        with pytest.raises(AdminResourceNotFound):
            await queue_service.get_request(
                s, context=env["pa_ctx"], request_id=RequestId(ids_b[0])
            )


async def test_project_viewer_cannot_cancel(queue_env):
    env = queue_env
    ids = await env["enqueue"](env["proj_a"], 1)
    async with env["factory"]() as s:
        with pytest.raises(AdminAuthorizationError):
            await queue_service.cancel_request(
                s, context=env["pv_ctx"], request_id=RequestId(ids[0])
            )


async def test_deployment_operations_require_system_admin(queue_env):
    env = queue_env
    async with env["factory"]() as s:
        with pytest.raises(AdminAuthorizationError):
            await queue_service.pause_endpoint(
                s, context=env["pa_ctx"], endpoint_id=EndpointId(ENDPOINT_ID)
            )
        with pytest.raises(AdminAuthorizationError):
            await queue_service.list_endpoint_runtime(s, context=env["pa_ctx"])
        with pytest.raises(AdminAuthorizationError):
            await queue_service.list_quota_status(s, context=env["pa_ctx"])


# ---------------------------------------------------------------------------
# HTTP-level tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def queue_http(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_queue_http")
    await ensure_database(url)
    engine = create_async_engine(url)
    await reset_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr("aethergate.api.admin.get_session_factory", lambda: factory)

    async def _test_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[persistence_db.get_session] = _test_session

    # Seed catalog + identity via a scheduler service bound to the same engine.
    async with factory() as s:
        async with s.begin():
            await _seed_catalog(s)
            dev_ctx = await ensure_dev_identity(s)

    mock = MockAdapter()
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = _settings()
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: mock,
    )
    service = SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # bootstrap
        resp = await client.post(
            "/admin/v1/bootstrap", headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}
        )
        assert resp.status_code == 201, resp.text
        sa_hdr = {"Authorization": f"Bearer {resp.json()['raw_key']}"}

        # project A + project_admin
        resp = await client.post("/admin/v1/projects", json={"name": "project-a"}, headers=sa_hdr)
        assert resp.status_code == 201, resp.text
        proj_a = resp.json()
        resp = await client.post("/admin/v1/projects", json={"name": "project-b"}, headers=sa_hdr)
        assert resp.status_code == 201, resp.text
        proj_b = resp.json()

        async def _make_role(project, role, scopes):
            resp = await client.post(
                f"/admin/v1/projects/{project['id']}/principals",
                json={"kind": "service_account", "name": f"{project['name']}-p"},
                headers=sa_hdr,
            )
            assert resp.status_code == 201, resp.text
            pid = resp.json()["id"]
            resp = await client.post(
                "/admin/v1/role-assignments",
                json={
                    "principal_id": pid,
                    "role": role,
                    "resource_scope_type": "project",
                    "resource_id": project["id"],
                },
                headers=sa_hdr,
            )
            assert resp.status_code == 201, resp.text
            resp = await client.post(
                "/admin/v1/credentials",
                json={
                    "project_id": project["id"],
                    "principal_id": pid,
                    "name": f"{project['name']}-cred",
                    "audience": "admin",
                    "scopes": scopes,
                },
                headers=sa_hdr,
            )
            assert resp.status_code == 201, resp.text
            return {"Authorization": f"Bearer {resp.json()['raw_key']}"}

        pa_hdr = await _make_role(
            proj_a, "project_admin", ["admin:queue:read", "admin:queue:write"]
        )

        async def enqueue(project, content="secret-prompt"):
            enq = await service.admit_and_enqueue(
                alias_name="gpt-4",
                messages=[Message(role="user", content=content)],
                params=GenerationParams(),
                stream=False,
                context=RequestContext(
                    project_id=ProjectId(project["id"]),
                    principal_id=dev_ctx.principal_id,
                    api_credential_id=dev_ctx.api_credential_id,
                    audience=dev_ctx.audience,
                    scopes=dev_ctx.scopes,
                ),
            )
            return enq.request_id

        yield client, factory, service, sa_hdr, pa_hdr, proj_a, proj_b, enqueue

    app.dependency_overrides.clear()
    await reset_schema(engine)
    await engine.dispose()


async def test_http_queue_request_dto_no_content(queue_http):
    client, _factory, _service, sa_hdr, _pa_hdr, proj_a, _proj_b, enqueue = queue_http
    rid = await enqueue(proj_a, content="TOP-SECRET-PROMPT")

    resp = await client.get(f"/admin/v1/queue/requests/{rid}", headers=sa_hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for forbidden in ("payload_encrypted", "result_encrypted", "fencing_token",
                      "prompt", "content", "secret", "stream_event"):
        assert forbidden not in body
    assert body["request_id"] == rid
    assert body["state"] == "queued"


async def test_http_cross_project_request_404(queue_http):
    client, _factory, _service, sa_hdr, pa_hdr, proj_a, proj_b, enqueue = queue_http
    rid_b = await enqueue(proj_b, content="b-secret")
    resp = await client.get(f"/admin/v1/queue/requests/{rid_b}", headers=pa_hdr)
    assert resp.status_code == 404, resp.text

    # system_admin can read it.
    resp = await client.get(f"/admin/v1/queue/requests/{rid_b}", headers=sa_hdr)
    assert resp.status_code == 200, resp.text


async def test_http_pause_requires_system_admin(queue_http):
    client, _factory, _service, sa_hdr, pa_hdr, _proj_a, _proj_b, _enqueue = queue_http
    resp = await client.post(
        f"/admin/v1/queue/endpoints/{ENDPOINT_ID}/pause", headers=pa_hdr
    )
    assert resp.status_code == 403, resp.text

    resp = await client.post(
        f"/admin/v1/queue/endpoints/{ENDPOINT_ID}/pause", headers=sa_hdr
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["operational_state"] == "paused"


async def test_http_cancel_queued_request(queue_http):
    client, _factory, _service, sa_hdr, _pa_hdr, proj_a, _proj_b, enqueue = queue_http
    rid = await enqueue(proj_a)
    resp = await client.post(f"/admin/v1/queue/requests/{rid}/cancel", headers=sa_hdr)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"] == "cancelled_now"
    assert body["state"] == "cancelled"
