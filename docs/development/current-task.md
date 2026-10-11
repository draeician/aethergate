# AetherGate — Current Task

## Task ID
AGV2-027

## Title
Canonical v2 cutover — retire v1 and make the current architecture the only supported runtime

## Superseded Task

AGV2-026 (Embeddings) was queued but **not started**.

It is intentionally deferred by operator direction. Do not implement embeddings in this task.

New sequence:
1. AGV2-027 — clean v2 cutover / v1 retirement;
2. Vision / multimodal inference;
3. Embeddings may be resumed later.

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-027
- branch: v2
- UTC start timestamp

The marker is gitignored.
Never stage, commit, or push it.
Keep it present for the entire incomplete task.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every local/live criterion below is green;
2. every task commit is pushed to origin/v2;
3. final origin/v2 remote CI is green;
4. handoff is committed/pushed and remote verified.

## Operator Cutover Decision

The v1 deployment was never used for production and contains no important state.

The authoritative requirements have already been updated:

- `project_spec.md` — current cutover is clean/destructive; no v1 SQLite data import;
- `AGENTS.md` — agents must not resurrect a legacy data importer;
- `docs/migrations/v1-to-v2.md` — clean-cutover procedure replaces the prior preservation plan.

Therefore:

- **do not build a v1 SQLite -> v2 PostgreSQL data importer;**
- **do not preserve v1 users, keys, balances, request logs, endpoints, models, or plaintext provider
  credentials;**
- v2 starts with a fresh PostgreSQL authority;
- the dated architecture audit remains immutable historical evidence.

## Starting Point

Last completed feature milestone:

AGV2-025 — Responses text subset.

Verified final SHA:
`07ae618ff95503f765ae2f4ad45f531292921b3a`

Green baseline:
- backend: **613 collected**
- frontend: **87 passed**
- Playwright: **32 passed** (+ 3 CI-only skips)
- migration head: **0017**
- Python SDK `openai==2.54.0`
- JS SDK `openai@7.32.0`
- remote CI 4/4 green.

The branch also contains the requirement-update commits immediately after the superseded AGV2-026
queue commit. Pull latest `origin/v2` before doing anything beyond the WIP marker.

## Goal

After this task, the repository must have **one supported AetherGate implementation**: the current
PostgreSQL/Alembic/scheduler architecture under `src/aethergate`.

No active source file, root runtime entrypoint, operator document, agent instruction, or CI path may
suggest that the old SQLModel/SQLite implementation is still supported.

This is a codebase/runtime cutover, not a historical-data conversion.

## Recovery

If context is compacted/restarted/uncertain:

1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. re-read docs/migrations/v1-to-v2.md;
7. inspect git status/history;
8. continue from repository state.

## 1. Inventory legacy v1 artifacts before deletion

Before changing files, inventory every v1-only artifact.

Known legacy candidates include:

- `app/`
- root `Dockerfile`
- root `docker-compose.yml`
- root `requirements.txt`
- `init_db.py`
- `manage.py`
- `debug_inference.py`
- `scripts/add_model_config_columns.py`
- `scripts/add_rate_limit_column.py`
- `scripts/migrate_endpoints.py`
- `API_REFERENCE.md`
- `OPS_RUNBOOK.md`
- `AETHERGATE_RULES.md`
- `.context-resume.md`
- stale `.cursorrules`
- stale `CLAUDE.md`.

Search the whole tracked tree for additional:
- imports from `app`;
- SQLModel/aiosqlite runtime references;
- v1 `MASTER_API_KEY` / `x-admin-key` instructions;
- `sk-` v1 credential examples;
- balance-as-authorization language;
- direct SQLite paths;
- obsolete v1 container names;
- v1-only admin URLs;
- old migration scripts.

Do not delete the immutable dated audit:
`docs/audits/v2-architecture-audit-2026-10-03.md`.

Do not delete historical references inside the audit merely because they describe v1.

## 2. Remove the legacy runtime

Delete the unsupported v1 application/runtime code.

Expected removals:
- entire `app/`;
- root legacy Dockerfile;
- root legacy docker-compose.yml;
- root legacy requirements.txt;
- v1 DB/admin/debug scripts;
- v1 ad-hoc SQLite schema migration scripts.

No compatibility shim is required.

Do not leave a dead import path that can accidentally start the old FastAPI app.

After removal, searches such as:
- `from app.`
- `import app.`
- `sqlmodel`
- `aiosqlite`
- `sqlite+aiosqlite`

must not appear in active runtime/build/test/operator code unless a historical document explicitly
describes the retired system.

## 3. Retire stale v1 operator documentation

Remove or replace stale root documents that describe the old runtime.

### API_REFERENCE.md
The old document describes:
- `sk-` keys;
- balance authorization;
- `x-admin-key`;
- old `/admin/*` routes.

It must not remain as an apparent current API reference.

Preferred:
- remove it and point README/docs to generated OpenAPI + current contract docs;
or
- replace it with a very small canonical pointer explicitly stating the old reference was retired.

Do not duplicate the generated API documentation manually.

### OPS_RUNBOOK.md
The old docker02/Streamlit/NPM runbook is historical and unsafe as a current operator guide.

Remove it or replace it with a pointer to:
`docs/operations/alpha-runbook.md`.

No stale host IPs, old container names, Streamlit instructions, or MASTER_API_KEY instructions should
remain in active operator docs.

## 4. Retire stale agent instructions

Old agent files currently instruct:
- SQLModel;
- prepaid balance as universal authorization;
- old v1 architecture.

That directly conflicts with `project_spec.md`.

Required:
- remove `AETHERGATE_RULES.md` if nothing current depends on it;
- remove `.context-resume.md`;
- update or replace `.cursorrules` so it points to `AGENTS.md` + `project_spec.md`, with no v1
  architecture rules;
- update `CLAUDE.md` similarly if it is retained.

The rule hierarchy must be unambiguous:
1. AGENTS.md
2. project_spec.md
3. current task/handoff
4. architecture docs.

No agent bootstrap file may tell a model to use SQLModel or prepaid-balance gating.

## 5. Canonical runtime remains the proven v2 stack

Do **not** gratuitously rename the stable current deployment tree during this cutover.

The canonical implementation may continue to live at:
- `src/aethergate/`
- `deploy/v2/`
- `scripts/dev/v2`
- `scripts/ops/v2`

Those paths are already deeply verified and the internal `v2` path name does not imply v1 remains
supported.

The goal is one supported implementation, not a cosmetic path-renaming project.

README/docs must clearly say that these are the canonical current AetherGate paths.

Do not create duplicate root Compose/Docker definitions merely to remove the string "v2".

## 6. Root build/runtime ambiguity

After deleting the legacy root Dockerfile/Compose:

- README quick-start must exclusively use the supported current commands;
- no documentation may suggest `docker compose up` at repo root unless that is actually supported;
- `deploy/v2/Dockerfile` is the canonical image;
- `deploy/v2/compose*.yaml` is the canonical Compose stack;
- `scripts/dev/v2` and `scripts/ops/v2` are canonical wrappers.

If any tooling assumes a root Dockerfile, update that tooling to the canonical path.

Do not add a root Dockerfile just as a compatibility alias unless a real supported workflow requires it.

## 7. CI must be ready for main cutover

Current workflows run on pushes to `v2`.

Update all four GitHub Actions so push triggers include:
- `v2`;
- `main`.

Pull-request behavior remains.

Required workflows:
- backend;
- frontend;
- schema-secret-gate;
- alpha-golden-path.

Preserve:
- least privilege;
- concurrency;
- locked dependencies;
- fully offline golden path.

This ensures the same gates protect the repository after v2 is merged into main.

## 8. Add a legacy-reintroduction CI guard

Add a small deterministic CI script/check that fails if forbidden v1 runtime artifacts are reintroduced.

Recommended:
`scripts/ci/no_legacy_v1.sh`

At minimum fail if tracked active files include:
- `app/`;
- root legacy `docker-compose.yml`;
- root legacy `requirements.txt`;
- retired v1 migration scripts;
- active imports of `app.*`;
- active SQLModel/aiosqlite dependencies outside historical audit text.

Do not make the check so broad that the immutable v1 audit or migration-retirement document fails for
describing history.

Wire this guard into `schema-secret-gate` or backend CI.

Add a test or self-check proving the guard itself catches a synthetic forbidden path/reference.

## 9. Dependency cleanup

After v1 removal, verify no current dependency exists solely for v1.

Specifically audit:
- sqlmodel;
- aiosqlite;
- passlib/bcrypt v1 usage;
- typer v1 management usage;
- numpy v1 pin rationale.

Do not remove a package merely because v1 used it if the current stack still needs it transitively or
directly.

The committed `constraints.txt` must remain reproducible.

If dependency files legitimately change:
- refresh the lock through the documented locking workflow;
- regenerate OpenAPI if dependency versions change;
- run all regression gates.

Avoid dependency churn unrelated to the legacy removal.

## 10. Current documentation canonicalization

Update at minimum:
- README.md
- docs/development/README.md
- docs/operations/alpha-runbook.md
- docs/releases/alpha1-readiness.md
- docs/migrations/v1-to-v2.md if implementation discoveries refine it
- AGENTS.md if any now-deleted file is still mandatory reading
- docs/development/agent-handoff.md.

README should present:
- **AetherGate**, not “old v1 plus v2”;
- the current architecture as the only supported runtime;
- Models + Chat Completions + Responses text subset;
- embeddings still deferred;
- vision planned next after cutover;
- canonical dev/operator commands.

Historical documentation may say "v1" and "v2" where needed to explain history.

## 11. Architecture/reference scan

Search current non-audit docs for statements that are now false, especially:
- "v1 migration still required";
- "legacy app retained for reference";
- SQLite as an active runtime;
- identity/history preservation as a current cutover requirement;
- old admin key model;
- balance/prepaid as universal authorization;
- Streamlit dashboard/current NPM setup.

Correct current docs.

Do not rewrite the dated audit.

Do not erase useful historical context; label it clearly as retired/historical.

## 12. Clean-install cutover proof

Use a fresh disposable environment, not the accumulated dev DB.

Prove the actual supported transition is:

1. no v1 database imported;
2. fresh PostgreSQL database;
3. Alembic empty -> head;
4. config preflight;
5. API/worker readiness;
6. one-use bootstrap;
7. deterministic OIDC login;
8. create project/principal/catalog/provider account/endpoint/alias/route;
9. create inference credential;
10. official Python SDK Models;
11. Chat non-stream;
12. Chat stream;
13. Responses non-stream;
14. Responses stream;
15. dashboard/operator console loads;
16. accounting/observability state appears;
17. backup succeeds;
18. restart succeeds;
19. post-restart SDK request succeeds.

This is the replacement for a v1 data-import test.

Migration head should remain `0017`.

## 13. Legacy runtime negative proof

Prove:
- old `uvicorn app.main:app` cannot be started from the repository because `app/` is gone;
- old root Compose definition is gone;
- old SQLite DB initialization/management commands are gone;
- no CI/build path imports them;
- no web/frontend code still targets old `/admin/*` or `x-admin-key` contracts.

A historical audit mention does not count as active code.

## 14. Nomnom cutover rehearsal

On the internal dev/test environment:

1. inspect whether legacy v1 containers/processes exist;
2. if they are clearly the obsolete AetherGate v1 stack, stop/remove them safely;
3. do not touch unrelated containers/services;
4. do not depend on or import the legacy SQLite database;
5. start a **fresh disposable current AetherGate PostgreSQL stack**;
6. migrate to 0017;
7. execute bootstrap/OIDC/catalog/credential flow;
8. official SDK Chat + Responses green with bypass=false;
9. queue/scheduler behavior green;
10. web console green;
11. backup/restore or restart proof remains green;
12. tear down only the disposable rehearsal resources.

Do not perform destructive actions on an unknown host/path merely because a filename resembles
`aethergate.db`.

The operator has stated the known v1 deployment data is disposable, but still positively identify a
resource as the old AetherGate stack before deleting it.

## 15. Main-branch readiness

Do **not** push directly to main.

At task completion:
- `origin/v2` must contain the canonical clean-cutover state;
- all four CI workflows must be green on the final v2 SHA;
- CI push triggers must include main;
- handoff must state that v2 is ready to become the repository's main line.

If GitHub CLI/auth is already available and repository policy permits creating PRs:
- create or update a PR from `v2` -> `main`;
- do **not** merge it unless a later explicit instruction authorizes the merge.

If PR creation is not available, record the exact final SHA and recommend one next step:
**merge v2 into main through the repository's normal reviewed PR process**.

Never force-push main.

## 16. Embeddings status

AGV2-026 Embeddings was superseded before implementation.

Do not accidentally implement it during cutover.

After cutover, operator direction is:
**vision next**.

Record embeddings as deferred, not cancelled forever.

## 17. Regression gate

Run the complete current gate:

- backend full suite; baseline **613**
- frontend unit/component; baseline **87**
- full Playwright; baseline **32**
- npm build
- npm lint
- `npx tsc -b --noEmit`
- OpenAPI/client drift under locked dependencies
- ruff check src tests
- git diff --check
- secret/token/content scan
- new no-legacy-v1 guard
- migration empty -> 0017
- populated 0016 -> 0017
- config_check
- fake Ollama provider tests
- official Python SDK:
  - Models
  - Chat non-stream/stream
  - Responses non-stream/stream
- official JS SDK current smoke
- fresh clean-install cutover rehearsal.

No migration revision is expected.
Do not modify migrations 0001-0017.

Every red test must be root-caused.

## 18. Remote CI gate

Push completed work to origin/v2.

On the FINAL SHA require:
- backend = completed/success
- frontend = completed/success
- schema-secret-gate = completed/success
- alpha-golden-path = completed/success

All must be for that exact final SHA.

If red:
- inspect logs;
- root-cause;
- fix;
- push;
- require green on the new final SHA.

## 19. Handoff

Record:
- operator decision that v1 data was disposable;
- AGV2-026 embeddings superseded/deferred before implementation;
- exact legacy files removed/replaced;
- stale agent/operator docs removed or redirected;
- dependency cleanup decisions;
- no-legacy guard behavior;
- clean-install cutover proof;
- nomnom v1 retirement/rehearsal result;
- migration head 0017;
- backend/frontend/Playwright counts;
- SDK results;
- OpenAPI drift;
- final SHA and four remote workflow run IDs/conclusions;
- PR v2 -> main status if created;
- known limitations;
- WIP lifecycle;
- exactly one recommended next step:
  **merge v2 into main through reviewed PR, or if already merged by an authorized later action,
  begin vision/multimodal inference.**

Never include credentials, session/OIDC/provider secrets, prompt/completion content, encrypted bytes,
or large logs.

## Commit and Push

Suggested commits:
- `chore: retire legacy v1 runtime`
- `ci: guard canonical aethergate runtime`
- `docs: complete clean v2 cutover`

Use fewer/more scoped conventional commits if cleaner.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to continue/commit/push.

The task is complete only when:
- the v1 runtime and misleading active v1 instructions are gone;
- the current PostgreSQL/scheduler implementation is the only supported runtime;
- a fresh install from no v1 state is proven;
- migration head remains 0017;
- all current regressions are green;
- all four GitHub Actions are green on the final origin/v2 SHA;
- main branch CI triggers are ready;
- the handoff is complete;
- local `.aethergate-wip` is removed only after final remote verification.
