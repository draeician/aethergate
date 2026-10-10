# AetherGate v2

AetherGate v2 is a self-hosted, OpenAI-compatible inference gateway with a
durable scheduler, quota/budget accounting, enterprise identity, a versioned
`/admin/v1` management API, a Linux CLI, and a web operator console.

This repository is at **Alpha 1** (internal/self-hosted). See
`docs/releases/alpha1-readiness.md` for scope and gates, and
`docs/operations/alpha-runbook.md` for the operator runbook. The legacy v1
Python app under `app/` is retained for reference only and is not part of v2.

## Supported today (Alpha 1)

- `GET /v1/models`, `GET /v1/models/{model}`
- `POST /v1/chat/completions` (non-streaming and streaming)
- `/admin/v1` management API (identity/RBAC, catalog/routing, accounting, queue,
  observability, OIDC/session, CLI/device flow)
- Linux CLI (`aethergate`, thin client over `/admin/v1`)
- Web console (operator + management + accounting + observability)

Not yet: Responses API, embeddings, v1->v2 migration, production HA, external
metrics/alerting.

## Quick start (development)

Prerequisites: Docker + Docker Compose, Python 3.12, Node 22 (for the web image).

```bash
# 1. Configuration preflight (never prints secrets)
python -m aethergate.config_check

# 2. Build + start the core stack (postgres + api + worker) and migrate
scripts/dev/v2 build
scripts/dev/v2 up
scripts/dev/v2 migrate
scripts/dev/v2 wait

# 3. (Optional) web console + deterministic dev OIDC IdP
scripts/dev/v2 web up
```

The API binds an ephemeral loopback host port; `scripts/dev/v2 url` prints it.
`scripts/dev/v2 test` runs the canonical containerized backend suite.

For a real operator install (secrets, bootstrap, OIDC, TLS expectations,
backup/restore, upgrade), follow `docs/operations/alpha-runbook.md`.

## Repository layout

- `src/aethergate/` — v2 application (API, worker, scheduler, accounting,
  identity, catalog, observability, contracts, admin/CLI surfaces).
- `frontend/` — web console (React + TypeScript).
- `deploy/v2/` — Compose files and the container image definition.
- `scripts/dev/v2` — development helper.
- `scripts/ops/v2` — operator recovery tooling (backup/restore/diagnostics).
- `scripts/ci/` — offline CI/golden-path tooling.
- `docs/` — architecture, operations, releases, and development documentation.

## Documentation

- Operator runbook: `docs/operations/alpha-runbook.md`
- Alpha status: `docs/releases/alpha1-readiness.md`
- Architecture: `docs/architecture/`
- Development: `docs/development/README.md`
- Web console: `docs/web-console.md`
