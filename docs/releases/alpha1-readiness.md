# AetherGate v2 — Alpha 1 Readiness

This document records what **Alpha 1** means for AetherGate v2, the exact gates
that were run, and known limitations. It is an internal/self-hosted alpha status
marker — not a production/beta claim.

## Versioning

The package version is `0.1.0` (see `pyproject.toml` / `src/aethergate/__init__.py`).
The repository has no release-tag/SemVer-bump policy that this task is required to
advance, so **no version bump is made to label Alpha 1**; the status is recorded
here instead. Migration head is **`0017`**.

The runtime/dev dependency graph is locked in the committed `constraints.txt`
(consumed by CI, the Docker image, and the canonical local install) so the
generated OpenAPI cannot drift with whatever version pip resolves on a given day.
See `docs/development/dependency-locking.md` and the "Dependency reproducibility"
section below.

## Definition of Alpha 1

AetherGate may be called Alpha 1 because all of the following hold:

- the clean-install path is documented and repeatable
  (`docs/operations/alpha-runbook.md`);
- a fresh database migrates to head (`0017`);
- a populated `0016` database upgrades through `0017` (rows preserved, no fake
  backfill, new observability defaults correct);
- PostgreSQL backup + restore preserves control-plane/accounting identity;
- API/worker/full-stack restart does not lose queued/config/accounting state;
- CI enforces backend/frontend/schema/secret/core offline browser gates;
- structured logs let operators diagnose request lifecycle without content or
  secrets;
- the official OpenAI Python SDK (non-stream + stream) works against a clean
  alpha deployment with inference-auth bypass `false`;
- no known red regression gate remains.

## Supported surfaces

- OpenAI: `GET /v1/models`, `GET /v1/models/{model}`, `POST /v1/chat/completions`
  (non-stream + stream).
- Admin API `/admin/v1`: identity/RBAC, catalog/routing, accounting, queue/
  operator, observability, OIDC/session, CLI/device flow.
- Linux CLI (thin `/admin/v1` client; OAuth device flow).
- Web console (operator + management + accounting + observability).

## Deferred surfaces (not Alpha 1)

Responses API, embeddings, v1->v2 data migration, production secret-backend
selection, production-grade HA/failover, external Prometheus/OpenTelemetry/TSDB
exporters, paging/alerting, billing/invoice/payment semantics, hosted SaaS
tenancy, and broad visual redesign.

## Exact test gates

CI (offline, deterministic; see `.github/workflows/`):

- backend: Python 3.12 + PostgreSQL 16, `ruff check src tests`, `git diff
  --check`, `pytest`, empty -> head migration, `0016 -> 0017` migration,
  `python -m aethergate.config_check`, deterministic OpenAPI generation;
- frontend: Node 22, `npm ci`, `npm run test`, `npm run lint`, `npm run build`,
  `npx tsc -b --noEmit`, OpenAPI/generated-client drift;
- alpha-golden-path: real API + worker + web + deterministic DevOidcIdp + fake
  Ollama upstream; OIDC login, catalog route, inference credential, official SDK
  non-stream + stream, dashboard load, backup, restore, post-restore SDK;
- schema-secret-gate: `git diff --check`, repository secret/canary scan, workflow
  YAML validation, OpenAPI/client drift.

Local/nomnom regression gates (with baselines):

- backend full suite: baseline **561** (canonical `scripts/dev/v2 test`, exit 0);
- frontend unit/component: baseline **87**;
- full Playwright: baseline **32** (3 skipped: the CI-only `alpha-golden.spec.ts`);
- `npm run build`, `npm run lint`, `npx tsc -b --noEmit`;
- OpenAPI/client drift (two consecutive generations produce zero diff); `ruff
  check src tests`; `git diff --check`;
- secret/token/content canary scan;
- migration empty -> `0017`; populated `0016` -> `0017`;
- backup/restore golden path; official SDK non-stream + stream, bypass `false`.

## Dependency reproducibility (AGV2-024V)

`pyproject.toml` keeps human-readable compatible ranges; the committed
`constraints.txt` pins the exact known-green resolution of every runtime + dev
transitive dependency. CI (`backend.yml`, `frontend.yml`, `schema-secret-gate.yml`,
`alpha-golden-path.yml`), the Docker image (`deploy/v2/Dockerfile`), and the local
canonical install all use `-c constraints.txt`, so the resolved graph — and thus
the generated OpenAPI — is identical everywhere. Refresh is explicit via
`scripts/ci/refresh_lock.sh` followed by OpenAPI/client regeneration and the full
gate run (`docs/development/dependency-locking.md`).

## AGV2-024V — remote CI status

The substantive code/test/workflow commit is
`b3c61d5e7409bbb0504b33c87a5361bf5105beed`; all four workflows `completed/success`:

| Workflow | Run ID | Conclusion |
|---|---|---|
| backend | 38092737427 | completed/success |
| frontend | 38092737557 | completed/success |
| schema-secret-gate | 38092737259 | completed/success |
| alpha-golden-path | 38092737337 | completed/success |

AGV2-024V final substantive `origin/v2` code/test/workflow SHA
`b3c61d5e7409bbb0504b33c87a5361bf5105beed`:

| Workflow | Run ID | Conclusion |
|---|---|---|
| backend | 38092737427 | completed/success |
| frontend | 38092737557 | completed/success |
| schema-secret-gate | 38092737259 | completed/success |
| alpha-golden-path | 38092737337 | completed/success |

Every later commit in the task is documentation-only and was verified green with
the same four workflows; the `origin/v2` tip at handoff is green for
backend/frontend/schema-secret-gate/alpha-golden-path. The gates are deterministic
and offline, so the result is stable across the docs-only commits.

## AGV2-024V — closing the first remote CI run

The first pushed Alpha 1 SHA (`fb00f875513b8bec90d5284facf122850768868e`) failed
all four remote workflows. Root causes and fixes:

- **backend** — CI never created the database named by
  `AETHERGATE_TEST_DATABASE_URL`, so DB-gated tests failed with
  `database "aethergate_test" does not exist`; and `test_prod_requires_queue_key`
  was satisfied by the workflow-level `AETHERGATE_QUEUE_KEY` (ambient-env
  contamination). Fixed by creating the test database via
  `tests.db_helpers.ensure_database` before pytest, and by isolating the config
  tests (`monkeypatch.delenv` + `_env_file=None` + a clean cwd). The production
  queue-key requirement is unchanged.
- **frontend + schema-secret-gate** — CI resolved a newer Pydantic than the
  environment that generated the committed OpenAPI, changing the Decimal schema
  string branch (dropping a Pydantic-generated `pattern`). Fixed by the committed
  dependency lock so generation is reproducible.
- **alpha-golden-path** — the secrets step imported `cryptography` before
  dependencies were installed (`ModuleNotFoundError`), and teardown then failed on
  missing `POSTGRES_PASSWORD` interpolation. Fixed by generating the Fernet key
  with the standard library (`base64.urlsafe_b64encode(os.urandom(32))`) and
  guarding teardown/log-dump on `deploy/v2/.env` existing. Two further latent
  ordering/networking defects the step-1 failure had masked were also fixed: the
  OIDC issuer is now the runner host IP (reachable by both browser and api
  container) instead of `127.0.0.1`, and the empty database is migrated **before**
  the readiness wait (readiness is `503 schema_not_current` until head).

## Known limitations

- Alpha targets one company per deployment with project boundaries; no hosted
  multi-tenant SaaS.
- Only the Ollama provider kind is wired to a real adapter; the adapter boundary
  is provider-neutral but additional providers are not yet exercised.
- `deploy/v2/compose.yaml` alone is a development/alpha compose file, not a
  production deployment (no TLS termination, no HA).
- The web console nginx DNS must be re-resolved (restart the `web` container)
  after recreating the API.
- The deterministic DevOidcIdp is verification tooling only.
- Percentile/observability metrics are PostgreSQL-computed over bounded windows;
  there is no external TSDB exporter or alerting.
- Queue encryption-key loss makes queued encrypted content unrecoverable; the key
  must be backed up separately from PostgreSQL.
