# AetherGate Agent Handoff

## Current State
- Branch: v2
- AGV2-012V implementation commit: `fix(identity): harden credential lifecycle and live authorization checks`
  (`fca2359`), pushed to `origin/v2`.
- Prior AGV2-012 commits remain (`c5aa986` implementation, `c852094` handoff); this handoff supersedes
  the AGV2-012 handoff.

## Task Completed
AGV2-012V — Complete inference-identity live verification and harden credential lifecycle invariants.

### Lifecycle hardening (behavioral changes)
- **Create validation.** `create_credential` now validates the target identity before generating any
  key: project must exist and be active, principal must exist and be active, and
  `principal.project_id` must equal the requested `project_id`. A failing creation raises
  `CredentialLifecycleError` (a new `DomainError` in `src/aethergate/errors.py`) and generates/returns
  no raw key and persists no credential row.
- **Rotation eligibility.** `rotate_credential` is now an active-credential operation only: the old
  credential must exist, be active, not revoked, and not already expired, and its project/principal
  must still be valid/active/matching. Any failure raises `CredentialLifecycleError` before a
  replacement is created, so a failed rotation never yields a new valid key.
- **Idempotent revocation.** `revoke_api_credential` stamps `revoked_at` only on the first revoke;
  repeated revokes preserve the original timestamp and only re-assert `is_active=False`. There is no
  reactivation path through revoke/rotate.
- **Audience/scope coherence.** Default scopes are audience-derived: `inference` -> `(inference:invoke,)`,
  `admin` -> `()`. An admin credential carrying `inference:invoke` is rejected. The `dev_credential`
  CLI `--scopes` default is now `None` (audience-derived).
- **`last_used_at`** remains intentionally unset: no unconditional credential-row write is performed
  per inference request (hot-row avoidance); deferred.

### Live nomnom verification (real Ollama, bypass disabled)
Host = nomnom; backend = ollama `qwen3.8-2b-distill:Q6_K`; litellm 1.104.0 has no trusted token
estimator (fail-closed), so the seeded token quota limit remains disabled for smoke (known
AGV2-008/010 limitation). Dynamic AetherGate port this session (final): **45137** (re-run
`scripts/dev/v2 url`). Live scenarios A–H ran against an earlier dynamic port (40991); the dev-bypass
regression (G) required a restart to toggle bypass.

- **A. Expired credential — PASS.** Expired key -> 401; zero `inference_requests` enqueued; no
  upstream call.
- **B. Revocation while queued — PASS (live multi-process).** Forced a durable queue via the request
  quota limit (temporarily `limit_units=1`): an "exhaust" request committed the single unit, and the
  target request enqueued and waited with `wait_reason=quota_window_exhausted`. Revoking the target
  credential while queued terminated it with `state=failed, error_code=authorization_failed`. Durable
  state for that request showed zero `execution_attempts`, zero endpoint `reservations`, zero
  `quota_reservations`, zero `budget_reservations`, no `price_snapshot`, zero `usage_records`, and
  zero `ledger_entries` (API + worker + real PostgreSQL as separate processes).
- **C. Inactive project — PASS.** Deactivating the project made the next valid-key request 401;
  reactivated for cleanup.
- **D. Inactive principal — PASS.** Deactivating the principal made the next request 401; reactivated.
- **E. Audience separation — PASS.** Admin-audience credential -> 401 on both `/v1/models` and
  `/v1/chat/completions`.
- **F. Missing inference scope — PASS.** Inference credential with empty scopes -> 401 on both
  endpoints.
- **G. Dev-bypass — PASS (containerized).** `bypass=true` + no Authorization -> 200 (seeded dev
  identity); `bypass=true` + invalid Authorization -> 401 (never falls through); `prod` + `bypass=true`
  -> startup config validation fails ("allow_inference_auth_bypass cannot be enabled in production
  mode").
- **H. Rotation lifecycle — PASS.** Rotating an active credential produced a new key (new succeeds,
  old -> 401). Rotating the now-revoked old credential raised `CredentialLifecycleError ... is revoked`
  and created no third credential. Two repeated revokes preserved the identical `revoked_at`.
- **I. Regression — PASS.** Real OpenAI SDK non-stream and stream both succeeded on a valid key;
  full scheduler/quota/accounting suite, six-request/two-slot, queued-authorization regression, and
  migration head checks all green in the full containerized suite below.

### Automated tests
`scripts/dev/v2 test` -> **268 passed** (was 250; +18). `ruff check src tests` clean;
`git diff --check` clean; staged-content secret scan clean (no raw keys/hashes/secrets committed).
New deterministic coverage: create-rejects missing/cross-project/inactive project & principal, no raw
key on rejection, rotate-rejects missing/revoked/inactive/expired/invalid project/invalid principal,
failed rotation creates no replacement, repeated revoke preserves timestamp, admin audience has no
inference scope, incompatible audience/scope rejected, inference default scope, auth-failure no-enqueue.

## Migration
- No new migration: lifecycle hardening is enforced in the service/repository layer only; `0001`–`0009`
  untouched. Live DB stays at `alembic_version = 0009`; empty-DB -> latest and `0008 -> 0009` remain
  green in the suite.

## API key / verifier design (no secret values)
- Raw key format `agk_<display>_<secret>`; `<display>` is 8 non-secret hex chars, `<secret>` is 32
  bytes of `secrets` CSPRNG entropy (>= 256 bits). Raw key returned exactly once at create/rotate.
- Verifier is `SHA-256(raw_key)` hex; lookup is exact by full-key hash (fixed-cost indexed). The
  display prefix is never the authentication selector. Safe only for high-entropy generated tokens —
  not a password-hashing scheme.

## Key files
- `src/aethergate/identity/service.py` — create/rotate lifecycle validation + shared auth/authorization.
- `src/aethergate/identity/keys.py` — key generation/hashing.
- `src/aethergate/errors.py` — `CredentialLifecycleError`.
- `src/aethergate/persistence/repository.py` — idempotent `revoke_api_credential`.
- `src/aethergate/dev_credential.py` — audience-derived `--scopes` default.
- `src/aethergate/api/deps.py` — real Bearer auth + dev bypass.
- `src/aethergate/scheduler/service.py` — pre-dispatch revalidation (queued revocation block).
- `tests/test_identity.py`, `tests/test_openai_auth.py` — lifecycle + auth no-enqueue coverage.
- `docs/architecture/security.md`, `docs/contracts/domain-model.md`,
  `docs/contracts/admin-v1-foundation.md`, `docs/development/README.md` — lifecycle invariants
  documented.

## Decisions
- Lifecycle invariants are enforced in the shared identity service (single source), not scattered in
  callers; a rejected create/rotate never generates or returns a raw key.
- `CredentialLifecycleError` (a `DomainError`) is distinct from `AuthenticationRequired` (presented-key
  failure) so create/rotate validation errors are explicit domain errors.
- Rotation eligibility reuses `_require_valid_identity`, keeping project/principal validity rules
  identical between create and rotate.
- `last_used_at` deferred rather than a throttled write — the simplest correct option with no hot-row
  risk.

## Deferred
- OIDC authorization-code flow, browser sessions/CSRF, OAuth device flow, full admin RBAC, admin HTTP
  CRUD routes, CLI, React UI, principal-level budgets, `/v1/responses`, embeddings, v1 SQLite
  migration, `last_used_at` population.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`. The dynamic API host port changes every `up`/`workers`; re-run `scripts/dev/v2 url`.
- litellm 1.104.0 exposes no trusted token estimator, so token-quota/budget routes fail closed
  (`quota_token_estimator_unavailable`); live smoke used request-only quota.
- The dev DB volume retains a now-disabled token quota limit from the smoke; not committed.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content; gateway timings don't reflect upstream
  parallelism.
- Live queued-revocation (B) temporarily set the dev request-quota limit to 1 and endpoint
  `max_concurrency` to 1, both restored afterward (request limit 30/60, max_concurrency 2).

## Recommended Next Step
Expose the admin v1 credential lifecycle (create/read/list/rotate/revoke) over `/admin/v1` using the
identity service and the `ApiCredential*` DTO foundations, followed by the human OIDC identity/admin
phase.
