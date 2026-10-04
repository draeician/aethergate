# AetherGate v2 — Current Task

## Task ID
AGV2-009V

## Title
Complete deferred live verification for shared-quota hardening

## Ownership
Primary: scheduler/queueing verification  
Coordinating: provider adapters, platform/testing

## Why This Task Exists

AGV2-009 implemented the shared-quota hardening and its deterministic test suite is green, but the
handoff explicitly says the required live nomnom A-E verification was deferred.

That means AGV2-009 did not satisfy its own completion criteria.

This task closes that gap. Do not start budgets/accounting until this verification is complete.

## Compaction Recovery

This is a long-running verification task.

If context is compacted, summarized, restarted, or you become uncertain what to do next:

1. re-read `AGENTS.md`;
2. re-read `project_spec.md`;
3. re-read this file;
4. re-read `docs/development/agent-handoff.md`;
5. inspect `git status` and recent commits;
6. continue from repository state.

Do not ask the user to choose whether to commit, push, continue, or stop when this file already
specifies those actions.

## Before You Start

1. Work on branch `v2`.
2. Run `git pull --ff-only origin v2`.
3. Read:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/agent-handoff.md`
   - `docs/development/current-task.md`
   - `docs/architecture/scheduler.md`
   - AGV2-009 quota tests and scheduler implementation
4. Do not modify code unless live verification exposes a real defect.
5. Do not modify/delete legacy v1 `app/` or `frontend/src/`.
6. Do not commit unrelated local/untracked files.

## Goal

Run the live/container verification that AGV2-009 required but deferred.

Use nomnom as the test bed and the existing Docker/Podman workflow.

Do not guess ports. Use the dynamic AetherGate host-port workflow and verify the current external
backend/model before testing.

## Required Verification

### A. Phantom-reservation regression against the running stack

Configure through development tooling:

- one endpoint with `max_concurrency=1`;
- request quota with ample room;
- if token quota is configured for this route, respect the real estimator behavior discovered in
  AGV2-009 rather than bypassing fail-closed behavior.

Run request A long enough to occupy the endpoint.

Queue request B against the same scheduling scope and allow several worker polling/claim cycles while
A remains active.

Prove from PostgreSQL/inspection metadata:

- B remains queued;
- B has zero live quota reservations while endpoint capacity is unavailable;
- B has zero execution attempts;
- B has zero endpoint reservations;
- quota-window committed/reserved counters do not increase because of B while it is waiting;
- after A completes, B acquires admission once, dispatches once, and completes.

Do not record prompt/completion content.

### B. Same physical endpoint, independent quota scopes

Configure two aliases/routes sharing the same physical endpoint:

- group A exhausted or in cooldown;
- group B available.

Prove:

- an older A request remains queued;
- a B request dispatches on the same endpoint without waiting for A;
- FIFO remains intact within group A;
- endpoint `max_concurrency` is still respected across A+B;
- no phantom quota reservation is created for blocked A polls.

### C. Token-estimator fail-closed behavior on the real nomnom model

Re-check the current backend/model and installed LiteLLM behavior.

For the real model currently used on nomnom:

- verify whether a provider/model-specific usable tokenizer/estimator exists;
- if unavailable, prove a token-quota route fails closed with
  `quota_token_estimator_unavailable` (or the current explicit equivalent);
- prove a request-quota-only route still performs real inference successfully;
- do not add a heuristic fallback to make token quota pass.

If the estimator has become available due to dependency/environment change, document exactly which
provider/model-specific path is used and run a real token-quota inference smoke test.

### D. Monotonic provider cooldown

Use the deterministic/mock upstream or controlled adapter test environment in the container.

Prove:

1. apply a long cooldown;
2. apply a shorter cooldown afterward/concurrently;
3. the shorter value does not reduce the existing deadline;
4. apply a longer cooldown;
5. it extends the deadline;
6. an unrelated quota group remains eligible.

This does not require forcing the real Ollama backend to emit 429.

### E. Real inference regression

Against the currently running real backend through AetherGate:

- official OpenAI Python SDK non-stream succeeds;
- official OpenAI Python SDK stream succeeds;
- six-request/two-slot endpoint-concurrency regression remains correct;
- request-quota shared-alias behavior remains correct;
- multi-window request quota remains correct;
- worker recovery/invariant tests remain green.

Use the actual dynamically assigned AetherGate port and record it only in the handoff.

## If Verification Finds a Defect

Fix only the defect needed to satisfy this verification task.

Requirements:

- add a regression test before/with the fix;
- preserve existing scheduler invariants;
- run full containerized tests;
- use a focused conventional commit;
- describe the defect and correction in the handoff.

Do not expand scope into budgets, pricing, auth, admin API, Responses API, UI, or unrelated cleanup.

## Automated Regression

Run at minimum:

- `scripts/dev/v2 test`;
- ruff/lint;
- migration head verification;
- `git diff --check`;
- secret scan.

The current baseline reported by AGV2-009 is 181 passing tests. Any reduction requires an explicit
documented reason.

## Handoff

Update `docs/development/agent-handoff.md`.

Include:

- branch and starting commit;
- verification/fix commit(s);
- actual dynamic AetherGate port;
- current backend/model;
- result of live phantom-reservation test;
- result of same-endpoint cross-quota test;
- token-estimator result for the actual model;
- monotonic cooldown result;
- official SDK non-stream/stream result;
- six-request/two-slot result;
- final automated test count;
- any defects found/fixed;
- remaining issues/risks;
- exactly one recommended next step.

Remove/replace the statement that live A-E checks are deferred if they are now complete.

No credentials, prompt/completion bodies, encryption keys, or large logs.

## Commit and Push

If verification requires no code changes, commit the updated handoff/documentation with a conventional
documentation/test message.

If defects are fixed, use focused conventional fix/test commits.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

Do not ask the user whether to commit or push. This task explicitly requires both.

The task is complete only when the required live verification is finished, the handoff truthfully
records the results, and `origin/v2` contains the resulting commit(s).
