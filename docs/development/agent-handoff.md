# AetherGate Agent Handoff

## Current State
- Branch: v2
- Starting commit: 4ce9e8d (pulled latest origin/v2)
- Implementation commit: 687232b

## Task Completed
AGV2-004 — first real OpenAI-compatible inference through AetherGate v2. Added the direct-dispatch
inference path: a provider-adapter boundary backed by the LiteLLM SDK (retries/fallback disabled),
an explicit egress/destination guard, OpenAI-compatible `GET /v1/models`, `GET /v1/models/{model}`,
and `POST /v1/chat/completions` (non-streaming + SSE streaming), plus a development-only seed tool.

Real inference through an official OpenAI SDK client -> AetherGate v2 -> public alias -> route ->
endpoint/account/provider -> LiteLLM adapter -> nomnom Ollama backend was verified for both
non-streaming and streaming.

## Provider adapter boundary
- `src/aethergate/adapters/` — `ChatAdapter` protocol (`complete`/`stream`), `ChatRequest`/
  `CompletionResult`/`StreamChunk`/`GenerationParams` data structures, `LiteLLMChatAdapter`, and
  `registry.adapter_for(kind)`.
- AetherGate resolves the endpoint/account/provider/upstream model before entering the adapter;
  the adapter receives `base_destination` and `upstream_model` from resolved config only (never from
  the client). LiteLLM Router is not used; `num_retries=0`, `max_retries=0`, `drop_params=False`.
- Provider-kind translation (`registry.adapter_for`) is isolated so new providers do not change API
  routers. Secrets are resolved only via `SecretResolver` (`EnvSecretResolver`, dev/test only).

## Upstream-model contract / migration
- `RouteBinding` gained `upstream_model` (domain entity, admin DTO create/read/update, persistence
  model/repository, catalog `ResolvedRoute`). Migration `0002` adds the nullable column compatibly;
  `0001` was not rewritten.
- `catalog.resolve_model_alias` now raises `RouteUnresolved` when a single active route has no
  `upstream_model`; a later tightening migration (NOT NULL) is deferred until existing rows are
  backfilled.

## Egress / destination guard
- `src/aethergate/egress.py` — `DestinationPolicy` validates the resolved destination before
  dispatch: http/https only, rejects URL userinfo, rejects metadata/link-local/loopback/reserved
  hosts, and requires the host to be explicitly allowlisted via `AETHERGATE_UPSTREAM_ALLOWLIST`
  (comma-separated; empty = deny all). Redirect-escape hardening remains deferred.

## OpenAI-compatible API surface
- `GET /v1/models` lists active public aliases only; `GET /v1/models/{model}` returns a single alias
  or an OpenAI-compatible `404 model_not_found`.
- `POST /v1/chat/completions` supports non-streaming and SSE `stream=true`; `data: [DONE]` terminates
  streams; the gateway request ID is the response `id`; the public alias is returned as `model`; the
  upstream request ID is preserved separately (`X-Upstream-Request-Id` header, non-stream).
- Transport/provider-control fields (`api_base`, `base_url`, `api_key`, `headers`, `upstream_model`,
  etc.) are rejected; unsupported semantic features (multimodal content, tool roles, `n != 1`) are
  rejected explicitly; unknown additive fields are ignored.
- Errors use the OpenAI `{"error": {...}}` envelope with safe messages (no credentials/URLs).

## Temporary development auth policy
- Inference is unauthenticated only when `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=true` AND
  `app_env != prod` (config validator rejects prod+bypass). Default is fail-closed (`401`). This is
  temporary, not the final API-key design.

## Development seed
- `python -m aethergate.devseed --kind ... --upstream-model ... --alias ... --base-destination ...`
  (optional `--secret-ref-name`). Idempotent (name-based lookups), validates the destination against
  the allowlist. Values also accepted via `AETHERGATE_SEED_*` env vars. Development/bootstrap tooling
  only; normal administration will go through `/admin/v1`.

## Nomnom verification (actual commands/results)
- Host = nomnom (`192.168.22.50/24`); `docker` = podman 4.9.3 + docker-compose 2.40.3.
- Discovered backend: **local Ollama** listening on all interfaces `*:11434`; model
  **`qwen3.8-2b-distill:Q6_K`** (~5s, returns reasoning content before its final answer). The
  container reaches it via the host LAN IP `http://192.168.22.50:11434`.
- `deploy/v2/.env` (gitignored) got `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=true` and
  `AETHERGATE_UPSTREAM_ALLOWLIST=192.168.22.50`.
- `scripts/dev/v2 build` -> `aethergate-v2:local` (now includes `litellm>=1.100,<2` and `openai>=2,<3`).
- `scripts/dev/v2 up` -> dynamically allocated port **33585** (fresh per `up`).
- `scripts/dev/v2 migrate` -> applied `0002` (add `route_bindings.upstream_model`).
- Seeded via devseed: kind=ollama, upstream=`qwen3.8-2b-distill:Q6_K`, alias=`gpt-4`,
  destination=`http://192.168.22.50:11434`.
- `GET /v1/models` -> `{"object":"list","data":[{"id":"gpt-4",...}]}`; `GET /v1/models/gpt-4` -> 200;
  unknown model -> 404.
- Official OpenAI SDK (openai 2.54.0) non-stream -> success (`model=gpt-4`, `finish_reason` + usage
  returned; content "Ping").
- Official OpenAI SDK stream -> success (`model=gpt-4`, `finish_reason=stop`, content reassembled).
- Unknown alias -> 404 (no backend dispatch). `api_base` override -> 400
  `invalid_request` (rejected, never forwarded).
- `scripts/dev/v2 test` -> **88 passed** (full suite incl. DB-gated + migration-from-empty + compose).
- `scripts/dev/v2 down` -> clean stop.

## Decisions
- LiteLLM pinned `litellm>=1.100,<2`; `openai` dev pin is `>=2,<3` (litellm 1.104 requires
  openai>=2.20, so `openai>=1` conflicted and was widened).
- `upstream_model` stored on `RouteBinding` (no new ProviderModel entity) per the task's preference;
  nullable + `RouteUnresolved` rejection (NOT NULL tightening deferred).
- Request IDs: a per-request gateway id is generated in `api.deps.get_gateway_request_id` (no
  middleware, to avoid breaking SSE streaming) and used across success/error/stream responses.
- DTOs: `extra="ignore"` for additive tolerance, with explicit transport-control fields declared and
  rejected; `extra="forbid"` on chat messages to reject multimodal/tool payloads.

## Deferred
- Durable scheduler/queue, quota reservation, concurrency slots, retries/fallback, accounting
  settlement, full identity/auth, admin CRUD API, `/v1/responses`, embeddings, v1 SQLite migration.
- Redirect-escape hardening in the egress guard; `upstream_model` NOT NULL tightening migration.
- Production secret backend (Vault/KMS/envelope encryption); real production egress allowlist policy.

## Issues / Risks
- `docker` on nomnom is podman; keep compose healthchecks single-token (`python -m
  aethergate.healthcheck`). `docker compose run` needs `--no-deps` to avoid recreating postgres.
- The dynamic API host port changes every `up`; re-run `scripts/dev/v2 url` after `up`.
- `qwen3.8-2b-distill:Q6_K` emits `</think>` reasoning content; this is upstream behavior, not a
  gateway defect (no content filtering is applied in this milestone).
- Pre-commit hook scans staged content for `password=`/`secret=`/`api_key=`/`sk-` patterns; keep
  fixture literals value-neutral (verified clean on commit).

## Recommended Next Step
Implement `/v1/responses` (the preferred modern surface) using the same adapter/egress/catalog path,
and add scoped API-credential authentication to replace the temporary development auth bypass.
