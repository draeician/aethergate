# AetherGate v2 — Accounting, Pricing, and Project Budgets

Living document. Distinguish **settled** / **direction** / **deferred**. Canonical reference for the
accounting foundation added in AGV2-010.

## Purpose

Record immutable pricing snapshots, authoritative usage, optional project budget policy, monetary
budget reservations, an append-only ledger, and idempotent settlement — as distinct concepts from
authorization, throughput quota, and physical capacity. The commercial model remains deliberately
deferred. (Settled)

## Separation of concerns

These remain distinct and are never collapsed (Settled):

- authorization / entitlement;
- throughput quota / physical capacity;
- budget policy;
- usage accounting;
- pricing;
- settlement / billing.

Specifically (Settled):

- no positive-balance authorization gate;
- no implicit prepaid-wallet model;
- no account balance field used as universal access control;
- no assumption that a ledger debit means an invoice or external payment;
- future prepaid, showback, chargeback, and reseller modes are not foreclosed.

A project budget is an **optional spending-cap policy**. It is not a prepaid balance and a positive
monetary balance is not required for authorization. A project with no active budget policy is never
monetarily blocked. (Settled)

## Monetary representation

Money is fixed-point `Decimal` end to end. Binary floating point (`float`) is rejected for prices,
budgets, reservations, and ledger amounts. (Settled)

- `MONEY_PRECISION = 12`, `MONEY_QUANTUM = Decimal("1e-12")`; `quantize_money()` rounds half-up.
- PostgreSQL money columns are `Numeric(24, 12)`.
- Currency is normalized uppercase ISO-style three-letter code (`^[A-Z]{3}$`); no FX conversion in
  this task; budget and price must share a currency to interact.
- API/domain contracts reject floats (`Money`, `NonNegativeMoney`, `PositiveMoney`, `Currency`
  annotated types in `domain/value_objects.py`).

## Pricing configuration vs immutable snapshot

Mutable pricing configuration (`PricePolicy`, associated with a `RouteBinding`) is **not** the
historical record. The effective price is captured into an immutable `PriceSnapshot` at the final
admission/dispatch decision. Editing a `PricePolicy` never alters an existing snapshot. (Settled)

### Price configuration

A `PricePolicy` supports (Settled):

- **Request pricing** — `billing_unit=request`; fixed `request_price` (required); exact pre-dispatch
  budget reservation is possible without token estimation. Token `input_price`/`output_price` must
  be null/absent.
- **Token pricing** — `billing_unit=token`; `input_price` / `output_price` (both required); positive
  integer `unit_scale` (e.g. price per 1,000,000 units); `request_price` must be null/absent;
  pre-dispatch reservation requires a trustworthy input-token estimate plus a bounded output
  reservation.

Both carry `currency` and `enabled`. No float prices; validation is billing-unit specific. Missing
price is not equivalent to zero: an explicit `Decimal("0")` is a valid zero price, while a missing
price for a required field is rejected at the domain, DTO, service, and database CHECK layers. No
image/audio pricing behavior beyond contract extensibility in this task. (Settled)

At most one enabled `PricePolicy` exists per `RouteBinding`. Disabled historical/edit records may
coexist; the database enforces the invariant with a partial unique index
(`uq_price_policies_one_enabled_per_route` on `route_binding_id WHERE enabled`), and the
service/repository layer surfaces a conflict as a clear domain validation error
(`PricePolicyConflictError`) rather than a scheduler `MultipleResultsFound`. (Settled)

### Price snapshot

A `PriceSnapshot` captures, at minimum (Settled):

- source pricing-config ID;
- route binding ID;
- public model alias ID;
- provider account ID;
- billing unit;
- currency;
- unit scale;
- request price and/or input/output prices as applicable;
- captured timestamp.

Once created, application code must not update/delete a snapshot during normal operation. A request
that never reaches durable dispatch intent must not retain a historical active price snapshot: the
snapshot is captured at reservation (so budget reservations are priced deterministically) but is
discarded when the request never dispatches (pre-dispatch cancel, reclaim, or dispatch failure). A
request that never reaches dispatch never creates historical priced usage. (Settled)

## Project budget policy

A `ProjectBudgetPolicy` contains: stable opaque ID, `project_id`, `name`, `currency`, positive
`limit_amount`, positive `window_seconds`, `enabled`. Windows are **fixed and UTC-epoch-anchored**
(`fixed_window_start`, identical to quota-window semantics). (Settled)

A project may have more than one enabled budget policy; every applicable same-currency policy must
admit the request. Principal-level budgets are deferred but the persistence model does not foreclose
adding principal scope. (Settled / deferred)

## Budget windows and reservations

- `BudgetWindow` is the authority row: `(budget_policy_id, window_start)` unique, with
  `committed_amount` and `reserved_amount`.
- `BudgetReservation` is per-request: `request_id`, `budget_policy_id`, `price_snapshot_id`,
  `window_start`, `reserved_amount`, `committed_amount`, `state`, `settlement_reason`.

Admission enforces, transactionally under PostgreSQL locks (Settled):

```
committed + reserved + requested <= budget limit
```

No process-local budget counter exists. If any required constraint cannot be acquired, none is
(all-or-nothing, sharing the scheduler's admission transaction).

### Admission behavior

- A budget-exhausted request stays queued until the next eligible budget window, subject to normal
  queue/total deadlines.
- A request whose minimum required monetary reservation cannot fit an empty applicable window fails
  explicitly (`budget_request_too_large`).
- Non-content wait metadata is persisted: blocking budget policy, `wait_limit_metric="budget"`,
  `next_eligible_at` (next window reset). Budget blocking is distinct from throughput quota and
  endpoint capacity.
- A blocked budget scope does not head-of-line block unrelated project/scope work sharing capacity.

## Monetary reservation calculation

### Request-priced route

Reserve exactly the configured per-request price. This is the preferred live budget test path because
it needs no token estimation. (Settled)

### Token-priced route

Reserve `estimated input cost + bounded maximum output cost` (Settled):

- uses the same trustworthy provider/model-specific estimator contract already enforced by the
  scheduler (`estimate_input_tokens -> int | None`);
- uses the client's explicit `max_tokens` or the route's configured `default_output_tokens`;
- with no trustworthy estimator, a budget-enforced token-priced request fails closed
  (`budget_token_estimator_unavailable`);
- with no output bound, it fails with `budget_unbounded_output`;
- no character/word heuristic is reintroduced.

Token pricing may still be recorded post-completion from trustworthy actual usage even with no budget
policy, because no pre-dispatch monetary reservation is required in that case. (Settled)

## Usage accounting

`UsageRecord` rows are immutable records of trustworthy measured usage, at minimum (Settled):

request ID, execution-attempt ID, project ID, principal ID (when available), API credential ID (when
available), public model alias ID, route binding ID, provider account ID, price snapshot ID, billing
unit, measured input units, measured output units, request units (when applicable), calculated
monetary amount, currency, recorded timestamp, upstream request ID (when safe/available).

- one logical settled usage record per request; uniqueness/idempotency enforced in PostgreSQL;
- no prompt/completion content; no secret material;
- append-only/immutable in normal operation;
- provider-reported actual usage wins over estimates when trustworthy;
- never fabricate measured token usage from a reservation;
- a dispatched request that fails without trustworthy usage creates no fake measured usage record.

## Budget settlement

- **Success with trustworthy priceable usage** — compute the actual amount from the immutable
  snapshot; create `UsageRecord` idempotently; settle `BudgetReservation` to actual; release unused
  reservation; if actual exceeds reservation, record it honestly (window may become over limit;
  nothing is truncated or hidden).
- **Known post-dispatch failure/cancellation with no trustworthy usage** — conservatively commit the
  reserved amount (budget-cap safety); create no measured usage record; record the settlement reason
  in reservation metadata. This is budget-policy accounting, not external billing.
- **`outcome_unknown`** — keep the monetary reservation held; never release automatically; explicit
  reconciliation to failed/cancelled conservatively commits it (unless trustworthy usage is supplied
  through a future reconciliation flow); do not invent a usage record.
- **Pre-dispatch cancellation/reclaim** — release the reservation completely; no usage record; no
  usage ledger entry.

## Append-only ledger

The ledger is an accounting/event primitive, not a mandatory prepaid balance. Entry types (Settled):

- `usage_debit` — priced measured-usage debit (references a `UsageRecord`);
- `adjustment_credit` / `adjustment_debit` — explicit adjustments (no usage record).

Entries carry project ID, optional usage-record ID, currency, signed typed Decimal amount, entry
type, immutable timestamp, stable idempotency key / uniqueness rule, and optional safe reason
metadata (no content/secrets).

A measured `UsageRecord`'s `usage_debit` ledger entry is created idempotently in the same settlement
transaction. A conservative unknown-usage budget commitment never creates a measured-usage ledger
debit. (Settled)

No invoice generation, payment processing, prepaid balance deduction, or external billing exports in
this task. (Deferred)

## Idempotent settlement

Settlement can be retried after worker/API/process interruption without double charging (Settled):

- DB uniqueness/idempotency for `UsageRecord`;
- DB uniqueness/idempotency for the usage `LedgerEntry`;
- `BudgetReservation` settles at most once;
- repeated settlement with the same canonical data is a no-op/same result;
- conflicting second settlement raises `AccountingInvariantError` (internal-only), never a silent
  overwrite;
- the canonical equality set excludes volatile timestamps: for `UsageRecord` it is
  attempt/project/principal/credential/alias/route/account/snapshot/billing-unit/input/output/
  request-units/amount/currency; for `LedgerEntry` it is project/usage-record/type/amount/currency;
- no double budget commit; no duplicate ledger debit.

Crash/retry fault-injection tests cover this.

## Scheduler integration / lock order

Monetary budget evaluation participates in the same all-or-nothing admission plan as request/token
quota and endpoint physical capacity. Deterministic lock order (Settled):

1. request / scheduling-scope row(s) (scope = endpoint + quota group + project);
2. active price policy;
3. project budget policy / budget windows (stable ID order);
4. provider quota group / limit / windows (stable ID order);
5. endpoint physical capacity;
6. request / attempt / reservation mutations.

No open database transaction spans provider inference. Budget cannot oversubscribe, quota cannot
oversubscribe, endpoint cannot oversubscribe, and no partial reservation survives a failed combined
admission. A budget-blocked project does not head-of-line block another project sharing the same
endpoint/quota resources (the scheduling scope includes project identity).

## Admin surface (AGV2-017)

The accounting control plane is exposed under `/admin/v1` through a thin router + a centralized
accounting admin service (`accounting/admin.py`) that accepts a typed `AdminRequestContext` and
authorizes internally — an internal caller cannot bypass RBAC merely by supplying an actor principal
ID. (Settled)

- **Route pricing** — `PricePolicy` create/read/list/update (deployment-scoped `system_admin` +
  `admin:accounting:read`/`write`), immutable `PriceSnapshot` list/read (deployment-scoped, read-only).
- **Project budgets** — `ProjectBudgetPolicy` create/read/list/update; `GET
  /projects/{id}/budget-status` (one `BudgetStatusRead` per policy/window, current-window zero state
  computed without fabricating history); `BudgetReservation` list/read (read-only; a released
  pre-dispatch reservation exposes `price_snapshot_id = null`).
- **Usage/ledger** — immutable `UsageRecord` and append-only `LedgerEntry` list/read (read-only, no
  update/delete).
- **Audit** — `GET /audit-events` / `GET /audit-events/{id}` under `admin:audit:read`; `system_admin`
  reads all, project roles read only events explicitly scoped to their project; a `project_id = null`
  event is deployment-scoped and hidden from project roles.

### Authorization (project vs deployment scope)

- Deployment-scoped (route `PricePolicy`/`PriceSnapshot`): `system_admin` only.
- Project-scoped (`ProjectBudgetPolicy`, budget status, reservations, usage, ledger): `system_admin`
  any project; `project_admin` read/write own; `project_viewer` read own; cross-project opaque IDs are
  indistinguishable from nonexistent (`404`), and deployment-scoped IDs follow the same
  non-enumeration pattern.

### Budget mutation safety

`name`, `limit_amount`, and `enabled` are mutable for future admission; `currency` and `window_seconds`
are immutable after creation (changing them requires a replacement policy — a PATCH attempting to
mutate them returns a stable validation error). Edits never rewrite historical `BudgetWindow`/
`BudgetReservation` rows, and budget config writes serialize with scheduler admission using the
existing canonical lock order.

### Audit

Config changes emit immutable audit events — `price_policy.created`/`updated` (deployment-scoped) and
`project_budget_policy.created`/`updated` (project-scoped) — with actor principal ID and safe non-secret
metadata. No-op PATCHes emit no event. Usage/ledger/snapshot reads do not create audit noise.

### Deferred in this task

- Manual ledger adjustment HTTP writes (adjustment policy/approval not settled).
- Invoice/payment/prepaid/exports/FX (unchanged from the accounting foundation).

## Deferred

- The commercial model (what is billed, to whom, at what margin).
- Principal-level budgets.
- Invoice generation, payment processing, prepaid balance deduction, external billing exports.
- FX conversion.
- Image/audio pricing behavior beyond contract extensibility.
