# AetherGate v2 — Project Specification

Baseline assessment: `docs/audits/v2-architecture-audit-2026-10-03.md` (dated, authoritative for
the v1 state; this spec supersedes it for direction).

> Convention used below:
> - **Settled** — a product/security requirement that is not negotiable.
> - **Direction** — the current architectural approach, to be elaborated in `docs/architecture/`.
> - **Deferred** — intentionally left for later design work; do not assume a decision.

## Product purpose

AetherGate is a company-operated, self-hosted inference gateway that publishes an
OpenAI-compatible data API over authorized model aliases. Administrators configure providers,
provider accounts, endpoints/deployments, models, routing, users/projects, API keys, quotas,
budgets, and policies. Clients call the gateway's OpenAI-compatible endpoints.

The first enterprise release targets **one company per deployment with project boundaries**,
not a hosted multi-company SaaS tenancy model. (Settled)

## v2 goals

1. Production-quality, correct, self-hosted OpenAI-compatible gateway.
2. A durable, multi-worker-safe scheduler that queues eligible requests when provider/endpoint
   capacity is temporarily unavailable, instead of rejecting them.
3. Correct quota/budget accounting: decimal fixed-point, conditional reservations, append-only
   ledger, idempotent settlement.
4. Enterprise identity: OIDC-backed named identities, role/resource authorization, scoped
   expiring credentials, secure browser sessions.
5. One versioned management API (`/admin/v1`) shared by the web console and the Linux CLI.
6. A migration path from the v1 database that preserves account identity and usage attribution.
7. Observability that lets operators explain *why* a request is waiting and what limit is blocking.

## Non-goals (for the first enterprise release)

- No implicit multi-company SaaS tenancy. (Settled)
- No semantic caching, prompt rewriting, or automatic cross-provider fallback by default — these
  can change outputs or data destinations. (Settled)
- No promise of zero provider `429` responses when provider limits are undisclosed or other
  applications share the same provider account outside the gateway. (Settled)
- No custom queue-status events injected into a standard completion stream. Long-lived async jobs,
  if ever built, are a separately documented extension API. (Settled)

## Technology direction

- **Language / runtime:** Python 3.12+, FastAPI (async). Keep the existing codebase's language. (Direction)
- **Frontend:** React + TypeScript, reusing the current visual shell, reorganized into feature modules. (Direction)
- **Architecture:** A modular monolith with **separate API and execution-worker entrypoints** from one
  codebase. Not a fleet of microservices. (Settled)
- **Database:** PostgreSQL becomes the authoritative store for identities, configuration, durable
  queued work, quota reservations, accounting, and audit. (Direction)
- **Migrations:** Versioned migrations managed by a migration framework (Alembic is the natural fit),
  replacing `create_all()` and ad-hoc scripts. Migrations must be explicit and reversible where
  practical. (Direction)
- **Provider adapter:** LiteLLM retained as a pinned provider adapter behind an internal interface.
  AetherGate owns admission, retry decisions, routing authorization, and settlement. Hidden
  adapter/SDK retries must be disabled or routed back through admission. (Settled)
- **Queue primitives:** PostgreSQL row locking + `SKIP LOCKED` for queue-like workloads, with an
  explicit scheduler layer for ordering and quota reservation. No transaction stays open while
  waiting for inference. Caching/wake-up infrastructure is added only after measurement, and is
  never a second quota authority. (Direction)

## Major architectural domains

See `docs/architecture/` for the elaborated model. Domain modules, each owning its own
service/repository/model/tests:

| Domain | Responsibility |
|---|---|
| contracts | Versioned OpenAI + admin schemas, immutable DTOs, generated clients, fixtures |
| identity/auth | OIDC, principals, projects, roles, authorization, sessions |
| catalog/routing | Providers, accounts, endpoints, model aliases, capabilities, data-destination policy |
| scheduler/queueing | Queue lifecycle, ordering, quota admission, concurrency ownership, cancellation/recovery |
| provider adapters | Pinned LiteLLM adapter, transport controls, usage + limit-feedback mapping |
| accounting/audit | Budgets, price snapshots, reservations, ledger, reconciliation, admin events |
| admin API | `/admin/v1` typed contracts and thin routers |
| CLI | Thin Linux CLI over the same admin API |
| web console | Feature pages, generated-client integration, operator workflows |
| platform/migrations/observability/testing | Migration integration, deploy, observability, CI, failure testing |

## OpenAI compatibility requirement

- Pin an official schema/reference and verify with black-box tests using the official Python and
  JavaScript SDK clients. (Settled)
- Core v2 target: model listing/retrieval, Chat Completions, Responses, and embeddings. Image/audio
  are advertised only after their routes and adapters pass contract tests. (Direction)
- Preserve supported message variants (developer/system/user/assistant/tool), nullable content,
  content parts, tool-call IDs, tool argument deltas, structured outputs, generation limits, finish
  reasons, and usage. Unsupported features must be rejected explicitly, never silently dropped. (Settled)
- Return JSON objects, not JSON strings. Structured error objects with documented status codes and
  safe messages. Distinguish gateway vs upstream request IDs. (Settled)
- Never blindly forward client-supplied transport settings (upstream URLs, secrets, trusted headers). (Settled)
- Compatibility behavior must be **tested, not assumed**. (Settled)

## Scheduler / queueing requirement

- When capacity is temporarily unavailable, eligible requests queue rather than being rejected. (Settled)
- Lifecycle: `validated -> queued -> reserved -> dispatched -> streaming -> succeeded/failed/cancelled/expired`,
  plus `outcome_unknown` for ambiguous failures. (Direction)
- Reserve requests-per-window, token allowances, shared-account limits, physical concurrency, and
  project budgets **together**, in a consistent lock order. Use provider-specific token reservation
  rules. (Settled)
- Queue bounds: request count, payload bytes, per-principal share, max waiting time, max total lifetime.
  Reject work that cannot fit even an empty quota window. (Settled)
- Persist ownership and reservation IDs before dispatch; use leases and fencing. A lease expiring does
  not prove upstream inference stopped. (Settled)
- Honor provider feedback (`Retry-After`, reset windows); apply cooldown to the actual shared quota
  scope. Retry only eligible failures, bounded, with jitter. Never restart generation after content
  has been delivered. (Settled)
- Correctness must hold with multiple API and worker processes. (Settled)

## Security requirements

- OIDC-backed named identities, role/resource authorization, company MFA policy. (Settled)
- Server-managed browser sessions with Secure, HttpOnly cookies and CSRF protection. No long-lived
  master secret in JavaScript storage. (Settled)
- Bootstrap credential is one-use, explicitly configured, disabled after setup; refuse startup with
  absent/empty/placeholder credentials. (Settled)
- Scoped, expiring service-account authentication for automation; human Linux CLI login via an
  approved OAuth device flow. (Settled)
- Upstream credentials stored via a secrets service or envelope encryption with the decrypting key
  outside the database. Secrets returned only at explicit create/rotate boundaries. (Settled)
- Content logging off by default; logs/traces redacted with defined retention. Temporary queue
  payload storage is encrypted, access-controlled, and expiring. (Settled)
- Enforce approved egress destinations; block metadata destinations, DNS rebinding, and redirect
  escapes. Inference callers never choose arbitrary server-side destinations. (Settled)
- Security-sensitive shortcuts are prohibited. Provider limits must never be bypassed for throughput. (Settled)

## Administration requirements

- One versioned management API under `/admin/v1` with typed schemas, stable opaque resource IDs,
  PATCH semantics, optimistic versioning, pagination, filtering, and safe import/diff/apply. (Direction)
- Covers providers/accounts/secrets/endpoints/quota groups/model aliases/routes/users/service
  accounts/keys/budgets/queues/audit/configuration. (Settled)
- **CLI and web console must use the same admin API/service contracts.** Normal administrative tooling
  never edits the database directly. (Settled)
- Separate administrative vs inference audiences/permissions. (Settled)

## Web console requirement

- Landing view shows queued and in-flight requests, occupied/available slots, current blocking quota,
  oldest wait, queue and time-to-first-token percentiles, upstream health, retry rate, budget headroom. (Settled)
- Each request explains why it is waiting. Operators can safely drain/pause, cancel queued work, and
  inspect effective limits. (Settled)
- Generated client from the same API contract as the CLI; server pagination; centralized auth-expiry
  handling. (Direction)

## Linux CLI requirement

- Installable package, profiles, trusted CA configuration, machine-readable JSON, predictable nonzero
  failure codes, shell completion, protected token storage, secret input via prompt/stdin (never argv). (Settled)
- Thin client over the same `/admin/v1` API. Offline recovery tooling is separate and tightly controlled. (Settled)

## Database / migration direction

- PostgreSQL authoritative; SQLite/SQLModel is the v1 legacy state to be migrated from. (Direction)
- Versioned, tested migrations; explicit and reversible where practical. (Settled)
- v1 -> v2 migration runs against a copy of the real old schema, preserves account identity and
  historical attribution, and never infers account identity from URL alone. (Settled)

## Testing expectations

- No automated test suite exists today (see audit AG-030); it must be built. (Direction)
- Required suites: unit, provider-contract, API, migration, browser, concurrency, fault-injection,
  and official-SDK interoperability, wired into CI. (Direction)
- Key scenarios: six-request/two-slot dispatch; cross-worker limits; quota-authority failure/failover;
  ambiguous outcomes without duplicate execution or premature slot release; SDK tool loops, structured
  outputs, SSE usage; migration/restore; canary-secret leakage. (Settled)
- Tests run deterministically offline (no network, no real provider credentials). (Settled)
- Frontend: build (`npm run build`) and lint (`npm run lint`) exist today; browser tests are to be
  added. (Direction)

## Observability expectations

- Separate liveness from readiness; readiness reflects dependency health, not just process up. (Settled)
- Structured logs with redaction and retention policy; content logging off by default. (Settled)
- Metrics: queue wait, time-to-first-token, retry rate, budget headroom, upstream health. (Direction)
- Distinct gateway vs upstream request IDs. (Settled)

## Modularity requirements

- Each domain owns its service/repository/model/tests. API routers and CLI code contain no database
  policy. (Settled)
- Dependency direction enforced by architecture tests and CODEOWNERS. (Direction)
- Shared contracts, generated files, and the migration revision graph have a designated integrator. (Settled)

## Rules for parallel agent development

See `docs/development/agent-workstreams.md` for ownership boundaries.

- Contracts established first; a workstream must not silently change public contracts. (Settled)
- One owner per bounded domain; separate branches/worktrees to minimize file overlap. (Settled)
- Coordination is required before touching shared areas (contracts, generated clients, migration
  revision graph, shared god-files). (Settled)
- Agents add/update tests with implementation work. (Settled)
- No unrelated cleanup in scoped tasks. (Settled)

## Definition of done

A task is done only when all of the following hold:

- Acceptance criteria from the task file are met and checkable (commands pass deterministically).
- New/changed behavior is covered by tests; the project's standard test command passes.
- No secrets, credentials, provider keys, or prompt content are committed or logged.
- Public contracts and the migration graph are either untouched or changed through the integrator.
- `project_spec.md` and the relevant `docs/architecture/` document remain consistent with the change.
- `git diff` shows only files within the task scope.

## Status

- [x] Bootstrap complete — AetherGate v2 development foundation established.
