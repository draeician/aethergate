# AetherGate — Current Task

## Task ID
AGV2-028V2

## Title
Vision contract closure — distinguish omitted vs explicit-null Chat image detail

## Why This Exists

AGV2-028V correctly fixed:
- strict WebP RIFF + WEBP signature validation;
- explicit handling of image detail modes.

Independent review of the pinned SDK types found one remaining narrow mismatch:

### Chat `image_url.detail`

Pinned `openai==2.54.0` Python:
`detail: Literal["auto", "low", "high"]`
inside a total=False TypedDict — optional, but not nullable.

Pinned `openai@7.32.0` JS:
`detail?: 'auto' | 'low' | 'high' | 'original'`
— optional, but not nullable.

Current AetherGate code does:

```python
detail = image_url.get("detail")
validate_image_detail(detail, ...)
```

and `validate_image_detail(None)` accepts `None`.

That makes an explicitly supplied JSON `"detail": null` indistinguishable from omission and therefore accepted on Chat, even though the pinned Chat contracts permit omission but not null.

This task closes only that mismatch.

PR #2 must remain open/unmerged until this passes.

## WIP Marker — FIRST LOCAL ACTION

Immediately create `.aethergate-wip` before any other local repository action:

- task=AGV2-028V2
- branch=v2
- started_at=<UTC timestamp>

Never stage/commit/push it.
Remove it only after final remote verification, handoff update, and PR update.

## Starting Point

Current `origin/v2` before this task:
`06728b21e91ea982e171385751067a64fb1ae45a`

AGV2-028V remote CI is green on that exact SHA.

Current baseline:
- backend: **685**
- frontend: **89**
- Playwright: **32 passed / 6 skipped**
- migration head: **0017**
- PR #2: open/unmerged/mergeable.

## 1. Preserve omission semantics

For Chat image parts:

- omitted `image_url.detail` must remain accepted;
- `detail: "auto"` remains accepted;
- `low` / `high` / `original` remain explicit `400 unsupported_feature`;
- invalid strings remain rejected;
- non-string values remain rejected;
- **explicit `detail: null` must now be rejected**.

Do not treat missing and explicit null as the same state.

Use key-presence detection or a sentinel; do not rely on `.get()` alone.

## 2. Responses semantics

Do not blindly copy Chat's null rule into Responses.

The pinned JS Responses content-part type is:
`detail?: ImageDetail | null`.

The pinned Python type generation differs in requiredness/nullability.

Keep the currently documented Responses omission/default behavior unless direct inspection of the exact content-part contract used by both SDKs proves a safer correction is required.

At minimum:
- do not regress Responses omitted/auto behavior;
- do not broaden non-default detail support;
- preserve explicit rejection of low/high/original;
- document any intentional cross-SDK tolerance decision clearly.

This task is about the clear Chat null mismatch, not reopening the entire Responses contract.

## 3. Tests

Add focused tests proving:

### Chat
- omitted succeeds;
- auto succeeds;
- explicit JSON null returns structured 400 / UnsupportedFeature;
- low/high/original rejected;
- invalid string rejected;
- non-string rejected;
- rejected null request does not reach adapter/upstream.

Include direct contract-level and API-level coverage.

### Responses
Retain regression coverage for the currently documented omission/auto behavior and non-default rejection.

## 4. SDK / contract evidence

Record in comments/docs/handoff the exact pinned Chat evidence:

Python:
`ImageURL.detail: Literal["auto","low","high"]` optional by TypedDict total=False, not Optional.

JS:
`detail?: 'auto'|'low'|'high'|'original'`, optional, not `| null`.

Do not claim `original` is supported; it remains explicitly rejected because transport cannot implement it faithfully.

No external network/provider behavior changes are needed.

## 5. Documentation

Update `docs/contracts/vision.md` narrowly:

- Chat detail omitted/auto accepted;
- Chat explicit null rejected;
- Chat low/high/original rejected;
- Responses behavior remains as currently documented, with any intentional null tolerance stated if relevant.

Do not broaden compatibility claims.

## 6. Regression Gate

Run focused tests first, then the established full gate:

- backend full suite, >= 685 collected and all pass;
- frontend >= 89 pass;
- full Playwright >= 32 pass;
- npm build;
- npm lint;
- `npx tsc -b --noEmit`;
- OpenAPI/client drift;
- ruff;
- git diff --check;
- secret/content/image-data scans;
- no-legacy-v1 guard + self-test;
- migrations empty -> 0017;
- populated 0016 -> 0017;
- config_check;
- fake Ollama vision;
- official Python vision;
- official JS vision;
- offline golden path.

No migration.
Do not modify migrations 0001-0017.

No live nomnom rerun is required: this is pre-dispatch request-contract validation only.

## 7. Remote CI

Push to `origin/v2`.

On the exact FINAL SHA require:
- backend completed/success;
- frontend completed/success;
- schema-secret-gate completed/success;
- alpha-golden-path completed/success.

Root-cause any red workflow.

## 8. PR #2

PR #2 remains open/unmerged.

Verify:
- head updated to final v2 SHA;
- mergeable;
- description mentions the explicit-null Chat detail closure;
- exact final SHA + workflow IDs are current.

Do NOT merge PR #2.

## 9. Handoff

Update `docs/development/agent-handoff.md` with:
- independent-review finding;
- exact pinned Chat optional-but-non-null evidence;
- implementation choice used to distinguish omission from null;
- focused tests;
- final regression counts;
- migration head 0017;
- final SHA + four CI workflow IDs/conclusions;
- PR #2 status/head;
- WIP lifecycle;
- exactly one recommended next step:
  **Merge PR #2 (v2 -> main) through the reviewed/user-authorized process.**

Never include image bytes/base64, secrets, prompts/outputs, encrypted payloads, or large logs.

## Commit / Push

Use a narrow conventional commit such as:
`fix(vision): reject null chat image detail`

Push to `origin/v2`.
Never push directly to main.
Do not ask whether to commit/push/continue.

The task is complete only when explicit null is rejected on Chat without changing omission/auto behavior,
all regressions and exact-SHA remote CI are green, PR #2 is updated and still unmerged, handoff is
truthful, and `.aethergate-wip` is removed only after final remote verification.
