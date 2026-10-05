# AetherGate Agent Handoff

## Current State
- Branch: v2
- AGV2-014V implementation commit: `fix(admin): close identity verification and RBAC race gaps`
  (this session), pushed to `origin/v2`.
- Prior commits remain: `312210d` (AGV2-014V queued), `d283fd2` (AGV2-014), `1ddfcbb`
  (AGV2-014 queued), `5a81425` (AGV2-013). This handoff supersedes the AGV2-014 handoff.

## Task Completed
AGV2-014V — Close admin CRUD verification gaps and harden bootstrap/RBAC races.

### Bootstrap secret strength guard (no migration)
- `src/aethergate/config.py` now rejects a configured `AETHERGATE_BOOTSTRAP_TOKEN` shorter than
  `MIN_BOOTSTRAP_TOKEN_LENGTH` (32 chars) in addition to the existing placeholder rejection.
  Empty/unset still disables bootstrap; the token remains a `SecretStr` and never appears in
  `repr`/validation/log output. This is a **floor against short/guessable secrets, not an entropy
  proof** — the dev helper (`agb_ + secrets.token_urlsafe(32)`, 47 chars) remains valid and operators
  must still supply high-entropy material.

### Concurrent duplicate role assignment is idempotent
- `src/aethergate/identity/admin.py::create_role_assignment` now inserts through
  `session.begin_nested()` (savepoint) and translates a duplicate-race `IntegrityError` back to the
  canonical winner via `find_active_equivalent_assignment`. Unrelated `IntegrityError`s (e.g. FK
  violations) are re-raised, not swallowed. Exactly one active row and one `role_assignment.created`
  audit event result under concurrency.

### Real nomnom verification (dynamic port 45435)
- **Bootstrap strength (live).** Short arbitrary token -> `ValidationError`; placeholder -> rejected;
  generated `agb_` token accepted; token hidden in `repr`; unset (env cleared) disables bootstrap.
- **Principal deactivation.** Created project/principal/project_admin credential; `whoami` `200`;
  `PATCH /principals/{id} {"is_active":false}`; next `whoami` with that credential `401` immediately
  (no restart).
- **Role revocation.** Created project-scoped project_admin credential; `GET
  /projects/{id}/credentials` `200`; `POST /role-assignments/{id}/revoke`; next
  `GET /projects/{id}/credentials` `404` (no roles -> non-enumerating); credential `is_active: true`;
  `whoami` still `200` with `roles: []`.
- **Real SDK inference** (`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`, Ollama
  `qwen3.8-2b-distill:Q6_K` at `http://192.168.22.50:11434`, alias `gpt-4`): official OpenAI Python
  SDK non-stream chat completion succeeded; SDK streaming chat completion succeeded; admin key `401`
  on `/v1/models` and `/v1/chat/completions`; inference key `401` on `/admin/v1/whoami`.
- **Attribution.** Both inference requests recorded `project_id`/`principal_id`/`api_credential_id`
  matching the inference credential, state `succeeded`.

## Migration
- No new migration. The bootstrap guard is runtime config validation; concurrent duplicate handling
  reuses the existing `uq_role_assignments_active_equivalent` partial unique index (migration 0010).
  `0001`–`0011` untouched; `0010 -> 0011` and empty-DB -> latest remain green.

## Automated tests
- `scripts/dev/v2 test` -> **315 passed** (was 306; +9). New coverage: bootstrap token strength guard
  (unset/short/placeholder/generated/hidden/whitespace), concurrent duplicate role assignment
  (one active row, one audit event, no IntegrityError), deterministic duplicate-insert recovery,
  unrelated IntegrityError not swallowed. Existing principal-deactivation and role-revocation tests
  remain green.
- `ruff check src tests` clean; `git diff --check` clean; staged-content secret scan clean.

## Key files
- `src/aethergate/config.py` — `MIN_BOOTSTRAP_TOKEN_LENGTH` + `_validate_bootstrap_token`.
- `src/aethergate/identity/admin.py` — savepoint + `IntegrityError` recovery in
  `create_role_assignment`.
- `tests/test_settings.py` — bootstrap strength guard tests.
- `tests/test_admin_auth.py` — concurrent/duplicate/not-swallowed role-assignment tests (bootstrap
  token now >= 32 chars).
- `tests/test_admin_crud.py` — bootstrap token updated to >= 32 chars.
- `docs/architecture/security.md`, `docs/architecture/admin-api.md`,
  `docs/development/README.md` — AGV2-014V documentation.

## Decisions
- Bootstrap strength guard is a config-time minimum length only; docs explicitly disclaim it as an
  entropy proof.
- Concurrency safety uses a savepoint + `IntegrityError` translation narrow around the
  active-equivalent uniqueness constraint; the recovery re-fetches the winner and re-raises when no
  equivalent exists (unrelated failure not swallowed).
- Live role-revocation protected action returns `404` (not `403`) because a credential with zero roles
  has no project visibility; this is the correct non-enumerating behavior, distinct from the
  in-scope-but-insufficient-permission `403`.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`. Dynamic host port changes every `up`; this session: **45435**.
- litellm 1.104.0 has no trusted token estimator, so token-quota routes fail closed
  (`quota_token_estimator_unavailable`). The inference smoke therefore seeded a **request-only** quota
  (no `--token-limit`) and a request-priced budget; token-priced quotas/budgets will need a token
  estimator before they are exercised live.
- The dev DB was reset for a clean live-verification slate; re-bootstrap and re-seed were performed
  this session.

## Recommended Next Step
Introduce human OIDC authorization-code flow plugged into the same `Principal`/`RoleAssignment`
model, then extend the remaining catalog/accounting/queue admin CRUD over `/admin/v1` reusing the
centralized authorization and pagination foundations.
