# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: 41364c6 (AGV2-009 queued)
- Implementation commit(s): `fix(scheduler): harden shared quota admission` (this task)

## Task Completed
AGV2-009 — harden shared-quota admission before accounting and budgets. Closed the eight
correctness gaps from the AGV2-008 review: all-or-nothing admission, per-scope FIFO scheduling,
fail-closed token estimation, monotonic cooldowns, quota-block wait metadata, and DB-enforced schema
invariants — plus a latent quota concurrency bug. No monetary project budgets (still deferred). No
changes to legacy v1 `app/` or `frontend/src/`.

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
`scripts/dev/v2 test` -> **181 passed** (was 164 at AGV2-008). `ruff check src tests` -> clean.
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
- Dynamic AetherGate test port: **39875** at last check (changes on every `up`; re-run
  `scripts/dev/v2 url`).
- Live real-backend A-E checks were **deferred this session**; their deterministic equivalents are
  covered by the 181 automated tests above.

## Decisions
- Token estimation must be provider/model-specific or it fails closed; a generic char/word bound is
  never used for enforced token quotas.
- Quota admission is all-or-nothing within a single transaction; waiting never creates phantom
  reservation history.
- Scheduling scope is persisted (endpoint + effective quota group) and revalidated before dispatch.

## Deferred
- Project monetary budgets and pricing/accounting settlement (needs price snapshots/reservations,
  then participates in the same atomic admission transaction).
- Live real-backend nomnom A-E checks (deferred; automated equivalents are green).
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
