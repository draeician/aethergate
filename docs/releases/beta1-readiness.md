# AetherGate v2 — Beta 1 Readiness

This document records what **Beta 1** means for AetherGate v2, the exact gates
that gate it, and the GO/NO-GO checklist. Beta 1 is a **release candidate** for a
self-hosted, single-company production deployment — not a public release and not
a hosted multi-tenant service.

## Versioning

The package version is `0.1.0` (see `pyproject.toml` / `src/aethergate/__init__.py`).
There is no release-tag/SemVer-bump policy this task is required to advance, so
**no version bump is made to label Beta 1**; the status is recorded here instead.
Migration head is **`0017`**.

The runtime/dev dependency graph is locked in the committed `constraints.txt`
(consumed by CI, the Docker image, and the canonical local install). See
`docs/development/dependency-locking.md`.

## Definition of Beta 1

Beta 1 is a production deployment **release candidate** for a single company.
It may be called Beta 1 because all of the following hold, in addition to the
Alpha 1 gates recorded in `docs/releases/alpha1-readiness.md`:

- a dedicated production compose profile (`deploy/beta1/compose.yaml`) exists and
  is isolated from the dev/alpha profile (distinct project/network/volume names,
  no hardcoded container names, no host-published PostgreSQL, loopback-only API);
- the profile runs non-root with a read-only rootfs + tmpfs `/tmp`, bounded
  resources, and real liveness healthchecks (API + worker heartbeat);
- production invariants are hardcoded: `prod`, inference-auth bypass off,
  file-backed provider secrets, literal-IP-only egress with a deny-by-default
  allowlist;
- a **production configuration/deployment smoke** is deterministic and offline
  (`scripts/ci/beta_release_gate.sh`): empty DB migrates to head, prod
  `config_check` reports VALID, one-use bootstrap + literal-IP catalog + official
  SDK Models/Chat/Embeddings with bypass off, worker restart recovery, a real
  PostgreSQL outage/recovery gate (a valid request proven good first, then
  readiness `503` while down, a valid request failing closed with a `5xx`, a
  pre-outage queued request persisted and drained exactly once with no duplicate
  settlement, and a post-outage SDK request), and backup -> destructive restore
  -> post-restore SDK;
- deterministic **fault-injection recovery** tests exist at the service level
  (`tests/test_recovery.py`, `tests/test_scheduler_hardening.py`) covering worker
  restart, API restart, provider failure, PostgreSQL outage (fail-closed, no
  unguarded dispatch, and an in-flight request lost to an outage surfaces as
  `outcome_unknown` with exactly one attempt and no duplicate settlement), and
  scheduler lease/outcome recovery;
- a **live nomnom acceptance** (`scripts/ci/beta_nomnom_acceptance.sh`) proves the
  beta1 profile against the real internal Ollama on its allowlisted literal IP —
  prod `config_check` VALID, official Python SDK Models/Chat with bypass off, and
  a scoped teardown of only the disposable project (no model downloads);
- **architecture ownership** is enforced (`scripts/ci/architecture_boundaries.py`,
  wired into `schema-secret-gate.yml`) so persistence never reaches up into
  services, CLI never imports persistence, and the API never imports persistence
  models; module ownership is codified in `.github/CODEOWNERS`;
- an operator runbook (`docs/operations/beta1-runbook.md`) documents install,
  secrets, bootstrap, catalog, backup/restore, recovery, and failure modes.

## Exact gates

The four Alpha 1 CI workflows remain required and green on the final SHA:

- **backend**: Python 3.12 + PostgreSQL 16, `ruff check src tests`,
  `git diff --check`, `pytest` (including the recovery/fault-injection and
  production-compose tests), empty -> head migration, `0016 -> 0017` migration,
  `python -m aethergate.config_check`, deterministic OpenAPI generation;
- **frontend**: Node 22, `npm ci`, `npm run test`, `npm run lint`,
  `npm run build`, `npx tsc -b --noEmit`, OpenAPI/generated-client drift;
- **schema-secret-gate**: `git diff --check`, repository secret/canary scan,
  workflow YAML validation, OpenAPI/client drift, and the **architecture
  boundary** check;
- **alpha-golden-path**: full offline OIDC + browser + official SDK golden path
  with backup/restore and post-restore SDK.

A fifth, additive workflow gates Beta 1 specifically:

- **beta-release-gate**: `scripts/ci/beta_release_gate.sh` against the disposable
  `deploy/beta1` production profile with the literal-IP fake upstream.

Local/nomnom acceptance (with the Alpha 1 baselines recorded in
`docs/releases/alpha1-readiness.md`) additionally runs:

- the full backend suite via the canonical `scripts/dev/v2 test`;
- the Beta 1 release gate (`scripts/ci/beta_release_gate.sh`) end-to-end on a
  disposable stack, offline, no model downloads;
- the live Beta 1 nomnom acceptance (`scripts/ci/beta_nomnom_acceptance.sh`):
  the beta1 production profile against the real internal Ollama
  (`192.168.22.50:11434`, no downloads) — prod `config_check` VALID, official
  Python SDK Models/Chat with bypass off on the allowlisted literal IP, then a
  scoped teardown of only the disposable project.

## GO / NO-GO checklist

| Gate | Requirement | Status |
|---|---|---|
| Backend CI | `pytest` (incl. recovery + beta compose) green on final SHA | **PASS** |
| Frontend CI | build/lint/tsc/OpenAPI green on final SHA | **PASS** |
| schema-secret-gate CI | secret scan + architecture boundaries green on final SHA | **PASS** |
| alpha-golden-path CI | offline OIDC + browser + SDK golden path green on final SHA | **PASS** |
| beta-release-gate CI | disposable prod-profile smoke green on final SHA | **PASS** |
| Local backend suite | canonical `scripts/dev/v2 test` exit 0 (862 collected) | **PASS** |
| Local beta release gate | `scripts/ci/beta_release_gate.sh` end-to-end (migrate -> VALID -> SDK -> recovery -> backup/restore -> post-restore SDK) | **PASS** |
| Live nomnom acceptance | beta1 profile vs real Ollama `192.168.22.50` (prod `config_check` VALID, SDK Models/Chat, bypass off, scoped teardown) | **PASS** |
| Secret scan | no committed credentials/providers/`.env`/keys | **PASS** |
| Branch protection / CODEOWNERS review enforcement | hosted rule active on `main` | **NO-GO (operator action)** |

Every row is green on the exact final SHA except the branch-protection row, which
is an **operator action** and is recorded below as a release limitation rather
than an achieved protection. `main` has no branch protection rule today
(`gh api repos/draeician/aethergate/branches/main/protection` → `404 Branch not
protected`), so the committed CODEOWNERS file does **not** enforce review until
the operator enables a protected branch + required reviews on `main`.

## Deferred (not Beta 1)

Production-grade HA/failover, external Prometheus/OpenTelemetry/TSDB exporters,
paging/alerting, billing/invoice/payment semantics, hosted multi-company SaaS
tenancy, broad visual redesign, and provider kinds beyond Ollama. Production
secret-backend **selection** is now concrete (file backend) but other secret
backends (Vault/K8s-native) remain deferred.

The v1 -> v2 transition is a clean/destructive cutover to a fresh PostgreSQL
database (no v1 import); see `docs/migrations/v1-to-v2.md`.

## Known limitations

- One company per deployment with project boundaries; no hosted multi-tenant SaaS.
- Only the Ollama provider kind is wired to a real adapter; the adapter boundary
  is provider-neutral but additional providers are not yet exercised.
- No built-in TLS termination: the API binds loopback only and must sit behind an
  operator-managed reverse proxy.
- OIDC is optional; when enabled its issuer must be HTTPS in `prod`.
- The worker heartbeat relies on a tmpfs file refreshed on a fixed cadence from a
  dedicated event-loop task (independent of the claim/dispatch loop); a hard
  stall (not a crash) is detected after the freshness window.
- `main` has no branch protection rule and the committed CODEOWNERS file therefore
  does not enforce review until the operator enables a protected branch + required
  reviews. This is an explicit operator action, not an achieved protection.

## Remote CI status

Substantive code/test head `4fb56e06dc62d6040dbdb71e34f0e22c539d7d82` — all five
workflows `completed/success` on that exact SHA (pull_request events for the open
draft PR #5):

| Workflow | Run ID | Conclusion |
|---|---|---|
| backend | 38344290957 | completed/success |
| frontend | 38344291014 | completed/success |
| schema-secret-gate | 38344290966 | completed/success |
| alpha-golden-path | 38344290975 | completed/success |
| beta-release-gate | 38344290969 | completed/success |

The final documentation-only branch tip is also verified green on all five
workflows; its exact SHA and run IDs are recorded in PR #5's description.

Post-merge main preflight (section 0) was verified green on main merge SHA
`abce2245444efc616b617b53746ba4c4427da689` (AGV2-030 PR #4): backend `38335810551`,
frontend `38335811087`, schema-secret-gate `38335810559`, alpha-golden-path
`38335810532`.
