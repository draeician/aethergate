# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: 4ce9e8d (pulled latest origin/v2)
- Implementation commits: 687232b (AGV2-004), `27f552b` + AGV2-005 commits (this task)

## Task Completed
AGV2-005 — durable scheduler phase 1: PostgreSQL queue, worker ownership, and endpoint concurrency.
Replaced the direct-dispatch inference path with a durable scheduler: the API enqueues an encrypted
request and a separate worker process claims/dispatches it, enforcing physical endpoint concurrency
(`endpoint.max_concurrency`), with multi-worker ownership via leases + fencing tokens and conservative
recovery. Verified real inference and concurrency on nomnom with two workers.

## Scheduler phase 1 behavior
- `POST /v1/chat/completions` validates/resolves, enqueues an encrypted request, then waits for a
  terminal result (non-stream) or replays the encrypted per-request stream events (SSE). The API
  process never calls the provider adapter.
- The worker claims FIFO via `FOR UPDATE SKIP LOCKED`, re-resolves/revalidates the route by alias id,
  locks the endpoint row, counts active reservations vs `max_concurrency`, then transactionally
  reserves + creates an execution attempt with a lease and fencing token. Dispatch intent is committed
  durably before contacting upstream; inference runs outside any DB transaction.
- Concurrency contract verified on nomnom: with `max_concurrency=2` and six simultaneous requests,
  exactly two dispatch at once, the rest queue FIFO, and each slot releases on completion.
- Conservative recovery: expired lease on a `reserved` (pre-dispatch-intent) request is safely
  requeued; expired lease after durable dispatch intent goes `outcome_unknown` and keeps its slot;
  fencing token gates terminal settlement. Verified queued requests survive worker restart and drain
  once a worker returns.

## Encryption of queued content
- `src/aethergate/encryption.py` — `QueueEncryptor` (Fernet) + `encryptor_from_key`; key from
  `AETHERGATE_QUEUE_KEY`, required in `prod`, never committed/logged. Prompt/messages, completion, and
  stream-event content are encrypted at rest in PostgreSQL; scheduling metadata is plaintext.
- `src/aethergate/errors.py` — `QueueKeyError`, `QueueFull`, `QueueTimeout`.

## Scheduler schema (migration `0003`)
- `endpoints.max_concurrency`; tables `inference_requests`, `reservations`, `execution_attempts`,
  `stream_events`; `fencing_token` switched to `sa.BigInteger`. Applied on nomnom via `migrate`.
- `src/aethergate/persistence/models.py` + `repository.py` add scheduler models and helpers
  (`update_endpoint_max_concurrency`, dev-identity getters).

## Key files
- `src/aethergate/scheduler/` — `repository.py` (claim/reserve/settle/recover/stream), `service.py`
  (`SchedulingService`), `runtime.py` (shared service builders), `__init__.py`.
- `src/aethergate/api/openai_chat.py` + `api/deps.py` — scheduler-backed chat path; `dev_request_context`.
- `src/aethergate/inference/service.py` (`prepare_from_resolved`/`validate_destination`) +
  `catalog/service.py` (`resolve_model_alias_by_id`).
- `src/aethergate/worker.py` (worker loop), `dev_identity.py` (`ensure_dev_identity`),
  `inspect_queue.py` (queue-state summary), `config.py` (scheduler settings + prod queue-key check).
- `deploy/v2/compose.yaml` (worker service + queue key env), `scripts/dev/v2` (`workers`, `inspect`,
  queue-key generation).
- Tests: `tests/test_encryption.py`, `tests/test_scheduler.py`, `tests/test_openai_api.py` (rewritten),
  `tests/conftest.py` (`sched_engine` DB fixture), `tests/db_helpers.py` (`reset_schema`),
  `tests/test_settings.py`, `tests/test_persistence_models.py`, `tests/test_migrations.py`.

## Nomnom verification (actual commands/results)
- Host = nomnom (`192.168.22.50/24`); `docker` = podman 4.9.3 + docker-compose 2.40.3.
- `scripts/dev/v2 build` -> `aethergate-v2:local`; `scripts/dev/v2 up` -> API on host port 45241;
  `scripts/dev/v2 migrate` applied `0003`; `scripts/dev/v2 workers 2` -> two worker containers.
- Seeded `devseed --max-concurrency 2` (kind=ollama, upstream=`qwen3.8-2b-distill:Q6_K`, alias=`gpt-4`,
  destination=`http://192.168.22.50:11434`); endpoint_max_concurrency reported `2`.
- Six simultaneous OpenAI SDK chat requests (concurrency test) all succeeded; DB timestamps showed
  exactly two in-flight at a time (A/B dispatched at t=17.87/17.95, C/D at t=22.11/22.52 after A/B
  finished, E/F at t=22.90/23.31), FIFO order preserved.
- OpenAI SDK stream through scheduler -> 21 chunks reassembled.
- Queue-content at-rest check -> no plaintext prompt/completion bytes in `payload_encrypted`/
  `result_encrypted`; 0 requests with multiple execution attempts (no double dispatch).
- Stopped both workers, enqueued 3 requests (durably queued), restarted one worker -> all 3 drained and
  completed. Restarted worker-2; stack healthy (`inspect` shows `succeeded: 7`, active reservations 0).
- `scripts/dev/v2 test` -> **111 passed in 19.60s** (containerized full suite).

## Decisions
- Phase 1 enforces only physical endpoint concurrency; RPM/TPM, token reservation, shared-account
  quotas, project budgets, and retry/cooldown remain later phases and must be reserved together.
- Encryption key lives outside PostgreSQL and is required in `prod`; scheduling metadata stays
  plaintext for correctness, content is encrypted.
- Conservative recovery: never auto-replay an ambiguous (post-dispatch-intent) attempt; `outcome_unknown`
  keeps its physical slot until an operator resolves it.
- FIFO is admission/dispatch order, not completion order; head-of-line fairness is deferred.

## Deferred
- Provider RPM/TPM windows; TPM/token reservation; shared provider-account quota windows; project
  budgets; retries/cooldown orchestration.
- Fairness/reordering for head-of-line blocking; caching/wake-up for empty-queue arrival.
- Queue-content encryption key rotation.
- `/v1/responses`; scoped API-credential auth to replace the dev bypass; full admin CRUD API; v1
  SQLite migration.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token. `docker compose run` needs
  `--no-deps`. The dynamic API host port changes every `up`; re-run `scripts/dev/v2 url`.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content (upstream behavior, not a gateway defect).
- `scripts/dev/v2 workers N` requires the worker service to have no fixed `container_name` (removed);
  scaling uses `up -d --scale worker=N`.
- Pre-commit hook scans staged content for `password=`/`secret=`/`api_key=`/`sk-` patterns; keep
  fixture literals value-neutral.

## Recommended Next Step
Implement `/v1/responses` (preferred modern surface) using the scheduler/encryption path, and add
scoped API-credential authentication to replace the temporary development auth bypass.
