# AetherGate Agent Handoff

## Current State
- Branch: `v2`.
- AGV2-016 implementation commit: `3243efc` (`feat(admin): add catalog and routing control plane`),
  pushed to `origin/v2` this session. This handoff supersedes the AGV2-015C handoff.
- Migration head: `0014`.
- Full containerized suite: **386 passed** (was 364; +22: 21 new `tests/test_catalog_admin.py` +
  1 new migration test).

## Task Completed
AGV2-016 — Catalog and routing admin API with configuration invariants.

### Endpoints added (deployment-scoped `/admin/v1`)
- Providers: `POST /providers`, `GET /providers` (paginated), `GET /providers/{id}`,
  `PATCH /providers/{id}`.
- Secret refs (metadata only): `POST /secret-refs`, `GET /secret-refs`, `GET /secret-refs/{id}`.
- Provider accounts: `POST /provider-accounts`, `GET /provider-accounts` (filter `provider_id`),
  `GET /provider-accounts/{id}`, `PATCH /provider-accounts/{id}`.
- Endpoints: `POST /endpoints`, `GET /endpoints` (filter `provider_account_id`),
  `GET /endpoints/{id}`, `PATCH /endpoints/{id}`.
- Quota groups: `POST /quota-groups`, `GET /quota-groups` (filter `provider_account_id`),
  `GET /quota-groups/{id}`, `PATCH /quota-groups/{id}`.
- Quota limits: `POST /quota-limits`, `GET /quota-limits` (filter `quota_group_id`),
  `GET /quota-limits/{id}`, `PATCH /quota-limits/{id}`.
- Model aliases: `POST /model-aliases`, `GET /model-aliases`, `GET /model-aliases/{id}`,
  `PATCH /model-aliases/{id}`.
- Route bindings: `POST /route-bindings`, `GET /route-bindings` (filter `model_alias_id` /
  `provider_account_id`), `GET /route-bindings/{id}`, `PATCH /route-bindings/{id}`.

Routers are thin; all authorization/validation lives in `src/aethergate/catalog/admin.py`, which
accepts a typed `AdminRequestContext` and authorizes internally.

### Deployment-scope authorization
Catalog resources are deployment infrastructure, not project-owned. Every list/read/mutation
requires deployment-scoped authority (`system_admin`) **and** the matching `admin:catalog:read`/
`write` permission. A `project_admin`/`project_viewer` credential is denied (`403`) regardless of its
`admin:catalog:*` scopes. Bearer admin credentials and human OIDC browser sessions share the same
service layer; browser mutations remain CSRF-protected via the existing session machinery.

### Egress validation
Endpoint create and `base_destination` updates run the same `DestinationPolicy` used before dispatch:
non-allowlisted hosts, URL userinfo, and metadata/link-local/loopback/reserved destinations return
`400 destination_denied`; allowlisted private-LAN hosts are accepted. A destination dispatch would
reject is never persisted.

### One-active-route enforcement
Migration `0014` adds the partial unique index `uq_route_bindings_one_active_per_alias`
(`model_alias_id WHERE is_active = true`). Together with service-layer validation this guarantees
exactly one active `RouteBinding` per alias: a second active route (or activating an inactive
alternate while another is active) returns `409 active_route_conflict`; inactive alternates are
allowed; deactivate-then-activate swaps work; concurrent activations resolve to exactly one winner.
The migration fails with a clear diagnostic if an existing DB already has multiple active routes per
alias (no silent winner selection).

### Route/account/quota consistency
`route_binding.provider_account_id` must equal `endpoint.provider_account_id`, and any
`route_binding.quota_group_id` must belong to that same account. Violations return `400
parent_mismatch` and are never persisted. `devseed` was refactored only as necessary so it still
cannot create inconsistent routes or multiple active routes (no HTTP authentication required).

### PATCH omitted-vs-null semantics
Via Pydantic `model_fields_set`: `external_account_id`, `secret_ref_id`, `upstream_model`,
`quota_group_id`, `default_output_tokens` (and nullable `description`/`name`) distinguish omitted
(unchanged) from explicit `null` (clear where clearing is supported). Direct HTTP tests cover both.

### Secret-ref metadata decision
`SecretRef` exposes only `id`/`name`/`created_at`; no raw secret value is accepted or returned, and
no environment-variable value is exposed. The production secret backend remains deferred;
`EnvSecretResolver` stays a dev/test convenience. No delete/rotate in this task.

### Audit behavior
Immutable audit events on actual state changes only: `provider.created`/`updated`,
`secret_ref.created`, `provider_account.created`/`updated`, `endpoint.created`/`updated`,
`quota_group.created`/`updated`, `quota_limit.created`/`updated`, `model_alias.created`/`updated`,
`route_binding.created`/`updated`. Actor principal ID and safe metadata only; no raw secrets,
Authorization/session/CSRF/OIDC tokens; idempotent no-op PATCH emits no event.

### Error translation
Stable admin codes, never SQL text/constraint names/stack traces: `409 resource_conflict` (unique
name, including DB-constraint races), `409 active_route_conflict`, `400 parent_mismatch`,
`400 destination_denied`, `404 not_found`, `400 invalid_request` (validation). Conflict races are
translated (savepoint + `IntegrityError` recovery), not only pre-checked.

## Live nomnom verification (dynamic host port 44777)
Clean reset -> `scripts/dev/v2 migrate` (empty -> `0014`) -> one-use bootstrap -> catalog built
entirely through `/admin/v1` (no direct DB inserts). Backend Ollama `http://192.168.22.50:11434`,
upstream `qwen3.8-2b-distill:Q6_K`, alias `gpt-4`, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`.

- **A catalog via admin API.** Created provider/account/endpoint/quota-group/request-limit/alias/
  active route; every list/read DTO round-tripped; pagination bounded (`limit=1000` -> `400`).
- **B real SDK inference.** Official OpenAI Python SDK `/v1/models` listed `gpt-4`; non-stream and
  stream chat completions both succeeded; both `inference_requests` rows recorded `succeeded` with
  `project_id`/`principal_id`/`api_credential_id` matching the minted inference credential.
- **C active-state.** Deactivating alias (absent from `/v1/models`, inference fails safely),
  endpoint, provider account, and provider each failed inference without hidden fallback; reactivation
  restored inference.
- **D egress.** Non-allowlisted, URL-userinfo, metadata, and link-local destinations each returned
  `400 destination_denied`; the allowlisted Ollama host was accepted.
- **E route ambiguity.** Inactive second route allowed; activating it while the first is active
  returned `409 active_route_conflict`.
- **F route/account mismatch.** Route with endpoint-A + account-B, and quota-group-A on a route for
  account-B, both returned `400 parent_mismatch`; `route-bindings` total stayed 2 (no invalid row).
- **G quota edit live effect.** PATCH `limit_units=1/window_seconds=60`; request #1 succeeded;
  request #2 observed `wait_reason=quota_window_exhausted` in the DB then auto-dispatched on the
  fixed-window reset; `enabled` toggled false/true; limit restored. Historical reservation/window rows
  remained coherent.
- **H RBAC/CSRF (browser session) not re-run live.** The host-network IdP
  (`http://192.168.22.50:8490`) is currently unreachable; browser-session RBAC/CSRF is covered by
  `tests/test_oidc_session.py` (47 tests) and the prior AGV2-015 live proof. Catalog routers reuse the
  existing session/CSRF machinery unchanged.

## Migration
- `0014` (`src/aethergate/migrations/versions/0014_one_active_route_per_alias.py`): partial unique
  index `uq_route_bindings_one_active_per_alias` plus an explicit diagnostic pre-flight for
  pre-existing ambiguous active routes. `0001`–`0013` untouched. Live `0013 -> 0014` and empty-DB ->
  latest both succeeded.

## Automated tests
- `scripts/dev/v2 test` -> **386 passed**. New `tests/test_catalog_admin.py` (21 tests: deployment
  authorization, project_admin denial, provider CRUD/pagination/conflict, secret-ref metadata only,
  provider-account parent/omitted-vs-null, endpoint parent/egress/max-concurrency, quota-group CRUD,
  quota-limit validation/update/no-historical-mutation, model-alias CRUD/conflict/`/v1/models`
  reflection, route parent/mismatch/omitted-vs-null/one-active/inactive-alternate/concurrent-activation/
  409-translation, list filter bounds, no-op-audit, audit-no-secret-material). `tests/test_migrations.py`
  gained `0013 -> 0014`. `tests/test_catalog.py`/`tests/test_scheduler_quota.py` updated for the new
  typed errors (`ActiveRouteConflictError`, `CatalogParentMismatchError`).
- `ruff check src tests` clean; `git diff --check` clean; staged secret scan clean (one pre-commit
  hook false positive on the local `clear_secret` flag resolved by renaming it to `clear_secret_ref`).

## Key files
- `src/aethergate/catalog/admin.py` — catalog admin service (authz, invariants, egress, audit, conflict
  translation).
- `src/aethergate/api/catalog_admin.py` — thin `/admin/v1` catalog router (PATCH `model_fields_set`).
- `src/aethergate/api/admin_errors.py`, `src/aethergate/errors.py` — catalog error types + handlers.
- `src/aethergate/contracts/admin_v1.py` — catalog DTOs (create/read/update + `Page[T]`).
- `src/aethergate/persistence/models.py`, `src/aethergate/persistence/repository.py` — `RouteBinding`
  partial unique index + catalog repository functions.
- `src/aethergate/migrations/versions/0014_one_active_route_per_alias.py` — migration 0014.
- `src/aethergate/main.py` — router registration.
- `tests/test_catalog_admin.py` — new suite; `tests/test_migrations.py`, `tests/test_catalog.py`,
  `tests/test_scheduler_quota.py` — updated.

## Decisions
- Catalog resources are deployment-scoped; project roles never see them.
- SecretRef is metadata only; production secret backend still deferred.
- `provider.kind` stays an opaque non-empty string (not a restrictive enum) so LiteLLM-backed providers
  are not over-constrained.
- The one-active-route invariant is enforced by a DB partial unique index plus service validation; a
  cross-table trigger was deliberately avoided.
- `quota_limit.metric` and `quota_group_id` are immutable; only `limit_units`/`window_seconds`/`enabled`
  (and `name`) are editable, and edits never rewrite historical reservation/window rows.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`.
- The full suite has pre-existing flaky concurrency tests
  (`test_scheduler_quota.py::test_saturated_quota_group_does_not_block_unrelated_group` and
  `test_accounting.py::test_two_workers_cannot_oversubscribe_budget`) that fail intermittently only
  under full-suite load; they pass in isolation/targeted runs and are unrelated to AGV2-016.
- Live scenario H (browser-session RBAC/CSRF) was not re-run because the host-network IdP is
  unreachable this session.
- `deploy/v2/compose.yaml` still does not wire OIDC env passthrough; live verification used an
  uncommitted compose override to pin the host port (`44777`).
- litellm still has no trusted token estimator; token-priced quotas fail closed (request-only quota used
  in the smoke).

## Recommended Next Step
Queue the next workstream task (pricing/budget admin CRUD, usage/ledger admin reads, or the
queue/operator admin API) and, once the host IdP is reachable again, re-run live scenario H to confirm
browser-session catalog RBAC/CSRF end-to-end.
