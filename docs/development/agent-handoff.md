# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: de91479 (AGV2-009V verification)
- Implementation commit(s): `fix(scheduler): harden shared quota admission` (AGV2-009);
  `docs(development): complete AGV2-009V live verification handoff` (this task)

## Task Completed
AGV2-009 — harden shared-quota admission before accounting and budgets. Closed the eight
correctness gaps from the AGV2-008 review: all-or-nothing admission, per-scope FIFO scheduling,
fail-closed token estimation, monotonic cooldowns, quota-block wait metadata, and DB-enforced schema
invariants — plus a latent quota concurrency bug. No monetary project budgets (still deferred). No
changes to legacy v1 `app/` or `frontend/src/`.

AGV2-009V — completed the live nomnom A–E verification that AGV2-009 deferred. All five scenarios
pass against the real Ollama backend; no defects found, so no code changed. Live evidence below.

## Scheduler quota behavior (post-AGV2-009)
- **Atomic admission order** (single transaction): claim oldest eligible request per scope ->
  lock quota group (FOR UPDATE) -> lock every quota limit/window in stable ID order
  (`_get_or_create_quota_window`, get-or-create via unique constraint + FOR UPDATE) -> lock
  endpoint -> evaluate endpoint physical capacity -> only then mutate quota reservations +
  endpoint reservation + attempt + request reserved state together. If any resource cannot be
  acquired, none is (all-or-nothing). `plan_quota_reservation` (lock/evaluate) is split from
  `reserve_quota` (mutate) to guarantee this.
- **Scheduling scope** = (endpoint, effective quota group). FIFO is per scope; a quota-blocked
  scope never strands unrelated capacity on the same endpoint, and endpoint `max_concurrency`
  remains shared across scopes. The effective scope is persisted on the request as non-content
  metadata (`InferenceRequest.quota_group_id`) and revalidated before dispatch.
- **Token estimation fails closed**: `LiteLLMChatAdapter.estimate_input_tokens` returns `None`
  when LiteLLM registers no provider/model-specific tokenizer (`huggingface_tokenizer_kind` is
  `None`), rather than a generic char heuristic. For the nomnom model this is `None`, so enforced
  token quotas fail with `quota_token_estimator_unavailable`, while request-only quotas still
  dispatch.
- **Cooldown monotonicity**: `set_quota_group_cooldown` locks the group and keeps
  `max(existing, new)`, so a later short Retry-After cannot shorten an existing longer cooldown.
- **Wait metadata**: quota-blocked requests persist `quota_group_id`, `wait_limit_id`,
  `wait_limit_metric`, and `next_eligible_at` (next fixed-window reset / cooldown). Cleared once
  the request becomes eligible/reserved/dispatched. Endpoint-full waiting is distinguishable from
  quota-window/cooldown waiting.
- **Commitment semantics preserved**: request units commit only at durable dispatch intent; token
  units reserve before dispatch and settle to actual usage; unknown post-dispatch usage commits the
  conservative reservation; pre-dispatch cancel/reclaim releases reserved (never committed) quota.

## Key files
- `src/aethergate/adapters/{base,litellm}.py` — `estimate_input_tokens -> int | None`;
  `huggingface_tokenizer_kind` gate; no generic char fallback.
- `src/aethergate/inference/service.py` — `estimate_tokens -> int | None`.
- `src/aethergate/persistence/models.py` — `InferenceRequest.quota_group_id`/`next_eligible_at`/
  `wait_limit_id`/`wait_limit_metric`; quota CHECK constraints.
- `src/aethergate/migrations/versions/0006_quota_hardening.py` — new columns + CHECK constraints
  (metric, reservation state, nonnegative window/reservation units, positive default_output_tokens),
  with explicit validation/rejection of invalid historical rows.
- `src/aethergate/scheduler/repository.py` — `QuotaReservationPlan`, `plan_quota_reservation`/
  `reserve_quota`, `list_queued_scopes`, `claim_next_queued_for_scope`, monotonic
  `set_quota_group_cooldown`, `set/clear_request_wait_metadata`; `_get_or_create_quota_window`
  forces fresh reads (`populate_existing=True`).
- `src/aethergate/scheduler/service.py` — `_claim_scope`, `_QuotaEvaluation`, `_evaluate_quota`.
- Tests: `tests/test_scheduler_quota.py` (27 quota scenarios), `tests/test_adapter.py`,
  `tests/test_migrations.py`, `tests/test_openai_api.py`.

## Automated tests (containerized, real PostgreSQL)
`scripts/dev/v2 test` -> **181 passed** (unchanged from AGV2-009 baseline). `ruff check src tests` ->
clean. `alembic -c src/aethergate/migrations/alembic.ini heads`/`current` -> `0006 (head)`.
`git diff --check` -> clean. Secret scan -> clean (only false-positive identifier matches).

New deterministic coverage: endpoint-full/inactive leaves no quota reservation; repeated endpoint-full
claims do not change windows; same-endpoint blocked group dispatches other group; FIFO within group on
shared endpoint; shared endpoint max_concurrency across groups; token estimator unavailable fails
closed; request-only quota without estimator; no generic char heuristic; monotonic + concurrent
cooldown; wait metadata identifies scope; stale wait metadata clears when eligible; cancellation/
recovery release token but not committed request quota; two workers cannot exceed request limit
(regression); migration 0005 -> 0006; DB constraints reject invalid rows; request quota commits only
at dispatch; token settlement semantics.

## Concurrency bug fixed (important)
AGV2-008's `commit_request_quota` re-read the quota window in the same session that had already
loaded it during reservation; with `expire_on_commit=False` the SELECT returned the stale cached
object, enabling a lost update that freed a quota slot (`test_two_workers_cannot_exceed_request_limit`
dispatched 3 instead of 2). Fixed by adding `.execution_options(populate_existing=True)` to both
SELECTs in `_get_or_create_quota_window`, so commit/settle/release always read fresh values. Verified
stable across repeated concurrent runs.

## Nomnom verification
- Host = nomnom (`192.168.22.50/24`); docker = podman 4.9.3 + docker-compose 2.40.3.
- Backend = ollama `qwen3.8-2b-distill:Q6_K` (confirmed still present via `/api/tags`).
- Token-estimator truthfulness: litellm 1.104.0 `huggingface_tokenizer_kind('ollama/qwen3.8-2b-distill:Q6_K')`
  returns `None` (no model-specific tokenizer), so token quota fails closed and request-only quota
  works — no fabricated TPM guarantee.
- Dynamic AetherGate test port for this session: **43625** (changes on every `up`/`workers`; re-run
  `scripts/dev/v2 url`).

### A. Phantom-reservation regression — PASS
Endpoint `max_concurrency=1`, group `qa-pha` (request 100/60s), alias `qa-pha-1`, 2 workers. While
A (2000-token) held the single slot, B was `queued` with `wait_reason=endpoint_full` and **zero**
quota reservations, zero execution attempts, zero endpoint reservations. The current quota window
showed `committed=1` (A only) / `reserved=0` — B did not move the counters while waiting. After A
completed, B acquired admission once, dispatched once (1 attempt), and succeeded.

### B. Same physical endpoint, independent quota scopes — PASS
Endpoint `max_concurrency=2`. Group A `qa-blk` (request 1/3600, exhaustible) vs group B `qa-open2`
(request 100/60). A1 (group A) dispatched and consumed the window; A2 (group A) stayed `queued` with
`wait_reason=quota_window_exhausted` and **zero** reservations while a newer B1 (group B) dispatched
and succeeded ~1.0s later — no head-of-line blocking across scopes. FIFO preserved within group A
(A1 before A2). Cross-group endpoint capacity stays a single pool: A+B (different groups) occupied
both slots and a third request C queued with zero reservations, then dispatched after a slot freed.

### C. Token-estimator fail-closed — PASS
Token-quota route (`qa-tok-1`, token 1000/60) failed closed with `error_code=quota_token_estimator_unavailable`
(HTTP 502, `failed`, zero reservations/attempts). Request-only route (`qa-req-1`, request 100/60)
performed real inference (HTTP 200, real completion). No heuristic fallback added.

### D. Monotonic provider cooldown — PASS
Deterministic cooldown tests green (`test_cooldown_cannot_shorten`, `test_concurrent_cooldown_preserves_max`,
`test_429_cooldown_sets_group`, `test_cooldown_expiry`). Live container demo against the real DB:
apply 600s -> set; apply 60s -> unchanged (never shortens); apply 900s -> extends; unrelated group
stays `NULL` (eligible).

### E. Real inference regression — PASS
Official OpenAI SDK 2.54.0: non-stream succeeded (`chat.completion`, real content); stream succeeded
(17 chunks). Six-request/two-slot (`max_concurrency=2`): max concurrent `dispatched` observed was
exactly **2**; all requests succeeded. Shared-alias, multi-window, and worker-recovery/invariant
behaviors are covered by the 181 deterministic tests (e.g. `test_request_quota_limits_shared_across_aliases`,
`test_multiple_request_windows_reserved_together`).

## Decisions
- Token estimation must be provider/model-specific or it fails closed; a generic char/word bound is
  never used for enforced token quotas.
- Quota admission is all-or-nothing within a single transaction; waiting never creates phantom
  reservation history.
- Scheduling scope is persisted (endpoint + effective quota group) and revalidated before dispatch.

## Deferred
- Project monetary budgets and pricing/accounting settlement (needs price snapshots/reservations,
  then participates in the same atomic admission transaction).
- Sliding-window quotas and per-provider tokenizer correctness beyond the fail-closed gate.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token. `docker compose run` needs
  `--no-deps`. The dynamic API host port changes every `up`; re-run `scripts/dev/v2 url`.
- The dev `aethergate` database currently holds aliases `gpt-4-a`/`gpt-4-b` (not `gpt-4`); the
  OpenAI models API tests were made DB-isolated this session to stop reading the dev DB.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content and the ollama backend serializes
  concurrent generation; gateway dispatch timings do not reflect upstream parallelism.

## Recommended Next Step
Start the budget/accounting milestone (deferred): add immutable price snapshots and usage records,
then fold monetary budget admission into the same atomic quota+concurrency acquisition transaction —
keeping authorization, throughput quota, budget policy, usage accounting, pricing, and settlement
distinct.
