# AetherGate v2 — Current Task

## Task ID
AGV2-008

## Title
Scheduler phase 2 — shared provider-account request/token quotas

## Ownership
Primary: scheduler/queueing  
Coordinating: catalog/routing, provider adapters, contracts, platform/testing

## Why This Task Exists

AGV2-004 through AGV2-007 established and hardened:

- real OpenAI-compatible inference;
- durable PostgreSQL queueing;
- multi-worker ownership;
- endpoint physical concurrency;
- leases/fencing;
- encrypted queued content;
- conservative ambiguous-outcome handling;
- per-endpoint FIFO without unrelated-endpoint head-of-line blocking;
- atomic queue admission and deadline handling.

The next scheduler layer is shared provider capacity.

This task adds provider-account/shared-quota request and token windows and reserves them
transactionally with endpoint physical capacity.

It does **not** add project monetary budgets yet. Monetary budgets require the accounting/pricing
foundation and must not be faked by conflating quota with billing.

## Before You Start

1. Work on branch `v2`.
2. Run `git pull --ff-only origin v2`.
3. Read:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/agent-handoff.md`
   - `docs/development/current-task.md`
   - `docs/architecture/scheduler.md`
   - `docs/architecture/provider-model.md`
   - `docs/contracts/domain-model.md`
   - `src/aethergate/scheduler/repository.py`
   - `src/aethergate/scheduler/service.py`
   - `src/aethergate/adapters/base.py`
   - `src/aethergate/adapters/litellm.py`
4. Preserve all AGV2-007 scheduler invariants.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated local/untracked files.

## Goal

AetherGate must understand and enforce configured shared provider capacity such as:

- 30 requests / 60 seconds;
- 5,000 requests / 86,400 seconds;
- 60,000 tokens / 60 seconds;

where multiple aliases and endpoints share the same provider-account quota scope.

When a configured quota window lacks capacity, an otherwise valid request remains durably queued
until the relevant window can admit it, subject to its queue and total deadlines.

The scheduler must reserve **all applicable configured capacity together**:

- endpoint physical concurrency;
- every applicable request window;
- every applicable token window.

No partial admission.

## Quota Model

Extend `QuotaGroup` from descriptive metadata into an explicit shared allowance scope.

### Scope ownership

For this phase, a QuotaGroup belongs to exactly one `ProviderAccount`.

Requirements:

- add `provider_account_id` to QuotaGroup;
- routes may reference a quota group only when that group's provider account matches the route's
  provider account;
- multiple aliases/endpoints/routes may share one quota group;
- creating another alias/endpoint never creates independent provider quota;
- one provider account may have more than one explicitly configured quota group when the operator
  intentionally separates scopes;
- no quota is inferred from URL or provider name.

If a route has no quota group, it is governed only by endpoint physical concurrency until a group is
configured. Do not silently invent limits.

### Generic limits

A quota group may contain multiple independent limits.

Model a reusable quota-limit contract with at least:

- stable opaque ID;
- quota_group_id;
- metric: `requests` or `tokens`;
- limit_units: positive integer;
- window_seconds: positive integer;
- enabled;
- optional descriptive name.

This must support multiple simultaneous request windows (for example RPM + RPD) and multiple token
windows without special-casing "minute" or "day" in the scheduler.

Add a typed `QuotaLimitId`.

Use fixed windows anchored deterministically to UTC epoch for this phase. Document exact boundary
semantics.

Do not claim sliding-window semantics.

## Token Reservation Contract

Token quotas require conservative pre-dispatch reservation.

Add a narrow token-estimation interface to the provider-adapter boundary.

Requirements:

- estimation happens before quota reservation/dispatch;
- it must not contact the upstream inference service;
- the estimate must include input tokens plus a bounded output reservation;
- provider/model-specific tokenizer behavior stays behind the adapter;
- the scheduler consumes only a numeric conservative reservation value;
- no generic whitespace/character-count heuristic may silently masquerade as an authoritative
  provider token count.

### Output reservation

A request with a token quota must have a bounded output reservation.

Implement explicit route/model configuration for a default output-token reservation used when the
client omits `max_tokens` / the equivalent supported field.

Requirements:

- positive integer;
- if client supplies a smaller explicit max, reserve that explicit max;
- if client supplies a larger explicit max, reserve the explicit requested max;
- if token quota applies and neither an explicit client max nor configured default can bound output,
  reject configuration/request explicitly rather than dispatch without a safe reservation;
- preserve current OpenAI request semantics otherwise.

Use the adapter's tokenizer/estimator for input tokens and add the output reservation.

For the current nomnom Ollama model, verify the chosen LiteLLM-backed estimator against actual usage
well enough to establish that reservations are conservative. If the adapter cannot reliably estimate
that model, document the blocker and do not fake TPM correctness.

## Persistence / Migration

Do not rewrite migrations `0001` through `0004`.

Add migration `0005` (and only additional revisions if genuinely necessary).

Persist at least:

- QuotaGroup -> ProviderAccount ownership;
- quota limit definitions;
- quota window/bucket state;
- per-request quota reservations/commitments;
- route/model default output-token reservation if used by the chosen design;
- quota cooldown/reset metadata required for provider feedback.

Use integer token/request units. No floats.

### Window authority

PostgreSQL is authoritative.

A quota window record must permit atomic evaluation of:

`committed_units + reserved_units + requested_units <= limit_units`

under row locks.

Window creation at boundaries must be safe under concurrent workers.

Do not use process-local counters.

## Atomic Admission / Lock Ordering

Extend the existing endpoint reservation transaction so all applicable capacity is acquired together.

Define and document one deterministic lock order.

Recommended conceptual order:

1. quota group / quota limits in stable ID order;
2. current quota-window rows in stable metric/window/ID order;
3. endpoint row / physical capacity;
4. request/attempt/reservation state.

A different order is acceptable if it is deterministic and tested.

Requirements:

- if any required quota lacks capacity, acquire **none** of the capacity for that request;
- do not hold endpoint capacity while merely waiting for a future request/token window;
- no DB transaction remains open while waiting for a reset;
- multiple workers cannot oversubscribe a quota window;
- quota-authority DB failure means no new dispatch.

## Request Quota Semantics

A request unit is consumed when durable dispatch intent is committed immediately before upstream
contact.

Requirements:

- queued/pre-dispatch-cancelled work consumes no request quota;
- safe pre-dispatch reclaim consumes no request quota;
- once dispatch intent is durable, the request unit remains consumed even if the upstream call
  later fails;
- retries are still out of scope, so one execution attempt == one request-unit commitment.

## Token Quota Semantics

Before dispatch:

- reserve the conservative token amount.

On a successful completion with trustworthy usage:

- settle reserved tokens to actual provider-reported total usage;
- release unused reservation within the same active window;
- if reported usage exceeds the reservation, record actual usage honestly and mark the window
  over-limit; do not hide or truncate the overage.

For a known provider failure/cancellation after dispatch:

- if trustworthy usage exists, settle to that usage;
- if trustworthy usage is unavailable, conservatively commit the reserved token amount.

For `outcome_unknown`:

- do not release the token reservation as if nothing happened;
- conservatively retain/commit the reservation for the affected window until explicit reconciliation
  or window expiry according to the documented design;
- reconciliation must never create additional capacity based on an invented token count.

Pre-dispatch cancellation/reclaim releases token reservations because upstream was never contacted.

## Eligibility / Queue Behavior

When the endpoint has a free physical slot but a quota window is exhausted:

- leave the request queued;
- do not create a provider execution attempt;
- do not spin hot;
- calculate/persist enough eligibility/reset information to avoid treating it as generic endpoint
  saturation;
- another request for an unrelated endpoint/quota group with available capacity must still dispatch.

Within the same endpoint + quota scope, preserve FIFO for eligible work.

A request that cannot fit even an empty configured token window because its required token reservation
exceeds the limit must be rejected/failed explicitly rather than waiting forever.

## Provider 429 / Reset Feedback

Extend the provider adapter error boundary to preserve safe structured rate-limit feedback when
available:

- HTTP/upstream status;
- Retry-After seconds or absolute reset time when reliably supplied;
- no raw headers containing credentials;
- no upstream URL leakage.

On provider 429:

- apply cooldown to the actual configured shared quota group/provider-account scope used by the route;
- future work for that scope remains queued until cooldown expires;
- do not automatically retry the failed request in this task;
- the failed dispatched attempt remains a consumed request unit;
- token settlement follows the conservative rules above.

If reliable Retry-After/reset information is absent, apply a small configurable conservative cooldown
rather than hammering the upstream.

Do not claim AetherGate can prevent all provider 429s when limits are unknown or shared outside the
gateway.

## Domain / Admin Contracts

Update domain and admin v1 DTO foundations for:

- QuotaGroup provider-account scope;
- QuotaLimit create/read/update;
- route/model default output-token reservation configuration;
- effective quota metadata necessary for future CLI/UI.

No admin HTTP CRUD routes yet.

Validate:

- positive limits;
- positive window duration;
- valid metric enum;
- quota group / provider account consistency at service/persistence boundary.

## Development Seed

Extend the dev seed without hard-coding nomnom specifics.

Support defining:

- provider/account/endpoint/alias/route;
- endpoint max concurrency;
- shared quota group;
- one or more request limits;
- one or more token limits;
- default output-token reservation.

Make it idempotent.

The current no-quota seed path should continue to work.

## Inspection / Observability

Extend the scheduler inspection tooling to show non-content metadata such as:

- quota group;
- metric;
- configured limit;
- window start/end;
- committed units;
- reserved units;
- cooldown-until;
- request's limiting constraint / next eligible time when known.

Never show prompts/completions/secrets.

A queued request should be explainable as, for example:

`waiting: quota group provider-acct-a tokens 60000/60000 until 12:34:00Z`

rather than merely "queued".

## Real Nomnom Verification — Required

Use the existing dynamic-port Docker/Podman workflow.

Verify the current backend/model rather than assuming it is unchanged.

### A. Shared request quota across aliases/endpoints

Configure two routes/aliases sharing one provider account + one quota group.

Use a small test limit such as 2 requests per short window.

Prove:

- endpoint physical capacity may be available;
- only 2 requests dispatch in the configured quota window;
- additional requests remain queued;
- after the next fixed window begins, queued work dispatches;
- both aliases consume the same shared quota;
- two workers cannot oversubscribe the limit.

### B. Multiple simultaneous request windows

Configure both a short and a longer request window on the same group.

Prove dispatch requires capacity in **both** windows and that exhausting either one blocks dispatch.

### C. Token quota

Configure a deliberately small token window suitable for testing.

Prove:

- pre-dispatch token reservation occurs;
- queued requests cannot collectively oversubscribe the configured window;
- successful usage settles the reservation to actual usage;
- unused reserved units become available again when safe;
- a request too large for an empty token window is rejected explicitly;
- two workers cannot oversubscribe tokens.

Use non-sensitive prompts; do not record their content in the handoff.

### D. Provider feedback

If practical with a controlled local/mock upstream, return a synthetic 429 + Retry-After through the
adapter contract and prove:

- the shared quota group enters cooldown;
- unrelated quota groups remain dispatchable;
- no automatic retry occurs.

This can be deterministic integration testing rather than forcing the real Ollama backend to emit a
429.

### E. Regression

Re-run:

- official OpenAI SDK non-stream;
- official OpenAI SDK stream;
- six-request/two-slot endpoint concurrency;
- lease/dead-worker tests;
- cross-endpoint no-HOL behavior.

## Automated Tests

Add deterministic coverage for at least:

1. quota limit domain/DTO validation;
2. quota group belongs to provider account;
3. route cannot reference another account's quota group;
4. fixed-window boundary calculation;
5. concurrent window creation is safe;
6. atomic request-window reservation;
7. two workers cannot exceed request limit;
8. two aliases share one request quota;
9. multiple request windows are reserved together;
10. token estimator boundary;
11. output-token default behavior;
12. unbounded token-quota request rejected;
13. token reservation before dispatch;
14. successful token settlement to actual usage;
15. over-reservation releases unused units;
16. actual usage greater than reservation is recorded honestly;
17. dispatched failure with unknown usage conservatively consumes reservation;
18. pre-dispatch cancellation releases token reservation;
19. outcome_unknown does not free token capacity;
20. request larger than empty token window fails explicitly;
21. quota exhaustion does not reserve endpoint slot;
22. saturated quota group does not block unrelated group;
23. provider 429 cooldown scope;
24. cooldown expiry;
25. no automatic retry;
26. quota-authority DB failure prevents dispatch;
27. existing scheduler invariant suite remains green;
28. official SDK scheduled chat tests remain green.

## Accounting / Budget Boundary

Do **not** implement monetary project budgets in this task.

Document explicitly that:

- throughput quota/capacity policy is being implemented here;
- usage accounting, pricing, and settlement remain separate;
- project monetary budgets will later participate in the same atomic admission transaction once
  price snapshots/reservations exist;
- a positive monetary balance is not a universal authorization requirement;
- prepaid billing remains possible but is not selected as the product model.

## Documentation

Update:

- `docs/architecture/scheduler.md`
- `docs/architecture/provider-model.md`
- `docs/contracts/domain-model.md`
- `docs/contracts/admin-v1-foundation.md`
- `docs/development/README.md`
- `project_spec.md` only if needed for clarification
- `docs/development/agent-handoff.md`

Do not modify the dated architecture audit.

## Verification Before Commit

- full test suite;
- containerized tests;
- existing DB `0004 -> 0005`;
- empty DB -> latest;
- ruff/lint;
- `git diff --check`;
- secret scan;
- legacy `app/` and `frontend/src/` untouched;
- dated audit unchanged;
- required nomnom quota verification completed.

## Handoff

Update `docs/development/agent-handoff.md` with concise evidence for:

- implementation commit(s);
- migration revision(s);
- quota schema/lock order;
- request-window semantics;
- token-reservation/settlement semantics;
- actual shared-quota nomnom test;
- multi-worker oversubscription proof;
- 429 cooldown test;
- regression test count;
- dynamic AetherGate host port;
- current backend/model;
- issues/risks;
- exactly one recommended next step.

Do not include prompt/completion bodies, credentials, encryption keys, or large logs.

## Commit and Push

Use conventional commits on branch `v2`.

Suggested primary commit:

`feat(scheduler): add shared request and token quotas`

A handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

The task is complete only when `origin/v2` contains the changes and updated handoff and the required
nomnom shared-quota tests pass, or a genuine technical/environmental blocker is documented with
non-sensitive evidence.
