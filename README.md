# AetherGate

AetherGate is a self-hosted, OpenAI-compatible inference gateway with a durable
scheduler, quota/budget accounting, enterprise identity, a versioned `/admin/v1`
management API, a Linux CLI, and a web operator console.

This repository has **one supported AetherGate implementation**: the current
PostgreSQL/Alembic/scheduler runtime under `src/aethergate/`. The retired v1
SQLModel/SQLite application has been removed from the tree; there is no v1 data
import path. See `docs/migrations/v1-to-v2.md` for the clean-cutover decision.

This repository is at **Alpha 1** (internal/self-hosted). See
`docs/releases/alpha1-readiness.md` for scope and gates, and
`docs/operations/alpha-runbook.md` for the operator runbook.

## Supported today (Alpha 1)

- `GET /v1/models`, `GET /v1/models/{model}`
- `POST /v1/chat/completions` (non-streaming and streaming), text-only **and**
  inline image input (vision -> text) on image-capable aliases.
- `POST /v1/responses` (Responses text-generation subset: string/text input,
  `instructions`, `max_output_tokens`, `temperature`, `top_p`, non-streaming and
  streaming; durable queue + accounting), plus inline `input_text`+`input_image`
  vision input on image-capable aliases. Unsupported Responses semantics
  (tools/function/MCP/computer-use, image *output*, structured outputs,
  reasoning, `previous_response_id`/conversations, background, stored responses,
  remote/file image sources) are rejected with a structured `400
  unsupported_feature`; see `docs/contracts/responses.md` and
  `docs/contracts/vision.md`.
- `/admin/v1` management API (identity/RBAC, catalog/routing, accounting, queue,
  observability, OIDC/session, CLI/device flow)
- Linux CLI (`aethergate`, thin client over `/admin/v1`)
- Web console (operator + management + accounting + observability)

Vision input is inline-only (`data:` URLs, PNG/JPEG/WebP); AetherGate never
fetches a client-supplied image URL, never accepts `file_id`/file storage, and
never logs image bytes. See `docs/contracts/vision.md`.

Deferred: embeddings (`POST /v1/embeddings`); image *generation*; audio;
files API; video; tools/MCP/computer-use; object/blob storage. Also not yet:
production HA, external metrics/alerting.

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

- `src/aethergate/` — the current application (API, worker, scheduler,
  accounting, identity, catalog, observability, contracts, admin/CLI surfaces).
- `frontend/` — web console (React + TypeScript).
- `deploy/v2/` — the canonical Compose stack and container image definition
  (`deploy/v2/Dockerfile`, `deploy/v2/compose*.yaml`). There is no root
  Dockerfile or root Compose file.
- `scripts/dev/v2` — canonical development helper.
- `scripts/ops/v2` — canonical operator recovery tooling (backup/restore/
  diagnostics).
- `scripts/ci/` — offline CI/golden-path tooling (incl. the legacy-v1 guard).
- `docs/` — architecture, operations, releases, and development documentation.

The `v2` path component in `deploy/v2/`, `scripts/dev/v2`, and `scripts/ops/v2`
is historical naming; these are the canonical current paths (do not rename them
or add duplicate root Docker/Compose definitions).

## Documentation

- Operator runbook: `docs/operations/alpha-runbook.md`
- Alpha status: `docs/releases/alpha1-readiness.md`
- Architecture: `docs/architecture/`
- Development: `docs/development/README.md`
- Web console: `docs/web-console.md`
