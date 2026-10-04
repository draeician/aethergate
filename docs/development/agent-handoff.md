# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: c26d2f7 (pulled latest origin/v2; AGV2-008 queued)
- Implementation commit(s): `feat(scheduler): add shared request and token quotas` (this task)

## Task Completed
AGV2-008 — scheduler phase 2 shared provider-account request/token quotas. Added a
provider-account-scoped `QuotaGroup` with generic request/token limits, fixed UTC-epoch-anchored
windows, per-request quota reservations, a conservative adapter token estimator, bounded output
reservation, and provider-429 cooldown — all reserved transactionally with endpoint physical
capacity in a deterministic lock order. No monetary project budgets (still deferred; needs the
accounting/pricing foundation). No changes to legacy v1 `app/` or `frontend/src/`.

## Scheduler quota behavior
- **Quota model**: `QuotaGroup` requires `provider_account_id`; routes may reference a group only
  when accounts match (enforced in `create_route_binding`). A group holds multiple `QuotaLimit`s
  (`metric` requests|tokens, positive `limit_units`/`window_seconds`, `enabled`, optional name).
- **Fixed windows**: anchored to the UTC epoch (`window_start = (epoch // window) * window`), range
  `[start, start+window)`; no sliding-window semantics. `QuotaWindow` is authoritative
  (`committed_units` + `reserved_units`, unique `(quota_limit_id, window_start)`); admission checks
  `committed + reserved + requested <= limit` under row locks.
- **Request quota**: a unit commits at durable dispatch intent, before upstream contact; queued /
  pre-dispatch-cancelled work consumes none; a dispatched unit stays consumed even if upstream
  fails; one attempt == one request-unit.
- **Token quota**: pre-dispatch reserve `adapter.estimate_input_tokens(...)` + bounded output
  (`max_tokens` or `route_binding.default_output_tokens`); reject explicitly when a token-quota
  request cannot bound output or cannot fit an empty window. On success settle to actual reported
  total usage (release unused); if actual exceeds reservation, record honestly (no truncation); on
  post-dispatch failure/cancel with unknown usage commit the reserved amount conservatively;
  `outcome_unknown` keeps the reservation until reconciliation/window expiry; reconciliation commits
  (never creates) capacity; pre-dispatch cancel/reclaim releases.
- **Lock order** (deterministic): quota group (FOR UPDATE) -> quota limits (stable ID order) ->
  current quota-window rows (get-or-create via unique constraint + FOR UPDATE) -> endpoint row ->
  request/attempt/reservation. All-or-nothing: if any quota lacks capacity, none is acquired; no DB
  transaction stays open waiting for a reset.
- **Eligibility/queue**: a quota-exhausted request stays queued with non-content `wait_reason`
  (`quota_window_exhausted`/`quota_group_cooldown`), creates no execution attempt, does not spin
  hot, and does not block an unrelated endpoint/group. FIFO within endpoint+scope preserved.
- **Provider 429**: adapter error boundary preserves safe `status_code`/`retry_after_seconds` (no
  header/URL leak); 429 applies cooldown to the route's group (`cooldown_until`); no auto-retry;
  failed attempt remains consumed. Configurable fallback `provider_429_cooldown_seconds` (default 60).

## Key files
- `src/aethergate/domain/{ids,enums,entities}.py` — `QuotaLimitId`; `QuotaMetric`/
  `QuotaReservationState`; `QuotaGroup.provider_account_id`, `QuotaLimit`,
  `RouteBinding.default_output_tokens`.
- `src/aethergate/contracts/admin_v1.py` — quota group/limit DTOs + route default output.
- `src/aethergate/persistence/{models,repository}.py` — quota tables; group/limit persistence;
  route-binding account-consistency validation.
- `src/aethergate/migrations/versions/0005_shared_quotas.py` — quota schema + cooldown + wait_reason.
- `src/aethergate/scheduler/repository.py` — `fixed_window_start`, `_get_or_create_quota_window`,
  `reserve_quota`/`commit_request_quota`/`settle_token_quota`/`release_quota_reservations`,
  `set_quota_group_cooldown`; reconcile commits reserved tokens.
- `src/aethergate/scheduler/service.py` — `_reserve_quota_capacity`, quota reserve/commit/settle/
  release/cooldown integration, `wait_reason`, `ClaimedWork.quota_group_id`.
- `src/aethergate/adapters/{base,litellm}.py` — `estimate_input_tokens` protocol + LiteLLM impl;
  `ProviderError(status_code, retry_after_seconds)` in `errors.py`.
- `src/aethergate/inference/service.py` — `estimate_tokens` helper; `config.py` —
  `provider_429_cooldown_seconds`.
- `src/aethergate/devseed.py` — `--quota-group`/`--request-limit`/`--token-limit`/
  `--default-output-tokens` (idempotent); `inspect_queue.py` — quota windows + limiting reasons.
- Tests: `tests/test_scheduler_quota.py` (18 scenarios), `tests/test_domain_entities.py`,
  `tests/test_contracts.py`, `tests/test_adapter.py`.

## Automated tests (containerized, real PostgreSQL)
`scripts/dev/v2 test` -> **164 passed** (was 140 before AGV2-008; +18 scheduler-quota, +3 domain,
+3 contracts). `ruff check src tests` -> all checks passed. `git diff --check` -> clean. Secret scan
(file-name + content regex) -> clean.

Coverage map (deterministic): quota limit domain/DTO validation; group-belongs-to-account; route
cannot reference another account's group; fixed-window boundary; request-quota limit across aliases;
quota exhaustion does not reserve endpoint slot; multiple request windows reserved together; two
workers cannot exceed request limit; token reservation before dispatch; successful settlement to
actual; over-reservation release; actual>reservation recorded honestly; dispatched failure commits
reservation; pre-dispatch cancellation releases token; `outcome_unknown` keeps token reservation;
request larger than empty window fails; unbounded output rejected; 429 cooldown scope; cooldown
expiry; saturated group does not block unrelated group.

## Nomnom verification (actual commands/results)
- Host = nomnom (`192.168.22.50/24`); docker = podman 4.9.3 + docker-compose 2.40.3. Backend =
  ollama `qwen3.8-2b-distill:Q6_K` (verified via `/api/tags`).
- Migrations: empty DB -> head (0001..0005) and live 0004 -> 0005 both applied cleanly
  (`scripts/dev/v2 migrate`).
- **Shared request quota across aliases**: seeded group `qa` (request 2/30s, token 60000/60s,
  default output 64, endpoint `max_concurrency=4`) shared by aliases `gpt-4-a` and `gpt-4-b`. Fired
  6 concurrent requests -> 2 dispatched per 30s fixed window, 4 durably queued with
  `wait_reason=quota_window_exhausted`, then dispatched in subsequent windows (completion times
  ~0.5s / 25.7s / 55.6s = 30s cadence). `inspect` showed `qa requests 2/2` committed per window
  across six windows — never oversubscribed.
- **Token quota**: `qa tokens` windows settled to actual usage (25 tokens/request = 17 prompt + 8
  completion; reservation was the conservative input estimate + 64 output). `inspect` showed e.g.
  `qa tokens 100/60000` for 4 requests.
- **Regression (official OpenAI SDK)**: non-stream and stream (`stream=True`) both succeeded against
  the live backend through the gateway (`gpt-4-a`, finish `stop`, 6 stream chunks).
- Dynamic AetherGate test port during verification: **39875** (changes on every `up`; re-run
  `scripts/dev/v2 url`).

## Decisions
- Windows are fixed and UTC-epoch-anchored; no sliding-window semantics (documented).
- `committed_units` records actual usage honestly and may exceed `limit_units`; overage is not
  truncated or hidden.
- Token estimation is a conservative upper bound; a fallback is documented as such and never
  masquerades as an authoritative provider token count.
- Unknown-usage post-dispatch failure commits the reserved amount (never releases, never invents);
  reconciliation likewise commits reserved tokens — it can never create capacity.
- A single global advisory lock remains for the queue cap; quota windows use their own row locks
  (per limit+window), so the queue cap and quota admission are independent.

## Deferred
- Project monetary budgets and pricing/accounting settlement (needs price snapshots/reservations,
  then participates in the same atomic admission transaction).
- Bounded retries with jitter for eligible failures.
- Sliding-window quotas (if ever needed) and per-provider tokenizer correctness beyond the
  conservative estimate.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token. `docker compose run` needs
  `--no-deps`. The dynamic API host port changes every `up` (and when scaling workers recreates the
  api container); re-run `scripts/dev/v2 url`.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content (upstream behavior, not a gateway
  defect), and the ollama backend serializes concurrent generation, so gateway dispatch timings do
  not reflect upstream parallelism.
- The LiteLLM token estimator is conservative but not verified as authoritative for this model; the
  gateway treats it as an upper-bound reservation, never as a TPM guarantee (see `scheduler.md`).
- The devseed derives endpoint name from provider `kind`; multiple distinct endpoints require
  distinct provider kinds. Consider making endpoint identity an explicit seed arg later.

## Recommended Next Step
Implement project monetary budgets: add price snapshots/reservations, then fold budget checks into
the same atomic quota+concurrency admission transaction — without conflating budget with throughput
quota.
