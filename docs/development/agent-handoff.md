# AetherGate Agent Handoff

## Current State
- Branch: `v2`. Remote `origin/v2` contains three AGV2-020 commits.
- AGV2-020 (web-console foundation) is **implemented and largely verified live**. One criterion is
  not demonstrated live (see "Residual gap" below), so `.aethergate-wip` is intentionally **left in
  place**.
- Migration head unchanged at `0016`; no new migration; `0001`–`0016` untouched. Legacy Python v1
  app and the dated architecture audit are untouched.

## AGV2-020 — web-console OIDC foundation

### Commits (origin/v2)
- `6f5fb88 feat(web): add web-console OIDC completion mode` — backend browser-session + CSRF cookie,
  fixed-302 web callback, `ag_csrf` cookie, deterministic `scripts/gen_openapi.py`, tests.
- `e10e61b feat(web): add OIDC operator console foundation` — typed frontend client, login/callback
  pages, role-aware shell, dashboard + queue + outcome-unknown pages, deploy (`compose.web.yaml` +
  `dev_oidc_idp_server.py`), docs.
- `0204820 fix(web): make dev web-up rewrite OIDC issuer/redirect and fix E2E logout` — `scripts/dev/v2`
  overwrites `AETHERGATE_OIDC_ISSUER`/`_REDIRECT_URI` on every web bring-up; E2E locator + logout test.

### Auth / CSRF browser design
- Browser uses the human OIDC authorization-code + PKCE flow. Web mode (`AETHERGATE_OIDC_WEB_CALLBACK_PATH`
  set) returns a fixed `302` to the SPA callback instead of JSON.
- On completion the backend sets `ag_session` (HttpOnly, `Path=/admin`) and `ag_csrf`
  (`HttpOnly=false`, JS-readable, `Path=/`). The raw CSRF token is only in the `ag_csrf` cookie, never
  in URL/history/localStorage/sessionStorage; the frontend reads it and echoes `X-CSRF-Token` on
  mutations. Missing/wrong CSRF on a browser-session mutation is `403`.
- No browser master/API/OIDC secret is ever stored client-side; RBAC remains authoritative server-side
  (UI hiding is cosmetic).

### Generated / typed client workflow
- `scripts/gen_openapi.py` (venv) emits `frontend/src/generated/openapi.json` deterministically.
- `frontend/package.json` scripts: `generate:openapi`, `generate:client` (openapi-typescript →
  `src/generated/schema.d.ts`), `test`, `test:watch`, `test:e2e`.
- `src/lib/client.ts` is the single same-origin typed client: `credentials: "include"`, CSRF injection,
  `ApiError` envelope, centralized `401` → `AUTH_EXPIRED_EVENT` (no hard-coded host/port).

### Routed pages
- `/` → role-aware shell; `/login`, `/auth/callback`, `/dashboard`, `/queue`, `/queue/:id`,
  `/outcome-unknown`. Old v1 pages (Users/Keys/Endpoints/Models/Logs) removed.

### Dashboard data sources / unavailable metrics
- Real sources: `queue/summary`, `queue/endpoints`, `quota-status`, `accounting` budget. TTFT /
  upstream health / retry are **not instrumented** and render explicitly as "Not yet instrumented".

### Queue / operator UI behavior
- Queue page: filterable, paginated, per-request detail + cancel. Dashboard exposes pause/drain/resume.
  `OutcomeUnknownPage` offers reconcile only for `system_admin` and only for `failed`/`cancelled`.

## Verification results
- Backend full suite (host, `AETHERGATE_TEST_DATABASE_URL` + `POSTGRES_*`): **500 passed** (baseline 495).
  `ruff check src tests` clean; `ruff format` still out of scope (pre-existing).
- Frontend: `npm run build` clean, `npm run lint` clean, **27 unit/component tests pass** (client, roles,
  AuthContext, LoginPage, DashboardPage, QueuePage).
- Browser E2E (`npx playwright test`, chromium): **4 passed** — unauthenticated→login page (no credential
  input), OIDC sign-in→dashboard (no secret in URL/storage), dashboard renders live queue data, logout
  revokes server session (probe returns `401`) and returns to login.
- Real stack live checks (API `http://127.0.0.1:8080`, IdP issuer `http://192.168.22.50:8090`, worker +
  Ollama `http://192.168.22.50:11434`, inference-auth bypass false):
  - **B** web login: verified by E2E 1–2.
  - **C** dashboard: E2E 3 renders live counts for the real `ollama-endpoint` (`max_concurrency=2`,
    `operational_state=active`).
  - **D** six/two: 6 real `gpt-4` requests fired → peak queued 6, concurrency capped (never exceeded 2;
    local model ~1.4s/request so simultaneous in-flight=2 was not captured), all 6 converged to
    `succeeded`.
  - **E** pause/drain/resume: live API → `paused`, then `draining` (`draining_complete=true`), then
    `active`.
  - **F** cancel: live API `system_admin` cancelled queued requests → `cancelled_now` / `cancelled`;
    the held client completion then errored (expected). project_admin/project_viewer/cross-project
    matrix is deterministic-test-covered, not re-run in-browser.
  - **H** logout/expiry: E2E 4 proves server-side revocation + return to login; role revocation
    enforcement is deterministic-test-covered.
  - **I** CSRF: deterministic tests (missing/wrong → `403`, valid → success); browser sets `ag_csrf`
    (E2E 2) with no raw token logged.
  - **J** regression: OpenAI Python SDK non-stream + stream passed against `gpt-4` with bypass false.

## Migration / no-migration decision
- No migration. Browser-session CSRF needs no schema change (CSRF stored server-side with the session;
  cookie names are constants). `0016` remains head.

## Residual gap (why `.aethergate-wip` remains)
- Criterion **G** (`outcome_unknown` reconcile) is deterministic-test-covered but **not re-created live**:
  producing a real `outcome_unknown` requires simulating dead-worker post-dispatch lease expiry (direct
  DB lease manipulation on the live dev DB), which is deliberately avoided. `OutcomeUnknownPage` render +
  reconcile authorization are unit/deterministic-tested; the live browser reconcile of an
  `outcome_unknown` row is the one step left before the marker is removed.

## Issues / Risks
- The dev IdP regenerates its RSA key on restart; the API caches JWKS (~300s). After restarting the IdP,
  restart the API (or wait out the cache) before browser login. `scripts/dev/v2 web up` now rewrites the
  browser-reachable issuer/redirect on every bring-up (commit `0204820`).
- The local Ollama model is fast (~1.4s), so observing simultaneous `in-flight=2` needs a slower model or
  a higher `max_tokens`; the cap was confirmed to never exceed 2 regardless.
- `ruff format` remains out of scope (pre-existing reformat of many files); `ruff check` is the gate.

## Key files
- Backend: `src/aethergate/config.py`, `src/aethergate/identity/session.py`,
  `src/aethergate/api/session_auth.py`, `src/aethergate/dev_oidc_idp_server.py`, `tests/test_oidc_session.py`.
- Frontend: `frontend/src/lib/client.ts`, `frontend/src/context/*`, `frontend/src/pages/*`,
  `frontend/src/components/Sidebar.tsx`, `frontend/src/generated/*`.
- Deploy: `deploy/v2/compose.yaml`, `deploy/v2/compose.web.yaml`, `scripts/dev/v2`.
- Docs: `docs/architecture/security.md`, `docs/architecture/admin-api.md`, `docs/web-console.md`,
  `docs/development/README.md`, `frontend/README.md`.

## Recommended Next Step
Complete criterion **G** live (create a controlled `outcome_unknown` — e.g. stop the worker after
dispatch on the local dev stack, not production — and drive the `OutcomeUnknownPage` reconcile as
`system_admin`, confirming project roles are denied and `succeeded` is never offered). Then commit any
residual handoff note, verify `origin/v2`, and remove `.aethergate-wip`.
