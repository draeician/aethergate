# AetherGate v2 — Web Console

Living document. The web console is a same-origin React + TypeScript SPA that authenticates through
the existing human OIDC Authorization Code + PKCE flow and consumes the `/admin/v1` API. It is the
operator surface for the live scheduler, not a replacement for the CLI or the service-account admin
API.

## Authentication and session

- Authentication is the `HttpOnly` `ag_session` cookie only. There is **no** admin API key, CLI
  token, OIDC token, session cookie, or CSRF token stored in `localStorage`, `sessionStorage`,
  `IndexedDB`, frontend config, or JS-readable application state.
- The login page (`/login`) has no credential input; it links to
  `/admin/v1/auth/oidc/login`. The backend completes the flow and, in web-console mode, sets the
  session cookie plus a separate JS-readable `ag_csrf` cookie and `302`-redirects to the fixed
  `/auth/callback` route.
- On boot the app calls `GET /admin/v1/auth/session`. `401` → `unauthenticated` (login page);
  success → `authenticated` (console). Any later `401` is treated centrally as session expiry and
  returns the console to login; `403` (authorization, incl. CSRF failure) is kept distinct.
- Mutations attach `X-CSRF-Token` read from the `ag_csrf` cookie; `GET`/`HEAD` do not. The raw CSRF
  token is never placed in a URL/query/fragment and never stored in web storage.
- Backend RBAC remains authoritative. Hiding a button or a nav item is cosmetic only; the server
  re-checks every protected action on each request.

See `docs/architecture/security.md` ("Identity phase 5 — web console session and CSRF cookie") for the
full design.

## Client and API types

The frontend stops hand-maintaining endpoint/types as its source of truth. The workflow:

1. `npm run generate:openapi` — export the FastAPI OpenAPI schema deterministically from the running
   app into `frontend/src/generated/openapi.json` (via `scripts/gen_openapi.py`).
2. `npm run generate:client` — run `openapi-typescript` to emit
   `frontend/src/generated/schema.d.ts` (committed; do not hand-edit).

The application wrapper is `frontend/src/lib/client.ts`: it owns `credentials: "include"`, CSRF
injection for mutations, structured AetherGate error parsing, and the centralized 401 →
auth-expired transition. It targets `/admin/v1` only; there is no generic arbitrary-path admin
escape hatch, and server-side pagination stays server-side.

## Routes and pages

- `/login` — OIDC sign-in.
- `/auth/callback` — session bootstrap after the backend redirect (fixed failure state on failure).
- `/` — live operator dashboard.
- `/queue` — paginated queue/request table with filters (state; endpoint and project for
  `system_admin`).
- `/queue/:requestId` — safe request metadata detail.
- `/outcome-unknown` — `system_admin`-only outcome-unknown list with reconcile actions.

### Identity management (AGV2-021)

- `/projects` — server-paginated project list; `/projects/:projectId` — detail with principals and
  role assignments.
- `/principals` — paginated principal list (USER vs SERVICE); `/principals/:principalId` — detail.
- `/credentials` — credential metadata list (audience `inference` vs `admin`), create/rotate/revoke.
- `/roles` — role-assignment list with grant/revoke.

### Catalog management (AGV2-021, `system_admin` only)

- `/catalog/providers` — provider CRUD (kind, capabilities, active).
- `/catalog/provider-accounts` — provider-account CRUD with SecretRef metadata selection.
- `/catalog/endpoints` — endpoint CRUD; shows catalog `is_active` and queue runtime
  `operational_state` side by side.
- `/catalog/quotas` — quota-group and quota-limit CRUD (request/token metric, window, units).
- `/catalog/models` — public model-alias CRUD.
- `/catalog/routes` — route-binding CRUD (alias → provider account → endpoint → optional quota group).

### Accounting management (AGV2-022)

- `/accounting/pricing` — route `PricePolicy` CRUD (request/token/image/minute billing; currency,
  `unit_scale`, request/input/output price, enabled, name) with route/enabled/billing-unit filters.
- `/accounting/snapshots` — immutable `PriceSnapshot` list/detail (route/account/alias, source policy,
  exact prices, `captured_at`); read-only.
- `/accounting/budgets` — project `ProjectBudgetPolicy` create/edit (project, name, currency,
  `limit_amount`, `window_seconds`, enabled) with live budget status/headroom; currency and
  `window_seconds` are immutable after creation.
- `/accounting/reservations` — read-only `BudgetReservation` history; a released pre-dispatch
  reservation renders `price_snapshot_id = null` as "No snapshot / released before dispatch".
- `/accounting/usage` — immutable `UsageRecord` history (attribution, exact amount, units,
  `recorded_at`); read-only, no content/secret material.
- `/accounting/ledger` — append-only `LedgerEntry` history (signed exact amount, entry type,
  usage-record link); read-only, no manual adjustment writes.
- `/audit` — read-only `AuditEvent` list/detail, scoped by role.

Accounting role-aware behavior:

- `system_admin`: all accounting + audit pages.
- `project_admin`: own-project budgets/reservations/usage/ledger/audit; cannot access deployment
  pricing/snapshot surfaces.
- `project_viewer`: own-project read-only budgets/status/reservations/usage/ledger/audit.

Money is fixed-point Decimal and stays a string end-to-end (`frontend/src/lib/decimal.ts` validates and
formats without `Number()`/`parseFloat()`). The web console never computes an authoritative balance the
backend did not provide.

Identity/catalog role-aware behavior:

- `system_admin`: sees the identity and catalog management routes plus all operator controls
  (endpoint occupied-vs-available slots, operational state, pause/drain/resume, quota/cooldown,
  project/endpoint filters, outcome-unknown reconcile). Can create/update projects, providers,
  provider accounts, endpoints, quota groups/limits, model aliases, route bindings, credentials, and
  grant/revoke roles.
- `project_admin`: sees the identity management routes scoped to its own project (projects,
  principals, credentials, roles). Can manage permitted principals/credentials/roles in its own
  project and cancel its own eligible queued work; cannot enumerate other projects, cannot see the
  catalog routes, and is denied deployment/catalog mutations by the backend (`403`).
- `project_viewer`: read-only identity surfaces; no mutation controls; backend denies mutations
  (`403`).

Hiding a nav item or a control is cosmetic only. The backend is authoritative: a project role that
navigates directly to a catalog route or calls a catalog/queue-deployment mutation still receives
`403`, and cross-project opaque IDs are non-enumerating (`404`).

## Management UI behavior (AGV2-021)

- **Feature modules**: new console code lives under `frontend/src/features/identity/` and
  `frontend/src/features/catalog/`, with shared primitives in `frontend/src/components/ui/`
  (`Modal`, `ConfirmDialog`, `ErrorBanner`, `EmptyState`, `StatusBadge`, `RevealSecret`, `Form`,
  `Pagination`). The typed client in `frontend/src/lib/client.ts` is the single access point.
- **Server-side pagination and filtering**: every list page pages server-side (page size 20 default),
  shows the total when the contract supplies it, and keeps filter state in component state. The
  frontend never fetches all pages to render one table.
- **PATCH semantics**: edit forms send only changed fields (omitted fields are unchanged; explicit
  `null` only where the contract allows clearing), so backend PATCH semantics are respected and
  generated OpenAPI types are used end to end.
- **Credential one-time reveal**: create/rotate return a raw key once. It is shown only inside a
  dedicated reveal modal/panel with a Copy action and a clear "cannot be retrieved again" note; the
  value is never placed in the URL/history, never written to `localStorage`/`sessionStorage`/
  `IndexedDB`, never logged, and is destroyed when the reveal closes. There is no "show existing key"
  control (the backend cannot recover it).
- **SecretRef metadata boundary**: provider-account configuration selects from existing
  `SecretRef` metadata (a stable opaque ID). The console never invents a plaintext provider-secret
  input or displays raw provider secrets; raw secret storage remains deferred.
- **Endpoint catalog-vs-runtime distinction**: the endpoint list distinguishes catalog `is_active`
  (config unavailable) from queue runtime `operational_state` (`active`/`paused`/`draining`, a
  temporary operator scheduling state). Pause/drain/resume remain queue/operator actions, not
  endpoint PATCH mutations.
- **Route-binding invariants**: account/endpoint/quota-group mismatches and the one-active-route rule
  render as stable structured backend errors (e.g. `409 active_route_conflict`) with no client-side
  fallback chain.

## Accounting management behavior (AGV2-022)

- **Decimal-safe representation**: monetary fields stay strings end-to-end; the console never converts
  an accounting Decimal to an IEEE-754 `Number` for editing, arithmetic, or formatting. Syntax is
  validated with `frontend/src/lib/decimal.ts` (no `Number()`/`parseFloat()`), and exact strings are
  sent back to the backend.
- **Pricing vs immutable snapshots**: `PricePolicy` is mutable configuration; `PriceSnapshot` captures
  immutable dispatch-time pricing. Editing a policy never rewrites a captured snapshot.
- **Budget policy vs headroom**: a project budget is an optional policy control, not a prepaid balance.
  The console displays the server-returned `limit_amount`/`committed_amount`/`reserved_amount`/`headroom`
  and never recomputes an authoritative headroom client-side. `currency` and `window_seconds` are
  immutable after create; the edit form renders them read-only and points to creating a replacement
  policy.
- **Immutable history**: `BudgetReservation`, `UsageRecord`, and `LedgerEntry` are read-only. A released
  pre-dispatch reservation shows `price_snapshot_id = null` as "No snapshot / released before dispatch".
  The ledger shows signed exact amounts (`usage_debit` is negative); there are no manual adjustment
  writes.
- **One-enabled-price-policy invariant**: a second enabled `PricePolicy` on the same route renders a
  stable `409 price_policy_conflict`; the console never auto-disables the previous policy.
- **Audit scope**: deployment-scoped (`project_id = null`) events are hidden from project roles;
  `system_admin` sees both deployment and project events.

## Dashboard data sources

| Surface | Endpoint | Notes |
| --- | --- | --- |
| Queue summary | `GET /admin/v1/queue/summary` | queued/in-flight/outcome-unknown, oldest wait |
| Endpoint slots | `GET /admin/v1/queue/endpoints` | `system_admin` only |
| Quota/cooldown | `GET /admin/v1/queue/quota-status` | `system_admin` only |
| Budget headroom | `GET /admin/v1/projects/{id}/budget-status` | project-scoped roles |
| Queue wait / streaming TTFT / retry | `GET /admin/v1/observability/summary` | scoped to the caller's authorized project(s) |
| Upstream health | `GET /admin/v1/observability/upstreams` | `system_admin` only |

### Operational observability metrics

The dashboard renders authoritative operational metrics from the durable scheduler
facts (see `docs/architecture/observability.md` for the exact definitions):

- **Queue wait** — admission delay (`earliest attempt start − queued`); shows
  p50/p95/p99 and sample count.
- **Streaming TTFT** — dispatch-to-first-token (streaming only, `first_token_at −
  attempt started_at`); the label states it is streaming/dispatch-to-first-token.
- **Retry rate** — request retry rate plus retried/attempted counts and the
  retry-attempt total (additional attempts for the same gateway request).
- **Upstream health** (`system_admin`) — one compact card per endpoint with a
  passive success rate, successes, upstream failures, ambiguous attempts, 429
  count, last success/failure, and cooldown.

A 15m / 1h / 24h window selector drives the summary/upstream reads. When there is
no sample the console renders "No samples" — it never fabricates a `0ms`
percentile or a `100%` success rate. Project roles see only their own
queue/TTFT/retry aggregate and never request or render deployment endpoint
health.

## Development and deployment

- Same-origin browser API calls; `frontend/vite.config.ts` proxies `/admin`, `/health`, `/v1` to the
  local API (target overridable with `VITE_API_PROXY_TARGET`; runtime client code uses relative paths
  and never hard-codes the API host port).
- `deploy/v2/compose.web.yaml` (committed) adds the `web` nginx service (loopback-only, fixed dev
  port `8080`) and the deterministic in-repo `dev-oidc-idp` (`python -m
  aethergate.dev_oidc_idp_server`, port `8090`). It replaces the old uncommitted OIDC override.
- `scripts/dev/v2 web up` writes the OIDC env into `deploy/v2/.env` and brings up the web + IdP
  stack; `scripts/dev/v2 web url` prints the web URL and issuer; `scripts/dev/v2 web down` stops it.
- The IdP `ISSUER` must be a URL reachable by both the gateway container and the browser — in
  practice the host LAN IP (e.g. `http://192.168.x.y:8090`); the web console redirect URI is the fixed
  loopback `http://127.0.0.1:8080/admin/v1/auth/oidc/callback`. The IdP auto-approves `/authorize`
  and generates its RSA key in memory (a restart changes the key; restart the API after restarting
  the IdP so its JWKS cache does not reject the new signature).
- The IdP subject resolves through the same `ExternalIdentity` linking as browser OIDC. A
  JIT-provisioned identity is unprivileged (zero roles); to exercise the console, link the dev subject
  to a principal that has an admin role (bootstrap + `POST /admin/v1/oidc/identities`).

## Tests

```
cd frontend
npm run build         # tsc + vite production build
npm run lint          # eslint
npm run test          # vitest unit/component tests
npm run test:e2e      # Playwright browser tests (requires a live stack)
```

The Playwright specs (`frontend/e2e/`) target `WEB_BASE_URL` (default `http://127.0.0.1:8080`) and
use the deterministic local OIDC provider — no external IdP credentials are required.
