# AGV2-032 — LAN/Tailscale access, model discovery, admin & thinking controls (2026-10-15 UTC)

Branch `feat/agv2-032-lan-tailscale-admin-thinking`, from post-AGV2-031 `origin/main`
`79838ec65a46a8c6936ca0176330507bf2155984`. PR open for independent review, **unmerged**; no tag,
publication, or release. WIP marker `.aethergate-wip` (task `AGV2-032`).

## Delivered

- **Per-route thinking policy** (migration `0018`, reversible): `RouteBinding.thinking_policy`
  (`inherit`/`off`/`on`/`low`/`medium`/`high`, default `inherit`) + CHECK constraint. New module
  `src/aethergate/thinking.py`. Resolved at dispatch (`InferenceService._build_request`) into
  upstream `reasoning_effort`; **explicit administrative policy always overrides client-supplied
  `reasoning_effort`** (clients cannot change routing). `low`/`medium`/`high` are rejected at
  configuration time (`catalog/admin.py`) unless the upstream can represent a distinct level — the
  pinned LiteLLM Ollama transport forwards a level only for `gpt-oss`, so non-`gpt-oss` Ollama
  accepts only `inherit`/`off`/`on`. Contracts `RouteBindingCreate/Read/Update`; router pass-through;
  scheduler payload round-trips `reasoning_effort` (legacy payloads -> None). Responses `reasoning`
  remains explicitly rejected (documented scope decision).
- **Model discovery:** additive `capabilities: list[str]` on the OpenAI `/v1/models` `ModelObject`
  (`api/openai_models.py`); no routing/endpoint/provider leak.
- **Admin CLI:** `create-provider-account`, `create-endpoint`, `create-model-alias`,
  `create-route-binding` (`--thinking-policy`), `update-route-binding`, `credentials create`.
- **Access front-end:** `scripts/ops/beta1-access` (install/status/remove) provisions a socat
  `systemd --user` unit for `<LAN_IP>:8088 -> 127.0.0.1:<api>` and `tailscale serve --bg --https=443`
  for the MagicDNS name; `docs/operations/beta1-access.md`. The API container stays bound to
  `127.0.0.1`; Funnel is never enabled. `deploy/beta1/compose.yaml` honours
  `AETHERGATE_API_HOST_PORT` (documented in `deploy/beta1/env.example`) for a stable upstream.
- OpenAPI + TS client regenerated; frontend `RoutesPage` gains a thinking-policy selector/column.
- Docs updated: `docs/architecture/provider-model.md`, `docs/development/README.md`,
  `docs/operations/beta1-runbook.md` (head `0018`), `docs/releases/beta1-readiness.md`.

## Verification (local, before PR)

- `scripts/dev/v2 test` -> **900 passed** (baseline 865); `ruff check src tests` clean;
  `git diff --check` clean; `scripts/ci/secret_scan.sh` clean.
- Frontend: `npm run lint` clean, `npx tsc -b --noEmit` clean, `npm run test` -> **89 passed**,
  `npm run build` green.
- Isolated `scripts/ci/beta_release_gate.sh` (production profile + fake Ollama) -> **EXIT 0**: empty
  DB -> `0018`, config_check VALID, SDK Models/Chat/Embeddings, worker restart, PostgreSQL outage
  fail-closed with queued work preserved and drained exactly once, backup -> destructive restore ->
  `0018` -> post-restore SDK.

## Live acceptance on nomnom (`aethergate-beta1`)

- Migrated live DB `0017 -> 0018`; recreated api/worker from `aethergate-beta1:local`;
  `/health/ready` 200; api stays on `127.0.0.1:39549` (`AETHERGATE_API_HOST_PORT=39549`).
- `scripts/ops/beta1-access install` -> LAN listener `http://192.168.22.50:8088` active (401 without
  key, 200 with the inference key). `/v1/models` now reports capabilities
  (`nomic-embed`=embedding, `qwen3-2b`=text, `qwen3-vl`=text+image).
- Reproduced the `inherit` reasoning leak on `qwen3-2b` (`...\n</think>\n\n4`), then set its route
  binding (`39a1519d4cad4e93a612922ca721efcf`) `thinking_policy=off`:
  - non-stream `'4'`; `reasoning_effort="high"` from the client still `'4'` (administrative wins);
  - stream `'4'`; persisted across api+worker restart; head still `0018`; verified over the LAN IP.
  - `PATCH ... {"thinking_policy":"low"}` -> **HTTP 400** `invalid_request` ("level is not supported
    by this route's upstream; use 'inherit', 'off', or 'on'"); policy stayed `off`.
  - `nomic-embed` embeddings returned 768 dims.

## Operator-only blocker

Tailscale Serve is **not enabled on the tailnet** (`https://login.tailscale.com/f/serve?node=nwjx2TALce11CNTRL`).
`tailscale serve` prints the enable link and does not configure; the tailnet HTTPS listener
(`https://nomnom.tail5750dd.ts.net/v1`) activates once the operator enables Serve and re-runs
`scripts/ops/beta1-access install`. LAN access is unaffected.

## Outstanding / limitations

- No second physical client exercised live; loopback + own-LAN-IP + MagicDNS resolution were used.
- Level policies (`low`/`medium`/`high`) are enforced and unit-tested but not live-verified: no
  `gpt-oss` Ollama model is present on the pilot.
- The frontend has no dedicated UI test for the new thinking-policy selector beyond the updated
  fixtures; backend precedence/validation is covered in `tests/test_thinking.py`,
  `tests/test_catalog_admin.py`, `tests/test_inference_boundary.py`, `tests/test_adapter.py`.

---

# AGV2-031-PM1 — post-merge Beta gate correction — complete (2026-10-15 UTC)

Corrects the post-merge `main` Beta release-gate regression. Branch
`fix/agv2-031-postmerge-beta-gate`, draft PR **#6** (open, **unmerged**); no
Beta 1 tag/publication/deployment.

## Reproduction — failing post-merge run 38681868099

Push-event `beta-release-gate` `38681868099` (job `116247985882`) failed in the
disposable outage gate: a request durably queued with the worker stopped
(`queued_total=1`) was **gone** after PostgreSQL stop/restart —
`beta_db_outage.py --stage verify-persisted` → *persisted queued request was
lost during the outage*.

## Root cause (verified)

The OpenAI endpoints cancelled durably admitted work whenever their terminal
waiter raised — a `finally: if result is None: _schedule_durable_cancel(...)`.
While PostgreSQL was unreachable the API-side `wait_for_terminal` poll raised
`OperationalError`; `result` stayed `None` and the detached cancellation task
then durably cancelled the **QUEUED** request. A transient server-side database
error is **not** a client disconnect.

The loss is a **race** on the detached cancel task's DB connection: it only
destroys the request if it acquires a connection *after* PostgreSQL restarts
(while the DB is down `request_cancellation` fails and is swallowed). This is why
the failure reproduced on the `main` push run while the PR-event run on the same
code (`0d6e077`, run `38682249095`) passed the outage gate — the earlier green
runs did **not** clear the defect. The fix removes the race.

## Fix

Only cancel on response-cycle cancellation (client disconnect / server
shutdown), never on a transient server error:
- non-streaming `chat`/`responses`/`embeddings` waiters replaced
  `finally: if result is None: cancel` with
  `except asyncio.CancelledError: cancel; raise` — an `OperationalError` now
  propagates (real `5xx`) while the request stays `QUEUED`;
- `chat`/`responses` SSE generators cancel only on a directly-detected
  `is_disconnected()`, `asyncio.CancelledError`, or `GeneratorExit`; a stream
  broken by a server error no longer cancels the request.

`scripts/ci/beta_db_outage.py` failure diagnostics now include the observed
snapshot and `counts_by_state` (no secrets), naming the terminal state that
replaced `QUEUED`.

## Red-before / green-after evidence

New deterministic tests in `tests/test_recovery.py`:
- `test_api_db_error_waiter_preserves_queued_work` — **red before** (request went
  `cancelled`), **green after** (`queued`, claimable, drains to `succeeded`);
- `test_api_stream_db_error_preserves_queued_work` — red before / green after;
- `test_api_client_disconnect_still_cancels_queued_work` — client disconnect still
  durably cancels (green both before and after; cancellation semantics preserved);
- the existing `test_stream_disconnect_cancels_queued_request` stays green.

Run against the unfixed image, the two server-error tests failed; against the
fixed image all pass.

## Operational acceptance

`scripts/ci/beta_release_gate.sh` run **3×** on fresh disposable projects, all
`EXIT=0`. Each outage section observed: pre-outage `200`; barrier
`queued_total=1`; readiness `503`; denied valid request `HTTP 500`; **persisted
`queued_total=1`**; drained `counts_by_state={"succeeded":5}` with
usage/ledger/attempts each `+1`; post-outage `200`.

## Regression counts

- backend: **865 collected / exit 0** via canonical `scripts/dev/v2 test`
  (862 + 3 new); `ruff check src tests` clean; `git diff --check` clean;
  `scripts/ci/secret_scan.sh` clean; `architecture_boundaries.py` OK;
  `no_legacy_v1.sh` clean; OpenAPI generation no drift (frontend
  `generated/openapi.json`); frontend lint / 89 tests / build / `tsc -b` clean.

## Commits

- `ce68e6c` fix(api): preserve durably admitted work on transient DB errors

## Final remote CI (corrected head `ce68e6c664bdca4cc3b0ad89b9189a762b774beb`)

All five PR-event workflows `completed/success` on the exact head:
- backend — `38685560485`
- frontend — `38685560410`
- schema-secret-gate — `38685560412`
- alpha-golden-path — `38685560439`
- beta-release-gate — `38685560416`

The final documentation-only tip (this handoff commit) is re-verified green on all
five workflows; its exact SHA and run IDs are recorded in PR #6's description.

## Operator actions / limitations

- `main` branch protection / enforced independent review remains an operator
  action; do not treat CODEOWNERS as enforcement.
- No Beta 1 tag/publication/deployment; the operator decides GO/NO-GO after
  review, merge, and green post-merge `main` CI.
- The gate's previous green PR-event runs were racy; the deterministic regression
  tests (not the gate alone) are the authoritative red-before proof.

---

# AGV2-031 post-merge CI failure — AGV2-031-PM1 kickoff (superseded above)

- PR #5 was operator-authorized and successfully merged to canonical `main` at `5695637e821e8c4dd33051e3d937cf3b6c88c628`, preserving previous AGV2-031 code head `d3319091a91baabe8f29adeec023dc584d5626f2`.
- On this exact `main` merge SHA, push-event `beta-release-gate` workflow **38681868099** FAILED (job `116247985882`): verified queued request with worker stopped `queued_total=1`, PostgreSQL outage (readiness 503, valid request HTTP 500), then `beta_db_outage.py --stage verify-persisted` failed: **persisted queued request was lost during the outage**. This was not seen on the green feature-head PR CI. Link: https://github.com/draeician/aethergate/actions/runs/38681868099.
- Two push workflows succeeded (frontend 38681868242, schema-secret-gate 38681868088). Backend 38681868122 and alpha-golden-path 38681868090 were still running at last check. Check their final results rather than assuming.
- Do not auto-merge corrective PR. Repo `main` branch protection remains unconfigured as last verified.

---

# AGV2-031 DB outage gate correction — complete (2026-10-13 UTC)

The additional independent-review finding (PR #5, 2026-10-13T13:30Z) is
**resolved** on the corrected substantive head
`d979d7e8b1662792145135e6f1fa4b7b5022a33d`. PR #5 remains open/draft/**unmerged**.
No release/tag/publication; no duplicate ntfy alert.

## Finding: vacuous DB outage proof

The old `scripts/ci/beta_release_gate.sh` outage step POSTed with
`Authorization: Bearer outage-probe` (not a real issued credential) and accepted
`401`/`500`/`503`/connection failure as "denied". An invalid key can be rejected
even with a healthy database, so it did not prove that a **valid** request fails
closed *because PostgreSQL is unavailable*, and it arranged no preexisting
queued/in-flight work.

## Correction

`scripts/ci/beta_release_gate.sh` now:
- reads the real persisted `AETHERGATE_BETA_INFERENCE_KEY` /
  `AETHERGATE_BETA_ADMIN_KEY` from the mode-0600 `$WORKDIR/beta_secrets.env`
  (value never printed or logged);
- proves a valid request returns `200` **before** the outage;
- stops the worker, durably enqueues one request (barrier: `queued_total == 1`),
  then stops PostgreSQL and asserts readiness `503` and the valid request fails
  closed with a `5xx` (explicitly rejecting vacuous `200`/`201`/`401`/`403`/`000`);
- restores PostgreSQL and asserts the queued request persisted with **no** new
  usage/ledger/attempt records from the denied request;
- restarts the worker and asserts the queued request drains **exactly once**
  (usage/ledger/attempts each `+1`, zero `outcome_unknown`), then a post-outage
  valid request returns `200`.

New helper `scripts/ci/beta_db_outage.py` performs the staged snapshots and
assertions over the admin API (`queue/requests`, `queue/summary`,
`usage-records`, `ledger-entries`, `observability/summary`).

Focused regression: `test_database_outage_during_dispatch_no_double_settlement`
in `tests/test_recovery.py` — an in-flight request lost to a DB outage recovers
as `OUTCOME_UNKNOWN` with exactly one execution attempt, zero usage records and
zero ledger entries (never re-dispatched or billed twice).

## Verification

- Local operational acceptance: `scripts/ci/beta_release_gate.sh` end-to-end
  `EXIT=0`; the outage section observed pre-outage `200`, queued barrier
  `queued_total=1`, readiness `503`, denied request `500`, persisted
  `queued_total=1` with no denied-request records, drained `+1` usage/ledger/
  attempt, post-outage `200`.
- backend suite: **862 collected / exit 0** via canonical `scripts/dev/v2 test`;
  `ruff check src tests` clean; `git diff --check` clean; secret scan clean.

## Final remote CI (corrected substantive head `d979d7e8b1662792145135e6f1fa4b7b5022a33d`)

All five workflows `completed/success` on the exact head (pull_request events for PR #5):
- backend — `38413350247`
- frontend — `38413351513`
- schema-secret-gate — `38413350479`
- alpha-golden-path — `38413350025`
- beta-release-gate — `38413350285`

The final documentation-only tip (this handoff commit) is re-verified green on all
five workflows; its exact SHA and run IDs are recorded in PR #5's description.

## Corrective commit

- `d979d7e` fix(release): prove DB outage fails closed with a real credential

## Operator actions (unchanged release NO-GO)

- `main` branch protection / independent CODEOWNERS review enforcement remains
  NO-GO until the operator configures and verifies a protected branch + required
  independent reviews.
- The original ntfy alert was delivered prematurely and is **not resent**; no
  duplicate alert for AGV2-031.

---

# AGV2-031 four-finding corrective closure (superseded by the DB outage correction above)

The independent-review HOLD below is **resolved**. All four findings are closed
on the substantive head `cf84cfbbd789071e2b0a5b6001f4efed21f5e270`; PR #5 is
open/draft and **unmerged**. No release/tag/publication is authorized.

## Finding-by-finding closure

1. **Worker liveness false negative.** `src/aethergate/worker.py` now refreshes
   the heartbeat file from a dedicated event-loop task (`_heartbeat_loop`) on a
   fixed cadence, independent of the claim/dispatch loop, so a legitimate
   >90-second dispatch (120-second default timeout) no longer marks a busy but
   healthy worker stale. A genuinely hung (CPU-blocked) worker also starves the
   task, so a real stall is still detected. Deterministic tests:
   `test_heartbeat_refreshes_during_long_dispatch` and
   `test_heartbeat_clean_stop_and_restart_without_leak` in `tests/test_worker.py`.
   The `docker restart` false-unhealthy root cause (heartbeat going stale during
   graceful in-flight shutdown) is resolved by the background task; the runbook
   retains `stop`+`start` as the recommended restart form.

2. **Acceptance-script failure cleanup / collision safety.** Both
   `scripts/ci/beta_release_gate.sh` and `scripts/ci/beta_nomnom_acceptance.sh`
   now fail closed before any build/up when the fixed compose project name
   already owns containers/networks/volumes, and trap EXIT/INT/TERM to `down -v`
   only the project this run created (a `STARTED` ownership flag). Deterministic
   fake-docker collision regression tests in `tests/test_beta1_scripts.py`.

3. **Architecture check bypass.** `scripts/ci/architecture_boundaries.py`
   `_imported_names` now resolves `ast.ImportFrom.level` against the importing
   package, so `from ..persistence import models` resolves to
   `aethergate.persistence.models` and is flagged. Red-before/green-after
   fixtures in `tests/test_architecture_boundaries.py` (direct + relative +
   `from . import ...` + persistence->higher-layer escapes); the current tree
   still passes (ratchet).

4. **Explicit database outage/recovery release gate.** Added a deterministic
   disposable-stack PostgreSQL outage/recovery step to
   `scripts/ci/beta_release_gate.sh` (readiness `503` while down, requests not
   admitted, recovery, post-outage SDK smoke) and a service-level
   `test_database_outage_fails_closed_without_dispatch` in `tests/test_recovery.py`
   proving `claim_and_reserve`/`admit_and_enqueue` raise `OperationalError` with
   zero upstream dispatch. Runbook + readiness docs updated.

## Corrective implementation commits
- `63db2c0` fix(release): keep worker heartbeat fresh during long-running dispatch
- `c5b31f1` fix(release): resolve relative imports in architecture boundary check
- `50b95d6` fix(release): add failure teardown, collision guards and DB outage gate to beta scripts
- `b91e975` test(release): add deterministic DB outage test and update Beta docs
- `cf84cfb` fix(release): ship ci/ops scripts in image for containerized test workflow

## Regression counts
- backend: **861 collected / exit 0** via canonical `scripts/dev/v2 test`
  (baseline 846 + 15 new); `ruff check src tests` clean; `git diff --check`
  clean; `scripts/ci/secret_scan.sh` clean. Frontend untouched (89 passed).

## Final remote CI (substantive head `cf84cfbbd789071e2b0a5b6001f4efed21f5e270`)
All five workflows `completed/success` on the exact head (pull_request events for PR #5):
- backend — `38402826381`
- frontend — `38402826599`
- schema-secret-gate — `38402826502`
- alpha-golden-path — `38402826700`
- beta-release-gate — `38402830108`

The final documentation-only tip (this handoff commit) is re-verified green on all
five workflows; its exact SHA and run IDs are recorded in PR #5's description.

## Operator actions (unchanged release NO-GO)
- `main` branch protection / independent CODEOWNERS review enforcement remains
  NO-GO until the operator configures and verifies a protected branch + required
  reviews. Recorded as a limitation, not an achieved protection.
- No release/tag/publication. The operator decides GO/NO-GO after reviewer-approved
  merge and post-merge main CI.
- The original ntfy alert was delivered prematurely and is **not resent**.

---

# AGV2-031 independent reviewer HOLD — 2026-10-13

- The previous `ee7fbec7857dd2459d1b1c4a1fee6a5d464fb5c6` feature SHA
  passed all five PR CI workflows. The live nomnom proof is recorded in the
  handoff below (not replayed by the remote reviewer). PR #5 is open/draft,
  mergeable and unmerged.
- Four corrective release-hardening issues remain, detailed in
  `docs/development/current-task.md` under **Independent reviewer HOLD**:
  worker heartbeat falsely becomes stale during a long real dispatch; local
  acceptance scripts omit failure-path teardown and project-collision guards;
  the architecture test does not resolve relative Python imports; and the
  required real PostgreSQL outage/recovery safety gate is not explicitly
  exercised by the new recovery tests or release script.
- `main` branch protection and independent review enforcement remain
  operator NO-GO actions. No release/tag or automatic merge is authorized.
- The original ntfy notification has been acknowledged once, but it was
  sent before final live proof and before independent review. Do not
  automatically send another alert; operator can explicitly request a
  corrected final notification.
- Resume the original AGV2-031 per-task branch, preserve/recreate the
  local gitignored WIP marker, close the focused defects and all remaining
  release evidence gaps, then obtain five green PR CI workflows on the
  corrected **exact SHA**, update PR #5 and re-request independent review.
  The older completion record below is retained as historical evidence,
  **not** as final release approval.

---

## AGV2-031 operator completion alert (requested; NOT SENT)

The operator explicitly requests an urgent ntfy.sh phone alert **only when
AGV2-031 is complete and ready for independent review**. The exact curl
command, safe payload, URL, headers and release-gate trigger are now canonical
in `docs/development/current-task.md`, section 9 on the AGV2-031 task branch.

**No alert is due yet.** An open draft PR or initial CI passes do not satisfy
the completion trigger. At final completion, send once, verify delivery,
and record the delivery result in a PR comment and final response. Do not
send credentials or other sensitive details.

---

# AGV2-031 complete — ready for independent review (2026-10-13 UTC)

## Status
Beta 1 release hardening is **implemented and verified**. This is a release
*candidate*, not a shipped release, tag, or public deployment. Branch
`feat/agv2-031-beta1-release-hardening`, substantive code/test head
`4fb56e06dc62d6040dbdb71e34f0e22c539d7d82`, open draft PR **#5** (unmerged). All
five PR workflows are green on the substantive head and on the final
documentation-only tip; the final tip SHA and its five run IDs are recorded in
PR #5's description. `.aethergate-wip` removed.

## Section 0 preflight — post-merge main CI (verified)
Green on main merge SHA `abce2245444efc616b617b53746ba4c4427da689` (AGV2-030 PR #4):
backend `38335810551`, frontend `38335811087`, schema-secret-gate `38335810559`,
alpha-golden-path `38335810532` (evidence mechanism: `gh run view`/API).

## Implementation commits
- `9fea250` feat(release): add Beta 1 production profile and worker heartbeat healthcheck
- `121d23a` feat(release): add recovery fault-injection tests and architecture boundary gate
- `4fb56e0` feat(release): add Beta 1 release gate smoke and operator docs

## Deliverables
- `deploy/beta1/compose.yaml` (prod profile: postgres+api+worker, non-root,
  read-only rootfs+tmpfs, loopback-only API, hardcoded prod invariants),
  `deploy/beta1/env.example`, `deploy/beta1/compose.smoke.yaml` (CI-only fake
  upstream on a literal IP). No hardcoded `container_name`; project-scoped naming.
- Worker heartbeat: `AETHERGATE_WORKER_HEARTBEAT_FILE` in `config.py`, heartbeat
  write in `worker.run`, and `src/aethergate/worker_healthcheck.py`.
- `deploy/v2/Dockerfile` now ships the beta1 compose + env template.
- Tests: `tests/test_beta1_compose.py` (10 shape/prod-invariant tests),
  `tests/test_recovery.py` (3 worker/API restart + provider-failure recovery),
  plus the existing `tests/test_scheduler_hardening.py` (20).
- `scripts/ci/architecture_boundaries.py` (stdlib AST; persistence never reaches
  up, CLI never imports persistence, routers never import raw ORM models), wired
  into `schema-secret-gate.yml`; `.github/CODEOWNERS` maps bounded areas to
  `@draeician`.
- `scripts/ci/beta_smoke.py` + `scripts/ci/beta_release_gate.sh` +
  `.github/workflows/beta-release-gate.yml`: disposable production release gate
  (migrate -> prod `config_check` VALID -> one-use bootstrap + literal-IP catalog
  + official SDK Models/Chat/Embeddings with bypass off -> worker restart recovery
  -> backup -> destructive restore -> head 0017 -> post-restore SDK -> teardown).
- `docs/operations/beta1-runbook.md`, `docs/releases/beta1-readiness.md`.

## Regression counts
- backend: **846 collected / exit 0** (baseline 833 + 13 new) via canonical
  `scripts/dev/v2 test`; `ruff check src tests` clean; `git diff --check` clean;
  `scripts/ci/secret_scan.sh` clean.
- Local `scripts/ci/beta_release_gate.sh` end-to-end pass on a disposable stack
  (offline, no model downloads): migration empty -> `0017`, prod `config_check`
  VALID, Python SDK Models/Chat(non-stream+stream)/Embeddings, worker stop/start
  recovery, backup/restore `0017`, post-restore SDK with the same credentials.
- Note: a single `docker restart <worker>` does not reset the container
  healthcheck state machine and can report a false `unhealthy` after a clean
  SIGTERM shutdown even though the worker is live; `docker compose stop` + `start`
  recovers cleanly. Documented in the runbook.

## Final remote CI (substantive head `4fb56e06dc62d6040dbdb71e34f0e22c539d7d82`)
All five workflows `completed/success` (pull_request events for PR #5):
- backend — `38344290957`
- frontend — `38344291014`
- schema-secret-gate — `38344290966`
- alpha-golden-path — `38344290975`
- beta-release-gate — `38344290969`

The final documentation-only tip (this handoff commit and its run IDs) is also
verified green on all five workflows; its exact SHA and run IDs are recorded in
PR #5's description.

## Live nomnom proof — COMPLETE (2026-10-13 UTC)
Disposable production-policy stack (`ag-beta1-nomnom`, ephemeral loopback API),
torn down afterward; the long-lived dev stack was not touched.

- Real internal Ollama at the allowlisted literal IP `192.168.22.50:11434`
  (installed models inspected; no downloads).
- beta1 production profile (`deploy/beta1/compose.yaml`) with `AETHERGATE_ENV=prod`,
  `AETHERGATE_UPSTREAM_ALLOWLIST=192.168.22.50`, `AETHERGATE_SECRET_BACKEND=file`
  + read-only empty secret dir, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`,
  `AETHERGATE_EGRESS_REQUIRE_LITERAL_IP=true`, generated queue key + bootstrap
  token. `config_check` => VALID; migrations `0001 -> 0017` clean.
- Official Python SDK (`openai==2.54.0`) with bypass off: `models.list()`
  returns the three public aliases; `chat.completions.create` non-stream and
  stream return non-empty content against `qwen3.8-2b-distill:Q6_K` on the
  allowlisted literal IP.
- Repeatable via `scripts/ci/beta_nomnom_acceptance.sh` (the offline CI gate uses
  the deterministic fake Ollama; this live gate uses the real internal Ollama).
- Tear-down: only the disposable `ag-beta1-nomnom` project removed (`down -v`);
  no unrelated services touched.

## Known limitations / explicit operator actions
- **Branch protection not enabled.** `gh api
  repos/draeician/aethergate/branches/main/protection` returns `404 Branch not
  protected`, so the committed CODEOWNERS file does **not** enforce review until
  the operator enables a protected branch + required reviews on `main`. Recorded
  as a release limitation, not an achieved protection.
- No release tag/publication; the operator decides GO/NO-GO for Beta 1 after
  reviewer-approved merge and post-merge main CI.
- OIDC is optional and its issuer must be HTTPS in `prod`; TLS termination is
  operator-managed (API binds loopback only).

## Recommended next step
Independent review of PR **#5**; do **not** merge automatically. After reviewer
approval + merge + green post-merge `main` CI, the operator decides Beta 1
GO/NO-GO.

---

# AGV2-031 kickoff — 2026-10-13 UTC (superseded by the complete record above)

- **AGV2-030 complete and merged.** GitHub PR #4 is closed/merged; canonical main merge SHA `abce2245444efc616b617b53746ba4c4427da689` includes reviewer-approved Beta security foundation. Its feature head `630b3546c899ef8f7b20710447e37565319b0538` passed all four PR-event workflows: backend 38334919438; frontend 38334919423; schema-secret-gate 38334919416; alpha-golden-path 38334919443.
- **Post-merge main workflow evidence pending:** The GitHub integration available to the architect does not surface main push-event runs. Confirm all four completed/success with head_sha `abce2245444efc616b617b53746ba4c4427da689` using authenticated GitHub UI/gh/API before AGV2-031 implementation. No claim that an unobserved push run passed or failed.
- **New branch:** `feat/agv2-031-beta1-release-hardening` created directly from the above main merge SHA. This is the only active task branch. Legacy persistent `v2` is retired and AGV2-030 branch remains historical.
- **AGV2-031 assignment:** Beta 1 release hardening, not a shipped release or tag. Scope: isolated production deployment/profile, recovery/fault-injection evidence, architecture ownership/CODEOWNERS plus actual review-enforcement validation, Beta 1 runbook and GO/NO-GO release record. See `docs/development/current-task.md` on the task branch.
- AGV2-030 baseline: 833 backend collected / exit 0, 89 frontend tests passed, full local Playwright 32 passed / 7 skipped, 7 CI browser golden checks; migration head `0017`. Real nomnom proof recorded under the preceding AGV2-030 handoff.
- **Release safety:** No deployment of production, no public release/tag, no automatic PR merge. Opencode must first create gitignored `.aethergate-wip` as its first local repository action, then read task/handoff, verify main post-merge CI, and proceed with scoped changes. Preserve all older handoff history below.

---

# AGV2-030 corrective closure — complete (2026-10-13 UTC)

- Branch: `feat/agv2-030-beta1-security`; canonical `main` is still
  `7c47cd8575dc96265c02e17333ee88bda920de99`; PR #4 is open, mergeable, and
  **unmerged**. No migration files changed; head remains `0017`.
- The independent-review HOLD is resolved; all three items are closed:

  1. **Strict literal-IP validation.** `_is_ip_literal` in
     `src/aethergate/egress.py` now parses the host exactly as spelled (no
     trailing-dot stripping), so `192.168.22.50.` and multi-dot spellings are
     rejected under `require_literal_ip=True` even when that exact spelling is
     allowlisted. Red-before/green-after regression tests in
     `tests/test_egress.py` (single/multiple/triple trailing dot, both
     spellings allowlisted, trailing-dot URL with plain IP allowlisted). Valid
     allowlisted IPv4/bracketed IPv6 and dev/test service-DNS semantics are
     preserved.
  2. **Transport direct-connect verification (proxy closure).** The pinned
     LiteLLM aiohttp transport reads `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` via
     `LiteLLMAiohttpTransport._get_proxy_settings`, gated by
     `litellm.disable_aiohttp_trust_env` (False by default); its httpx fallback
     (`DISABLE_AIOHTTP_TRANSPORT`/`LITELLM_HTTP2`) builds `trust_env=True`
     clients that resolve env proxies into `_mounts` at construction. A proxy
     **can** therefore intervene and defeat "validated IP == connected IP".
     `install_proxy_safety()` (applied once at adapter import) pins
     `litellm.disable_aiohttp_trust_env=True` and strips `_trust_env`/`_mounts`
     from every future and already-created client. Proven by real-transport
     canary tests (`tests/test_proxy_safety.py`) for both the default aiohttp
     and the httpx-fallback transport: a loopback canary proxy is never
     contacted, the origin is reached directly, and the guard's
     `disable_aiohttp_trust_env`/`_trust_env`/`_mounts` state is asserted.
  3. **Section-11 live nomnom proof** executed on a disposable
     production-policy stack (evidence below).

- Substantive closure commit: `f25d31f5159852eae155620fc70e0cc0ddb4ec4b`
  (`fix(security): close trailing-dot and env-proxy egress escapes`). All four
  PR workflows completed/success on that exact SHA: backend `38333972927`,
  frontend `38333972938`, schema-secret-gate `38333972978`,
  alpha-golden-path `38333972924`.
- `.aethergate-wip` remains in place until this documentation-only handoff tip
  is also verified green on all four workflows and PR #4 is updated.
- The historical checkpoints below are superseded by this closure record.

---

# Resume checkpoint - 2026-10-12 (per-task branch policy)

> Superseded: this checkpoint predates the AGV2-030 implementation. See the
> **AGV2-030** section below for the current, complete state.

- **Policy decided by operator:** new work uses per-task branches created from
  the latest reviewed/verified `main`. Historical long-lived `v2` is retired
  for development (preserved for audit), and must not be rebased, force-pushed,
  or used as the AGV2-030 base. Do not push implementation directly to `main`.
- Canonical `main`: `7c47cd8575dc96265c02e17333ee88bda920de99`.
  PRs #1-3 merged; backend `38263020817`, frontend `38263020743`,
  schema-secret-gate `38263020802`, and alpha-golden-path `38263020770`
  all succeeded on that merge SHA. Migration head `0017`.
- **Current prepared branch:** `feat/agv2-030-beta1-security`, created from
  canonical `main` with **zero commits behind**. Its previously existing
  preparation commits modified only `AGENTS.md` and
  `docs/development/current-task.md`; the product implementation is **not
  started**. This checkpoint and the current task are the active instructions.
  All downstream AGV2-031 work must start on a fresh branch from the then
  current reviewed `main`, not from AGV2-030 or old `v2`.
- `AGV2-030` remains Beta 1 security foundation: file-backed production
  provider-secret backend and pinned Ollama/LiteLLM DNS/redirect egress closure.
  Complete scope, proof requirements, nomnom test, no-migration expectation,
  `.aethergate-wip` lifecycle, and PR gate are in
  `docs/development/current-task.md` on the active branch.
- **Golden-path evidence requiring preflight correction:** On historical
  docs-only `v2` queue SHA `df6ac9ec8e6a218ab214ebb93f268f0f4038b2d9`,
  golden-path run `38263913698` failed only the second browser test at
  `frontend/e2e/fixtures.ts:loginAs` (Dashboard not visible, 30 s).
  The first browser OIDC test and the other four browser tests passed.
  Redacted web logs show the second test's unauthenticated session check
  returned 401 around 14:38:42 UTC, **without an OIDC login redirect**
  before timeout. Immediate `isVisible()` on the sign-in link after
  `page.goto("/")` is a credible client-auth-loading race. This diagnosis
  needs deterministic reproduction and a verified fix; do not dismiss as
  transient or mark resolved from a later green run alone.
- The historical `v2` handoff SHA
  `b715f780573b43d4046ddfbb6a6979c7fa368826` subsequently passed
  **all four** workflows: backend `38312457242`, frontend `38312457133`,
  schema-secret-gate `38312457067`, golden `38312457044`.
  This confirms the old green regression baseline, but does not close the
  missing-auth-redirect race. Preserve failing CI evidence for the fix.
- Work must proceed in this order: (1) create `.aethergate-wip` as first
  local repo action for active AGV2-030; (2) root-cause/repair/prove the
  OIDC smoke defect as the preflight; (3) implement AGV2-030 security scope;
  (4) independently verify tests, exact-final PR head and four CI gates;
  (5) reviewed PR to main, with **no automatic merge**.
- CI workflows respond to `pull_request` for task branches. Their `push`
  triggers currently name `main` and legacy `v2`; therefore a draft
  AGV2-030 PR may be used to get PR-event CI, but must not be merged before
  complete independent review. Do not claim branch-push CI exists when only
  PR-triggered CI ran.

---

# AetherGate Agent Handoff

## Current State
- Branch: `feat/agv2-030-beta1-security`, created from canonical `main`
  `7c47cd8575dc96265c02e17333ee88bda920de99`. **AGV2-030 (Beta 1 security foundation)** is
  implemented: the production file-backed provider-secret backend and the DNS-rebinding/redirect-
  escape egress closure. The historical persistent `v2` branch is retired for new work.
- **Migration head remains `0017`**; no new migration; `0001`–`0017` untouched.
- All four required remote workflows are `completed/success` on the AGV2-030 head (recorded below);
  feature PR **#4** (`feat/agv2-030-beta1-security` -> `main`) is open/unmerged.
- **Open item:** the live nomnom production-policy proof (task section 11) has not yet been
  executed. nomnom is reachable (Ollama `192.168.22.50:11434` open; dev IdP `:8090` down). All
  deterministic/offline proof is green; this live proof is the sole remaining AGV2-030 item.

## AGV2-030 — Beta 1 security foundation (provider-secret backend + egress DNS/redirect closure)

### Implementation commits
- `65984bac391eebf9f0419558788a1a7608b1a7e6` — fix(e2e): wait for resolved auth state before OIDC sign-in
- `0978438786505fc88a3de201d9188733df76c666` — feat(security): add production provider-secret backend and egress closure
- `6b9907fcb78da61258e65904c4594c0d81434e3d` — docs: document beta 1 provider-secret backend and egress closure
- `f25d31f5159852eae155620fc70e0cc0ddb4ec4b` — fix(security): close trailing-dot and env-proxy egress escapes (corrective closure)

### Preflight — OIDC golden-path loginAs race (root-caused and fixed)
- **Root cause:** `frontend/e2e/fixtures.ts:loginAs` polled `isVisible()` once immediately after
  `page.goto("/")`. The console renders a `Loading…` placeholder while its async
  `/admin/v1/auth/session` probe is in flight, so the poll could observe neither the sign-in link
  nor the Dashboard, skip the OIDC click, and time out waiting for a Dashboard it never reached.
  This matches the golden-path failure where the session probe returned 401 with no subsequent
  `/admin/v1/auth/oidc/login` redirect.
- **Fix:** a `waitForAuthState(page)` helper polls until a terminal auth state resolves (Dashboard
  heading or sign-in link) before `loginAs` acts; the same helper replaced the duplicated racy
  polls in `web-console.spec.ts`. No timeout inflation, no rerun-only disposition.
- **Regression:** `alpha-golden.spec.ts` adds `login readiness survives a delayed unauthenticated
  session probe`, which `page.route`s `**/admin/v1/auth/session` with a 1500 ms delay so the race
  is forced deterministically; the helper must still reach the Dashboard. Verified to fail (30 s
  timeout) pre-fix and pass post-fix.

### Production provider-secret backend
- `FileSecretResolver` resolves a `SecretRef` by its stable opaque `id` (never the display name) from
  `AETHERGATE_SECRET_DIRECTORY`. Safety: absolute root; resolved target must stay inside the root
  (traversal and symlink escape rejected, in-root symlinks permitted for rotation projection);
  regular files only; 64 KiB bound; UTF-8 only, no NUL, non-empty; exactly one trailing `LF`/`CRLF`
  removed (arbitrary whitespace preserved). Errors carry only safe `SecretRef` metadata, never file
  contents.
- `build_secret_resolver(settings)` is the single API/worker factory: `env` is a dev/test convenience
  rejected in `prod`; `prod` requires `file`; no implicit fallback (a file deployment never consults
  `AETHERGATE_SECRET_*`, and prod never falls back to env). `runtime.build_inference_service` now
  uses it. No resolver choice comes from a client request or database row.
- `config_check` reports prod `env` backend INVALID, and a missing/non-absolute/unusable file
  directory INVALID, without printing secret material.

### Egress / DNS / redirect closure
- **DNS rebinding (literal-IP prod policy):** `build_destination_policy(settings)` enables
  `DestinationPolicy(require_literal_ip=True)` in `prod`, so the configured upstream host must be a
  literal IPv4/IPv6 address — the validated address is the connected address, so DNS cannot be
  swapped between validation and connect. Authorized private-LAN literal IPs remain allowed when
  allowlisted (RFC1918 is not rejected generally). Dev/test may keep service DNS names; no insecure
  "allow unsafe DNS" override.
- **Redirect escape:** pinned LiteLLM `1.104.0` was found to construct its httpx clients with
  `follow_redirects=True`. `install_redirect_safety()` (invoked once at adapter import) patches the
  `AsyncHTTPHandler` constructor to force `follow_redirects=False` and mutates already-created/cached
  module-level and in-memory-cache handlers, so completion/streaming/embedding transports never
  contact a redirect target: every `301/302/303/307/308` surfaces as a safe provider error and the
  `Location` target is never requested.
- **Shared policy:** endpoint catalog create/update, dev seeding, and dispatch-time route
  preparation all use the same environment-aware `DestinationPolicy`; secret resolution runs only
  after dispatch-time destination validation, so a denied destination never receives credentials.
- **Strict literal-IP (trailing-dot closure):** `_is_ip_literal` parses the host exactly as spelled
  (no `rstrip(".")`/lower normalization), so a trailing-dot FQDN spelling such as `192.168.22.50.`
  is rejected under `require_literal_ip=True` even when that exact spelling is allowlisted, as are
  multiple-dot spellings. Valid allowlisted IPv4/bracketed IPv6 and dev/test service-DNS names are
  unchanged.
- **Implicit env-proxy closure:** the pinned LiteLLM aiohttp transport reads
  `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` via `LiteLLMAiohttpTransport._get_proxy_settings` unless
  `litellm.disable_aiohttp_trust_env=True`, and its httpx fallback
  (`DISABLE_AIOHTTP_TRANSPORT`/`LITELLM_HTTP2`) builds `trust_env=True` clients that resolve env
  proxies into `_mounts` at construction — so an implicit env proxy **could** route a "validated"
  literal address through a proxy. `install_proxy_safety()` (applied once at adapter import) pins
  `disable_aiohttp_trust_env=True` and strips `_trust_env`/`_mounts` from every future and
  already-created client (module-level and in-memory-cache handlers), so both transports connect
  directly to the validated address.

### Deterministic / offline proof
- File-resolver safety matrix (traversal, symlink escape, size, UTF-8/NUL/empty, newline) in
  `tests/test_secrets.py`; factory prod/dev selection and no-env-fallback in `tests/test_runtime_secrets.py`.
- Prod literal-IP policy and shared catalog/dispatch policy in `tests/test_egress.py` and
  `tests/test_inference_boundary.py`.
- Real-transport redirect canary (completion + streaming + embedding; silent for all five redirect
  classes, canary hit count zero) in `tests/test_redirect_safety.py`.
- Real-transport env-proxy canary (`tests/test_proxy_safety.py`) for both the default aiohttp and the
  httpx-fallback transport: a loopback canary proxy is never contacted and the origin is reached
  directly, with the guard state (`disable_aiohttp_trust_env`/`_trust_env`/`_mounts`) asserted.
- `config_check` prod/dev cases in `tests/test_config_check.py`.

### Regression counts (AGV2-030)
- backend: **833 collected / exit 0** (baseline 758 + 75 new/expanded security tests) via canonical
  `scripts/dev/v2 test`; `ruff check src tests` clean. The 833 includes the 5 trailing-dot
  literal-IP tests in `tests/test_egress.py` and the 3 env-proxy transport tests in
  `tests/test_proxy_safety.py`.
- frontend unit/component: **89 passed** (22 files, unchanged).
- full Playwright: **32 passed / 7 skipped** locally (6 CI-only `alpha-golden.spec.ts` + 1 new
  login-readiness regression); CI `alpha-golden.spec.ts` runs **7 passed**.
- migration head `0017` (empty -> `0017` and populated `0016 -> 0017` unchanged); `git diff --check`
  clean; secret scan clean; no-legacy guard clean; config_check dev VALID and prod security cases
  correct.

### Final remote CI (AGV2-030 substantive head `f25d31f`)
All four required PR-event workflows are `completed/success` on the substantive closure head:
- backend — run `38333972927`
- frontend — run `38333972938`
- schema-secret-gate — run `38333972978`
- alpha-golden-path — run `38333972924`

The exact final task-branch tip (including this documentation-only handoff commit) is re-verified on
all four workflows; its exact SHA and four exact-SHA workflow IDs are recorded in PR #4's description.

### Live nomnom proof — COMPLETE (2026-10-13 UTC)
Executed on a disposable production-policy stack (`ag-agv2030-nomnom`, loopback api `:18080`), torn
down afterward; the long-lived dev stack was not touched.

- **Installed models inspected** (no downloads) via `GET http://192.168.22.50:11434/api/tags`:
  vision-capable `qwen3-vl:latest` (and others), embedding-capable
  `nomic-embed-text:latest`/`nomic-embed-text:v1.5` (`caps=["embedding"]`, 768-dim), and
  completion-capable `qwen3.8-2b-distill:Q6_K` among many more.
- **Production-policy stack:** `AETHERGATE_ENV=prod`,
  `AETHERGATE_UPSTREAM_ALLOWLIST=192.168.22.50`,
  `AETHERGATE_SECRET_BACKEND=file` with a read-only mounted empty
  `AETHERGATE_SECRET_DIRECTORY=/run/secrets/aethergate-provider`,
  `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false`, `AETHERGATE_EGRESS_REQUIRE_LITERAL_IP=true`,
  generated `AETHERGATE_QUEUE_KEY`/`AETHERGATE_BOOTSTRAP_TOKEN`, fresh postgres volume, OIDC
  disabled. `python -m aethergate.config_check` => **VALID**; migrations `0001 -> 0017` clean.
- **bypass=false:** inference auth bypass disabled in prod (and prod forbids it by validator).
- **Official Python SDK (`openai==2.54.0`):**
  - `client.models.list()` returns the three public aliases (text, vision, embedding).
  - `client.chat.completions.create` (text, `nomnom-text` -> `qwen3.8-2b-distill:Q6_K`) returns
    non-empty content against the allowlisted literal IP.
  - `client.chat.completions.create` (vision, `nomnom-vision` -> `qwen3-vl:latest`) returns
    non-empty content for a deterministic 16x16 PNG.
  - `client.embeddings.create` (`nomnom-embed` -> `nomic-embed-text:latest`) returns a 768-dim
    vector.
- **Hostname form rejected in prod:** catalog endpoint create with
  `http://ollama.internal:11434` => `400` (destination denied).
- **Metadata/link-local denied:** `http://169.254.169.254:80` and `http://169.254.1.1:80` =>
  `400` (destination denied); trailing-dot `http://192.168.22.50.:11434` => `400` as well.
- **Tear-down:** only the disposable `ag-agv2030-nomnom` project was removed (`down -v`); no
  unrelated services or the dev IdP were touched. `FileSecretResolver` was proven deterministically
  (offline) because the real Ollama route has no provider credential.
- No secret contents, provider credentials, prompt/output text, or large logs are reproduced here.

### Feature PR
PR **#4** (`feat/agv2-030-beta1-security` -> `main`): open, `mergeable`, **unmerged**; head tracks
the AGV2-030 tip. Description records the preflight root-cause/fix, the security design choice, the
verification summary, the exact final SHA, and the four exact-SHA workflow IDs.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-030`, `branch=feat/agv2-030-beta1-security`, UTC start timestamp)
created as the first local action and kept until every criterion is green, this handoff commit is
pushed, and the exact final tip is verified green on all four workflows. Never
staged/committed/pushed (gitignored).

### Recommended next step
**Begin AGV2-031 Beta 1 release hardening: production deployment profile, architecture/CODEOWNERS
enforcement, fault-injection/recovery release gates, and Beta readiness documentation.** (AGV2-030 is
now complete pending the final tip's four green workflows and PR #4 update; do not merge PR #4
automatically.)

## AGV2-029V — Embeddings verification closure (index integrity, null semantics, bounded inputs)

### Independent-review findings (all three closed)
1. **Provider index integrity.** The LiteLLM embedding adapter read each provider `index` and
   checked only that the entry count equalled the input count, then sorted by index. Malformed
   indexes (`[0,0]`, `[1,1]`, `[1,2]` for two inputs, negative/out-of-range) could survive the count
   check and be silently renumbered by the public API, corrupting input → vector correspondence.
2. **Optional does not mean nullable.** The request DTO treated explicit JSON `null` for the pinned
   optional-but-non-nullable fields as omission: `"encoding_format": null` silently defaulted to
   `float`, and `dimensions`/`user` null bypassed the required `unsupported_feature` rejection.
3. **Unbounded input cardinality.** `_normalize_embedding_input` accepted any-length `list[str]`;
   only the general queue payload byte bound constrained it.

### Provider index policy (adapter boundary, `src/aethergate/adapters/litellm.py`)
Provider entries may arrive in any order. Each `index` must be a genuine Python `int` (bool, float,
numeric string, `None`, and missing keys are malformed), and after sorting the index sequence must
equal exactly `0, 1, …, len(inputs)-1`. Duplicates, gaps, negative indexes, and out-of-range indexes
are rejected with a safe `ProviderError` (no vectors or input text in the message) instead of being
silently renumbered. Wrong-count, non-finite, and malformed-vector rejections are unchanged.

### Optional vs explicit-null semantics (pinned evidence)
Pinned: Python `openai==2.54.0` `EmbeddingCreateParams` is `total=False` (`dimensions: int`,
`encoding_format: Literal["float","base64"]`, `user: str`); JS `openai@7.32.0` uses `?` without
`| null`. So optional ≠ nullable. `EmbeddingCreateRequest` now uses a `model_validator(mode="before")`
to distinguish key absence from explicit `null`:

- `encoding_format` omitted — accepted (server default `float`, unchanged);
- `encoding_format` `"float"` / `"base64"` — accepted;
- `encoding_format: null` — rejected `400 invalid_request` (never silently defaults to `float`);
- `dimensions` omitted — accepted as omission;
- `dimensions` present with any value, **including `null`** — rejected `400 unsupported_feature`;
- `user` omitted — accepted as omission;
- `user` present with any value, **including `null`** — rejected `400 unsupported_feature`.

Unknown additive-field tolerance and transport-control handling are unchanged. A rejected request is
rejected before durable enqueue and never dispatches upstream.

### Bounded embedding input cardinality
Maximum: **2048 inputs per request**, as the explicit constant
`MAX_EMBEDDING_INPUTS = 2048` in `src/aethergate/contracts/embeddings.py`.
Compatibility source: the pinned upstream OpenAI OpenAPI reference
(`docs/contracts/openai-compatibility-baseline.md`, commit
`f7bc81b7f30e834bbdb6bcc3370903e49fe7e5b1`), schema `CreateEmbeddingRequest.input`, whose array forms
declare `minItems: 1` / `maxItems: 2048`. This is the provider-neutral, OpenAI-compatible
request-shape bound (deliberately not an Ollama-specific limit). It is enforced in
`to_canonical_inputs()` before durable enqueue: a single string is one input, a list of exactly 2048
succeeds, 2049 returns `400 invalid_request`. The separate `AETHERGATE_MAX_QUEUE_PAYLOAD_BYTES`
limit is retained as an independent safety bound.

### Focused tests
- `tests/test_adapter.py`: valid in-order accepted; out-of-order `[1,0]` restored to input order;
  duplicate / negative / out-of-range(gapped) / non-integer (`None`/`"1"`/`1.5`/`True`) indexes
  rejected; index error message contains neither input text nor vector values; existing
  wrong-count / non-finite / malformed-vector cases still rejected.
- `tests/test_embeddings_contract.py`: encoding omitted/`float`/`base64` accepted; `encoding_format:
  null` rejected `InvalidRequest`; `dimensions`/`user` rejected for `null` as well as values; omitted
  dimensions/user accepted; input count at 2048 accepted and 2049 rejected.
- `tests/test_embeddings_api.py`: `encoding_format: null`, `dimensions: null`, `user: null`, and
  count 2049 all return structured 400 with **no dispatch** (`embed_calls == 0`); count exactly 2048
  succeeds end-to-end.
- New focused tests were verified to fail against the pre-fix behavior; a forced-ordering probe
  proved the provider backstop and transaction-abort interactions where relevant.

### Full regression counts (AGV2-029V)
- backend: **758 passed / 758 collected** via the canonical `scripts/dev/v2 test` (baseline 734 +
  24 new focused tests); `ruff check src tests` clean.
- frontend unit/component: **89 passed** (22 files, unchanged).
- full Playwright: **32 passed / 6 skipped** (CI-only `alpha-golden.spec.ts`), run locally against
  the dev stack (web console served on loopback 8081 because loopback 8080 is held by an unrelated
  local service; this is environmental only).
- offline golden path (`scripts/ci/golden_path.py` main + backup + restore + post-restore on a
  disposable fake-Ollama stack): all `ok`, including `sdk_embedding`, `sdk_embedding_multi`,
  `sdk_embedding_base64`, and the `dimensions`/`user`/token-id/text-only `400` embeddings cases; the
  official JS SDK smoke reported all Models + Chat + Responses + Embeddings checks `ok`.
- `npm run build` / `npm run lint` / `npx tsc -b --noEmit` clean; OpenAPI/client regeneration run
  twice produced zero diff; `git diff --check` clean; `scripts/ci/secret_scan.sh` clean;
  `no_legacy_v1.sh` + `--self-test` clean; migrations empty -> `0017` and populated `0016 -> 0017`;
  `config_check` clean (all within the 758 backend suite plus targeted runs).

### Unrelated CI root-cause (not an embeddings change)
The first push on `e8b763a` had a red `backend` workflow:
`test_accounting_admin.py::test_concurrent_price_policy_enable_race` intermittently raised
`InvalidRequestError: Can't operate on closed transaction` after the price-policy partial unique
index backstop aborted the loser's transaction. Root cause: the race tests caught
`PricePolicyConflictError` *inside* `async with session.begin()`, so the context manager attempted to
commit the already-aborted transaction. Fixed in `d32c8db` by catching the domain error outside
`session.begin()` (repository conflict translation unchanged); a deterministic forced-ordering probe
confirmed the backstop/abort behaviour. No embeddings code was involved.

### Migration / OpenAPI
- No migration; head remains `0017`; `0001`–`0017` untouched.
- Request-schema/validator changes did not alter generated OpenAPI; two consecutive generations
  produced zero diff for `frontend/src/generated/openapi.json` and `schema.d.ts`.

### Final remote CI (AGV2-029V)
Substantive code/test SHA `d32c8dbe0c4d0ce20ff2a9455d9e1f63e43c4f51`; all four required push-event
workflows are `completed/success`:
- backend — run `38256553204`
- frontend — run `38256553189`
- alpha-golden-path — run `38256553253`
- schema-secret-gate — run `38256553198`

The final `origin/v2` tip (this documentation-only handoff commit) is independently verified green
on all four workflows; its exact SHA and four exact-SHA workflow IDs are recorded in PR #3's
description.

### Feature PR
PR **#3** (`v2` -> `main`): open, **unmerged**, `mergeable`; head tracks the AGV2-029V final
`origin/v2` SHA. Its description records this closure (three review findings), the exact final SHA,
and the four exact-SHA workflow IDs.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-029V`, `branch=v2`, UTC start timestamp) created as the first local
action, kept for the whole task, and removed only after all criteria were green, the handoff
committed, commits pushed to `origin/v2`, final remote CI verified green, and PR #3 updated. Never
staged/committed/pushed (gitignored).

### Recommended next step
**Merge PR #3 (`v2` -> `main`) through the reviewed/user-authorized process.**

## AGV2-029 — OpenAI Embeddings API through the durable scheduler/accounting authority

### Implementation commits
- `7e7c471c7b1408253988d483d7ae12b9878c2688` — feat(inference): add durable embedding operation
- `4b74bf024deaf4bd571aec09e7c439a1366b3c5f` — feat(api): add openai embeddings endpoint
- `0d47aa6e724e2a661963a15dec607cb683d18955` — test: cover embeddings contract, api, scheduler, adapter
- `783dcc1d893cbbaf92b7017be72309fa90ba29b9` — test(ci): prove embeddings sdk compatibility offline
- `46abf50c9d3261a7c601e85ffd671879de8235c4` — docs: document embeddings support
- `35599fc9395f88119712e884a561e6f31073b234` — chore: regenerate openapi and frontend client schema

### Exact pinned embedding contract subset
- Supported: `model` (public alias), `input` (one non-empty string, or a non-empty ordered list of
  non-empty strings), `encoding_format` (`float` | `base64`). Non-streaming only.
- Rejected `400 unsupported_feature`: token-ID input (`number[]`/`number[][]`), `dimensions`, `user`,
  empty input, and every transport/provider-control field (`api_base`, `base_url`, `headers`,
  `extra_headers`, `api_key`, `api_token`, `organization`, `project`, `provider`, `endpoint`,
  `upstream_model`, `deployment`, `custom_api_base`).
- Response: `object="list"`, ordered `data` (`object="embedding"`, `index`, `embedding` numeric for
  `float` / base64 string for `base64`), `model` = public alias, `usage` = `prompt_tokens` +
  `total_tokens` (no completion-token field).

### JS default-base64 behavior and exact encoding proof
- `openai@7.32.0` sends `encoding_format=base64` when the user omits it, then decodes the returned
  base64 to numeric vectors via a native little-endian `Float32Array`; `openai==2.54.0` does the same
  with a `post_parser` using `array.array("f")` / `np.frombuffer(..., dtype="float32")`.
- The server's base64 is IEEE-754 little-endian `float32` (`base64.b64encode(struct.pack("<%df", ...))`),
  matching both SDK decode paths exactly. Proven by the JS smoke `embeddings_default=ok` and the
  golden path `sdk_embedding=ok` / `sdk_embedding_base64=ok_embed_base64`.

### Operation-aware encrypted payload/result design
- The scheduler payload/result envelope is now operation-aware (`operation` discriminator lives
  **inside** `payload_encrypted`; no plaintext DB column). `chat` and `embedding` operations share
  the single state machine. Embeddings never enter `streaming` and never fabricate a `first_token_at`
  / TTFT sample.

### Legacy chat payload/result backward compatibility
- Payloads/results without an operation discriminator deserialize as chat exactly as before; pre-vision
  text and current multimodal chat payloads remain readable. No migration was required.

### Adapter / LiteLLM / Ollama transport discovery
- Empirically pinned: LiteLLM `1.104.0` ollama embedding transport POSTs
  `{"model", "input": [<str>...]}` to `{base_url}/api/embed` and reads
  `{"embeddings": [[...]...], "prompt_eval_count": <int>}` (singular `/api/embed`, not `/api/embeddings`).
- `src/aethergate/adapters/litellm.py` exposes a real `embed` operation; hidden retries/fallback stay
  disabled; provider errors sanitized; 429 feeds the shared cooldown authority.

### Capability enforcement
- Embedding requires the public ModelAlias **and** Provider to advertise `Capability.EMBEDDING` and
  the selected adapter to support embeddings, checked at admission and re-checked at dispatch. No
  fallback to text/image-only routes; no inference from provider kind or endpoint URL. A text-only
  alias returns `400 model_unavailable` (SDK-visible).

### Quota / budget / accounting semantics
- One embedding request consumes exactly one request unit; token quota reserves summed input tokens
  with output reservation zero. The embedding input-token estimator returns no trustworthy estimate,
  so enforced token quotas / token-priced budgets fail closed (estimator-unavailable). Request-priced
  settlement records `request_units=1`, `input_units=0`, `output_units=0`; one request produces exactly
  one `UsageRecord` and one signed negative `usage_debit` ledger entry. Golden path recorded
  `usage_records=12` / `ledger_entries=12` after the full proof sequence.

### Fake provider proof
- `src/aethergate/testing/fake_ollama.py` gained a deterministic `/api/embed` (ordered 3-dim vectors,
  `prompt_eval_count`, wrong-count/malformed/non-finite injection). Never logs input text or vector
  contents.

### Python SDK proof
- Golden path main: default (base64->float decode), multi-input explicit float, explicit base64 all
  `ok`; dimensions/user/token-ids/text-only-alias all `400`. Official-SDK test
  `test_official_openai_sdk_embeddings` (default decode) green.

### JS SDK proof
- `sdk/js/smoke.mjs`: `embeddings_default`, `embeddings_multi_float`, `embeddings_base64`,
  `embeddings_unsupported_400`, `embeddings_text_only_400` all `ok`.

### Live nomnom limitation (truthful)
- No installed embedding-capable Ollama model was available, and none was silently downloaded. The
  deterministic fake Ollama + real pinned LiteLLM `/api/embed` transport contract tests are
  authoritative for provider wire compatibility; this limitation alone does not fail the milestone
  (all deterministic/API/scheduler/accounting/SDK gates are green). See `docs/contracts/embeddings.md`.

### No-TTFT behavior
- Embeddings never fabricate `first_token_at`/TTFT; they contribute to queue/in-flight/slot/upstream-
  health/accounting observability but never a synthesized first-token sample.

### OpenAPI drift / migration
- Backend OpenAPI + frontend `openapi.json`/`schema.d.ts` regenerated under the committed lock; two
  consecutive generation runs produce zero diff. Migration head `0017`; no migration added.

### Regression counts (AGV2-029)
- backend: **734 passed** (baseline 691 + 43 embedding/operation tests), `ruff check src tests` clean.
- frontend: **89 passed** (unchanged); full Playwright: **32 passed / 6 skipped** (unchanged).
- `npm run build` / `npm run lint` / `npx tsc -b --noEmit` clean; `git diff --check` clean; secret
  scan clean; no-legacy guard + self-test clean; `python -m aethergate.config_check` clean.

### Final remote CI (AGV2-029)
The substantive code/test SHA is `35599fc9395f88119712e884a561e6f31073b234`; all four required
workflows are `completed/success` there (push events):
- backend — run `38206296208`
- frontend — run `38206296226`
- schema-secret-gate — run `38206296218`
- alpha-golden-path — run `38206296252`

The final `origin/v2` tip (this documentation-only handoff commit) is independently verified green on
all four workflows; its exact SHA and four exact-SHA workflow IDs are recorded in PR #3's description.

### Feature PR
PR **#3** (`v2` -> `main`): created from the final `origin/v2` SHA, open and **unmerged**; its
description records the exact final SHA, the four workflow IDs above, and this verification summary.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-029`, `branch=v2`, UTC start timestamp) created first, kept for the whole
task, and removed only after all criteria were green, the handoff committed, commits pushed to
`origin/v2`, final remote CI verified green, and PR #3 created. Never staged/committed/pushed
(gitignored).

### Recommended next step
**Review and merge PR #3 (`v2` -> `main`) through the reviewed/user-authorized process.** No further
embeddings work is queued.

## AGV2-028V2 — Chat image-detail null closure (omitted vs explicit-null)

### Independent-review finding
The AGV2-028V detail handling used `image_url.get("detail")` and a `validate_image_detail` that
treated `None` as "omitted". Independent inspection of the pinned SDK types found a narrow
remaining mismatch: Chat's `detail` is **optional but not nullable**, so a client-supplied JSON
`"detail": null` was being silently accepted as though it were omitted.

### Pinned contract evidence (exact, from the locked SDKs)
- **Chat** Python `openai==2.54.0` `ImageURL` is a `total=False` TypedDict with
  `detail: Literal["auto","low","high"]` — optional, **not** `Optional`, **not** nullable.
- **Chat** JS `openai@7.32.0` `ChatCompletionContentPartImage.ImageURL` is
  `detail?: 'auto' | 'low' | 'high' | 'original'` — optional, **not** `| null`.
- **Responses** JS `ResponseInputImageContent.detail?: ImageDetail | null` — optional **and**
  nullable; the Python `ResponseInputImageParam` generation differs in requiredness/nullability.

### Implementation (sentinel-based omission detection)
`src/aethergate/inference/images.py` now exports a `DETAIL_OMITTED` sentinel and
`validate_image_detail(detail, *, param, allow_null=False)` distinguishes three states:

- omitted (`DETAIL_OMITTED`) — always permitted;
- explicit `None` — rejected by default (`400 unsupported_feature`, `feature="image_detail"`), and
  only tolerated when the caller opts in with `allow_null=True`;
- a string — `auto` accepted, any other value (`low`/`high`/`original`/unknown/non-string)
  rejected.

Chat (`contracts/openai.py`) uses key-presence detection (`"detail" in image_url`) and the default
`allow_null=False`; Responses (`contracts/responses.py`) uses the same sentinel but
`allow_null=True`, preserving the documented nullable content-part contract. Error text never
contains image bytes or base64.

### Focused tests
- `tests/test_vision_images.py`: Chat omitted/auto succeed; Chat explicit-null, `low`/`high`/
  `original`, invalid enum, and non-string rejected (precise `param`); a direct
  `validate_image_detail` unit test proves the sentinel distinguishes omission from null;
  Responses omitted/auto/null succeed, `low`/`high`/`original` rejected.
- `tests/test_vision_api.py`: end-to-end — Chat omitted/auto reach the adapter; Chat explicit null
  returns `400 unsupported_feature` with **no adapter dispatch**; Responses explicit null is
  tolerated and dispatches.

### Regression counts (null closure)
- backend: **691 collected, exit 0** (baseline 685 + 6 null-closure tests).
- frontend: **89 passed** (unchanged); full Playwright: **32 passed / 6 skipped** (unchanged).
- `npm run build`/`npm run lint`/`npx tsc -b --noEmit` clean; `ruff` clean; `git diff --check`
  clean; secret scan clean; no-legacy guard + self-test clean.
- No OpenAPI/client drift (validation-only change; no request/response model shape changed).
- Migration head `0017`; empty -> `0017` and populated `0016 -> 0017` unchanged.

### Local-gate note (environmental, not a code defect)
The first local full-suite run failed in `test_oidc_session.py` (callback `302` instead of `200`)
and DB-gated tests with DNS `socket.gaierror`. Both were environmental, not product defects:
(1) the long-lived dev stack had stopped (all containers in `created` state after a daemon
restart), so `postgres` no longer resolved and `scripts/dev/v2 up` was required; (2) `deploy/v2/.env`
carried `AETHERGATE_OIDC_WEB_CALLBACK_PATH=/auth/callback` (written by a prior `web up`), which
leaked into the test container's ambient env and flipped `web_console_enabled=True`. The
`AETHERGATE_OIDC_WEB_CALLBACK_PATH` value was cleared locally for the green run and restored
afterward. Remote CI is unaffected (it generates a clean `.env`). Root-caused per the ambient-env
contamination lesson; no source/test change was needed.

### Final remote CI (null closure)
The substantive code/test SHA is `389fffd772894ab4ae9cede2ce8d2e3a5e925845`; all four required
workflows are `completed/success` there (push events):
- backend — run `38194540176`
- frontend — run `38194540156`
- schema-secret-gate — run `38194540202`
- alpha-golden-path — run `38194540148`

The independently verified pre-metadata tip is `936cec102d40315c6d5b90c4c05fc4ca3dac3622`
(handoff/docs-only commit after `389fffd`); all four required workflows are `completed/success`
there (push events):
- backend — run `38195202524`
- frontend — run `38195202501`
- schema-secret-gate — run `38195202508`
- alpha-golden-path — run `38195202546`

The AGV2-028V3 metadata-synchronization commit is
`bb3d58c76222970a3e410b75da9f2244ac5002a0`; all four required workflows are `completed/success`
there (push events):
- backend — run `38198302116`
- frontend — run `38198302136`
- schema-secret-gate — run `38198302315`
- alpha-golden-path — run `38198302139`

The final `origin/v2` tip (the documentation-only handoff commit that records this section) is
verified green on all four workflows; its exact SHA and four exact-SHA workflow IDs are recorded
in PR #2.

### PR
PR **#2** (`v2` -> `main`): https://github.com/draeician/aethergate/pull/2 — open, **unmerged**,
`mergeable`, head `v2` tracking the AGV2-028V3 final SHA. Its GitHub description was corrected in
AGV2-028V3 (via the GitHub REST API, not a git commit) to remove the stale `06728b21…` final SHA
and record the AGV2-028V2 null closure plus the exact final v2 SHA and its four exact-SHA workflow
IDs.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-028V2`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, commits pushed
to `origin/v2`, final remote CI verified green, and PR #2 updated. Never staged/committed/pushed
(gitignored).

### Recommended next step
**Merge PR #2 (`v2` -> `main`) through the reviewed/user-authorized process.** Do **not** start
embeddings before PR #2 is merged and post-merge `main` CI is green — otherwise PR #2 stops being a
scoped vision PR.

## AGV2-028V — Vision verification closure (strict WebP + explicit image detail)

### Independent-review findings (both closed)
1. WebP validation was too permissive: `_SIGNATURES["image/webp"] = ((b"RIFF",), (b"WEBP",))`
   with a generic "any one group matching at offset 0" check accepted **any** RIFF payload
   (RIFF/WAVE, RIFF/AVI, arbitrary RIFF) as `image/webp`.
2. The OpenAI image `detail` selector was accepted but never inspected (silently dropped),
   violating the settled rule that semantic fields are implemented faithfully or rejected.

### Fix 1 — strict per-format signatures (`src/aethergate/inference/images.py`)
Replaced the generic signature map/matcher with explicit per-format predicates:
`_is_png` (`89 50 4E 47 0D 0A 1A 0A`), `_is_jpeg` (`FF D8 FF`), and `_is_webp`
(header length ≥ 12 **and** `bytes 0:4 == b"RIFF"` **and** `bytes 8:12 == b"WEBP"`).
RIFF alone is no longer accepted. PNG/JPEG strictness unchanged. No decoding library used.

### Fix 2 — explicit image `detail` semantics
Pinned contract enums inspected from the locked SDKs (not memory):
- **Chat** `image_url.detail`: Python `openai==2.54.0` `ImageURL` = `Literal['auto','low','high']`;
  JS `openai@7.32.0` `ChatCompletionContentPartImage.ImageURL` = `'auto'|'low'|'high'|'original'`.
- **Responses** `input_image.detail`: Python `ResponseInputImageParam` =
  `Literal['low','high','auto','original']` ("Defaults to `auto`"); JS
  `ResponseInputImageContent.detail?: ImageDetail | null`.

Chosen policy (identical for Chat and Responses; `validate_image_detail`):
- **omitted** allowed (Responses defaults to `auto`; Chat `auto` is the neutral provider default);
- **`auto`** allowed — neutral/default mode, imposes no fidelity semantics on the transport;
- **`low`/`high`/`original`** explicitly rejected with `400 unsupported_feature`
  (`param` = `messages[...].content[i].image_url.detail` / `input[i].content[j].detail`), because
  the pinned LiteLLM/Ollama path has no faithful per-image detail control;
- any non-string or unrecognized value rejected as a contract violation.
Rejected detail never reaches the adapter/upstream (validation is pre-enqueue/pre-dispatch).

### Focused regression proof
- `tests/test_vision_images.py`: valid WebP accepted; RIFF/WAVE, RIFF/AVI, arbitrary RIFF, and
  `WEBP`-without-`RIFF` rejected; too-short header rejected; error text contains neither the
  base64 nor the raw `RIFF`/`WAVE` markers (verified to fail against the AGV2-028 logic). Plus
  Chat + Responses detail matrices: omitted/`auto` succeed; `low`/`high`/`original` rejected with
  the precise `param`; invalid enum and non-string rejected.
- `tests/test_vision_api.py`: end-to-end — `auto` succeeds and reaches the adapter; `low`/`high`/
  `original` return `400 unsupported_feature` with **no adapter dispatch** (Chat and Responses).
- `scripts/ci/golden_path.py` / `sdk/js/smoke.mjs` / `frontend/e2e/`: official Python + JS SDK
  proof of default/`auto` detail and an SDK-visible 400 for `low`.

### Invariants preserved (unchanged)
Inline `data:` only; PNG/JPEG/WebP; no remote URL/file_id; no-fetch/SSRF; image count/byte/payload
limits; encrypted queue payload; capability enforcement (alias+provider, dispatch-time revalidation);
same scheduler/accounting/worker; token quota/budget fail-closed; auth/cancellation/recovery;
text behavior unchanged. **No provider-transport change** was needed, so no live nomnom rerun was
required by the task; the local fake-Ollama offline golden path was re-run end-to-end.

### Gate counts (AGV2-028V)
- backend: **685 collected, exit 0** (baseline 657 + 28 new/expanded WebP+detail tests).
- frontend unit/component: **89 passed**.
- full Playwright: **32 passed, 6 skipped** (CI-only `alpha-golden.spec.ts`, now +1 detail test).
- `npm run build`/`npm run lint`/`npx tsc -b --noEmit` clean; `ruff` clean; `git diff --check`
  clean; secret/content/image-data scans clean; no-legacy guard + self-test clean.
- OpenAPI/client drift: two consecutive generations md5-stable.
- Migrations empty → `0017`, populated `0016 → 0017`; `config_check` VALID.
- Offline golden path (fake Ollama) green incl. `sdk_vision_detail_400` and JS
  `chat_vision_detail_auto`/`chat_vision_detail_400`.

### Playwright root-cause (not a product defect)
The first full Playwright run after rebuilding the image failed two specs. Root causes, both
environmental and documented:
1. `catalog-inference.spec.ts` — the OIDC/JWKS browser cold start (`Sign-in failed`); passes in
   isolation and on the subsequent full run.
2. `accounting.spec.ts` budget-unblock — a stale `outcome_unknown` request (from the prior task's
   live vision runs) held a shared `ollama-endpoint` slot, so its first live SDK call queued and
   expired. Reconciled through the real operator path (`python -m aethergate.reconcile resolve …
   --disposition failed`) which released the slot; the full `accounting.spec.ts` then passed 8/8
   and the full suite passed 32/32. Documented live-data-accumulation defect family.

### Documentation cleanup
`docs/contracts/vision.md` now documents the exact detail semantics and the strict WebP signature.
Stale "Responses text-generation subset" wording that AGV2-028 made materially false was corrected
to "text + inline-image input subset" in `README.md`, `docs/contracts/openai-compatibility-baseline.md`,
`docs/contracts/responses.md`, `docs/development/README.md`, `docs/releases/alpha1-readiness.md`, and
the `ResponsesRequest` docstring. Compatibility claims were not broadened.

### Migration
No migration; head remains `0017`; `0001`–`0017` untouched.

### Final remote CI (AGV2-028V)
The substantive code/test SHA is `e1d7086` (fix `f9e7619` + tests `e1d7086`; every later commit in
this task is documentation-only). Because the four workflows use `cancel-in-progress` concurrency,
rapid successive pushes cancel earlier same-ref runs, so exact-SHA runs are retained only for the
tips pushed after each verification pause. All four required workflows are `completed/success`
(push events) on each such verified tip:
- tip `2f1fb88cb0e4315ddbaaab122198f86770737fb3` — `backend 38186374537`, `frontend 38186374519`,
  `schema-secret-gate 38186374535`, `alpha-golden-path 38186374541`
- tip `7b00f50e693c5945990f5cd65ce2b8a209f97ab1` — `backend 38185998082`, `frontend 38185998066`,
  `schema-secret-gate 38185998076`, `alpha-golden-path 38185998068`
- tip `f3b465134431915334dbad0c7d2e6eb49d80cc9c` — `backend 38185647257`, `frontend 38185647190`,
  `schema-secret-gate 38185647180`, `alpha-golden-path 38185647208`
- tip `5e4c94fd3f5b69a40d1213b0521681d8fb0e8661` — `backend 38183328077`, `frontend 38183328123`,
  `schema-secret-gate 38183328067`, `alpha-golden-path 38183328081` (that alpha-golden run initially
  hit the documented transient OIDC/JWKS browser cold-start flake at `loginAs` in the pre-existing
  remote-URL test; `gh run rerun --failed` then passed)
- tip `3f93f05d7f9e7850cf23fc15d463a16661196ea2` — `backend 38182977904`, `frontend 38182977886`,
  `schema-secret-gate 38182977887`, `alpha-golden-path 38182977874`

Every commit after the substantive code/test SHA is documentation-only and is verified green with
the same four deterministic, offline workflows; the `origin/v2` tip at handoff (this handoff
commit) is green for all four.

### PR
PR **#2** (`v2` -> `main`): https://github.com/draeician/aethergate/pull/2 — open, **unmerged**,
mergeable, head `v2` now tracking the AGV2-028V tip. Description updated with the strict-WebP
validation closure, explicit detail behavior, and the final CI evidence.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-028V`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, removed only after all criteria green, handoff committed, commits pushed to `origin/v2`,
final remote CI verified green, and PR #2 updated. Never staged/committed/pushed (gitignored).

### Recommended next step
**Merge PR #2 (`v2` -> `main`) through the reviewed/user-authorized process** once its head is the
final green AGV2-028V SHA. Do **not** start embeddings before PR #2 is merged and post-merge `main`
CI is green — otherwise PR #2 stops being a scoped vision PR.

## AGV2-028 — Vision / multimodal image input (Chat Completions + Responses)

### Pinned contract / SDK baseline
- Request/response shapes derived from the locked official **`openai==2.54.0`** Python SDK
  (`ChatCompletionContentPartParam` = `text`/`image_url`; `ResponseInputImageParam` =
  `input_image` with `image_url`/`file_id`) and the pinned upstream OpenAPI baseline; never from
  memory. JS smoke uses the locked official **`openai` Node SDK `7.32.0`**.
- Provider transport shape discovered **empirically** against pinned LiteLLM `1.104.0`: a chat
  content list `[{type:text,text}, {type:image_url,image_url:{url:"data:<mime>;base64,..."}}]`
  is transformed by `litellm/llms/ollama/...` into a POST to **`/api/generate`** with top-level
  `images: ["<pure base64>"]` (and `/api/chat` message `images`). It strips the `data:` prefix.

### Supported surface / source form
- Chat Completions: `messages[].content` = string **or** ordered `text`/`image_url` parts.
- Responses: `input[].content` = `input_text`/`output_text`/`input_image` parts.
- Inline `data:` URLs only; media types **PNG/JPEG/WebP** (strict base64 + magic-byte signature).
- Non-stream + stream on both surfaces; text-only forms unchanged.

### Explicitly rejected (structured 400)
Remote `http(s)://` image URLs (**never fetched / no DNS+HTTP client**), `file_id`/files,
provider-local paths, arbitrary URI schemes, SVG/unsupported media types, malformed data URLs,
bad/non-canonical base64, empty payloads, MIME/signature mismatch.

### Canonical multimodal model + encryption
- `adapters.base.Message(role, content:str, parts: tuple[ContentPart,...] | None)`;
  `TextContentPart`/`ImageContentPart(media_type, data:bytes)`. Part order preserved.
- Chat and Responses map into the **same** canonical messages; adapters receive canonical parts.
- `serialize_chat_payload` emits a `parts` key only for multimodal messages; image bytes are
  base64 within the JSON envelope, which is then Fernet-encrypted into `payload_encrypted`.
  Legacy text payloads (no `parts`) deserialize byte-for-byte as before
  (`tests/test_vision_images.py::test_legacy_text_payload_deserializes_exactly_as_before`).
- No plaintext image column `migration`; **no migration** (head `0017`).

### Configurable limits (pre-enqueue)
`AETHERGATE_MAX_IMAGES_PER_REQUEST=4`, `AETHERGATE_MAX_IMAGE_BYTES=8 MiB`,
`AETHERGATE_MAX_TOTAL_IMAGE_BYTES=16 MiB`, `AETHERGATE_MAX_QUEUE_PAYLOAD_BYTES=24 MiB`. The
payload bound applies to text and vision payloads and accounts for base64 expansion. Oversize ->
`400 image_limit_exceeded` / `400 payload_too_large`, checked in-app (not only nginx).

### Capability enforcement
Image request requires `Capability.IMAGE` on the public **ModelAlias** and the **Provider**
(`scheduler/service._enforce_image_capability`), at admission **and** again at dispatch time
(capability may be revoked while queued). No fallback to a text-only route; images never silently
removed. Text-only requests are unaffected.

### Adapter / provider mapping
Pinned `LiteLLMChatAdapter._wire_message` emits the OpenAI `image_url` content union; provider kind
stays `ollama`; base destination/upstream model remain authoritative; retries/fallback stay
disabled; image bytes are never logged. Fake Ollama records only `last_image_count` /
`last_image_byte_lengths` / `image_request_count` (decoded lengths) — never bytes/base64.

### Quota / budget / accounting
Request quotas unchanged (1 request = 1 unit). Image requests return `estimate_input_tokens=None`,
so enforced **token** quota / token-priced budget fail closed
(`quota_token_estimator_unavailable` / `budget_token_estimator_unavailable`) — no fabricated image
tokens. Request-priced accounting, UsageRecord, signed `usage_debit`, leases/fencing, cancellation,
`outcome_unknown`, streaming, TTFT, auth + dispatch-time credential revalidation all unchanged.

### Fake-Ollama + offline golden path proof
- `tests/test_fake_ollama.py` / `tests/test_adapter.py`: real pinned LiteLLM transport reaches the
  image path; metadata-only capture; wire-mapping assertion.
- `scripts/ci/golden_path.py` adds a distinct image-capable `golden-vision-model` alias/route and
  proves: text Chat + Responses still green; Chat/Responses vision non-stream/stream; Python SDK
  vision; remote-URL + text-only-alias 400; **backup/restore preserves the vision route**;
  **post-restore vision inference** succeeds. `sdk/js/smoke.mjs` adds JS Chat/Responses vision
  non-stream/stream + remote-URL 400. Full offline golden path run green locally on a disposable
  stack (`fake-ollama`): main phase `sdk_chat_vision/…_stream`, `sdk_responses_vision/…_stream`,
  `sdk_vision_remote_url_400`, `sdk_text_only_image_400` all `ok`; post-restore vision `ok`;
  JS smoke all `ok`.

### Live nomnom proof (real internal Ollama, bypass=false)
- Installed vision model positively identified from `/api/tags` capabilities: **`qwen3-vl:latest`**
  (`capabilities: [completion, vision, tools, thinking]`). Many more vision models installed; none
  downloaded.
- Disposable image-capable provider/account/endpoint(`max_concurrency=2`)/alias/route; official
  Python SDK: Chat vision non-stream **ok**, Chat vision stream **ok** (TTFT ≈ 1.0 s), Responses
  vision non-stream/stream **ok**, remote URL → **400**, text-only alias image → **400**;
  six concurrent vision requests / two slots all succeeded (wall times 1.05–6.32 s).
- Full harness: streaming image request settled **succeeded** with exactly **1 UsageRecord** and
  **1 signed ledger entry** and **first_token_at recorded** (TTFT); route disabled while queued →
  **failed** (no dispatch/fallback); queued credential revocation → **failed / authorization_failed**
  (no upstream dispatch). Real model rejects a 1x1 PNG but accepts a small decodable PNG; fixtures
  use a tiny deterministic 16x16 solid PNG for live parity.

### OpenAPI drift / migration
- Regenerated backend OpenAPI + frontend `schema.d.ts` from the contract change; two consecutive
  generations md5-stable.
- **No migration**; head remains `0017`; `0001`–`0017` untouched. Empty → `0017` and populated
  `0016 → 0017` verified; `config_check` VALID.

### Regression counts (AGV2-028)
- backend: **657 collected, exit 0** (baseline 613 + 44 new/converted vision tests).
- frontend unit/component: **89 passed** (baseline 87 + 2 image-capability component tests).
- full Playwright: **32 passed, 5 skipped** (CI-only `alpha-golden.spec.ts`, now including vision).
- `npm run build`/`npm run lint`/`npx tsc -b --noEmit` clean; `ruff check src tests` clean;
  `git diff --check` clean; secret/content/image-data scans clean; no-legacy guard + self-test clean.

### Final remote CI (AGV2-028)
The substantive feature SHA is `7bd9fe5ba25611da0e5e5bf0f5fbaa83f36dcf7f`; all four
required workflows are `completed/success` there (push events):
- backend — run `38175168398`
- frontend — run `38175168385`
- schema-secret-gate — run `38175168307`
- alpha-golden-path — run `38175168359`

Every commit after the substantive feature SHA is documentation-only and was verified green
with the same four deterministic, offline workflows on the then-current `origin/v2` tip
(e.g. docs SHA `408c9a937268e95e652eca68d2acf952f4eb8640`: `backend 38176875175`,
`frontend 38176875089`, `schema-secret-gate 38176875240`, `alpha-golden-path 38176875139`).
The `origin/v2` tip at handoff is green for all four. One PR-event `alpha-golden-path` run
(`38175613763`, docs SHA `8d67f95`) initially hit the documented transient OIDC/JWKS browser
cold-start flake at `loginAs` in a non-vision test; `gh run rerun --failed` then passed, and
the push-event run on the identical SHA was green throughout.

### Known limitations
- Inline-only; no remote fetch, files/`file_id`, object storage, image generation, audio, video,
  tools/MCP/computer-use. Not full multimodal/OpenAI compatibility.
- Image token estimation is unavailable by design; enforced token quotas/budgets fail closed.
- `deploy/v2`/`scripts/*/v2` retain historical `v2` naming (canonical; do not rename).

### Feature PR
- PR **#2** (`v2` -> `main`): https://github.com/draeician/aethergate/pull/2 — created, includes
  the final SHA and verification summary, **not merged**. (PR #1, the AGV2-027 cutover, was
  already merged into `main` before this task; this is a new feature PR.)

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-028`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, removed only after all criteria were green, the handoff committed, commits pushed to
`origin/v2`, final remote CI verified green, and PR #2 created. Never staged/committed/pushed
(gitignored).

### Recommended next step (superseded by AGV2-028V — see the top of this file)
This section previously recommended resuming embeddings before merging PR #2. That ordering was
wrong and is reversed by AGV2-028V: **merge PR #2 (`v2` -> `main`) through the reviewed process
first**; only after that merge and post-merge `main` CI are green should embeddings be queued.

## Prior — AGV2-027 (canonical v2 cutover — retire v1)

- AGV2-027 complete: the current PostgreSQL/Alembic/scheduler implementation under
  `src/aethergate/` is the only supported AetherGate runtime. The retired v1 SQLModel/SQLite
  application and its stale operator/agent instructions are removed, a legacy-reintroduction CI
  guard is wired in, all four workflows push on `v2` + `main`, and a fresh clean-install cutover
  is proven end-to-end on a disposable stack (real Ollama) with no v1 data imported.
- Follows the verified AGV2-025 baseline SHA `07ae618` (backend 613 / frontend 87 / Playwright 32).
- **Migration head remains `0017`**; no new migration; `0001`–`0017` untouched.

## AGV2-027 — Canonical v2 cutover (retire legacy v1)

### Operator decisions
- **v1 data is disposable.** The v1 deployment was never production and holds no state worth
  preserving. The supported transition is a clean/destructive cutover to a fresh PostgreSQL
  authority. **No SQLite -> PostgreSQL importer is built** and none should be resurrected.
- **AGV2-026 Embeddings was superseded before implementation** (queued, never started). It is
  deferred, not cancelled. Per operator direction, **vision/multimodal inference is next** after
  cutover; embeddings may be resumed later.

### Legacy artifacts removed (exact)
- Runtime/code: `app/` (auth, database, main, models, routers/{admin,proxy}, services/{billing,
  limiter}), root `Dockerfile`, root `docker-compose.yml`, root `requirements.txt`, `init_db.py`,
  `manage.py`, `debug_inference.py`.
- Ad-hoc SQLite migration scripts: `scripts/add_model_config_columns.py`,
  `scripts/add_rate_limit_column.py`, `scripts/migrate_endpoints.py`.
- Stale agent instructions: `AETHERGATE_RULES.md`, `.context-resume.md`; rewrote `.cursorrules`
  and `CLAUDE.md` to point at `AGENTS.md`/`project_spec.md` only.
- Stale operator docs: `API_REFERENCE.md` (now a pointer to generated OpenAPI/current contracts)
  and `OPS_RUNBOOK.md` (now a pointer to `docs/operations/alpha-runbook.md`).
- Untracked v1-era helper scripts at the root (`aethergate_client.py`, `chat_completion.py`,
  `diagnose.py`, `inspect_routing.py`, `list_models.py`, `ollama_direct.py`, `prompt1.md`) were
  removed; `docs/development/agent-workstreams.md` now documents their supported v2 replacements.
- **Not touched:** `docs/audits/v2-architecture-audit-2026-10-03.md` (immutable historical
  evidence) and migrations `0001`–`0017`.

### Canonical paths preserved
`src/aethergate/`, `deploy/v2/`, `scripts/dev/v2`, `scripts/ops/v2` keep their historical `v2`
path component and are the canonical current paths. No root Docker/Compose aliases were added.
Tooling audit found no remaining assumptions on a root Dockerfile/Compose (all use `deploy/v2/`).

### Dependency cleanup decisions
None of `sqlmodel`, `aiosqlite`, `passlib`/`bcrypt`, `typer`, or `numpy` are in the current
`pyproject.toml` or `constraints.txt` — they were v1-only and were already absent from the locked
graph. No dependency churn; `constraints.txt` unchanged and OpenAPI drift is zero under the lock.

### No-legacy CI guard
`scripts/ci/no_legacy_v1.sh` fails if the active tree reintroduces `app/`, root legacy
`docker-compose.yml`/`requirements.txt`, retired SQLite scripts, active `app.*` imports, or active
SQLModel/aiosqlite/`MASTER_API_KEY`/`x-admin-key` references (code/config only). It exempts the
immutable audit and the migration-retirement doc, and `--self-test` proves it rejects a synthetic
legacy tree. Wired into `schema-secret-gate`. Local run: clean + self-test ok.

### Clean-install cutover proof (fresh disposable stack, real Ollama `tev1:0.8b`, bypass=false)
`scripts/ci/nomnom_acceptance.sh` against a disposable compose project/volume (never the dev DB):
- no v1 DB imported; fresh PostgreSQL; Alembic empty -> head (**0017**); `config_check` VALID;
- API/worker readiness; one-use bootstrap; deterministic OIDC browser login;
- catalog chain created (provider/account/endpoint/alias/route/price-policy, project/principal);
- inference credential created; official Python SDK Models (`golden-model`) + Chat non-stream +
  Chat stream + Responses non-stream + Responses stream all green;
- usage/ledger state present (4 -> 4 across restore, no duplicate settlement); observability
  summary populated; web console root + `/auth/callback` load (HTTP 200);
- backup succeeds; destroy + restore; API restart, worker restart, full-stack restart all recover;
  post-restart SDK inference succeeds.
- Official JS SDK smoke green (Models + Chat non-stream/stream + Responses non-stream/stream +
  Responses tools 400). Disposable stack + volume torn down afterward.

### Legacy negative proof
`app/` is absent (old `uvicorn app.main:app` cannot start), root `Dockerfile`/`docker-compose.yml`
absent, `init_db.py`/`manage.py` absent, old SQLite scripts absent, no active `app.*`/SQLModel
imports in the build path, and the frontend does not target old `/admin/*` or `x-admin-key`.

### Nomnom v1 retirement
No obsolete AetherGate v1 containers/processes were running. One positively-identified v1 image
(`aethergate-api:latest`, Cmd `uvicorn app.main:app`) with **no referencing containers** was
removed. Unrelated containers/services (langfuse, matrix-comms, paperclip, etc.) were left
untouched. The old SQLite DB was not imported. The disposable rehearsal stack was the only thing
torn down.

### Migration head
`0017` (unchanged; `0001`–`0017` untouched).

### Regression counts (AGV2-027)
- backend: **613 passed / 613 collected** (canonical `scripts/dev/v2 test`, exit 0).
- frontend unit/component: **87 passed** (22 files).
- full Playwright: **32 passed, 3 skipped** (CI-only `alpha-golden.spec.ts`).
- `npm run build`/`npm run lint`/`npx tsc -b --noEmit` clean; `ruff check src tests` clean;
  `git diff --check` clean; `scripts/ci/secret_scan.sh` clean; `no_legacy_v1.sh --self-test` clean.
- OpenAPI/client drift: two consecutive generations identical (md5 stable); regenerated committed
  OpenAPI/client produced zero diff.

### Final remote CI (AGV2-027)
The substantive cutover SHA is `f2c37446b25e4ac9b3545bb3ed3f2d8a32fa05e1`; all four
required workflows are `completed/success` there:
- backend — run `38157863695`
- frontend — run `38157863677`
- schema-secret-gate — run `38157863609`
- alpha-golden-path — run `38157863692`

Every commit after the substantive cutover is documentation-only and was verified green with the
same four deterministic, offline workflows (`26a1344` runs `38158674053`/`38158674062`/
`38158674024`/`38158674106`; `e8aeef2` runs `38159180593`/`38159180505`/`38159180483`/
`38159180504`; `4aaf3ec` runs `38159638931`/`38159638848`/`38159638893`/`38159638860`). The
`origin/v2` tip at handoff is green for all four.

### Main-branch readiness
- All four workflows now push on `v2` **and** `main`; pull-request + `workflow_dispatch` kept,
  least privilege, concurrency, locked installs, and the offline golden path preserved.
- PR **#1** (`v2` -> `main`) created: https://github.com/draeician/aethergate/pull/1 — **not
  merged** (requires the reviewed merge process).

### Known limitations
- Migration head is `0017`; no schema change in this task.
- `deploy/v2`/`scripts/*/v2` retain historical `v2` naming (canonical; do not rename).
- Embeddings remain unimplemented; vision/multimodal not yet started.
- The no-legacy guard is a deterministic structural/string check, not a full history scanner.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-027`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, removed only after all criteria were green, the handoff committed, commits pushed to
`origin/v2`, and the final remote CI verified green. It was never staged, committed, or pushed
(gitignored).

### Recommended next step
**Merge `v2` into `main` through the reviewed PR process (PR #1)**, or — if an authorized later
action has already merged it — begin **vision/multimodal inference** (embeddings remain deferred).

## Prior — AGV2-025 — OpenAI Responses API (text generation, durable queue, official SDK)

### Pinned contract / SDK baseline
- Request/response/event shapes derived from the locked official **`openai==2.54.0`** Python SDK
  (`openai.types.responses.*`, `openai.resources.responses`) and the pinned upstream OpenAPI
  baseline (`docs/contracts/openai-compatibility-baseline.md`), never from memory.
- JS smoke uses the locked official **`openai` Node SDK `7.32.0`** (`sdk/js/package-lock.json`).

### Supported request fields / input forms
- `model` (always the public `ModelAlias.name`), `input` (string OR array of text message items
  with `input_text`/`output_text` content parts), `instructions`, `max_output_tokens`,
  `temperature`, `top_p`, `stream`.
- `instructions` -> leading highest-precedence canonical `system` message (never dropped);
  `developer` -> canonical `system` (`tests/test_responses_contracts.py`, live proof).
- Unknown purely-additive fields are ignored (documented tolerance policy).

### Explicitly unsupported semantics (HTTP 400 `unsupported_feature`)
`tools`/`tool_choice`/`max_tool_calls`/`parallel_tool_calls`, image/audio/file input, output audio,
structured-output `text.format`, `reasoning`, `previous_response_id`, `conversation`, `background`,
`store=true`, `include`, `top_logprobs`, non-`disabled` `truncation`, `metadata`, `moderation`,
`service_tier`, `prompt`/`prompt_cache_*`, `safety_identifier`, `user`, `context_management`,
`stream_options`, and all provider/endpoint/base-url/header/secret transport overrides. Known
semantic fields are never silently dropped; no client may choose provider/account/endpoint/upstream
model/URL/transport secrets.

### Responses -> scheduler mapping
`ResponsesRequest -> canonical Message[] + GenerationParams -> SchedulingService.admit_and_enqueue
-> existing worker (claim_and_reserve/run_complete/run_stream) -> existing provider adapter ->
existing quotas/budgets/PriceSnapshot/UsageRecord/usage_debit ledger -> Responses-specific
response/SSE translation (API side only)`. No second scheduler, worker, accounting path, or hidden
fallback.

### Streaming event sequence
`response.created` -> `response.in_progress` -> `response.output_item.added` ->
`response.content_part.added` -> `response.output_text.delta`* -> `response.output_text.done` ->
`response.content_part.done` -> `response.output_item.done` -> `response.completed` (final
response + usage). Monotonic `sequence_number`; stable response/output/content IDs and indices; no
proprietary queue events; **no `[DONE]`**. Failed/cancelled/expired/outcome_unknown ends with
`response.failed` and never fakes success.

### Cancellation / auth
Same durable cancellation as Chat (queued/reserved never dispatches; dispatched/streaming flagged
for the owning worker). Same auth as Chat: inference-audience `agk_...`, `inference:invoke`,
project/principal attribution, dispatch-time credential revalidation, `bypass=false` in all proofs.

### Quota/budget/accounting proof
Responses participates in endpoint slots, request/token quotas, cooldown, project budget,
PriceSnapshot, UsageRecord, signed `usage_debit`, exactly-once settlement. Live run: 10 Responses
requests (6 non-stream, 4 stream) -> exactly 10 UsageRecords and 10 usage_debit ledger entries.
`max_output_tokens` feeds the same canonical output reservation bound as Chat.

### Official SDK results
- Python `openai==2.54.0`: Responses non-stream parses a typed `Response` (`output_text`,
  `usage`, `status=completed`); Responses stream parses typed events and reconstructs exact text;
  `response.completed` observed; unsupported tools raise an SDK-visible `BadRequestError` (400);
  `bypass=false` throughout.
- JS `openai@7.32.0` (`sdk/js/smoke.mjs`): Models + Chat non-stream/stream + Responses
  non-stream/stream + Responses tools 400. The web UI never imports the OpenAI SDK.

### Fake-Ollama CI proof
`scripts/ci/golden_path.py` phase main/post-restore now also run Responses non-stream + stream via
the official Python SDK. `alpha-golden-path.yml` adds a locked official JS SDK smoke step. All
offline (fake Ollama, no Internet/model download/provider credentials).

### Live Ollama proof (real internal Ollama, bypass=false)
- Chat non-stream + stream green; Responses non-stream (≈9 KB output) + stream (≈8.9 KB)
  green against `tev1:0.8b`.
- `instructions` request returns non-empty output (not dropped).
- Six concurrent Responses streams under `max_concurrency=1` visibly queue
  (queue-wait p95 ≈ 67 s) and observability reports queue-wait + TTFT samples.
- Deactivate route -> safe failure (no fallback); restore -> succeeds.
- Revoke credential -> never reaches upstream; unsupported tool -> 400 with no
  `inference_requests` row created.

### OpenAPI drift / migration
- OpenAPI + frontend generated types regenerated; two consecutive generations produce zero
  additional diff (md5 stable).
- **No migration**; head remains `0017`; `0001`–`0017` untouched.

### Regression counts (AGV2-025)
- backend: **613 collected** (baseline 561 + 13 `test_responses_contracts.py` + 17
  `test_responses_api.py` + …); canonical `scripts/dev/v2 test` exit 0.
- frontend unit/component: **87 passed**.
- full Playwright: **32 passed, 3 skipped** (CI-only alpha-golden).
- `npm run build`/`npm run lint`/`npx tsc -b --noEmit` clean; `ruff check src tests` clean;
  `git diff --check` clean; `scripts/ci/secret_scan.sh` clean.
- migration empty -> `0017`; populated `0016` -> `0017`; `config_check` valid (exit 0) and invalid
  (nonzero).

### Final remote CI
All four required workflows are `completed/success` on the substantive `origin/v2` SHA
`f59f778dfbca1d1bef4f63b5d915733655f398d0`:
- backend — run `38106324981`
- frontend — run `38106324962`
- schema-secret-gate — run `38106325011`
- alpha-golden-path — run `38106325036`

Every later commit in this task is documentation-only and was verified green with the same four
workflows; the `origin/v2` tip at handoff is green for all four (the gates are deterministic and
offline, so the result is stable). The commit series: `4039e7d` (feat api), `aa7d7ec` (test ci),
`8cad5b4` (docs baseline/readme), then handoff docs commits.

### Known limitations
- Only the Responses text-generation subset is implemented; rich semantics are explicitly
  rejected, not emulated (see `docs/contracts/responses.md`).
- `developer` maps to canonical `system` (the Ollama adapter path cannot distinguish them); the
  highest-precedence instruction slot is preserved rather than dropped.
- No Responses persistence/retrieval, background jobs, or `previous_response_id` (deferred).
- `POST /v1/embeddings` is still unimplemented (next step).

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-025`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, removed only after all criteria green and remote verified; never staged/committed/pushed.

### Recommended next step
Implement the **embeddings** surface (`POST /v1/embeddings`) through the same durable
scheduler/accounting authority, following the AGV2-025 Responses pattern.

## Prior — AGV2-024V (close Alpha 1 remote CI gates and dependency reproducibility)
- Branch: `v2`. **AGV2-024V** is complete: local regressions green, dependency/OpenAPI generation
  reproducible, and all four remote GitHub Actions workflows green on the final `origin/v2` SHA.
  `.aethergate-wip` is removed only after the final remote verification.
- **Migration head remains `0017`**; no new migration; `0001`–`0017` untouched.

## AGV2-024V — Close Alpha 1 remote CI gates and dependency reproducibility

### Why the first remote CI run failed (SHA `fb00f875513b8bec90d5284facf122850768868e`)
All four workflows were red:
- **backend** (run 38085330138): CI set `AETHERGATE_TEST_DATABASE_URL` but never created that database,
  so DB-gated tests failed (`database "aethergate_test" does not exist`); and
  `test_prod_requires_queue_key` was satisfied by the workflow-level `AETHERGATE_QUEUE_KEY`
  (ambient-environment contamination).
- **frontend** (38085330153) + **schema-secret-gate** (38085330056): OpenAPI generation removed the
  Pydantic-generated Decimal `pattern` because CI resolved a newer Pydantic stack than the environment
  that generated the committed OpenAPI. A real reproducibility defect.
- **alpha-golden-path** (38085329988): the "Generate deterministic secrets" step imported
  `cryptography.fernet.Fernet` before dependencies were installed (`ModuleNotFoundError`), so
  `deploy/v2/.env` was never written; teardown then failed on missing `POSTGRES_PASSWORD`
  interpolation.

### Dependency locking strategy
- `pyproject.toml` keeps the human-readable compatible ranges.
- `constraints.txt` (repository root) is a committed pip **constraints** file pinning every
  runtime+dev transitive dependency of the known-green Alpha environment (pydantic `2.13.5`, fastapi
  `0.142.2`, litellm `1.104.0`, …). The block after `# BEGIN LOCKED VERSIONS` is machine-managed.
- Consumed by: `backend.yml`/`frontend.yml`/`schema-secret-gate.yml`/`alpha-golden-path.yml`
  (`pip install -c constraints.txt …`), `deploy/v2/Dockerfile` (`pip install -c constraints.txt
  ".[dev]"`), and the canonical local install. No editable paths, no credentials, no `aethergate`
  entry in the lock.
- Refresh is explicit: `scripts/ci/refresh_lock.sh` rebuilds the pinned block from a clean venv, then
  OpenAPI + frontend types are regenerated and the full gate run is repeated
  (`docs/development/dependency-locking.md`).

### Exact fixes
- **backend.yml**: added a "Create explicit test database" step
  (`tests.db_helpers.ensure_database(AETHERGATE_TEST_DATABASE_URL)`) before pytest; locked install.
  DB-gated tests are not skipped.
- **tests/test_config_check.py**: `test_prod_requires_queue_key` now explicitly
  `monkeypatch.delenv("AETHERGATE_QUEUE_KEY")` and uses `_env_file=None`; an autouse fixture clears all
  relevant settings env vars and chdirs to an empty tmp dir. `test_settings.py`, `test_oidc_session.py`,
  `test_cli_session.py` got the same isolation (`_env_file=None` / clean cwd). The production queue-key
  requirement is unchanged.
- **alpha-golden-path.yml**: secrets generated with the standard library only
  (`base64.urlsafe_b64encode(os.urandom(32))` Fernet key; `secrets` for password/bootstrap; nothing
  printed). Teardown/log-dump guarded on `deploy/v2/.env` existing and non-fatal. **Two latent defects
  the step-1 failure had masked were also fixed**: (1) `OIDC_ISSUER` is now the runner host IP
  (reachable by both the browser and the api container) instead of `127.0.0.1`; (2) the empty database
  is migrated **before** the readiness wait (readiness is `503 schema_not_current` until head).
  Post-restore now restarts api/worker with the full compose file set (so the CI/web overrides survive)
  via `scripts/ops/v2 restore --keep-stopped` + `compose up -d`, and restarts `web` for nginx DNS.
  `AETHERGATE_PYTHON=python` for the Playwright SDK driver. A warm-up step primes OIDC discovery before
  the browser smoke.
- **Second remote run (after the lock + step-1 fixes) — backend**: the created test database now
  exposed two further real defects: `test_admin_auth.py::test_http_admin_key_rejected_on_inference`
  and `test_admin_crud.py::test_credential_audience_separation_via_endpoint` (plus
  `test_cli_session.py::test_cli_session_cannot_authenticate_inference`) reached
  `aethergate.api.deps`'s process-wide session factory — which pointed at the unmigrated
  `aethergate_test` in CI — and failed on `relation "api_credentials" does not exist`. Fixed by
  patching `aethergate.api.deps.get_session_factory` to the per-test database (matching the existing
  `test_openai_auth.py` pattern) so the audience rejection is proven against the test schema.
- **Second remote run — alpha-golden-path**: the browser golden smoke performed live SDK inference
  that appended usage records *after* `phase main` recorded the counts and *before* the backup, so the
  post-restore count assertion saw `2 -> 4`. Fixed by running the browser smoke **after** the
  post-restore count proof.
- **constraints.txt / Dockerfile / workflows**: locked installs (`-c constraints.txt`).
- **Secret-scan fixtures**: reviewed; exclusions remain narrow and explicit
  (`tests/test_logging_safety.py`, `tests/test_diagnostics.py`, `scripts/ci/secret_scan.sh`). All
  fixtures are obviously synthetic (`agk_live_0123456789abcdefABCDEF`, a PEM with literal
  `secretsecretsecret`). The scan is otherwise clean; the git pre-commit hook has no allowlist file, so
  the repository-native narrow allowlist is the CI scanner's explicit file list.

### Final local regression counts
- backend canonical suite (`scripts/dev/v2 test`): **561 collected**, exit 0.
- frontend unit/component: **87 passed** (22 files).
- full Playwright (`WEB_BASE_URL=http://127.0.0.1:8081`): **32 passed**, 3 skipped (CI-only
  `alpha-golden.spec.ts`).
- `npm run build`, `npm run lint`, `npx tsc -b --noEmit` clean.
- OpenAPI/client drift: two consecutive generations under the lock produced **zero diff**.
- `ruff check src tests` clean; `git diff --check` clean; `scripts/ci/secret_scan.sh` clean.
- migration empty -> `0017`; populated `0016` -> `0017` (via `tests/test_migrations.py`, green).
- `python -m aethergate.config_check` valid (exit 0) and invalid (nonzero).
- fake-Ollama adapter tests green; official OpenAI Python SDK non-stream + stream against the live
  stack with bypass `false`. Full CI golden-path simulated locally end-to-end (disposable stack:
  migrate → readiness → bootstrap → OIDC login → catalog → SDK → backup → restore → SDK), all green.

### Final remote CI
The substantive code/test/workflow commit is `b3c61d5e7409bbb0504b33c87a5361bf5105beed`;
all four required workflows `completed/success` there:
- backend — run `38092737427`
- frontend — run `38092737557`
- schema-secret-gate — run `38092737259`
- alpha-golden-path — run `38092737337`

AGV2-024V final substantive `origin/v2` code/test/workflow SHA
`b3c61d5e7409bbb0504b33c87a5361bf5105beed`:
- backend — run `38092737427`
- frontend — run `38092737557`
- schema-secret-gate — run `38092737259`
- alpha-golden-path — run `38092737337`

Every later commit in the task is documentation-only and was verified green with
the same four workflows; the `origin/v2` tip at handoff is green for all four.
The gates are deterministic and offline, so the result is stable.

(First remote run `fb00f875…` was red in all four; the intermediate run on the
lock+step-1 fixes was green for frontend/schema-secret-gate and exposed the two
follow-up defects fixed above.)

### Migration head
`0017` (unchanged; `0001`–`0017` untouched).

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-024V`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, removed only after all criteria were green and remote verified; never staged/committed/pushed.

## Prior task — AGV2-024 (Alpha 1 hardening)
- Branch: `v2`. **AGV2-024 (Alpha 1 hardening — CI, fresh install, upgrade/restore, structured logs,
  operator runbook)** is complete. `.aethergate-wip` is removed only after the final remote
  verification.
- **Migration head remains `0017`**; no new migration; `0001`–`0017` untouched. (Superseded by
  AGV2-027, which removed the retired v1 `app/` runtime; see the top of this file.)
- Repository is at **Alpha 1** (internal/self-hosted): see `docs/releases/alpha1-readiness.md` and
  `docs/operations/alpha-runbook.md`. No SemVer bump was invented (package stays `0.1.0`).
- New: `.github/workflows/` CI (backend/frontend/alpha-golden-path/schema-secret-gate), deterministic
  offline fake Ollama (`src/aethergate/testing/`), config preflight
  (`python -m aethergate.config_check`), schema-head readiness (`src/aethergate/schema_check.py`),
  structured logging + redaction (`src/aethergate/logging_config.py`), operator tooling
  (`scripts/ops/v2` backup/restore/migrate/diagnostics), safe diagnostics
  (`python -m aethergate.diagnostics`), and CI/golden-path tooling (`scripts/ci/`).

## AGV2-024 — Alpha 1 hardening

### CI (`.github/workflows/`)
- `backend.yml` — ubuntu-24.04 + Python 3.12 + PostgreSQL 16 service; `ruff check src tests`,
  `git diff --check`, `pytest`, empty -> head migration, `0016 -> 0017` migration, `config_check`,
  deterministic `scripts/gen_openapi.py`. Least-privilege `contents: read`; concurrency-cancel.
- `frontend.yml` — Node 22 `npm ci`, `npm run test`, `npm run lint`, `npm run build`,
  `npx tsc -b --noEmit`, OpenAPI/generated-client drift (fails on drift, never auto-commits).
- `alpha-golden-path.yml` — fully offline: builds images, starts postgres+api+worker+web with the
  deterministic DevOidcIdp and the fake Ollama override, migrates, runs `scripts/ci/golden_path.py`
  (bootstrap, OIDC login, catalog route, inference credential, official SDK non-stream + stream,
  dashboard load, backup, database drop/recreate, restore, post-restore state + SDK). No Internet /
  model download / provider credentials.
- `schema-secret-gate.yml` — `git diff --check`, `scripts/ci/secret_scan.sh`, workflow YAML lint
  (yamllint), OpenAPI/client drift. All workflows pin action major versions and use least privilege.
- CI is fully deterministic/offline (fake Ollama only); the live nomnom pre-release acceptance
  (`scripts/ci/nomnom_acceptance.sh`) uses the real internal Ollama and is not run per-PR.
- Remote GitHub Actions runs could not be observed from this environment; CI workflow configuration
  is validated locally (YAML parses; jobs enumerated; `scripts/ci/secret_scan.sh` and the golden-path
  phases are exercised on the real stack). No claim is made that a remote run passed.

### Deterministic fake Ollama (`src/aethergate/testing/fake_ollama.py`)
Test/CI-only local HTTP server speaking the minimum Ollama `/api/chat` + `/api/generate` surface used
by the pinned LiteLLM ollama adapter, so the **real** scheduler + adapter transport run offline with
no model download and no provider credentials. Supports deterministic non-stream, deterministic
multi-chunk streaming (`/api/generate` ndjson, which LiteLLM's ollama completion path uses),
`prompt_eval_count`/`eval_count` usage, deterministic upstream request id, configurable delay, and
configurable 404/429/other failures. It introduces no new production provider kind (emulates
`ollama`), is never referenced by production compose, and never persists/logs request content.
`tests/test_fake_ollama.py` proves the real adapter path, failure statuses, and delay against it.

### CI-safe deterministic OIDC
The existing `DevOidcIdp` is reused with a fixed CI issuer (`OIDC_ISSUER`, e.g.
`http://127.0.0.1:8090` on the runner / `http://<host-lan-ip>:18090` on nomnom), no LAN-IP discovery
requirement in CI, and a deterministic subject. The production HTTPS-issuer rule is unchanged
(`AETHERGATE_OIDC_ISSUER` must be `https://` in prod; a test asserts this is not weakened).

### Fresh-install path, config preflight, schema readiness
- `docs/operations/alpha-runbook.md` documents the clean install: prerequisites, secret generation,
  build, PostgreSQL start, empty -> `0017` migrate, API+worker start, `/health/live` + `/health/ready`,
  one-use bootstrap, bootstrap-token removal/recreation, OIDC config, catalog/provider setup,
  inference credential, official SDK non-stream + stream, TLS/reverse-proxy expectations.
- `python -m aethergate.config_check` validates DB config, queue key (valid Fernet), rejects prod
  inference-auth bypass, checks OIDC prod issuer rules, validates the bootstrap-token shape, reports
  the upstream allowlist, and warns on intentionally disabled optional features. Exit 0 valid /
  nonzero invalid; never prints secrets (`tests/test_config_check.py`).
- Readiness (`/health/ready`) now returns `503 schema_not_current` when the applied Alembic revision
  does not equal the code's expected head (`src/aethergate/schema_check.py`); the schema is still
  applied explicitly (never auto-run by workers). Readiness does not depend on upstream providers.

### Operator backup/restore + diagnostics
- `scripts/ops/v2 backup --output <p>` — PostgreSQL custom-format complete backup, explicit path,
  refuses overwrite without `--force`, 0600 permissions, never echoes the DB password.
- `scripts/ops/v2 restore --input <p> --force` — quiesces (stops) the API + worker first, replaces the
  database, reports the applied revision, and restarts api+worker; container-name overrides support
  disposable stacks. Documents that the queue key / external secret material must be backed up
  separately.
- `scripts/ops/v2 diagnostics --output <p>` / `python -m aethergate.diagnostics` — redacted safe
  bundle (version, migration state, DB-reachable boolean, sanitized feature flags, queue counts,
  observability summary, redacted logs); no credentials/content/DB URLs. `tests/test_diagnostics.py`
  proves canary absence.

### Structured logging + redaction + correlation
`src/aethergate/logging_config.py`: JSON logs in prod/alpha, human-readable dev logs; a central
`RedactionFilter` backstop scrubs credential prefixes (`agk_`/`ags_`/`agb_`), Bearer tokens, session/
CSRF cookies, OIDC fields, DB URLs/passwords, and PEM keys. An HTTP access middleware logs only the
matched route **template**, method, status, duration, and the gateway request id (never query strings,
headers, cookies, bodies, or content). Worker logs `worker.dispatch` and `request.settled` with safe
attempt/endpoint IDs. The gateway request id (response + logs) is distinct from the upstream request
id. `tests/test_logging_safety.py` canary-proves every secret and content class is absent and that the
response request id correlates with its access-log record.

### Populated 0016 -> 0017 upgrade proof
`tests/test_migrations.py::test_populated_0016_to_0017_preserves_rows` inserts representative
identity/provider/account/endpoint/alias/route/request/attempt rows at `0016`, upgrades to head, and
asserts: rows preserved, `upstream_error=false`, `upstream_status_code=null`, `first_token_at=null`
(no fake backfill), and head `0017`.

### Backup/restore + restart acceptance (clean disposable stack, real Ollama)
Ran via `scripts/ci/nomnom_acceptance.sh` on a **fresh disposable** compose project
(`ag-alpha-acceptance`, separate volume; not the accumulated dev DB):
- empty -> `0017`; bootstrap (one-use); deterministic OIDC browser login (httpx);
  catalog/provider/account/endpoint/alias/route to the real internal Ollama; inference credential;
- official OpenAI Python SDK 2.54.0 non-stream + stream `ok`/`ok_stream`, bypass `false`;
- request-priced policy -> completed inference producing `UsageRecord` + signed `LedgerEntry`;
- `scripts/ops/v2`-equivalent backup, database drop/recreate, restore; post-restore verification
  proved identities/roles/catalog/credential and **exact pre-backup counts**
  (`usage_records == ledger_entries`, no duplicate settlement), migration head `0017`, and
  post-restore SDK non-stream + stream success;
- API restart (readiness recovered), worker restart, full-stack restart (persistent volume) — SDK
  inference succeeded afterwards.
- The disposable stack was torn down (containers + volume) after the run; the long-lived dev stack and
  its database are untouched.

### Regression evidence (AGV2-024)
- Backend containerized suite (`scripts/dev/v2 test`): green, exit 0; **561 collected** (baseline 527
  + config_check/diagnostics/fake-ollama/logging-safety tests + populated 0016->0017).
- Frontend unit/component (`npm run test`): **87 passed** (baseline 87), 22 files.
- Full Playwright (`WEB_BASE_URL=http://127.0.0.1:8081`): **32 passed** (baseline 32); the new
  `e2e/alpha-golden.spec.ts` is CI-only and skips without `GOLDEN_INFERENCE_KEY` (3 skipped).
- `npm run build` clean; `npm run lint` clean; `npx tsc -b --noEmit` clean.
- OpenAPI/client drift clean (`scripts/gen_openapi.py` + `npm run generate:client` produced no diff).
- `ruff check src tests` clean; `git diff --check` clean; `scripts/ci/secret_scan.sh` clean.
- Official OpenAI Python SDK 2.54.0, inference-auth bypass `false` throughout.
- Migration head `0017`; `0001`–`0017` untouched.

### Real defect / flake root-caused
The full Playwright suite intermittently failed the first OIDC-dependent test after the API/worker
were recreated (`Sign-in failed` / landing on the login page). Root-caused to the documented
post-recreate cold start: the API caches OIDC discovery for 300s and does not cache failures, and a
recreated/reused `web` nginx or a stale JWKS/discovery momentary failure self-heals. Warming the
IdP discovery/JWKS and re-running the suite produced two consecutive clean full runs (**32 passed**).
No product defect; recorded so future runs restart `web` (nginx DNS) and warm IdP discovery first.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-024`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (gitignored).

## AGV2-023 — Operational observability (queue wait, streaming TTFT, retry, upstream health)

### Why this task exists
The web-console requirement calls for authoritative queue-wait, TTFT, retry-rate, and upstream-health
telemetry. Budget headroom was already Decimal-safe; the other three areas were labeled "Not yet
instrumented". This task adds the durable first-token/provider-outcome metadata plus bounded aggregate
read models, without logging/decrypting prompt or completion content and without introducing a second
scheduling/accounting authority.

### Migration 0017 (`0017_execution_attempt_observability.py`, `down_revision = 0016`)
Extends `execution_attempts` with safe non-content metadata:
- `first_token_at TIMESTAMPTZ NULL` — first non-empty streaming content chunk time;
- `upstream_error BOOLEAN NOT NULL DEFAULT FALSE` — true only for a terminal `ProviderError`;
- `upstream_status_code INTEGER NULL` — sanitized numeric upstream status when reliably known.

Justified indexes only: `ix_execution_attempts_started_at`,
`ix_execution_attempts_endpoint_finished` (`endpoint_id, finished_at`), and
`ix_inference_requests_queued_at`. No content/log columns. Historical rows are not backfilled with
fabricated values (`first_token_at` NULL, `upstream_error` false, `upstream_status_code` NULL). The
migration is reversible (`downgrade` drops the columns and indexes); `tests/test_migrations.py::test_migration_0016_to_0017`
proves `0016 -> 0017`, empty -> latest, and the explicit downgrade.

### First-token streaming transition
`SchedulingService.run_stream` calls `scheduler/repository.py::mark_first_token` on the first non-empty
`chunk.content`. It atomically: checks fence/attempt ownership; moves the request and attempt
`dispatched -> streaming`; sets `first_token_at` exactly once; is idempotent on repeat; refuses a
stale/foreign/terminal attempt; and never fires for a content-less chunk. Only the **presence** of
content is inspected — no prompt/completion content is persisted or logged. Encrypted StreamEvent
behavior is unchanged. A stream with no non-empty content chunk leaves TTFT NULL (never zero).

### Provider failure classification
`run_complete`/`run_stream` on `ProviderError` settle the attempt with `upstream_error=true` and persist
`upstream_status_code` when integer; `UnsupportedProvider`/`SecretResolutionError`/internal config
failures settle `upstream_error=false`. Cancellations are not upstream failures; `outcome_unknown`
stays ambiguous. Only numeric status is stored — never exception text, headers, URLs, or bodies.

### Metric definitions
- **Queue wait** = earliest `ExecutionAttempt.started_at` − `InferenceRequest.queued_at` (admission
  delay; the mutable request `started_at` is deliberately not used). p50/p95/p99 + sample count.
- **Streaming TTFT** = `first_token_at` − attempt `started_at`, streaming attempts only
  (dispatch-to-first-token; no decrypt, no terminal-completion time, no non-stream requests).
- **Retry rate** = additional `ExecutionAttempt`s for the same gateway request id
  (`attempted_requests`, `retried_requests`, `retry_attempts`, `request_retry_rate` nullable at 0
  denominator). Queued polling and new-request-id SDK retries are not counted.
- **Passive upstream health** per endpoint: sample count, succeeded/upstream-failed/ambiguous/429
  counts, nullable success rate, last success/failure, and related quota-group cooldown. No active
  probes, no synthetic percentages.
- Percentiles are computed by PostgreSQL (`percentile_cont`) in bounded, read-only, SQL-scoped
  queries; no unbounded history in Python/React. Window default 900 s (60..86400). No samples =>
  NULL percentiles (never zero).

### Admin API + RBAC
- `GET /admin/v1/observability/summary` — window + queue-wait/TTFT/retry. Uses the existing
  `admin:queue:read` permission (no new RBAC scope): `system_admin` reads the deployment aggregate or
  an explicit project; `project_admin`/`project_viewer` read only their own project; a cross-project
  project-role query is non-enumerating (`404`).
- `GET /admin/v1/observability/upstreams` — `system_admin` deployment scope only; project roles get
  `403` and cannot enumerate endpoint/provider-account health.

### Frontend
`frontend/src/pages/DashboardPage.tsx` renders the three real metric groups (queue wait, streaming
TTFT labeled dispatch-to-first-token, retry rate) plus system-admin upstream-health cards; a window
selector drives the read; `frontend/src/lib/client.ts` gains `observabilitySummary` /
`observabilityUpstreams` on the regenerated OpenAPI types; `frontend/src/lib/format.ts` gains
`formatMillis`/`formatRate` with explicit no-sample handling. Generated files
(`frontend/src/generated/*`) were regenerated, not hand-edited (drift check clean).

### Live nomnom verification (real stack; inference-auth bypass = false)
- **WIP/migration**: marker lifecycle correct; `0016 -> 0017`, empty -> latest, head `0017`.
- **Queue wait (six requests / two slots)** (`e2e/observability.spec.ts`): six concurrent official-SDK
  requests against a dedicated `max_concurrency=2` endpoint all succeed; the summary reports
  `sample_count >= 6` with non-null, ordered `p50 <= p95 <= p99`. Live DB sample snapshot (2 h):
  70 queue-wait samples, p50 ≈ 156.6 ms, p95 ≈ 85.2 s, p99 ≈ 124.6 s.
- **Streaming TTFT**: a real official-SDK stream returns `OK_STREAM`; `first_token_at` becomes
  non-null and a TTFT sample appears (live 2 h: 6 samples, p50 ≈ 110.7 ms, p95 ≈ 169.5 ms, TTFT > 0);
  no StreamEvent is decrypted for the metric and no prompt/completion content appears in the summary.
- **Upstream health**: pointing a disposable route at a nonexistent Ollama model fails the official SDK
  and increments `upstream_failed_attempts` with safe status evidence (persisted `upstream_status_code=404`,
  no raw provider body/URL); restoring `qwen3.8-2b-distill:Q6_K` succeeds and records success evidence.
- **Retry semantics**: the real scheduler pre-dispatch reclaim path (reserved attempt with an expired
  lease -> recovery reclaims -> worker re-claims) yields exactly two `ExecutionAttempt`s for one
  gateway request and `retry_attempts == 1`. Covered deterministically by
  `tests/test_observability.py::test_retry_metrics_real_scheduler_reclaim` (v2 intentionally never
  auto-retries dispatched work).
- **RBAC**: `system_admin` sees deployment summary + upstream health; `project_admin`/`project_viewer`
  see only their own summary; project roles cannot call upstream health (`403`); a cross-project
  explicit `project_id` is non-enumerating.

### Regression evidence (AGV2-023)
- Backend containerized suite (`scripts/dev/v2 test`): **527 passed, 0 failed** (baseline 506 + 20
  `tests/test_observability.py` + 1 `test_migrations.py::test_migration_0016_to_0017`; 527 collected).
- Frontend unit/component (`npm run test`): **87 passed** (baseline 82 + 5 new DashboardPage
  observability cases), 22 files.
- Full browser suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **32
  passed, 0 failed** (baseline 26 + 6 `e2e/observability.spec.ts`).
- `npm run build` clean; `npm run lint` clean; `npx tsc -b --noEmit` clean.
- OpenAPI/client drift clean: `python scripts/gen_openapi.py` + `npm run generate:client` produced no
  diff after regeneration.
- `ruff check src tests` clean; `git diff --check` clean.
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files; no key patterns in
  the changed files).
- Official OpenAI Python SDK regression (`openai 2.54.0`, Python 3.12.3), inference-auth bypass
  `false` (verified via `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` in the running api and an
  unauthenticated `/v1/models` returning `401`): non-stream + stream succeed in the observability spec.
- Migration head `0017`; `0001`–`0016` untouched.

### Real defect found and fixed (latent live-data defect)
Running the new observability six-request proof added project-A traffic, which pushed
`operator-rbac.spec.ts`'s "project_admin cancels own queued work" past the point where its
oldest-first, page-1 `waitForQueuedRequest` scan (limit 200, no state filter) could see the freshly
queued request. Root-caused as the documented live-data accumulation/pagination defect family and
fixed by filtering the queue read to `state=queued` before scanning. Two stale `outcome_unknown`
requests were holding both slots of the shared `ollama-endpoint` from abandoned prior runs; they were
reconciled through the real operator path (`aethergate.reconcile`) to free the slots.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:40953/v1`; web console loopback-only on
  `http://127.0.0.1:8081` (host `8080` occupied by `matrix-comms-element`; the committed
  `compose.web.yaml` default stays `127.0.0.1:8080`, and only the gitignored `deploy/v2/.env` redirect
  URI plus a throwaway compose `!override` port map pointed at `8081`); IdP issuer
  `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-023`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (gitignored).

## AGV2-022D — Remove final browser Decimal coercion from budget headroom

### Why this task exists
AGV2-022C closed the required regression gate, but a final code review found one remaining violation of
the phase-wide Decimal rule: `frontend/src/pages/DashboardPage.tsx` did
`const headroom = Number(budget.headroom)` and compared `headroom <= 0`. `BudgetStatusRead.headroom` is
a signed `Money` string and may be negative after overage; the browser may only make an exact
sign/zero classification, never an IEEE-754 `Number` conversion. This task closes that single dashboard
leak; it starts no new product scope (no observability, Responses, embeddings, accounting expansion, v1
migration, or redesign).

### Exact signed-Decimal comparison helper
`frontend/src/lib/decimal.ts` gains `compareDecimalToZero(value: string): -1 | 0 | 1`, a pure regex
classifier with no `Number()`, `parseFloat()`, bigint, or third-party float path. It accepts an optional
leading `-`, and returns `-1`/`0`/`1` for exact negative/zero/positive. Zero forms `0`,
`0.000000000000`, `-0`, and `-0.000000000000` are all `0`; `0.000000000001` is `1`;
`-0.000000000001` is `-1`; `123456789.123456789012` is `1` and `-123456789.123456789012` is `-1`.
Malformed input (`""`, `1e-3`, `NaN`, `Infinity`, `12,34`, `1.2.3`, `-`, `.5`, `5.`) is classified as
`0` ("not positive") so callers fail closed rather than treating garbage as an available balance.
`isValidDecimalString` / `isPositiveDecimalString` retain their existing non-negative contract.

### Dashboard behavior
`BudgetRow` now computes `blocked = budget.enabled && compareDecimalToZero(budget.headroom) <= 0`.
Exact positive => "ok"; zero or exact negative => "exhausted"; a disabled budget is never marked
exhausted solely because headroom is non-positive. The rendered headroom string is the server-returned
value verbatim (`{budget.headroom} {budget.currency} ...`); no client-side recomputation from
limit/committed/reserved.

### Runtime frontend Decimal-coercion scan
Grep of `frontend/src/` for `Number(`, `parseFloat(`, and `.toFixed(` now matches only the
`decimal.ts` module docstring (a prose mention). The only remaining numeric conversions are `parseInt`
on integer fields: `unit_scale` (PricingPage), `window_seconds` (BudgetsPage, QuotasPage),
`limit_units` (QuotasPage), `max_concurrency` (EndpointsPage), and `default_output_tokens`
(RoutesPage). No monetary Decimal field passes through `Number()`/`parseFloat()`/`.toFixed()` anywhere
in the reviewed runtime accounting surfaces (DashboardPage, `features/accounting/*`,
`features/catalog/QuotasPage`, `lib/decimal.ts`).

### Regression evidence (AGV2-022D)
- Backend containerized suite (`scripts/dev/v2 test`): **506 passed** (`506 tests collected`; clean
  green run, no failures). Baseline unchanged (frontend-only task).
- Frontend unit/component (`npm run test`): **82 passed** (baseline 74 + 8 new: 2 `compareDecimalToZero`
  decimal canaries + 6 DashboardPage budget-headroom cases), 22 files.
- Full browser suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`):
  **26 passed, 0 failed**. The first attempt had one transient `oidc_unavailable` (503) on the first
  accounting login; it was root-caused to a transient `OidcConfigurationError("OIDC discovery failed")`
  (httpx fetch of `/.well-known/openid-configuration` momentarily failed; the API caches discovery for
  300s and does not cache failures, so it self-heals). The IdP was reachable immediately after
  (`/jwks` 200, `.well-known` 200), and the full suite passed clean on the immediately following run.
- `npm run build` clean; `npm run lint` clean; `npx tsc -b --noEmit` clean.
- OpenAPI/client drift clean: `python scripts/gen_openapi.py` + `npm run generate:client` produced no
  diff on `frontend/src/generated/openapi.json` / `schema.d.ts` (no contract/route change).
- `ruff check src tests` clean; `git diff --check` clean.
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files; no key patterns in
  the changed files).
- Official OpenAI Python SDK regression (`openai 2.54.0`), inference-auth bypass `false` (verified via
  the running api `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` and an unauthenticated `/v1/models`
  returning 401): non-stream + stream against the UI-created disposable alias succeed
  (`catalog-inference.spec.ts` test 9), plus the credential lifecycle and accounting live SDK proofs.
- No migration; `0016` remains head; `0001`–`0016` untouched.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022D`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (gitignored).

## AGV2-022C — Close accounting regression gate and budget-policy disable wake-up

### Why this task exists
AGV2-022V closed its two accounting live-proof gaps and fixed two real defects, but the required full
Playwright regression was red (24 passed / 2 failed), and a final review found one adjacent scheduler
correctness hole. AGV2-022C closes exactly those three items; it starts no new product scope
(no observability, Responses, embeddings, accounting expansion, v1 migration, or redesign).

### Budget-policy disable wake semantics (real scheduler hole)
`src/aethergate/accounting/admin.py::update_project_budget_policy` now wakes budget-blocked queued
requests whenever the mutation makes the policy less restrictive:
- `new_limit > existing.limit_amount` (raise — AGV2-022V), **or**
- `existing.enabled is True and new_enabled is False` (disable — new).

Both cases call the existing scoped primitive
`scheduler/repository.py::clear_budget_wait_metadata_for_policy`, which clears wait metadata only for
`state = queued` + `wait_reason = budget_window_exhausted` + `wait_limit_id = this policy`. The worker
re-evaluation remains authoritative: if another policy still blocks, it re-applies correct metadata.
Name-only edits, limit decreases, unchanged policies, and disabled->enabled do **not** clear metadata.

### Deterministic disable-unblock regression
`tests/test_accounting.py::test_budget_window_exhausted_request_unblocks_after_policy_disable` drives
the real admin mutation path (not the repository helper directly): request price P; a budget with
`limit == P` admits the first request and queues the second as `budget_window_exhausted` (not a
terminal rejection); the first commits exactly P; the same policy is **disabled** via
`accounting_admin.update_project_budget_policy` under a deployment `system_admin` context; the cleared
wait metadata is asserted; the **same** `request_id` becomes eligible, dispatches, and settles exactly
once — exactly one new `UsageRecord` and exactly one signed negative `usage_debit`, no duplicate
settlement.

### Full Playwright suite fixes (the gating criterion AGV2-022V missed)
- **Pricing-table pagination** (`accounting.spec.ts` test 6): the pricing table accumulates policies
  across runs and paginates oldest-first at 20, so the run's new policy could fall off page 1. The test
  now scopes the pricing list to this run's route through the existing UI "Route binding" filter before
  asserting the created row, so it is deterministic as disposable policies grow.
- **management-rbac project/provider pagination** (`management-rbac.spec.ts`): after creating a
  uniquely named project/provider through the UI, the test used to assert on page 1 of the
  oldest-first, 20-per-page lists. It now uses the real UI pagination helper `goToLastPage` and asserts
  the new project and provider through the browser UI (no direct-API substitution).
- **credential lifecycle timeout** (`management-rbac.spec.ts` "inference credential lifecycle"): the
  test issues multiple live official-SDK calls against slow Ollama generation inside Playwright's
  default 60s timeout. It now sets a scoped `test.setTimeout(240_000)`; all official OpenAI Python SDK
  calls, `bypass=false`, and the old/new/revoked key assertions are unchanged (no raw `fetch`
  substitution; no global timeout inflation).

### Full browser regression (the gate)
- Full suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **26 passed,
  0 failed**, run twice consecutively with identical results. Per spec: `accounting` 8,
  `catalog-inference` 1, `management-rbac` 6, `operator-rbac` 7, `web-console` 4.
- The two AGV2-022V accounting live proofs remain green **inside the full suite**:
  same-request `budget_window_exhausted` unblock (test 7) and the released pre-dispatch reservation
  browser proof (test 8).

### Verification results (AGV2-022C)
- Backend containerized suite (`scripts/dev/v2 test`): **506 passed** (baseline 505 + 1 deterministic
  disable-unblock test); `506 tests collected`.
- Frontend: `npm run build` clean, `npm run lint` clean, `npm run test` **74 passed** (22 files).
- Browser E2E full suite: **26 passed, 0 failed** (twice).
- Official OpenAI Python SDK (`openai 2.54.0`, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` verified
  in the running api and by an unauthenticated `/v1/models` returning 401): non-stream + stream against
  the UI-created disposable alias succeed (`catalog-inference.spec.ts`), and the credential lifecycle
  proves old/new/revoked key outcomes (`management-rbac.spec.ts`).
- `ruff check src tests` clean; `git diff --check` clean; `npx tsc -b --noEmit` clean; OpenAPI/client
  generation drift clean (`scripts/gen_openapi.py` + `npm run generate:client` produced no diff on
  `openapi.json` / `schema.d.ts`; no contract/route change).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files; no key patterns in
  the changed files).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:37439/v1`; web console loopback-only on
  `http://127.0.0.1:8081` (because `matrix-comms-element` occupied `0.0.0.0:8080`; the committed
  `compose.web.yaml` default stays `127.0.0.1:8080`, and only the gitignored `deploy/v2/.env`
  redirect URI plus a throwaway compose override pointed at `8081` for this run); IdP issuer
  `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022C`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (it is
gitignored).

## AGV2-022V — Close accounting live queue-unblock and released-reservation proofs


### Why this task exists
AGV2-022's final review found two narrow **live acceptance** gaps:
1. The budget live proof used `budget_request_too_large`, a terminal *failed* request, then submitted a
   *new* request after raising the limit. That does not prove the **same queued**
   `budget_window_exhausted` request becomes eligible after a policy edit.
2. There was no live/browser proof of a **real pre-dispatch released BudgetReservation** rendered with
   `price_snapshot_id = null`, zero reserved/committed amounts, no UsageRecord, and no `usage_debit`.

These two are now closed (see below), plus the real scheduler and nginx defects the proofs exposed.

### Semantics distinction (recorded truthfully)
- `budget_request_too_large` = fail-fast for a request that cannot fit even an empty budget window
  (terminal rejection).
- `budget_window_exhausted` = the queued temporary-capacity condition; the same request resumes after a
  policy edit admits it. Only this scenario is called "block/unblock".

### Real scheduler defect fix (found by the live unblock proof)
`src/aethergate/scheduler/repository.py` gains `clear_budget_wait_metadata_for_policy(session,
budget_policy_id)`; `src/aethergate/accounting/admin.py::update_project_budget_policy` calls it when
`new_limit > existing.limit_amount`. Before this, raising the cap left the queued request's
`next_eligible_at` pointing at the window reset, so the worker never reclaimed it. Deterministic
coverage: `tests/test_accounting.py::test_budget_window_exhausted_request_unblocks_after_policy_raise`
(first request commits P; second queues `budget_window_exhausted`; limit raise + metadata clear; the
**same** request_id dispatches and settles exactly once).

### Real nginx proxy defect fix (found by the live SDK proof)
`frontend/nginx.conf` sets `proxy_read_timeout 600s` / `proxy_send_timeout 600s` on the `/v1/` location.
The gateway holds non-stream requests synchronously while a queued request waits for admission and the
upstream model generates (no bytes are flushed until the terminal result). The nginx default 60s cut
those held connections with a 504, causing the official SDK to retry (new request_id each time). The web
image was rebuilt and the `aethergate-v2-web` container recreated to apply it.

### Live proofs (browser UI + official OpenAI Python SDK, inference-auth bypass = false)
- **Budget unblock — same request** (`accounting.spec.ts` test 7): read the route's enabled request
  price P; create a budget with limit == P through the UI; first official SDK request commits P; spawn a
  second official SDK call and keep it pending; poll the queue API for the `budget_window_exhausted`
  request and capture its stable `request_id` (targeted by `waitForBudgetQueuedRequest` via
  `wait_limit_id`, and the row filter now uses the unique `budgetName` — filtering by the price string
  is ambiguous because committed_amount equals P on older leftover rows and the list is ordered
  oldest-first); prove queued/`budget_window_exhausted`/`wait_limit_metric=budget`/no error/no usage;
  raise the **same** policy limit through the UI; await the original pending SDK call and prove it
  succeeds (no replacement request); prove exactly one new UsageRecord and one signed `usage_debit`
  (`-P`), and the server-exact committed/headroom values.
- **Released reservation — real lifecycle + browser** (`accounting.spec.ts` test 8, new):
  `src/aethergate/live_accounting_release.py` is a controlled harness using the real
  scheduler/accounting path (enqueue → `claim_and_reserve` → reserve + snapshot → direct SQL revert to
  the reserved pre-dispatch window → `request_cancellation`), with a `NoopAdapter` that asserts
  `complete`/`stream` are never called (no upstream dispatch). Workers are stopped/restarted around it
  (`runReleaseHarness`/`listWorkerContainers` in `fixtures.ts`, using direct `docker stop`/`start` to
  avoid the podman `service_healthy` wait). The harness prints identifiers only (JSON, no secret/prompt/
  completion) and asserts the released invariants. The browser Reservations page then proves the same
  real reservation renders: state `released`, `reserved_amount = 0`, `committed_amount = 0`, and
  `price_snapshot_id = null` shown as "No snapshot / released before dispatch".

### Verification results (AGV2-022V)
- Backend containerized suite (`scripts/dev/v2 test`): **505 passed** (baseline 504 + 1 deterministic
  budget-unblock test). `505 tests collected`.
- Frontend: `npm run build` clean, `npm run lint` clean, **74 unit/component tests pass** (22 files).
- Browser E2E — `accounting.spec.ts`: **8 passed** (was 7; the new released-reservation proof makes 8).
- Browser E2E — full suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`):
  **24 passed, 2 failed** at the time. > **Correction (AGV2-022C):** AGV2-022V was therefore **not**
  fully complete — the required full-browser regression gate was red. Both failures were real,
  deterministic-under-load defects (not "pre-existing and unrelated" flakes as first recorded), and
  both are now fixed in AGV2-022C, which closes the full suite at **26 passed, 0 failed** (twice):
  - `management-rbac.spec.ts` "creates a project and a provider through the UI" failed because
    accumulated disposable projects exceed the oldest-first 20-per-page list; fixed in AGV2-022C with
    the real UI pagination helper `goToLastPage`.
  - `management-rbac.spec.ts` "inference credential lifecycle" intermittently exceeded the default 60s
    timeout across multiple slow live SDK calls; fixed in AGV2-022C with a scoped
    `test.setTimeout(240_000)`.
  Additionally, the accounting pricing-table assertion (test 6) had the same accumulation/pagination
  root cause and was fixed in AGV2-022C by scoping the pricing list to the run's route through the UI
  filter.
- Official OpenAI Python SDK (`openai 2.54.0`): the budget-unblock proof drives a **pending** non-stream
  call that stays held through the browser policy edit and then succeeds; the released-reservation proof
  asserts no upstream dispatch occurred.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean from
  this task (no contract/route change; `frontend/src/generated/openapi.json` and `schema.d.ts` are
  unchanged in this diff). Secret/token/content canary scan clean.
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:43591/v1`; web console loopback-only on
  `http://127.0.0.1:8081`; IdP issuer `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022V`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified.

## AGV2-022 — Accounting management UI

### Implementation commits (origin/v2)
- `feat(web): add accounting management UI` — accounting feature module
  (`frontend/src/features/accounting/`), Decimal-safe helpers (`frontend/src/lib/decimal.ts`), typed
  accounting client methods + `roles.ts` guards, role-aware routing/navigation, accounting
  unit/component tests, and the live browser E2E (`frontend/e2e/accounting.spec.ts`).
- `fix(accounting): serialize money canonically and sign usage ledger debits` — canonical fixed-point
  money JSON serialization and signed `usage_debit` ledger entries, with regression tests.

### Routed pages / features
- Accounting configuration: `/accounting/pricing`, `/accounting/snapshots`, `/accounting/budgets`.
- Accounting history/observability: `/accounting/reservations`, `/accounting/usage`,
  `/accounting/ledger`, `/audit`.
- Pages: `PricingPage`, `PriceSnapshotsPage`, `BudgetsPage`, `BudgetReservationsPage`, `UsagePage`,
  `LedgerPage`, `AuditPage` (all under `frontend/src/features/accounting/`).
- Typed client methods in `frontend/src/lib/client.ts`; role guards in `frontend/src/lib/roles.ts`.

### Decimal-safe browser handling
- Money is fixed-point Decimal and stays a string end-to-end. `frontend/src/lib/decimal.ts` provides
  `isValidDecimalString`, `isPositiveDecimalString`, and `formatMoney` without `Number()`/`parseFloat()`.
- Form state for money/prices is string; validation rejects lossy/float syntax; exact strings are sent
  to the backend; rendered read models are verbatim. Canary tests use `0.000000000123` and
  `123456789.123456789012`.

### Backend accounting corrections
- **Canonical money serialization** (`src/aethergate/domain/value_objects.py`): `Money`,
  `NonNegativeMoney`, and `PositiveMoney` now serialize to JSON as canonical fixed-point strings via
  `PlainSerializer(_serialize_money, return_type=str, when_used="json")` (`format(value.normalize(),
  "f")`). This removes Pydantic's auto `pattern` from money fields in
  `frontend/src/generated/openapi.json`; `schema.d.ts` is unchanged. Regression test:
  `tests/test_contracts.py::test_money_serializes_as_canonical_fixed_point`.
- **Signed usage ledger debits** (`src/aethergate/scheduler/service.py`): the `usage_debit`
  `LedgerEntry` is now written with a negative amount (`amount=-amount`) to match the documented
  "signed" ledger convention (`docs/architecture/accounting.md`). Regression assertion added to
  `tests/test_accounting.py::test_request_priced_budget_reserves_and_queues_exhausted`.

### Live proofs (browser UI + official OpenAI Python SDK, inference-auth bypass = false)
- **Pricing + immutable snapshots** (`accounting.spec.ts`): create a request-priced `PricePolicy`
  through the UI with the exact Decimal `0.000000000123`; official SDK request succeeds; the resulting
  `PriceSnapshot` captures the old price; edit the policy to `0.000000000456`; a second SDK request
  captures the new price; the old snapshot is verified immutable and unchanged.
- **Budget block/unblock** (`accounting.spec.ts`): create a project budget through the UI with a limit
  below the request price; an official SDK request is blocked (`budget_request_too_large`); the budget
  status stays zero-committed; raise the limit through the UI; the same request succeeds; committed
  spend, usage, and the (negative) `usage_debit` ledger entry reflect the exact price.
- **RBAC + audit scope** (`accounting.spec.ts` tests 1–5): system_admin sees the full surface;
  project_admin cannot access deployment pricing/snapshot surfaces and cannot enumerate project B;
  project_viewer is read-only; a project role sees only own-project audit events.

### Verification results (AGV2-022)
- Backend containerized suite (`scripts/dev/v2 test`): **504 passed** (baseline 503 + 1 money
  serialization test).
- Frontend: `npm run build` clean, `npm run lint` clean, **74 unit/component tests pass** (22 files;
  baseline 54 + 20 accounting tests).
- Browser E2E (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **25 passed**
  (baseline 18 + 7 accounting): 4 `web-console`, 7 `operator-rbac`, 7 `management-rbac`, 1
  `catalog-inference`, 7 `accounting`.
- Official OpenAI Python SDK (`openai 2.54.0`): non-stream + stream succeed; the accounting live proofs
  exercise non-stream success plus `budget_request_too_large` fail-closed and recovery after a limit
  raise.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean
  (`openapi.json` only lost the auto `pattern` fields from the money serialization change; `schema.d.ts`
  unchanged).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files, no key patterns in
  the new/modified files).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:39569/v1`; web console loopback-only on
  `http://127.0.0.1:8081`; IdP issuer `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified.

## AGV2-021V — Management lifecycle verification gaps
(unchanged; see the prior handoff section below for the full record)

### Why this task exists
AGV2-021's final review found three narrow acceptance gaps in the **live proof** (not the core UI):
1. the catalog → inference E2E reused the pre-provisioned Ollama provider/account/endpoint instead of
   creating the disposable path through the UI;
2. the credential create/rotate/revoke lifecycle used direct admin API calls instead of the browser UI;
3. `catalog-inference.spec.ts` used raw `fetch`, but the handoff called that an "official OpenAI Python
   SDK" proof.

These three are now closed with truthfully worded evidence (see below).

### Full disposable catalog path through the browser UI
`frontend/e2e/catalog-inference.spec.ts` (system_admin OIDC browser session against the live stack) now
creates the complete disposable path through the actual web pages, using unique names per run
(`e2e-disp-*` + `Date.now()`):
1. **Provider** — `/catalog/providers`, kind `ollama`, capability `text` (reuses the existing Ollama
   adapter; no new backend adapter was invented).
2. **Provider account** — `/catalog/provider-accounts`, no `SecretRef` (local Ollama needs no provider
   secret; raw provider secrets are never placed in browser fields, DB metadata, logs, or Git).
3. **Endpoint** — `/catalog/endpoints`, account = the new account, base destination = the allowlisted
   nomnom Ollama destination `http://192.168.22.50:11434`, `max_concurrency = 1`, active; backend
   egress policy remains authoritative.
4. **Model alias** — `/catalog/models`, client-visible alias `e2e-disp-alias-<runId>`.
5. **Route binding** — `/catalog/routes`, using only the new alias/account/endpoint; upstream model
   `qwen3.8-2b-distill:Q6_K`.

The test resolves the created IDs via `adminJson` postcondition inspection and asserts the new route
uses only the new account/endpoint (not the pre-provisioned `ollama-account` / `ollama-endpoint`), so
no hidden fallback to the pre-existing route is possible.

There is no DELETE surface for catalog resources, so the test leaves the disposable route and endpoint
**inactive** at the end (Active toggle through the UI) rather than mutating rows directly.

### Official Python SDK against the UI-created alias
`frontend/e2e/sdk_inference.py` is a small official `openai` SDK driver invoked by
`frontend/e2e/fixtures.ts` `runOfficialSdk`. It reads the base URL, key, model, and stream flag from
the environment (never argv), and prints a single redacted status line (`OK`, `OK_STREAM`, or
`FAIL <Class> status=<int> code=<code>`). No raw key is ever printed.

- **Official SDK version**: `openai 2.54.0` (Python 3.12, repo venv).
- `base_url` = the web console origin `/v1` (proxied through nginx), model = the disposable alias.
- **inference-auth bypass = false** throughout.
- Non-stream succeeds (`OK`) and stream succeeds (`OK_STREAM`) against the disposable alias.
- Deactivate the RouteBinding through the UI → the official SDK fails safely with `model_unavailable`
  (no fallback); restore/activate → the official SDK succeeds again.

This is genuine **official OpenAI Python SDK** evidence, distinct from the raw `fetch` proof in
AGV2-021 (see the correction note under AGV2-021 below).

### Credential lifecycle entirely through the browser UI
`frontend/e2e/management-rbac.spec.ts` adds "inference credential lifecycle → create/rotate/revoke
through the UI with official SDK outcomes". A disposable project + principal are provisioned via admin
fixtures (allowed), but every lifecycle action is performed through the browser UI:

- **Create** — `/credentials?project=<disposable>`: New credential, select principal, audience =
  inference, Create; the one-time raw key is captured only from the `RevealSecret` UI in test memory.
  Leak canary: key absent from URL/history, `localStorage`/`sessionStorage`, console messages, and the
  DOM after the reveal is closed.
- **Use** — official SDK with the new key against the live alias `gpt-4` succeeds.
- **Rotate** — Rotate → confirm → new one-time key from `RevealSecret`; old key fails `401` and the new
  key succeeds via the official SDK.
- **Revoke** — Revoke → confirm; the rotated key fails `401` immediately.

No direct `adminJson` create/rotate/revoke substitutes the lifecycle actions being proven.

### Operator-rbac robustness fix
`frontend/e2e/operator-rbac.spec.ts` assumed a single endpoint (it paused
`queue/endpoints` `items[0]` and clicked a unique `Pause` button). Because AGV2-021V's catalog E2E now
legitimately leaves a second (inactive) endpoint, the spec was made robust:
- `endpointId` is resolved through the live `gpt-4` route binding (`resolveRouteEndpointId`), not
  `items[0]`;
- the pause/resume UI test scopes `Pause`/`Resume` to the first available button (`.first()`).

This is test-only; no product code changed.

### Verification results (AGV2-021V)
- Backend containerized suite (`scripts/dev/v2 test`): **503 passed** (exit 0; the `-q -q` summary line
  is swallowed by docker-compose output; the count is confirmed by a clean 100% run).
- Frontend: `npm run build` clean, `npm run lint` clean, **54 unit/component tests pass** (18 files).
- Browser E2E (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **18 passed** —
  4 `web-console`, 7 `operator-rbac`, 7 `management-rbac` (6 prior + 1 lifecycle), 1 `catalog-inference`.
- Official OpenAI Python SDK (`openai 2.54.0`): non-stream + stream succeed against the disposable
  alias; deactivate → `model_unavailable`; restore → success; old/rotated/revoked key outcomes proven.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean
  (`python scripts/gen_openapi.py` from repo root + `npm run generate:client` produced no diff on
  `openapi.json` / `schema.d.ts`).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files, no key patterns in
  the new/modified files).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral (`http://127.0.0.1:46291/v1`); web console loopback-only on
  `http://127.0.0.1:8081` (because `matrix-comms-element` occupied `0.0.0.0:8080`; the committed
  `compose.web.yaml` default stays `127.0.0.1:8080`, and only a gitignored `deploy/v2/.env` redirect
  URI plus a throwaway compose override pointed at `8081` for this run); IdP issuer
  `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-021V`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified.

## AGV2-021 — Management UI I

### Implementation commit(s) (origin/v2)
- `feat(web): add identity and catalog management UI` — feature modules
  (`frontend/src/features/identity/`, `frontend/src/features/catalog/`), shared UI primitives
  (`frontend/src/components/ui/`), typed management client methods + `roles.ts` guards, role-aware
  navigation/routing, and the management + catalog unit/component tests.
- `test(web): add management RBAC and catalog-inference browser E2E` — the initial browser E2E.
- `test(web): fix operator-rbac queue pagination fragility` — the pre-existing
  `operator-rbac.spec.ts` "project_admin cancels own queued work" test filtered by queue state before
  and after cancel, so it no longer depends on the freshly submitted request landing on page 1 of an
  oldest-first, 20-per-page queue (it broke as historical traffic accumulated).
- `docs(web): document identity and catalog management UI` — the handoff plus `docs/web-console.md`,
  `docs/development/README.md`, and `frontend/README.md` updates.

> **Correction (recorded in AGV2-021V):** the AGV2-021 handoff called the catalog → inference proof an
> "official OpenAI Python SDK" proof, but AGV2-021's `catalog-inference.spec.ts` used raw
> `fetch`/`submitInference` (raw OpenAI-compatible HTTP) and reused the pre-provisioned
> provider/account/endpoint. That wording overstated the evidence. AGV2-021V replaces it with the
> genuine official-`openai`-SDK proof against a fully UI-created disposable path.

### Routed pages / features
- Identity: `/projects`, `/projects/:projectId`, `/principals`, `/principals/:principalId`,
  `/credentials`, `/roles`.
- Catalog (`system_admin` only): `/catalog/providers`, `/catalog/provider-accounts`,
  `/catalog/endpoints`, `/catalog/quotas`, `/catalog/models`, `/catalog/routes`.
- Shared primitives under `frontend/src/components/ui/`: `Modal`, `ConfirmDialog`, `ErrorBanner`,
  `EmptyState`, `StatusBadge`, `RevealSecret`, `Form`, `Pagination`.
- Typed client: `frontend/src/lib/client.ts` (all management methods + `apiErrorMessage`); role
  guards: `frontend/src/lib/roles.ts` (`isSystemAdmin`, `isProjectAdmin`, `canManageIdentity`,
  `canManageCatalog`).

### RBAC behavior
- `system_admin` sees and may mutate identity + catalog management. `project_admin` sees only
  project-scoped identity surfaces and is denied catalog/deployment mutations; `project_viewer` is
  read-only. Nav hiding is cosmetic; the backend `403`/non-enumeration (`404`) remains the boundary.
- Proven live in `management-rbac.spec.ts`: system_admin create project/provider/credential + raw-key
  reveal + revoke + grant/revoke role; project_admin manage own principals but cannot enumerate B and
  is denied catalog APIs (`403`); project_viewer read-only and denied direct mutations (`403`).

### One-time credential reveal
- Create/rotate return the raw key once, shown only in a `RevealSecret` modal with Copy and a
  "cannot be retrieved again" note; destroyed on close. Never in URL/history/web storage/logs; no
  "show existing key" control. The leak canary proves it does not survive reload/navigation/storage
  inspection and is absent from non-secret list/detail responses.

### SecretRef boundary
- Provider-account configuration selects existing `SecretRef` metadata (stable opaque ID) only; no
  plaintext provider-secret input, no raw provider-secret display, no internal secret backend
  material exposed. Raw secret storage remains deferred (truthful boundary).

### Catalog form / invariant handling
- Endpoint list distinguishes catalog `is_active` from queue runtime `operational_state`.
  Pause/drain/resume remain queue/operator actions. Route-binding/account/quota mismatches and the
  one-active-route rule render stable structured errors (`409 active_route_conflict`,
  `400 parent_mismatch`, `400 destination_denied`) with no client-side fallback chain. Edit forms
  send only changed fields (PATCH semantics); server-side pagination everywhere.

## Issues / Risks
- **Web Docker build**: `vite.config.ts` must alias `cookie` and `set-cookie-parser` to their
  concrete CommonJS entries, or `react-router` fails to resolve them under Rollup
  (`Rollup failed to resolve import "cookie"`). The aliases are committed; rebuild with
  `docker build --no-cache -t aethergate-v2-web:local frontend/`.
- **Live SDK inference speed**: the nomnom Ollama `qwen3.8-2b-distill:Q6_K` non-stream generation is
  slow enough (tens of seconds per call under load) that the live-proof specs need a generous
  `test.setTimeout(240_000)` (`accounting.spec.ts` tests 6/7 and `catalog-inference.spec.ts`). If Ollama
  is reloading the model (cold start) the first SDK call is noticeably slower.
- **Docker healthcheck under this environment**: `docker compose up` waits on service healthchecks
  that the host's systemd-less Docker cannot run (`dial unix /run/user/1000/systemd/private: connect:
  connection refused`), so containers may show `(starting)` while fully functional. Bring the stack up
  with manual `docker start`/compose `up -d --no-deps` rather than `scripts/dev/v2 up`.
- **API recreation and OIDC web callback**: recreating the API with only `compose.yaml` drops
  `AETHERGATE_OIDC_WEB_CALLBACK_PATH=/auth/callback` (it lives in `compose.web.yaml`), which breaks
  browser OIDC login. Recreate the API with both `-f compose.yaml -f compose.web.yaml`, then restart
  the `web` container so nginx re-resolves the `api` DNS name.
- **Operator-rbac single-endpoint assumption** (fixed in AGV2-021V): the spec paused
  `queue/endpoints` `items[0]` and clicked a unique `Pause` button, which broke once the catalog E2E
  left a second endpoint. It now resolves the `gpt-4` route's endpoint and scopes buttons with
  `.first()`. Future endpoint-adding tests should keep this in mind.
- **Operator-rbac queue pagination** (fixed in AGV2-021): the queue page sorts oldest-first and
  paginates at 20, so the pre-existing "project_admin cancels" test depended on the new request
  landing on page 1. The test now filters by queue state before/after cancel.
- Live E2E runs accumulate disposable test data (providers/accounts/endpoints/aliases/credentials/
  queue requests/price policies/budgets). The specs are idempotent, use unique names, scope
  assertions to the current run's route, and disable leftover enabled budgets; the disposable
  route/endpoint are deactivated at the end of the catalog E2E. The pricing table can still accumulate
  leftover price policies across aborted runs (assertions must stay route-scoped).
- **Project list pagination (fixed in AGV2-022C)** — `management-rbac.spec.ts` "creates a project and a
  provider through the UI" used to assert `getByText(projectName)` on page 1 after creating a project,
  but the projects list sorts oldest-first at 20 per page and disposable projects accumulate. It now
  uses `goToLastPage` (real UI pagination) before asserting the new project and provider through the
  browser. Same root-cause family as the AGV2-021 operator-rbac queue pagination fix.
- **management-rbac SDK lifecycle test timeout (fixed in AGV2-022C)** — "inference credential lifecycle"
  issues multiple live `gpt-4` SDK calls (each routed to slow `qwen3.8-2b-distill:Q6_K`) inside the
  default 60s test timeout. It now sets a scoped `test.setTimeout(240_000)`; the SDK calls,
  `bypass=false`, and key assertions are unchanged (no global timeout inflation).
- **Accounting pricing-table pagination (fixed in AGV2-022C)** — `accounting.spec.ts` test 6 asserted the
  run's new PricePolicy on page 1 of the oldest-first, 20-per-page pricing table, which accumulates
  policies across runs. It now scopes the table to the run's route through the existing UI "Route
  binding" filter before asserting, so it is deterministic as leftover policies grow.
- **nginx proxy_read_timeout (fixed in AGV2-022V)** — the web nginx must not use the 60s default for
  `/v1/`; the gateway holds non-stream requests while queued/upstream-generating. `proxy_read_timeout
  600s` / `proxy_send_timeout 600s` are now set; recreate the web image+container to apply.
- **Budget list oldest-first pagination (fixed in AGV2-022V)** — the budget list is ordered oldest-first;
  the budget-unblock proof now targets its row by the unique per-run `budgetName`, not the price string
  (committed_amount equals P on older leftover rows).
- Existing AGV2-020V risks still apply: dev IdP key regeneration vs API JWKS cache; the Chromium
  `--disable-software-rasterizer` rAF fix; do not scale workers via `scripts/dev/v2 workers` (use
  `docker compose up -d --scale worker=N --no-deps worker`); host port `8080` may be occupied.
- `ruff format` remains out of scope (pre-existing reformat); `ruff check` is the gate.

## Key files
- Observability (new): `src/aethergate/observability/service.py`,
  `src/aethergate/observability/repository.py`, `src/aethergate/api/observability_admin.py`,
  `src/aethergate/migrations/versions/0017_execution_attempt_observability.py`,
  `tests/test_observability.py`, `frontend/e2e/observability.spec.ts`,
  `docs/architecture/observability.md`.
- Scheduler observability metadata: `src/aethergate/scheduler/repository.py`
  (`mark_first_token`, `settle` with `upstream_error`/`upstream_status_code`),
  `src/aethergate/scheduler/service.py` (`run_stream` first-token transition; provider-failure
  classification), `src/aethergate/persistence/models.py` (`ExecutionAttempt` columns/indexes).
- Frontend: `frontend/src/pages/DashboardPage.tsx` (+ `.test.tsx`), `frontend/src/lib/client.ts`,
  `frontend/src/lib/format.ts`, `frontend/src/generated/openapi.json` / `schema.d.ts`.
- E2E: `frontend/e2e/observability.spec.ts` (new), `frontend/e2e/operator-rbac.spec.ts`
  (`waitForQueuedRequest` state-scoped), `frontend/e2e/web-console.spec.ts` (placeholders replaced).
- Frontend accounting: `frontend/src/features/accounting/*` (7 pages + 3 test files),
  `frontend/src/lib/decimal.ts` (now incl. `compareDecimalToZero`), `frontend/src/lib/client.ts`,
  `frontend/src/lib/roles.ts`, `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`.
- Backend: `src/aethergate/domain/value_objects.py` (canonical money serialization),
  `src/aethergate/scheduler/service.py` (signed usage debits),
  `src/aethergate/accounting/admin.py` (clears wait metadata on limit raise **and policy disable**),
  `src/aethergate/live_accounting_release.py` (live released-reservation harness),
  `tests/test_contracts.py`, `tests/test_accounting.py`.
- Docs: `docs/architecture/observability.md`, `docs/architecture/scheduler.md`,
  `docs/architecture/admin-api.md`, `docs/web-console.md`, `docs/development/README.md`,
  `frontend/README.md`.

## Recommended Next Step
Alpha 2 feature work: the next milestone is the **Responses API** (or embeddings), built on the now
Alpha-1-hardened foundation (deterministic offline CI + golden path, config preflight, schema-head
readiness, structured logging/redaction, backup/restore, and the operator runbook). Reuse the
now-verified scheduler read model, typed `/admin/v1` + generated-client patterns, the fake-Ollama
offline fixture, and the official-SDK + backup/restore acceptance harnesses, starting from the green
regression baseline: backend **561**, frontend **87**, full Playwright **32**, migration head
**0017**. Do not start Responses/embeddings/v1-migration in AGV2-024 (explicitly deferred).
