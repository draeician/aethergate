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

Role-aware behavior:

- `system_admin`: endpoint occupied-vs-available slots, operational state, pause/drain/resume,
  quota/cooldown, project/endpoint filters, outcome-unknown reconcile (failed/cancelled only).
- `project_admin`: own-project queue read + cancellation of own eligible work; budget headroom; no
  deployment pause/drain/resume/reconcile.
- `project_viewer`: read-only.

## Dashboard data sources

| Surface | Endpoint | Notes |
| --- | --- | --- |
| Queue summary | `GET /admin/v1/queue/summary` | queued/in-flight/outcome-unknown, oldest wait |
| Endpoint slots | `GET /admin/v1/queue/endpoints` | `system_admin` only |
| Quota/cooldown | `GET /admin/v1/queue/quota-status` | `system_admin` only |
| Budget headroom | `GET /admin/v1/projects/{id}/budget-status` | project-scoped roles |

### Not-yet-instrumented metrics

The product spec also calls for queue/TTFT percentiles, upstream health, and retry rate. The backend
does not yet expose these authoritatively, so the dashboard renders them explicitly as
"Not yet instrumented" — it never fabricates zeros from unrelated fields. These are the recommended
backend observability follow-up.

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
