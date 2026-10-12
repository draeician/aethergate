# AetherGate - Current Task

## Task ID
AGV2-029

## Title
OpenAI Embeddings API through the durable scheduler/accounting authority

## WIP Marker - FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

.aethergate-wip

Safe contents:
- task=AGV2-029
- branch=v2
- started_at=<UTC timestamp>

The marker is gitignored.
Never stage, commit, or push it.
Keep it present for the entire incomplete task.
If context is compacted/restarted and the task is incomplete, recreate it if missing.
If blocked/incomplete, leave it present.

Remove it only after:
1. every local/live criterion below is green;
2. all task commits are pushed to origin/v2;
3. the exact final origin/v2 SHA has all four required GitHub Actions green;
4. the handoff is committed/pushed and remotely verified;
5. the feature PR to main exists and is updated.

## Starting Point

Vision/multimodal is complete and merged to main.

PR #2:
- merged
- reviewed head: a98d46244346a42eb121a8ba3ce0ceb00615ac1a
- main merge commit: bd5757a508fc45557b075d1c978c3a398c54e979

All four post-merge main workflows are completed/success on that exact merge commit:
- backend: 38199781353
- frontend: 38199781316
- schema-secret-gate: 38199781369
- alpha-golden-path: 38199781191

Current green baseline:
- backend: 691 collected/passed
- frontend: 89 passed
- Playwright: 32 passed / 6 skipped
- migration head: 0017
- Python OpenAI SDK: openai==2.54.0
- JavaScript OpenAI SDK: openai@7.32.0
- LiteLLM: 1.104.0

AGV2-026 previously queued embeddings but was intentionally deferred before implementation.
AGV2-029 is the authoritative resumed/re-scoped embeddings milestone. Do not revive the old task text as authority.

## Goal

Implement POST /v1/embeddings as an OpenAI-compatible, non-streaming inference operation that uses
the SAME durable AetherGate authority as Chat and Responses:

- same inference credential/auth path;
- same route/catalog authority;
- same endpoint max_concurrency;
- same durable queue;
- same leases/fencing;
- same quota/budget authority;
- same cancellation/recovery/outcome_unknown semantics;
- same accounting/ledger authority;
- same provider error/cooldown handling;
- same encrypted-at-rest request/result policy.

Do NOT implement embeddings as a direct synchronous provider call that bypasses the scheduler.

Do NOT fake embeddings by converting the request into a ChatRequest.

## 1. Compatibility authority

Before implementation inspect the exact locked sources, not memory:

- docs/contracts/openai-compatibility-baseline.md
- openai==2.54.0 embedding request/response types and resource behavior
- openai@7.32.0 embeddings resource and SDK behavior
- pinned LiteLLM 1.104.0 embedding implementation
- pinned LiteLLM Ollama transformer/transport used by aembedding
- current fake Ollama
- scheduler payload/result serialization
- inference service / adapter registry
- accounting/quota reservation code.

Important pinned JavaScript behavior already independently confirmed:
openai@7.32.0 sends encoding_format=base64 when the user omits encoding_format, then decodes the
returned base64 Float32 data back to numeric vectors in the SDK.

Therefore base64 support is REQUIRED for official JS default interoperability. It may not be
rejected merely because float is the API-facing default in other clients.

Inspect the exact pinned JS base64 fixture/helper and match its Float32 representation faithfully.
Do not guess byte order/width.

## 2. Endpoint and request contract

Add a dedicated embeddings router/module, preferably:
src/aethergate/api/openai_embeddings.py

Implement:
POST /v1/embeddings

Supported initial input:
- one non-empty string;
- a non-empty array of non-empty strings;
- preserve input ordering exactly.

Token-ID inputs from the pinned contract:
- single token-ID array;
- array of token-ID arrays;

must be explicitly rejected with structured HTTP 400 unsupported_feature unless a faithful
provider-neutral/tokenizer-preserving implementation is proven during this task.

Do not coerce token IDs to strings.

Known request fields:
- model: supported;
- input: supported subset above;
- encoding_format: float and base64 REQUIRED;
- dimensions: implement only if the pinned LiteLLM/Ollama path faithfully carries and honors it;
  otherwise explicitly reject with 400 unsupported_feature;
- user: implement only if its semantics can be faithfully preserved through the current provider path;
  otherwise explicitly reject rather than silently dropping it.

Unknown purely additive fields may follow the existing tolerance policy, but every known semantic or
transport-control field must be implemented or explicitly rejected.

Transport/provider override fields must remain rejected.

Apply pinned request-shape constraints that are relevant and provider-neutral, including empty-input
rejection and bounded multi-input cardinality. Do not invent provider-specific token limits as
OpenAI guarantees for unrelated models.

The existing global encrypted queue payload byte limit remains authoritative.

## 3. Response contract

Return the pinned OpenAI shape:

- object = "list"
- data = ordered array
- each item:
  - object = "embedding"
  - index = original input position
  - embedding = numeric float array for encoding_format=float
  - embedding = base64 string for encoding_format=base64
- model = PUBLIC AetherGate model alias, never upstream model name
- usage:
  - prompt_tokens
  - total_tokens

No completion-token field is exposed in the OpenAI embeddings response.

For base64:
- faithfully encode the vector as the pinned SDK expects;
- exact Float32 byte representation;
- deterministic tests against the locked JS SDK decode path;
- reject malformed/non-finite provider vector values before serialization.

## 4. Provider-neutral embedding model

Add explicit internal types, separate from ChatRequest, such as:
- EmbeddingRequest
- EmbeddingResult
- embedding usage representation or the existing Usage type with completion_tokens=0 where safe.

The request must carry:
- provider kind;
- trusted admin-configured base destination;
- upstream model;
- ordered text inputs;
- supported embedding options;
- timeout.

The result must carry:
- ordered vectors;
- measured usage if provided;
- upstream request ID if available.

Validate provider results:
- result count equals input count;
- input order/index correspondence is preserved;
- vectors are non-empty where expected;
- every vector element is numeric and finite;
- no NaN/Infinity;
- no provider response body/vector contents are logged.

## 5. Adapter/registry design

Generalize the adapter boundary explicitly.

Do not pretend embeddings are chat.

The Ollama/LiteLLM adapter must expose a real embed operation, using the pinned LiteLLM embedding API
(for example aembedding if that is the pinned correct path).

Keep hidden provider retries/fallback disabled.

The registry must make embedding support explicit enough that an image/text-capable adapter cannot
silently be assumed embedding-capable merely because the provider kind is "ollama".

Provider errors remain sanitized.
Provider 429 feedback must continue feeding the shared cooldown authority.

## 6. Discover the real LiteLLM -> Ollama embedding transport

Empirically determine the exact HTTP endpoint and payload emitted by pinned LiteLLM 1.104.0.

Do not guess between endpoints such as /api/embed and /api/embeddings.

Add provider-contract tests that prove:
- exact endpoint used;
- configured base destination is authoritative;
- configured upstream_model is authoritative;
- ordered single/multi input reaches the transport;
- retries/fallback are disabled;
- provider 404/429/5xx become safe ProviderError values;
- malformed vector results fail safely.

Extend the fake Ollama to implement the exact discovered embedding transport.

The fake may record safe metadata such as:
- request count;
- input count;
- upstream model;
- vector count/dimension.

It must NOT persist/log raw input text or embedding vector contents.

## 7. Capability enforcement

Capability.EMBEDDING already exists.

An embedding request must require:
- the public ModelAlias advertises embedding;
- the Provider advertises embedding;
- the selected adapter supports embeddings.

Re-check capability at dispatch time so queued work cannot dispatch after capability is revoked.

No fallback to a text-only/image-only model.
No inference of embedding capability solely from provider kind or endpoint URL.

Catalog/UI:
- if the current enum-driven management UI already exposes embedding, add/adjust tests only;
- otherwise make the narrowest UI change necessary.
Do not build a consumer embeddings UI.

## 8. Encrypted operation-aware queue payload

The current durable scheduler payload is chat-shaped.

Generalize it minimally into an operation-aware encrypted envelope.

Required:
- chat operation;
- embedding operation;
- operation discriminator lives INSIDE payload_encrypted;
- do not add a plaintext DB operation column merely for this feature;
- old pre-AGV2-029 payloads with no operation discriminator MUST deserialize as chat;
- pre-vision text payloads remain readable;
- current multimodal chat payloads remain readable;
- embedding inputs are encrypted at rest;
- no embedding text appears in request metadata/logs/DB plaintext columns.

Prefer typed internal queued-payload variants rather than dict-shape guessing across the scheduler.

Migration is expected to remain unnecessary.

## 9. Operation-aware scheduler preparation and dispatch

Refactor only as much as required so one scheduler can claim and prepare either:
- chat generation; or
- embedding.

The common state machine must remain single-source:
validated -> queued -> reserved -> dispatched -> succeeded/failed/cancelled/expired/outcome_unknown.

Claim flow must still perform, transactionally where currently required:
- decrypt payload;
- determine operation;
- dispatch-time inference credential revalidation;
- route/catalog re-resolution;
- destination/egress validation;
- capability revalidation;
- quota evaluation/reservation;
- budget evaluation/reservation;
- endpoint slot reservation;
- execution attempt/lease/fencing;
- provider preparation.

Do not copy/paste a second embeddings scheduler.

Embedding requests are non-streaming.
They must never use run_stream.
They must never fabricate stream events or a TTFT sample.

## 10. Quota semantics

Request quota:
- one embeddings API request consumes exactly one request unit regardless of the number of input
  strings/vectors.

Token quota:
- count/reserve INPUT tokens summed across all embedding inputs;
- output token reservation is exactly zero;
- vector dimension/count is never token usage;
- raw character/byte count is never substituted as fake token usage.

Add an adapter/provider-specific conservative embedding input token estimator only if defensible.

If an enforced token quota requires an estimate and no trustworthy estimator is available:
- fail closed with the existing estimator-unavailable family;
- do not dispatch.

Do not require a chat max_output_tokens/default_output_tokens bound for embeddings.

This is a critical regression point: chat quota behavior must remain exactly as before.

## 11. Budget/accounting semantics

Request-priced embedding route:
- one request unit, regardless of input count/vector count.

Token-priced embedding route:
- reservation based on estimated input units;
- output_units = 0;
- no output-token bound requirement.

If a token-priced enforced budget requires input estimation and the estimator is unavailable:
- fail closed before dispatch.

Successful settlement:
- use measured provider input tokens when trustworthy usage is returned;
- output_units = 0;
- request_units follows billing unit rules;
- one successful gateway embeddings request produces exactly ONE UsageRecord;
- exactly ONE signed negative usage_debit ledger entry;
- immutable PriceSnapshot behavior unchanged;
- exactly-once settlement under fencing/idempotency unchanged.

Do not price vector dimensions as tokens.
Do not implement BillingUnit.IMAGE for embeddings.
Do not invent token measurements when provider usage is absent.

## 12. Encrypted operation-aware result

The current result envelope is chat-shaped.

Generalize it so successful encrypted terminal results can represent:
- chat completion result; or
- embedding result.

Required:
- existing legacy chat results without an operation discriminator remain readable;
- embedding vectors live only inside encrypted result content before API response;
- vectors never appear in logs/plain audit metadata;
- terminal API translation is typed enough that embeddings cannot be confused with chat content.

wait_for_terminal or its replacement must expose an unambiguous typed result for the embeddings API.

## 13. Auth/cancellation/recovery

Use the same inference credential:
- Bearer agk_...
- inference audience
- inference:invoke scope
- project/principal attribution
- bypass=false in acceptance proofs.

Queued credential revocation must prevent dispatch.

Cancellation/disconnect and ambiguous upstream outcomes must retain existing safe semantics:
- no duplicate dispatch;
- no immediate free/retry on ambiguous result;
- no double accounting;
- outcome_unknown remains operator-reconcilable.

## 14. Observability

Embedding requests must contribute to the existing:
- queue/in-flight counts;
- slot utilization;
- queue wait;
- upstream health/error rate;
- retry/cooldown visibility;
- accounting/usage views.

Do NOT fabricate first-token/TTFT measurements for embeddings.

If existing dashboards assume every successful request has TTFT, fix the narrow read-model/UI
assumption rather than synthesizing a fake sample.

## 15. Fake Ollama / deterministic CI

Extend the fake provider with deterministic embedding vectors through the exact LiteLLM transport.

Prove:
- single input;
- multiple inputs;
- order preservation;
- deterministic vector shape;
- measured usage mapping if the fake transport contract supports it;
- 404;
- 429 + cooldown;
- 5xx;
- malformed result;
- non-finite result;
- wrong result count.

Never log input text or vectors.

## 16. Scheduler/accounting regression scenarios

Add durable scheduler tests including:
- six embedding requests / two endpoint slots: two execute, four queue, all drain as capacity frees;
- embedding capability absent on alias -> no dispatch;
- embedding capability absent/revoked on provider -> no dispatch;
- queued inference credential revoked -> no dispatch;
- route/endpoint deactivate -> safe failure/no fallback;
- request quota;
- token quota with estimator;
- token quota estimator unavailable -> fail closed;
- request-priced budget success;
- token-priced budget input-only reservation/settlement;
- cancellation;
- outcome_unknown/reconcile behavior;
- exactly-once accounting under duplicate/fenced settlement attempts.

Keep existing Chat + Responses + vision scheduler tests green.

## 17. Official Python SDK

Using locked openai==2.54.0, prove black-box:

- models list still works;
- embeddings.create with one string;
- embeddings.create with multiple strings;
- encoding_format="float";
- encoding_format="base64" if the Python SDK exposes/preserves the string response;
- expected ordered indexes;
- public alias in response;
- usage parsed;
- unsupported token-ID input -> SDK-visible 400;
- unsupported dimensions/user behavior -> SDK-visible 400 where not faithfully implemented;
- capability mismatch -> safe 400;
- bypass=false.

Do not substitute raw requests/httpx for the official SDK interoperability proof.

## 18. Official JavaScript SDK - base64 is mandatory

Using locked openai@7.32.0 prove:

- client.embeddings.create with one string and NO encoding_format;
- the SDK sends base64 on the wire and successfully decodes the response into numeric vectors;
- multiple string inputs preserve order;
- explicit encoding_format: "float" returns numeric vectors;
- explicit encoding_format: "base64" returns base64 strings per the pinned overload behavior;
- safe structured errors for unsupported semantics.

This default-JS proof is a release gate.

The server's base64 representation must match the pinned SDK's Float32 decode exactly.

## 19. Offline alpha golden path

Extend the fully offline fake-Ollama golden path with a distinct embedding-capable alias/route.

Golden path must retain existing:
- Models;
- Chat text + vision;
- Responses text + vision;
- Python SDK;
- JS SDK;
- backup/restore.

Add embeddings:
- Python one + multi input;
- JS default/base64-decode path;
- explicit float;
- embedding capability rejection on text/vision-only alias;
- unsupported token-ID/dimensions/user cases as applicable;
- backup/restore preserves embedding catalog configuration;
- post-restore embeddings succeeds.

No Internet.
No model download.
No provider credentials.

## 20. Live nomnom verification

First inspect already installed Ollama models and identify an ACTUALLY embedding-capable model.

Do not assume a generation/vision model can embed.
Do not silently download a model.

If an embedding-capable model is installed:
1. configure it through supported admin surfaces;
2. alias + provider advertise embedding capability;
3. official Python single/multi embeddings;
4. official JS default embeddings path;
5. verify real pinned LiteLLM transport;
6. six requests / two slots visibly queue and drain;
7. request-priced accounting exactly once;
8. token-priced accounting only if provider usage/estimation is trustworthy;
9. route deactivate -> safe no-fallback failure;
10. queued credential revoke -> no upstream dispatch;
11. observability shows queue/upstream health but no fabricated TTFT.

If NO embedding-capable model is installed:
- do not download one silently;
- document that truthful limitation;
- deterministic fake Ollama + real pinned LiteLLM transport contract tests are authoritative for
  provider wire compatibility;
- this lack of a live model alone does not fail the milestone if every deterministic/API/scheduler/
  accounting/SDK gate is green.

Never put input text or vectors in the handoff.

## 21. OpenAPI / generated client

Regenerate under the committed dependency lock:
- backend OpenAPI;
- frontend generated OpenAPI;
- frontend generated TypeScript schema.

Do not hand-edit generated artifacts.

Two consecutive generation runs must produce zero additional diff.

## 22. Documentation

Update:
- docs/contracts/openai-compatibility-baseline.md
- add docs/contracts/embeddings.md
- README.md
- docs/development/README.md
- current release/readiness docs
- accounting/scheduler docs if operation-aware behavior needs clarification
- docs/development/agent-handoff.md.

Document exactly:
Supported:
- POST /v1/embeddings
- string + list-of-strings input
- float + base64 encoding
- non-streaming only
- durable queue/concurrency/cancellation/auth/accounting
- embedding capability requirements.

Explicitly unsupported/deferred where not implemented:
- token-ID input;
- dimensions if current provider path cannot faithfully honor it;
- user if not faithfully supported;
- provider additions;
- batch/async embeddings;
- vector database/RAG/storage.

Do not claim full OpenAI embeddings compatibility beyond the tested subset.

## 23. Migration

Expected: NO migration.

Keep head at 0017.
Do not modify migrations 0001-0017.

The operation discriminator belongs inside the encrypted scheduler payload, not in a new plaintext
request-content column.

If implementation truly requires a migration, stop and root-cause why before proceeding; do not add
one casually.

## 24. Regression gate

Run the complete current gate:

- backend full suite; baseline 691
- frontend unit/component; baseline 89
- full Playwright; baseline 32 passed / 6 skipped
- npm build
- npm lint
- npx tsc -b --noEmit
- OpenAPI/client drift
- ruff check src tests
- git diff --check
- secret/content/vector-data scan
- no-legacy-v1 guard + self-test
- migration empty -> 0017
- populated 0016 -> 0017
- config_check
- fake Ollama chat + vision + embeddings
- official Python Models + Chat + Responses + Embeddings
- official JS Models + Chat + Responses + Embeddings
- offline golden path.

Every red test must be root-caused.
Do not weaken existing vision validation/security to make embeddings pass.

## 25. Remote CI gate

Push completed work to origin/v2.

On the EXACT FINAL v2 SHA require:
- backend = completed/success
- frontend = completed/success
- schema-secret-gate = completed/success
- alpha-golden-path = completed/success.

If any is red:
- inspect logs;
- root-cause;
- fix;
- push;
- require all four green again on the new final SHA.

Do not declare completion from local results alone.

## 26. Feature PR to main

After the final v2 SHA is green:
- create a new PR from v2 -> main (expected PR #3 if no other PR is created);
- include final SHA and verification summary;
- verify mergeable;
- keep it open/unmerged.

Do NOT merge it automatically.
Never push directly to main.

## 27. No new scope

Do not implement:
- image generation;
- audio/STT/TTS;
- remote image fetch;
- files API;
- tools/function calling;
- MCP/computer use;
- RAG/vector DB;
- batch/async embeddings;
- new provider kinds;
- unrelated frontend redesign.

## 28. Handoff

Record:
- implementation commits;
- exact pinned embedding contract subset;
- JS default-base64 behavior and exact encoding proof;
- supported/rejected request fields;
- operation-aware encrypted payload/result design;
- legacy chat payload/result backward compatibility;
- adapter/LiteLLM/Ollama transport discovery;
- capability enforcement;
- quota/budget/accounting semantics;
- fake provider proof;
- Python SDK proof;
- JS SDK proof;
- live nomnom proof or truthful no-installed-embedding-model limitation;
- no-TTFT behavior;
- OpenAPI drift;
- migration decision;
- backend/frontend/Playwright counts;
- final v2 SHA + exact four workflow IDs/conclusions;
- PR status/head;
- WIP lifecycle;
- exactly one recommended next step.

Never include input text, embedding vectors/base64, credentials, provider/session secrets, encrypted bytes,
or large logs.

## Commit / Push

Use scoped conventional commits, for example:
- feat(inference): add durable embedding operation
- feat(api): add openai embeddings endpoint
- test(ci): prove embeddings sdk compatibility
- docs: document embeddings support

Use fewer/more commits if cleaner.

Push all completed work to origin/v2.
Never push directly to main.
Do not ask whether to continue/commit/push.

The task is complete only when:
- official Python and JS embeddings interoperability is green;
- JS default base64 path is proven;
- embeddings use the existing durable scheduler/accounting authority;
- capability checks prevent dispatch to non-embedding routes;
- quotas/budgets/accounting are operation-correct and input-only for embeddings;
- vectors and inputs remain encrypted at rest and absent from logs;
- existing Chat/Responses/vision regressions remain green;
- migration head remains 0017;
- all four remote workflows are green on the exact final v2 SHA;
- a new v2 -> main PR exists and remains unmerged;
- handoff is complete;
- .aethergate-wip is removed only after final remote verification.
