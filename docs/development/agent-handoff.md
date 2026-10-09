# AetherGate Agent Handoff

## Current State
- Branch: `v2`. AGV2-020 (web-console foundation) is implemented, and AGV2-020V (close live browser
  verification gaps) is now complete: every live criterion is green, tests/docs/fixes are pushed to
  `origin/v2`, and `.aethergate-wip` has been removed.
- AGV2-020V was **independently re-verified live on 2026-10-09** against the isolated nomnom stack
  (backend 503, frontend 27, Playwright 11, build/lint/ruff clean, real SDK non-stream + stream,
  live outcome_unknown reconcile, live project RBAC/revocation/CSRF, and a visible six/two browser
  capture). No code change was required; only this handoff was corrected.
- Migration head unchanged at `0016`; no new migration; `0001`–`0016` untouched. Legacy Python v1 app
  and the dated architecture audit are untouched.

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

## AGV2-020V — live verification gaps closed

All previously deterministic-only live criteria are now demonstrated with a real browser against the
isolated nomnom dev stack. Automated Playwright coverage was added where practical; the controlled
`outcome_unknown` creation is kept as a one-off live driver (recorded here) because it is too expensive
to reproduce inside every E2E run.

### Commits (this push)
- `fix(config): treat empty OIDC web callback path as unset` — `src/aethergate/config.py` adds
  `oidc_web_callback_path` to the empty→None validator, so the base compose default
  (`${AETHERGATE_OIDC_WEB_CALLBACK_PATH:-}`) no longer fails `_validate_oidc` and crashes api-only
  invocations (`inspect`/`reconcile`/`test`/`migrate`).
- `test(web): close live operator verification gaps` — dev IdP `/subject` control endpoint,
  `tests/test_oidc_session.py` + `tests/test_settings.py` additions, Chromium rAF launch fix,
  `frontend/e2e/fixtures.ts` + `operator-rbac.spec.ts`, `.gitignore` for Playwright artifacts, handoff.

### Outcome_unknown (criterion G) — live
- **Creation method** (real scheduler semantics, no row fabricated): scale to one worker
  (`docker compose up -d --scale worker=1 --no-deps worker`), submit one slow real `gpt-4` request
  (`max_tokens=2000`, ~15s), wait for durable `dispatched`, `docker kill aethergate-v2-worker-1`,
  wait 125s for the 120s `AETHERGATE_WORKER_LEASE_SECONDS` to expire, restart the worker; the recovery
  loop then marks the abandoned dispatch `outcome_unknown`.
- **system_admin browser reconcile**: `OutcomeUnknownPage` listed the request; the UI offered only
  `failed` and `cancelled` (never `succeeded`); reconcile as `failed` succeeded with the real CSRF
  cookie and the row left `outcome_unknown` (DB shows a terminal `failed` request). Slot release is
  covered deterministically by `test_reconcile_releases_slot_once`.
- **project-role denial**: `POST /admin/v1/queue/requests/{id}/reconcile` is deployment-only; covered by
  `test_reconcile_rejects_succeeded_and_project_role` (project_admin cannot reconcile) and the live E2E
  proving project roles get `403` on deployment controls; the reconcile controls are gated to
  `system_admin` in the UI.

### Live project queue RBAC (criterion 2) — browser
- `operator-rbac.spec.ts` drives real OIDC browser sessions: project_admin(A) sees A rows, cannot see B
  (direct B request id is `404`/non-enumerating), cancels eligible queued A work via the UI, and is
  denied deployment controls (`403`); project_viewer(A) sees A, no cancel control, direct cancel is
  `403`, and deployment endpoints are `403`.

### Live role revocation / deactivation (criterion 3) — browser
- Role revocation reflects on the next protected request without re-login (`200` → `403`).
- Principal deactivation returns the browser to the login state (`401` + login link) via the centralized
  auth-expiry flow.

### Live CSRF negative proof (criterion 4) — browser
- Missing `X-CSRF-Token` → `403 invalid_csrf_token`; wrong value → `403 invalid_csrf_token`; correct
  `ag_csrf` echoed as `X-CSRF-Token` → `200`. Raw token never logged.

### Six / two (criterion 5) — browser
- Endpoint `ollama-endpoint` `max_concurrency=2`; six slow real requests (`max_tokens=2000`, ~15s each
  when serialized); dashboard visibly captured `in_flight_total == 2` and `occupied_slots == 2`
  (`2 / 2 slots`, available 0) with 4 queued; never exceeded 2; all settled.

## Verification results (AGV2-020V)
- Backend containerized suite (`scripts/dev/v2 test`): **503 passed** (baseline 500 + 3 new in
  `tests/test_oidc_session.py`). `ruff check src tests` clean; a full `ruff check .` also reports
  pre-existing violations in the legacy v1 `app/` and `scripts/` files, which are out of scope for
  this task (not introduced by AGV2-020/020V).
- Frontend: `npm run build` clean, `npm run lint` clean, **27 unit/component tests pass**.
- Browser E2E (`npx playwright test`, chromium): **11 passed** — 4 `web-console.spec.ts` +
  7 `operator-rbac.spec.ts`.
- `git diff --check` clean; secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential
  files, no key patterns).
- Real SDK regression (official OpenAI Python SDK against `gpt-4`, inference-auth bypass **false**):
  non-stream and stream chat completions both passed with a real inference credential.
- Live porting is dynamic: API base URL ephemeral (re-verified `http://127.0.0.1:46291/v1`), web
  console loopback-only (re-verified on `http://127.0.0.1:8081` because `matrix-comms-element` already
  occupied `0.0.0.0:8080` on the host — the committed `compose.web.yaml` default remains
  `127.0.0.1:8080` and only the gitignored `deploy/v2/.env` redirect URI and a throwaway compose
  override were pointed at `8081` for this run), IdP issuer `http://192.168.22.50:8090`.

## Migration / no-migration decision
- No migration. Browser-session CSRF needs no schema change (CSRF stored server-side with the session;
  cookie names are constants). `0016` remains head.

## Issues / Risks
- The dev IdP regenerates its RSA key on restart; the API caches JWKS (~300s). After restarting the IdP,
  restart the API (or wait out the cache) before browser login.
- **Chromium rAF stall**: without `--disable-software-rasterizer` in `frontend/playwright.config.ts`,
  Chromium's bundled software rasterizer never produces frames in this environment, stalling
  `requestAnimationFrame` and Playwright's "stable" actionability check (clicks hang). The flag is set
  in the committed config.
- **Worker scaling must not use `scripts/dev/v2 workers`**: that command re-reads only the base
  `compose.yaml`, dropping `AETHERGATE_OIDC_WEB_CALLBACK_PATH` (so web login regresses to JSON) and
  recreating the API container (changing its IP and leaving the web container's nginx proxy pointing at
  the stale IP → `502`). Scale workers directly with
  `docker compose up -d --scale worker=N --no-deps worker` instead; bring the web stack back with
  `scripts/dev/v2 web up`.
- The local Ollama model is fast (~1.4s for short prompts); to observe simultaneous `in-flight=2`, the
  six/two proof uses `max_tokens=2000` (~24s per request when serialized). The cap never exceeds 2
  regardless, and observing `in-flight=2` requires **two** worker replicas — a single worker processes
  one request at a time even when `endpoint.max_concurrency=2` (the physical concurrency bound is the
  min of `max_concurrency` and the number of workers).
- **Host port `8080` is not always free**: the unrelated `matrix-comms-element` container binds
  `0.0.0.0:8080`, which also claims loopback and prevents `compose.web.yaml` from binding
  `127.0.0.1:8080`. When that happens, point the web service at another loopback port (a throwaway
  compose override `ports: !override ["127.0.0.1:8081:80"]`) and update the gitignored
  `deploy/v2/.env` `AETHERGATE_OIDC_REDIRECT_URI` to match, then run Playwright with
  `WEB_BASE_URL=http://127.0.0.1:8081`.
- `ruff format` remains out of scope (pre-existing reformat of many files); `ruff check` is the gate.

## Key files
- Backend: `src/aethergate/config.py`, `src/aethergate/identity/session.py`,
  `src/aethergate/api/session_auth.py`, `src/aethergate/dev_oidc_idp.py`,
  `src/aethergate/dev_oidc_idp_server.py`, `tests/test_oidc_session.py`, `tests/test_settings.py`.
- Frontend: `frontend/src/lib/client.ts`, `frontend/src/context/*`, `frontend/src/pages/*`,
  `frontend/src/components/Sidebar.tsx`, `frontend/src/generated/*`, `frontend/playwright.config.ts`,
  `frontend/e2e/fixtures.ts`, `frontend/e2e/operator-rbac.spec.ts`.
- Deploy: `deploy/v2/compose.yaml`, `deploy/v2/compose.web.yaml`, `scripts/dev/v2`.
- Docs: `docs/architecture/security.md`, `docs/architecture/admin-api.md`, `docs/web-console.md`,
  `docs/development/README.md`, `frontend/README.md`.

## Recommended Next Step
Begin the next management UI phase (per `project_spec.md`), starting from the now-verified
web-console foundation and the green 503/27/11 regression baseline.
