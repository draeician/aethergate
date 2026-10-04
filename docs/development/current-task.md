# AetherGate v2 — Current Task

## Task ID
AGV2-009

## Title
Harden shared-quota admission before accounting and budgets

## Ownership
Primary: scheduler/queueing  
Coordinating: provider adapters, catalog/routing, contracts, platform/testing

## Why This Task Exists

AGV2-008 added shared request/token quotas and passed its primary nomnom tests, but code review found
several correctness gaps that must be closed before adding monetary budgets/accounting.

Do not start budget/accounting work in this task.

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
   - `src/aethergate/scheduler/repository.py`
   - `src/aethergate/scheduler/service.py`
   - `src/aethergate/adapters/base.py`
   - `src/aethergate/adapters/litellm.py`
   - `tests/test_scheduler_quota.py`
4. Preserve all AGV2-007 scheduler invariants.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated files.

## Required Fixes

### 1. Fix phantom quota reservations when endpoint capacity is unavailable

Current AGV2-008 flow reserves quota before checking endpoint physical capacity. When the endpoint is
already full, the function returns `"full"` and commits the transaction, leaving quota reservations
behind for a request that did not dispatch.

This is a correctness bug.

Required invariant:

> If a request cannot acquire every required admission resource in the same transaction, it acquires
> none of them.

For a request that remains queued because endpoint capacity is full:

- no new `QuotaReservation` rows may remain;
- quota-window `reserved_units` must not increase;
- request quota must not be committed;
- no execution attempt may be created;
- no endpoint reservation may be created.

Repeated worker claims while an endpoint stays full must not accumulate phantom quota reservations.

Preferred design:

- lock/evaluate all applicable quota group/limit/window rows in deterministic order;
- lock/evaluate endpoint capacity;
- only after every constraint is known to fit, mutate quota reservations + endpoint reservation +
  attempt/request reserved state;
- commit all those mutations together.

If a different design is used, it must prove equivalent all-or-nothing behavior.

Do not "fix" this by creating quota reservations and then leaving released audit rows on every failed
poll unless there is a strong documented reason. Waiting should not create artificial reservation
history.

### 2. Preserve all-or-nothing behavior on every pre-dispatch failure

Audit the entire claim path after quota evaluation.

At minimum verify:

- endpoint missing/inactive;
- endpoint full;
- route/provider/account revalidation failure;
- token estimation failure;
- request expiry;
- cancellation race;
- scheduler invariant failure.

If upstream contact has not begun, none of those paths may leave live quota or endpoint capacity
reserved.

Add fault-injection tests.

### 3. Remove head-of-line blocking across quota scopes sharing one endpoint

Current queue selection is FIFO per endpoint. That is insufficient now that one endpoint can serve
routes using different quota groups.

Example:

```text
endpoint E max_concurrency=2

request A1 -> quota group A (exhausted)
request B1 -> quota group B (available)
```

B1 must be allowed to dispatch even though A1 is older, because A's independent quota exhaustion
must not strand unrelated capacity.

Required ordering rule:

- FIFO within the same scheduling scope;
- a scheduling scope for this phase is at least endpoint + effective quota group;
- quota-blocked scope A must not block eligible scope B on the same endpoint;
- endpoint physical capacity is still shared across both;
- no newer request in scope A may bypass an older eligible request in scope A.

Choose a durable implementation.

A good design may persist the effective scheduling/quota scope on the queued request as non-content
metadata, but if you do this, revalidate it immediately before dispatch and handle configuration
changes safely rather than dispatching against stale policy.

Document the exact rule.

### 4. Token estimation must fail closed when no defensible estimator exists

The current LiteLLM adapter fallback:

```text
len(text) // 3
```

is not a defensible conservative upper bound for arbitrary model/tokenizer/input text.

Do not label or treat a generic character heuristic as authoritative or conservative TPM protection.

Before changing behavior, verify current LiteLLM token-counting/tokenizer behavior for the supported
provider/model path using current LiteLLM documentation/source.

Required contract:

- adapter may return a token reservation estimate only when it can identify a provider/model-specific
  method that is known to be usable for that route;
- any safety margin must be explicit and documented;
- if a configured token-quota route has no trustworthy pre-dispatch estimator, fail closed with an
  explicit safe configuration/request error;
- request-only quotas must continue to work even when token estimation is unavailable;
- never silently fall back to a generic char/word approximation for enforced token quotas.

If the current nomnom Ollama model cannot be reliably token-estimated pre-dispatch, record that
honestly. Do not fake a token-quota guarantee just to preserve the smoke test.

### 5. Cooldown updates must be monotonic and concurrency-safe

Concurrent provider 429s must never shorten an existing cooldown.

When applying a cooldown:

`new cooldown_until = max(existing cooldown_until, newly observed cooldown_until)`

Requirements:

- lock/update the shared quota scope safely;
- a later short Retry-After cannot shorten an existing longer cooldown;
- unrelated quota groups remain unaffected;
- expired cooldowns naturally stop blocking.

Add concurrent tests.

### 6. Persist useful quota-block metadata and clear stale reasons

The current `wait_reason` is too generic and can remain stale after the request becomes eligible.

For a quota-blocked request, persist non-content metadata sufficient to explain:

- effective quota group;
- limiting quota limit / metric;
- next fixed-window reset or cooldown time when known.

Use typed columns or a compact structured design; do not store prompt/completion content.

Requirements:

- future inspection can state why the request is blocked;
- worker may use `next_eligible_at` to avoid needlessly hammering the same blocked request every poll;
- once the request becomes eligible/reserved/dispatched, stale wait metadata is cleared;
- endpoint-full waiting is distinguishable from quota-window/cooldown waiting.

Do not add Redis just for wakeups.

### 7. Harden quota schema invariants

Do not rewrite migrations `0001` through `0005`.

Add migration `0006` only if needed for persisted fixes/metadata.

At the database layer, add sensible invariants where missing, including at minimum:

- quota-limit metric restricted to supported values;
- route `default_output_tokens` positive when present;
- quota window committed/reserved units nonnegative;
- quota reservation units nonnegative;
- quota reservation state restricted to supported values.

If an invariant cannot safely be added because historical rows may violate it, explicitly validate
and repair/reject those rows in the migration rather than silently accepting invalid state.

Do not rewrite `0005`.

### 8. Keep request/token commitment semantics intact

Regression requirements:

- request unit commits only at durable dispatch intent;
- token units reserve before dispatch;
- actual usage settles honestly;
- unknown post-dispatch usage commits the conservative reservation;
- `outcome_unknown` does not create capacity;
- pre-dispatch cancellation/reclaim frees reservations;
- physical endpoint slots and quota capacity remain coordinated transactionally.

## Real Nomnom Verification — Required

Use the existing dynamic-port Docker/Podman workflow.

Verify the current backend/model instead of assuming it.

### A. Phantom-reservation regression

Configure:

- endpoint max_concurrency = 1;
- shared request + token quota with ample remaining capacity.

Hold the single endpoint slot with request A.

Queue request B.

While A remains active, allow multiple worker claim/poll cycles.

Prove:

- B remains queued;
- B has zero live quota reservations;
- quota-window reserved/committed values do not increase because of B;
- B has zero attempts and zero endpoint reservations;
- after A completes, B acquires quota+endpoint capacity once and dispatches exactly once.

### B. Same-endpoint independent quota scopes

Use one physical endpoint with two route/alias scopes:

- scope/group A exhausted;
- scope/group B available.

Prove:

- an older A request remains queued;
- B dispatches on the same endpoint;
- A does not block B;
- FIFO within A remains intact;
- endpoint max_concurrency remains respected.

### C. Token-estimator truthfulness

For the current nomnom model:

- determine whether the adapter has a provider/model-specific usable estimator;
- if yes, demonstrate the method and keep the token-quota smoke test;
- if no, prove token-quota dispatch fails closed with an explicit error while request-quota-only
  dispatch still works.

Do not use prompt content in the handoff.

### D. Cooldown monotonicity

Using deterministic/mock provider feedback:

- apply a long cooldown;
- concurrently/later apply a shorter cooldown;
- prove the long cooldown remains;
- apply a later longer cooldown and prove it extends;
- unrelated group remains dispatchable.

### E. Regression

Re-run:

- official OpenAI SDK non-stream;
- official OpenAI SDK stream;
- six-request/two-slot endpoint test;
- shared request quota across aliases;
- multi-window request quota;
- scheduler invariant/dead-worker suite.

## Automated Tests

Add deterministic coverage for at least:

1. endpoint-full request leaves no quota reservation;
2. repeated endpoint-full claim attempts do not change quota windows;
3. endpoint-inactive after quota evaluation leaves no quota reservation;
4. cancellation race leaves no pre-dispatch live quota;
5. expiry race leaves no pre-dispatch live quota;
6. same endpoint + blocked group A + eligible group B dispatches B;
7. FIFO remains within group A;
8. shared endpoint max_concurrency still enforced across groups;
9. token estimator unavailable fails closed for token quota;
10. request-only quota works without token estimator;
11. no generic character heuristic used for enforced token quota;
12. cooldown update cannot shorten existing cooldown;
13. concurrent cooldown updates preserve max reset time;
14. wait metadata identifies group/limit/reset;
15. stale wait metadata clears when request becomes eligible;
16. DB constraints reject invalid quota metric/state/negative units;
17. migration 0005 -> 0006 if migration is added;
18. empty DB -> latest;
19. request quota commits only at dispatch;
20. token settlement semantics remain green;
21. original scheduler invariant suite remains green;
22. official SDK scheduled chat remains green.

## Accounting / Budget Boundary

Do not implement monetary project budgets yet.

After this task is green, the next milestone can add:

- immutable price snapshots;
- usage records;
- monetary budget reservations;
- ledger/settlement primitives;

and fold budget admission into the same resource-acquisition transaction.

Continue to preserve the distinction between:

- authorization;
- throughput quota/capacity;
- budget policy;
- usage accounting;
- pricing;
- settlement/billing.

## Documentation

Update as needed:

- `docs/architecture/scheduler.md`
- `docs/architecture/provider-model.md`
- `docs/contracts/domain-model.md`
- `docs/contracts/admin-v1-foundation.md`
- `docs/development/README.md`
- `docs/development/agent-handoff.md`

Do not modify the dated architecture audit.

## Verification Before Commit

- full test suite;
- containerized tests;
- migration verification if schema changed;
- ruff/lint;
- `git diff --check`;
- secret scan;
- legacy `app/` and `frontend/src/` untouched;
- dated audit unchanged;
- all required nomnom checks complete.

## Handoff

Update `docs/development/agent-handoff.md` with concise evidence for:

- implementation commit(s);
- phantom-reservation fix;
- exact atomic admission ordering;
- same-endpoint cross-quota scheduling result;
- token-estimator decision for the real nomnom model;
- monotonic cooldown result;
- wait-metadata behavior;
- migration revision if any;
- test count;
- dynamic AetherGate port;
- backend/model;
- issues/risks;
- exactly one recommended next step.

No credentials, prompts, completions, encryption keys, or large logs.

## Commit and Push

Use conventional commits on branch `v2`.

Suggested primary commit:

`fix(scheduler): harden shared quota admission`

A handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

The task is complete only when `origin/v2` contains the work and updated handoff and the required
nomnom quota-correctness tests pass, or a genuine blocker is documented with non-sensitive evidence.
