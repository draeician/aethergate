# AetherGate repository audit and v2 implementation blueprint

Reviewed: October 3, 2026  
Repository: draeician/aethergate  
Pinned source: `06518fad8a62016212218f5579b808f86c82045c`

## Verdict

A substantial architectural overhaul is warranted before the requested company deployment. Keep Python/FastAPI, React/TypeScript, the product concept and useful UI building blocks. Replace the execution core, authorization model, quota scheduler, accounting lifecycle and protocol boundary. This is a v2 rebuild of critical subsystems, not a justification for a new programming language or a fleet of microservices.

The present system is a prototype proxy with CRUD administration and post-request accounting. It does not implement the required capacity queue and does not reliably provide the OpenAI-compatible behavior expected by modern agent clients.

## Scope and limitations

This review used the live GitHub tree and pinned source for the backend, frontend application logic, admin API, CLI, deployment files and migration scripts, plus current primary-source documentation. Nine isolated source-pattern reproductions ran locally; they are not the repository's own integration tests. The probe environment is recorded in `probe_results.json`.

No deployed service was accessed or modified. The full application was not installed, the frontend was not built, the dependency lockfile was not vulnerability-scanned, and no production load test or penetration test was performed. The review does not establish which vulnerable defaults are active in the real deployment. Third-party provider internals and hidden limits cannot be certified by source inspection.

## The intended product

A company-operated gateway publishes authorized model aliases through an OpenAI-compatible data API. Administrators use a secure web console or Linux CLI, both backed by the same versioned management API. Provider accounts, physical endpoints, shared quotas and model deployments are configured separately. Valid requests wait within bounded, documented deadlines when provider capacity is temporarily unavailable.

### Recommended architecture

Use a modular monolith with separate API and execution-worker entrypoints from one codebase. PostgreSQL is the authoritative store for identities, configuration, durable queued work, quota reservations, accounting and audit. For the first enterprise release, keep admission and budget reservation in the same transactional authority instead of splitting correctness across SQL and Redis. Add caching/wake-up infrastructure only after measurement, without making it a second quota authority.

PostgreSQL supports row locking and SKIP LOCKED for queue-like workloads [S7]. That primitive is not a complete scheduler and does not establish FIFO by itself. The scheduler must explicitly serialize queue ordering and reserve all relevant quota/budget resources in a consistent lock order. No database transaction should remain open while waiting for inference.

Retain LiteLLM as a pinned provider adapter, behind an internal interface. AetherGate owns admission, retry decisions, routing authorization and settlement. Hidden adapter/SDK retries must be disabled or routed back through admission. Every network attempt is independently accounted for. LiteLLM's current max_parallel_requests behavior is per-process and rejects excess requests rather than providing the desired global durable queue [S4].

### Domain model

Separate organization/project, user/service-account principal, scoped API credential, provider, provider account, secret reference, endpoint/deployment, public model alias, route binding, shared quota group, policy, request, execution attempt, reservation, usage record, ledger entry and administrative audit event.

A provider account is not identified by its base URL. Two different accounts can use the same URL; multiple endpoint records and credentials can also share a provider organization/project quota. Public aliases must not mint independent provider capacity. Credentials must be rotatable without resetting quota history.

The first release should target one company per deployment with project boundaries, rather than implicitly promise a hosted multi-company SaaS tenancy model.

## Scheduler contract

For an example endpoint with two concurrent slots, six valid arrivals A-F should logically dispatch A/B first and queue C-F. Releasing one completed slot admits the next eligible queued request only when every other constraint also permits it. FIFO describes admission/dispatch order, not completion order.

Check and reserve configured requests-per-window, token allowances, shared-account limits, physical concurrency and project budgets together. Use provider-specific token reservation rules; do not assume every provider counts input/output or daily resets identically. Validate request size and context/output limits before admission. Recheck revocation and disabled routes immediately before dispatch.

Keep ingress abuse throttling separate from provider capacity queueing. Queue bounds must include request count, payload bytes, per-principal share, maximum waiting time and maximum total lifetime. Reject work that cannot fit even an empty quota window instead of leaving it permanently queued. FIFO can cause head-of-line blocking; a fairness/reordering mode should be explicit rather than silently changing the user's ordering expectation.

Recommended lifecycle:

`validated -> queued -> reserved -> dispatched -> streaming -> succeeded/failed/cancelled/expired`

Also represent `outcome_unknown` for ambiguous failures. Persist ownership and reservation identifiers before dispatch. Use leases and fencing to prevent multiple workers from owning the same attempt. A lease expiring does not prove upstream inference stopped. Do not automatically release capacity or replay an ambiguous attempt until upstream cancellation/completion, supported idempotency, or a conservative recovery policy resolves it.

Honor provider feedback, documented reset windows and Retry-After. Apply cooldown to the actual shared quota scope. Retry only eligible failures, with bounded attempts and jitter; failed upstream attempts can still consume quota [S1]. Never restart generation after content has already been delivered as though it were the original stream.

On quota-authority failure, stop new dispatch rather than reverting to local counters. A high-availability database deployment must preserve acknowledged reservation state across promotion; deployment durability and reconciliation are part of the guarantee.

The truthful guarantee is enforcement of known configured limits and provider feedback. A gateway cannot promise zero provider 429 responses when limits are undisclosed or other applications consume the same provider account outside its visibility. Dedicated provider projects/accounts and routing all relevant traffic through the gateway make enforcement more predictable.

## OpenAI compatibility contract

Use a pinned official schema/reference and black-box tests with official Python and JavaScript clients. Publish an endpoint/model capability matrix rather than claiming the entire OpenAI platform is implemented.

The core v2 target should include model listing/retrieval, Chat Completions, Responses and embeddings. Image/audio capabilities should be advertised only after their actual routes and adapters pass contract tests. OpenAI continues to support Chat Completions and recommends Responses for new integrations [S2].

Preserve supported developer/system/user/assistant/tool message variants, nullable content, content parts, tool-call IDs, tool argument deltas, structured-output controls, generation limits, finish reasons and usage. Validate unsupported features explicitly; do not silently drop them. Never blindly forward client-supplied transport settings such as upstream URLs, secrets or trusted headers.

Return JSON objects, not JSON strings. Provide structured error objects with documented status codes, parameters and safe messages. Distinguish gateway and upstream request IDs. Preserve rate/retry semantics without leaking upstream credentials or internal topology.

Chat streaming and Responses streaming have different contracts. Support final usage chunks, empty choices where appropriate and missing usage on interruption. Missing final usage is not proof of zero cost [S3]. Stateful Responses chains require ownership checks and routing affinity: previous_response_id cannot be indiscriminately sent to a different provider, and shared upstream credentials must not allow cross-project retrieval of stored objects.

A normal chat request should remain a normal chat HTTP request while it waits within the supported deadline. Do not return a custom 202/job body or insert invented queue events into a standard completion stream. Expose long-lived asynchronous jobs under a separately documented extension API. Delay success headers until a valid upstream response can be established when possible; once SSE headers are sent, HTTP status cannot be changed retroactively. Align proxy, client, queue and upstream deadlines and disable nginx buffering on streaming routes [S6].

## Security and administration

Use OIDC-backed named identities, role/resource authorization and company MFA policy. Keep browser sessions server-managed with Secure, HttpOnly cookies and CSRF protections. Do not put a long-lived master secret in JavaScript storage. A bootstrap credential should be one-use, explicitly configured and disabled after setup.

Provide scoped, expiring service-account authentication for automation and a human Linux CLI login using a supported OAuth device flow or equivalent approved flow [S8]. Normal CLI commands must use the same management API as the web console, never directly edit the database. Keep administrative and inference audiences/permissions separate.

Use TLS on management access, including LAN deployments, and support a private management listener or ingress. Store upstream credentials through a secrets service or envelope encryption with the decrypting key outside the database. Existing random client API keys can remain hash-verified; password hashing is not automatically required for high-entropy random tokens.

Turn content logging off by default; redact logs/traces and define retention. Temporary queue payload storage is separate from permanent prompt logging and still needs encryption, access control and expiry. Enforce approved egress destinations while explicitly supporting authorized private LAN endpoints. Protect against metadata destinations, DNS rebinding and redirect escapes; ordinary inference callers must never choose arbitrary server-side destinations.

Use /admin/v1 with typed schemas, stable resource IDs, PATCH semantics, optimistic versioning, pagination, filtering and safe import/diff/apply workflows. Cover providers/accounts/secrets/endpoints/quota groups/model aliases/routes/users/service accounts/keys/budgets/queues/audit/configuration. Return secrets only at explicit creation/rotation boundaries, not in normal reads or exports.

Linux CLI requirements: installable package, profiles, trusted CA configuration, machine-readable JSON, predictable nonzero failure codes, shell completion, protected token storage and secret input via prompt/stdin rather than command arguments. Offline recovery tooling must be separate and tightly controlled.

## Accounting and privacy

Separate access entitlement from a positive cash balance. Internal company users should be governed by project policies and optional budgets, not require artificial prepaid balances to use a zero-price local model. Keep resale billing as an isolated capability if required.

Use decimal/fixed-point values, immutable pricing snapshots, conditional reservations and an append-only ledger. Uniquely identify request, attempt and settlement. Provider incurred cost, estimated usage, measured usage and customer charge decisions are different records. Reconcile uncertain outcomes without silently treating them as free or exact.

Revocation and key-secret rotation must preserve identity and usage attribution. Preserve necessary financial/audit metadata while implementing a separate retention/redaction process for personal information and prompt content. Do not accidentally erase all spend history when deleting a key.

## Web console

Keep the existing visual shell but reorganize it into feature modules and reusable validated forms. Generate clients from the same API contract used by the CLI. Use server pagination and query cancellation, and handle authentication expiry centrally.

The landing view should show queued and in-flight requests, occupied/available slots, the current blocking quota, oldest wait, queue and time-to-first-token percentiles, upstream health, retry rate and budget headroom. Each request should explain why it is waiting. Provide endpoint test/discovery, safe drain/pause, queued cancellation, route policy previews and effective-limit inspection. Confirm destructive rotation and deletion operations and accurately describe what is retained.

Do not add semantic caching, prompt rewriting or automatic cross-provider fallbacks by default. They can change outputs or data destinations. Any such feature needs explicit policy, tenant isolation and tests.

## Parallel-agent module ownership

Use these bounded owners, with contracts established first:

| Owner | Scope |
|---|---|
| contracts | Versioned OpenAI/admin schemas, common immutable DTOs, generated clients, compatibility fixtures |
| identity | OIDC/session/token verification, principals, projects, roles and authorization |
| catalog-routing | Providers/accounts/endpoints/model aliases, capabilities, data-destination policy |
| scheduler | Queue lifecycle, ordering, quota admission, concurrency ownership, cancellation/recovery |
| providers | Pinned LiteLLM adapter, transport controls, usage and limit-feedback mapping |
| accounting | Budgets, price snapshots, reservations, ledger and reconciliation |
| audit | Administrative events, retention/redaction and searchable operational history |
| interfaces | Thin OpenAI/admin routers and CLI over service contracts |
| web | Feature pages, generated-client integration, forms and operator workflows |
| platform-quality | Database migration integration, deploy, observability, CI and failure testing |

Each domain owns its service/repository/model/tests. API routers and CLI code do not contain database policy. Enforce dependency direction with architecture tests and CODEOWNERS. Use separate branches/worktrees. Shared contracts, generated files and the migration revision graph need a designated integrator to prevent conflicting edits.

## Delivery sequence

1. Contain current risks: reject unsafe admin configuration and unknown/inactive routes; correct protocol serialization/parameter loss; stop SQL/prompt leakage; repair destructive audit behavior; pin dependencies and correct streaming proxy settings. This is containment, not enterprise readiness.
2. Establish v2 contracts and authoritative data model, migrations and identity. Build and test a repeatable migration from a copy of the old database; preserve account identity and historical attribution.
3. Implement scheduler, execution adapters, cancellation/recovery and ledger. Pass concurrency/fault tests before adding cosmetic features.
4. Move web and Linux CLI onto the same management API and expose queue/provider operations.
5. Run official-SDK contract tests, migration/restore rehearsals and production-like load/failure tests through the actual reverse proxy. Cut over by draining existing work. Shadow scheduling decisions, not duplicate billable inference traffic.

## Release acceptance gates

- Six-request/two-slot scenario, shared quotas across aliases/endpoints and multiple API/worker processes.
- No limit increase from key rotation, alias changes, process restart or worker scaling.
- Exact configured window semantics, token admission, nonpositive/invalid limits and requests larger than a full quota allowance.
- Queue saturation, FIFO definition, cancellation, expiration and bounded memory under slow consumers.
- Provider 429/Retry-After, unavailable service, hidden retry prevention and no replay after streamed content.
- Worker death before dispatch, during inference and after completion; ambiguous outcomes do not create duplicate execution or premature slot release.
- Quota-authority outage/failover and recovery without resetting known usage to zero.
- Concurrent budget reservations, idempotent settlement, price changes during execution and incomplete usage receipts.
- Tool loops, structured outputs, enabled content types, SSE usage, Responses affinity/ownership and official SDK interoperability.
- Project isolation, revocation, malformed auth, source URL controls and no canary-secret leakage.
- Fresh install, old-database migration, encrypted backup restore, rollback and meaningful readiness.
- Measured gateway overhead, queue wait and first-token latency. No performance claim or percentile target is established by this source-only review.

## Prioritized implementation backlog

P0 means a blocker for the requested enterprise release, not a claim that every item is remotely exploitable. P1 is required production hardening/feature work. P2 is maintainability/usability. Each entry includes evidence, owner and a verifiable acceptance criterion. Machine-readable equivalents are in findings.json.

### AG-001 [P0] Fail-open administrative bootstrap

**Owner:** identity  
**Source:** [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py), [docker-compose.yml](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/docker-compose.yml)

**Evidence:** verify_master_key falls back to a publicly known value when MASTER_API_KEY is absent. Compose also does not require a nonempty ADMIN_KEY.

**Change:** Refuse startup with absent, empty, or placeholder bootstrap credentials. Replace routine master-key authentication with identifiable principals and scoped permissions.

**Acceptance:** Missing, empty, and placeholder credentials prevent management startup; no shared default grants access.

### AG-002 [P0] Inactive resources remain callable

**Owner:** routing  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py)

**Evidence:** The chat route queries a model and endpoint without enforcing their is_active fields.

**Change:** Centralize entitlement and routability checks; repeat safety-critical checks at dispatch.

**Acceptance:** Disabled model or endpoint never receives new inference; queued work follows an explicit disable policy.

### AG-003 [P0] Unknown or unlinked models fall back to an implicit backend

**Owner:** routing  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py), [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py), [app/services/billing.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/services/billing.py)

**Evidence:** Unknown model IDs become Ollama targets. Deleting an endpoint unlinks models, which then use the environment/default backend. Missing prices settle as zero.

**Change:** Deny unknown models, require published route bindings, reject deletion while referenced or explicitly disable dependent routes. No implicit data destination.

**Acceptance:** Unknown, deleted, or unbound aliases cannot invoke an upstream or receive accidental free routing.

### AG-004 [P0] No capacity queue or concurrency admission

**Owner:** scheduler  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py), [app/services/limiter.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/services/limiter.py)

**Evidence:** Excess requests are rejected with 429. The implementation has no queue, in-flight semaphore, cancellation state machine, or recovery protocol.

**Change:** Implement bounded durable queues and provider-aware, transactional admission with leased execution ownership.

**Acceptance:** Six requests against a two-slot endpoint start only two; remaining valid requests dispatch in defined order as capacity and other quotas permit.

### AG-005 [P0] Worker-local counters multiply shared limits

**Owner:** scheduler  
**Source:** [app/services/limiter.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/services/limiter.py)

**Evidence:** Every process has its own dictionary; restarting loses state. The implementation is fixed-window despite the module title saying sliding-window.

**Change:** Use one durable quota authority across workers, aliases and shared provider accounts. Match each configured provider window and burst rule.

**Acceptance:** Adding workers or restarting processes does not increase provider allowance. Boundary tests cover rolling and fixed windows.

### AG-006 [P0] Quota model omits important constraints

**Owner:** scheduler  
**Source:** [app/models.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/models.py), [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py)

**Evidence:** Only key request windows and model/endpoint RPM/day limits are represented. No token, concurrent, shared-account, or context/output constraints.

**Change:** Add quota groups, token reservation models, concurrency resources, calendar resets and a provider capability/limit profile.

**Acceptance:** All applicable constraints must allow dispatch, including when two endpoint records share one account quota.

### AG-007 [P1] Partial quota spending before admission

**Owner:** scheduler  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py)

**Evidence:** The code consumes key/model/endpoint buckets sequentially, before the balance check; a later rejection leaves earlier buckets spent.

**Change:** Separate ingress abuse throttling from provider quota accounting; acquire provider allowances together at dispatch.

**Acceptance:** Locally rejected work consumes no upstream quota reservation, while ingress abuse controls still function.

### AG-008 [P1] Invalid limits and unbounded counter storage

**Owner:** scheduler  
**Source:** [app/services/limiter.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/services/limiter.py), [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py)

**Evidence:** Malformed key limits fail on use; nonpositive counts admit the first request. Idle dictionary entries have no cleanup. Retry-After truncates fractional reset time.

**Change:** Validate limit schemas on write, distinguish disabled/inherit/unlimited, bound retained state, and round reset delays conservatively.

**Acceptance:** Bad specifications are rejected by the admin API; disabled limits never admit a request; no zero-delay retry loop.

### AG-009 [P0] Lossy request schema breaks compatible clients

**Owner:** protocol  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py)

**Evidence:** Message.content is always a string; valid tool-call/null/content-parts shapes are rejected or stripped. Extra request fields such as tools and response_format are dropped.

**Change:** Implement a versioned OpenAI compatibility contract with explicit capability validation and preservation of supported protocol fields.

**Acceptance:** Official Python/JavaScript SDK tool loops, structured output and enabled multimodal requests pass contract tests.

### AG-010 [P0] Accepted generation controls are ignored

**Owner:** protocol  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py)

**Evidence:** temperature and max_tokens are parsed but not forwarded to either acompletion call.

**Change:** Forward validated model-supported controls and enforce a bounded output allowance used by quota and budget admission.

**Acceptance:** A mock upstream observes supplied controls; unsupported controls produce clear parameter errors, not silent omission.

### AG-011 [P0] Nonstreaming response serialization is unsafe

**Owner:** protocol  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py)

**Evidence:** The route returns response.json(). Returning serialized JSON as a FastAPI string double-encodes the response in the isolated reproduction; the repository does not pin LiteLLM.

**Change:** Return a validated object or an explicit JSON response. Verify with the pinned adapter implementation.

**Acceptance:** HTTP response JSON decodes once to the expected object, not a string, under the supported SDK/version matrix.

### AG-012 [P1] Errors lose status and may expose internals

**Owner:** protocol  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py), [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py)

**Evidence:** Upstream exceptions generally become HTTP 500 with raw exception text. Streaming errors use an ad hoc error string after HTTP 200 has started.

**Change:** Create a protocol error mapper; preserve appropriate status/retry semantics, redact internals, and distinguish pre-header from mid-stream failure.

**Acceptance:** Provider 429, invalid requests, auth failures, timeouts and interrupted streams have tested documented behavior without secret leakage.

### AG-013 [P1] Streaming lacks explicit lifecycle and backpressure

**Owner:** execution  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py), [frontend/nginx.conf](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/nginx.conf)

**Evidence:** The generator accumulates all visible text, has no explicit execution/cancellation ownership contract, and nginx does not disable response buffering for SSE.

**Change:** Use bounded streaming buffers, cancellation propagation, unbuffered proxy settings and separate queue/first-byte/idle/overall deadlines.

**Acceptance:** Slow consumers and disconnects do not create unbounded memory or orphan slot releases; tokens arrive incrementally through the real proxy.

### AG-014 [P0] Race-prone balance writes and no reservations

**Owner:** accounting  
**Source:** [app/services/billing.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/services/billing.py), [app/models.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/models.py), [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py)

**Evidence:** Balance > 0 permits inference; later unlocked read/modify/write deducts a floating-point amount. Concurrent operations can lose debits or overspend.

**Change:** Use decimal/fixed-point amounts, conditional reservations, immutable ledger entries and unique request/attempt settlement keys.

**Acceptance:** Concurrent reservations cannot exceed a hard budget; retries of settlement do not duplicate charges; precision is deterministic.

### AG-015 [P0] Settlement is nondurable and reprices requests

**Owner:** accounting  
**Source:** [app/services/billing.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/services/billing.py), [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py)

**Evidence:** BackgroundTasks and detached asyncio.create_task perform billing. Price is looked up after inference and disappears to zero if the model is removed.

**Change:** Persist the execution/settlement intent and price version before work; settle durably and idempotently; reconcile uncertain outcomes.

**Acceptance:** Process death or price edits during inference do not silently lose or alter an accepted charge.

### AG-016 [P1] Usage is inferred from visible text

**Owner:** accounting  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py)

**Evidence:** The code recounts content, sometimes with length/4, rather than using authoritative upstream usage; tool/reasoning/cached usage is not correctly represented.

**Change:** Capture provider usage details, preserve estimated versus measured status, and separate provider cost from customer charge policy.

**Acceptance:** Tool-only responses work; interrupted streams do not become zero-cost by assumption; usage categories and estimates remain auditable.

### AG-017 [P0] Provider credentials are stored and exported in plaintext

**Owner:** secrets  
**Source:** [app/models.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/models.py), [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py)

**Evidence:** LLMEndpoint.api_key is a plaintext database field and backup/export includes it.

**Change:** Use secret references or envelope encryption with keys outside the database. Default config exports omit secrets; sensitive backups require a protected explicit path.

**Acceptance:** Database/config dumps and normal administrative reads do not reveal recoverable provider credentials without the separate key authority.

### AG-018 [P1] Sensitive content logging is enabled by default

**Owner:** privacy  
**Source:** [app/models.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/models.py), [app/database.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/database.py)

**Evidence:** APIKey.log_content defaults to true and SQLAlchemy echo=True can expose SQL bind values such as prompt content and credentials in operational logs.

**Change:** Disable content capture and SQL parameter logging by default; use explicit policy, retention, redaction and protected temporary queue payload storage.

**Acceptance:** Canary secrets and prompts do not appear in normal logs, traces, metrics, backups or build output.

### AG-019 [P0] No enterprise principal or project authorization

**Owner:** identity  
**Source:** [app/auth.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/auth.py), [app/models.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/models.py), [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py), [frontend/src/context/AuthContext.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/context/AuthContext.tsx)

**Evidence:** Administration uses a single shared secret in browser sessionStorage. Users have a free-text organization field; keys lack expiry and enforced model/capability scopes.

**Change:** Add OIDC identities, project/service-account principals, scoped expiring credentials, role enforcement and secure browser sessions.

**Acceptance:** Cross-project resource access is denied consistently; revocation applies to queued work; administrative actions identify the actor.

### AG-020 [P1] Authorization parsing does not verify the Bearer scheme

**Owner:** identity  
**Source:** [app/auth.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/auth.py)

**Evidence:** The code uses Authorization.split(" ")[1] without validating the scheme.

**Change:** Use a strict, standards-aware bearer parser with controlled error responses.

**Acceptance:** Malformed and wrong-scheme headers fail predictably; valid bearer forms work.

### AG-021 [P0] Deletion erases accounting evidence; rotation orphans logs

**Owner:** audit  
**Source:** [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py), [frontend/src/pages/UsersPage.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/pages/UsersPage.tsx)

**Evidence:** Deleting a user/key deletes RequestLog rows. Rotation replaces the key ID and deletes the old key, explicitly leaving dangling log references. The UI incorrectly promises preserved logs.

**Change:** Use revocation/tombstones or key-secret versions with stable identity, preserved ledger references, and a separate privacy retention process.

**Acceptance:** Rotation/revocation preserves usage attribution with real FK enforcement; UI accurately reports destructive effects.

### AG-022 [P1] Inconsistent validation and update semantics

**Owner:** admin-api  
**Source:** [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py), [frontend/src/lib/api.ts](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/lib/api.ts)

**Evidence:** UUID strings are converted inside handlers; numeric limits/prices have few bounds. Null commonly means no-op, while zero is a magic clearing sentinel.

**Change:** Use typed path/query/body schemas, explicit PATCH semantics, constraints, optimistic resource versions and consistent error contracts.

**Acceptance:** Invalid IDs/values yield 4xx; omitted versus null fields behave predictably; conflicting edits are detected.

### AG-023 [P1] Model public identifiers do not fit CRUD paths

**Owner:** admin-api  
**Source:** [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py), [frontend/src/lib/api.ts](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/lib/api.ts)

**Evidence:** Normal {model_id} path segments cannot handle public IDs containing slashes even when encodeURIComponent is used; isolated test returns 404.

**Change:** Use stable opaque resource IDs in administrative paths and preserve public model names as data.

**Acceptance:** Create/edit/delete works for aliases containing every permitted character without changing their spelling or case.

### AG-024 [P1] Unbounded lists and weak import safety

**Owner:** admin-api  
**Source:** [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py)

**Evidence:** Several list routes return all rows, stats repeatedly aggregate history, and imports accept largely untyped dictionaries that overwrite identity/balance/credential state.

**Change:** Add server pagination/filtering, aggregate usage views, typed versioned import schemas and preview/diff/authorization/audit of bulk changes.

**Acceptance:** Large inventories remain bounded; invalid imports leave no partial changes; privileged overwrite behavior is explicit.

### AG-025 [P1] CLI bypasses the API and lacks administrative coverage

**Owner:** cli  
**Source:** [manage.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/manage.py)

**Evidence:** The CLI imports database models and writes directly, with only init/add-user/gen-key/add-model/check-balance operations.

**Change:** Build a thin packaged Linux CLI over the same typed /admin/v1 API as the web console; reserve separate local tooling for bootstrap/recovery.

**Acceptance:** Every normal CLI mutation is authorized and audited server-side; full resource management works remotely with correct exit codes.

### AG-026 [P1] Migrations target inconsistent paths and merge credentials

**Owner:** database  
**Source:** [scripts/migrate_endpoints.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/scripts/migrate_endpoints.py), [scripts/add_model_config_columns.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/scripts/add_model_config_columns.py), [scripts/add_rate_limit_column.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/scripts/add_rate_limit_column.py)

**Evidence:** Scripts open local aethergate.db paths rather than the configured deployed database. Endpoint migration deduplicates only by base URL, potentially combining different accounts.

**Change:** Use versioned migrations and explicit identity-preserving migration mapping; never infer account identity from URL alone.

**Acceptance:** Migration from a copy of the actual old schema preserves accounts, secrets, keys and usage references, with tested rollback/cutover controls.

### AG-027 [P1] Fresh deployment and health checks are misleading

**Owner:** operations  
**Source:** [app/main.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/main.py), [app/database.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/database.py), [docker-compose.yml](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/docker-compose.yml), [init_db.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/init_db.py)

**Evidence:** The app does not invoke initial schema creation/migration on startup; /health returns online without dependency checks. init_db.py prints errors without a failing exit.

**Change:** Run a one-shot migration stage, verify schema compatibility, and separate liveness/readiness from provider health.

**Acceptance:** A clean installation and an upgrade work without manual database surgery; dependency failures change readiness and return nonzero migration status.

### AG-028 [P1] Container and deployment defaults need hardening

**Owner:** operations  
**Source:** [Dockerfile](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/Dockerfile), [docker-compose.yml](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/docker-compose.yml), [.dockerignore](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/.dockerignore), [frontend/Dockerfile](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/Dockerfile)

**Evidence:** API image has no nonroot USER, retains build compiler, and broadly copies the repository. Ignore patterns do not explicitly cover deployed data/ or frontend node_modules. Compose publishes both service ports and hardcodes a host path.

**Change:** Use minimal nonroot immutable images, explicit source copies, protected secret mounts, configurable persistent storage, restricted listeners and resource limits.

**Acceptance:** Built images contain no runtime data/secrets or unnecessary build tree; TLS-only management ingress and clean-volume recovery are tested.

### AG-029 [P1] Python dependencies are effectively unlocked

**Owner:** supply-chain  
**Source:** [requirements.txt](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/requirements.txt), [Dockerfile](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/Dockerfile)

**Evidence:** Only numpy is pinned; core runtime/provider dependencies have no reproducible lock. The frontend does have a package lock and uses npm ci.

**Change:** Add a complete reproducible dependency lock, hashes, SBOM, vulnerability/secret scans and controlled upgrade tests. Preserve the documented CPU baseline until compatibility is tested.

**Acceptance:** Two builds from the same release use the same resolved dependencies; dependency upgrades pass protocol and queue regression tests.

### AG-030 [P1] No automated test/CI structure in the inspected tree

**Owner:** quality  
**Source:** Pinned recursive repository tree.

**Evidence:** The recursive repository tree has no automated test directory or CI workflow. debug_inference.py is an upstream connectivity example, not a gateway regression suite.

**Change:** Add unit, provider-contract, API, migration, browser, concurrency, fault-injection and SDK interoperability suites in CI.

**Acceptance:** Enterprise release gates include queue correctness, cross-worker limits, privacy, authentication and crash recovery, not just an HTTP 200.

### AG-031 [P1] Console omits operator-critical workflow and API parity

**Owner:** web  
**Source:** [frontend/src/App.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/App.tsx), [frontend/src/pages/DashboardPage.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/pages/DashboardPage.tsx), [frontend/src/pages/KeysPage.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/pages/KeysPage.tsx), [frontend/src/lib/api.ts](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/lib/api.ts)

**Evidence:** Console has basic CRUD/logs/stats, no queue/capacity view, no key editing/revocation/expiry controls, and no backup client wiring. Login validates by running stats.

**Change:** Build an operator console with effective limits, queued/in-flight work, blocked reasons, provider probes, scoped identity and a generated API client.

**Acceptance:** Operators can determine why a request is waiting, safely drain endpoints, revoke keys and administer every documented resource from web and CLI.

### AG-032 [P2] UI/API contract mismatches and lifecycle issues

**Owner:** web  
**Source:** [frontend/src/pages/UsersPage.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/pages/UsersPage.tsx), [frontend/src/pages/EndpointsPage.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/pages/EndpointsPage.tsx), [frontend/src/pages/KeysPage.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/pages/KeysPage.tsx), [frontend/src/pages/ModelsPage.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/pages/ModelsPage.tsx), [frontend/src/lib/types.ts](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/lib/types.ts)

**Evidence:** Clearing organization sends null but backend ignores it; empty provider-key edit cannot clear credentials; rotation has no confirmation; public model names are lowercased; clipboard completion is not awaited; several create forms permit repeated submission.

**Change:** Use generated contracts, explicit destructive actions, validated shared forms, cancellable queries, server pagination and accurate per-operation status.

**Acceptance:** Browser tests cover clearing values, duplicate submission, destructive confirmation, clipboard failure and expired sessions.

### AG-033 [P1] Provider transport policy is implicit

**Owner:** providers  
**Source:** [app/routers/proxy.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/proxy.py), [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py)

**Evidence:** Endpoints accept arbitrary URL strings and the proxy supplies a browser-impersonating User-Agent intended to bypass generic blocks.

**Change:** Require approved endpoint destinations and TLS policy, allow intended private ranges explicitly, block metadata/rebinding/redirect escapes, and use supported authentication and honest identification.

**Acceptance:** Inference clients cannot override destinations, credentials or trusted transport settings; approved LAN inference remains supported.

### AG-034 [P2] Monolithic admin/models files impede parallel ownership

**Owner:** architecture  
**Source:** [app/routers/admin.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/routers/admin.py), [app/models.py](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/app/models.py), [frontend/src/pages/ModelsPage.tsx](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/frontend/src/pages/ModelsPage.tsx)

**Evidence:** Large shared files own unrelated CRUD, identity, secrets, backup and accounting concepts, while the UI duplicates request types and form logic.

**Change:** Create explicit domain modules and versioned contracts, module-specific tests and CODEOWNERS; coordinate migration integration centrally.

**Acceptance:** Agents can implement modules on separate branches/worktrees without routinely editing shared god files or violating architecture tests.

### AG-035 [P1] Operational documentation describes retired deployment

**Owner:** documentation  
**Source:** [README.md](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/README.md), [OPS_RUNBOOK.md](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/OPS_RUNBOOK.md), [docker-compose.yml](https://github.com/draeician/aethergate/blob/06518fad8a62016212218f5579b808f86c82045c/docker-compose.yml)

**Evidence:** README and operations runbook still refer to the removed Streamlit service/8501, older environment wiring and host-specific migration procedures.

**Change:** Archive historical notes; publish supported installation, TLS/SSO bootstrap, upgrade, rollback, backup/restore and provider-limit configuration instructions.

**Acceptance:** A new operator can install and recover the documented release on a clean Linux host without undocumented commands.

## Primary external references consulted

These are living sources inspected on October 3, 2026. Pin a tested protocol/dependency baseline for releases; do not assume later upstream documentation matches an older installed package.

- [S1] OpenAI rate limits: https://developers.openai.com/api/docs/guides/rate-limits
- [S2] OpenAI Responses migration: https://developers.openai.com/api/docs/guides/migrate-to-responses
- [S3] OpenAI usage and streamed usage: https://help.openai.com/en/articles/10478918-api-usage-dashboard/
- [S4] LiteLLM Router / max parallel requests: https://docs.litellm.ai/docs/routing
- [S5] 9router reference project: https://github.com/decolua/9router
- [S6] nginx proxy buffering: https://nginx.org/en/docs/http/ngx_http_proxy_module.html
- [S7] PostgreSQL SELECT locking / queue consumers: https://www.postgresql.org/docs/current/sql-select.html
- [S8] OAuth device authorization grant: https://www.rfc-editor.org/rfc/rfc8628
- [S9] OAuth security best current practice: https://www.rfc-editor.org/rfc/rfc9700
- [S10] OpenAI official schema repository: https://github.com/openai/openai-openapi
- [S11] OpenAI Chat Completions reference: https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create

The design takes provider abstraction from LiteLLM and accessible connection/routing management from 9router. Neither marketing descriptions nor a dependency's name establish compliance with company quota, security, privacy or durability requirements. AetherGate must test and own those guarantees.
