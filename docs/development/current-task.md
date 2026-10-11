# AetherGate — Current Task

## Task ID
AGV2-028V

## Title
Vision verification closure — strict WebP signature + explicit image detail semantics

## Why This Verification Task Exists

AGV2-028 is substantially complete and all four remote workflows are green on:

`8dafbfd3c1e96cf616bbb7cd88487eee36335d03`

Independent review found two narrow contract/validation gaps that the current tests do not catch.

Do **not** merge PR #2 until this verification task is complete.

### Gap 1 — WebP validation is too permissive

Current `src/aethergate/inference/images.py` represents WebP signatures as:

```python
"image/webp": ((b"RIFF",), (b"WEBP",)),
```

and `_decoded_signature_matches()` accepts any one group whose members all match at offset 0.

Therefore any payload starting with `RIFF` can currently be accepted as `image/webp`, even when
bytes 8–11 are not `WEBP` (for example RIFF/WAVE or RIFF/AVI content).

That contradicts the documented strict MIME/signature validation.

### Gap 2 — OpenAI image `detail` is silently ignored

The pinned OpenAI multimodal contracts include an image-detail selector.

Current Chat and Responses parsing accepts raw image-part dictionaries and ignores the detail field.
For example the current Responses vision test sends `"detail": "auto"`, but the parser does not
inspect it at all.

AetherGate's settled compatibility rule is:
**semantic fields must be implemented faithfully or explicitly rejected; never silently dropped.**

This task must make image-detail behavior explicit.

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-028V
- branch: v2
- UTC start timestamp

Never stage/commit/push it.
Keep it for the entire incomplete task.
Remove it only after final remote verification and handoff/PR update are complete.

## Starting Point

AGV2-028 feature tip:
`8dafbfd3c1e96cf616bbb7cd88487eee36335d03`

Remote workflows on that exact SHA are completed/success:
- backend — `38177261422` (also duplicate green push/PR run `38177264313`)
- frontend — `38177261410`
- schema-secret-gate — `38177261420`
- alpha-golden-path — `38177261437`

Baseline after vision:
- backend: **657**
- frontend: **89**
- Playwright: **32 passed / 5 skipped**
- migration head: **0017**

PR #2 (`v2` -> `main`) exists, is open/unmerged, and currently points at the AGV2-028 feature branch.

## 1. Fix WebP signature validation

Replace the generic logic as needed so WebP requires the real container signature:

- minimum length sufficient to inspect the header;
- bytes `0:4 == b"RIFF"`;
- bytes `8:12 == b"WEBP"`.

Do not accept RIFF alone.

Keep PNG/JPEG validation at least as strict as today.

Prefer clear per-format validation over a misleading generic abstraction if that is safer/readable.

No image decoding/decompression library is needed.

## 2. Add negative WebP tests

Add deterministic tests proving all of the following:

- valid WebP fixture is accepted;
- a RIFF/WAVE-like payload labeled `image/webp` is rejected;
- a RIFF/AVI-like or arbitrary RIFF payload without the WebP marker is rejected;
- a payload beginning with `WEBP` but lacking the RIFF container signature is rejected;
- MIME/signature failure returns the existing safe structured error;
- raw bytes/base64 are not reflected in error text/logs.

The test must specifically fail against the AGV2-028 implementation and pass after the fix.

## 3. Inspect the pinned image-detail contract

Before coding detail behavior, inspect the exact locked compatibility sources:

- pinned upstream OpenAI reference in `docs/contracts/openai-compatibility-baseline.md`;
- `openai==2.54.0` Python SDK types;
- `openai@7.32.0` JS SDK types.

Record the exact allowed detail values separately for:
- Chat Completions `image_url` content part;
- Responses `input_image`.

Do not guess the enum from memory.

## 4. Make image-detail behavior explicit

This is a verification closure, not a new image-quality feature.

If the pinned contract defines `auto` as the neutral/default behavior and the current LiteLLM/Ollama
path has no faithful low/high detail control, the preferred narrow policy is:

- allow omitted detail;
- allow `null` only if the pinned request contract permits it;
- allow `auto` when it is semantically equivalent to provider/default behavior;
- explicitly reject non-default semantic detail modes (for example low/high, and any other pinned
  non-neutral values) with structured HTTP 400 `unsupported_feature`.

If inspection proves LiteLLM/Ollama faithfully supports the pinned detail semantics, preserving them
through the canonical model is acceptable, but do not expand scope merely to avoid a 400.

In all cases:
- do not silently ignore a semantic detail value;
- Chat and Responses must behave consistently;
- use precise `param` paths;
- no upstream dispatch for rejected detail values.

## 5. Add detail contract tests

Add tests for BOTH Chat and Responses proving:

- omitted/default detail succeeds;
- `auto` succeeds only if the pinned contract/default semantics justify it;
- every pinned non-default mode that is not faithfully supported returns 400 `unsupported_feature`;
- invalid detail enum values are rejected according to the pinned contract;
- rejected detail requests do not reach the adapter/upstream.

Extend official Python SDK compatibility proof with at least one supported/default image detail and
one explicitly rejected semantic detail mode if the SDK exposes it.

Extend the locked JS SDK smoke equivalently when the JS SDK exposes the field.

## 6. Preserve all AGV2-028 invariants

Do not alter:
- inline-data-only source policy;
- PNG/JPEG/WebP supported set;
- remote URL/file_id rejection;
- no-fetch/SSRF invariant;
- image count/byte/payload limits;
- encrypted queue storage;
- image capability enforcement;
- scheduler/worker/accounting authority;
- token quota/budget fail-closed behavior;
- auth/cancellation/recovery;
- text-only compatibility.

No image bytes/base64 in logs or handoff.

## 7. Documentation cleanup

Update `docs/contracts/vision.md` to state exact image-detail behavior.

Also correct stale wording that still calls the Responses implementation a "text subset" in files
modified by AGV2-028 where that wording is now materially false. Keep claims scoped:
Responses supports the documented text + inline-image input subset, not full multimodal Responses.

Do not perform unrelated documentation cleanup.

## 8. Handoff next-step correction

The current handoff says to resume embeddings and then merge PR #2.

Reverse that ordering.

A completed, independently verified vision feature PR must be merged/reviewed before adding the next
feature to `v2`; otherwise PR #2 stops being a scoped vision PR.

After AGV2-028V passes, the handoff's exactly-one recommended next step must be:

**Merge PR #2 (`v2` -> `main`) through the reviewed/user-authorized process.**

Only after that merge and post-merge main CI are green should embeddings be queued.

## 9. Regression verification

Run the focused new tests first, then the full existing gate:

- backend full suite (must be >= 657 collected and all pass);
- frontend >= 89 pass;
- full Playwright >= 32 pass (expected CI-only skips allowed);
- npm build;
- npm lint;
- `npx tsc -b --noEmit`;
- OpenAPI/client drift;
- ruff;
- git diff --check;
- secret/content/image-data scan;
- no-legacy-v1 guard + self-test;
- migration empty -> 0017;
- populated 0016 -> 0017;
- config_check;
- fake Ollama vision;
- official Python Chat/Responses vision;
- official JS vision smoke;
- offline golden path.

No migration.
Do not modify migrations 0001–0017.

A full live nomnom repetition is not required merely for the parser correction unless the detail
behavior changes provider transport. If provider transport is changed, re-run the relevant real model proof.

## 10. Remote CI

Commit/push the correction to `origin/v2`.

On the exact FINAL v2 SHA require:
- backend completed/success;
- frontend completed/success;
- schema-secret-gate completed/success;
- alpha-golden-path completed/success.

Root-cause any red workflow.

## 11. PR #2

PR #2 must remain open/unmerged.

Because its head is `v2`, verify it updates to the final AGV2-028V SHA and still remains mergeable.

Update the PR description if needed so it accurately states:
- strict WebP header validation;
- explicit image-detail behavior;
- final SHA and final four workflow IDs.

Do NOT merge PR #2.

## 12. Handoff

Update `docs/development/agent-handoff.md` with:
- independent-review findings;
- exact WebP fix;
- exact pinned detail enums and chosen supported/rejected policy;
- focused regression proof;
- full gate counts;
- migration head 0017;
- final v2 SHA;
- exact four remote workflow IDs/conclusions;
- PR #2 status/head;
- WIP lifecycle;
- exactly one recommended next step: reviewed/user-authorized merge of PR #2.

Never include image contents, base64, credentials, prompt/output content, encrypted bytes, or large logs.

## Commit / Push

Use a narrow conventional commit such as:
`fix(vision): tighten image validation semantics`

Additional test/docs commits are fine if useful.

Push to `origin/v2`.
Never push directly to main.
Do not ask the user whether to commit/push/continue.

The task is complete only when the two review gaps are closed, all regressions pass, all four remote
workflows are green on the exact final SHA, PR #2 is updated and still unmerged, handoff is truthful,
and `.aethergate-wip` is removed only after final remote verification.
