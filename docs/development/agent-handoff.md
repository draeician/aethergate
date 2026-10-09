# AetherGate Agent Handoff

## Current State
- Branch: `v2`. AGV2-021 (Management UI I — identity, credentials, projects, and catalog
  administration) is implemented and verified: every stated automated/live criterion is green and
  pushed to `origin/v2`. `.aethergate-wip` has been removed after final push verification.
- Migration head unchanged at `0016`; no new migration; `0001`–`0016` untouched. Legacy Python v1 app
  and the dated architecture audit are untouched.
- Prior work (AGV2-020/020V) established the web-console OIDC/CSRF foundation, the live operator
  dashboard/queue, and the real browser RBAC/CSRF/lifecycle E2E. That foundation is unchanged and
  remains green.

## AGV2-021 — Management UI I

### Implementation commit(s) (origin/v2)
- `feat(web): add identity and catalog management UI` — feature modules
  (`frontend/src/features/identity/`, `frontend/src/features/catalog/`), shared UI primitives
  (`frontend/src/components/ui/`), typed management client methods + `roles.ts` guards, role-aware
  navigation/routing, and the management + catalog unit/component tests.
- `test(web): fix operator-rbac queue pagination fragility` — the pre-existing
  `operator-rbac.spec.ts` "project_admin cancels own queued work" test filtered by queue state before
  and after cancel, so it no longer depends on the freshly submitted request landing on page 1 of an
  oldest-first, 20-per-page queue (it broke as historical traffic accumulated).
- `docs(web): document identity and catalog management UI` — this handoff plus `docs/web-console.md`,
  `docs/development/README.md`, and `frontend/README.md` updates.

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

### Live catalog → SDK inference proof
- `catalog-inference.spec.ts` (browser as system_admin against the live stack): create a disposable
  provider/account/endpoint/alias/route, run the official OpenAI Python SDK (`openai 2.54.0`) with
  inference-auth bypass **false** and the model name = the new alias; inference succeeds; deactivate
  the route in the UI → inference fails safely with `model_unavailable` (no fallback); restore →
  inference succeeds again. A one-off SDK non-stream + stream regression also passed.

### Live credential lifecycle proof
- `management-rbac.spec.ts`: create an inference credential for a disposable principal/project; copy
  the one-time value; use it with the official SDK; rotate; old key fails / new key succeeds; revoke;
  revoked key fails immediately. Admin-credential audience separation preserved (no admin key issued
  for inference scope).

### Verification results (AGV2-021)
- Backend containerized suite (`scripts/dev/v2 test`): **503 passed** (exit 0; collect-only confirms
  503 tests; the `-q` summary line is swallowed by docker-compose output, so the count is from
  collection + a clean 100% run).
- Frontend: `npm run build` clean, `npm run lint` clean, **54 unit/component tests pass** (18 files).
- Browser E2E (`npx playwright test`, chromium): **18 passed** — 4 `web-console`, 7 `operator-rbac`,
  6 `management-rbac`, 1 `catalog-inference`.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean
  (`scripts/gen_openapi.py` + `npm run generate:client` produced no diff on `openapi.json` /
  `schema.d.ts`).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files, no key patterns).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral (`http://127.0.0.1:46291/v1`); web console loopback-only on
  `http://127.0.0.1:8081` (because `matrix-comms-element` occupied `0.0.0.0:8080`; committed
  `compose.web.yaml` default stays `127.0.0.1:8080`, and only a gitignored `deploy/v2/.env` redirect
  URI plus a throwaway compose override pointed at `8081` for this run); IdP issuer
  `http://192.168.22.50:8090`.

## Issues / Risks
- **Web Docker build**: `vite.config.ts` must alias `cookie` and `set-cookie-parser` to their
  concrete CommonJS entries, or `react-router` fails to resolve them under Rollup
  (`Rollup failed to resolve import "cookie"`). The aliases are committed; rebuild with
  `docker build --no-cache -t aethergate-v2-web:local frontend/`.
- **Operator-rbac queue pagination** (fixed in this task): the queue page sorts oldest-first and
  paginates at 20, so the pre-existing "project_admin cancels" test depended on the new request
  landing on page 1. The test now filters by queue state before/after cancel.
- Live E2E runs accumulate disposable test data (providers/accounts/endpoints/aliases/credentials/
  queue requests). The specs are idempotent and the queue-drain helper clears in-flight work, but the
  queue table can grow over time; the state-filter fix keeps operator assertions robust to that.
- Existing AGV2-020V risks still apply: dev IdP key regeneration vs API JWKS cache; the Chromium
  `--disable-software-rasterizer` rAF fix; do not scale workers via `scripts/dev/v2 workers` (use
  `docker compose up -d --scale worker=N --no-deps worker`); host port `8080` may be occupied.
- `ruff format` remains out of scope (pre-existing reformat); `ruff check` is the gate.

## Key files
- Frontend: `frontend/src/features/identity/*`, `frontend/src/features/catalog/*`,
  `frontend/src/components/ui/*`, `frontend/src/lib/client.ts`, `frontend/src/lib/roles.ts`,
  `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/vite.config.ts`,
  `frontend/src/generated/openapi.test.ts`, `frontend/src/test/helpers.tsx`.
- E2E: `frontend/e2e/management-rbac.spec.ts`, `frontend/e2e/catalog-inference.spec.ts`,
  `frontend/e2e/operator-rbac.spec.ts` (state-filter fix).
- Docs: `docs/web-console.md`, `docs/development/README.md`, `frontend/README.md`.

## Recommended Next Step
Begin the accounting/budget/usage/ledger management UI (the next management phase after this task),
reusing the now-verified typed client, role-guard, pagination, PATCH, and one-time-reveal patterns
established here, starting from the green 503/54/18 regression baseline.
