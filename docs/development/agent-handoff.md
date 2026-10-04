# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: 31871c2 (AGV2-010 task specification)
- Implementation commit(s): `feat(accounting): add pricing usage and project budgets` (AGV2-010);
  `docs(development): complete AGV2-010 accounting handoff` (this task)

## Task Completed
AGV2-010 — accounting foundation: immutable pricing, usage records, optional project budgets,
monetary reservations, append-only ledger, idempotent settlement. Pricing/budget admission now
participates in the same all-or-nothing scheduler transaction as request/token quota and endpoint
physical capacity. The commercial model remains deferred; a project budget is an optional
spending-cap policy, not a prepaid balance, and no positive-balance authorization gate was
introduced. No changes to legacy v1 `app/` or `frontend/src/`.

## Migration
- Revision `0007` (`accounting foundation`). Live `0006 -> 0007` succeeded; empty DB -> latest
  succeeded. Creates `price_policies`, `price_snapshots`, `project_budget_policies`,
  `budget_windows`, `budget_reservations`, `usage_records`, `ledger_entries`; adds
  `inference_requests.price_snapshot_id` (+ index) and `inference_requests.wait_limit_metric`.
  CHECKs: currency format (`^[A-Z]{3}$`), positive budget limit/window, nonnegative
  reserved/committed, reservation/ledger state+type enums, positive unit scale, idempotency
  uniqueness (usage per request; ledger per idempotency key).

## Pricing model / immutable snapshot
- `PricePolicy` is mutable configuration attached to a `RouteBinding` (request or token billing
  unit; `currency`; positive `unit_scale`; non-negative prices; `enabled`).
- At the final admission/dispatch decision the effective price is captured into an immutable
  `PriceSnapshot` (referenced by `inference_requests.price_snapshot_id`). Editing a policy never
  alters an existing snapshot; a request that never dispatches creates no priced usage.
- Money is `Decimal` end-to-end (`Numeric(24,12)` in PG; `MONEY_PRECISION=12`,
  `MONEY_QUANTUM=1e-12`, `quantize_money` half-up). No float. No FX; price and budget must share a
  currency to interact.

## Budget reservation / settlement
- `ProjectBudgetPolicy` (optional cap) with fixed UTC-epoch-anchored windows (same
  `fixed_window_start` as quota). Admission enforces `committed + reserved + requested <= limit`
  transactionally under row locks; no process-local counter; all-or-nothing with quota + endpoint.
- Request-priced reservation = exact `request_price`. Token-priced reservation = estimated input +
  bounded output cost; fails closed when the estimator is unavailable
  (`budget_token_estimator_unavailable`) or output is unbounded (`budget_unbounded_output`).
- Budget-exhausted requests queue with `wait_reason=budget_window_exhausted`,
  `wait_limit_metric="budget"`, `next_eligible_at`; they hold no endpoint slot and no monetary
  reservation. A request whose minimum reservation cannot fit an empty window fails
  (`budget_request_too_large`).
- Success settles to actual (release unused; overage recorded honestly, never truncated). Unknown
  post-dispatch usage conservatively commits the reservation with no measured usage record.
  `outcome_unknown` keeps the reservation held; explicit reconciliation commits it. Pre-dispatch
  cancel/reclaim releases fully.

## Ledger
- Append-only `LedgerEntry`: `usage_debit` (references a `UsageRecord`), `adjustment_credit`,
  `adjustment_debit`. Signed typed Decimal amount, currency, immutable timestamp, idempotency key,
  optional safe reason (no content/secrets). A measured `UsageRecord`'s debit is created idempotently
  in the same settlement transaction; conservative unknown-usage commitments never create a measured
  debit. Not a prepaid wallet; no invoices/payments in this task.

## Lock order (deterministic, tested)
request/scope row(s) -> project budget policy/windows (stable ID order) -> provider quota
group/limit/windows (stable ID order) -> endpoint physical capacity -> request/attempt/reservation
mutations. No open transaction spans provider inference.

## Idempotency
`UsageRecord` unique per request; usage `LedgerEntry` unique per idempotency key;
`BudgetReservation` settles at most once. Repeated settlement = no-op; conflicting second
settlement = explicit invariant error. No double budget commit or duplicate debit.

## Automated tests
`scripts/dev/v2 test` -> **202 passed** (was 181 baseline; +21 accounting/migration tests).
`ruff check src tests` clean. `tests/test_accounting.py` covers the 27 deterministic scenarios
(decimal/currency validation, snapshot immutability, budget exhaustion without holding capacity,
two-worker oversubscription, all-or-nothing admission, too-large failure, fail-closed token budget,
trusted-estimator reservation, success/under/over settlement, unknown-usage conservative commit,
pre-dispatch release, outcome_unknown hold, reconcile commit, repeated/conflicting settlement,
price-change immutability, no head-of-line blocking). `tests/test_migrations.py` covers
`0006 -> 0007` and empty-DB->latest. `git diff --check` clean; secret scan clean.

## Real nomnom verification (live, real Ollama)
Host = nomnom (`192.168.22.50/24`); docker = podman 4.9.3 + docker-compose 2.40.3. Backend = ollama
`qwen3.8-2b-distill:Q6_K`; litellm 1.104.0 `huggingface_tokenizer_kind(...)` returns `None` (no
trusted estimator). Dynamic AetherGate port this session: **38411** (changes on every
`up`/`workers`; re-run `scripts/dev/v2 url`).

- **A. Request-priced budget — PASS.** Alias `budget-req`, request price 0.05 USD, budget 0.10/60s,
  endpoint `max_concurrency=8`, 2 workers. 4 concurrent SDK requests: exactly 2 dispatched (window
  committed = 0.10), 2 stayed `queued` with `wait_reason=budget_window_exhausted` and
  `price_snapshot_id IS NULL` (no monetary reservation, no endpoint slot held). After window
  rollover the 2 queued requests dispatched (next window committed = 0.10). No window exceeded 0.10;
  every success created exactly one snapshot + usage record + `usage_debit` ledger entry.
- **B. Price immutability — PASS.** Dispatched under request price 0.05; changed the policy to 0.99;
  prior snapshots/usage/ledger unchanged (0.05); the next request captured a fresh snapshot at 0.99
  and a 0.99 ledger debit.
- **F. outcome_unknown — PASS.** Caught a dispatched request mid-flight, killed both workers,
  expired its lease, restarted workers -> recovery marked it `outcome_unknown`; budget reservation
  remained `reserved` (held). `reconcile resolve ... --disposition failed` conservatively committed
  it (`committed=0.05`, `settlement_reason=failed`); no usage record and no measured ledger debit.
- **G. Token-estimator fail-closed — PASS.** Token-priced route (`budget-tok`, 1/1/1000) + active
  budget -> failed closed with `error_code=budget_token_estimator_unavailable` (HTTP 502, no
  snapshot/usage/reservation). Request-priced route continued to dispatch.
- **H. Regressions — PASS.** Official OpenAI SDK 2.54.0 non-stream + stream succeeded; six-request/
  two-slot (`max_concurrency=2`) observed max concurrent `dispatched` exactly 2, all succeeded;
  shared/cross-quota and recovery/fencing covered by the 202-test suite.
- **C/D/E** (actual < / > reservation, unknown-usage failure) are deterministic/mock and covered by
  `tests/test_accounting.py` (scenarios 18/19/20).

## Key files
- `src/aethergate/domain/{enums,ids,value_objects,entities}.py` — accounting contracts
  (`BillingUnit`, `LedgerEntryType`, `BudgetReservationState`, `Money`/`Currency`/`quantize_money`,
  `PricePolicy`, `PriceSnapshot`, `ProjectBudgetPolicy`, `BudgetWindow`, `BudgetReservation`,
  `UsageRecord`, `LedgerEntry`).
- `src/aethergate/persistence/{models,repository}.py` — ORM models + repository methods.
- `src/aethergate/migrations/versions/0007_accounting_foundation.py` — migration.
- `src/aethergate/accounting/{repository,service}.py` — money math + reservation/settlement.
- `src/aethergate/scheduler/{service,repository}.py` — budget admission (`_evaluate_budget`),
  snapshot capture, settlement, recovery/cancel/reconcile accounting.
- `src/aethergate/contracts/admin_v1.py` — accounting DTO foundations.
- `src/aethergate/{devseed,inspect_queue}.py` — seed/inspection tooling.
- `docs/architecture/accounting.md` (new), `docs/architecture/scheduler.md`,
  `docs/contracts/{domain-model,admin-v1-foundation}.md`, `docs/development/README.md`.

## Decisions
- A project budget is an optional spending cap, never a prepaid balance; no positive-balance gate.
- Pricing config is mutable; the snapshot is the immutable historical record.
- Unknown-usage conservative budget commit is budget-policy accounting, not measured usage/billing.
- Token budgets fail closed without a trustworthy estimator; no char/word heuristic.

## Deferred
- Final commercial model (showback/chargeback/prepaid/reseller). Principal-level budgets. FX.
- Invoice generation, payment processing, prepaid balance deduction, external billing exports.
- Admin HTTP CRUD routes for the accounting DTO foundations.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`. The dynamic API host port changes every `up`/`workers`; re-run `scripts/dev/v2 url`.
- `scripts/dev/v2 workers N` recreates the `api` container and thus reassigns the host port.
- The dev DB now holds extra aliases `budget-req`/`budget-tok` and a `USD-budget` policy on the dev
  project; the budget only affects price-policy-bearing routes, so the older `qa-*` aliases are
  unaffected.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content and ollama serializes concurrent
  generation; gateway dispatch timings do not reflect upstream parallelism.

## Recommended Next Step
Expose the admin v1 accounting CRUD surface (route pricing, project budget policy, budget
status/headroom, usage-record and ledger-entry reads) over `/admin/v1`, reusing the DTO foundations
and service/repository contracts added here.
