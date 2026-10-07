# AetherGate v2 — Developer Instructions

Container-first development on the nomnom test bed. The v2 stack lives in `deploy/v2/`
(Compose project `aethergate-v2`), and a single helper wraps the workflow.

## One-command workflow

```bash
scripts/dev/v2 <command>
```

| Command | What it does |
|---|---|
| `build` | Build the v2 container image (`aethergate-v2:local`). |
| `up` | Start PostgreSQL + the v2 API + a scheduler worker, then print the discovered base/health URLs. |
| `url` | Print the current `AETHERGATE_BASE_URL` / `AETHERGATE_HEALTH_URL`. |
| `wait [url]` | Block until readiness passes. |
| `migrate` | Run the Alembic migrations (`upgrade head`) inside the API container. |
| `test` | Run the full test suite inside the API container against a throwaway test DB. |
| `workers [N]` | Scale the scheduler worker service to N replicas (default 2). |
| `inspect` | Print a scheduler queue-state summary (state counts, endpoints, active reservations, quota windows, queued requests' limiting reasons). |
| `reconcile list` | List `outcome_unknown` requests (metadata only, never content). |
| `reconcile resolve <id> --disposition <state> --by <op>` | Explicitly reconcile an `outcome_unknown` request to `failed`/`cancelled`/`succeeded` and release its held reservation. || `down` | Stop the stack, keeping the PostgreSQL volume. |
| `reset` | Stop the stack and **delete** the PostgreSQL volume (destructive). |
| `web up` | Bring up the web console + deterministic dev OIDC IdP (writes OIDC env into `deploy/v2/.env`). |
| `web url` | Print the web URL (`http://127.0.0.1:8080`) and the IdP issuer. |
| `web down` | Stop the web console + IdP services (keeps api/worker/postgres). |

## Port and network rules

- The API listens on a **stable container-internal port** (8000) and is bound to the host
  **loopback only**. Docker assigns a free host port; there is no committed fixed port.
- After `up`, the helper prints the actual mapping, e.g.:

  ```text
  AETHERGATE_BASE_URL=http://127.0.0.1:<docker-assigned-port>/v1
  AETHERGATE_HEALTH_URL=http://127.0.0.1:<docker-assigned-port>/health/ready
  ```

- PostgreSQL is reachable **only on the Compose network**; it is never host-published.
- The Compose project is `aethergate-v2`; container names are `aethergate-v2-api`,
  `aethergate-v2-postgres`, and `aethergate-v2-worker-{1..N}` (distinct from the legacy v1 stack).
- The scheduler worker runs as a separate `worker` service (`python -m aethergate.worker`); it can be
  scaled independently with `scripts/dev/v2 workers N`.

## Configuration

- `AETHERGATE_ENV` selects the environment/mode (`dev`/`test`/`prod`).
- Database settings come from `DATABASE_URL`, or from the `POSTGRES_HOST`/`POSTGRES_PORT`/
  `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD` parts (assembled safely; secrets masked in
  repr/logs).
- `scripts/dev/v2 up` generates a random `POSTGRES_PASSWORD` into `deploy/v2/.env` on first run
  (gitignored, never committed). It also generates `AETHERGATE_QUEUE_KEY` (Fernet key for queued
  content encryption) and `AETHERGATE_BOOTSTRAP_TOKEN` (the one-use admin bootstrap secret) if
  absent. All are required and never committed/logged; the bootstrap token should be removed after
  the initial admin bootstrap.
- The bootstrap token has a minimum-length floor (32 characters) and rejects placeholders; empty/unset
  disables bootstrap. The floor is **not** an entropy proof — the dev helper generates strong random
  material (`agb_ + secrets.token_urlsafe(32)`), and operators must supply high-entropy secrets.

## Endpoints

- `GET /health/live` — process liveness.
- `GET /health/ready` — readiness; returns `503` with `{"status":"not_ready"}` when PostgreSQL is
  unavailable.
- `GET /v1/models` — list active public model aliases.
- `GET /v1/models/{model}` — retrieve a public model alias.
- `POST /v1/chat/completions` — Chat Completions (non-streaming + SSE `stream=true`).

Admin control-plane endpoints (AGV2-013/014):

- `POST /admin/v1/bootstrap` — one-use admin bootstrap (bearer = `AETHERGATE_BOOTSTRAP_TOKEN`);
  returns the initial `system_admin` credential's raw key once.
- `GET /admin/v1/whoami` — safe metadata for an authenticated admin key.
- Projects: `POST /admin/v1/projects`, `GET /admin/v1/projects` (paginated),
  `GET /admin/v1/projects/{id}`, `PATCH /admin/v1/projects/{id}`.
- Principals: `POST /admin/v1/projects/{project_id}/principals`,
  `GET /admin/v1/projects/{project_id}/principals` (paginated), `GET /admin/v1/principals/{id}`,
  `PATCH /admin/v1/principals/{id}`.
- Role assignments: `POST /admin/v1/role-assignments`, `GET /admin/v1/role-assignments` (paginated),
  `GET /admin/v1/role-assignments/{id}`, `POST /admin/v1/role-assignments/{id}/revoke`.
- Credentials: `POST /admin/v1/credentials`, `GET /admin/v1/projects/{project_id}/credentials`
  (paginated), `GET /admin/v1/credentials/{id}`, `POST /admin/v1/credentials/{id}/rotate` and `/revoke`.

Human OIDC/session endpoints (AGV2-015):

- `GET /admin/v1/auth/oidc/login` — start an OIDC Authorization Code + PKCE login (302 to the
  configured provider).
- `GET /admin/v1/auth/oidc/callback` — complete login; on success returns session metadata plus the
  one-time `csrf_token` and sets the session cookie.
- `GET /admin/v1/auth/session` — resolve the current browser session.
- `POST /admin/v1/auth/logout` — revoke the session and clear the cookie (CSRF-protected, idempotent).
- `POST /admin/v1/oidc/identities` — link an external identity (`issuer` + `subject`) to a principal.

CLI/device-flow endpoints (AGV2-019):

- `POST /admin/v1/auth/device/start` — start a gateway-mediated OAuth device transaction (no auth);
  returns `user_code`, `verification_uri`, `expires_in`, `interval`, and the raw provider
  `device_code` (sent back on each poll).
- `POST /admin/v1/auth/device/poll` — submit `{"device_code": ...}`; returns a typed status
  (`pending` | `slow_down` | `success` | `access_denied` | `expired_token`). `success` reveals the
  raw CLI session token once.
- `POST /admin/v1/auth/cli/logout` — revoke the current CLI session (idempotent).

`GET /admin/v1/whoami` reports `authentication_kind` (`service_credential` | `browser_session` |
`cli_session`), populating `api_credential_id` / `browser_session_id` / `cli_session_id` respectively.

Catalog/routing control-plane endpoints (AGV2-016), all deployment-scoped (`system_admin` +
`admin:catalog:*`; project roles denied):

- Providers: `POST /admin/v1/providers`, `GET /admin/v1/providers` (paginated),
  `GET /admin/v1/providers/{id}`, `PATCH /admin/v1/providers/{id}`.
- Secret refs (metadata only): `POST /admin/v1/secret-refs`, `GET /admin/v1/secret-refs`,
  `GET /admin/v1/secret-refs/{id}`.
- Provider accounts: `POST /admin/v1/provider-accounts`, `GET /admin/v1/provider-accounts`
  (filter `provider_id`), `GET /admin/v1/provider-accounts/{id}`, `PATCH /admin/v1/provider-accounts/{id}`.
- Endpoints: `POST /admin/v1/endpoints`, `GET /admin/v1/endpoints` (filter `provider_account_id`),
  `GET /admin/v1/endpoints/{id}`, `PATCH /admin/v1/endpoints/{id}`.
- Quota groups: `POST /admin/v1/quota-groups`, `GET /admin/v1/quota-groups` (filter
  `provider_account_id`), `GET /admin/v1/quota-groups/{id}`, `PATCH /admin/v1/quota-groups/{id}`.
- Quota limits: `POST /admin/v1/quota-limits`, `GET /admin/v1/quota-limits` (filter
  `quota_group_id`), `GET /admin/v1/quota-limits/{id}`, `PATCH /admin/v1/quota-limits/{id}`.
- Model aliases: `POST /admin/v1/model-aliases`, `GET /admin/v1/model-aliases`,
  `GET /admin/v1/model-aliases/{id}`, `PATCH /admin/v1/model-aliases/{id}`.
- Route bindings: `POST /admin/v1/route-bindings`, `GET /admin/v1/route-bindings` (filter
  `model_alias_id`/`provider_account_id`), `GET /admin/v1/route-bindings/{id}`,
  `PATCH /admin/v1/route-bindings/{id}`.

Catalog errors use stable codes: `409 resource_conflict` (unique name), `409 active_route_conflict`
(second active route), `400 parent_mismatch` (route/endpoint/quota-group account mismatch),
`400 destination_denied` (egress), `404 not_found`.

Accounting control-plane endpoints (AGV2-017) — route pricing is deployment-scoped (`system_admin` +
`admin:accounting:*`); project budget/usage/ledger/audit are project-scoped (`system_admin` any
project, `project_admin` read/write own, `project_viewer` read own):

- Price policies: `POST /admin/v1/price-policies`, `GET /admin/v1/price-policies` (filter
  `route_binding_id`/`enabled`/`billing_unit`), `GET /admin/v1/price-policies/{id}`,
  `PATCH /admin/v1/price-policies/{id}`.
- Price snapshots (immutable, read-only): `GET /admin/v1/price-snapshots`,
  `GET /admin/v1/price-snapshots/{id}`.
- Project budget policies: `POST /admin/v1/project-budget-policies`,
  `GET /admin/v1/project-budget-policies` (filter `project_id`/`enabled`/`currency`),
  `GET /admin/v1/project-budget-policies/{id}`, `PATCH /admin/v1/project-budget-policies/{id}`.
- Budget status/headroom: `GET /admin/v1/projects/{project_id}/budget-status`.
- Budget reservations (read-only): `GET /admin/v1/budget-reservations`,
  `GET /admin/v1/budget-reservations/{id}`.
- Usage records (read-only): `GET /admin/v1/usage-records`, `GET /admin/v1/usage-records/{id}`.
- Ledger entries (read-only): `GET /admin/v1/ledger-entries`, `GET /admin/v1/ledger-entries/{id}`.
- Audit events: `GET /admin/v1/audit-events`, `GET /admin/v1/audit-events/{id}` (`admin:audit:read`;
  project roles see only their own project's events).

Accounting errors use stable codes: `409 price_policy_conflict` (second enabled policy per route),
`400 invalid_request` (shape/currency/Decimal/time-range), `400 parent_mismatch`, `404 not_found`
(cross-project opaque IDs are non-enumerating), plus stable immutable-field validation errors for
budget `currency`/`window_seconds`. Money is fixed-point `Decimal` end to end; binary float is
rejected. `currency` and `window_seconds` are immutable after budget creation; `limit_amount`/`name`/
`enabled` are mutable for future admission. Manual ledger adjustment writes are deferred.

Queue/operator control-plane endpoints (AGV2-018) — queue reads/cancellation are project-scoped
(`system_admin` all, `project_admin`/`project_viewer` own project); endpoint runtime/pause/drain/
resume, quota-status, and outcome_unknown reconciliation are deployment-only (`system_admin` +
`admin:queue:*`):

- `GET /admin/v1/queue/requests` (paginated, filters), `GET /admin/v1/queue/requests/{request_id}`
  — safe metadata only (never content, encrypted bytes, stream events, or fencing tokens).
- `GET /admin/v1/queue/summary` — side-effect-free aggregate counts by state / wait reason.
- `GET /admin/v1/queue/endpoints`, `GET /admin/v1/queue/endpoints/{endpoint_id}` — runtime slot
  status (deployment-only).
- `POST /admin/v1/queue/endpoints/{endpoint_id}/pause` / `drain` / `resume` (deployment-only;
  idempotent, durable).
- `POST /admin/v1/queue/requests/{request_id}/cancel` — typed result (`cancelled_now` /
  `cancellation_requested` / `already_cancelled` / `terminal` / `outcome_unknown`).
- `GET /admin/v1/queue/outcome-unknown`, `POST /admin/v1/queue/requests/{request_id}/reconcile`
  (deployment-only; disposition `failed` | `cancelled`).
- `GET /admin/v1/queue/quota-status` (deployment-only; runtime quota metadata, never fabricates rows).

Queue errors use stable codes: `404 not_found` (cross-project non-enumeration), `403 forbidden`,
`400 invalid_request` (bad reconcile disposition), `409 invalid_lifecycle` (cancel a terminal request /
reconcile a non-`outcome_unknown` request).

Admin auth requires an `admin`-audience credential (`Authorization: Bearer agk_...`) plus an active
role assignment granting the needed `admin:*` permission (`system_admin` deployment-wide;
`project_admin`/`project_viewer` scoped to one project). Admin errors use
`{"error":{"code","message","request_id"}}`; auth/authorization/bootstrap failures are fixed and
indistinguishable. Opaque-ID read/mutate operations are non-enumerating across project boundaries: a
nonexistent ID and a cross-project ID both return `404 not_found`. Duplicate active-equivalent role
assignments are idempotent and concurrency-safe (a concurrent duplicate resolves to the same
canonical assignment with exactly one `role_assignment.created` audit event).

Inference is authenticated with an AetherGate-issued Bearer API key
(`Authorization: Bearer agk_...`). A missing/invalid/wrong-scheme/malformed header returns a
structured `401` with `WWW-Authenticate: Bearer`; a valid key resolves to the credential's
project/principal and persists that attribution on the queued request. Credentials are scoped to the
`inference` audience with the `inference:invoke` scope, and are revalidated again immediately before
worker dispatch so a credential revoked while queued never reaches upstream.

Inference is a **durable queue** path: the API enqueues an encrypted request and the worker claims
and dispatches it. Physical endpoint concurrency is enforced by `endpoint.max_concurrency`, and
shared provider-account request/token quotas are reserved transactionally with endpoint capacity
(migration `0005`). Accounting (migration `0007`) adds immutable price snapshots, usage records,
optional project budget policy, monetary budget reservations, and an append-only ledger, reserved in
the same atomic admission transaction (see `docs/architecture/accounting.md`). Queued work persists
in PostgreSQL and is recovered when workers restart. Inference is unauthenticated only under
`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=true` and only when no Authorization header is supplied
(development only, and rejected in `prod` mode); a supplied header is always authenticated normally
and an invalid key never falls through to the bypass.
Upstream hosts must be explicitly allowlisted via `AETHERGATE_UPSTREAM_ALLOWLIST` (comma-separated);
empty means deny all.

## Seeding a development backend

The catalog/routing control plane is now available through `/admin/v1` (AGV2-016), but `devseed`
remains the convenient idempotent bootstrap for local development. It seeds the minimum inference
configuration directly through the same service/repository validation layer (so it cannot create
route/account inconsistencies or multiple active routes):

```bash
docker compose --project-directory deploy/v2 --file deploy/v2/compose.yaml \
  run --rm --no-deps api python -m aethergate.devseed \
  --kind ollama \
  --upstream-model 'qwen3.8-2b-distill:Q6_K' \
  --alias gpt-4 \
  --base-destination http://<host>:11434 \
  --max-concurrency 2 \
  --quota-group shared \
  --request-limit 30/60 \
  --token-limit 60000/60 \
  --default-output-tokens 64
```

Accounting seed options (AGV2-010), all idempotent:

- `--price-currency USD` — currency for seeded price/budget (default `USD`).
- `--request-price AMOUNT` — seed a request-priced policy on the route.
- `--token-price INPUT/OUTPUT/UNIT_SCALE` — seed a token-priced policy (e.g. `1/1/1000`).
- `--budget-limit LIMIT/WINDOW_SECONDS` — seed a project budget policy on the dev project
  (e.g. `0.10/60`).

Values may also be provided via `AETHERGATE_SEED_PROVIDER_KIND`, `AETHERGATE_SEED_UPSTREAM_MODEL`,
`AETHERGATE_SEED_PUBLIC_ALIAS`, `AETHERGATE_SEED_BASE_DESTINATION`,
`AETHERGATE_SEED_SECRET_REF_NAME`, and `AETHERGATE_SEED_MAX_CONCURRENCY`. The destination host must
already be present in the upstream allowlist. `--max-concurrency` sets (or updates) the endpoint's
physical concurrency limit. `--quota-group` creates (or reuses) a shared quota group on the same
account; `--request-limit LIMIT/WINDOW_SECONDS` and `--token-limit LIMIT/WINDOW_SECONDS` are
repeatable and idempotent; `--default-output-tokens` sets the route's default bounded output
reservation.

## Issuing an inference API credential

With bypass disabled (`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`), real inference requires an
AetherGate-issued key. Use the dev-safe credential tool (service/repository-backed; not a raw DB
write):

```bash
docker compose --project-directory deploy/v2 --file deploy/v2/compose.yaml \
  run --rm --no-deps api python -m aethergate.dev_credential create \
  --project <project-id> --principal <principal-id> --name smoke
```

`list` shows metadata only (never raw keys or hashes); `revoke <id>` disables a credential
(idempotently — repeated revokes preserve the original timestamp); `rotate <id>` issues a replacement
and atomically revokes the old one, but only for an active, non-revoked, non-expired credential whose
project/principal are still valid. The raw key is printed exactly once at `create`/`rotate`. `create`
defaults scopes from the audience (`inference` -> `inference:invoke`; `admin` -> none); pass
`--scopes` to override.

## One-use admin bootstrap

Initialize the control plane exactly once with the configured bootstrap token:

```bash
TOKEN=$(grep '^AETHERGATE_BOOTSTRAP_TOKEN=' deploy/v2/.env | cut -d= -f2-)
curl -sS -X POST "$AETHERGATE_BASE_URL/../admin/v1/bootstrap" -H "Authorization: Bearer $TOKEN"
```

The response returns the initial `system_admin` credential's raw key once. Verify it:

```bash
curl -sS "$BASE/admin/v1/whoami" -H "Authorization: Bearer <admin-raw-key>"
```

After bootstrap the token can never be reused (further calls return `409`), and it should be removed
from the environment.

## OIDC human login (AGV2-015)

Human administrators authenticate with an OIDC Authorization Code + PKCE flow and a server-managed
browser session. Configure the provider in `deploy/v2/.env` (or the environment):

```
AETHERGATE_OIDC_ENABLED=true
AETHERGATE_OIDC_ISSUER=https://idp.example.com
AETHERGATE_OIDC_CLIENT_ID=<client-id>
AETHERGATE_OIDC_CLIENT_SECRET=<client-secret>   # optional (public clients omit it)
AETHERGATE_OIDC_REDIRECT_URI=https://<gateway>/admin/v1/auth/oidc/callback
AETHERGATE_OIDC_SCOPES=openid profile email      # openid always enforced
AETHERGATE_OIDC_PROVIDER_NAME=Example            # optional display name
AETHERGATE_OIDC_JIT_PROVISIONING=false           # unprivileged self-provisioning (optional)
AETHERGATE_OIDC_JIT_PROJECT_NAME=default
AETHERGATE_OIDC_SESSION_IDLE_SECONDS=3600
AETHERGATE_OIDC_SESSION_ABSOLUTE_SECONDS=43200
AETHERGATE_OIDC_LOGIN_TTL_SECONDS=600
```

The issuer must be `https://` in `prod`; `dev`/`test` allow HTTP for the local verification IdP.
`openid` is always included; scopes may be comma- or whitespace-separated. The session cookie is
`HttpOnly`, `SameSite=Lax`, `Secure` in `prod`, `Path=/admin`, and never carries the raw token in JSON
or the URL. Mutating cookie-authenticated admin requests require the `X-CSRF-Token` header returned
once at login; Bearer service-account requests and `GET`/`HEAD` do not.

A known identity maps to a linked, active principal. A `system_admin` links an identity by
`issuer`+`subject` via `POST /admin/v1/oidc/identities`; unknown identities are denied unless JIT is
enabled (which creates an unprivileged `user` principal with zero roles). No role is derived from IdP
claims.

### Local test IdP

For offline/controlled verification there is a deterministic local OIDC provider
(`src/aethergate/dev_oidc_idp.py`, `aethergate.dev_oidc_idp.DevOidcIdp`) that implements discovery,
JWKS (RS256), an auto-approving authorize endpoint, and a PKCE S256 token endpoint that returns a
signed ID token. It is **not** production identity infrastructure. Serve it locally (for example, as a
one-off container on the host network) and point `AETHERGATE_OIDC_ISSUER` at it. It generates its key
in memory, so restarting it changes the signing key (restart the API to clear its in-memory JWKS
cache).

## Linux CLI (AGV2-019)

The `aethergate` command is an installable Linux client that is a thin HTTP client over `/admin/v1`
(it never imports persistence/repository modules). See `docs/cli.md` for the full reference.

```bash
pip install -e .
aethergate --version          # works with no server/config
aethergate --help
aethergate completion bash    # completion without contacting the server
```

Profiles live under `~/.config/aethergate/config.toml` and contain **no tokens**. Human login uses
the gateway-mediated OAuth device flow (`aethergate auth login`); automation uses an admin service
credential (`aethergate auth set-token --stdin`). Persistent tokens are stored in the OS keyring
(never plaintext profile TOML); a missing/locked keyring fails safely with no plaintext fallback. The
`AETHERGATE_TOKEN` env var overrides storage for the current process only (CI).

Device-flow configuration reuses the browser OIDC issuer/discovery/JWKS with a public device client:

```
AETHERGATE_OIDC_DEVICE_CLIENT_ID=<public-device-client-id>
AETHERGATE_OIDC_DEVICE_SCOPES=openid profile    # openid always enforced
AETHERGATE_CLI_SESSION_TTL_SECONDS=43200        # 12h default
```

The device client is **public** (no client secret); the browser confidential-client secret is never
placed in the CLI. Device flow is enabled when OIDC is enabled **and** a public device client ID is
configured. If the provider advertises no `device_authorization_endpoint`, device login
returns `503 device_flow_unavailable`. The deterministic local IdP (`dev_oidc_idp.py`) also
implements device authorization plus explicit `approve`/`deny`/`expire` control endpoints for
offline verification.

## Web console (AGV2-020)

The web console is a same-origin React + TypeScript SPA under `frontend/` (see
`docs/web-console.md` for the full design). It authenticates through the existing OIDC Authorization
Code + PKCE flow, stores no credential/key/token in browser storage, and consumes `/admin/v1` only.

- Routes: `/login`, `/auth/callback`, `/` (dashboard), `/queue`, `/queue/:requestId`, and
  `/outcome-unknown` (system_admin). Dashboard data comes from `/admin/v1/queue/summary`,
  `/admin/v1/queue/endpoints`, `/admin/v1/queue/quota-status`, and
  `/admin/v1/projects/{id}/budget-status`; un-instrumented metrics (TTFT/upstream/retry) are marked
  unavailable, never faked.
- Web-console OIDC completion: set `AETHERGATE_OIDC_WEB_CALLBACK_PATH=/auth/callback` (with
  `AETHERGATE_OIDC_ENABLED=true`). A successful callback then sets the `HttpOnly` `ag_session`
  cookie plus a JS-readable `ag_csrf` cookie and `302`-redirects to `/auth/callback`; the raw CSRF
  token is never placed in the URL.
- Run it with `scripts/dev/v2 web up` (committed `deploy/v2/compose.web.yaml`), which serves the
  console on `http://127.0.0.1:8080` and the in-repo deterministic IdP
  (`python -m aethergate.dev_oidc_idp_server`) on `http://<host-lan-ip>:8090`. The IdP `ISSUER` must
  be reachable by both the gateway container and the browser; `scripts/dev/v2 web url` prints it.

## Tests
```bash
# host (offline; DB-gated tests skip)
.venv/bin/python -m pytest -q

# full suite including DB-gated tests, inside the container
scripts/dev/v2 test

# frontend (see docs/web-console.md)
cd frontend && npm run build && npm run lint && npm run test
```

DB-gated tests use `AETHERGATE_TEST_DATABASE_URL` and skip when it is unset; `scripts/dev/v2 test`
sets it automatically to a throwaway `aethergate_test` database.

## Migrations

The v2 schema baseline lives under `src/aethergate/migrations/` (revisions `0001`–`0009`; `0003`
adds scheduler tables, `endpoints.max_concurrency`, and a `BigInteger` fencing token; `0004` adds a
positive-concurrency CHECK on `endpoints` and reconciliation metadata on `inference_requests`;
`0005` adds shared provider-account request/token quotas — `quota_limits`, `quota_windows`,
`quota_reservations`, `quota_groups.provider_account_id`/`cooldown_until`,
`route_bindings.default_output_tokens`, `inference_requests.wait_reason`; `0006` hardens shared-quota
admission metadata; `0007` adds the accounting foundation — `price_policies`, `price_snapshots`,
`project_budget_policies`, `budget_windows`, `budget_reservations`, `usage_records`,
`ledger_entries`, `inference_requests.price_snapshot_id`; `0008` enforces accounting invariants — the
one-enabled-price-policy-per-route partial unique index, billing-unit price-shape CHECK constraints,
and a nullable `budget_reservations.price_snapshot_id` for the snapshot lifecycle; `0009` refines
`api_credentials` into one-way-verifiable scoped client credentials — `key_prefix`, `key_hash`
(unique), `audience`, `scopes`, `expires_at`/`revoked_at`/`last_used_at`, dropping `secret_ref_id`;
`0010` adds admin identity — `role_assignments` (with a partial-unique index preventing duplicate
active equivalent assignments), the singleton `bootstrap_state`, and `audit_events`; `0011` enforces
the role/scope coherence shape — `system_admin` must be deployment-scoped, project roles must be
project-scoped; `0012` adds human identity — `external_identities` (unique `issuer`+`subject`),
`browser_sessions` (one-way session/CSRF verifiers), and `oidc_login_states` (one-time PKCE
transactions); `0013` binds the OIDC login transaction to the initiating browser (one-way
`txn_cookie_hash`); `0014` adds the catalog one-active-route partial unique index
`uq_route_bindings_one_active_per_alias` (`model_alias_id WHERE is_active = true`); `0015` adds
`endpoints.operational_state` (`active` default, CHECK `active|paused|draining`) for the queue operator
control plane; `0016` adds the device-flow and CLI-session tables (`device_authorizations` with a
one-way `device_code_hash` verifier and one-time/expiry metadata, and `cli_sessions` with a one-way
`token_hash` verifier, expiry, and revocation).
Schema is applied
only via `scripts/dev/v2 migrate`; startup never calls `create_all()`.
