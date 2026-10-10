"""Observability tests (queue wait, streaming TTFT, retry rate, upstream health).

DB-gated. Two layers:

- Service/repository: direct ``AdminRequestContext`` calls against a real
  scheduler service so state transitions and SQL aggregates are deterministic.
- HTTP: the FastAPI app through ``httpx.ASGITransport`` for route wiring and
  RBAC/scoping (403/404).

Covers first-token atomicity/fencing/idempotency, provider-failure
classification, percentile correctness on known datasets, no-sample NULL
semantics, retry cohort counting, ambiguous/cancellation exclusion, 429 counting,
project scoping, and system-admin-only upstream health.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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
    ExecutionAttemptId,
    ModelAliasId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    ReservationId,
    RouteBindingId,
)
from aethergate.egress import DestinationPolicy
from aethergate.encryption import QueueEncryptor
from aethergate.errors import (
    AdminAuthorizationError,
    AdminResourceNotFound,
    AdminValidationError,
    ProviderError,
    SecretResolutionError,
    UnsupportedProvider,
)
from aethergate.identity import admin as admin_service
from aethergate.inference.service import InferenceService
from aethergate.main import app
from aethergate.observability import service as observability
from aethergate.persistence import db as persistence_db
from aethergate.persistence import models, repository
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


class _RaisingAdapter:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        raise self._exc

    async def stream(self, request: ChatRequest, secret: str | None):
        raise self._exc
        yield  # pragma: no cover - makes this an async generator


class _StreamAdapter:
    """Streams: one empty content chunk, then real content, then a usage chunk."""

    def __init__(self, *, content: str = "hello") -> None:
        self._content = content

    async def complete(self, request: ChatRequest, secret: str | None) -> CompletionResult:
        return CompletionResult(
            content=self._content,
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            upstream_request_id="u-1",
        )

    async def stream(self, request: ChatRequest, secret: str | None):
        # First chunk carries no content (role-only): must not set first_token_at.
        yield StreamChunk(content=None, finish_reason=None, usage=None)
        yield StreamChunk(content=self._content, finish_reason=None, usage=None)
        yield StreamChunk(
            content=None,
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
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


def _make_service(factory, adapter) -> SchedulingService:
    encryptor = QueueEncryptor(QueueEncryptor.generate_key())
    settings = _settings()
    inference = InferenceService(
        EnvSecretResolver(),
        DestinationPolicy(settings.upstream_allowlist_hosts),
        adapter_factory=lambda kind: adapter,
    )
    return SchedulingService(
        encryptor=encryptor,
        inference_service=inference,
        session_factory=factory,
        settings=settings,
    )


@pytest.fixture
async def obs_env(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_observability")
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
                    s, context=sa_ctx, project_id=project_id,
                    kind=PrincipalKind.USER, name=f"pr-{_new_id()}",
                )
                cred, _raw = await admin_service.create_credential(
                    s, context=sa_ctx, project_id=project_id,
                    principal_id=principal.id, name=f"cred-{_new_id()}",
                    audience=CredentialAudience.INFERENCE,
                    scopes=(CredentialScope.INFERENCE_INVOKE,), expires_at=None,
                )
        return RequestContext(
            project_id=project_id, principal_id=principal.id,
            api_credential_id=cred.id, audience=cred.audience, scopes=cred.scopes,
        )

    inf_a = await _inference_identity(proj_a.id)
    inf_b = await _inference_identity(proj_b.id)

    async def _project_role(project_id: ProjectId, role: Role, scopes):
        async with factory() as s:
            async with s.begin():
                principal = await admin_service.create_principal(
                    s, context=sa_ctx, project_id=project_id,
                    kind=PrincipalKind.SERVICE_ACCOUNT, name=f"pr-{_new_id()}",
                )
                await admin_service.create_role_assignment(
                    s, context=sa_ctx, principal_id=principal.id, role=role,
                    resource_scope_type=ResourceScopeType.PROJECT,
                    resource_id=project_id,
                )
                _, raw = await admin_service.create_credential(
                    s, context=sa_ctx, project_id=project_id,
                    principal_id=principal.id, name=f"cred-{_new_id()}",
                    audience=CredentialAudience.ADMIN, scopes=scopes, expires_at=None,
                )
        async with factory() as s:
            async with s.begin():
                return await admin_service.authenticate_admin(s, raw)

    pa_ctx = await _project_role(
        proj_a.id, Role.PROJECT_ADMIN,
        (CredentialScope.ADMIN_QUEUE_READ, CredentialScope.ADMIN_QUEUE_WRITE),
    )
    pv_ctx = await _project_role(
        proj_a.id, Role.PROJECT_VIEWER, (CredentialScope.ADMIN_QUEUE_READ,)
    )
    pa_b_ctx = await _project_role(
        proj_b.id, Role.PROJECT_ADMIN,
        (CredentialScope.ADMIN_QUEUE_READ, CredentialScope.ADMIN_QUEUE_WRITE),
    )

    yield {
        "factory": factory,
        "sa_ctx": sa_ctx,
        "pa_ctx": pa_ctx,
        "pv_ctx": pv_ctx,
        "pa_b_ctx": pa_b_ctx,
        "proj_a": proj_a.id,
        "proj_b": proj_b.id,
        "inf_by_project": {proj_a.id: inf_a, proj_b.id: inf_b},
    }
    await reset_schema(engine)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Streaming first-token transition
# ---------------------------------------------------------------------------


async def test_first_token_atomic_and_once(obs_env):
    service = _make_service(obs_env["factory"], _StreamAdapter())
    ctx = obs_env["inf_by_project"][obs_env["proj_a"]]
    enq = await service.admit_and_enqueue(
        alias_name="gpt-4", messages=[Message(role="user", content="hi")],
        params=GenerationParams(), stream=True, context=ctx,
    )
    claim = await service.claim_and_reserve("w1")
    assert not isinstance(claim, str) and claim is not None

    # Before any content: dispatched, no first_token_at.
    async with obs_env["factory"]() as s:
        attempt = (
            await s.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.id == claim.attempt_id
                )
            )
        ).scalar_one()
        assert attempt.first_token_at is None
        assert attempt.state == "dispatched"

    await service.run_stream(claim)

    async with obs_env["factory"]() as s:
        req = await sched_repo.get_request(s, enq.request_id)
        attempt = (
            await s.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.id == claim.attempt_id
                )
            )
        ).scalar_one()
        assert attempt.first_token_at is not None
        assert req.state == RequestState.SUCCEEDED
        assert attempt.started_at is not None
        assert attempt.first_token_at >= attempt.started_at


async def test_first_token_empty_chunk_does_not_set(obs_env):
    """A stream of only empty/usage chunks never records first_token_at."""
    class _EmptyAdapter(_StreamAdapter):
        async def stream(self, request, secret):
            yield StreamChunk(content=None, finish_reason=None, usage=None)
            yield StreamChunk(
                content=None, finish_reason="stop",
                usage=Usage(prompt_tokens=1, completion_tokens=0, total_tokens=1),
            )

    service = _make_service(obs_env["factory"], _EmptyAdapter())
    ctx = obs_env["inf_by_project"][obs_env["proj_a"]]
    enq = await service.admit_and_enqueue(
        alias_name="gpt-4", messages=[Message(role="user", content="hi")],
        params=GenerationParams(), stream=True, context=ctx,
    )
    claim = await service.claim_and_reserve("w1")
    await service.run_stream(claim)
    async with obs_env["factory"]() as s:
        attempt = (
            await s.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.request_id == enq.request_id
                )
            )
        ).scalars().one()
        assert attempt.first_token_at is None
        assert attempt.state == "succeeded"


async def test_mark_first_token_idempotent(obs_env):
    service = _make_service(obs_env["factory"], _StreamAdapter())
    ctx = obs_env["inf_by_project"][obs_env["proj_a"]]
    enq = await service.admit_and_enqueue(
        alias_name="gpt-4", messages=[Message(role="user", content="hi")],
        params=GenerationParams(), stream=True, context=ctx,
    )
    claim = await service.claim_and_reserve("w1")
    now = datetime.now(UTC)
    async with obs_env["factory"]() as s:
        async with s.begin():
            first = await sched_repo.mark_first_token(
                s, request_id=claim.request_id, attempt_id=claim.attempt_id,
                fencing_token=claim.fencing_token, now=now,
            )
    assert first is True
    # A second call is idempotent (rows already streaming with a timestamp).
    async with obs_env["factory"]() as s:
        async with s.begin():
            second = await sched_repo.mark_first_token(
                s, request_id=claim.request_id, attempt_id=claim.attempt_id,
                fencing_token=claim.fencing_token, now=now,
            )
    assert second is False
    async with obs_env["factory"]() as s:
        attempt = (
            await s.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.id == claim.attempt_id
                )
            )
        ).scalar_one()
        assert attempt.first_token_at is not None
        assert attempt.state == "streaming"
    assert enq  # sanity


async def test_stale_fence_cannot_set_first_token(obs_env):
    service = _make_service(obs_env["factory"], _StreamAdapter())
    ctx = obs_env["inf_by_project"][obs_env["proj_a"]]
    await service.admit_and_enqueue(
        alias_name="gpt-4", messages=[Message(role="user", content="hi")],
        params=GenerationParams(), stream=True, context=ctx,
    )
    claim = await service.claim_and_reserve("w1")
    now = datetime.now(UTC)
    async with obs_env["factory"]() as s:
        async with s.begin():
            result = await sched_repo.mark_first_token(
                s, request_id=claim.request_id, attempt_id=claim.attempt_id,
                fencing_token=claim.fencing_token + 999, now=now,
            )
    assert result is False
    async with obs_env["factory"]() as s:
        attempt = (
            await s.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.id == claim.attempt_id
                )
            )
        ).scalar_one()
        assert attempt.first_token_at is None


async def test_terminal_worker_cannot_overwrite_first_token(obs_env):
    service = _make_service(obs_env["factory"], _StreamAdapter())
    ctx = obs_env["inf_by_project"][obs_env["proj_a"]]
    enq = await service.admit_and_enqueue(
        alias_name="gpt-4", messages=[Message(role="user", content="hi")],
        params=GenerationParams(), stream=True, context=ctx,
    )
    claim = await service.claim_and_reserve("w1")
    # Force the request terminal behind the worker's back.
    async with obs_env["factory"]() as s:
        async with s.begin():
            await s.execute(
                update(models.InferenceRequest)
                .where(models.InferenceRequest.id == enq.request_id)
                .values(state=RequestState.SUCCEEDED)
            )
    now = datetime.now(UTC)
    async with obs_env["factory"]() as s:
        async with s.begin():
            result = await sched_repo.mark_first_token(
                s, request_id=claim.request_id, attempt_id=claim.attempt_id,
                fencing_token=claim.fencing_token, now=now,
            )
    assert result is False


# ---------------------------------------------------------------------------
# Provider failure classification
# ---------------------------------------------------------------------------


async def test_provider_error_marks_upstream_error_and_status(obs_env):
    service = _make_service(
        obs_env["factory"],
        _RaisingAdapter(ProviderError("upstream failed", status_code=503)),
    )
    ctx = obs_env["inf_by_project"][obs_env["proj_a"]]
    enq = await service.admit_and_enqueue(
        alias_name="gpt-4", messages=[Message(role="user", content="hi")],
        params=GenerationParams(), stream=False, context=ctx,
    )
    claim = await service.claim_and_reserve("w1")
    await service.run_complete(claim)
    async with obs_env["factory"]() as s:
        attempt = (
            await s.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.request_id == enq.request_id
                )
            )
        ).scalars().one()
        assert attempt.upstream_error is True
        assert attempt.upstream_status_code == 503
        assert attempt.state == RequestState.FAILED


async def test_provider_429_status_recorded(obs_env):
    service = _make_service(
        obs_env["factory"],
        _RaisingAdapter(ProviderError("rate limited", status_code=429)),
    )
    ctx = obs_env["inf_by_project"][obs_env["proj_a"]]
    enq = await service.admit_and_enqueue(
        alias_name="gpt-4", messages=[Message(role="user", content="hi")],
        params=GenerationParams(), stream=False, context=ctx,
    )
    claim = await service.claim_and_reserve("w1")
    await service.run_complete(claim)
    async with obs_env["factory"]() as s:
        attempt = (
            await s.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.request_id == enq.request_id
                )
            )
        ).scalars().one()
        assert attempt.upstream_error is True
        assert attempt.upstream_status_code == 429


async def test_secret_and_unsupported_not_upstream_failures(obs_env):
    for exc in (SecretResolutionError("no secret"), UnsupportedProvider("kind")):
        service = _make_service(obs_env["factory"], _RaisingAdapter(exc))
        ctx = obs_env["inf_by_project"][obs_env["proj_b"]]
        enq = await service.admit_and_enqueue(
            alias_name="gpt-4", messages=[Message(role="user", content="hi")],
            params=GenerationParams(), stream=False, context=ctx,
        )
        claim = await service.claim_and_reserve("w1")
        await service.run_complete(claim)
        async with obs_env["factory"]() as s:
            attempt = (
                await s.execute(
                    select(models.ExecutionAttempt).where(
                        models.ExecutionAttempt.request_id == enq.request_id
                    )
                )
            ).scalars().one()
            assert attempt.upstream_error is False
            assert attempt.upstream_status_code is None
            assert attempt.state == RequestState.FAILED


# ---------------------------------------------------------------------------
# Metric aggregates: queue wait, TTFT, retry
# ---------------------------------------------------------------------------


async def _direct_request(
    factory,
    *,
    project_id: str,
    stream: bool,
    queued_at: datetime,
    endpoint_id: str = ENDPOINT_ID,
) -> str:
    rid = _new_id()
    async with factory() as s:
        async with s.begin():
            s.add(
                models.InferenceRequest(
                    id=rid,
                    project_id=project_id,
                    model_alias_id="alias-gpt4",
                    endpoint_id=endpoint_id,
                    state=RequestState.SUCCEEDED,
                    stream=stream,
                    payload_encrypted=b"x",
                    queued_at=queued_at,
                    expires_at=queued_at + timedelta(hours=1),
                )
            )
    return rid


async def _direct_attempt(
    factory,
    *,
    request_id: str,
    started_at: datetime,
    finished_at: datetime,
    state: str,
    first_token_at: datetime | None = None,
    upstream_error: bool = False,
    upstream_status_code: int | None = None,
    endpoint_id: str = ENDPOINT_ID,
) -> None:
    async with factory() as s:
        async with s.begin():
            s.add(
                models.ExecutionAttempt(
                    id=_new_id(),
                    request_id=request_id,
                    endpoint_id=endpoint_id,
                    state=state,
                    fencing_token=1,
                    started_at=started_at,
                    finished_at=finished_at,
                    first_token_at=first_token_at,
                    upstream_error=upstream_error,
                    upstream_status_code=upstream_status_code,
                )
            )


async def test_queue_wait_percentiles_known_dataset(obs_env):
    factory = obs_env["factory"]
    base = datetime.now(UTC) - timedelta(minutes=5)
    # Queue waits of exactly 100, 200, 300, 400 ms -> percentile_cont values.
    for i, ms in enumerate((100, 200, 300, 400)):
        rid = await _direct_request(
            factory, project_id=obs_env["proj_a"], stream=False,
            queued_at=base + timedelta(seconds=i),
        )
        await _direct_attempt(
            factory, request_id=rid, state=RequestState.SUCCEEDED,
            started_at=base + timedelta(seconds=i, milliseconds=ms),
            finished_at=base + timedelta(seconds=i, milliseconds=ms + 10),
        )
    async with factory() as s:
        result = await observability.repository.queue_wait_percentiles(
            s, window_start=base - timedelta(minutes=1),
            window_end=base + timedelta(minutes=1), project_ids=None,
        )
    assert result.sample_count == 4
    assert result.p50_ms == pytest.approx(250.0, abs=1.0)
    assert result.p99_ms == pytest.approx(397.0, abs=1.0)
    assert result.p50_ms <= result.p95_ms <= result.p99_ms


async def test_queue_wait_no_samples_null(obs_env):
    base = datetime.now(UTC)
    async with obs_env["factory"]() as s:
        result = await observability.repository.queue_wait_percentiles(
            s, window_start=base, window_end=base + timedelta(minutes=1),
            project_ids=None,
        )
    assert result.sample_count == 0
    assert result.p50_ms is None and result.p95_ms is None and result.p99_ms is None


async def test_ttft_percentiles_streaming_only_known(obs_env):
    factory = obs_env["factory"]
    base = datetime.now(UTC) - timedelta(minutes=5)
    # Two streaming attempts with TTFT 50 and 150 ms; a non-stream attempt with a
    # first_token_at must be excluded (stream flag is False).
    s1 = await _direct_request(
        factory, project_id=obs_env["proj_a"], stream=True, queued_at=base
    )
    await _direct_attempt(
        factory, request_id=s1, state=RequestState.SUCCEEDED,
        started_at=base, finished_at=base + timedelta(milliseconds=200),
        first_token_at=base + timedelta(milliseconds=50),
    )
    s2 = await _direct_request(
        factory, project_id=obs_env["proj_a"], stream=True,
        queued_at=base + timedelta(seconds=1),
    )
    await _direct_attempt(
        factory, request_id=s2, state=RequestState.SUCCEEDED,
        started_at=base + timedelta(seconds=1),
        finished_at=base + timedelta(seconds=1, milliseconds=200),
        first_token_at=base + timedelta(seconds=1, milliseconds=150),
    )
    ns = await _direct_request(
        factory, project_id=obs_env["proj_a"], stream=False,
        queued_at=base + timedelta(seconds=2),
    )
    await _direct_attempt(
        factory, request_id=ns, state=RequestState.SUCCEEDED,
        started_at=base + timedelta(seconds=2),
        finished_at=base + timedelta(seconds=2, milliseconds=90),
        first_token_at=base + timedelta(seconds=2, milliseconds=10),
    )
    async with factory() as s:
        result = await observability.repository.ttft_percentiles(
            s, window_start=base - timedelta(minutes=1),
            window_end=base + timedelta(minutes=1), project_ids=None,
        )
    assert result.sample_count == 2  # non-stream excluded
    assert result.p50_ms == pytest.approx(100.0, abs=1.0)  # midpoint of 50,150


async def test_ttft_no_samples_null(obs_env):
    base = datetime.now(UTC)
    async with obs_env["factory"]() as s:
        result = await observability.repository.ttft_percentiles(
            s, window_start=base, window_end=base + timedelta(minutes=1),
            project_ids=None,
        )
    assert result.sample_count == 0
    assert result.p50_ms is None


async def test_retry_metrics_counts_additional_attempts(obs_env):
    factory = obs_env["factory"]
    base = datetime.now(UTC) - timedelta(minutes=5)
    # Request 1: two attempts (one retry). Request 2: one attempt. Request 3:
    # queued only (no attempt; polls must not count).
    r1 = await _direct_request(
        factory, project_id=obs_env["proj_a"], stream=False, queued_at=base
    )
    for ms in (0, 10):
        await _direct_attempt(
            factory, request_id=r1, state=RequestState.SUCCEEDED,
            started_at=base + timedelta(milliseconds=ms),
            finished_at=base + timedelta(milliseconds=ms + 5),
        )
    r2 = await _direct_request(
        factory, project_id=obs_env["proj_a"], stream=False,
        queued_at=base + timedelta(seconds=1),
    )
    await _direct_attempt(
        factory, request_id=r2, state=RequestState.SUCCEEDED,
        started_at=base + timedelta(seconds=1),
        finished_at=base + timedelta(seconds=1, milliseconds=5),
    )
    await _direct_request(
        factory, project_id=obs_env["proj_a"], stream=False,
        queued_at=base + timedelta(seconds=2),
    )
    async with factory() as s:
        result = await observability.repository.retry_metrics(
            s, window_start=base - timedelta(minutes=1),
            window_end=base + timedelta(minutes=1), project_ids=None,
        )
    assert result.attempted_requests == 2
    assert result.retried_requests == 1
    assert result.retry_attempts == 1


async def test_upstream_health_classification(obs_env):
    factory = obs_env["factory"]
    base = datetime.now(UTC) - timedelta(minutes=5)
    # success, provider failure (429), ambiguous outcome_unknown, cancelled.
    for state, upstream_error, status in (
        (RequestState.SUCCEEDED, False, None),
        (RequestState.FAILED, True, 429),
        (RequestState.OUTCOME_UNKNOWN, False, None),
        (RequestState.CANCELLED, False, None),
    ):
        rid = await _direct_request(
            factory, project_id=obs_env["proj_a"], stream=False,
            queued_at=base + timedelta(seconds=len(state)),
        )
        await _direct_attempt(
            factory, request_id=rid, state=state,
            started_at=base, finished_at=base + timedelta(seconds=1),
            upstream_error=upstream_error, upstream_status_code=status,
        )
    async with factory() as s:
        rows = await observability.repository.upstream_health(
            s, window_start=base - timedelta(minutes=1),
            window_end=base + timedelta(minutes=1),
        )
    row = next(r for r in rows if r.endpoint_id == ENDPOINT_ID)
    assert row.succeeded_attempts == 1
    assert row.upstream_failed_attempts == 1
    assert row.ambiguous_attempts == 1
    assert row.rate_limited_attempts == 1
    assert row.last_success_at is not None
    assert row.last_failure_at is not None


async def test_summary_project_scoping(obs_env):
    factory = obs_env["factory"]
    base = datetime.now(UTC) - timedelta(minutes=5)
    for project in (obs_env["proj_a"], obs_env["proj_b"]):
        rid = await _direct_request(
            factory, project_id=project, stream=False, queued_at=base
        )
        await _direct_attempt(
            factory, request_id=rid, state=RequestState.SUCCEEDED,
            started_at=base + timedelta(milliseconds=100),
            finished_at=base + timedelta(milliseconds=200),
        )
    async with factory() as s:
        summary_all = await observability.get_summary(
            s, context=obs_env["sa_ctx"], window_seconds=3600,
        )
        # project A role sees only project A (one sample).
        summary_a = await observability.get_summary(
            s, context=obs_env["pa_ctx"], window_seconds=3600,
        )
        # project B role sees only project B (one sample).
        summary_b = await observability.get_summary(
            s, context=obs_env["pa_b_ctx"], window_seconds=3600,
        )
    assert summary_all.queue_wait.sample_count == 2
    assert summary_a.queue_wait.sample_count == 1
    assert summary_b.queue_wait.sample_count == 1
    # project A role cannot request project B's explicit project (non-enumerating).
    async with factory() as s:
        with pytest.raises(AdminResourceNotFound):
            await observability.get_summary(
                s, context=obs_env["pa_ctx"], window_seconds=3600,
                project_id=obs_env["proj_b"],
            )


async def test_retry_metrics_real_scheduler_reclaim(obs_env):
    """A real pre-dispatch reclaim yields exactly one additional ExecutionAttempt.

    Reproduces the only real retry path (a request reserved but never dispatched
    whose lease expires, then reclaimed and re-claimed) using the real scheduler
    repository primitives and ``SchedulingService.recover``/``claim_and_reserve``:
    attempt #1 is reserved with an expired lease, recovery safely reclaims the
    request to ``queued`` and abandons attempt #1, and the worker claims attempt
    #2 and completes. The retry metric must count exactly one retried request and
    one retry attempt (a queued-only poll would count zero).
    """
    factory = obs_env["factory"]
    service = _make_service(factory, _StreamAdapter())
    ctx = obs_env["inf_by_project"][obs_env["proj_a"]]
    enq = await service.admit_and_enqueue(
        alias_name="gpt-4", messages=[Message(role="user", content="hi")],
        params=GenerationParams(), stream=False, context=ctx,
    )
    past = datetime.now(UTC) - timedelta(seconds=5)
    async with factory() as s:
        async with s.begin():
            request = await sched_repo.claim_next_queued_for_scope(
                s, datetime.now(UTC), ENDPOINT_ID, None, str(obs_env["proj_a"])
            )
            assert request is not None and request.id == enq.request_id
            reservation = await sched_repo.create_reservation(
                s, reservation_id=ReservationId("res-1"), request_id=enq.request_id,
                endpoint_id=ENDPOINT_ID, acquired_at=past,
            )
            await sched_repo.create_attempt(
                s, attempt_id=ExecutionAttemptId("attempt-1"),
                request_id=enq.request_id,
                endpoint_id=ENDPOINT_ID, reservation_id=reservation.id,
                fencing_token=1, worker_id="w-crashed", lease_expires_at=past,
                started_at=past,
            )
            request.state = RequestState.RESERVED
            request.worker_id = "w-crashed"
            request.fencing_token = 1
            request.lease_expires_at = past
            request.started_at = past

    await service.recover()
    async with factory() as s:
        row = await sched_repo.get_request(s, enq.request_id)
        assert row.state == RequestState.QUEUED

    second = await service.claim_and_reserve("w1")
    assert not isinstance(second, str) and second is not None
    assert second.request_id == enq.request_id
    await service.run_complete(second)

    async with factory() as s:
        result = await observability.repository.retry_metrics(
            s, window_start=datetime.now(UTC) - timedelta(minutes=10),
            window_end=datetime.now(UTC) + timedelta(minutes=1), project_ids=None,
        )
        attempts = (
            await s.execute(
                select(models.ExecutionAttempt).where(
                    models.ExecutionAttempt.request_id == enq.request_id
                )
            )
        ).scalars().all()
    assert len(attempts) == 2
    assert result.attempted_requests == 1
    assert result.retried_requests == 1
    assert result.retry_attempts == 1


async def test_upstream_health_requires_system_admin(obs_env):
    async with obs_env["factory"]() as s:
        with pytest.raises(AdminAuthorizationError):
            await observability.list_upstream_health(
                s, context=obs_env["pa_ctx"], window_seconds=3600,
            )


async def test_window_bounds_validated(obs_env):
    async with obs_env["factory"]() as s:
        with pytest.raises(AdminValidationError):
            await observability.get_summary(
                s, context=obs_env["sa_ctx"], window_seconds=10,
            )
        with pytest.raises(AdminValidationError):
            await observability.get_summary(
                s, context=obs_env["sa_ctx"], window_seconds=100_000,
            )


# ---------------------------------------------------------------------------
# HTTP / RBAC
# ---------------------------------------------------------------------------


@pytest.fixture
async def obs_http(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("AETHERGATE_TEST_DATABASE_URL not set")
    monkeypatch.setattr(admin_service, "get_settings", _settings)
    url = url_for_database(TEST_DATABASE_URL, "aethergate_test_observability_http")
    await ensure_database(url)
    engine = create_async_engine(url)
    await reset_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("aethergate.api.admin.get_session_factory", lambda: factory)

    async def _test_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[persistence_db.get_session] = _test_session

    async with factory() as s:
        async with s.begin():
            await _seed_catalog(s)
            await ensure_dev_identity(s)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/v1/bootstrap", headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}
        )
        assert resp.status_code == 201, resp.text
        sa_hdr = {"Authorization": f"Bearer {resp.json()['raw_key']}"}

        resp = await client.post("/admin/v1/projects", json={"name": "project-a"}, headers=sa_hdr)
        proj_a = resp.json()

        async def _role(role, scopes):
            resp = await client.post(
                f"/admin/v1/projects/{proj_a['id']}/principals",
                json={"kind": "service_account", "name": f"p-{_new_id()}"},
                headers=sa_hdr,
            )
            pid = resp.json()["id"]
            await client.post(
                "/admin/v1/role-assignments",
                json={
                    "principal_id": pid, "role": role,
                    "resource_scope_type": "project", "resource_id": proj_a["id"],
                },
                headers=sa_hdr,
            )
            resp = await client.post(
                "/admin/v1/credentials",
                json={
                    "project_id": proj_a["id"], "principal_id": pid,
                    "name": f"c-{_new_id()}", "audience": "admin", "scopes": scopes,
                },
                headers=sa_hdr,
            )
            return {"Authorization": f"Bearer {resp.json()['raw_key']}"}

        pa_hdr = await _role("project_admin", ["admin:queue:read"])
        yield client, sa_hdr, pa_hdr

    app.dependency_overrides.clear()
    await reset_schema(engine)
    await engine.dispose()


async def test_http_summary_system_admin(obs_http):
    client, sa_hdr, _pa_hdr = obs_http
    resp = await client.get(
        "/admin/v1/observability/summary?window_seconds=900", headers=sa_hdr
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window"]["window_seconds"] == 900
    assert body["queue_wait"]["sample_count"] == 0
    assert body["queue_wait"]["p50_ms"] is None
    assert body["ttft"]["p50_ms"] is None
    assert body["retry"]["request_retry_rate"] is None
    for forbidden in ("payload_encrypted", "content", "secret", "prompt"):
        assert forbidden not in resp.text


async def test_http_upstreams_system_admin_only(obs_http):
    client, sa_hdr, pa_hdr = obs_http
    resp = await client.get("/admin/v1/observability/upstreams", headers=pa_hdr)
    assert resp.status_code == 403, resp.text
    resp = await client.get("/admin/v1/observability/upstreams", headers=sa_hdr)
    assert resp.status_code == 200, resp.text
    assert "items" in resp.json()
