# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: `31871c2` (AGV2-010 task specification); AGV2-010 completed via
  `feat(accounting): add pricing usage and project budgets` + `docs(development): complete AGV2-010
  accounting handoff`.
- AGV2-011 implementation commit(s): `fix(accounting): harden budget scheduling and price
  invariants` (+ this `docs(development): complete AGV2-011 accounting hardening handoff` follow-up).

## Task Completed
AGV2-011 — harden accounting and budget scheduling invariants. All seven fixes landed:

1. Scheduling scope extended from `(endpoint_id, quota_group_id)` to
   `(endpoint_id, quota_group_id, project_id)`; null project is its own scope. A budget-blocked
   project no longer head-of-line blocks another project sharing the same endpoint/quota resources.
2. Canonical lock order implemented and documented identically: request/scope rows -> active price
   policy -> project budget policy/windows (stable ID order) -> provider quota group/limit/windows
   (stable ID order) -> endpoint physical capacity -> request/attempt/reservation mutations. No
   transaction spans provider inference.
3. Exactly one enabled `PricePolicy` per `RouteBinding`, enforced by a partial unique index and
   surfaced as `PricePolicyConflictError` (clear domain error, never a scheduler
   `MultipleResultsFound`).
4. Billing-unit-specific price shape enforced at domain, DTO, service, and DB CHECK layers: request
   billing requires `request_price` (token prices rejected); token billing requires both
   `input_price`/`output_price` (`request_price` rejected). Missing price != zero; explicit
   `Decimal("0")` is a valid zero price.
5. Idempotent settlement now detects conflicting replays: identical replay is a no-op; conflicting
   replay raises `AccountingInvariantError` (internal-only) with an explicit canonical equality set
   that excludes volatile timestamps.
6. Pre-dispatch snapshot lifecycle: the snapshot is discarded on pre-dispatch cancel/reclaim/
   dispatch failure (and released budget reservations detach their `price_snapshot_id`); only work
   reaching durable dispatch intent retains one immutable snapshot.
7. Price-policy edit cannot race historical capture: the active policy is locked and the reservation
   amount + snapshot derive from the same canonical values.

No changes to legacy v1 `app/` or `frontend/src/`.

## Migration
- Revision `0008` (`accounting invariants`). Live `0007 -> 0008` succeeded on the running dev DB
  (current `alembic_version = 0008`); empty-DB -> latest covered by `tests/test_migrations.py`.
- Adds the one-enabled-policy-per-route partial unique index
  (`uq_price_policies_one_enabled_per_route` on `route_binding_id WHERE enabled`),
  `ck_price_policies_request_shape` / `ck_price_policies_token_shape` CHECK constraints, and drops
  the NOT NULL on `budget_reservations.price_snapshot_id`.
- Migration detects invalid existing rows (shape-violating or duplicate-enabled price policies)
  before adding constraints, and fails with a clear diagnostic rather than guessing or reinterpreting
  missing prices as zero. `0001`–`0007` untouched.

## Lock order (canonical, tested)
request/scope row(s) -> active price policy -> project budget policy/windows (stable ID order) ->
provider quota group/limit/windows (stable ID order) -> endpoint physical capacity ->
request/attempt/reservation mutations. No open transaction spans provider inference.

## Automated tests
`scripts/dev/v2 test` -> **220 passed** (was 202; +17 accounting tests +1 migration test).
`ruff check src tests` clean. `tests/test_accounting.py` adds coverage for cross-project no-HOL
(fix 1), FIFO-within-project, cross-project endpoint/quota concurrency, one-enabled-price-policy
(fix 3), concurrent-enable conflict, request/token price shape incl. missing-vs-zero (fix 4),
identical/conflicting UsageRecord and LedgerEntry replay (fix 5), pre-dispatch snapshot lifecycle
(cancel/expiry/dispatch-failure vs. normal dispatch; fix 6), concurrent price-edit consistency
(fix 7), and the accounting/admission stress/deadlock scenarios. `tests/test_migrations.py` adds
`0007 -> 0008` (and empty-DB->latest) plus `_indexes`/`_nullable_columns` introspection helpers.
`git diff --check` clean; secret scan clean.

## Real nomnom verification (live, real Ollama)
Host = nomnom (`192.168.22.50/24`); docker = podman 4.9.3 + docker-compose 2.40.3. Backend = ollama
`qwen3.8-2b-distill:Q6_K`; litellm 1.104.0 `huggingface_tokenizer_kind(...)` returns `None` (no
trusted estimator). Dynamic AetherGate port this session: **37223** (changes on every `up`/`workers`;
re-run `scripts/dev/v2 url`).

- **Migration — PASS.** `scripts/dev/v2 migrate` ran `0007 -> 0008` cleanly; `alembic_version=0008`;
  both shape CHECK constraints and the partial unique index present in `pg_constraint`.
- **B. Single enabled price policy — PASS (live).** `INSERT` of a second `enabled` policy on a route
  already carrying an enabled policy was rejected by `uq_price_policies_one_enabled_per_route`.
- **C. Price-shape validation — PASS (live).** Request billing with `request_price NULL` rejected by
  `ck_price_policies_request_shape`; token billing with missing `output_price` rejected by
  `ck_price_policies_token_shape`; explicit `request_price = 0` accepted.
- **F. Regression — PASS (live).** Official OpenAI SDK 2.54.0 non-stream + stream succeeded
  (`qa-open-1`); request-priced route `budget-req` dispatched and settled to a `usage_record`
  (`billing_unit=request`, `request_units=1`, `amount=0.05 USD`) plus a `usage_debit` ledger entry.
  Full 220-test suite green (covers six-request/two-slot, shared/cross-quota, recovery/fencing).
- **D (snapshot lifecycle) — live-consistent.** `scripts/dev/v2 inspect` shows non-succeeded
  requests with `snapshot=- usage_record=-` (no orphan snapshot) and every `succeeded` request with
  exactly one `snapshot` + one `usage_record`.
- **A (cross-project no-HOL), E (idempotent conflict), and the deterministic race scenarios for D**
  are covered by `tests/test_accounting.py` (deterministic) — cross-project no-HOL + FIFO, concurrent
  policy-edit, replay conflict, and pre-dispatch cancel/expiry/dispatch-failure all assert the exact
  invariants.

## Key files
- `src/aethergate/errors.py` — new `AccountingInvariantError`, `PricePolicyConflictError`.
- `src/aethergate/domain/entities.py` — `PricePolicy` billing-unit shape validation.
- `src/aethergate/contracts/admin_v1.py` — `PricePolicyCreate`/`PricePolicyUpdate` shape validation.
- `src/aethergate/persistence/repository.py` — `create_price_policy` one-enabled conflict check.
- `src/aethergate/persistence/models.py` — partial unique index, shape CHECKs, nullable
  `budget_reservations.price_snapshot_id`.
- `src/aethergate/accounting/repository.py` — idempotency conflict detection, `discard_price_snapshot`,
  released-reservation detach.
- `src/aethergate/accounting/service.py` — `_require_price` (missing price raises, zero stays zero).
- `src/aethergate/scheduler/repository.py` — 4-tuple scheduling scope, `clear_price_snapshot_reference`.
- `src/aethergate/scheduler/service.py` — budget-before-quota order, project scope, pre-dispatch
  snapshot discard.
- `src/aethergate/migrations/versions/0008_accounting_invariants.py` — migration.
- `tests/test_accounting.py`, `tests/test_migrations.py` — AGV2-011 coverage.
- `docs/architecture/{accounting,scheduler}.md`, `docs/contracts/{domain-model,admin-v1-foundation}.md`,
  `docs/development/README.md`.

## Decisions
- One-enabled-price-policy enforced at the DB (partial unique index) with a domain-level
  `PricePolicyConflictError` translation; disabled historical/edit rows may coexist.
- Missing price != zero; explicit zero is valid. Enforcement spans domain/DTO/service/DB CHECKs.
- Pre-dispatch snapshot discard (not soft-delete of possibly-referenced snapshots) preserves immutable
  historical snapshots without orphaning them for work that never dispatched.

## Deferred
- Final commercial model (showback/chargeback/prepaid/reseller). Principal-level budgets. FX.
- Invoice generation, payment processing, prepaid balance deduction, external billing exports.
- Admin HTTP CRUD routes for the accounting DTO foundations.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`. The dynamic API host port changes every `up`/`workers`; re-run `scripts/dev/v2 url`.
- `scripts/dev/v2 workers N` recreates the `api` container and thus reassigns the host port.
- The dev DB still holds the AGV2-010 aliases (`budget-req`/`budget-tok`, `qa-*`) and a `USD-budget`
  policy on the dev project; the budget only affects price-policy-bearing routes.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content and ollama serializes concurrent
  generation; gateway dispatch timings do not reflect upstream parallelism.

## Recommended Next Step
Expose the admin v1 accounting CRUD surface (route pricing, project budget policy, budget
status/headroom, usage-record and ledger-entry reads) over `/admin/v1`, reusing the DTO foundations
and service/repository contracts added in AGV2-010/011.
