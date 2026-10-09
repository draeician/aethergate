# AetherGate v2 — Web Console

Same-origin React + TypeScript operator console for the v2 `/admin/v1` API. See
`../docs/web-console.md` for the design, data sources, and deployment path.

## Setup

```bash
npm install
```

## Development

```bash
npm run dev            # Vite dev server (proxies /admin, /health, /v1 to the local API)
npm run build          # tsc -b && vite build
npm run lint           # eslint
npm run preview        # serve the production build
```

The Vite dev proxy target defaults to `http://localhost:8000`; override with
`VITE_API_PROXY_TARGET` if the API binds another port. Runtime client code uses same-origin relative
paths — it never hard-codes the API host port.

## Generated API types

The frontend does not hand-maintain endpoint/types as its source of truth:

```bash
npm run generate:openapi   # deterministic FastAPI OpenAPI export -> src/generated/openapi.json
npm run generate:client    # openapi-typescript -> src/generated/schema.d.ts (committed)
```

`src/generated/schema.d.ts` is auto-generated; do not edit it. The application wrapper
(`src/lib/client.ts`) owns credentials, CSRF, and error handling on top of the generated types.

## Tests

```bash
npm run test          # vitest unit/component tests
npm run test:watch    # vitest in watch mode
npm run test:e2e      # Playwright browser tests (requires a live stack)
```

The Playwright specs in `e2e/` target `WEB_BASE_URL` (default `http://127.0.0.1:8080`) and use the
deterministic local OIDC provider — no external IdP credentials required.

## Management UI (AGV2-021)

The console now includes identity and catalog management over `/admin/v1`, organized into feature
modules:

- `src/features/identity/` — Projects (`/projects`, `/projects/:projectId`), Principals
  (`/principals`, `/principals/:principalId`), Credentials (`/credentials`), Roles (`/roles`).
- `src/features/catalog/` — Providers (`/catalog/providers`), Provider Accounts
  (`/catalog/provider-accounts`), Endpoints (`/catalog/endpoints`), Quotas (`/catalog/quotas`),
  Model Aliases (`/catalog/models`), Route Bindings (`/catalog/routes`).
- `src/components/ui/` — shared `Modal`, `ConfirmDialog`, `ErrorBanner`, `EmptyState`, `StatusBadge`,
  `RevealSecret`, `Form`, `Pagination`.
- `src/lib/client.ts` — typed management client; `src/lib/roles.ts` — `isSystemAdmin` /
  `isProjectAdmin` guards for role-aware navigation (backend remains authoritative).

Security notes:

- Credential raw keys are shown once in a `RevealSecret` modal (Copy, destroyed on close); never in
  URL/history/web storage, and no "show existing key" control.
- Provider accounts select existing `SecretRef` metadata only (no plaintext provider-secret input).
- Endpoints distinguish catalog `is_active` from queue runtime `operational_state`.

Live E2E: `e2e/management-rbac.spec.ts` (RBAC + one-time reveal canary + credential lifecycle) and
`e2e/catalog-inference.spec.ts` (disposable alias → real SDK inference → deactivate fails safely →
restore succeeds).

## Accounting management (AGV2-022)

The console now includes the accounting-management surface over `/admin/v1`:

- `src/features/accounting/` — Pricing (`/accounting/pricing`), Price Snapshots
  (`/accounting/snapshots`), Budgets (`/accounting/budgets`), Reservations
  (`/accounting/reservations`), Usage (`/accounting/usage`), Ledger (`/accounting/ledger`), Audit
  (`/audit`).
- `src/lib/decimal.ts` — Decimal-safe validation/formatting that never uses `Number()`/`parseFloat()`;
  monetary fields stay strings end-to-end.
- `src/lib/client.ts` — typed accounting methods; `src/lib/roles.ts` — accounting role guards
  (`system_admin` sees all; `project_admin` own-project; `project_viewer` read-only).

Accounting invariants:

- `PricePolicy` is mutable config; `PriceSnapshot`, `UsageRecord`, `LedgerEntry`, and
  `BudgetReservation` are immutable/read-only history.
- Budgets are optional policy controls (not prepaid balances); `currency`/`window_seconds` are
  immutable after create, and the console uses the server-returned headroom.
- A released pre-dispatch reservation renders `price_snapshot_id = null` safely.
- Ledger amounts are signed (`usage_debit` negative); there are no manual adjustment writes.

Live E2E: `e2e/accounting.spec.ts` (accounting RBAC + exact-Decimal price policy + immutable snapshot
proof + budget block/unblock proof via the official OpenAI SDK).

