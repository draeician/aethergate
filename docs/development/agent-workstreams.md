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
- **Existing god-files** `app/routers/admin.py`, `app/models.py`, `app/routers/proxy.py` — must be
  split before domains can own them independently (AG-034). Until then, changes to these files
  require coordination.
- **`docs/architecture/` and `project_spec.md`** — living documents; update only within your
  domain's section, and flag cross-domain implications.

## Dependency direction

Enforced by architecture tests and CODEOWNERS: routers/CLI -> services -> repositories -> models,
never upward, and never policy inside routers/CLI. (Direction)

## Existing diagnostic/support utilities (do not delete; review recommendation)

These untracked developer scripts currently sit at the repo root. Recommendation recorded here;
**no reorganization performed in this task.**

| File | Recommendation |
|---|---|
| `aethergate_client.py` | **Become a test/dev library.** Shared OpenAI SDK client helper; reuse as a fixture for SDK-interoperability tests. Eventually move under a `tools/` or `tests/` support module. |
| `chat_completion.py` | **Become a test / dev tool.** Exercises `POST /v1/chat/completions`; candidate for SDK contract tests and a dev smoke command. Move under `tools/`. |
| `diagnose.py` | **Remain a developer tool** (connectivity + key-validation smoke). Move under `tools/`; supersede the error-guessing heuristics with structured gateway error codes over time. |
| `inspect_routing.py` | **Superseded** by the future admin API `effective-limits`/routing inspection, but keep as a temporary dev tool until the console exposes it. Move under `tools/`. |
| `list_models.py` | **Become a test / dev tool** for `/v1/models`; candidate for contract fixtures. Move under `tools/`. |
| `ollama_direct.py` | **Remain a developer tool** for pre-gateway backend verification (historical/reference). Move under `tools/`. |
| `prompt1.md` | **Historical/reference material.** Phase-1 characterization prompt; archive, do not execute against v2. |

Related v1-era files that will be superseded (documented for completeness, not reorganized here):
`manage.py` (direct-DB CLI -> superseded by the admin-API CLI), `scripts/migrate_endpoints.py`,
`scripts/add_model_config_columns.py`, `scripts/add_rate_limit_column.py` (ad-hoc migrations ->
versioned migrations), `debug_inference.py` (upstream connectivity example -> test fixture).
