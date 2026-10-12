# AetherGate — Current Task

## Task ID
AGV2-028V3

## Title
Vision handoff/PR metadata synchronization — no product changes

## Why This Exists

AGV2-028V2 product code is independently verified correct on:

`936cec102d40315c6d5b90c4c05fc4ca3dac3622`

The Chat explicit-null detail bug is closed:
- omission accepted;
- `auto` accepted;
- explicit `null` rejected on Chat;
- Responses intentionally tolerates explicit null;
- low/high/original remain explicit unsupported;
- tests and exact-SHA remote CI are green.

However, independent review found the durable project metadata is stale/inconsistent:

1. `docs/development/agent-handoff.md` still labels the closure as **AGV2-028V**, not
   **AGV2-028V2**.
2. That handoff still records the substantive/final state as `389fffd...` and does not record the
   final `936cec...` tip or its final exact-SHA workflow IDs.
3. The handoff WIP lifecycle still says `task=AGV2-028V`, not `AGV2-028V2`.
4. PR #2's actual GitHub description is stale: it still says final SHA
   `06728b21e91ea982e171385751067a64fb1ae45a` and describes only AGV2-028/028V.
   It does not include the explicit-null closure or final `936cec...` CI evidence.

This is a metadata-only closure. Do not change product behavior.

## WIP Marker — FIRST LOCAL ACTION

Create `.aethergate-wip` before other local repository work:

- task=AGV2-028V3
- branch=v2
- started_at=<UTC timestamp>

Never stage/commit/push it.
Remove only after final remote verification and PR/handoff synchronization.

## Starting Point

Verified product tip before this metadata task:
`936cec102d40315c6d5b90c4c05fc4ca3dac3622`

All four workflows are completed/success on that exact SHA:
- backend — `38195202524` (duplicate green run `38195206211`)
- frontend — `38195202501` (duplicate green run `38195206185`)
- schema-secret-gate — `38195202508` (duplicate green run `38195206173`)
- alpha-golden-path — `38195202546` (duplicate green run `38195206199`)

Current regression baseline:
- backend: **691**
- frontend: **89**
- Playwright: **32 passed / 6 skipped**
- migration head: **0017**

PR #2:
- open
- unmerged
- mergeable
- head branch v2
- actual description is stale and must be corrected.

## 1. Update the handoff accurately

Update `docs/development/agent-handoff.md` so the top/current section is clearly:

**AGV2-028V2 — Chat image-detail null closure**

Record:
- pinned Chat optional-but-non-null evidence;
- sentinel/key-presence implementation;
- Responses nullable tolerance;
- backend 691;
- frontend 89;
- Playwright 32 passed / 6 skipped;
- migration head 0017;
- substantive code/test SHA `389fffd772894ab4ae9cede2ce8d2e3a5e925845`;
- independently verified final pre-metadata tip
  `936cec102d40315c6d5b90c4c05fc4ca3dac3622`;
- the exact four green workflow IDs above;
- correct WIP task name `AGV2-028V2`;
- PR #2 status.

Do not claim the stale PR body was already updated.

After this metadata commit becomes the new final v2 SHA, update the handoff again if necessary so it
records the actual final SHA and exact-SHA green workflows for that final tip.

The exactly-one recommended next step must remain:

**Merge PR #2 (v2 -> main) through the reviewed/user-authorized process.**

Do not queue embeddings yet.

## 2. Update PR #2 description

Update the actual GitHub PR #2 body so it includes:

- AGV2-028 vision implementation;
- AGV2-028V strict WebP/detail semantics closure;
- AGV2-028V2 explicit-null Chat detail closure;
- Chat omission/auto accepted;
- Chat explicit null rejected;
- Responses explicit null intentionally tolerated;
- low/high/original explicitly unsupported;
- backend 691 / frontend 89 / Playwright 32 passed, 6 skipped;
- migration head 0017;
- final v2 SHA;
- exact four workflow IDs/conclusions for the final SHA.

Remove stale statements that call `06728b21...` the final SHA.

PR #2 must remain:
- open;
- unmerged;
- mergeable.

Do not merge it.

## 3. No product/code changes

Do not modify:
- application code;
- tests;
- generated OpenAPI/types;
- migrations;
- CI logic;
- provider behavior;
- scheduler/accounting behavior.

This task exists only to make the durable repo/PR communication truthful.

## 4. Verification

Because the handoff commit changes `origin/v2`, require the normal remote exact-SHA gate on the
new final tip:

- backend completed/success;
- frontend completed/success;
- schema-secret-gate completed/success;
- alpha-golden-path completed/success.

No full local regression rerun is required for docs-only changes beyond:
- git diff --check;
- no-legacy-v1 guard if touched by normal workflow;
- confirm only intended documentation metadata changed.

If any remote workflow is red, root-cause it before completion.

## 5. Final consistency check

Before completion verify all three agree:

1. `origin/v2` exact final SHA;
2. `docs/development/agent-handoff.md`;
3. PR #2 body/head.

They must all name the same final v2 SHA and the same exact-SHA four green workflow runs.

Also verify:
- PR #2 remains unmerged;
- migration head remains 0017;
- `.aethergate-wip` is absent locally and never tracked/staged/committed/pushed.

## Commit / Push

Use a docs-only conventional commit such as:

`docs: synchronize vision handoff and pr metadata`

Push to `origin/v2`.
Never push directly to main.

Do not implement embeddings.
Do not merge PR #2.

The task is complete only when the handoff, PR #2, and actual final v2/CI state are mutually
consistent and truthful.
