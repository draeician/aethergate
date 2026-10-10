# AetherGate v2 — Current Task

## Task ID
AGV2-024V

## Title
Close Alpha 1 remote CI gates and dependency reproducibility

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-024V
- branch: v2
- UTC start timestamp

The marker is gitignored.
Never stage, commit, or push it.
Keep it present for the entire incomplete task.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every local criterion below is green;
2. the FINAL pushed origin/v2 SHA has the required GitHub Actions workflows completed successfully;
3. handoff is committed/pushed and remote verified.

## Why This Task Exists

AGV2-024 implemented the Alpha 1 hardening scope and its local/nomnom acceptance is strong:
- backend 561 collected green locally;
- frontend 87;
- full Playwright 32;
- clean disposable nomnom install/backup/restore/restart acceptance;
- migration head 0017;
- config preflight;
- schema-head readiness;
- structured logging/redaction;
- deterministic fake Ollama;
- operator tooling/runbook.

However, after AGV2-024 was pushed, GitHub Actions actually ran on final head
`fb00f875513b8bec90d5284facf122850768868e`, and **all four workflows failed**.

Remote evidence:

### alpha-golden-path
Run id: 38085329988
Failure:
- "Generate deterministic secrets" imports `cryptography.fernet.Fernet` before dependencies are installed.
- GitHub runner reports `ModuleNotFoundError: No module named 'cryptography'`.
- Because the .env file was never created, failure cleanup also errors when Compose interpolates
  missing `POSTGRES_PASSWORD`.

### backend
Run id: 38085330138
Failures:
- CI sets `AETHERGATE_TEST_DATABASE_URL=.../aethergate_test` but does not create that database before
  pytest; DB-gated tests fail with `database "aethergate_test" does not exist`.
- `test_prod_requires_queue_key` is contaminated by workflow-level
  `AETHERGATE_QUEUE_KEY`; Pydantic Settings reads that ambient env value, so the test does not raise.

### frontend + schema-secret-gate
Run ids: 38085330153 and 38085330056
Failure:
- OpenAPI generation changes committed Decimal schema string branches by removing Pydantic-generated
  `pattern` fields.
- CI installs a newer unconstrained Pydantic stack than the environment that generated the committed
  OpenAPI.
- This is a real reproducibility problem: generated contracts must not vary with whichever compatible
  dependency version pip resolves that day.

The remote secret scan itself passed.

Close these CI/reproducibility gaps only. Do not start Responses/embeddings yet.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history and GitHub Actions status;
7. continue from repository state.

## 1. Make Python dependency resolution reproducible

Alpha CI, container builds, local canonical tests, and generated OpenAPI must use a reproducible Python
dependency set.

Current broad ranges such as:
- `pydantic>=2.7,<3`
- `fastapi>=0.115,<1`
- `litellm>=1.100,<2`

allow schema/runtime behavior to change independently of the repository.

Choose a simple repository-native locking strategy and document it.

Preferred acceptable approaches:
- a committed constraints/lock file generated from the known-green Alpha environment and consumed by
  CI + Docker/dev installs; or
- a standard Python lock workflow compatible with the existing setuptools/pip project.

Requirements:
- pin transitive versions sufficiently for deterministic CI/builds;
- the canonical install path uses the lock/constraints;
- CI uses exactly the same resolved versions;
- Docker build uses the same resolved runtime dependency set;
- developers have a documented command to intentionally refresh the lock;
- refreshing dependencies is an explicit change with tests/OpenAPI regeneration, never implicit;
- do not silently pin only Pydantic while leaving the rest of the runtime non-reproducible unless
  there is a documented compelling reason.

Do not accidentally include environment-specific editable paths or credentials in the lock.

## 2. Settle the OpenAPI Decimal schema deterministically

After the dependency set is locked:

1. regenerate OpenAPI from the locked environment;
2. regenerate frontend types;
3. commit the canonical generated output if it legitimately changes;
4. run the drift command again and prove no diff.

Do not manually edit generated OpenAPI/schema files.

If the locked known-green environment emits the existing Decimal `pattern`, preserve it.
If the chosen current lock legitimately emits the newer schema without that Pydantic-generated
pattern:
- inspect generated TypeScript impact;
- ensure Decimal-safe application behavior is unchanged;
- add/retain explicit backend/frontend Decimal validation tests;
- commit the regenerated canonical contract.

The wire requirement remains exact Decimal string serialization and no browser float coercion.

## 3. Fix backend CI test database creation

Before `pytest` in `.github/workflows/backend.yml`:

- create the database named by `AETHERGATE_TEST_DATABASE_URL` using the existing
  `tests.db_helpers.ensure_database` or equivalent;
- do not depend on a prior test to create it;
- keep the database isolated from the main PostgreSQL service DB;
- preserve the explicit test URL.

Then run the complete backend suite.

Do not change DB-gated tests to skip merely to make CI green.

## 4. Isolate configuration tests from ambient workflow env

Fix `tests/test_config_check.py::test_prod_requires_queue_key` (and audit nearby Settings tests) so the
test proves the intended invariant regardless of ambient CI environment.

Preferred:
- explicitly clear `AETHERGATE_QUEUE_KEY` with `monkeypatch.delenv(..., raising=False)`; and
- set `_env_file=None` where appropriate.

Do not weaken the production requirement.

Audit other Settings tests for the same ambient-env contamination family and harden them if necessary.

## 5. Fix golden-path secret generation ordering

The golden workflow must not import an uninstalled package.

Preferred simplest fix:
- generate a valid Fernet key using Python standard library only:
  `base64.urlsafe_b64encode(os.urandom(32)).decode()`;
- generate password/bootstrap with `secrets`.

Alternative:
- install package dependencies before generating secrets.

Prefer the standard-library method so secret generation has no package-install prerequisite.

Ensure:
- .env is written before any Compose command;
- cleanup is safe even if setup fails before the stack starts;
- failure log/teardown steps do not mask the original failure with missing Compose env interpolation.

For cleanup, use a guard such as checking `deploy/v2/.env` exists or provide safe ephemeral env
fallbacks before invoking Compose.

Never print generated secrets.

## 6. Validate GitHub workflow reality, not just YAML syntax

After fixes are pushed:

1. wait for the workflows triggered by the FINAL origin/v2 commit to appear;
2. inspect their actual conclusions.

Required workflows:
- backend;
- frontend;
- schema-secret-gate;
- alpha-golden-path.

**All four must be `completed/success` on the final task SHA.**

Do not mark this task complete based only on:
- local YAML parsing;
- local test simulation;
- a workflow run belonging to an earlier SHA;
- "expected to pass".

If a remote run fails:
- inspect the failing job/step/log;
- root-cause it;
- fix it;
- push;
- require green runs on the new final SHA.

Do not merely rerun a deterministic code/config failure without fixing it.

## 7. CI workflow hygiene

While touching workflows:

- preserve least-privilege `contents: read`;
- preserve PR + v2 push + workflow_dispatch triggers;
- preserve timeouts/concurrency cancellation;
- make failure cleanup useful and non-destructive;
- no repository secrets are required for the offline golden path;
- do not introduce external provider/IdP dependencies.

Official action refs should remain explicitly versioned. If updating them for current runner warnings,
do so only with a known compatible stable version and document it; this is not required merely to
silence a warning.

## 8. Secret scanner / deliberate fixtures

The remote `schema-secret-gate` secret scan passed, so there is no current leaked-secret finding.

Review the local pre-commit false-positive path mentioned in the AGV2-024 report:
- one placeholder literal;
- one deliberate PEM canary.

Requirements:
- deliberate synthetic fixtures must be unmistakably synthetic;
- repository CI scanner exclusions remain narrow and explicit;
- do not broadly exclude whole source trees;
- no real secret-shaped material outside intended canary tests.

If the pre-commit hook has a documented allowlist mechanism, prefer narrowly registering these fixtures
over routine `--no-verify`.
Do not redesign hooks if that is outside the repository.

Record the decision in the handoff.

## 9. Re-run local Alpha gates

After dependency locking/CI fixes, re-run locally/nomnom:

- backend suite; baseline 561 collected;
- frontend unit/component; baseline 87;
- FULL Playwright; baseline 32;
- npm build;
- npm lint;
- `npx tsc -b --noEmit`;
- OpenAPI/client drift;
- ruff check src tests;
- git diff --check;
- secret/token/content scan;
- migration empty -> 0017;
- populated 0016 -> 0017;
- config_check;
- deterministic fake-Ollama adapter tests;
- official OpenAI Python SDK non-stream + stream with bypass=false.

If dependency locking changes package versions, re-run the clean offline golden path locally if
practical.

No migration expected.
Do not modify migrations 0001-0017.

## 10. Alpha readiness truthfulness

Update:
- docs/releases/alpha1-readiness.md;
- docs/development/agent-handoff.md;
- README/docs only if dependency installation commands change.

Record:
- why the first remote CI run was red;
- exact fixes;
- dependency lock strategy;
- final backend/frontend/Playwright counts;
- final OpenAPI drift status;
- final migration head;
- final GitHub Actions run ids/conclusions for the final SHA;
- WIP marker lifecycle.

Only describe Alpha 1 as closed once the final remote CI runs are green.

## 11. No new feature scope

Do not implement:
- Responses API;
- embeddings;
- v1 migration;
- new observability features;
- new accounting features;
- unrelated UI changes.

## Commit and Push

Suggested commits:
- `fix(ci): make alpha gates reproducible`
- `build: lock alpha python dependencies`
- `docs: record green alpha1 release gates`

Use fewer commits if cleaner.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to commit/push.

The task is complete only when:
- local Alpha regressions are green;
- generated OpenAPI is deterministic under the committed dependency lock;
- the final origin/v2 SHA has **backend, frontend, schema-secret-gate, and alpha-golden-path all green
  remotely in GitHub Actions**;
- handoff/readiness docs record the real remote run ids/conclusions;
- local `.aethergate-wip` is removed only after final remote verification.
