# AetherGate — Active Task

## Identity

- **Task:** AGV2-031-PM1 — correct post-merge Beta 1 release-gate regression.
- **Status:** BLOCKED RELEASE — implementation not started on this corrective branch.
- **Repository:** `draeician/aethergate`
- **Only task branch:** `fix/agv2-031-postmerge-beta-gate`, created exactly from reviewed post-AGV2-031 `main` merge SHA `5695637e821e8c4dd33051e3d937cf3b6c88c628`.
- **Original AGV2-031 PR:** https://github.com/draeician/aethergate/pull/5 (merged); do not modify or reopen it.
- **Current Beta 1 state:** code is merged, but Beta 1 NOT tagged, published or deployed. Release gate is RED on new main; Beta GO/NO-GO is HOLD.
- **Historical `v2` branch:** retired. Never push to it or directly to `main`.

## FIRST local repository action: WIP marker

Before syncing, checking out branches or implementing changes, create the gitignored root `.aethergate-wip` with `task=AGV2-031-PM1`, `branch=fix/agv2-031-postmerge-beta-gate`, and UTC start time. Keep it until all corrective completion gates, final handoff and final-head PR CI pass. Never commit it or include credentials. Follow `AGENTS.md` and `project_spec.md`; reread this file and `docs/development/agent-handoff.md` after compaction.

## Verified failure — exact post-merge main CI

Merged commit: `5695637e821e8c4dd33051e3d937cf3b6c88c628`.

GitHub **push-event** workflows at this SHA:
- schema-secret-gate `38681868088`: completed / SUCCESS.
- frontend `38681868242`: completed / SUCCESS.
- beta-release-gate `38681868099`: completed / **FAILURE**.
- backend `38681868122`: last independently observed in_progress.
- alpha-golden-path `38681868090`: last independently observed in_progress.

GitHub Beta job `116247985882` failed in `scripts/ci/beta_release_gate.sh`, after:
1. production `config_check RESULT: VALID`, bootstrap/SDK smoke, worker stop;
2. baseline snapshot `requests_total=4; queued_total=0; usage=4; ledger=4; attempts=4`;
3. legitimate inference credential enqueues a request; barrier `requests_total=5; queued_total=1; in_flight=0` (at 2026-10-15T04:32:49Z);
4. PostgreSQL stop; readiness `503`; legitimate credential request fails closed HTTP `500`;
5. after PostgreSQL restart, `beta_db_outage.py --stage verify-persisted` exits with **`FAIL: persisted queued request was lost during the outage`** (at 2026-10-15T04:32:52Z). No logs currently establish which terminal state replaced QUEUED.

PR #5's final-head `d3319091a91baabe8f29adeec023dc584d5626f2` passed all five **PR-event** CI workflows, but this post-merge main push-run failed. Never count earlier green PR CI as a substitute for this red push-run; never claim Beta 1 released.

## Root-cause investigation — REQUIRED

The new test currently uses a background HTTP `curl --max-time 600` to submit synchronous Chat Completions while the worker is deliberately stopped. Once PostgreSQL drops, the API-side `wait_for_terminal` may raise and the `finally` block in `src/aethergate/api/openai_chat.py` calls `_schedule_durable_cancel`. The scheduler intentionally cancels queued requests on client failure/disconnect. **This is a hypothesis, not a verified root cause.** Inspect actual post-outage request IDs/states and client life cycle before changing code.

First determine whether the missing queued request was CANCELLED, EXPIRED, DISPATCHED, or otherwise mutated, and exactly why. Instrument safe state/count logs in the disposable test (never output credentials or payloads), add a reproducible deterministic failing test, and preserve correct cancellation/durable-accounting semantics. Do NOT suppress or weaken legitimate disconnect cancellation to make the gate green.

## Minimal supported correction

- Make the release-gate's persisted-queued-work proof **reliably deterministic and faithful to product behavior** through PostgreSQL outage and restore. If the long-lived HTTP waiter cannot safely survive a DB outage by design, arrange a durable queued fixture **without a live cancellable API waiter** using a disposable-only test harness against the real scheduler/persistence layer. Continue to prove a valid issued inference credential returns 200 before the outage, gets a real 5xx (NOT 401/403/000) while PostgreSQL is down, and 200 after recovery. Confirm rejected requests leave zero rows/attempts/ledger entries.
- Prove the queued request is preserved across DB stop/start, drains **exactly once** with one attempt, one usage record, one ledger entry and no spurious `outcome_unknown`. Preserve a *separate* in-flight-post-dispatch outage test with conservative `OUTCOME_UNKNOWN` and no double settlement.
- Explicitly test normal client cancellation semantics on DB failure so the fixture does not hide a genuine lost-work/scheduler defect. Tighten failure diagnostics to identify observed state and include no secrets.
- Reproduce the prior red case before the fix; add targeted regression and test repeatability. Run the disposable Beta gate at least 3 times on fresh projects with no manual retries treated as success. Do not touch long-lived nomnom/developer projects.
- Maintain `prod` hardcoded policy, file secrets, literal-IP egress, no auth bypass, isolation, read-only/non-root, migration head `0017`, no new provider downloads.
- Full canonical `scripts/dev/v2 test`, ruff, frontend lint/test/build/TS, schema/canary, Alpha golden, Beta release gate, migration/restore, official SDK supported paths. On **exact final PR head** require all five PR-event workflows completed/success.
- Update `docs/operations/beta1-runbook.md` and `docs/releases/beta1-readiness.md` only where verified behavior changes. Update the handoff with reproduction, root cause, red-before/green-after evidence, exact commits, run IDs and any limitation.

## PR, merge, release and phone alert

Push corrected scoped commits to `fix/agv2-031-postmerge-beta-gate`, update its existing draft PR to `main`, then make it ready for independent technical review **without merging it**. The operator has given ChatGPT standing direction to finish the release work; ChatGPT will independently review, merge the exact verified SHA, and verify all five POST-MERGE push runs on the new main. Release/tag/deploy decisions occur only after post-merge CI green; do not create tags, publish a release, or deploy production while the gate is red.

`main` still lacks branch protection / enforced review; do not misrepresent this as resolved. Where GitHub admin access exists, configure a safe branch rule requiring PRs and the five checks after ensuring it does not break the operator's ability to continue work. Do not invent formal reviewer approvals.

**Standing notification rule:** `AGENTS.md` requires one urgent `https://ntfy.sh/draeician_alerts` alert when **this new corrective task AGV2-031-PM1** is fully complete and independently review-ready. Do not send for partial/blocked runs; do not duplicate the older AGV2-031 notification. Include PR link and exact SHA, verify HTTP 2xx, and record delivery in PR conversation (not via a final-head-changing commit).

## Completion response

State root cause, test proof, final branch SHA, five green PR CI run IDs, live acceptance evidence/limits, phone alert receipt, PR URL, and unresolved operator-only blockers. No routine confirmation prompts; implement the whole assigned fix.
