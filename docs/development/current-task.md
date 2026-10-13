# AetherGate — Current Task

## Task ID
AGV2-031

## Title
Beta 1 release hardening — isolated production deployment, recovery gates, architecture ownership, and release evidence

## Status and release authority
**Assigned, not implemented.** This task prepares a defensible Beta 1 release candidate; it does NOT declare Beta 1 released. The operator must approve final release/tag/cutover separately after independently reviewed CI, operational acceptance, and the post-merge main gate.

## Canonical branch
- Work ONLY on `feat/agv2-031-beta1-release-hardening`.
- This branch was created directly from canonical `main` merge SHA `abce2245444efc616b617b53746ba4c4427da689` (AGV2-030 PR #4 merged).
- Historical long-lived `v2` and the completed AGV2-030 feature branch must not be used as new development bases; never push directly to main.
- Keep one scoped task/PR; no unsolicited changes to unrelated OpenAI features.

## WIP marker — FIRST LOCAL REPOSITORY ACTION
Immediately upon entering the local repository (before checkout/pull/inspection or implementation), create gitignored root `.aethergate-wip` if it is missing, with:
- task=AGV2-031
- branch=feat/agv2-031-beta1-release-hardening
- started_at=<UTC timestamp>

Never stage/commit/push this file or store secrets in it. Leave it in place while blocked/incomplete. Remove it ONLY after all acceptance gates, final handoff, commits/push, four exact-final-SHA PR CI workflows and an open, verified PR are complete. After context compaction, reread AGENTS.md, project_spec.md, this task, and agent-handoff.md before proceeding.

## 0. REQUIRED preflight — post-merge main CI
- GitHub confirms PR #4 merged via `abce2245444efc616b617b53746ba4c4427da689` and main points to that SHA.
- AGV2-030 feature HEAD `630b3546c899ef8f7b20710447e37565319b0538` had all four green PR workflows: backend 38334919438, frontend 38334919423, schema-secret-gate 38334919416, alpha-golden-path 38334919443.
- **Post-merge main push-event CI remains NOT VERIFIED** by the architect: the available GitHub connector reports only PR-triggered runs and cannot demonstrate the four runs on main merge SHA. This is an evidence gap, NOT a declared failure or success.
- Before starting release-hardening *implementation*, use authenticated GitHub Actions UI or gh/API with appropriate access to verify that backend, frontend, schema-secret-gate, and alpha-golden-path all completed/success with head_sha **exactly** `abce2245444efc616b617b53746ba4c4427da689`. Record exact run IDs and the evidence mechanism in agent-handoff.md. If any is red, root-cause and repair within a scoped change before continuing. If the check is inaccessible, record a truthful release hold and do not imply success.
- The **branch and these assignment docs** may be prepared before that external CI evidence is available; this exception does not waive the release preflight.

## Starting state — factual baseline
- Merged core: Models, Chat Completions text/stream/inline vision, Responses text/inline vision subset, Embeddings; one real supported provider kind (Ollama via pinned LiteLLM 1.104.0).
- Durable PostgreSQL scheduler, quota/admission, signed accounting ledger and budgets, project/RBAC identity, OIDC browser auth, Linux CLI/device flow, admin API and web console, offline golden-path plus backup/restore.
- AGV2-030 closed production file-backed provider-secret selection, literal-IP-only production upstream destinations, redirect and implicit proxy escape paths; successful reported disposable live nomnom SDK Models/Chat/vision/embedding proof.
- Migration head **0017**. No schema change is anticipated for this release-hardening task.
- Last verified AGV2-030 reference baseline: backend 833 tests collected / exit 0; frontend 89 passed; full local Playwright 32 passed / 7 skipped; offline CI browser golden 7 passed.
- Current `deploy/v2/compose.yaml` is Alpha/dev-oriented; it hardcodes development env/container names and has no Beta production profile. `.github/CODEOWNERS` does not exist as of the baseline. `docs/releases/beta1-readiness.md` does not exist.
- Read `project_spec.md`, `AGENTS.md`, `docs/development/agent-workstreams.md`, `docs/architecture/v2-overview.md`, `docs/architecture/security.md`, `docs/operations/alpha-runbook.md`, `docs/releases/alpha1-readiness.md` before design/implementation.

## 1. Product target / definition of done
Deliver a reproducible, least-privilege, single-company/self-hosted Beta 1 **release candidate**, with a documented install/upgrade/restore/rollback procedure and repeatable failure/recovery evidence. The release decision must be traceable to exact git SHAs, CI run IDs, tests and a disposable production-policy nomnom acceptance run. Correctness of scheduler/accounting/security wins over uptime claims, UI polish or throughput.

## 2. Production deployment profile (primary implementation scope)
Provide a clearly named, isolated Beta production Compose deployment/profile (prefer `deploy/beta1/` unless a more robust repo-native layout is demonstrated) and operator-facing sample configuration **without** committing any live secrets.

Mandatory invariants:
- `AETHERGATE_ENV=prod`; `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`; `AETHERGATE_SECRET_BACKEND=file` with existing absolute `AETHERGATE_SECRET_DIRECTORY` and read-only externally projected files; no production env secret backend or env fallback.
- `AETHERGATE_UPSTREAM_ALLOWLIST` explicit literal private/public IPs as approved; strict literal-IP production policy and no redirect or implicit proxy escape. No new bypass switches. FileSecretResolver requires no fake credential for anonymous real Ollama.
- PostgreSQL not host-published; API not publicly exposed over plaintext HTTP. Provide loopback-only API binding or an explicitly documented trusted TLS-terminating reverse-proxy integration. Never include the deterministic development IdP/fake provider in the production profile. Document TLS and real-issuer responsibility; do not falsely claim TLS exists if operator-managed.
- Distinct Compose project, networks, volumes, container names and host ports from long-lived `aethergate-v2` dev or any existing nomnom services. Avoid hardcoded `container_name` collisions; use explicit disposable project identifiers for acceptance. No `down -v` on shared resources.
- Run containers with minimum needed privileges/non-root where supported; bounded resources; reproducible pinned application/dependency images; real API/worker health checks and unambiguous readiness. If read-only rootfs/tmpfs controls are practical, test them; document any exception.
- Separate migration step and credentialed bootstrap lifecycle; no auto-destructive database operations or insecure pre-seeded credentials on app startup.
- Fail closed for invalid configuration or absent mounts/keys and verify the actual worker/runtime uses production settings (not just the config-check command). Do not echo secrets in compose output, diagnostics or logs.
- Test production configuration shape deterministically in CI, including negative/invalid-prod cases. Avoid installing kubernetes/Helm/cloud stacks for this milestone.

## 3. Recovery, resilience and safe fault-injection release gates
Implement **automated, repeatable release checks** on a disposable stack, using deterministic barriers/fixture hooks rather than arbitrary timing sleeps. Extend existing `scripts/ci/golden_path.py`, operations tooling, or a clearly named Beta CI check without weakening established tests. At minimum prove and record:

1. Worker termination/restart while requests are queued/in progress: queued work is not lost; leases expire/recover according to the documented state machine; ambiguous upstream execution remains `outcome_unknown` where appropriate, not silently re-dispatched or billed twice.
2. API restart while work exists: durable persisted work and authentication/config state survive; no phantom concurrency/quotas/budget reservations.
3. Database outage and recovery: readiness goes non-ready / requests fail safely; no fresh unguarded dispatch, corrupt admission, or silent data loss; recovery uses documented operations.
4. Provider HTTP timeout/connection failure/429, and redirected/denied upstream: safe errors/cooldowns, no hidden LiteLLM retry bypass, quota and budget invariants preserved.
5. Backup -> destructive restore **only on disposable database** -> migration head 0017 -> post-restore official Python and JS SDK golden smoke, appropriate audit/accounting state checks.
6. Lost/unavailable file-projected provider secret and queue-key unavailability: fail closed without leaking data or minting synthetic success; document queue-key-loss unrecoverability and operator restore procedure.

Use faithful domain behavior for cancellations, reservations, settlement, manual reconciliation, and audit. Do not claim external upstream execution is exactly-once when state cannot establish it. Where product limits make a scenario unsupportable, write down the evidence-backed limitation rather than concealing it.

## 4. Architecture boundaries and CODEOWNERS
- `docs/development/agent-workstreams.md` asserts dependency-direction enforcement by architecture tests and CODEOWNERS, but no CODEOWNERS file exists at baseline. Add `.github/CODEOWNERS` with **actual verified repository owner(s)** (the known current owner is `@draeician`); do not invent organizations, teams or reviewers. Map bounded areas to declared code ownership, or explicitly use a minimal safe owner mapping until real teams are appointed.
- Add focused, executable architectural boundary checks (Python AST/import or equivalent) proving routers/CLI do not host persistence policy and that dependencies follow the documented direction where mechanically enforceable. Prefer ratcheted checks and defined exception evidence over global imports that falsely fail or ignore known cases.
- Document how GitHub branch protection/CODEOWNERS review enforcement is configured and verify the *actual hosted setting* where access permits. Merely checking in CODEOWNERS does not itself enforce review approvals. If enforcement cannot be inspected/activated, mark it an explicit operator action and release limitation, not as an achieved protection.
- Preserve workstream ownership and contracts; no unrelated structural refactor or new public API shape.

## 5. Beta 1 operator docs / release evidence
Deliver:
- `docs/operations/beta1-runbook.md` with preflight, isolated install, TLS/auth issuer prerequisites, secrets projection/rotation, API + worker startup, migration, rollback/restore, health, observability/diagnostics, recovery handling, and safe teardown.
- `docs/releases/beta1-readiness.md` with a checkable GO/NO-GO matrix: code/contract/support scope, security, deployment isolation, auth/egress, backup+restore, fault/recovery, exact-SHA CI, live acceptance, key-loss limitations, branch protections, known limitations, rollback, and operator signoff.
- Truthfully update `README.md` and relevant developer/architecture docs only where new release behavior requires it; retain `docs/releases/alpha1-readiness.md` as historical evidence (no retroactive rewriting).
- Explicitly state Beta 1 **does not mean** high availability, full OpenAI Responses, new providers, complete billing, SaaS tenancy, cloud secret-manager integrations, or performance SLOs invented without measurement.
- Decide version/tag/release publication **only by an explicit operator-approved policy** after post-merge green CI. Do not tag, publish container images, or deploy public production automatically.

## 6. CI / regression requirements
Keep four established GitHub workflows, and add targeted release-hardening verification to the appropriate CI route. All required checks must be pass/fail actionable, with safe/short operational logs; no manual-only success substituted for deterministic CI.

Re-run:
- Full backend suite (>= reference 833 tests, accounting for legitimate test updates), ruff, full frontend unit suite (reference 89), lint/build/TypeScript.
- Playwright local full suite and CI golden path (reference 7 CI browser tests), official pinned Python and JS SDK supported API surface.
- Strict secrets/content canary scan, no-legacy guard + self-test, `git diff --check`, OpenAPI/client two-pass drift, workflow syntax validation.
- Clean migration empty -> 0017 and populated 0016 -> 0017, and **no new migration** unless separately justified and approved.
- Deterministic production configuration/deployment smoke and fault-injection recovery tests in addition to AGV2-030 file-secret/egress/redirect/proxy tests.
- Disposable nomnom (real Ollama models already installed, **no downloads**): at minimum official Python SDK Models/Chat with auth bypass off on the real allowlisted literal-IP upstream; vision/embedding where available. Test production-policy startup and teardown. Do not interfere with other nomnom/developer services.
- Preserve reproducibility from `constraints.txt`, package versions, and existing lock mechanisms; do not silently upgrade pinned LiteLLM/OpenAI SDKs.

On the **EXACT FINAL task branch SHA** require four completed/success runs with that SHA (backend, frontend, schema-secret-gate, alpha-golden-path), plus any new release-gate workflow. Create/update an open draft PR to main so `pull_request` workflows run; the existing branch `push` workflow filters historically include main/v2 only. No false attribution of a branch-push run.

## 7. No-scope / release safety
NO unrelated new endpoints, provider kinds, Responses tools/MCP/files, audio/image generation, HA cluster, Redis authority, multi-tenant SaaS, cloud secret-manager SDK matrix, general UI redesign, or data import from retired v1. Do not damage data or services outside disposable test environments. Do not change migrations, auth/audit/quotas/security semantics to force tests green.

## 8. Handoff and PR policy
- Record exact verified `main` post-merge CI preflight or explicit blocker; implementation commits, regression counts, safe command/evidence summaries, fault/recovery results, nomnom proof, known limitations and exact final SHA/run IDs in `docs/development/agent-handoff.md`.
- Push scoped conventional commits to `origin/feat/agv2-031-beta1-release-hardening`. Open or update one draft/feature PR `feat/agv2-031-beta1-release-hardening -> main`, with scope, risk, proof, missing operator actions, final exact SHA and all run IDs. Never merge it automatically.
- Keep `.aethergate-wip` until all completion conditions are satisfied, the handoff is pushed and remote PR head/CI are verified; leave it in place on a block.
- At final handoff, state **ready for independent review** rather than claiming Beta 1 publicly shipped. After reviewer-approved merge and four successful post-merge main workflows, the operator decides GO/NO-GO for Beta 1.

## Assignment disposition
This branch and task are **prepared**. The next actor is OpenCode on nomnom, with ChatGPT as independent architect/reviewer. Do not begin release implementation unless section 0 post-merge CI is positively verified and captured.
