# AetherGate v2 — Current Task

## Task ID
AGV2-004

## Title
First real OpenAI-compatible inference through AetherGate v2

## Ownership
Primary: provider adapters + OpenAI API surface  
Coordinating: contracts, catalog/routing, platform/testing

## Before You Start

1. Work on branch `v2`.
2. Run `git pull --ff-only origin v2`.
3. Read:
   - `AGENTS.md`
   - `project_spec.md`
   - `docs/development/agent-handoff.md`
   - `docs/development/current-task.md`
   - `docs/contracts/openai-compatibility-baseline.md`
   - `docs/contracts/domain-model.md`
   - `docs/architecture/provider-model.md`
   - `docs/architecture/security.md`
   - `docs/architecture/scheduler.md`
4. Verify the current official LiteLLM SDK documentation before relying on retry, streaming, provider
   naming, or custom-api-base behavior.
5. Do not commit unrelated local/untracked support files.
6. Do not modify or delete the legacy v1 `app/` or `frontend/src/` implementation.

## Goal

Send real inference through the containerized v2 gateway on nomnom.

At task completion, the following path must work with an existing real inference backend:

```text
official OpenAI client
    -> AetherGate v2 container
    -> public ModelAlias
    -> RouteBinding
    -> Endpoint / ProviderAccount / Provider
    -> LiteLLM provider adapter
    -> existing nomnom inference backend
    -> AetherGate
    -> client
```

Both non-streaming and streaming Chat Completions must succeed.

This is intentionally a **direct-dispatch milestone**. It does not implement the durable scheduler,
queueing, quota reservations, retries, or fallback policy.

## Important: Do Not Guess Ports or Backend Details

The nomnom host is the development test bed.

Before configuring a real backend:

1. inspect current listeners;
2. inspect currently running Docker/Podman containers;
3. inspect existing local developer diagnostic files as read-only hints if useful
   (`ollama_direct.py`, `diagnose.py`, `inspect_routing.py`, etc.);
4. probe candidate existing local inference services safely;
5. determine an actual reachable backend and model;
6. determine how the AetherGate container can reach it.

Do not assume Ollama, llama.cpp, a particular port, a particular model, or a particular container name.

Do not start, stop, reconfigure, or replace an existing inference service just to make the test pass.

Do not commit discovered host ports, credentials, or nomnom-specific backend addresses into normal
application configuration.

The AetherGate API itself must continue using the dynamic loopback host-port behavior from AGV2-003.

## Fix the Provider-Model Contract Gap

AGV2-003 exposed a concrete missing concept: a `RouteBinding` identifies the public alias and endpoint,
but does not identify the provider-facing model/deployment name that must actually be invoked.

Fix this explicitly.

Preferred direction:

- add a provider-facing/upstream model identifier to `RouteBinding` (for example
  `upstream_model`);
- keep it separate from the public `ModelAlias.name`;
- treat the value as provider-specific opaque configuration;
- never derive it implicitly from the public alias unless an administrator explicitly configured the
  same string.

Update:

- domain contract;
- admin v1 DTO foundation;
- persistence model/repository;
- catalog resolution result;
- docs;
- tests.

Do **not** rewrite migration `0001`, because it has already been applied on nomnom.

Add migration `0002` for the new persisted field.

If safe NOT NULL migration cannot be done without inventing a value for possible existing rows, add
the column compatibly and make the application reject unresolved routes until the value is
configured. Document the later tightening migration.

Do not introduce a larger ProviderModel entity unless the concrete implementation proves it is
necessary.

## Provider Adapter Boundary

Create a provider-adapter module under `src/aethergate/` with a narrow internal interface.

Implement the first adapter using the LiteLLM Python SDK.

Requirements:

- pin/range LiteLLM deliberately in `pyproject.toml`;
- call a single explicitly resolved route;
- AetherGate chooses the endpoint, account, provider and upstream model before entering the adapter;
- do not use LiteLLM Router for AetherGate routing;
- do not enable LiteLLM fallbacks;
- disable LiteLLM/provider retry loops for this milestone so one admitted AetherGate execution maps
  to one upstream attempt;
- use explicit timeouts;
- support non-streaming and streaming Chat Completions;
- support the actual provider kind discovered on nomnom;
- structure provider-kind translation so additional providers can be added without changing API
  routers;
- resolve provider credential material only through `SecretResolver`;
- never log or return provider secret material;
- never let inference clients supply `api_base`, provider credentials, arbitrary headers, or an
  upstream destination.

LiteLLM currently supports async completions and streaming, and its own Router can perform retry and
fallback behavior. AetherGate must not delegate scheduler/retry ownership to that Router.

## Egress / Destination Guard

The upstream destination comes only from administrator-controlled persisted configuration, never
from the inference request.

Add a minimal explicit destination policy before provider dispatch.

Requirements:

- only supported schemes;
- reject URL userinfo;
- reject known metadata/link-local destinations;
- use an explicit configured allowlist for upstream hosts in this milestone;
- private/LAN targets may be explicitly allowlisted for nomnom development;
- redirects must not escape the approved destination policy;
- do not create a permissive "allow all" production default.

The exact enterprise egress implementation can be strengthened later, but the first real inference
path must not establish an arbitrary SSRF primitive.

## OpenAI-Compatible API Surface

Implement:

- `GET /v1/models`
- `GET /v1/models/{model}`
- `POST /v1/chat/completions`

### Models

- publish active public model aliases, not provider-facing model names;
- do not expose endpoint/provider secrets or internal routing data;
- unknown model retrieval returns an OpenAI-compatible structured error.

### Chat Completions

Support enough of the pinned compatibility contract to work with the current official OpenAI Python
SDK and the real nomnom backend.

Requirements:

- public `model` resolves through `aethergate.catalog`;
- unknown alias: explicit model-not-found response;
- inactive/unavailable route: explicit safe error;
- ambiguous route: explicit safe error;
- no unknown-model fallback;
- forward supported semantic generation parameters instead of silently dropping them;
- client transport/provider-control fields are rejected and never forwarded;
- unsupported semantic features must fail explicitly rather than being silently ignored;
- keep LiteLLM parameter dropping disabled unless a provider-specific, documented compatibility
  adapter explicitly handles the difference;
- JSON responses are actual JSON objects;
- response `model` should preserve the public AetherGate model alias rather than leaking an internal
  provider/deployment name;
- generate a gateway request ID and return it consistently;
- preserve upstream request ID separately when available;
- map provider failures to structured safe errors without credentials or internal URLs.

Do not implement `/v1/responses` or embeddings in this task. They remain part of the v2 target but
come after this first real inference milestone.

## Streaming

Implement proper SSE for `stream=true`.

Requirements:

- valid `data: <json>\n\n` events;
- correct terminating behavior for Chat Completions;
- no custom queue-status events;
- public alias in emitted model fields;
- client disconnect/cancellation closes the upstream stream promptly;
- no retry after content has begun streaming;
- upstream error after headers/stream start is handled without inventing a new HTTP status.

Test with the official OpenAI Python client, not curl alone.

## Temporary Development Authentication Policy

Do not invent the final identity/auth design inside this task.

For this first nomnom smoke test, inference may be unauthenticated **only** under an explicit
development-only setting and only while the Compose API remains loopback-bound.

Requirements:

- the bypass must be explicit, not accidental;
- it must be impossible to enable the bypass in production mode;
- production startup/config validation must reject an insecure inference-auth bypass;
- document this as temporary;
- do not treat it as the final API-key implementation.

If the existing contracts make it straightforward to add correct scoped API credential
authentication without expanding this task substantially, that is acceptable, but do not design an
entire identity system here.

## Development Seed / Test Configuration

Because the admin API is not implemented yet, provide a clearly development-only, idempotent way to
seed the minimum inference configuration through the service/repository layer.

It must accept values via environment/arguments rather than hard-code nomnom specifics:

- provider kind;
- provider-facing model identifier;
- public model alias;
- endpoint/base destination;
- optional secret-reference/environment variable name;
- allowed upstream host.

It may manipulate the v2 database only as explicitly documented development/bootstrap tooling.
Normal production administration will later go through `/admin/v1`.

Do not commit actual provider keys.

## Real Nomnom Smoke Test — Required

The task is not complete merely because mocked tests pass.

After implementation:

1. inspect current nomnom listener/container state;
2. identify an existing real inference backend and model;
3. start AetherGate v2 using `scripts/dev/v2 up`;
4. obtain the dynamically allocated AetherGate host port using the existing helper;
5. run migrations through `0002`;
6. seed the discovered backend/model through the dev seed path;
7. verify `GET /v1/models`;
8. use the current official OpenAI Python SDK against the printed AetherGate base URL;
9. complete one real non-streaming Chat Completion;
10. complete one real streaming Chat Completion and consume it to completion;
11. verify the public alias, not the internal upstream model name, is exposed to the client;
12. verify an unknown alias does not hit the backend;
13. verify a client cannot override the upstream destination/credential;
14. stop the AetherGate v2 stack cleanly when verification is finished.

Record the discovered backend type/model and the AetherGate dynamically allocated test port in the
handoff if non-sensitive. Do not record credentials.

If no existing reachable inference backend can be found, do not guess or create one. Implement and
verify everything else, then mark the task BLOCKED in the handoff with the exact non-secret
discovery evidence. Do not falsely claim the inference milestone succeeded.

## Automated Tests

Add deterministic tests for at least:

1. route binding requires/handles the upstream model identifier correctly;
2. migration `0002`;
3. model listing exposes only public active aliases;
4. model retrieval;
5. unknown model;
6. inactive route/resource;
7. ambiguous route;
8. provider adapter receives resolved endpoint/model rather than client-controlled destination;
9. LiteLLM retries/fallback are disabled by AetherGate adapter configuration;
10. client transport override fields are rejected;
11. non-streaming response shape;
12. streaming SSE shape and termination;
13. public alias replacement in responses/chunks;
14. provider error mapping/redaction;
15. destination allowlist/metadata-address rejection;
16. development auth bypass cannot be enabled in production;
17. client cancellation closes an upstream stream;
18. official OpenAI Python SDK interoperability against an in-process/mock upstream.

Keep tests offline except the separately documented required nomnom smoke test.

## Documentation

Update/create as needed:

- `docs/architecture/provider-model.md`
- `docs/contracts/domain-model.md`
- `docs/contracts/openai-compatibility-baseline.md`
- `docs/development/README.md`

Clearly state that this milestone directly dispatches and **does not yet queue**.

Do not modify the dated audit.

## Out of Scope

Do not implement:

- durable scheduler/queue;
- quota reservation;
- concurrency slots;
- retries/fallback orchestration;
- accounting settlement;
- full OIDC/session system;
- admin CRUD API;
- web console changes;
- `/v1/responses`;
- embeddings;
- v1 SQLite migration.

## Verification

Before committing:

- full unit/contract test suite;
- containerized tests;
- ruff/lint checks;
- `git diff --check`;
- repository secret scan;
- confirm `app/` and `frontend/src/` untouched;
- confirm audit unchanged;
- complete the real nomnom non-stream + stream smoke test if a backend is available.

## Handoff

Update `docs/development/agent-handoff.md` with:

- branch and starting commit;
- implementation commit(s);
- provider adapter boundary;
- upstream-model contract/migration result;
- discovered non-sensitive nomnom backend type/model;
- dynamically allocated AetherGate host port;
- real non-streaming inference result;
- real streaming inference result;
- official OpenAI SDK test result;
- verification commands/results;
- decisions/deferred items;
- risks/issues;
- exactly one recommended next step.

Do not include credentials, secret values, or large logs.

## Commit and Push

Commit all work on branch `v2` using conventional commits.

Suggested primary message:

`feat(inference): add first v2 chat completion path`

A separate handoff-only follow-up commit is allowed.

**Push all completed commits to `origin/v2`.**

Never push directly to `main`.

The task is complete only when `origin/v2` contains the work and updated handoff. A successful
real nomnom inference smoke test is required unless the handoff explicitly records a genuine
environmental blocker.
