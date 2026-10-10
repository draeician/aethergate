# AetherGate Agent Handoff

## Current State
- Branch: `v2`. AGV2-022D (remove final browser Decimal coercion from budget headroom) is complete and
  pushed to `origin/v2`; `.aethergate-wip` was removed only after the final remote verification. The
  accounting phase is now fully closed: the last remaining `Number()`/`parseFloat()`/`.toFixed()` money
  coercion in the browser (dashboard budget headroom) is gone, and every required regression gate is
  green against the baseline below.
- AGV2-022/022V/022C/022D accounting phase closed. AGV2-022 (accounting management UI), AGV2-022V (live
  queue-unblock + released-reservation proofs), AGV2-022C (close the regression gate + budget-policy
  disable wake-up), and AGV2-022D (remove the final browser Decimal coercion) are all pushed. The phase
  is closed only against the green full suite plus the runtime frontend Decimal-coercion scan below.
- Migration head unchanged at `0016`; no new migration; `0001`–`0016` untouched. Legacy Python v1 app
  and the dated architecture audit are untouched.
- Budget wake semantics now cover both directions in
  `src/aethergate/accounting/admin.py::update_project_budget_policy`: (1) raising a budget policy limit
  clears the persisted `next_eligible_at` of `budget_window_exhausted` queued requests (AGV2-022V);
  (2) **disabling** an enabled policy also clears it (AGV2-022C) so queued requests are re-evaluated
  instead of waiting for the old window reset. Name-only edits, limit decreases, unchanged policies,
  and disabled->enabled do not wake requests.
- The web nginx proxy defect fixed in AGV2-022V (`proxy_read_timeout`/`proxy_send_timeout 600s`) remains
  in place; the full suite exercises the long-held inference path through it.

## AGV2-022D — Remove final browser Decimal coercion from budget headroom

### Why this task exists
AGV2-022C closed the required regression gate, but a final code review found one remaining violation of
the phase-wide Decimal rule: `frontend/src/pages/DashboardPage.tsx` did
`const headroom = Number(budget.headroom)` and compared `headroom <= 0`. `BudgetStatusRead.headroom` is
a signed `Money` string and may be negative after overage; the browser may only make an exact
sign/zero classification, never an IEEE-754 `Number` conversion. This task closes that single dashboard
leak; it starts no new product scope (no observability, Responses, embeddings, accounting expansion, v1
migration, or redesign).

### Exact signed-Decimal comparison helper
`frontend/src/lib/decimal.ts` gains `compareDecimalToZero(value: string): -1 | 0 | 1`, a pure regex
classifier with no `Number()`, `parseFloat()`, bigint, or third-party float path. It accepts an optional
leading `-`, and returns `-1`/`0`/`1` for exact negative/zero/positive. Zero forms `0`,
`0.000000000000`, `-0`, and `-0.000000000000` are all `0`; `0.000000000001` is `1`;
`-0.000000000001` is `-1`; `123456789.123456789012` is `1` and `-123456789.123456789012` is `-1`.
Malformed input (`""`, `1e-3`, `NaN`, `Infinity`, `12,34`, `1.2.3`, `-`, `.5`, `5.`) is classified as
`0` ("not positive") so callers fail closed rather than treating garbage as an available balance.
`isValidDecimalString` / `isPositiveDecimalString` retain their existing non-negative contract.

### Dashboard behavior
`BudgetRow` now computes `blocked = budget.enabled && compareDecimalToZero(budget.headroom) <= 0`.
Exact positive => "ok"; zero or exact negative => "exhausted"; a disabled budget is never marked
exhausted solely because headroom is non-positive. The rendered headroom string is the server-returned
value verbatim (`{budget.headroom} {budget.currency} ...`); no client-side recomputation from
limit/committed/reserved.

### Runtime frontend Decimal-coercion scan
Grep of `frontend/src/` for `Number(`, `parseFloat(`, and `.toFixed(` now matches only the
`decimal.ts` module docstring (a prose mention). The only remaining numeric conversions are `parseInt`
on integer fields: `unit_scale` (PricingPage), `window_seconds` (BudgetsPage, QuotasPage),
`limit_units` (QuotasPage), `max_concurrency` (EndpointsPage), and `default_output_tokens`
(RoutesPage). No monetary Decimal field passes through `Number()`/`parseFloat()`/`.toFixed()` anywhere
in the reviewed runtime accounting surfaces (DashboardPage, `features/accounting/*`,
`features/catalog/QuotasPage`, `lib/decimal.ts`).

### Regression evidence (AGV2-022D)
- Backend containerized suite (`scripts/dev/v2 test`): **506 passed** (`506 tests collected`; clean
  green run, no failures). Baseline unchanged (frontend-only task).
- Frontend unit/component (`npm run test`): **82 passed** (baseline 74 + 8 new: 2 `compareDecimalToZero`
  decimal canaries + 6 DashboardPage budget-headroom cases), 22 files.
- Full browser suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`):
  **26 passed, 0 failed**. The first attempt had one transient `oidc_unavailable` (503) on the first
  accounting login; it was root-caused to a transient `OidcConfigurationError("OIDC discovery failed")`
  (httpx fetch of `/.well-known/openid-configuration` momentarily failed; the API caches discovery for
  300s and does not cache failures, so it self-heals). The IdP was reachable immediately after
  (`/jwks` 200, `.well-known` 200), and the full suite passed clean on the immediately following run.
- `npm run build` clean; `npm run lint` clean; `npx tsc -b --noEmit` clean.
- OpenAPI/client drift clean: `python scripts/gen_openapi.py` + `npm run generate:client` produced no
  diff on `frontend/src/generated/openapi.json` / `schema.d.ts` (no contract/route change).
- `ruff check src tests` clean; `git diff --check` clean.
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files; no key patterns in
  the changed files).
- Official OpenAI Python SDK regression (`openai 2.54.0`), inference-auth bypass `false` (verified via
  the running api `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` and an unauthenticated `/v1/models`
  returning 401): non-stream + stream against the UI-created disposable alias succeed
  (`catalog-inference.spec.ts` test 9), plus the credential lifecycle and accounting live SDK proofs.
- No migration; `0016` remains head; `0001`–`0016` untouched.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022D`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (gitignored).

## AGV2-022C — Close accounting regression gate and budget-policy disable wake-up

### Why this task exists
AGV2-022V closed its two accounting live-proof gaps and fixed two real defects, but the required full
Playwright regression was red (24 passed / 2 failed), and a final review found one adjacent scheduler
correctness hole. AGV2-022C closes exactly those three items; it starts no new product scope
(no observability, Responses, embeddings, accounting expansion, v1 migration, or redesign).

### Budget-policy disable wake semantics (real scheduler hole)
`src/aethergate/accounting/admin.py::update_project_budget_policy` now wakes budget-blocked queued
requests whenever the mutation makes the policy less restrictive:
- `new_limit > existing.limit_amount` (raise — AGV2-022V), **or**
- `existing.enabled is True and new_enabled is False` (disable — new).

Both cases call the existing scoped primitive
`scheduler/repository.py::clear_budget_wait_metadata_for_policy`, which clears wait metadata only for
`state = queued` + `wait_reason = budget_window_exhausted` + `wait_limit_id = this policy`. The worker
re-evaluation remains authoritative: if another policy still blocks, it re-applies correct metadata.
Name-only edits, limit decreases, unchanged policies, and disabled->enabled do **not** clear metadata.

### Deterministic disable-unblock regression
`tests/test_accounting.py::test_budget_window_exhausted_request_unblocks_after_policy_disable` drives
the real admin mutation path (not the repository helper directly): request price P; a budget with
`limit == P` admits the first request and queues the second as `budget_window_exhausted` (not a
terminal rejection); the first commits exactly P; the same policy is **disabled** via
`accounting_admin.update_project_budget_policy` under a deployment `system_admin` context; the cleared
wait metadata is asserted; the **same** `request_id` becomes eligible, dispatches, and settles exactly
once — exactly one new `UsageRecord` and exactly one signed negative `usage_debit`, no duplicate
settlement.

### Full Playwright suite fixes (the gating criterion AGV2-022V missed)
- **Pricing-table pagination** (`accounting.spec.ts` test 6): the pricing table accumulates policies
  across runs and paginates oldest-first at 20, so the run's new policy could fall off page 1. The test
  now scopes the pricing list to this run's route through the existing UI "Route binding" filter before
  asserting the created row, so it is deterministic as disposable policies grow.
- **management-rbac project/provider pagination** (`management-rbac.spec.ts`): after creating a
  uniquely named project/provider through the UI, the test used to assert on page 1 of the
  oldest-first, 20-per-page lists. It now uses the real UI pagination helper `goToLastPage` and asserts
  the new project and provider through the browser UI (no direct-API substitution).
- **credential lifecycle timeout** (`management-rbac.spec.ts` "inference credential lifecycle"): the
  test issues multiple live official-SDK calls against slow Ollama generation inside Playwright's
  default 60s timeout. It now sets a scoped `test.setTimeout(240_000)`; all official OpenAI Python SDK
  calls, `bypass=false`, and the old/new/revoked key assertions are unchanged (no raw `fetch`
  substitution; no global timeout inflation).

### Full browser regression (the gate)
- Full suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **26 passed,
  0 failed**, run twice consecutively with identical results. Per spec: `accounting` 8,
  `catalog-inference` 1, `management-rbac` 6, `operator-rbac` 7, `web-console` 4.
- The two AGV2-022V accounting live proofs remain green **inside the full suite**:
  same-request `budget_window_exhausted` unblock (test 7) and the released pre-dispatch reservation
  browser proof (test 8).

### Verification results (AGV2-022C)
- Backend containerized suite (`scripts/dev/v2 test`): **506 passed** (baseline 505 + 1 deterministic
  disable-unblock test); `506 tests collected`.
- Frontend: `npm run build` clean, `npm run lint` clean, `npm run test` **74 passed** (22 files).
- Browser E2E full suite: **26 passed, 0 failed** (twice).
- Official OpenAI Python SDK (`openai 2.54.0`, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` verified
  in the running api and by an unauthenticated `/v1/models` returning 401): non-stream + stream against
  the UI-created disposable alias succeed (`catalog-inference.spec.ts`), and the credential lifecycle
  proves old/new/revoked key outcomes (`management-rbac.spec.ts`).
- `ruff check src tests` clean; `git diff --check` clean; `npx tsc -b --noEmit` clean; OpenAPI/client
  generation drift clean (`scripts/gen_openapi.py` + `npm run generate:client` produced no diff on
  `openapi.json` / `schema.d.ts`; no contract/route change).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files; no key patterns in
  the changed files).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:37439/v1`; web console loopback-only on
  `http://127.0.0.1:8081` (because `matrix-comms-element` occupied `0.0.0.0:8080`; the committed
  `compose.web.yaml` default stays `127.0.0.1:8080`, and only the gitignored `deploy/v2/.env`
  redirect URI plus a throwaway compose override pointed at `8081` for this run); IdP issuer
  `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022C`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (it is
gitignored).

## AGV2-022V — Close accounting live queue-unblock and released-reservation proofs


### Why this task exists
AGV2-022's final review found two narrow **live acceptance** gaps:
1. The budget live proof used `budget_request_too_large`, a terminal *failed* request, then submitted a
   *new* request after raising the limit. That does not prove the **same queued**
   `budget_window_exhausted` request becomes eligible after a policy edit.
2. There was no live/browser proof of a **real pre-dispatch released BudgetReservation** rendered with
   `price_snapshot_id = null`, zero reserved/committed amounts, no UsageRecord, and no `usage_debit`.

These two are now closed (see below), plus the real scheduler and nginx defects the proofs exposed.

### Semantics distinction (recorded truthfully)
- `budget_request_too_large` = fail-fast for a request that cannot fit even an empty budget window
  (terminal rejection).
- `budget_window_exhausted` = the queued temporary-capacity condition; the same request resumes after a
  policy edit admits it. Only this scenario is called "block/unblock".

### Real scheduler defect fix (found by the live unblock proof)
`src/aethergate/scheduler/repository.py` gains `clear_budget_wait_metadata_for_policy(session,
budget_policy_id)`; `src/aethergate/accounting/admin.py::update_project_budget_policy` calls it when
`new_limit > existing.limit_amount`. Before this, raising the cap left the queued request's
`next_eligible_at` pointing at the window reset, so the worker never reclaimed it. Deterministic
coverage: `tests/test_accounting.py::test_budget_window_exhausted_request_unblocks_after_policy_raise`
(first request commits P; second queues `budget_window_exhausted`; limit raise + metadata clear; the
**same** request_id dispatches and settles exactly once).

### Real nginx proxy defect fix (found by the live SDK proof)
`frontend/nginx.conf` sets `proxy_read_timeout 600s` / `proxy_send_timeout 600s` on the `/v1/` location.
The gateway holds non-stream requests synchronously while a queued request waits for admission and the
upstream model generates (no bytes are flushed until the terminal result). The nginx default 60s cut
those held connections with a 504, causing the official SDK to retry (new request_id each time). The web
image was rebuilt and the `aethergate-v2-web` container recreated to apply it.

### Live proofs (browser UI + official OpenAI Python SDK, inference-auth bypass = false)
- **Budget unblock — same request** (`accounting.spec.ts` test 7): read the route's enabled request
  price P; create a budget with limit == P through the UI; first official SDK request commits P; spawn a
  second official SDK call and keep it pending; poll the queue API for the `budget_window_exhausted`
  request and capture its stable `request_id` (targeted by `waitForBudgetQueuedRequest` via
  `wait_limit_id`, and the row filter now uses the unique `budgetName` — filtering by the price string
  is ambiguous because committed_amount equals P on older leftover rows and the list is ordered
  oldest-first); prove queued/`budget_window_exhausted`/`wait_limit_metric=budget`/no error/no usage;
  raise the **same** policy limit through the UI; await the original pending SDK call and prove it
  succeeds (no replacement request); prove exactly one new UsageRecord and one signed `usage_debit`
  (`-P`), and the server-exact committed/headroom values.
- **Released reservation — real lifecycle + browser** (`accounting.spec.ts` test 8, new):
  `src/aethergate/live_accounting_release.py` is a controlled harness using the real
  scheduler/accounting path (enqueue → `claim_and_reserve` → reserve + snapshot → direct SQL revert to
  the reserved pre-dispatch window → `request_cancellation`), with a `NoopAdapter` that asserts
  `complete`/`stream` are never called (no upstream dispatch). Workers are stopped/restarted around it
  (`runReleaseHarness`/`listWorkerContainers` in `fixtures.ts`, using direct `docker stop`/`start` to
  avoid the podman `service_healthy` wait). The harness prints identifiers only (JSON, no secret/prompt/
  completion) and asserts the released invariants. The browser Reservations page then proves the same
  real reservation renders: state `released`, `reserved_amount = 0`, `committed_amount = 0`, and
  `price_snapshot_id = null` shown as "No snapshot / released before dispatch".

### Verification results (AGV2-022V)
- Backend containerized suite (`scripts/dev/v2 test`): **505 passed** (baseline 504 + 1 deterministic
  budget-unblock test). `505 tests collected`.
- Frontend: `npm run build` clean, `npm run lint` clean, **74 unit/component tests pass** (22 files).
- Browser E2E — `accounting.spec.ts`: **8 passed** (was 7; the new released-reservation proof makes 8).
- Browser E2E — full suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`):
  **24 passed, 2 failed** at the time. > **Correction (AGV2-022C):** AGV2-022V was therefore **not**
  fully complete — the required full-browser regression gate was red. Both failures were real,
  deterministic-under-load defects (not "pre-existing and unrelated" flakes as first recorded), and
  both are now fixed in AGV2-022C, which closes the full suite at **26 passed, 0 failed** (twice):
  - `management-rbac.spec.ts` "creates a project and a provider through the UI" failed because
    accumulated disposable projects exceed the oldest-first 20-per-page list; fixed in AGV2-022C with
    the real UI pagination helper `goToLastPage`.
  - `management-rbac.spec.ts` "inference credential lifecycle" intermittently exceeded the default 60s
    timeout across multiple slow live SDK calls; fixed in AGV2-022C with a scoped
    `test.setTimeout(240_000)`.
  Additionally, the accounting pricing-table assertion (test 6) had the same accumulation/pagination
  root cause and was fixed in AGV2-022C by scoping the pricing list to the run's route through the UI
  filter.
- Official OpenAI Python SDK (`openai 2.54.0`): the budget-unblock proof drives a **pending** non-stream
  call that stays held through the browser policy edit and then succeeds; the released-reservation proof
  asserts no upstream dispatch occurred.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean from
  this task (no contract/route change; `frontend/src/generated/openapi.json` and `schema.d.ts` are
  unchanged in this diff). Secret/token/content canary scan clean.
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:43591/v1`; web console loopback-only on
  `http://127.0.0.1:8081`; IdP issuer `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022V`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified.

## AGV2-022 — Accounting management UI

### Implementation commits (origin/v2)
- `feat(web): add accounting management UI` — accounting feature module
  (`frontend/src/features/accounting/`), Decimal-safe helpers (`frontend/src/lib/decimal.ts`), typed
  accounting client methods + `roles.ts` guards, role-aware routing/navigation, accounting
  unit/component tests, and the live browser E2E (`frontend/e2e/accounting.spec.ts`).
- `fix(accounting): serialize money canonically and sign usage ledger debits` — canonical fixed-point
  money JSON serialization and signed `usage_debit` ledger entries, with regression tests.

### Routed pages / features
- Accounting configuration: `/accounting/pricing`, `/accounting/snapshots`, `/accounting/budgets`.
- Accounting history/observability: `/accounting/reservations`, `/accounting/usage`,
  `/accounting/ledger`, `/audit`.
- Pages: `PricingPage`, `PriceSnapshotsPage`, `BudgetsPage`, `BudgetReservationsPage`, `UsagePage`,
  `LedgerPage`, `AuditPage` (all under `frontend/src/features/accounting/`).
- Typed client methods in `frontend/src/lib/client.ts`; role guards in `frontend/src/lib/roles.ts`.

### Decimal-safe browser handling
- Money is fixed-point Decimal and stays a string end-to-end. `frontend/src/lib/decimal.ts` provides
  `isValidDecimalString`, `isPositiveDecimalString`, and `formatMoney` without `Number()`/`parseFloat()`.
- Form state for money/prices is string; validation rejects lossy/float syntax; exact strings are sent
  to the backend; rendered read models are verbatim. Canary tests use `0.000000000123` and
  `123456789.123456789012`.

### Backend accounting corrections
- **Canonical money serialization** (`src/aethergate/domain/value_objects.py`): `Money`,
  `NonNegativeMoney`, and `PositiveMoney` now serialize to JSON as canonical fixed-point strings via
  `PlainSerializer(_serialize_money, return_type=str, when_used="json")` (`format(value.normalize(),
  "f")`). This removes Pydantic's auto `pattern` from money fields in
  `frontend/src/generated/openapi.json`; `schema.d.ts` is unchanged. Regression test:
  `tests/test_contracts.py::test_money_serializes_as_canonical_fixed_point`.
- **Signed usage ledger debits** (`src/aethergate/scheduler/service.py`): the `usage_debit`
  `LedgerEntry` is now written with a negative amount (`amount=-amount`) to match the documented
  "signed" ledger convention (`docs/architecture/accounting.md`). Regression assertion added to
  `tests/test_accounting.py::test_request_priced_budget_reserves_and_queues_exhausted`.

### Live proofs (browser UI + official OpenAI Python SDK, inference-auth bypass = false)
- **Pricing + immutable snapshots** (`accounting.spec.ts`): create a request-priced `PricePolicy`
  through the UI with the exact Decimal `0.000000000123`; official SDK request succeeds; the resulting
  `PriceSnapshot` captures the old price; edit the policy to `0.000000000456`; a second SDK request
  captures the new price; the old snapshot is verified immutable and unchanged.
- **Budget block/unblock** (`accounting.spec.ts`): create a project budget through the UI with a limit
  below the request price; an official SDK request is blocked (`budget_request_too_large`); the budget
  status stays zero-committed; raise the limit through the UI; the same request succeeds; committed
  spend, usage, and the (negative) `usage_debit` ledger entry reflect the exact price.
- **RBAC + audit scope** (`accounting.spec.ts` tests 1–5): system_admin sees the full surface;
  project_admin cannot access deployment pricing/snapshot surfaces and cannot enumerate project B;
  project_viewer is read-only; a project role sees only own-project audit events.

### Verification results (AGV2-022)
- Backend containerized suite (`scripts/dev/v2 test`): **504 passed** (baseline 503 + 1 money
  serialization test).
- Frontend: `npm run build` clean, `npm run lint` clean, **74 unit/component tests pass** (22 files;
  baseline 54 + 20 accounting tests).
- Browser E2E (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **25 passed**
  (baseline 18 + 7 accounting): 4 `web-console`, 7 `operator-rbac`, 7 `management-rbac`, 1
  `catalog-inference`, 7 `accounting`.
- Official OpenAI Python SDK (`openai 2.54.0`): non-stream + stream succeed; the accounting live proofs
  exercise non-stream success plus `budget_request_too_large` fail-closed and recovery after a limit
  raise.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean
  (`openapi.json` only lost the auto `pattern` fields from the money serialization change; `schema.d.ts`
  unchanged).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files, no key patterns in
  the new/modified files).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:39569/v1`; web console loopback-only on
  `http://127.0.0.1:8081`; IdP issuer `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified.

## AGV2-021V — Management lifecycle verification gaps
(unchanged; see the prior handoff section below for the full record)

### Why this task exists
AGV2-021's final review found three narrow acceptance gaps in the **live proof** (not the core UI):
1. the catalog → inference E2E reused the pre-provisioned Ollama provider/account/endpoint instead of
   creating the disposable path through the UI;
2. the credential create/rotate/revoke lifecycle used direct admin API calls instead of the browser UI;
3. `catalog-inference.spec.ts` used raw `fetch`, but the handoff called that an "official OpenAI Python
   SDK" proof.

These three are now closed with truthfully worded evidence (see below).

### Full disposable catalog path through the browser UI
`frontend/e2e/catalog-inference.spec.ts` (system_admin OIDC browser session against the live stack) now
creates the complete disposable path through the actual web pages, using unique names per run
(`e2e-disp-*` + `Date.now()`):
1. **Provider** — `/catalog/providers`, kind `ollama`, capability `text` (reuses the existing Ollama
   adapter; no new backend adapter was invented).
2. **Provider account** — `/catalog/provider-accounts`, no `SecretRef` (local Ollama needs no provider
   secret; raw provider secrets are never placed in browser fields, DB metadata, logs, or Git).
3. **Endpoint** — `/catalog/endpoints`, account = the new account, base destination = the allowlisted
   nomnom Ollama destination `http://192.168.22.50:11434`, `max_concurrency = 1`, active; backend
   egress policy remains authoritative.
4. **Model alias** — `/catalog/models`, client-visible alias `e2e-disp-alias-<runId>`.
5. **Route binding** — `/catalog/routes`, using only the new alias/account/endpoint; upstream model
   `qwen3.8-2b-distill:Q6_K`.

The test resolves the created IDs via `adminJson` postcondition inspection and asserts the new route
uses only the new account/endpoint (not the pre-provisioned `ollama-account` / `ollama-endpoint`), so
no hidden fallback to the pre-existing route is possible.

There is no DELETE surface for catalog resources, so the test leaves the disposable route and endpoint
**inactive** at the end (Active toggle through the UI) rather than mutating rows directly.

### Official Python SDK against the UI-created alias
`frontend/e2e/sdk_inference.py` is a small official `openai` SDK driver invoked by
`frontend/e2e/fixtures.ts` `runOfficialSdk`. It reads the base URL, key, model, and stream flag from
the environment (never argv), and prints a single redacted status line (`OK`, `OK_STREAM`, or
`FAIL <Class> status=<int> code=<code>`). No raw key is ever printed.

- **Official SDK version**: `openai 2.54.0` (Python 3.12, repo venv).
- `base_url` = the web console origin `/v1` (proxied through nginx), model = the disposable alias.
- **inference-auth bypass = false** throughout.
- Non-stream succeeds (`OK`) and stream succeeds (`OK_STREAM`) against the disposable alias.
- Deactivate the RouteBinding through the UI → the official SDK fails safely with `model_unavailable`
  (no fallback); restore/activate → the official SDK succeeds again.

This is genuine **official OpenAI Python SDK** evidence, distinct from the raw `fetch` proof in
AGV2-021 (see the correction note under AGV2-021 below).

### Credential lifecycle entirely through the browser UI
`frontend/e2e/management-rbac.spec.ts` adds "inference credential lifecycle → create/rotate/revoke
through the UI with official SDK outcomes". A disposable project + principal are provisioned via admin
fixtures (allowed), but every lifecycle action is performed through the browser UI:

- **Create** — `/credentials?project=<disposable>`: New credential, select principal, audience =
  inference, Create; the one-time raw key is captured only from the `RevealSecret` UI in test memory.
  Leak canary: key absent from URL/history, `localStorage`/`sessionStorage`, console messages, and the
  DOM after the reveal is closed.
- **Use** — official SDK with the new key against the live alias `gpt-4` succeeds.
- **Rotate** — Rotate → confirm → new one-time key from `RevealSecret`; old key fails `401` and the new
  key succeeds via the official SDK.
- **Revoke** — Revoke → confirm; the rotated key fails `401` immediately.

No direct `adminJson` create/rotate/revoke substitutes the lifecycle actions being proven.

### Operator-rbac robustness fix
`frontend/e2e/operator-rbac.spec.ts` assumed a single endpoint (it paused
`queue/endpoints` `items[0]` and clicked a unique `Pause` button). Because AGV2-021V's catalog E2E now
legitimately leaves a second (inactive) endpoint, the spec was made robust:
- `endpointId` is resolved through the live `gpt-4` route binding (`resolveRouteEndpointId`), not
  `items[0]`;
- the pause/resume UI test scopes `Pause`/`Resume` to the first available button (`.first()`).

This is test-only; no product code changed.

### Verification results (AGV2-021V)
- Backend containerized suite (`scripts/dev/v2 test`): **503 passed** (exit 0; the `-q -q` summary line
  is swallowed by docker-compose output; the count is confirmed by a clean 100% run).
- Frontend: `npm run build` clean, `npm run lint` clean, **54 unit/component tests pass** (18 files).
- Browser E2E (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **18 passed** —
  4 `web-console`, 7 `operator-rbac`, 7 `management-rbac` (6 prior + 1 lifecycle), 1 `catalog-inference`.
- Official OpenAI Python SDK (`openai 2.54.0`): non-stream + stream succeed against the disposable
  alias; deactivate → `model_unavailable`; restore → success; old/rotated/revoked key outcomes proven.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean
  (`python scripts/gen_openapi.py` from repo root + `npm run generate:client` produced no diff on
  `openapi.json` / `schema.d.ts`).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files, no key patterns in
  the new/modified files).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral (`http://127.0.0.1:46291/v1`); web console loopback-only on
  `http://127.0.0.1:8081` (because `matrix-comms-element` occupied `0.0.0.0:8080`; the committed
  `compose.web.yaml` default stays `127.0.0.1:8080`, and only a gitignored `deploy/v2/.env` redirect
  URI plus a throwaway compose override pointed at `8081` for this run); IdP issuer
  `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-021V`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified.

## AGV2-021 — Management UI I

### Implementation commit(s) (origin/v2)
- `feat(web): add identity and catalog management UI` — feature modules
  (`frontend/src/features/identity/`, `frontend/src/features/catalog/`), shared UI primitives
  (`frontend/src/components/ui/`), typed management client methods + `roles.ts` guards, role-aware
  navigation/routing, and the management + catalog unit/component tests.
- `test(web): add management RBAC and catalog-inference browser E2E` — the initial browser E2E.
- `test(web): fix operator-rbac queue pagination fragility` — the pre-existing
  `operator-rbac.spec.ts` "project_admin cancels own queued work" test filtered by queue state before
  and after cancel, so it no longer depends on the freshly submitted request landing on page 1 of an
  oldest-first, 20-per-page queue (it broke as historical traffic accumulated).
- `docs(web): document identity and catalog management UI` — the handoff plus `docs/web-console.md`,
  `docs/development/README.md`, and `frontend/README.md` updates.

> **Correction (recorded in AGV2-021V):** the AGV2-021 handoff called the catalog → inference proof an
> "official OpenAI Python SDK" proof, but AGV2-021's `catalog-inference.spec.ts` used raw
> `fetch`/`submitInference` (raw OpenAI-compatible HTTP) and reused the pre-provisioned
> provider/account/endpoint. That wording overstated the evidence. AGV2-021V replaces it with the
> genuine official-`openai`-SDK proof against a fully UI-created disposable path.

### Routed pages / features
- Identity: `/projects`, `/projects/:projectId`, `/principals`, `/principals/:principalId`,
  `/credentials`, `/roles`.
- Catalog (`system_admin` only): `/catalog/providers`, `/catalog/provider-accounts`,
  `/catalog/endpoints`, `/catalog/quotas`, `/catalog/models`, `/catalog/routes`.
- Shared primitives under `frontend/src/components/ui/`: `Modal`, `ConfirmDialog`, `ErrorBanner`,
  `EmptyState`, `StatusBadge`, `RevealSecret`, `Form`, `Pagination`.
- Typed client: `frontend/src/lib/client.ts` (all management methods + `apiErrorMessage`); role
  guards: `frontend/src/lib/roles.ts` (`isSystemAdmin`, `isProjectAdmin`, `canManageIdentity`,
  `canManageCatalog`).

### RBAC behavior
- `system_admin` sees and may mutate identity + catalog management. `project_admin` sees only
  project-scoped identity surfaces and is denied catalog/deployment mutations; `project_viewer` is
  read-only. Nav hiding is cosmetic; the backend `403`/non-enumeration (`404`) remains the boundary.
- Proven live in `management-rbac.spec.ts`: system_admin create project/provider/credential + raw-key
  reveal + revoke + grant/revoke role; project_admin manage own principals but cannot enumerate B and
  is denied catalog APIs (`403`); project_viewer read-only and denied direct mutations (`403`).

### One-time credential reveal
- Create/rotate return the raw key once, shown only in a `RevealSecret` modal with Copy and a
  "cannot be retrieved again" note; destroyed on close. Never in URL/history/web storage/logs; no
  "show existing key" control. The leak canary proves it does not survive reload/navigation/storage
  inspection and is absent from non-secret list/detail responses.

### SecretRef boundary
- Provider-account configuration selects existing `SecretRef` metadata (stable opaque ID) only; no
  plaintext provider-secret input, no raw provider-secret display, no internal secret backend
  material exposed. Raw secret storage remains deferred (truthful boundary).

### Catalog form / invariant handling
- Endpoint list distinguishes catalog `is_active` from queue runtime `operational_state`.
  Pause/drain/resume remain queue/operator actions. Route-binding/account/quota mismatches and the
  one-active-route rule render stable structured errors (`409 active_route_conflict`,
  `400 parent_mismatch`, `400 destination_denied`) with no client-side fallback chain. Edit forms
  send only changed fields (PATCH semantics); server-side pagination everywhere.

## Issues / Risks
- **Web Docker build**: `vite.config.ts` must alias `cookie` and `set-cookie-parser` to their
  concrete CommonJS entries, or `react-router` fails to resolve them under Rollup
  (`Rollup failed to resolve import "cookie"`). The aliases are committed; rebuild with
  `docker build --no-cache -t aethergate-v2-web:local frontend/`.
- **Live SDK inference speed**: the nomnom Ollama `qwen3.8-2b-distill:Q6_K` non-stream generation is
  slow enough (tens of seconds per call under load) that the live-proof specs need a generous
  `test.setTimeout(240_000)` (`accounting.spec.ts` tests 6/7 and `catalog-inference.spec.ts`). If Ollama
  is reloading the model (cold start) the first SDK call is noticeably slower.
- **Docker healthcheck under this environment**: `docker compose up` waits on service healthchecks
  that the host's systemd-less Docker cannot run (`dial unix /run/user/1000/systemd/private: connect:
  connection refused`), so containers may show `(starting)` while fully functional. Bring the stack up
  with manual `docker start`/compose `up -d --no-deps` rather than `scripts/dev/v2 up`.
- **API recreation and OIDC web callback**: recreating the API with only `compose.yaml` drops
  `AETHERGATE_OIDC_WEB_CALLBACK_PATH=/auth/callback` (it lives in `compose.web.yaml`), which breaks
  browser OIDC login. Recreate the API with both `-f compose.yaml -f compose.web.yaml`, then restart
  the `web` container so nginx re-resolves the `api` DNS name.
- **Operator-rbac single-endpoint assumption** (fixed in AGV2-021V): the spec paused
  `queue/endpoints` `items[0]` and clicked a unique `Pause` button, which broke once the catalog E2E
  left a second endpoint. It now resolves the `gpt-4` route's endpoint and scopes buttons with
  `.first()`. Future endpoint-adding tests should keep this in mind.
- **Operator-rbac queue pagination** (fixed in AGV2-021): the queue page sorts oldest-first and
  paginates at 20, so the pre-existing "project_admin cancels" test depended on the new request
  landing on page 1. The test now filters by queue state before/after cancel.
- Live E2E runs accumulate disposable test data (providers/accounts/endpoints/aliases/credentials/
  queue requests/price policies/budgets). The specs are idempotent, use unique names, scope
  assertions to the current run's route, and disable leftover enabled budgets; the disposable
  route/endpoint are deactivated at the end of the catalog E2E. The pricing table can still accumulate
  leftover price policies across aborted runs (assertions must stay route-scoped).
- **Project list pagination (fixed in AGV2-022C)** — `management-rbac.spec.ts` "creates a project and a
  provider through the UI" used to assert `getByText(projectName)` on page 1 after creating a project,
  but the projects list sorts oldest-first at 20 per page and disposable projects accumulate. It now
  uses `goToLastPage` (real UI pagination) before asserting the new project and provider through the
  browser. Same root-cause family as the AGV2-021 operator-rbac queue pagination fix.
- **management-rbac SDK lifecycle test timeout (fixed in AGV2-022C)** — "inference credential lifecycle"
  issues multiple live `gpt-4` SDK calls (each routed to slow `qwen3.8-2b-distill:Q6_K`) inside the
  default 60s test timeout. It now sets a scoped `test.setTimeout(240_000)`; the SDK calls,
  `bypass=false`, and key assertions are unchanged (no global timeout inflation).
- **Accounting pricing-table pagination (fixed in AGV2-022C)** — `accounting.spec.ts` test 6 asserted the
  run's new PricePolicy on page 1 of the oldest-first, 20-per-page pricing table, which accumulates
  policies across runs. It now scopes the table to the run's route through the existing UI "Route
  binding" filter before asserting, so it is deterministic as leftover policies grow.
- **nginx proxy_read_timeout (fixed in AGV2-022V)** — the web nginx must not use the 60s default for
  `/v1/`; the gateway holds non-stream requests while queued/upstream-generating. `proxy_read_timeout
  600s` / `proxy_send_timeout 600s` are now set; recreate the web image+container to apply.
- **Budget list oldest-first pagination (fixed in AGV2-022V)** — the budget list is ordered oldest-first;
  the budget-unblock proof now targets its row by the unique per-run `budgetName`, not the price string
  (committed_amount equals P on older leftover rows).
- Existing AGV2-020V risks still apply: dev IdP key regeneration vs API JWKS cache; the Chromium
  `--disable-software-rasterizer` rAF fix; do not scale workers via `scripts/dev/v2 workers` (use
  `docker compose up -d --scale worker=N --no-deps worker`); host port `8080` may be occupied.
- `ruff format` remains out of scope (pre-existing reformat); `ruff check` is the gate.

## Key files
- Frontend accounting: `frontend/src/features/accounting/*` (7 pages + 3 test files),
  `frontend/src/lib/decimal.ts` (now incl. `compareDecimalToZero`), `frontend/src/lib/client.ts`,
  `frontend/src/lib/roles.ts`, `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`,
  `frontend/src/pages/DashboardPage.tsx` (decimal-safe budget headroom).
- E2E: `frontend/e2e/accounting.spec.ts` (accounting RBAC + live pricing/snapshot + budget
  block/unblock + released-reservation proofs), `frontend/e2e/catalog-inference.spec.ts` (timeout bump),
  `frontend/e2e/fixtures.ts` (`runOfficialSdk`, `spawnOfficialSdk`, `runReleaseHarness`,
  `listWorkerContainers`, `goToLastPage`), `frontend/e2e/sdk_inference.py`, `frontend/nginx.conf`
  (proxy read/send timeout).
- Backend: `src/aethergate/domain/value_objects.py` (canonical money serialization),
  `src/aethergate/scheduler/service.py` (signed usage debits), `src/aethergate/scheduler/repository.py`
  (`clear_budget_wait_metadata_for_policy`), `src/aethergate/accounting/admin.py` (clears wait metadata
  on limit raise **and policy disable**), `src/aethergate/live_accounting_release.py` (live
  released-reservation harness), `tests/test_contracts.py`, `tests/test_accounting.py`.
- Docs: `docs/web-console.md`, `docs/development/README.md`, `frontend/README.md`.

## Recommended Next Step
Observability metrics (queue/TTFT percentiles, upstream health, retry rate) — the single deferred
follow-up noted throughout the accounting phase and the only recommended next step. Reuse the
now-verified Decimal-safe, typed-client, role-guard, pagination, PATCH, one-time-reveal, and
long-held-SDK-proof patterns, starting from the green regression baseline: backend **506**, frontend
**82**, full Playwright **26**.
