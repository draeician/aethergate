# AetherGate - Current Task

## Task ID
AGV2-029V

## Title
Embeddings verification closure - strict optional-field semantics, bounded inputs, provider index integrity

## Why This Verification Task Exists

AGV2-029 is substantially complete and its architecture is correct:
- embeddings use the existing durable scheduler/accounting authority;
- operation discriminator is inside encrypted payload/result envelopes;
- official Python/JS SDK proofs are green;
- JS default-base64 compatibility is proven;
- migration head remains 0017;
- PR #3 is open/unmerged/mergeable.

Independent review found three narrow requirements that current green CI does not cover.

Do NOT merge PR #3 until this verification task is complete.

## Starting Point

AGV2-029 final feature tip before this correction:

fb9be76c665ae249dcab1001babfa929deaf1f68

All four required workflows are completed/success on that exact SHA, including:
- backend: 38206979640 (duplicate later green run also exists)
- frontend: 38206979781
- schema-secret-gate: 38206979661
- alpha-golden-path: 38206979769

Current regression baseline:
- backend: 734 passed
- frontend: 89 passed
- Playwright: 32 passed / 6 skipped
- migration head: 0017

PR #3:
- v2 -> main
- open
- unmerged
- mergeable
- head currently fb9be76c...

## WIP Marker - FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

.aethergate-wip

Safe contents:
- task=AGV2-029V
- branch=v2
- started_at=<UTC timestamp>

Never stage, commit, or push it.
Keep it present for the entire incomplete task.
Remove it only after final exact-SHA CI, handoff, and PR #3 are updated and verified.

## Gap 1 - provider embedding indexes are not validated strongly enough

Current LiteLLM adapter behavior:
- reads each provider data item's index + embedding;
- sorts entries by index;
- checks only that entry count equals input count;
- then returns vectors without verifying the provider indexes are exactly 0..N-1, unique, and in-range.

That means malformed responses such as:
- indexes [0, 0];
- indexes [1, 1];
- indexes [1, 2] for two inputs;
- negative/out-of-range indexes;

can have the correct vector count, survive sorting, and then be silently re-numbered by the public API
as indexes 0..N-1. That can corrupt input -> vector correspondence.

AGV2-029 explicitly required:
- result count equals input count;
- input order/index correspondence is preserved.

### Required fix

At the provider adapter boundary:

1. Parse provider indexes as today.
2. Reject malformed/non-integer indexes safely.
3. Permit provider response order to differ IF indexes are valid.
4. Sort by provider index.
5. Require the sorted provider index sequence to equal exactly:

   0, 1, ..., len(inputs)-1

6. Reject:
   - duplicates;
   - gaps;
   - negative indexes;
   - out-of-range indexes;
   - any otherwise invalid index mapping.

Do not silently reindex malformed provider results.

ProviderError text must remain safe and contain no vectors or inputs.

### Required tests

Add adapter tests proving:
- valid in-order indexes accepted;
- valid out-of-order provider entries [1,0] are sorted back to input order;
- duplicate index rejected;
- negative index rejected;
- out-of-range/gapped index rejected;
- existing wrong-count/malformed/non-finite cases remain rejected.

Where useful extend fake-provider injection to prove this through the real pinned LiteLLM boundary, but
do not add unnecessary complexity if direct adapter tests plus existing real-transport tests are stronger.

## Gap 2 - optional-but-non-null OpenAI fields collapse explicit null into omission

Pinned SDK evidence:

Python openai==2.54.0 EmbeddingCreateParams is total=False with:
- dimensions: int
- encoding_format: Literal["float", "base64"]
- user: str

These fields are optional because the TypedDict is total=False, but their values are not Optional/null.

JavaScript openai@7.32.0:
- dimensions?: number
- encoding_format?: "float" | "base64"
- user?: string

Again optional, not nullable.

Current AetherGate request DTO uses:
- encoding_format: Literal["float","base64"] | None = None
- dimensions: Any = None
- user: Any = None

and validators use value != None tests.

Therefore explicit JSON null is currently treated as omission/default:
- "encoding_format": null silently becomes float behavior;
- "dimensions": null bypasses the required unsupported-feature rejection;
- "user": null bypasses the required unsupported-feature rejection.

This violates the pinned request contract and AetherGate's settled rule:
known semantic fields are implemented faithfully or explicitly rejected, never silently dropped.

### Required fix

Distinguish KEY ABSENCE from EXPLICIT NULL.

Use model_fields_set, a before-validator, or an explicit sentinel.

Required semantics:

- encoding_format omitted:
  accepted; server/public behavior remains current.
  Note: locked JS SDK still sends base64 explicitly on its default wire path.

- encoding_format "float":
  accepted.

- encoding_format "base64":
  accepted.

- encoding_format explicit null:
  reject with an OpenAI-compatible structured request error.
  Prefer the same 400 invalid_request/unsupported-or-invalid contract style used elsewhere rather
  than silently defaulting to float.

- dimensions omitted:
  accepted as omission.

- dimensions present with any value, INCLUDING null:
  explicitly reject 400 unsupported_feature until dimensions is faithfully implemented.

- user omitted:
  accepted as omission.

- user present with any value, INCLUDING null:
  explicitly reject 400 unsupported_feature until user is faithfully implemented.

Do not change unknown purely-additive field tolerance.

Do not make transport/provider override fields a new scope unless a concrete security issue is found;
this closure is about pinned known embedding semantic fields.

### Required tests

Contract + API tests:
- encoding_format omitted succeeds;
- float succeeds;
- base64 succeeds;
- explicit encoding_format:null rejected;
- dimensions numeric rejected;
- dimensions:null rejected;
- user string rejected;
- user:null rejected;
- rejected cases never enqueue/dispatch upstream.

If the official Python/JS SDK type system cannot naturally emit null for these fields, raw API contract
tests are appropriate for the null edge; keep the official SDK happy-path/rejection proofs already present.

Document the omission-vs-null behavior in docs/contracts/embeddings.md.

## Gap 3 - required bounded multi-input cardinality is missing

AGV2-029 explicitly required a provider-neutral bounded multi-input cardinality.

Current _normalize_embedding_input accepts any non-empty list[str] with no count bound. Only the general
serialized queue payload byte limit eventually constrains it.

That does not satisfy the request-shape requirement.

### Required fix

Inspect the exact pinned OpenAI compatibility source/SDK schema and establish the supported maximum
number of embedding inputs for this gateway.

The locked Python/JS SDK documentation states the input array is bounded; confirm the exact pinned
maximum from the authoritative schema/reference before coding.

Then:
- define one explicit constant/configured request-shape bound in the embedding contract;
- enforce it before durable enqueue;
- single string remains one input;
- exactly-at-limit list succeeds;
- limit+1 fails with structured HTTP 400;
- do not rely on queue payload bytes as the cardinality policy;
- preserve the existing global payload byte bound as a separate safety limit.

Do not invent an Ollama-specific maximum.
This is an OpenAI-compatible gateway request-shape bound.

Document the exact supported max.

## Preserve all AGV2-029 invariants

Do not alter or regress:

- POST /v1/embeddings uses durable scheduler, never direct dispatch;
- same endpoint max_concurrency;
- same quota/budget/accounting authority;
- same credential/auth path;
- same leases/fencing/cancellation/recovery/outcome_unknown;
- operation discriminator inside payload_encrypted only;
- no plaintext operation/content DB column;
- legacy chat payloads/results remain readable;
- embedding inputs/vectors encrypted at rest;
- Capability.EMBEDDING on alias/provider/adapter;
- dispatch-time capability and credential revalidation;
- no fallback;
- non-stream only;
- no stream events;
- no fabricated TTFT;
- token quota/budget estimator-unavailable fail-closed;
- request-priced accounting exactly once;
- token-priced output_units=0;
- JS default-base64 compatibility;
- Float32 base64 format;
- public alias response model;
- fake Ollama /api/embed transport;
- no input/vector logging.

No migration.
Keep head at 0017.
Do not modify migrations 0001-0017.

## Focused verification

Run new focused tests first.

At minimum:
- embedding contract null/presence tests;
- embedding API no-dispatch tests for invalid null/cardinality;
- adapter index integrity tests.

Prove the new tests fail against the pre-fix AGV2-029 behavior where practical.

## Full regression gate

Then run the established full gate:

- backend full suite >= 734 and all pass;
- frontend >= 89 pass;
- full Playwright >= 32 pass (expected CI-only skips allowed);
- npm build;
- npm lint;
- npx tsc -b --noEmit;
- OpenAPI/client drift;
- ruff check src tests;
- git diff --check;
- secret/content/vector-data scan;
- no-legacy-v1 guard + self-test;
- migration empty -> 0017;
- populated 0016 -> 0017;
- config_check;
- fake Ollama Chat + vision + embeddings;
- official Python Models + Chat + Responses + Embeddings;
- official JS Models + Chat + Responses + Embeddings;
- offline golden path.

A full live nomnom rerun is NOT required merely for these request/provider-response validation fixes,
because no provider transport behavior should change.

If you change the real LiteLLM/Ollama wire transport unexpectedly, rerun the relevant live/provider
proof or root-cause why transport changed.

## OpenAPI / generated client

If request DTO/schema changes affect generated OpenAPI:
- regenerate backend OpenAPI;
- regenerate frontend openapi.json/schema.d.ts;
- do not hand-edit generated artifacts;
- two consecutive generations must produce zero additional diff.

Keep the locked dependencies unchanged unless genuinely required.

## Documentation

Update narrowly:
- docs/contracts/embeddings.md;
- docs/contracts/openai-compatibility-baseline.md only if its support matrix needs the input bound/null clarification;
- docs/development/agent-handoff.md.

Do not perform unrelated cleanup.

## Remote CI

Push completed corrections to origin/v2.

On the EXACT FINAL v2 SHA require:
- backend = completed/success
- frontend = completed/success
- schema-secret-gate = completed/success
- alpha-golden-path = completed/success

If any is red:
- inspect logs;
- root-cause;
- fix;
- push a new SHA;
- require all four green on the new final SHA.

## PR #3

PR #3 must remain open and unmerged.

After final CI:
- verify PR #3 head equals the exact final v2 SHA;
- verify mergeable;
- update its description with AGV2-029V closure details;
- update backend/frontend/Playwright counts if changed;
- update exact final SHA and exact four workflow IDs.

Do NOT merge PR #3.

## Handoff

Update docs/development/agent-handoff.md with:

- the three independent-review findings;
- provider index validation policy;
- pinned optional-but-non-null field evidence;
- exact null semantics;
- exact max embedding input count and source;
- focused tests;
- full regression counts;
- migration head 0017;
- final v2 SHA;
- exact four remote workflow IDs/conclusions;
- PR #3 head/status;
- WIP lifecycle;
- exactly one recommended next step:

  Merge PR #3 (v2 -> main) through the reviewed/user-authorized process.

Never include raw embedding input, vectors/base64, credentials, encrypted bytes, or large logs.

## Commit / Push

Use a narrow conventional commit such as:

fix(embeddings): tighten request and provider validation

Additional focused tests/docs commits are fine if useful.

Push to origin/v2.
Never push directly to main.
Do not ask whether to commit/push/continue.

The task is complete only when:
- malformed provider indexes cannot corrupt input/vector correspondence;
- explicit null cannot masquerade as omission for pinned embedding semantic fields;
- the documented embedding input-count bound is enforced before enqueue;
- all existing embeddings/Chat/Responses/vision behavior remains green;
- migration head remains 0017;
- all four remote workflows are green on the exact final v2 SHA;
- PR #3 is updated, mergeable, open, and unmerged;
- the handoff is truthful;
- .aethergate-wip is removed only after final remote verification.
