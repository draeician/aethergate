# AetherGate Agent Handoff

## Current State
- Branch: v2
- AGV2-012 implementation commit: `feat(identity): add scoped inference API credentials`
  (`c5aa986`), pushed to `origin/v2`.
- Prior AGV2-011 commits (`df8cf7e`, `0a0864b`) remain; this handoff supersedes the AGV2-011 handoff.

## Task Completed
AGV2-012 — Identity phase 1: scoped inference API credentials and durable authorization context.

- The OpenAI data plane now authenticates AetherGate-issued Bearer API keys (`Authorization: Bearer
  agk_...`) instead of relying solely on the dev bypass, resolving to a typed `RequestContext`
  (project/principal/credential/audience/scopes) that is persisted on the queued request exactly as
  the scheduler/accounting stack expects.
- `ApiCredential` is a one-way-verifiable scoped credential: 256-bit CSPRNG raw key
  (`agk_<display>_<secret>`), SHA-256 verifier (`key_hash`) and non-secret display prefix
  (`key_prefix`) persisted; `secret_ref_id` removed from the credential contract (upstream provider
  secrets still use `SecretRef`). Raw key is never stored in PostgreSQL and never in read DTOs.
- `CredentialAudience` (`inference`|`admin`) and `CredentialScope` (`inference:invoke`) separate the
  inference surface from future admin auth. Wrong audience or missing `inference:invoke` is rejected;
  there is no shared master key.
- A single identity service (`src/aethergate/identity/service.py`) is the one auth/authorization
  source shared by the API (strict Bearer parsing -> `authenticate`) and the worker (pre-dispatch
  `authorize_for_dispatch`), so the two never diverge.
- Queued-work revocation: a credential revoked/expired/inactivated while its request is queued is
  revalidated immediately before dispatch; the request terminates with `authorization_failed` without
  contacting upstream and with no surviving quota/budget/endpoint reservation, snapshot, usage, or
  ledger entry.
- Admin-v1 DTO foundations (`ApiCredentialCreate`/`Read`/`CreateResult`/`RotateResult`/
  `RevokeRequest`/`RevokeResult`/`Update`) added; read DTOs never expose the raw key or hash. No HTTP
  admin CRUD router yet.
- Development bypass kept as a deliberate dev/test escape hatch only: rejected in `prod` mode,
  consulted only when no Authorization header is present, and never a fallback for an invalid
  supplied key. The seeded dev identity is a synthetic credential with `key_hash = NULL`, so it can
  never be presented as a real Bearer token.

No changes to legacy v1 `app/` or `frontend/src/`; dated audit unchanged.

## Migration
- Revision `0009` (`identity phase 1: scoped inference API credentials`). Live `0008 -> 0009`
  succeeded on the running dev DB (`alembic_version = 0009`); empty-DB -> latest and `0008 -> 0009`
  covered by `tests/test_migrations.py::test_migration_0008_to_0009` / `test_migration_from_empty_database`.
- Adds `api_credentials.key_prefix`, `key_hash` (unique `uq_api_credentials_key_hash`), `audience`
  (CHECK `ck_api_credentials_audience`), `scopes` (JSON), `expires_at`/`revoked_at`/`last_used_at`;
  drops `secret_ref_id`. No raw key is created/stored by the migration; existing dev rows are
  backfilled with the inference audience/scope but no recoverable key, so they remain
  non-authenticatable and are replaced by newly generated credentials. `0001`–`0008` untouched.

## API key / verifier design (no secret values)
- Raw key format `agk_<display>_<secret>`; `<display>` is 8 non-secret hex chars, `<secret>` is 32
  bytes of `secrets` CSPRNG entropy (>= 256 bits). Raw key returned exactly once at create/rotate.
- Verifier is `SHA-256(raw_key)` hex; lookup is exact by full-key hash (fixed-cost indexed). The
  display prefix is never the authentication selector. This is safe only for high-entropy generated
  tokens — not a password-hashing scheme, and must never be applied to human-chosen secrets.

## Automated tests
`scripts/dev/v2 test` -> **250 passed** (was 220; +29 identity tests +1 migration test +1 auth-canary
test). `ruff check src tests` clean; `git diff --check` clean; pre-commit secret scan clean.
Coverage: key generation entropy/format, SHA-256 verifier, raw-key-never-persisted, DTO-never-exposes-
secret, typed `RequestContext` resolution, missing/malformed/wrong-scheme/invalid key, inactive/
revoked/expired credential, inactive project/principal, principal-project mismatch, wrong audience,
missing scope, dev-bypass absent-header + invalid-key-never-falls-through, prod-forbids-bypass,
create-returns-raw-once, rotate-revokes-old, queued revocation blocks pre-dispatch with no surviving
reservations, attribution to `InferenceRequest`, auth-log canary, migration `0008 -> 0009` + empty-DB.

## Real nomnom verification (live, real Ollama, bypass disabled)
Host = nomnom (`192.168.22.50/24`); backend = ollama `qwen3.8-2b-distill:Q6_K`; litellm 1.104.0 has
no trusted token estimator (fail-closed), so the seeded token quota limit was disabled for the smoke
(known AGV2-008/010 limitation, unrelated to identity). Dynamic AetherGate port this session:
**38967** (re-run `scripts/dev/v2 url`).

- **A. Real API-key inference — PASS.** Official OpenAI SDK 2.54.0 non-stream and stream both
  succeeded with an AetherGate-issued key (bypass disabled). Persisted `inference_requests` carry the
  credential's project/principal/credential IDs (attribution verified by direct DB read).
- **B. Missing/invalid key — PASS.** Missing key and random invalid key both return 401
  (`not_authenticated`, `WWW-Authenticate: Bearer`); no upstream call, no enqueue.
- **D. Revocation before request — PASS (live).** Revoking the credential made the next SDK call
  return 401 immediately.
- **H. Rotation — PASS (live).** Rotating produced a new raw key; the old key returned 401, the new
  key succeeded, and the old credential metadata is retained as revoked (not deleted). `list` shows
  metadata only (prefix/audience/scopes/timestamps; never the raw key or hash).
- **C/E/F/G (expiry, queued revocation, inactive project/principal, audience/scope)** are covered
  deterministically by the DB-gated automated tests above (expired/inactive/revoked/wrong-audience/
  missing-scope authentication, and queued-revocation dispatch-blocking with no surviving
  reservations).

## Key files
- `src/aethergate/identity/{keys,service}.py` — key generation/hashing; auth/authorization service.
- `src/aethergate/domain/entities.py` — `ApiCredential` refinement + `RequestContext`.
- `src/aethergate/domain/enums.py` — `CredentialAudience`, `CredentialScope`.
- `src/aethergate/persistence/models.py` — new `ApiCredential` ORM shape; unique hash index + CHECK.
- `src/aethergate/persistence/repository.py` — identity repository ops + active-state setters.
- `src/aethergate/migrations/versions/0009_identity_credentials.py` — migration.
- `src/aethergate/api/deps.py` — real Bearer auth + dev bypass; `request_context` dependency.
- `src/aethergate/api/openai_chat.py`, `openai_errors.py` — typed context; `WWW-Authenticate` on 401.
- `src/aethergate/scheduler/service.py` — pre-dispatch `authorize_for_dispatch` revalidation.
- `src/aethergate/dev_identity.py` / `dev_credential.py` — synthetic dev identity / dev credential CLI.
- `src/aethergate/contracts/admin_v1.py` — credential lifecycle DTOs.
- `tests/test_identity.py`, `tests/test_openai_auth.py`, `tests/test_migrations.py` — new coverage.
- `docs/architecture/{security,scheduler}.md`, `docs/contracts/{domain-model,admin-v1-foundation}.md`,
  `docs/development/README.md`.

## Decisions
- One-way SHA-256 verifier for high-entropy generated keys (not a password scheme); exact hash lookup,
  constant-time semantics, display prefix never the selector.
- `key_hash` is nullable so the synthetic dev-bypass identity can exist without a verifiable key
  (PostgreSQL treats NULLs as distinct under the unique index).
- Shared `_ensure_inference_access` policy used by both `authenticate` and `authorize_for_dispatch`
  with indistinguishable failures (no project/principal/credential existence leaks).

## Deferred
- OIDC authorization-code flow, browser sessions/CSRF, OAuth device flow, full admin RBAC, admin HTTP
  CRUD routes, CLI, React UI, principal-level budgets, `/v1/responses`, embeddings, v1 SQLite
  migration.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token; `docker compose run` needs
  `--no-deps`. The dynamic API host port changes every `up`/`workers`; re-run `scripts/dev/v2 url`.
- litellm 1.104.0 exposes no trusted token estimator, so token-quota/budget routes fail closed
  (`quota_token_estimator_unavailable`); the live smoke used request-only quota.
- The dev DB volume retains a now-disabled token quota limit from the smoke; not committed.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content; gateway timings don't reflect upstream
  parallelism.

## Recommended Next Step
Expose the admin v1 credential lifecycle (create/read/list/rotate/revoke) over `/admin/v1` using the
identity service and the `ApiCredential*` DTO foundations added in AGV2-012, followed by the human
OIDC identity/admin phase.
