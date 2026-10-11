# AetherGate v2 — Current Task

## Task ID
AGV2-026

## Title
Alpha 2 — OpenAI Embeddings API through the durable scheduler

## WIP Marker — FIRST LOCAL ACTION

Immediately after entering the repository, before pull/read/implementation work, create:

`.aethergate-wip`

Safe contents:
- task ID: AGV2-026
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
4. the handoff is committed/pushed and remote verified.

## Starting Point

AGV2-025 is complete and reviewed PASS.

Final verified Responses SHA:
`07ae618ff95503f765ae2f4ad45f531292921b3a`

Remote workflows on that exact SHA:
- backend — run `38107167919` — completed/success
- frontend — run `38107167870` — completed/success
- schema-secret-gate — run `38107167910` — completed/success
- alpha-golden-path — run `38107167873` — completed/success

Green baseline:
- backend: **613 collected**
- frontend: **87 passed**
- full Playwright: **32 passed** (+ 3 CI-only skips)
- migration head: **0017**
- Python SDK: `openai==2.54.0`
- JS SDK: `openai@7.32.0`
- LiteLLM: `1.104.0`
- pinned dependency graph: `constraints.txt`.

Core OpenAI target status:
- Models — implemented
- Chat Completions — implemented
- Responses text subset — implemented
- Embeddings — this task

## Why This Task Exists

`POST /v1/embeddings` is the remaining core v2 OpenAI endpoint in the pinned compatibility target.

Embeddings are not chat generation, but they must use the same AetherGate authority:

- inference credential authentication;
- public model aliases;
- approved route/egress resolution;
- durable queue;
- endpoint concurrency reservations;
- shared quota/cooldown authority;
- project budgets;
- immutable price snapshots;
- usage/ledger settlement;
- worker leases/fencing/cancellation/recovery;
- passive upstream health/retry observability;
- encrypted queued payload/result storage.

Do NOT implement embeddings as a direct synchronous adapter call that bypasses the scheduler.

The scheduler currently assumes a chat payload/result. This task must generalize that internal boundary
carefully while preserving all existing Chat + Responses behavior.

## Compatibility Authority

Before implementation, read:

- `docs/contracts/openai-compatibility-baseline.md`
- pinned upstream OpenAI reference named there
- locked `openai==2.54.0` Python Embeddings request/response types
- locked `openai@7.32.0` JS SDK behavior
- LiteLLM `1.104.0` embedding API behavior for provider kind `ollama`
- current fake Ollama implementation
- current scheduler payload/result serializers
- accounting/quota reservation code.

Do not guess the embedding wire schema, base64 encoding, or Ollama endpoint from memory.

## Recovery

If context is compacted/restarted/uncertain:

1. ensure `.aethergate-wip` exists if task incomplete;
2. re-read AGENTS.md;
3. re-read project_spec.md;
4. re-read this file;
5. re-read docs/development/agent-handoff.md;
6. inspect git status/history and GitHub Actions state;
7. continue from repository state.

## 1. Endpoint

Implement:

`POST /v1/embeddings`

in a dedicated router, e.g.:

`src/aethergate/api/openai_embeddings.py`

Register it in `aethergate.main`.

Semantics:
- validate/authenticate;
- durably enqueue;
- hold the normal synchronous HTTP request open while queued/executing;
- return the standard Embeddings response when terminally successful.

Embeddings are non-streaming.
Do not invent an embedding stream endpoint or proprietary queue events.

## 2. Supported request subset

Use the pinned OpenAI contract/SDK as authority.

At minimum support official SDK calls for:

- a single text string input;
- a list of text strings preserving input order;
- `model` as public AetherGate alias.

### encoding_format

Support the pinned default float representation.

If the pinned API/SDK defines `encoding_format="base64"`, implement it if the encoding can be
performed faithfully at the protocol translation layer.

Before implementing base64:
- inspect the pinned official SDK/reference for exact byte representation/endian/float width;
- add byte-level deterministic vectors;
- do not guess.

If faithful base64 cannot be established from the pinned reference, reject it explicitly with
`unsupported_feature` rather than silently returning floats.

### dimensions

Inspect the pinned contract + LiteLLM/Ollama behavior.

If `dimensions` can be passed faithfully through the existing provider adapter for the tested
embedding route, support it and test exact output dimension.

Otherwise:
- explicitly reject non-null `dimensions` with 400 `unsupported_feature`;
- never silently ignore it.

### token-array input

If the pinned endpoint accepts token-ID arrays but the current Ollama/LiteLLM path cannot faithfully
represent them:
- explicitly reject those input variants;
- do not stringify token IDs;
- do not reinterpret them as text.

### other semantic fields

Known semantic fields such as `user` or future embedding controls must be either:
- implemented faithfully; or
- explicitly rejected.

Purely additive unknown fields may follow the documented tolerance policy.

## 3. Transport/routing override rejection

As with Chat/Responses, inference callers may never choose transport/routing internals.

Reject:
- base_url/api_base;
- provider/provider account;
- endpoint;
- upstream model;
- arbitrary headers;
- api_key/api_token;
- deployment/custom_api_base;
- equivalent transport-control fields.

Use the existing safe structured OpenAI error envelope.

## 4. Capability enforcement

The domain already defines:

`Capability.EMBEDDING = "embedding"`

Embedding dispatch must require the configured public route to be intentionally embedding-capable.

At minimum:
- ModelAlias must advertise `embedding`;
- Provider must advertise `embedding`;
- registered provider adapter must implement embedding.

If any requirement is absent:
- fail before upstream dispatch;
- return a stable safe model/capability-unavailable error;
- no hidden fallback to a text model.

Do not infer embedding capability merely because an Ollama endpoint exists.

Existing text aliases and Chat/Responses must remain backward compatible.

Do not retroactively require every existing text provider record to add capabilities unless the
existing text path already enforces them.

## 5. Generalize the encrypted scheduler payload — no DB operation column

Expected migration decision: **no migration; head remains 0017**.

Introduce a versioned/canonical encrypted payload envelope, e.g.:

- operation = `chat`; chat messages + generation params
- operation = `embedding`; ordered text inputs + supported embedding params

Requirements:
- operation discriminator remains inside `payload_encrypted`;
- no plaintext prompt/input;
- no new DB column solely for operation kind;
- legacy chat payloads already queued/stored without an operation discriminator must deserialize as
  `chat`;
- new Chat/Responses behavior must remain wire-compatible;
- queue max encrypted/plain payload bounds still apply.

Add explicit backward-compatibility tests for a pre-AGV2-026 chat payload.

Do not make workers infer operation from request content heuristics.

## 6. Provider-neutral adapter model

Extend `src/aethergate/adapters/base.py` with embedding-specific types.

Recommended concepts:

- `EmbeddingRequest`
  - provider_kind
  - base_destination
  - upstream_model
  - ordered tuple/list of text inputs
  - supported embedding parameters
  - timeout

- `EmbeddingResult`
  - ordered vectors
  - `Usage`
  - upstream_request_id

- an `EmbeddingAdapter` protocol or a unified provider adapter protocol with explicit
  `embed(...)`.

Do not pretend an embedding is a ChatRequest/CompletionResult.

The existing Chat adapter protocol remains valid.

## 7. LiteLLM embedding adapter

Implement the embedding operation through pinned LiteLLM `1.104.0`.

Requirements:
- provider kind remains `ollama`;
- use the admin-configured base destination;
- use the route's admin-configured upstream model;
- `num_retries/max_retries=0` or equivalent — no hidden SDK retry;
- provider errors map through the existing sanitized `ProviderError`;
- safe numeric status/retry-after feedback preserved where available;
- no raw body/header/URL/secret leakage;
- input order preserved;
- vector order preserved;
- reject malformed/non-finite embedding values.

No new production provider kind.

## 8. Deterministic fake Ollama embedding surface

Extend `src/aethergate/testing/fake_ollama.py` to implement the exact Ollama HTTP embedding endpoint
that the pinned LiteLLM adapter actually calls.

Discover/verify it from the real pinned adapter behavior; do not assume whether the path is
`/api/embed`, `/api/embeddings`, or another form.

Provide deterministic:
- single-input vector;
- multi-input vectors;
- stable dimensions;
- usage/token metadata as available through the real LiteLLM mapping;
- configurable delay;
- 404;
- 429;
- 5xx.

Use canned deterministic numeric vectors.
Never log or persist submitted input text.

Tests must exercise the **real LiteLLM adapter transport** against the fake server.

## 9. Inference service

Extend `InferenceService` with embedding preparation/estimation that reuses:

- route resolution;
- egress destination policy;
- SecretRef resolution;
- adapter registry.

Recommended:
- `prepare_embedding_from_resolved`;
- `estimate_embedding_input_tokens`.

Do not duplicate destination/secret logic.

Provider/model-specific input-token estimation rules remain fail-closed when token quota/budget
enforcement requires an estimate.

## 10. Scheduler admission refactor

Generalize the minimum necessary scheduler boundary so chat and embedding workloads share the same
state machine.

Keep one:
- queue;
- claim path;
- lease/fence model;
- reservation table;
- cancellation/recovery path;
- worker.

Do not copy `claim_and_reserve` into an embedding scheduler.

At claim time:
1. decrypt canonical payload;
2. identify operation;
3. revalidate auth;
4. re-resolve alias/route;
5. revalidate embedding capability for embedding work;
6. evaluate budget/quota using operation-specific input/output reservation units;
7. reserve quota/budget/slot transactionally;
8. prepare the correct provider operation.

A `ClaimedWork` may carry an explicit operation + operation-specific prepared dispatch union.

## 11. Embedding quota semantics

Request quotas:
- identical to Chat/Responses: one gateway embedding request consumes one request unit, regardless of
  number of input strings.

Token quotas:
- count/estimate the total input-token units across all embedding inputs;
- embedding output vectors consume **zero output tokens**;
- no `default_output_tokens` or generation-token reserve for embeddings;
- if an enforced token quota exists and no defensible provider/model estimator is available, fail
  closed using the established estimator-unavailable behavior.

Do not count vector dimensions as output tokens.

Add tests proving a multi-input request aggregates input-token usage correctly.

## 12. Embedding budget/accounting semantics

Use the existing PricePolicy/PriceSnapshot/Budget/UsageRecord/Ledger authority.

### request-priced route
- one embedding API request = request_units 1;
- vector count does not multiply request price.

### token-priced route
- amount is based on measured/estimated **input tokens**;
- output_units = 0;
- output-price contribution = 0;
- no made-up charge for vector dimensions.

At dispatch:
- capture immutable PriceSnapshot exactly like other inference work;
- reserve project budget transactionally.

At settlement:
- UsageRecord input_units = measured embedding input tokens;
- output_units = 0;
- one UsageRecord per successful gateway request;
- one signed negative usage_debit;
- exactly-once semantics unchanged.

If provider usage is insufficient for trustworthy token-priced settlement:
- do not invent units;
- fail closed or follow the established accounting invariant appropriate to the current scheduler;
- document/test the behavior.

## 13. Encrypted result representation

Extend the encrypted terminal result envelope to distinguish:
- chat completion result;
- embedding result.

Embedding result contains:
- ordered vectors;
- trustworthy usage;
- upstream_request_id.

Requirements:
- vectors are encrypted at rest in `result_encrypted`;
- vectors are never copied into plaintext scheduler metadata/logs;
- old encrypted chat results remain readable;
- API protocol translation performs float/base64 formatting only after decrypting the terminal result
  for the authorized waiting request.

No schema migration solely for embedding result shape.

## 14. Worker dispatch

The worker's non-stream path must branch by canonical operation:

### chat
Preserve existing:
- adapter.complete
- result serializer
- accounting
- errors.

### embedding
Use:
- adapter.embed
- embedding result serializer
- same cancellation check;
- same fenced terminal settlement;
- same provider failure classification;
- same cooldown application for 429;
- same endpoint reservation release;
- same accounting settlement.

Embeddings never enter `run_stream`.
No TTFT/first_token_at sample should be fabricated.

## 15. Terminal result/API translation

Generalize `wait_for_terminal` / terminal-result decryption enough for the Embeddings router to
obtain the embedding vectors and usage without breaking Chat/Responses callers.

Prefer an explicit typed/operation-aware terminal payload instead of adding unrelated nullable fields
until the result object becomes ambiguous.

Chat + Responses current callers must continue to work unchanged or through a narrow compatibility
wrapper.

## 16. OpenAI Embeddings response

Return the exact pinned response shape.

At minimum:
- `object = "list"`;
- `data` in input order;
- each item has:
  - `object = "embedding"`;
  - correct `index`;
  - embedding vector in requested supported encoding;
- `model` = public AetherGate alias, never upstream model;
- usage:
  - prompt/input tokens according to pinned field names;
  - total tokens;
- gateway request ID remains available through established response headers/correlation.

Do not leak:
- endpoint;
- provider account;
- upstream model;
- upstream URL.

## 17. Authentication / queued revalidation

Use the exact same inference auth path:
- inference-audience `agk_`;
- `inference:invoke`;
- project/principal attribution;
- bypass=false in acceptance.

Dispatch-time credential revalidation is mandatory:
- revoke a queued embedding credential;
- prove it never reaches upstream;
- reservations are not incorrectly retained.

## 18. Cancellation / ambiguous outcomes

Client disconnect while queued/reserved:
- same durable cancellation/release path.

Disconnect/worker loss after durable dispatch:
- preserve the existing conservative ambiguity semantics;
- never assume an embedding did not run just because the client disconnected;
- no duplicate automatic execution.

Add deterministic scheduler/fault tests for embedding work.

## 19. Observability

Existing metrics should naturally include embeddings where semantically correct:

- queue wait — yes;
- retry rate — yes;
- upstream passive health — yes;
- TTFT — **no** (embeddings are non-streaming; no first-token sample).

Prove embedding provider success/failure contributes to endpoint upstream health.

Do not fabricate TTFT=0.

## 20. Contract/API tests

Add a dedicated Embeddings suite covering at least:

1. single text input;
2. list of text inputs;
3. empty/invalid input;
4. input order preserved;
5. default float encoding;
6. base64 encoding if implemented;
7. dimensions if implemented;
8. token-array form explicit rejection if unsupported;
9. known unsupported semantic fields explicit rejection;
10. transport override rejection;
11. public model alias in response;
12. exact response discriminators/item indices;
13. usage shape;
14. capability rejection;
15. unknown alias;
16. inactive/unrouted alias;
17. provider 404;
18. provider 429/cooldown;
19. safe 5xx;
20. inference auth audience/scope behavior.

## 21. Scheduler/accounting tests

Add deterministic coverage proving:

- embeddings use durable enqueue/claim/dispatch;
- six embedding requests / two endpoint slots;
- same endpoint reservation rules;
- no streaming state/TTFT;
- request quota reservation;
- aggregate input token quota across multi-input request;
- no output-token reservation;
- token estimator unavailable fails closed when required;
- request-priced budget;
- token-priced budget with output_units=0;
- exact PriceSnapshot;
- UsageRecord + signed usage_debit;
- no duplicate settlement;
- cancellation before dispatch;
- queued credential revocation;
- provider failure;
- ambiguous worker loss does not auto-retry.

Preserve all Chat/Responses scheduler tests.

## 22. Official Python SDK

Using locked `openai==2.54.0`, prove against deterministic fake Ollama and live gateway:

`client.embeddings.create(...)`

At minimum:
- single text;
- list of texts;
- parsed official SDK CreateEmbeddingResponse;
- input order/indexes;
- vector values/dimensions;
- usage;
- public alias.

If base64 is supported:
- prove the official SDK parses/returns the pinned base64 representation.

Also prove:
- unsupported dimensions/token-array/user/etc. surfaces return SDK-visible 400 when not supported;
- inference bypass=false.

Do not substitute raw HTTP for the official SDK acceptance.

## 23. Official JavaScript SDK

Extend the locked `openai@7.32.0` smoke to prove:

- embeddings single input;
- embeddings multi-input;
- ordering;
- vector shape/usage;
- explicit 400 for one unsupported semantic feature.

Do not import the SDK into the React web application.

## 24. Offline Alpha golden path

Extend deterministic CI to create an embedding-capable route to fake Ollama.

Prefer:
- one provider/account/endpoint can advertise both `text` + `embedding` if semantically valid;
- use a separate public embedding alias and separate route/upstream model.

Prove after fresh install:
- Chat non-stream + stream;
- Responses non-stream + stream;
- Embeddings single + multi;
- Python SDK;
- JS SDK.

Backup/restore phase must prove:
- embedding route survives;
- same credential still authenticates;
- post-restore embedding call succeeds;
- accounting/history counts remain consistent.

No Internet/model downloads/provider credentials.

## 25. Live nomnom verification

First inspect the real internal Ollama installation for an **already installed embedding-capable model**.

Do not silently treat an arbitrary generation model as embedding-capable.

If an installed embedding model exists:
1. create/activate a disposable embedding provider/alias/route via supported admin path;
2. provider + alias capabilities include `embedding`;
3. official Python SDK single input succeeds;
4. multi-input succeeds;
5. official JS smoke succeeds if practical against the live route;
6. six requests / two slots visibly queue;
7. queue-wait appears;
8. TTFT sample count does not increase from embeddings alone;
9. passive upstream health records successes;
10. deliberate invalid upstream model records safe failure;
11. restore valid model and succeed;
12. request-priced accounting exactly once;
13. token-priced accounting input-only if the provider reports trustworthy token usage;
14. queued credential revoke prevents upstream dispatch;
15. deactivate route -> safe no-fallback failure; restore -> success.

If **no embedding-capable model is installed**, do not silently download a large model and do not fake a
real-provider proof. Document the exact limitation in the handoff and make the deterministic fake
Ollama + real LiteLLM transport the authoritative provider-contract proof. A small model download may
only be performed if repository/operator policy already explicitly permits it.

Never record embedding input text or vector contents in handoff/logs.

## 26. Catalog/admin UI

Do not build a new embeddings-specific UI.

Existing Provider/ModelAlias capability fields must be able to configure `embedding`.

If the existing web capability selector cannot represent the already-defined `embedding` enum:
- fix that narrow catalog UI gap;
- add component/E2E coverage.

Do not redesign catalog pages.

## 27. OpenAPI/generated client

Add typed Embeddings contracts.

Regenerate under `constraints.txt`:
- backend OpenAPI;
- frontend schema/types.

Do not manually edit generated files.

Two consecutive generation runs must produce zero additional diff.

## 28. Compatibility docs

Update:
- `docs/contracts/openai-compatibility-baseline.md`;
- add `docs/contracts/embeddings.md`;
- README endpoint list;
- Alpha readiness status;
- development docs;
- handoff.

Document exactly:
- supported input forms;
- supported encodings;
- dimensions support/rejection;
- token-array support/rejection;
- capability requirements;
- quota semantics;
- accounting semantics;
- no TTFT;
- explicit unsupported fields.

After AGV2-026, core v2 OpenAI endpoint status should truthfully be:
- Models implemented;
- Chat Completions implemented;
- Responses text subset implemented;
- Embeddings implemented for the documented subset.

Do not claim unsupported embedding features.

## 29. Migration

Expected: **no migration**.

Head remains `0017`.

Do not modify migrations 0001-0017.

If a new plaintext operation column appears necessary, do not add it casually: first prove why the
encrypted payload discriminator cannot satisfy correctness/recovery/observability requirements.

## 30. Local regression gate

Run:
- backend full suite; baseline **613**;
- frontend unit/component; baseline **87**;
- full Playwright; baseline **32**;
- npm build;
- npm lint;
- `npx tsc -b --noEmit`;
- OpenAPI/client drift under locked dependencies;
- ruff check src tests;
- git diff --check;
- secret/token/content/vector canary scan;
- migration empty -> 0017;
- populated 0016 -> 0017;
- config_check;
- fake Ollama Chat/Responses/Embeddings provider-contract tests;
- official Python SDK:
  - Models;
  - Chat non-stream/stream;
  - Responses non-stream/stream;
  - Embeddings single/multi;
- official JS SDK equivalent smoke.

Every red test must be root-caused.

## 31. Remote CI gate

Push completed work to origin/v2.

On the FINAL task SHA require:
- backend = completed/success;
- frontend = completed/success;
- schema-secret-gate = completed/success;
- alpha-golden-path = completed/success.

If any run is red:
- inspect logs;
- fix the root cause;
- push;
- require all four green on the new final SHA.

Do not declare completion from local evidence alone.

## 32. No New Scope

Do not implement:
- v1 -> v2 migration;
- Responses tools/MCP/computer use;
- multimodal generation;
- vector database/storage/search;
- RAG;
- batch embeddings API;
- async/background jobs;
- arbitrary provider additions;
- unrelated web redesign.

## 33. Handoff

Record:
- implementation commits;
- pinned OpenAI SDK/reference behavior used;
- supported embedding input/encoding/dimension subset;
- explicit unsupported semantics;
- capability enforcement;
- encrypted operation payload format + legacy Chat compatibility;
- provider adapter behavior;
- fake Ollama endpoint behavior;
- scheduler/quota/budget mapping;
- request/token accounting proof;
- usage/ledger proof;
- cancellation/revocation/ambiguity proof;
- observability behavior including no TTFT sample;
- Python SDK version/results;
- JS SDK version/results;
- live Ollama embedding model/proof, or truthful no-installed-model limitation;
- OpenAPI drift;
- migration decision;
- backend/frontend/Playwright counts;
- final remote SHA + four workflow run IDs/conclusions;
- known limitations;
- WIP lifecycle;
- exactly one recommended next step.

Never include embedding input text, vector contents, credentials/session/OIDC/provider secrets,
encrypted payload/result bytes, raw provider response bodies, or large logs.

## Commit and Push

Suggested primary commit:

`feat(api): add durable embeddings endpoint`

Additional scoped scheduler/adapter/test/docs commits are fine.

Push all completed commits to origin/v2.
Never push directly to main.
Do not ask whether to continue/commit/push.

The task is complete only when:
- official Python + JS SDK Embeddings proofs are green;
- embeddings use the existing durable scheduler/accounting authority;
- capability checks prevent accidental text-route use;
- quota/budget/accounting semantics are correct with output_units=0;
- Chat + Responses remain green;
- migration head remains 0017 unless narrowly justified otherwise;
- local regressions are green;
- all four remote workflows are green on the final origin/v2 SHA;
- handoff is updated;
- local `.aethergate-wip` is removed only after final remote verification.
