# AetherGate v2 — Current Task

## Task ID
AGV2-025

## Title
Alpha 2 — OpenAI Responses API (text generation, durable queue, official SDK)

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-025
- branch: v2
- UTC start timestamp

It is gitignored.
Never stage, commit, or push it.
Keep it present for the entire incomplete task.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every local/live criterion below is green;
2. every task commit is pushed to origin/v2;
3. the final origin/v2 SHA has all required GitHub Actions green;
4. the handoff is committed/pushed and remote verified.

## Starting Point

Alpha 1 is closed.

Final verified Alpha 1 SHA:
`4cb82a96de23bd535e87829dcf0a5a06937f5c5a`

Remote GitHub Actions on that exact SHA are all green:
- backend — run `38095230150`
- frontend — run `38095230181`
- schema-secret-gate — run `38095230193`
- alpha-golden-path — run `38095230225`

Green baseline:
- backend: **561 collected**
- frontend unit/component: **87 passed**
- full Playwright: **32 passed** (+ CI-only skips)
- migration head: **0017**
- official OpenAI Python SDK pinned: **2.54.0**
- LiteLLM pinned: **1.104.0**
- Pydantic pinned: **2.13.5**
- dependency graph pinned by `constraints.txt`.

Do not regress Alpha 1 hardening.

## Why This Task Exists

The pinned compatibility baseline declares the v2 core target:
- Models;
- Chat Completions;
- Responses;
- Embeddings.

Models + Chat Completions are implemented. Responses is the preferred modern OpenAI text-generation
surface and is the next Alpha 2 feature.

The existing provider-neutral path is currently text-chat oriented:
- `Message(role, content: str)`;
- `GenerationParams`;
- durable encrypted scheduler payload;
- one provider dispatch per execution attempt;
- accounting/quota/budget settlement;
- encrypted stream events.

AGV2-025 must reuse that authority.

It must **not** build a parallel Responses scheduler/provider/accounting path.

This first Responses milestone intentionally supports the text-generation subset that can be represented
faithfully by the existing provider path. Rich tools/multimodal/background/conversation state are
explicitly rejected until their shared internal model exists.

## Compatibility Authority

Before implementation, read:

- `docs/contracts/openai-compatibility-baseline.md`
- pinned upstream OpenAI reference identified there
- the installed/locked official `openai==2.54.0` Python SDK types/behavior
- `project_spec.md` OpenAI compatibility requirements
- current `contracts/openai.py`
- current Chat Completions router/tests.

Do not guess Responses field/event shapes from memory.

Where this task names a semantic requirement but not every DTO field, use the pinned reference +
official SDK as the source of truth.

Update the compatibility baseline with exactly what is implemented.

## Recovery

If context is compacted/restarted/uncertain:
1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history and current GitHub Actions state;
7. continue from repository state.

## 1. Endpoint

Implement:

`POST /v1/responses`

under a dedicated router, e.g.:

`src/aethergate/api/openai_responses.py`

Register it in `aethergate.main`.

Do not add custom queue-status protocol.

Standard synchronous semantics remain:
- request accepted internally into durable queue;
- HTTP call remains open;
- returns a standard Responses API result when completed.

Streaming:
- standard Responses SSE semantic events;
- no proprietary queue events.

Do not add:
- `GET /v1/responses/{id}`;
- delete/cancel Responses resource endpoints;
- background response polling;
unless they are separately required by this task (they are currently deferred).

## 2. Supported text input subset

Support the pinned Responses text-generation request forms that can be mapped faithfully to the current
generation authority.

At minimum support official SDK calls equivalent to:

### Simple string input
`client.responses.create(model="...", input="Hello")`

Map to one user message.

### Text message input
Support the pinned SDK's text-only message/input-item representation for:
- system/developer instruction semantics where representable;
- user text;
- assistant text where valid as prior context.

The exact accepted wire union must match the pinned reference/SDK.

### `instructions`
Support the pinned `instructions` string field.

Preserve its ordering/precedence semantics when mapping to the canonical message list.

If the existing upstream provider path cannot distinguish a Responses `developer` role from system
semantics safely for the current Ollama adapter:
- document the canonical mapping;
- preserve the instruction before user input;
- do not silently drop it.

### Generation controls
Support where present in the pinned Responses schema and faithfully mappable:
- `max_output_tokens` -> internal max output token bound;
- `temperature`;
- `top_p`;
- `stream`.

Do not invent support for Chat-only fields that Responses does not expose.

## 3. Explicit unsupported semantic features

Known semantic fields/features that are not implemented in this milestone MUST be rejected explicitly,
not ignored.

At minimum evaluate and reject when requested/non-default:

- tools;
- tool_choice;
- function/custom tool calls;
- MCP/tool resources;
- computer use;
- multimodal image/audio/file input;
- output audio;
- structured output / JSON-schema formatting if not faithfully supported;
- reasoning controls/items if not faithfully supported;
- `previous_response_id`;
- conversation/thread state;
- background mode;
- response storage/retrieval semantics (`store=true` if storage semantics are implied);
- truncation modes not supported by the scheduler/provider path;
- provider/transport overrides;
- arbitrary upstream URL/headers/secrets;
- multiple candidate outputs if exposed;
- any other pinned semantic field whose effect would otherwise be silently lost.

Use a stable OpenAI-compatible structured error, ideally:
- HTTP 400;
- safe message;
- stable code such as `unsupported_feature`;
- field/param when appropriate;
- gateway request_id.

Purely additive unknown fields may follow the repository's documented additive-field tolerance policy,
but **known semantic fields may never be silently dropped**.

## 4. Shared internal generation path

Responses must translate into the existing internal generation/scheduler model before admission.

Do not duplicate:
- route resolution;
- destination validation;
- authentication;
- queue admission;
- endpoint concurrency;
- quota reservation;
- budget reservation;
- price snapshot;
- worker dispatch;
- cancellation;
- settlement;
- provider error mapping.

Preferred shape:
- protocol-specific request DTO -> canonical `Message[] + GenerationParams`;
- call existing `SchedulingService.admit_and_enqueue`;
- translate terminal scheduler result -> protocol-specific Responses object;
- translate generic encrypted stream events -> protocol-specific Responses SSE events.

If a small shared helper is needed to remove duplicated Chat/Responses scheduling boilerplate, add one
at the API/protocol orchestration layer.

Do not create a second scheduler service.

## 5. Durable payload compatibility

The current durable scheduler payload stores canonical messages + generation params encrypted.

Responses text requests should use the same encrypted payload representation when possible.

Requirements:
- no plaintext prompt/input in scheduler metadata;
- queue encryption unchanged;
- no migration solely to label a request as Chat vs Responses unless truly necessary;
- worker should not need protocol-specific knowledge to generate text;
- API-side protocol translation happens outside provider dispatch authority.

If a protocol discriminator is required for safe replay/stream translation:
- justify it;
- keep it non-content;
- prefer encrypted payload metadata over new plaintext DB columns;
- add migration only if unavoidable.

Expected migration decision: **no migration; head remains 0017**.

## 6. Response object compatibility

Build typed Responses DTOs in the OpenAI contracts module (or a focused Responses contracts module).

For a completed text response, match the pinned official Responses schema sufficiently for
`openai==2.54.0` to parse/use it naturally.

At minimum the response must correctly represent:
- gateway response id;
- object/type discriminator;
- created timestamp;
- model = public AetherGate alias, never upstream model;
- completed status;
- output message item;
- output text content item;
- usage with input/output/total token counts;
- error/incomplete fields per pinned schema where required;
- no provider routing internals.

The convenience SDK property / helper for output text must work if the official SDK exposes one.

Do not return a Chat Completions-shaped object from `/v1/responses`.

Gateway and upstream request IDs remain distinct:
- gateway ID in standard response/request ID fields;
- `X-Upstream-Request-Id` only where already safe/established.

## 7. Responses streaming

Implement pinned Responses streaming semantics over existing durable `StreamEvent` records.

Do not reuse Chat Completions chunk JSON.

Use the exact event names/shapes required by the pinned reference / `openai==2.54.0`.

The event sequence must include the appropriate lifecycle/text events for a normal successful text
response, such as the pinned equivalents of:
- response created/in-progress;
- output item/content part added;
- text delta events;
- text/content/output completion;
- response completed.

Do not assume a terminal `[DONE]` marker if the pinned Responses surface does not use one.

Requirements:
- content deltas preserve order;
- response/output/content indices/IDs are stable across the stream;
- usage is represented at the correct terminal event;
- a successful empty-content response follows the pinned behavior;
- no synthetic success event after failed/cancelled/expired/outcome_unknown execution;
- client disconnect triggers the same durable cancellation semantics as Chat;
- once SSE headers are sent, failures terminate according to pinned stream semantics without pretending
  success.

## 8. Authentication / authorization

Responses uses the exact inference credential path as Chat:

- Bearer `agk_...`;
- audience = inference;
- scope = `inference:invoke`;
- project/principal attribution persisted;
- revalidate credential immediately before dispatch;
- revoked/deactivated queued credential never reaches upstream.

`AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` in all acceptance proofs.

No Responses-specific authentication bypass.

## 9. Routing / capabilities

Responses model name is always the public `ModelAlias.name`.

No client-supplied:
- provider;
- provider account;
- endpoint;
- upstream model;
- base URL.

No hidden fallback.

If alias/provider capabilities are already enforced elsewhere, preserve them.
Do not invent a second capability registry merely for this task.

If a capability flag is needed to distinguish text-generation aliases that may serve Responses:
- use the existing model-alias capability representation;
- document/admin-manage it consistently;
- preserve backwards compatibility for existing Chat-capable aliases.

## 10. Quotas / budgets / accounting

Responses must use the existing transactional scheduler path.

Prove:
- request quota reservation same as Chat;
- token quota behavior same canonical estimate path;
- endpoint slot reservation same;
- project budget reservation same;
- immutable PriceSnapshot same;
- UsageRecord attribution correct;
- signed `usage_debit` ledger correct;
- no duplicate accounting.

Responses `max_output_tokens` participates in output reservation exactly as the canonical generation
bound does for Chat.

If no defensible tokenizer estimate exists under an enforced token/budget policy, preserve current
fail-closed behavior.

## 11. Errors

Responses endpoint uses the existing OpenAI structured error envelope.

Prove at minimum:
- no auth -> 401;
- invalid credential -> 401;
- admin-audience credential -> 401/established inference auth error;
- unknown alias -> established model-not-found response;
- inactive/unrouted alias -> model-unavailable/established response;
- unsupported semantic field -> 400 `unsupported_feature`;
- invalid request shape -> 400;
- queue timeout -> established timeout mapping;
- provider 404/429/5xx -> safe upstream/gateway error behavior;
- no raw provider body/URL/credential/content.

Do not leak validation internals beyond the established safe contract.

## 12. Contract tests

Add a dedicated Responses contract suite.

Cover:
1. simple string input;
2. text input-item/message form from the pinned SDK;
3. instructions;
4. max_output_tokens;
5. temperature/top_p;
6. stream flag;
7. transport-control rejection;
8. each unsupported semantic family;
9. exact response object discriminators;
10. output message/content item shape;
11. usage shape;
12. error shape.

Use fixtures derived from the pinned reference/locked SDK behavior, not memory.

## 13. Scheduler/integration tests

Prove Responses uses the real durable scheduler path:

- queued request lifecycle;
- six requests / two slots through Responses;
- worker claims/dispatches;
- non-stream terminal translation;
- stream event translation;
- queued credential revocation prevents dispatch;
- cancellation before dispatch;
- cancellation after dispatch flags owning worker;
- provider failure;
- outcome_unknown does not pretend success;
- quota/budget reservation + settlement;
- exactly-once usage/ledger settlement.

Reuse existing scheduler tests/helpers rather than duplicating the state machine.

## 14. Official Python SDK tests

Using locked `openai==2.54.0`, prove:

### Non-stream
`client.responses.create(...)`
returns a parsed official SDK Response object with expected text and usage.

### Stream
Use the official SDK's pinned Responses streaming API form.
Iterate typed events and reconstruct the expected text.

Prove:
- expected event types are parsed;
- text deltas reconstruct exactly;
- terminal completed event is parsed;
- usage is available where the SDK/pinned schema defines it.

Also prove:
- unsupported tool request returns the expected SDK error, not silent downgrade;
- bypass=false;
- public alias is used.

Do not use raw `requests`/fetch as a substitute for the official SDK proof.

## 15. Optional JavaScript SDK contract smoke

The project spec requires official Python **and JavaScript** SDK black-box compatibility for core
surfaces.

If the repository does not yet pin the official OpenAI JS SDK, add a narrow dev/CI dependency under
the frontend or a dedicated SDK test directory and lock it with npm.

At minimum add a deterministic offline Node smoke for:
- Responses non-stream;
- Responses stream event parsing.

Do not make browser UI import the OpenAI SDK.

If this creates disproportionate dependency/runtime complexity, document it explicitly and queue a
verification closure task; however, AGV2-025 should prefer closing the project-spec requirement now.

## 16. Fake Ollama / offline CI

Extend the existing deterministic fake Ollama only as needed.

Do not add a new provider kind.

The Alpha golden-path CI must now additionally prove:
- Chat non-stream still works;
- Chat stream still works;
- Responses non-stream works via official Python SDK;
- Responses stream works via official Python SDK;
- if JS SDK smoke is implemented, it also runs offline.

No Internet/model download/provider credentials.

Keep the fake upstream content-free in logs.

## 17. Live nomnom verification

Use a clean/disposable route/project where practical and real internal Ollama.

With inference auth bypass=false:

1. official Python SDK Chat non-stream still succeeds;
2. Chat stream still succeeds;
3. official Python SDK Responses non-stream succeeds;
4. Responses stream succeeds;
5. `instructions` affects the canonical request path and is not silently dropped;
6. Responses request can visibly queue behind capacity;
7. observability records queue wait and streaming TTFT for Responses too;
8. accounting records one UsageRecord/ledger settlement per successful Responses request;
9. deactivate route -> Responses fails safely with no fallback;
10. restore route -> Responses succeeds;
11. revoke queued credential -> it never reaches Ollama;
12. unsupported tool request returns explicit 400 without upstream dispatch.

Never record prompt/completion content in handoff/logs.

## 18. OpenAPI / generated client

Regenerate:
- backend OpenAPI;
- frontend generated schema/types.

Use the locked dependency environment.

Drift must be zero after committed generation.

Do not hand-edit generated files.

Even though the web console need not call `/v1/responses`, the shared generated schema must remain
canonical.

## 19. Compatibility documentation

Update:
- `docs/contracts/openai-compatibility-baseline.md`;
- add `docs/contracts/responses.md` if useful;
- README endpoint list;
- Alpha readiness docs;
- developer docs;
- `docs/development/agent-handoff.md`.

Document exactly:

### Supported in AGV2-025
- text input forms;
- instructions;
- generation controls;
- non-stream;
- stream;
- usage;
- public model alias routing;
- durable queue/accounting behavior.

### Explicitly unsupported/deferred
- tool/function/MCP/computer-use semantics;
- image/audio/file input;
- structured outputs if not implemented;
- reasoning controls if not implemented;
- previous_response_id/conversation state;
- background mode;
- stored response retrieval;
- all other unimplemented Responses semantics.

Do not claim "full Responses API compatibility."

## 20. Existing Chat compatibility must not regress

Run all existing Chat contract/SDK/browser tests.

Do not alter Chat wire shapes just to simplify Responses.

If shared internal refactoring is necessary:
- preserve current Chat JSON/SSE behavior;
- preserve current queue/cancellation/accounting behavior;
- add regression tests first.

## 21. Migration

Expected: **no schema migration**.

Head remains `0017`.

Do not modify migrations 0001-0017.

If a migration becomes unavoidable, stop and document the exact invariant that cannot be achieved with
the existing encrypted payload/result/event model before adding a linear 0018.

## 22. Local regression gate

Run:
- full backend suite; baseline **561**;
- frontend unit/component; baseline **87**;
- full Playwright; baseline **32**;
- npm build;
- npm lint;
- `npx tsc -b --noEmit`;
- OpenAPI/client drift under `constraints.txt`;
- ruff check src tests;
- git diff --check;
- secret/token/content scan;
- migration empty -> 0017;
- populated 0016 -> 0017;
- config_check;
- fake Ollama tests;
- official Python SDK:
  - Models;
  - Chat non-stream;
  - Chat stream;
  - Responses non-stream;
  - Responses stream;
- official JS Responses smoke if implemented.

Every red test must be root-caused.

## 23. Remote CI gate

Push completed work to origin/v2.

On the FINAL task SHA, require remotely observed GitHub Actions:

- backend = completed/success;
- frontend = completed/success;
- schema-secret-gate = completed/success;
- alpha-golden-path = completed/success.

If any is red:
- inspect logs;
- fix root cause;
- push new SHA;
- require all four green on that new final SHA.

Do not declare completion based only on local runs.

## 24. No New Scope

Do not implement in this task:
- embeddings;
- v1 -> v2 migration;
- tools/function execution engine;
- MCP;
- computer use;
- multimodal/image/audio/file inference;
- background Responses jobs;
- Responses persistence/retrieval;
- external vector stores;
- unrelated admin UI changes.

## 25. Handoff

Record:
- implementation commits;
- exact pinned Responses contract/SDK source used;
- supported request fields/input forms;
- explicit unsupported fields;
- canonical Responses -> scheduler mapping;
- streaming event sequence;
- cancellation behavior;
- quota/budget/accounting proof;
- official Python SDK version/results;
- JS SDK version/results if implemented;
- fake-Ollama CI proof;
- live Ollama proof;
- OpenAPI drift;
- migration/no-migration decision;
- backend/frontend/Playwright counts;
- final remote CI SHA + four run IDs/conclusions;
- known limitations;
- WIP marker lifecycle;
- exactly one recommended next step.

Never include prompt/input/output content, raw credentials/session/OIDC/provider secrets, encrypted bytes,
or provider response bodies in the handoff.

## Commit and Push

Suggested primary commit:
`feat(api): add durable Responses text API`

Additional scoped commits for contracts/CI/docs are fine.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to continue/commit/push.

The task is complete only when:
- official SDK Responses non-stream + stream are green;
- Responses uses the existing durable scheduler/accounting authority;
- unsupported semantics fail explicitly;
- Chat remains green;
- migration head remains 0017 unless narrowly justified otherwise;
- local full regressions are green;
- all four GitHub Actions are green on the final origin/v2 SHA;
- handoff is updated;
- local `.aethergate-wip` is removed only after final remote verification.
