# AetherGate Agent Handoff

## Current State
- Branch: `v2`. AGV2-021V (close management-UI live lifecycle verification gaps) is complete: all
  three gaps are closed, every automated/live criterion is green, and the work is pushed to
  `origin/v2`. `.aethergate-wip` was removed after final push verification.
- Migration head unchanged at `0016`; no new migration; `0001`–`0016` untouched. Legacy Python v1 app
  and the dated architecture audit are untouched.
- AGV2-021 (Management UI I) is implemented and pushed; AGV2-021V is a narrow follow-up that closes
  three live-proof gaps and corrects prior overstatement, without rebuilding AGV2-021 and without
  starting the accounting UI.

## AGV2-021V — Management lifecycle verification gaps

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
- **Operator-rbac single-endpoint assumption** (fixed in AGV2-021V): the spec paused
  `queue/endpoints` `items[0]` and clicked a unique `Pause` button, which broke once the catalog E2E
  left a second endpoint. It now resolves the `gpt-4` route's endpoint and scopes buttons with
  `.first()`. Future endpoint-adding tests should keep this in mind.
- **Operator-rbac queue pagination** (fixed in AGV2-021): the queue page sorts oldest-first and
  paginates at 20, so the pre-existing "project_admin cancels" test depended on the new request
  landing on page 1. The test now filters by queue state before/after cancel.
- Live E2E runs accumulate disposable test data (providers/accounts/endpoints/aliases/credentials/
  queue requests). The specs are idempotent, use unique names, and the queue-drain helper clears
  in-flight work; the disposable route/endpoint are deactivated at the end of the catalog E2E. The
  queue table can still grow over time; the state-filter fix keeps operator assertions robust to that.
- Existing AGV2-020V risks still apply: dev IdP key regeneration vs API JWKS cache; the Chromium
  `--disable-software-rasterizer` rAF fix; do not scale workers via `scripts/dev/v2 workers` (use
  `docker compose up -d --scale worker=N --no-deps worker`); host port `8080` may be occupied.
- `ruff format` remains out of scope (pre-existing reformat); `ruff check` is the gate.

## Key files
- Frontend: `frontend/src/features/identity/*`, `frontend/src/features/catalog/*`,
  `frontend/src/components/ui/*`, `frontend/src/lib/client.ts`, `frontend/src/lib/roles.ts`,
  `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`, `frontend/vite.config.ts`,
  `frontend/src/generated/openapi.test.ts`, `frontend/src/test/helpers.tsx`.
- E2E: `frontend/e2e/catalog-inference.spec.ts` (full UI-created disposable path → official SDK),
  `frontend/e2e/management-rbac.spec.ts` (UI credential lifecycle), `frontend/e2e/operator-rbac.spec.ts`
  (route-resolved endpoint + `.first()` scoping), `frontend/e2e/fixtures.ts` (`runOfficialSdk`,
  `goToLastPage`), `frontend/e2e/sdk_inference.py` (official OpenAI SDK driver).
- Docs: `docs/web-console.md`, `docs/development/README.md`, `frontend/README.md`.

## Recommended Next Step
Begin the accounting/budget/usage/ledger management UI (the next management phase after this task),
reusing the now-verified typed client, role-guard, pagination, PATCH, and one-time-reveal patterns
established here, starting from the green 503/54/18 regression baseline.
