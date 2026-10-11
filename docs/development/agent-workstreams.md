# AetherGate v2 — Agent Workstreams and Module Ownership

Living document. Defines bounded ownership so multiple agents can work concurrently with minimal
file overlap. Derived from the audit's "Parallel-agent module ownership" table.

## Ground rules

- Contracts are established **first**, through the `contracts` workstream and the integrator.
- One owner per bounded domain. Each domain owns its service/repository/model/tests.
- API routers and CLI code contain no database policy.
- Use separate branches/worktrees; never edit another domain's files without coordination.
- Changes to shared areas require coordination (see below).

## Ownership boundaries

| Workstream | Scope | Primary files/dirs (v2 target) |
|---|---|---|
| contracts | Versioned OpenAI/admin schemas, immutable DTOs, generated clients, fixtures | shared schema/ + generated clients |
| identity/auth | OIDC/session/token verification, principals, projects, roles, authorization | identity domain |
| provider catalog and routing | Providers/accounts/endpoints/model aliases, capabilities, data-destination policy | catalog-routing domain |
| scheduler/queueing | Queue lifecycle, ordering, quota admission, concurrency ownership, cancellation/recovery | scheduler domain |
| provider adapters | Pinned LiteLLM adapter, transport controls, usage + limit-feedback mapping | providers domain |
| accounting/audit | Budgets, price snapshots, reservations, ledger, reconciliation, admin events, retention/redaction | accounting + audit domains |
| admin API | Thin `/admin/v1` routers over service contracts | interfaces domain |
| CLI | Thin Linux CLI over the same admin API | CLI domain |
| frontend/web console | Feature pages, generated-client integration, forms, operator workflows | web domain |
| platform/migrations/observability/testing | Migration integration, deploy, observability, CI, failure testing | platform-quality domain |

Mapping to the audit's original owners: `contracts` -> contracts; `identity` -> identity/auth;
`catalog-routing` -> provider catalog and routing; `scheduler` -> scheduler/queueing; `providers` ->
provider adapters; `accounting` + `audit` -> accounting/audit; `interfaces` -> admin API (+ CLI);
`web` -> frontend/web console; `platform-quality` -> platform/migrations/observability/testing.

## Shared areas requiring coordination before modification

- **OpenAI + admin contracts and generated clients** — owned by `contracts`; changing them affects
  every consumer. Change only through the integrator.
- **Migration revision graph** — single linear/merge history; coordinate with
  platform/migrations and the integrator before adding a revision.
- **Domain model entities referenced across domains** (request, attempt, reservation, ledger entry) —
  owned by contracts/data-model; coordinate before renaming or re-keying.
- **Shared domain modules** `src/aethergate/persistence/models.py`,
  `src/aethergate/persistence/repository.py`, and the `src/aethergate/api/*` routers — the v2
  equivalents of the retired v1 god-files; keep routers thin (no DB policy) and coordinate before
  splitting or re-keying shared entities.
- **`docs/architecture/` and `project_spec.md`** — living documents; update only within your
  domain's section, and flag cross-domain implications.

## Dependency direction

Enforced by architecture tests and CODEOWNERS: routers/CLI -> services -> repositories -> models,
never upward, and never policy inside routers/CLI. (Direction)

## Developer/support utilities

The retired v1 root helper scripts (`aethergate_client.py`, `chat_completion.py`,
`diagnose.py`, `inspect_routing.py`, `list_models.py`, `ollama_direct.py`,
`prompt1.md`) were removed with the v1 cutover. The supported equivalents now
live in the product itself:

| Need | Current supported path |
|---|---|
| List/verify models + Chat/Responses smoke | Official SDKs via `sdk/js/smoke.mjs`, `scripts/ci/golden_path.py`, `frontend/e2e/sdk_inference.py` |
| Connect an OpenAI SDK client | `docs/operations/alpha-runbook.md` (base URL + `agk_...` key) |
| Routing/quota inspection | `/admin/v1` (`queue/summary`, `effective-limits`) and the web console |
| Pre-gateway upstream check | Use the provider directly (e.g. Ollama `/api/chat`); the gateway revalidates egress |

Related v1-era files removed in the cutover (superseded by v2): `manage.py`
(direct-DB CLI -> the admin-API `aethergate` CLI), `scripts/migrate_endpoints.py`,
`scripts/add_model_config_columns.py`, `scripts/add_rate_limit_column.py`
(ad-hoc SQLite migrations -> versioned Alembic migrations under
`src/aethergate/migrations/`), and `debug_inference.py` (upstream connectivity
example -> test fixtures / fake Ollama).
