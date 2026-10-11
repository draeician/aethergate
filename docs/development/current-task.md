# AetherGate — Current Task

## Task ID
AGV2-028

## Title
Vision / multimodal image input for Chat Completions + Responses

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-028
- branch: v2
- UTC start timestamp

The marker is gitignored.
Never stage, commit, or push it.
Keep it present for the entire incomplete task.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every local/live criterion below is green;
2. every task commit is pushed to origin/v2;
3. the final origin/v2 SHA has all required GitHub Actions green;
4. the handoff is committed/pushed and remote verified;
5. the feature PR to main is created/updated if repository tooling permits.

## Starting Point

The canonical v2 cutover is complete and merged into `main`.

Main merge commit:
`af216e730c3e2c0d84c63cb641dc6be5b2037e45`

PR #1:
- v2 -> main
- merged
- merge commit above.

All four post-merge workflows are green on that exact `main` commit:
- backend — run `38166139408`
- frontend — run `38166139425`
- schema-secret-gate — run `38166139401`
- alpha-golden-path — run `38166139352`

Current green baseline:
- backend: **613 passed**
- frontend: **87 passed**
- Playwright: **32 passed / 3 skipped**
- migration head: **0017**
- Python OpenAI SDK: `openai==2.54.0`
- JS OpenAI SDK: `openai@7.32.0`
- LiteLLM: `1.104.0`.

The current supported inference surfaces are:
- Models
- Chat Completions (text)
- Responses text subset.

Embeddings remain deferred.

This task adds **vision input -> text output**. It does NOT add image generation.

## Goal

AetherGate must accept image-bearing multimodal requests through the existing OpenAI-compatible
Chat Completions and Responses surfaces, route them only to explicitly image-capable aliases/providers,
queue them through the same durable scheduler, and return text output through the same accounting,
auth, cancellation, recovery, and observability authority already proven for text.

Vision is an additive data-plane feature, not a second inference system.

Do NOT create:
- a second scheduler;
- a second worker;
- a vision-only accounting path;
- an image-fetching proxy;
- a new provider kind merely for vision.

## Compatibility Authority

Before implementation, read:
- `project_spec.md`
- `docs/contracts/openai-compatibility-baseline.md`
- locked `openai==2.54.0` Python SDK types
- locked `openai@7.32.0` JS SDK types/behavior
- pinned upstream OpenAI reference named in the compatibility baseline
- current Chat contracts/router
- current Responses contracts/router
- current LiteLLM adapter
- scheduler payload serialization
- fake Ollama implementation.

Do not guess multimodal wire shapes from memory.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history;
7. continue from repository state.

## 1. Supported vision scope

Implement text-output vision inference on BOTH:

### Chat Completions
`POST /v1/chat/completions`

Support the pinned official multimodal message/content-parts form for:
- text parts;
- image input parts;
- existing system/user/assistant text behavior.

### Responses
`POST /v1/responses`

Support the pinned official Responses input-item/content-part form for:
- `input_text`;
- `input_image`;
- existing `instructions`;
- existing text input forms.

Non-stream and stream must both work.

Text-only requests must remain wire-compatible.

## 2. Initial image source policy — inline data only

For this milestone, support image bytes supplied inline through the pinned OpenAI-compatible data form,
typically a `data:` URL where that is the official contract representation.

Support at least:
- PNG;
- JPEG;
- WebP;

provided the real Ollama/LiteLLM path supports them.

Do NOT fetch arbitrary client-supplied HTTP/HTTPS image URLs in AetherGate.

Reject explicitly:
- remote `http://` / `https://` image URLs;
- provider-local file paths;
- `file_id` / uploaded-file references;
- arbitrary URI schemes;
- SVG unless the provider contract is explicitly proven safe and supported.

Reason:
AetherGate must not introduce an SSRF/egress bypass through vision input.

Do not silently drop or rewrite unsupported image sources.

Use structured HTTP 400 `unsupported_feature` or an established safe validation error.

## 3. Strict image decoding + validation

Normalize image inputs before scheduler admission.

Requirements:
- strict base64 decoding;
- MIME allowlist;
- reject malformed data URLs;
- reject empty image payloads;
- reject non-image media types;
- verify obvious format/magic-byte coherence where practical without a heavyweight parser;
- no decompression/decoding library should be invoked merely to estimate image dimensions unless
  justified and bounded;
- no image bytes in error messages/logs.

Add deterministic invalid fixtures:
- bad base64;
- mismatched MIME/signature;
- unsupported MIME;
- empty image;
- oversized image.

## 4. Explicit resource limits

Vision cannot turn PostgreSQL into an unbounded blob store.

Add explicit configurable Alpha limits.

At minimum define and enforce:
- maximum images per request;
- maximum decoded bytes per image;
- maximum decoded image bytes per request;
- maximum serialized/encrypted scheduler payload bytes.

Choose conservative defaults suitable for internal Alpha use and document the exact values.

Requirements:
- limits are checked before durable enqueue where possible;
- encoded/base64 expansion is accounted for;
- text-only behavior remains unaffected at normal sizes;
- oversized requests fail safely before provider dispatch;
- limits apply consistently through direct API and web reverse-proxy paths where relevant;
- do not rely only on nginx to protect the API.

If a general queue payload-byte bound required by the architecture spec is currently missing, implement
it as part of this task and apply it to text and vision payloads.

Do not add an object store in this milestone.

## 5. Canonical multimodal internal model

Generalize the provider-neutral message representation instead of passing protocol-specific JSON
through the worker.

Recommended concepts:
- text content part;
- image content part containing normalized media type + bytes (or an equivalently safe immutable
  representation);
- message with either legacy text content or ordered canonical parts.

Requirements:
- Chat and Responses both map into the SAME canonical multimodal message representation;
- provider adapters receive canonical content parts, not Chat/Responses DTOs;
- text-only legacy `Message(role, content: str)` behavior remains easy and compatible;
- ordering of interleaved text/image parts is preserved.

Do not encode image meaning into a text placeholder.

## 6. Encrypted scheduler payload compatibility

Vision requests must use the existing durable scheduler.

Extend the encrypted chat-generation payload serializer to support canonical multimodal message parts.

Requirements:
- image bytes/content remain inside `payload_encrypted`;
- no plaintext image/base64 data in request metadata, logs, wait metadata, audit metadata, or DB
  columns;
- legacy text-only encrypted payloads remain readable;
- old string-content message payloads deserialize exactly as before;
- no new plaintext DB content column;
- no migration expected.

Add backward-compatibility tests using pre-AGV2-028 text payload fixtures.

The worker must not need to know whether the public protocol was Chat vs Responses.

## 7. Capability enforcement

The domain already defines:
`Capability.IMAGE = "image"`.

For any request containing at least one image:
- public ModelAlias must advertise `image`;
- Provider must advertise `image`;
- route/adapter must be able to dispatch image content.

Because this is image-input -> text-output generation, configure/test vision routes with both
`text` and `image` capabilities unless the existing capability semantics establish a better exact
rule.

If image capability is absent:
- reject before upstream dispatch;
- no fallback to a text-only route/model;
- no silent image removal.

Text-only requests must not regress because an existing provider/alias predates image capability.

## 8. Provider-neutral adapter behavior

Extend the current ChatRequest/LiteLLM adapter path to preserve multimodal content parts.

Requirements:
- no new provider kind;
- provider kind remains `ollama` for the current real path;
- admin-configured base destination/upstream model remain authoritative;
- hidden retries/fallback remain disabled;
- safe ProviderError mapping unchanged;
- images are passed only through the provider adapter;
- adapter must never write image bytes/base64 to logs.

Inspect pinned LiteLLM behavior to determine the exact message representation it accepts and the exact
Ollama HTTP request it emits.

Do not assume the outgoing Ollama schema.

## 9. Fake Ollama vision contract

Extend the deterministic fake Ollama to support the exact image-bearing request shape emitted by the
pinned LiteLLM Ollama adapter.

The fake must:
- verify an image-bearing request actually reaches the upstream transport;
- preserve image count/order metadata for assertions;
- optionally record safe byte counts/media types;
- NEVER persist/log raw image bytes or base64;
- return deterministic text;
- support non-stream;
- support multi-chunk stream;
- support delay;
- support 404/429/5xx;
- remain test/CI-only;
- introduce no provider kind.

Add real adapter contract tests against the fake transport.

## 10. Chat Completions contract

Update typed Chat message contracts to match the pinned multimodal input union.

At minimum prove official SDK-compatible requests with:
- user text + one image;
- user text + multiple images;
- interleaved text/image parts where supported;
- prior text context + image-bearing user message.

Preserve existing:
- generation controls;
- stream options;
- public alias behavior;
- structured errors.

Unsupported Chat semantic features still reject explicitly.

Do not add tools/function calling in this task.

## 11. Responses contract

Extend Responses input parsing for the pinned `input_image` shape.

Support:
- text + image in the same input message;
- one/multiple image parts;
- `instructions`;
- non-stream;
- stream.

Existing Responses output stays text-only:
- output message item;
- output_text;
- existing SSE event sequence.

Do NOT add:
- image output;
- files API;
- stored response retrieval;
- background mode;
- tools/MCP/computer use.

Remote image URLs/file IDs remain explicit unsupported features.

## 12. Scheduler behavior

Vision uses the exact existing scheduler authority:
- durable queue;
- FIFO/scheduling-scope behavior;
- endpoint max_concurrency;
- provider/shared quota;
- budgets;
- worker lease/fence;
- cancellation;
- outcome_unknown;
- cooldown;
- retry authority.

No vision-specific queue.

Prove six image-bearing requests / two endpoint slots:
- first two execute;
- excess requests remain durably queued;
- queue drains as capacity frees.

Images must remain encrypted while queued.

## 13. Token quota / budget behavior

Request quotas:
- same as text; one gateway request is one request unit.

Output reservation:
- use the existing generation max-output-token behavior.

Input token estimation for image-bearing requests:
- inspect pinned LiteLLM/provider capabilities;
- use a provider/model-specific conservative estimator only if defensible;
- if no defensible image token estimator exists and an enforced token quota or token-priced budget
  requires a pre-dispatch estimate, fail closed using the existing estimator-unavailable family;
- NEVER count raw bytes, base64 characters, or image dimensions as fake token units.

Request-priced accounting must work normally.

At settlement:
- trust measured upstream usage only where the provider/LiteLLM reports it through the existing usage
  contract;
- do not fabricate image tokens;
- UsageRecord/ledger exactly-once behavior remains unchanged.

Do not implement `BillingUnit.IMAGE` for vision-input pricing unless the existing accounting model and
provider usage make it genuinely correct. Vision input can remain request-priced or token-priced under
the established rules.

## 14. Streaming / TTFT

Vision text output streams through the existing generation stream path.

Prove:
- Chat streaming chunks are unchanged in shape;
- Responses streaming events are unchanged in shape;
- first non-empty text chunk records TTFT exactly once;
- no image bytes appear in stream events;
- client disconnect follows existing durable cancellation semantics.

## 15. Authentication / authorization

Same inference auth as current text:
- Bearer `agk_...`;
- audience = inference;
- scope = `inference:invoke`;
- project/principal attribution;
- dispatch-time credential revalidation;
- bypass=false in all acceptance proofs.

Queued credential revocation must prevent an image-bearing request from reaching upstream.

No image-specific auth bypass.

## 16. Egress / SSRF invariant

AetherGate egress policy applies only to configured upstream provider destinations.

Client image input must NOT become a second egress mechanism.

Tests must prove:
- remote HTTP(S) image input is rejected before provider dispatch;
- loopback/private/network URLs cannot be used as image fetch targets;
- no DNS/HTTP client is invoked to resolve/fetch client image URLs;
- provider endpoint egress policy remains unchanged.

Do not weaken existing allowlist/private-address protections.

## 17. Logging / diagnostics safety

Extend redaction/canary coverage so logs/diagnostics never contain:
- full data URLs;
- raw base64 image bytes;
- decoded binary;
- image content hashes if they could be used as content fingerprints, unless explicitly justified;
- request bodies containing image content.

Safe metadata may include:
- image_count;
- total_image_bytes;
- allowed media types;
only if useful and already consistent with content-minimization policy.

Do not log image dimensions/content unless needed.

## 18. Official Python SDK

Using locked `openai==2.54.0`, prove official SDK requests for:

### Chat
- non-stream text + image;
- stream text + image.

### Responses
- non-stream input_text + input_image;
- stream input_text + input_image.

Use a tiny deterministic synthetic image fixture.

Prove:
- parsed official SDK output;
- expected deterministic text;
- public model alias;
- usage present where expected;
- remote URL input produces an SDK-visible 400;
- bypass=false.

Do not substitute raw requests/httpx for these compatibility proofs.

## 19. Official JavaScript SDK

Extend locked `openai@7.32.0` smoke with:
- Chat vision non-stream;
- Chat vision stream;
- Responses vision non-stream;
- Responses vision stream;
- remote URL rejection.

Do not import the OpenAI SDK into the React application.

## 20. Offline Alpha golden path

Extend fake-Ollama CI with an explicitly image-capable public alias/route.

Prefer a distinct alias such as a fixture vision alias rather than pretending every text alias accepts
images.

Golden path must prove:
- existing Models;
- Chat text non-stream/stream;
- Responses text non-stream/stream;
- Chat vision non-stream/stream;
- Responses vision non-stream/stream;
- official Python SDK;
- official JS SDK;
- image capability rejection on text-only alias;
- remote URL rejection;
- backup/restore preserves the vision catalog route;
- post-restore vision inference succeeds.

No Internet image fetch.
No model download.
No provider credentials.

## 21. Live nomnom proof

First inspect installed Ollama models and identify an actually vision-capable model.

Do not guess from model name alone if capability can be verified through metadata/provider behavior.

Do not silently download a model.

If an installed vision-capable model exists:
1. create a disposable image-capable alias/route through supported admin surfaces;
2. alias/provider advertise image capability;
3. official Python Chat vision non-stream;
4. Chat vision stream;
5. official Python Responses vision non-stream;
6. Responses vision stream;
7. official JS vision smoke if practical;
8. six requests / two slots visibly queue;
9. queue wait + TTFT observable;
10. request-priced accounting exactly once;
11. token-priced behavior only if provider reports/estimates trustworthy image token usage;
12. route deactivate -> safe no-fallback failure;
13. restore -> success;
14. queued credential revoke -> no upstream dispatch;
15. remote URL -> explicit 400/no fetch.

If no installed vision-capable model exists:
- do not silently download one;
- document the exact limitation;
- fake Ollama + real pinned LiteLLM transport remains the authoritative provider-contract proof;
- task can still complete if every deterministic contract/scheduler/security gate is green and the
  handoff truthfully records the missing live-model proof.

Never include image bytes/base64 or prompt/output content in the handoff.

## 22. Catalog / web console

Existing catalog management must expose `image` capability for Provider and ModelAlias.

If the current UI already supports the enum generically:
- add/adjust tests only.

If it cannot configure `image`:
- make the narrowest UI change necessary;
- add component + Playwright coverage.

Do not build a consumer image-upload UI in this task.

## 23. OpenAPI / generated client

Regenerate under the committed dependency lock:
- backend OpenAPI;
- frontend generated OpenAPI;
- frontend generated TypeScript schema.

Do not hand-edit generated files.

Two consecutive generation runs must produce zero additional diff.

## 24. Documentation

Update:
- `docs/contracts/openai-compatibility-baseline.md`
- add `docs/contracts/vision.md`
- README endpoint/capability status
- docs/development/README.md
- docs/releases/alpha1-readiness.md or current release-status document
- security docs for image URL/SSRF policy
- handoff.

Document exactly:

Supported:
- image input -> text output;
- Chat + Responses;
- inline/data image representation;
- PNG/JPEG/WebP subset actually proven;
- non-stream + stream;
- queue/concurrency/accounting behavior;
- capability requirements.

Explicitly unsupported/deferred:
- remote image URL fetching;
- files/file_id input;
- image generation;
- audio;
- tools/MCP/computer use;
- video;
- object/blob storage;
- unsupported media types.

Do not claim full multimodal/OpenAI compatibility.

## 25. Migration decision

Expected: **no migration**.

Migration head remains `0017`.

Do not modify migrations 0001-0017.

A new migration is not justified merely to distinguish vision requests; the encrypted message payload
already owns request content.

## 26. Regression gate

Run:
- backend full suite; baseline **613**
- frontend unit/component; baseline **87**
- full Playwright; baseline **32**
- npm build
- npm lint
- `npx tsc -b --noEmit`
- OpenAPI/client drift
- ruff check src tests
- git diff --check
- secret/content/image-data canary scan
- no-legacy-v1 guard + self-test
- migration empty -> 0017
- populated 0016 -> 0017
- config_check
- fake Ollama text + vision adapter tests
- official Python SDK:
  - Models
  - Chat text non-stream/stream
  - Responses text non-stream/stream
  - Chat vision non-stream/stream
  - Responses vision non-stream/stream
- official JS equivalent smoke
- fresh offline golden path.

Every red test must be root-caused.

## 27. Remote CI gate

Push completed work to `origin/v2`.

On the FINAL v2 SHA require:
- backend = completed/success
- frontend = completed/success
- schema-secret-gate = completed/success
- alpha-golden-path = completed/success.

If any is red:
- inspect logs;
- fix root cause;
- push;
- require all four green on the new final SHA.

Do not declare completion based on local evidence alone.

## 28. Feature PR to main

After the final v2 SHA is green:
- create or update a PR from `v2` -> `main`;
- include the final SHA and verification summary;
- do NOT merge it automatically;
- leave merge for the reviewed user-authorized step.

Never push directly to main.

## 29. No New Scope

Do not implement:
- embeddings;
- image generation endpoints;
- audio;
- files API/storage;
- remote image fetcher;
- tools/function execution;
- MCP;
- computer use;
- video;
- RAG/vector search;
- unrelated UI redesign.

## 30. Handoff

Record:
- implementation commits;
- exact pinned Chat/Responses image contract forms;
- supported media types/source form;
- exact image/payload limits;
- canonical multimodal message representation;
- legacy text payload backward compatibility;
- capability enforcement;
- LiteLLM/Ollama image mapping;
- fake Ollama vision proof;
- SSRF/remote-URL rejection proof;
- quota/budget/accounting behavior;
- logging/redaction behavior;
- Python SDK results;
- JS SDK results;
- live Ollama model/proof or truthful lack-of-installed-model limitation;
- queue/concurrency proof;
- OpenAPI drift;
- migration decision;
- backend/frontend/Playwright counts;
- final v2 SHA + four workflow run IDs/conclusions;
- PR-to-main number/status;
- WIP lifecycle;
- exactly one recommended next step.

Never include image bytes/base64, prompt/output content, credentials/session/OIDC/provider secrets,
encrypted payload bytes, or raw provider bodies.

## Commit and Push

Suggested commits:
- `feat(inference): add canonical multimodal image parts`
- `feat(api): add vision input to chat and responses`
- `test(ci): add deterministic vision compatibility proof`
- `docs: document vision input support`

Use fewer/more scoped conventional commits if cleaner.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to continue/commit/push.

The task is complete only when:
- official Python + JS Chat and Responses vision proofs are green;
- image-bearing requests use the existing durable scheduler/accounting authority;
- images are encrypted while queued;
- remote image URLs cannot create SSRF/egress;
- explicit size/count limits are enforced;
- capability checks prevent image input reaching text-only routes;
- text-only Chat + Responses remain green;
- migration head remains 0017;
- all local gates pass;
- all four remote workflows are green on the final origin/v2 SHA;
- a v2 -> main feature PR exists but is not merged;
- handoff is complete;
- local `.aethergate-wip` is removed only after final remote verification.
