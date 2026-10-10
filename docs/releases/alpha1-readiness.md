# AetherGate v2 — Alpha 1 Readiness

This document records what **Alpha 1** means for AetherGate v2, the exact gates
that were run, and known limitations. It is an internal/self-hosted alpha status
marker — not a production/beta claim.

## Versioning

The package version is `0.1.0` (see `pyproject.toml` / `src/aethergate/__init__.py`).
The repository has no release-tag/SemVer-bump policy that this task is required to
advance, so **no version bump is made to label Alpha 1**; the status is recorded
here instead. Migration head is **`0017`**.

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

- backend full suite: baseline **527** (now 561 with table-driven additions);
- frontend unit/component: baseline **87**;
- full Playwright: baseline **32**;
- `npm run build`, `npm run lint`, `npx tsc -b --noEmit`;
- OpenAPI/client drift; `ruff check src tests`; `git diff --check`;
- secret/token/content canary scan;
- migration empty -> `0017`; populated `0016` -> `0017`;
- backup/restore golden path; official SDK non-stream + stream, bypass `false`.

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
