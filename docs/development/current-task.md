# AetherGate v2 — Current Task

## Task ID
AGV2-024

## Title
Alpha 1 hardening — CI, fresh install, upgrade/restore, structured logs, and operator runbook

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-024
- branch: v2
- UTC start timestamp

The marker is gitignored.
Never stage, commit, or push it.
Keep it present for the entire incomplete task.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every criterion below is green;
2. handoff is committed;
3. every task commit is pushed to origin/v2;
4. origin/v2 is verified to contain the finished work.

## Why This Task Exists

AetherGate v2 now has a strong functional core:
- durable scheduler and recovery;
- quotas/cooldowns/budgets/accounting;
- OIDC/RBAC/browser/CLI identity;
- catalog/routing;
- Linux CLI/device flow;
- web operator + management UI;
- queue/operator controls;
- accounting UI;
- operational observability;
- official OpenAI SDK Chat Completions coverage.

Current green baseline:
- backend: 527;
- frontend unit/component: 87;
- full Playwright: 32;
- migration head: 0017.

The next milestone is **Alpha 1 hardening**, not feature expansion.

The goal is to prove a different operator can:
1. start from a clean host/repository checkout;
2. configure a fresh deployment without hidden local state;
3. bootstrap/login/configure a provider path;
4. issue a real OpenAI-compatible request;
5. stop/restart services safely;
6. back up PostgreSQL and restore it;
7. upgrade a populated database;
8. understand failures through safe structured logs;
9. rely on CI to enforce the same core gates automatically.

Responses, embeddings, and v1->v2 migration remain separate later milestones.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

current-task.md is authoritative.

## Before You Start

1. Work on branch v2 and pull latest origin/v2.
2. Read:
   - AGENTS.md
   - project_spec.md
   - docs/development/current-task.md
   - docs/development/agent-handoff.md
   - docs/development/agent-workstreams.md
   - docs/architecture/security.md
   - docs/architecture/scheduler.md
   - docs/architecture/observability.md
   - docs/architecture/admin-api.md
   - deploy/v2/*
   - scripts/dev/v2
   - README.md
   - docs/development/README.md
   - frontend/README.md
   - pyproject.toml
   - frontend/package.json
3. Use the canonical backend test command from AGENTS.md.
4. Treat every failing test as a real defect until root-caused.
5. Rebuild/restart affected containers before E2E.
6. Do not modify/delete legacy Python v1 app/.
7. Do not commit unrelated scratch files.

## Alpha 1 release definition for this task

AetherGate may be called **Alpha 1** after this task only if:

- clean-install path is documented and repeatable;
- a fresh database migrates to head;
- a populated 0016 database upgrades through 0017 successfully;
- PostgreSQL backup + restore preserves control-plane/accounting identity;
- API/worker restart does not lose queued/config/accounting state;
- CI enforces backend/frontend/schema/secret/core browser gates;
- structured logs are safe enough for operators to diagnose request lifecycle without content/secrets;
- official Python SDK non-stream + stream work against a clean alpha deployment with inference auth bypass false;
- no known red regression gate remains.

This is an **internal/self-hosted alpha**, not a production/beta claim.

## 1. Add CI under .github/workflows

There is currently no .github workflow.

Add GitHub Actions workflows that run on:
- pull_request;
- pushes to v2;
- optional workflow_dispatch.

Use pinned major/minor action versions and least privilege.

At minimum CI must enforce:

### Backend job
- Python 3.12;
- PostgreSQL 16 service;
- install package/dev dependencies;
- ruff check src tests;
- backend pytest using an explicit test database;
- migration empty -> head;
- migration 0016 -> 0017 fixture path;
- OpenAPI generation.

### Frontend job
- supported Node version pinned;
- npm ci;
- npm run test;
- npm run lint;
- npm run build;
- npx tsc -b --noEmit;
- generated OpenAPI/client drift check.

### Browser/SDK job
Run a deterministic, offline browser + SDK smoke path.

It MUST NOT depend on:
- nomnom;
- Internet IdP;
- real Ollama/model downloads;
- provider credentials.

Use:
- PostgreSQL service/container;
- deterministic in-repo OIDC IdP;
- deterministic fake Ollama-compatible upstream described below;
- real AetherGate API + worker + web console;
- official OpenAI Python SDK;
- Playwright/Chromium.

The CI browser job does not need to duplicate every long nomnom test if runtime would be unreasonable,
but it must cover the Alpha golden path:
- OIDC browser login;
- admin session/CSRF;
- catalog route exists;
- inference credential;
- official SDK non-stream;
- official SDK stream;
- queue/worker lifecycle;
- basic dashboard load.

Keep the full nomnom suite as the live pre-release gate.

### Secret/schema gate
- git diff --check;
- repository secret/canary scan;
- OpenAPI/generated-client drift fails CI.

CI must fail on generated drift, not silently commit regenerated files.

## 2. Deterministic fake Ollama-compatible upstream for offline CI

Add a test-only local upstream server that speaks only the minimum Ollama/LiteLLM-compatible surface
needed by the existing adapter path.

Goals:
- exercise real AetherGate scheduler + real LiteLLM adapter transport;
- no provider credentials;
- no model download;
- no Internet.

Required behaviors:
- deterministic non-stream completion;
- deterministic streaming completion with multiple chunks;
- usage metadata sufficient for existing SDK/accounting tests;
- configurable response delay so queueing can be tested;
- configurable status failure, including 429 and 404;
- deterministic upstream request ID if the protocol exposes one.

Security:
- explicitly test/dev-only;
- never started by production compose;
- never accepts or logs secrets/content beyond what is necessary for deterministic fixture handling;
- do not persist request content;
- fixed canned response is preferred.

Do not create a new production provider kind merely for CI.
Prefer emulating the existing Ollama HTTP surface so LiteLLM still uses provider kind `ollama`.

## 3. CI-safe deterministic OIDC

Reuse the existing deterministic DevOidcIdp.

Make its CI lifecycle first-class:
- fixed CI issuer reachable by API + browser;
- no LAN-IP discovery requirement in CI;
- deterministic subject;
- generated signing key may remain ephemeral per job;
- API and browser start after IdP readiness.

Do not weaken production HTTPS issuer validation.

## 4. Fresh-install operator path

Create a documented, supported Alpha clean-install flow separate from ad-hoc dev history.

Prefer:
- keep `deploy/v2/compose.yaml` as the core stack;
- add a small alpha/operator compose override only if necessary;
- do not bake dev IdP into the alpha deployment.

Fresh install must cover:
1. prerequisites;
2. generate/provide required secrets;
3. build/pull images;
4. start PostgreSQL;
5. migrate empty DB to 0017;
6. start API + worker;
7. verify /health/live and /health/ready;
8. perform one-use bootstrap;
9. remove/disable bootstrap token after use;
10. configure OIDC;
11. configure SecretRef/catalog/provider account/endpoint/alias/route;
12. create inference credential;
13. run official SDK non-stream + stream.

Document loopback/dev assumptions separately from real reverse-proxy/TLS expectations.

Do not claim the dev compose file alone is a production deployment.

## 5. Configuration preflight

Add an explicit configuration validation command, e.g.:

`python -m aethergate.config_check`

or a documented CLI entrypoint.

It must:
- parse Settings in the selected environment;
- verify required database configuration is present;
- verify queue encryption key is valid;
- reject inference auth bypass in prod (existing invariant);
- verify OIDC prod issuer rules when enabled;
- verify bootstrap-token validity if set;
- report whether upstream allowlist is empty;
- report safe warnings for intentionally disabled optional features;
- never print secret values.

Exit codes:
- 0 valid;
- nonzero invalid.

Add tests for prod/dev validation.

Do not connect to provider endpoints as part of config syntax validation.

## 6. Database backup / restore tooling

Normal admin tooling must not manipulate PostgreSQL directly, but tightly controlled operator recovery
tooling may.

Add safe operator scripts/commands under a clearly offline/ops namespace, for example:
- `scripts/ops/v2-backup`
- `scripts/ops/v2-restore`

or a single coherent `scripts/ops/v2` helper.

Requirements:
- use pg_dump custom format or another PostgreSQL-native complete backup;
- backup path explicitly supplied;
- restrictive file permissions where the platform supports it;
- never echo DB password;
- restore requires explicit target and confirmation/force semantics;
- restore into a stopped/quiesced API/worker state;
- restore must not silently overwrite a running database;
- document that queue encryption key / external secret backend material must be backed up separately and preserved;
- do not copy raw provider secrets into the DB backup beyond what PostgreSQL already stores (SecretRef metadata only).

## 7. Backup/restore acceptance proof

On a clean disposable stack:

1. migrate empty -> 0017;
2. create:
   - project;
   - principal;
   - role assignment;
   - catalog/provider/account/endpoint/alias/route;
   - inference credential metadata;
   - price policy/budget;
   - at least one completed request with UsageRecord/LedgerEntry;
3. record only safe IDs/counts/checksums of non-secret metadata;
4. take PostgreSQL backup;
5. destroy the PostgreSQL volume/database;
6. restore from backup;
7. start API/worker using the SAME queue encryption key and same external-secret configuration;
8. verify:
   - identities/roles preserved;
   - catalog routing preserved;
   - credential authentication still works if credential verifier data is part of PostgreSQL;
   - completed request metadata preserved;
   - UsageRecord/LedgerEntry/PriceSnapshot preserved;
   - no duplicate settlement;
   - migrations report head 0017;
   - official SDK inference works after restore.

Never print raw keys in evidence.

If queue encryption-key loss makes queued encrypted content unrecoverable, document that explicitly as a
backup dependency; do not weaken encryption.

## 8. Service restart / persistence proof

On a populated alpha stack:

### API restart
- restart API only;
- worker remains;
- /health recovers;
- admin/browser session behavior is documented (session persists if DB-backed);
- inference configuration remains intact.

### Worker restart
- submit queued/in-flight workload;
- restart worker in a controlled safe scenario;
- queued undispatched work resumes;
- pre-dispatch lease recovery works;
- dispatched ambiguous work follows the existing outcome_unknown conservative path;
- no duplicate execution/settlement.

### Full stack restart
- stop API + workers + PostgreSQL cleanly without deleting volume;
- start again;
- configuration/identity/accounting history remains;
- official SDK request succeeds.

Do not call a destructive `reset`.

## 9. Upgrade proof: populated 0016 -> 0017

Create/retain a representative populated database at migration 0016.

It must include safe representative rows from:
- identity;
- catalog;
- queue/execution attempts;
- accounting.

Then run 0016 -> 0017.

Prove:
- existing rows preserved;
- new observability columns have correct defaults/nulls;
- no fake first_token_at backfill;
- API/worker start normally;
- old accounting/history still readable;
- new requests produce observability data.

Do not test only an empty schema.

## 10. Structured operational logging

Implement a minimal structured logging baseline suitable for Alpha.

Preferred:
- JSON logs in prod/alpha mode;
- human-readable dev logs may remain available.

Every structured request/worker lifecycle log should use safe metadata only.

Useful fields:
- timestamp;
- level;
- logger/event;
- gateway request_id;
- execution attempt_id where applicable;
- endpoint_id/model_alias_id/project_id where safe;
- request lifecycle state;
- HTTP method/path template/status;
- duration_ms;
- upstream_status_code numeric only where safe.

Never log:
- Authorization header;
- cookies;
- CSRF;
- OIDC tokens/codes/verifiers/nonces;
- API/provider/CLI/session keys;
- prompt/completion/tool content;
- encrypted payload/result bytes;
- raw provider body/headers;
- database URL/password.

Use route templates, not arbitrary sensitive query-string dumps.

## 11. Redaction filter / tests

Add a central logging redaction/safety layer.

Tests must prove common secret forms do not appear in captured logs:
- `agk_`;
- `ags_`;
- bootstrap token prefix;
- Authorization Bearer;
- session/csrf cookie values;
- OIDC code/token-like fixture;
- provider secret fixture;
- DB password/URL fixture;
- prompt canary;
- completion canary.

Prefer not logging sensitive objects at all; redaction is defense in depth, not permission to log them.

Ensure stack traces do not stringify Settings/secret-bearing request objects unexpectedly.

## 12. Request correlation

Ensure every API response already carrying a gateway request ID has the same ID available in structured
logs for that request.

For worker lifecycle:
- propagate/persist the existing gateway request_id;
- include attempt_id/endpoint_id as safe correlation fields.

Do not generate a second unrelated "observability request ID".

Document gateway request ID vs upstream request ID.

## 13. Health/readiness alpha behavior

Review readiness semantics.

At minimum:
- liveness = process alive;
- readiness = PostgreSQL authoritative dependency reachable.

Do not make readiness depend on every configured upstream provider.

If migrations/schema are incompatible with the running code, startup or readiness must fail clearly
rather than serving normal traffic against an unsupported schema.

Add a startup/schema-head check if not already present, or document/implement an explicit pre-start
migration requirement with a safe failure mode.

Do not auto-run destructive migrations inside every worker process.

## 14. Alpha operator runbook

Add `docs/operations/alpha-runbook.md`.

It must be usable by someone who did not develop the project.

Include:
- supported Alpha scope:
  - Models;
  - Chat Completions non-stream/stream;
  - admin API;
  - CLI;
  - web console;
- explicitly unsupported/not-yet-alpha:
  - Responses;
  - embeddings;
  - v1->v2 migration;
  - production-grade HA;
  - external TSDB/alerting;
- prerequisites;
- secret generation/storage expectations;
- clean install;
- migrations;
- bootstrap;
- OIDC configuration;
- reverse proxy/TLS expectations;
- provider/catalog setup;
- creating credentials;
- health/readiness;
- logs/request correlation;
- pause/drain/cancel/outcome_unknown;
- backup;
- restore;
- upgrade;
- restart/recovery;
- common failure modes;
- rollback expectations;
- how to gather a safe support bundle without secrets/content.

No real secrets or environment-specific IPs.

## 15. Safe diagnostics/support bundle

Add an operator command/script that gathers only safe diagnostics, e.g.:

- app version;
- migration head/current;
- container/service state where available;
- health endpoint status;
- queue summary counts;
- observability summary;
- sanitized config feature flags;
- recent structured logs AFTER redaction.

Requirements:
- no credentials/tokens/cookies;
- no prompt/completion/encrypted content;
- no raw DB URL/password;
- no provider secret values;
- output path explicit;
- canary test proves sensitive fixtures are absent.

Do not automatically upload anything.

## 16. Alpha version/status marker

Do not invent a SemVer bump unless the repository's existing version policy supports it and the active
task explicitly requires it.

Instead add a documentation/status marker such as:
- `docs/releases/alpha1-readiness.md`

It should record:
- what Alpha 1 means;
- supported surfaces;
- deferred surfaces;
- exact test gates;
- current migration head;
- known limitations.

If the package already has a meaningful versioning convention, document the current version; do not
silently bump it just to label Alpha.

## 17. Deterministic Alpha golden-path test

Add one reproducible golden-path automation that can run from a clean environment.

It should prove:
1. clean DB;
2. migrate;
3. bootstrap/admin auth;
4. deterministic OIDC browser login;
5. catalog route to fake Ollama;
6. inference credential;
7. official SDK non-stream;
8. official SDK stream;
9. web dashboard loads;
10. backup;
11. restore;
12. post-restore SDK inference succeeds.

This can be a CI script/test composition rather than one monolithic test.

It must not require Internet/provider credentials.

## 18. Nomnom live Alpha acceptance

After offline CI/golden-path is green, run the real nomnom acceptance with internal Ollama:

1. clean disposable PostgreSQL volume/database;
2. empty -> 0017;
3. bootstrap;
4. real deterministic OIDC browser login;
5. configure actual Ollama route through supported admin/web path;
6. create inference credential;
7. official Python SDK non-stream + stream, bypass=false;
8. verify queue/dashboard/observability;
9. take backup;
10. generate additional accounting/history state;
11. restore backup into a fresh disposable DB;
12. verify the expected pre-backup state exactly;
13. perform normal post-restore inference;
14. API restart;
15. worker restart/recovery scenario;
16. full stack restart with persistent volume;
17. re-run SDK inference.

Never reuse the accumulated long-lived nomnom database as the only Alpha proof.

## 19. CI vs live-provider separation

Keep this distinction explicit:

### CI
- fully deterministic;
- offline with fake Ollama;
- no real provider credentials;
- no external network dependency.

### Nomnom pre-release
- real internal Ollama;
- exercises actual model latency/streaming;
- not required for every GitHub PR.

Do not make CI download large model weights.

## 20. Regression gate

Run locally/nomnom:
- full backend suite; baseline **527**;
- frontend unit/component; baseline **87**;
- FULL Playwright; baseline **32**;
- npm build;
- npm lint;
- `npx tsc -b --noEmit`;
- OpenAPI/client drift;
- ruff check src tests;
- git diff --check;
- secret/token/content canary scan;
- migration empty -> 0017;
- populated 0016 -> 0017;
- backup/restore golden path;
- official OpenAI Python SDK non-stream + stream with bypass=false.

Also validate the GitHub Actions workflow syntax/configuration as far as the environment permits.

If GitHub Actions can be observed after push, record its result truthfully. Do not claim a remote CI run
passed unless it actually did.

Every local red test must be root-caused.

## 21. Migration

No new schema migration is expected.

Migration head should remain **0017**.

Do not modify migrations 0001-0017 unless a genuine Alpha blocker requires a narrowly justified linear
0018.

Backup/restore tooling itself is not a migration.

## 22. Documentation

Update:
- README.md;
- docs/development/README.md;
- frontend/README.md if operator-facing web startup changes;
- docs/architecture/security.md for logging/redaction if needed;
- docs/architecture/observability.md for log correlation if needed;
- add docs/operations/alpha-runbook.md;
- add docs/releases/alpha1-readiness.md;
- docs/development/agent-handoff.md.

Do not edit the dated architecture audit.

## 23. Still Deferred

Do not implement:
- Responses API;
- embeddings;
- v1->v2 data migration;
- production secret backend selection;
- production-grade HA/failover orchestration;
- external Prometheus/OpenTelemetry/TSDB exporter;
- paging/alerting;
- billing/invoice/payment semantics;
- hosted SaaS tenancy;
- broad visual redesign.

## 24. Handoff

Include:
- implementation commits;
- CI workflow/jobs and what they actually ran;
- fake Ollama protocol/limitations;
- clean-install proof;
- config-preflight proof;
- backup/restore proof;
- populated 0016->0017 proof;
- API/worker/full-stack restart proof;
- structured logging format;
- redaction/canary proof;
- request-correlation proof;
- diagnostics bundle proof;
- Alpha golden-path proof;
- real nomnom fresh-stack acceptance;
- backend/frontend/Playwright counts;
- build/lint/typecheck/OpenAPI results;
- official SDK version/results;
- migration head;
- known Alpha limitations;
- WIP marker lifecycle;
- exactly one recommended next step.

Never include raw credentials/provider/session/CSRF/OIDC/bootstrap/database secrets, prompt/completion
content, encrypted content, or large logs.

## Commit and Push

Suggested commits:
- `ci: add alpha release gates`
- `feat(ops): add alpha backup restore and diagnostics`
- `feat(logging): add safe structured operational logs`
- `docs: add alpha1 operator runbook`

Use fewer commits if cleaner, but keep them conventional and scoped.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when:
- every local Alpha gate is green;
- deterministic offline CI/golden path exists;
- fresh nomnom Alpha acceptance is green;
- migration head is 0017 unless narrowly justified otherwise;
- origin/v2 contains final implementation/tests/docs/handoff;
- local `.aethergate-wip` is removed only after final remote verification.
