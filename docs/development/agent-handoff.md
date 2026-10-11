# AetherGate Agent Handoff

## Current State
- Branch: `v2`. **AGV2-028 (Vision / multimodal image input for Chat + Responses)** complete:
  image input -> text output is supported on `POST /v1/chat/completions` and `POST /v1/responses`
  through the **existing** durable scheduler/accounting/worker authority. Inline `data:` images
  (PNG/JPEG/WebP) only; remote URLs/`file_id`/files rejected and never fetched; explicit image
  count/byte/payload limits; images stay inside the encrypted scheduler payload; image capability
  required on alias + provider with dispatch-time revalidation; token quotas/budgets fail closed.
  No migration (head remains `0017`). Follows the verified AGV2-027 main-merge baseline.
- **Migration head remains `0017`**; no new migration; `0001`–`0017` untouched.

## AGV2-028 — Vision / multimodal image input (Chat Completions + Responses)

### Pinned contract / SDK baseline
- Request/response shapes derived from the locked official **`openai==2.54.0`** Python SDK
  (`ChatCompletionContentPartParam` = `text`/`image_url`; `ResponseInputImageParam` =
  `input_image` with `image_url`/`file_id`) and the pinned upstream OpenAPI baseline; never from
  memory. JS smoke uses the locked official **`openai` Node SDK `7.32.0`**.
- Provider transport shape discovered **empirically** against pinned LiteLLM `1.104.0`: a chat
  content list `[{type:text,text}, {type:image_url,image_url:{url:"data:<mime>;base64,..."}}]`
  is transformed by `litellm/llms/ollama/...` into a POST to **`/api/generate`** with top-level
  `images: ["<pure base64>"]` (and `/api/chat` message `images`). It strips the `data:` prefix.

### Supported surface / source form
- Chat Completions: `messages[].content` = string **or** ordered `text`/`image_url` parts.
- Responses: `input[].content` = `input_text`/`output_text`/`input_image` parts.
- Inline `data:` URLs only; media types **PNG/JPEG/WebP** (strict base64 + magic-byte signature).
- Non-stream + stream on both surfaces; text-only forms unchanged.

### Explicitly rejected (structured 400)
Remote `http(s)://` image URLs (**never fetched / no DNS+HTTP client**), `file_id`/files,
provider-local paths, arbitrary URI schemes, SVG/unsupported media types, malformed data URLs,
bad/non-canonical base64, empty payloads, MIME/signature mismatch.

### Canonical multimodal model + encryption
- `adapters.base.Message(role, content:str, parts: tuple[ContentPart,...] | None)`;
  `TextContentPart`/`ImageContentPart(media_type, data:bytes)`. Part order preserved.
- Chat and Responses map into the **same** canonical messages; adapters receive canonical parts.
- `serialize_chat_payload` emits a `parts` key only for multimodal messages; image bytes are
  base64 within the JSON envelope, which is then Fernet-encrypted into `payload_encrypted`.
  Legacy text payloads (no `parts`) deserialize byte-for-byte as before
  (`tests/test_vision_images.py::test_legacy_text_payload_deserializes_exactly_as_before`).
- No plaintext image column `migration`; **no migration** (head `0017`).

### Configurable limits (pre-enqueue)
`AETHERGATE_MAX_IMAGES_PER_REQUEST=4`, `AETHERGATE_MAX_IMAGE_BYTES=8 MiB`,
`AETHERGATE_MAX_TOTAL_IMAGE_BYTES=16 MiB`, `AETHERGATE_MAX_QUEUE_PAYLOAD_BYTES=24 MiB`. The
payload bound applies to text and vision payloads and accounts for base64 expansion. Oversize ->
`400 image_limit_exceeded` / `400 payload_too_large`, checked in-app (not only nginx).

### Capability enforcement
Image request requires `Capability.IMAGE` on the public **ModelAlias** and the **Provider**
(`scheduler/service._enforce_image_capability`), at admission **and** again at dispatch time
(capability may be revoked while queued). No fallback to a text-only route; images never silently
removed. Text-only requests are unaffected.

### Adapter / provider mapping
Pinned `LiteLLMChatAdapter._wire_message` emits the OpenAI `image_url` content union; provider kind
stays `ollama`; base destination/upstream model remain authoritative; retries/fallback stay
disabled; image bytes are never logged. Fake Ollama records only `last_image_count` /
`last_image_byte_lengths` / `image_request_count` (decoded lengths) — never bytes/base64.

### Quota / budget / accounting
Request quotas unchanged (1 request = 1 unit). Image requests return `estimate_input_tokens=None`,
so enforced **token** quota / token-priced budget fail closed
(`quota_token_estimator_unavailable` / `budget_token_estimator_unavailable`) — no fabricated image
tokens. Request-priced accounting, UsageRecord, signed `usage_debit`, leases/fencing, cancellation,
`outcome_unknown`, streaming, TTFT, auth + dispatch-time credential revalidation all unchanged.

### Fake-Ollama + offline golden path proof
- `tests/test_fake_ollama.py` / `tests/test_adapter.py`: real pinned LiteLLM transport reaches the
  image path; metadata-only capture; wire-mapping assertion.
- `scripts/ci/golden_path.py` adds a distinct image-capable `golden-vision-model` alias/route and
  proves: text Chat + Responses still green; Chat/Responses vision non-stream/stream; Python SDK
  vision; remote-URL + text-only-alias 400; **backup/restore preserves the vision route**;
  **post-restore vision inference** succeeds. `sdk/js/smoke.mjs` adds JS Chat/Responses vision
  non-stream/stream + remote-URL 400. Full offline golden path run green locally on a disposable
  stack (`fake-ollama`): main phase `sdk_chat_vision/…_stream`, `sdk_responses_vision/…_stream`,
  `sdk_vision_remote_url_400`, `sdk_text_only_image_400` all `ok`; post-restore vision `ok`;
  JS smoke all `ok`.

### Live nomnom proof (real internal Ollama, bypass=false)
- Installed vision model positively identified from `/api/tags` capabilities: **`qwen3-vl:latest`**
  (`capabilities: [completion, vision, tools, thinking]`). Many more vision models installed; none
  downloaded.
- Disposable image-capable provider/account/endpoint(`max_concurrency=2`)/alias/route; official
  Python SDK: Chat vision non-stream **ok**, Chat vision stream **ok** (TTFT ≈ 1.0 s), Responses
  vision non-stream/stream **ok**, remote URL → **400**, text-only alias image → **400**;
  six concurrent vision requests / two slots all succeeded (wall times 1.05–6.32 s).
- Full harness: streaming image request settled **succeeded** with exactly **1 UsageRecord** and
  **1 signed ledger entry** and **first_token_at recorded** (TTFT); route disabled while queued →
  **failed** (no dispatch/fallback); queued credential revocation → **failed / authorization_failed**
  (no upstream dispatch). Real model rejects a 1x1 PNG but accepts a small decodable PNG; fixtures
  use a tiny deterministic 16x16 solid PNG for live parity.

### OpenAPI drift / migration
- Regenerated backend OpenAPI + frontend `schema.d.ts` from the contract change; two consecutive
  generations md5-stable.
- **No migration**; head remains `0017`; `0001`–`0017` untouched. Empty → `0017` and populated
  `0016 → 0017` verified; `config_check` VALID.

### Regression counts (AGV2-028)
- backend: **657 collected, exit 0** (baseline 613 + 44 new/converted vision tests).
- frontend unit/component: **89 passed** (baseline 87 + 2 image-capability component tests).
- full Playwright: **32 passed, 5 skipped** (CI-only `alpha-golden.spec.ts`, now including vision).
- `npm run build`/`npm run lint`/`npx tsc -b --noEmit` clean; `ruff check src tests` clean;
  `git diff --check` clean; secret/content/image-data scans clean; no-legacy guard + self-test clean.

### Final remote CI (AGV2-028)
All four required workflows are `completed/success` on `origin/v2`:
- final v2 SHA `8d67f95af8a935b745905f76e5ffedbc0d65b652` (handoff commit; docs-only):
  push-event runs `backend 38175608201`, `frontend 38175608114`, `schema-secret-gate
  38175608306`, `alpha-golden-path 38175608163`; pull-request-event runs
  `backend 38175613843`, `frontend 38175613806`, `schema-secret-gate 38175613790`,
  `alpha-golden-path 38175613763` (this PR-event run initially hit the documented
  transient OIDC/JWKS browser cold-start flake at `loginAs` in a non-vision test;
  `gh run rerun --failed` then passed — the push-event run on the identical SHA was
  green throughout).
- substantive feature SHA `7bd9fe5ba25611da0e5e5bf0f5fbaa83f36dcf7f`: push-event runs
  `backend 38175168398`, `frontend 38175168385`, `schema-secret-gate 38175168307`,
  `alpha-golden-path 38175168359` (all green).

### Known limitations
- Inline-only; no remote fetch, files/`file_id`, object storage, image generation, audio, video,
  tools/MCP/computer-use. Not full multimodal/OpenAI compatibility.
- Image token estimation is unavailable by design; enforced token quotas/budgets fail closed.
- `deploy/v2`/`scripts/*/v2` retain historical `v2` naming (canonical; do not rename).

### Feature PR
- PR **#2** (`v2` -> `main`): https://github.com/draeician/aethergate/pull/2 — created, includes
  the final SHA and verification summary, **not merged**. (PR #1, the AGV2-027 cutover, was
  already merged into `main` before this task; this is a new feature PR.)

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-028`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, removed only after all criteria were green, the handoff committed, commits pushed to
`origin/v2`, final remote CI verified green, and PR #2 created. Never staged/committed/pushed
(gitignored).

### Recommended next step
Resume **embeddings** (`POST /v1/embeddings`) through the same durable scheduler/accounting
authority (deferred, not cancelled); then proceed to the reviewed merge of PR #2 (`v2` -> `main`).

## Prior — AGV2-027 (canonical v2 cutover — retire v1)

- AGV2-027 complete: the current PostgreSQL/Alembic/scheduler implementation under
  `src/aethergate/` is the only supported AetherGate runtime. The retired v1 SQLModel/SQLite
  application and its stale operator/agent instructions are removed, a legacy-reintroduction CI
  guard is wired in, all four workflows push on `v2` + `main`, and a fresh clean-install cutover
  is proven end-to-end on a disposable stack (real Ollama) with no v1 data imported.
- Follows the verified AGV2-025 baseline SHA `07ae618` (backend 613 / frontend 87 / Playwright 32).
- **Migration head remains `0017`**; no new migration; `0001`–`0017` untouched.

## AGV2-027 — Canonical v2 cutover (retire legacy v1)

### Operator decisions
- **v1 data is disposable.** The v1 deployment was never production and holds no state worth
  preserving. The supported transition is a clean/destructive cutover to a fresh PostgreSQL
  authority. **No SQLite -> PostgreSQL importer is built** and none should be resurrected.
- **AGV2-026 Embeddings was superseded before implementation** (queued, never started). It is
  deferred, not cancelled. Per operator direction, **vision/multimodal inference is next** after
  cutover; embeddings may be resumed later.

### Legacy artifacts removed (exact)
- Runtime/code: `app/` (auth, database, main, models, routers/{admin,proxy}, services/{billing,
  limiter}), root `Dockerfile`, root `docker-compose.yml`, root `requirements.txt`, `init_db.py`,
  `manage.py`, `debug_inference.py`.
- Ad-hoc SQLite migration scripts: `scripts/add_model_config_columns.py`,
  `scripts/add_rate_limit_column.py`, `scripts/migrate_endpoints.py`.
- Stale agent instructions: `AETHERGATE_RULES.md`, `.context-resume.md`; rewrote `.cursorrules`
  and `CLAUDE.md` to point at `AGENTS.md`/`project_spec.md` only.
- Stale operator docs: `API_REFERENCE.md` (now a pointer to generated OpenAPI/current contracts)
  and `OPS_RUNBOOK.md` (now a pointer to `docs/operations/alpha-runbook.md`).
- Untracked v1-era helper scripts at the root (`aethergate_client.py`, `chat_completion.py`,
  `diagnose.py`, `inspect_routing.py`, `list_models.py`, `ollama_direct.py`, `prompt1.md`) were
  removed; `docs/development/agent-workstreams.md` now documents their supported v2 replacements.
- **Not touched:** `docs/audits/v2-architecture-audit-2026-10-03.md` (immutable historical
  evidence) and migrations `0001`–`0017`.

### Canonical paths preserved
`src/aethergate/`, `deploy/v2/`, `scripts/dev/v2`, `scripts/ops/v2` keep their historical `v2`
path component and are the canonical current paths. No root Docker/Compose aliases were added.
Tooling audit found no remaining assumptions on a root Dockerfile/Compose (all use `deploy/v2/`).

### Dependency cleanup decisions
None of `sqlmodel`, `aiosqlite`, `passlib`/`bcrypt`, `typer`, or `numpy` are in the current
`pyproject.toml` or `constraints.txt` — they were v1-only and were already absent from the locked
graph. No dependency churn; `constraints.txt` unchanged and OpenAPI drift is zero under the lock.

### No-legacy CI guard
`scripts/ci/no_legacy_v1.sh` fails if the active tree reintroduces `app/`, root legacy
`docker-compose.yml`/`requirements.txt`, retired SQLite scripts, active `app.*` imports, or active
SQLModel/aiosqlite/`MASTER_API_KEY`/`x-admin-key` references (code/config only). It exempts the
immutable audit and the migration-retirement doc, and `--self-test` proves it rejects a synthetic
legacy tree. Wired into `schema-secret-gate`. Local run: clean + self-test ok.

### Clean-install cutover proof (fresh disposable stack, real Ollama `tev1:0.8b`, bypass=false)
`scripts/ci/nomnom_acceptance.sh` against a disposable compose project/volume (never the dev DB):
- no v1 DB imported; fresh PostgreSQL; Alembic empty -> head (**0017**); `config_check` VALID;
- API/worker readiness; one-use bootstrap; deterministic OIDC browser login;
- catalog chain created (provider/account/endpoint/alias/route/price-policy, project/principal);
- inference credential created; official Python SDK Models (`golden-model`) + Chat non-stream +
  Chat stream + Responses non-stream + Responses stream all green;
- usage/ledger state present (4 -> 4 across restore, no duplicate settlement); observability
  summary populated; web console root + `/auth/callback` load (HTTP 200);
- backup succeeds; destroy + restore; API restart, worker restart, full-stack restart all recover;
  post-restart SDK inference succeeds.
- Official JS SDK smoke green (Models + Chat non-stream/stream + Responses non-stream/stream +
  Responses tools 400). Disposable stack + volume torn down afterward.

### Legacy negative proof
`app/` is absent (old `uvicorn app.main:app` cannot start), root `Dockerfile`/`docker-compose.yml`
absent, `init_db.py`/`manage.py` absent, old SQLite scripts absent, no active `app.*`/SQLModel
imports in the build path, and the frontend does not target old `/admin/*` or `x-admin-key`.

### Nomnom v1 retirement
No obsolete AetherGate v1 containers/processes were running. One positively-identified v1 image
(`aethergate-api:latest`, Cmd `uvicorn app.main:app`) with **no referencing containers** was
removed. Unrelated containers/services (langfuse, matrix-comms, paperclip, etc.) were left
untouched. The old SQLite DB was not imported. The disposable rehearsal stack was the only thing
torn down.

### Migration head
`0017` (unchanged; `0001`–`0017` untouched).

### Regression counts (AGV2-027)
- backend: **613 passed / 613 collected** (canonical `scripts/dev/v2 test`, exit 0).
- frontend unit/component: **87 passed** (22 files).
- full Playwright: **32 passed, 3 skipped** (CI-only `alpha-golden.spec.ts`).
- `npm run build`/`npm run lint`/`npx tsc -b --noEmit` clean; `ruff check src tests` clean;
  `git diff --check` clean; `scripts/ci/secret_scan.sh` clean; `no_legacy_v1.sh --self-test` clean.
- OpenAPI/client drift: two consecutive generations identical (md5 stable); regenerated committed
  OpenAPI/client produced zero diff.

### Final remote CI (AGV2-027)
The substantive cutover SHA is `f2c37446b25e4ac9b3545bb3ed3f2d8a32fa05e1`; all four
required workflows are `completed/success` there:
- backend — run `38157863695`
- frontend — run `38157863677`
- schema-secret-gate — run `38157863609`
- alpha-golden-path — run `38157863692`

Every commit after the substantive cutover is documentation-only and was verified green with the
same four deterministic, offline workflows (`26a1344` runs `38158674053`/`38158674062`/
`38158674024`/`38158674106`; `e8aeef2` runs `38159180593`/`38159180505`/`38159180483`/
`38159180504`; `4aaf3ec` runs `38159638931`/`38159638848`/`38159638893`/`38159638860`). The
`origin/v2` tip at handoff is green for all four.

### Main-branch readiness
- All four workflows now push on `v2` **and** `main`; pull-request + `workflow_dispatch` kept,
  least privilege, concurrency, locked installs, and the offline golden path preserved.
- PR **#1** (`v2` -> `main`) created: https://github.com/draeician/aethergate/pull/1 — **not
  merged** (requires the reviewed merge process).

### Known limitations
- Migration head is `0017`; no schema change in this task.
- `deploy/v2`/`scripts/*/v2` retain historical `v2` naming (canonical; do not rename).
- Embeddings remain unimplemented; vision/multimodal not yet started.
- The no-legacy guard is a deterministic structural/string check, not a full history scanner.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-027`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, removed only after all criteria were green, the handoff committed, commits pushed to
`origin/v2`, and the final remote CI verified green. It was never staged, committed, or pushed
(gitignored).

### Recommended next step
**Merge `v2` into `main` through the reviewed PR process (PR #1)**, or — if an authorized later
action has already merged it — begin **vision/multimodal inference** (embeddings remain deferred).

## Prior — AGV2-025 — OpenAI Responses API (text generation, durable queue, official SDK)

### Pinned contract / SDK baseline
- Request/response/event shapes derived from the locked official **`openai==2.54.0`** Python SDK
  (`openai.types.responses.*`, `openai.resources.responses`) and the pinned upstream OpenAPI
  baseline (`docs/contracts/openai-compatibility-baseline.md`), never from memory.
- JS smoke uses the locked official **`openai` Node SDK `7.32.0`** (`sdk/js/package-lock.json`).

### Supported request fields / input forms
- `model` (always the public `ModelAlias.name`), `input` (string OR array of text message items
  with `input_text`/`output_text` content parts), `instructions`, `max_output_tokens`,
  `temperature`, `top_p`, `stream`.
- `instructions` -> leading highest-precedence canonical `system` message (never dropped);
  `developer` -> canonical `system` (`tests/test_responses_contracts.py`, live proof).
- Unknown purely-additive fields are ignored (documented tolerance policy).

### Explicitly unsupported semantics (HTTP 400 `unsupported_feature`)
`tools`/`tool_choice`/`max_tool_calls`/`parallel_tool_calls`, image/audio/file input, output audio,
structured-output `text.format`, `reasoning`, `previous_response_id`, `conversation`, `background`,
`store=true`, `include`, `top_logprobs`, non-`disabled` `truncation`, `metadata`, `moderation`,
`service_tier`, `prompt`/`prompt_cache_*`, `safety_identifier`, `user`, `context_management`,
`stream_options`, and all provider/endpoint/base-url/header/secret transport overrides. Known
semantic fields are never silently dropped; no client may choose provider/account/endpoint/upstream
model/URL/transport secrets.

### Responses -> scheduler mapping
`ResponsesRequest -> canonical Message[] + GenerationParams -> SchedulingService.admit_and_enqueue
-> existing worker (claim_and_reserve/run_complete/run_stream) -> existing provider adapter ->
existing quotas/budgets/PriceSnapshot/UsageRecord/usage_debit ledger -> Responses-specific
response/SSE translation (API side only)`. No second scheduler, worker, accounting path, or hidden
fallback.

### Streaming event sequence
`response.created` -> `response.in_progress` -> `response.output_item.added` ->
`response.content_part.added` -> `response.output_text.delta`* -> `response.output_text.done` ->
`response.content_part.done` -> `response.output_item.done` -> `response.completed` (final
response + usage). Monotonic `sequence_number`; stable response/output/content IDs and indices; no
proprietary queue events; **no `[DONE]`**. Failed/cancelled/expired/outcome_unknown ends with
`response.failed` and never fakes success.

### Cancellation / auth
Same durable cancellation as Chat (queued/reserved never dispatches; dispatched/streaming flagged
for the owning worker). Same auth as Chat: inference-audience `agk_...`, `inference:invoke`,
project/principal attribution, dispatch-time credential revalidation, `bypass=false` in all proofs.

### Quota/budget/accounting proof
Responses participates in endpoint slots, request/token quotas, cooldown, project budget,
PriceSnapshot, UsageRecord, signed `usage_debit`, exactly-once settlement. Live run: 10 Responses
requests (6 non-stream, 4 stream) -> exactly 10 UsageRecords and 10 usage_debit ledger entries.
`max_output_tokens` feeds the same canonical output reservation bound as Chat.

### Official SDK results
- Python `openai==2.54.0`: Responses non-stream parses a typed `Response` (`output_text`,
  `usage`, `status=completed`); Responses stream parses typed events and reconstructs exact text;
  `response.completed` observed; unsupported tools raise an SDK-visible `BadRequestError` (400);
  `bypass=false` throughout.
- JS `openai@7.32.0` (`sdk/js/smoke.mjs`): Models + Chat non-stream/stream + Responses
  non-stream/stream + Responses tools 400. The web UI never imports the OpenAI SDK.

### Fake-Ollama CI proof
`scripts/ci/golden_path.py` phase main/post-restore now also run Responses non-stream + stream via
the official Python SDK. `alpha-golden-path.yml` adds a locked official JS SDK smoke step. All
offline (fake Ollama, no Internet/model download/provider credentials).

### Live Ollama proof (real internal Ollama, bypass=false)
- Chat non-stream + stream green; Responses non-stream (≈9 KB output) + stream (≈8.9 KB)
  green against `tev1:0.8b`.
- `instructions` request returns non-empty output (not dropped).
- Six concurrent Responses streams under `max_concurrency=1` visibly queue
  (queue-wait p95 ≈ 67 s) and observability reports queue-wait + TTFT samples.
- Deactivate route -> safe failure (no fallback); restore -> succeeds.
- Revoke credential -> never reaches upstream; unsupported tool -> 400 with no
  `inference_requests` row created.

### OpenAPI drift / migration
- OpenAPI + frontend generated types regenerated; two consecutive generations produce zero
  additional diff (md5 stable).
- **No migration**; head remains `0017`; `0001`–`0017` untouched.

### Regression counts (AGV2-025)
- backend: **613 collected** (baseline 561 + 13 `test_responses_contracts.py` + 17
  `test_responses_api.py` + …); canonical `scripts/dev/v2 test` exit 0.
- frontend unit/component: **87 passed**.
- full Playwright: **32 passed, 3 skipped** (CI-only alpha-golden).
- `npm run build`/`npm run lint`/`npx tsc -b --noEmit` clean; `ruff check src tests` clean;
  `git diff --check` clean; `scripts/ci/secret_scan.sh` clean.
- migration empty -> `0017`; populated `0016` -> `0017`; `config_check` valid (exit 0) and invalid
  (nonzero).

### Final remote CI
All four required workflows are `completed/success` on the substantive `origin/v2` SHA
`f59f778dfbca1d1bef4f63b5d915733655f398d0`:
- backend — run `38106324981`
- frontend — run `38106324962`
- schema-secret-gate — run `38106325011`
- alpha-golden-path — run `38106325036`

Every later commit in this task is documentation-only and was verified green with the same four
workflows; the `origin/v2` tip at handoff is green for all four (the gates are deterministic and
offline, so the result is stable). The commit series: `4039e7d` (feat api), `aa7d7ec` (test ci),
`8cad5b4` (docs baseline/readme), then handoff docs commits.

### Known limitations
- Only the Responses text-generation subset is implemented; rich semantics are explicitly
  rejected, not emulated (see `docs/contracts/responses.md`).
- `developer` maps to canonical `system` (the Ollama adapter path cannot distinguish them); the
  highest-precedence instruction slot is preserved rather than dropped.
- No Responses persistence/retrieval, background jobs, or `previous_response_id` (deferred).
- `POST /v1/embeddings` is still unimplemented (next step).

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-025`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, removed only after all criteria green and remote verified; never staged/committed/pushed.

### Recommended next step
Implement the **embeddings** surface (`POST /v1/embeddings`) through the same durable
scheduler/accounting authority, following the AGV2-025 Responses pattern.

## Prior — AGV2-024V (close Alpha 1 remote CI gates and dependency reproducibility)
- Branch: `v2`. **AGV2-024V** is complete: local regressions green, dependency/OpenAPI generation
  reproducible, and all four remote GitHub Actions workflows green on the final `origin/v2` SHA.
  `.aethergate-wip` is removed only after the final remote verification.
- **Migration head remains `0017`**; no new migration; `0001`–`0017` untouched.

## AGV2-024V — Close Alpha 1 remote CI gates and dependency reproducibility

### Why the first remote CI run failed (SHA `fb00f875513b8bec90d5284facf122850768868e`)
All four workflows were red:
- **backend** (run 38085330138): CI set `AETHERGATE_TEST_DATABASE_URL` but never created that database,
  so DB-gated tests failed (`database "aethergate_test" does not exist`); and
  `test_prod_requires_queue_key` was satisfied by the workflow-level `AETHERGATE_QUEUE_KEY`
  (ambient-environment contamination).
- **frontend** (38085330153) + **schema-secret-gate** (38085330056): OpenAPI generation removed the
  Pydantic-generated Decimal `pattern` because CI resolved a newer Pydantic stack than the environment
  that generated the committed OpenAPI. A real reproducibility defect.
- **alpha-golden-path** (38085329988): the "Generate deterministic secrets" step imported
  `cryptography.fernet.Fernet` before dependencies were installed (`ModuleNotFoundError`), so
  `deploy/v2/.env` was never written; teardown then failed on missing `POSTGRES_PASSWORD`
  interpolation.

### Dependency locking strategy
- `pyproject.toml` keeps the human-readable compatible ranges.
- `constraints.txt` (repository root) is a committed pip **constraints** file pinning every
  runtime+dev transitive dependency of the known-green Alpha environment (pydantic `2.13.5`, fastapi
  `0.142.2`, litellm `1.104.0`, …). The block after `# BEGIN LOCKED VERSIONS` is machine-managed.
- Consumed by: `backend.yml`/`frontend.yml`/`schema-secret-gate.yml`/`alpha-golden-path.yml`
  (`pip install -c constraints.txt …`), `deploy/v2/Dockerfile` (`pip install -c constraints.txt
  ".[dev]"`), and the canonical local install. No editable paths, no credentials, no `aethergate`
  entry in the lock.
- Refresh is explicit: `scripts/ci/refresh_lock.sh` rebuilds the pinned block from a clean venv, then
  OpenAPI + frontend types are regenerated and the full gate run is repeated
  (`docs/development/dependency-locking.md`).

### Exact fixes
- **backend.yml**: added a "Create explicit test database" step
  (`tests.db_helpers.ensure_database(AETHERGATE_TEST_DATABASE_URL)`) before pytest; locked install.
  DB-gated tests are not skipped.
- **tests/test_config_check.py**: `test_prod_requires_queue_key` now explicitly
  `monkeypatch.delenv("AETHERGATE_QUEUE_KEY")` and uses `_env_file=None`; an autouse fixture clears all
  relevant settings env vars and chdirs to an empty tmp dir. `test_settings.py`, `test_oidc_session.py`,
  `test_cli_session.py` got the same isolation (`_env_file=None` / clean cwd). The production queue-key
  requirement is unchanged.
- **alpha-golden-path.yml**: secrets generated with the standard library only
  (`base64.urlsafe_b64encode(os.urandom(32))` Fernet key; `secrets` for password/bootstrap; nothing
  printed). Teardown/log-dump guarded on `deploy/v2/.env` existing and non-fatal. **Two latent defects
  the step-1 failure had masked were also fixed**: (1) `OIDC_ISSUER` is now the runner host IP
  (reachable by both the browser and the api container) instead of `127.0.0.1`; (2) the empty database
  is migrated **before** the readiness wait (readiness is `503 schema_not_current` until head).
  Post-restore now restarts api/worker with the full compose file set (so the CI/web overrides survive)
  via `scripts/ops/v2 restore --keep-stopped` + `compose up -d`, and restarts `web` for nginx DNS.
  `AETHERGATE_PYTHON=python` for the Playwright SDK driver. A warm-up step primes OIDC discovery before
  the browser smoke.
- **Second remote run (after the lock + step-1 fixes) — backend**: the created test database now
  exposed two further real defects: `test_admin_auth.py::test_http_admin_key_rejected_on_inference`
  and `test_admin_crud.py::test_credential_audience_separation_via_endpoint` (plus
  `test_cli_session.py::test_cli_session_cannot_authenticate_inference`) reached
  `aethergate.api.deps`'s process-wide session factory — which pointed at the unmigrated
  `aethergate_test` in CI — and failed on `relation "api_credentials" does not exist`. Fixed by
  patching `aethergate.api.deps.get_session_factory` to the per-test database (matching the existing
  `test_openai_auth.py` pattern) so the audience rejection is proven against the test schema.
- **Second remote run — alpha-golden-path**: the browser golden smoke performed live SDK inference
  that appended usage records *after* `phase main` recorded the counts and *before* the backup, so the
  post-restore count assertion saw `2 -> 4`. Fixed by running the browser smoke **after** the
  post-restore count proof.
- **constraints.txt / Dockerfile / workflows**: locked installs (`-c constraints.txt`).
- **Secret-scan fixtures**: reviewed; exclusions remain narrow and explicit
  (`tests/test_logging_safety.py`, `tests/test_diagnostics.py`, `scripts/ci/secret_scan.sh`). All
  fixtures are obviously synthetic (`agk_live_0123456789abcdefABCDEF`, a PEM with literal
  `secretsecretsecret`). The scan is otherwise clean; the git pre-commit hook has no allowlist file, so
  the repository-native narrow allowlist is the CI scanner's explicit file list.

### Final local regression counts
- backend canonical suite (`scripts/dev/v2 test`): **561 collected**, exit 0.
- frontend unit/component: **87 passed** (22 files).
- full Playwright (`WEB_BASE_URL=http://127.0.0.1:8081`): **32 passed**, 3 skipped (CI-only
  `alpha-golden.spec.ts`).
- `npm run build`, `npm run lint`, `npx tsc -b --noEmit` clean.
- OpenAPI/client drift: two consecutive generations under the lock produced **zero diff**.
- `ruff check src tests` clean; `git diff --check` clean; `scripts/ci/secret_scan.sh` clean.
- migration empty -> `0017`; populated `0016` -> `0017` (via `tests/test_migrations.py`, green).
- `python -m aethergate.config_check` valid (exit 0) and invalid (nonzero).
- fake-Ollama adapter tests green; official OpenAI Python SDK non-stream + stream against the live
  stack with bypass `false`. Full CI golden-path simulated locally end-to-end (disposable stack:
  migrate → readiness → bootstrap → OIDC login → catalog → SDK → backup → restore → SDK), all green.

### Final remote CI
The substantive code/test/workflow commit is `b3c61d5e7409bbb0504b33c87a5361bf5105beed`;
all four required workflows `completed/success` there:
- backend — run `38092737427`
- frontend — run `38092737557`
- schema-secret-gate — run `38092737259`
- alpha-golden-path — run `38092737337`

AGV2-024V final substantive `origin/v2` code/test/workflow SHA
`b3c61d5e7409bbb0504b33c87a5361bf5105beed`:
- backend — run `38092737427`
- frontend — run `38092737557`
- schema-secret-gate — run `38092737259`
- alpha-golden-path — run `38092737337`

Every later commit in the task is documentation-only and was verified green with
the same four workflows; the `origin/v2` tip at handoff is green for all four.
The gates are deterministic and offline, so the result is stable.

(First remote run `fb00f875…` was red in all four; the intermediate run on the
lock+step-1 fixes was green for frontend/schema-secret-gate and exposed the two
follow-up defects fixed above.)

### Migration head
`0017` (unchanged; `0001`–`0017` untouched).

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-024V`, `branch=v2`, UTC start timestamp) created first, kept for the
whole task, removed only after all criteria were green and remote verified; never staged/committed/pushed.

## Prior task — AGV2-024 (Alpha 1 hardening)
- Branch: `v2`. **AGV2-024 (Alpha 1 hardening — CI, fresh install, upgrade/restore, structured logs,
  operator runbook)** is complete. `.aethergate-wip` is removed only after the final remote
  verification.
- **Migration head remains `0017`**; no new migration; `0001`–`0017` untouched. (Superseded by
  AGV2-027, which removed the retired v1 `app/` runtime; see the top of this file.)
- Repository is at **Alpha 1** (internal/self-hosted): see `docs/releases/alpha1-readiness.md` and
  `docs/operations/alpha-runbook.md`. No SemVer bump was invented (package stays `0.1.0`).
- New: `.github/workflows/` CI (backend/frontend/alpha-golden-path/schema-secret-gate), deterministic
  offline fake Ollama (`src/aethergate/testing/`), config preflight
  (`python -m aethergate.config_check`), schema-head readiness (`src/aethergate/schema_check.py`),
  structured logging + redaction (`src/aethergate/logging_config.py`), operator tooling
  (`scripts/ops/v2` backup/restore/migrate/diagnostics), safe diagnostics
  (`python -m aethergate.diagnostics`), and CI/golden-path tooling (`scripts/ci/`).

## AGV2-024 — Alpha 1 hardening

### CI (`.github/workflows/`)
- `backend.yml` — ubuntu-24.04 + Python 3.12 + PostgreSQL 16 service; `ruff check src tests`,
  `git diff --check`, `pytest`, empty -> head migration, `0016 -> 0017` migration, `config_check`,
  deterministic `scripts/gen_openapi.py`. Least-privilege `contents: read`; concurrency-cancel.
- `frontend.yml` — Node 22 `npm ci`, `npm run test`, `npm run lint`, `npm run build`,
  `npx tsc -b --noEmit`, OpenAPI/generated-client drift (fails on drift, never auto-commits).
- `alpha-golden-path.yml` — fully offline: builds images, starts postgres+api+worker+web with the
  deterministic DevOidcIdp and the fake Ollama override, migrates, runs `scripts/ci/golden_path.py`
  (bootstrap, OIDC login, catalog route, inference credential, official SDK non-stream + stream,
  dashboard load, backup, database drop/recreate, restore, post-restore state + SDK). No Internet /
  model download / provider credentials.
- `schema-secret-gate.yml` — `git diff --check`, `scripts/ci/secret_scan.sh`, workflow YAML lint
  (yamllint), OpenAPI/client drift. All workflows pin action major versions and use least privilege.
- CI is fully deterministic/offline (fake Ollama only); the live nomnom pre-release acceptance
  (`scripts/ci/nomnom_acceptance.sh`) uses the real internal Ollama and is not run per-PR.
- Remote GitHub Actions runs could not be observed from this environment; CI workflow configuration
  is validated locally (YAML parses; jobs enumerated; `scripts/ci/secret_scan.sh` and the golden-path
  phases are exercised on the real stack). No claim is made that a remote run passed.

### Deterministic fake Ollama (`src/aethergate/testing/fake_ollama.py`)
Test/CI-only local HTTP server speaking the minimum Ollama `/api/chat` + `/api/generate` surface used
by the pinned LiteLLM ollama adapter, so the **real** scheduler + adapter transport run offline with
no model download and no provider credentials. Supports deterministic non-stream, deterministic
multi-chunk streaming (`/api/generate` ndjson, which LiteLLM's ollama completion path uses),
`prompt_eval_count`/`eval_count` usage, deterministic upstream request id, configurable delay, and
configurable 404/429/other failures. It introduces no new production provider kind (emulates
`ollama`), is never referenced by production compose, and never persists/logs request content.
`tests/test_fake_ollama.py` proves the real adapter path, failure statuses, and delay against it.

### CI-safe deterministic OIDC
The existing `DevOidcIdp` is reused with a fixed CI issuer (`OIDC_ISSUER`, e.g.
`http://127.0.0.1:8090` on the runner / `http://<host-lan-ip>:18090` on nomnom), no LAN-IP discovery
requirement in CI, and a deterministic subject. The production HTTPS-issuer rule is unchanged
(`AETHERGATE_OIDC_ISSUER` must be `https://` in prod; a test asserts this is not weakened).

### Fresh-install path, config preflight, schema readiness
- `docs/operations/alpha-runbook.md` documents the clean install: prerequisites, secret generation,
  build, PostgreSQL start, empty -> `0017` migrate, API+worker start, `/health/live` + `/health/ready`,
  one-use bootstrap, bootstrap-token removal/recreation, OIDC config, catalog/provider setup,
  inference credential, official SDK non-stream + stream, TLS/reverse-proxy expectations.
- `python -m aethergate.config_check` validates DB config, queue key (valid Fernet), rejects prod
  inference-auth bypass, checks OIDC prod issuer rules, validates the bootstrap-token shape, reports
  the upstream allowlist, and warns on intentionally disabled optional features. Exit 0 valid /
  nonzero invalid; never prints secrets (`tests/test_config_check.py`).
- Readiness (`/health/ready`) now returns `503 schema_not_current` when the applied Alembic revision
  does not equal the code's expected head (`src/aethergate/schema_check.py`); the schema is still
  applied explicitly (never auto-run by workers). Readiness does not depend on upstream providers.

### Operator backup/restore + diagnostics
- `scripts/ops/v2 backup --output <p>` — PostgreSQL custom-format complete backup, explicit path,
  refuses overwrite without `--force`, 0600 permissions, never echoes the DB password.
- `scripts/ops/v2 restore --input <p> --force` — quiesces (stops) the API + worker first, replaces the
  database, reports the applied revision, and restarts api+worker; container-name overrides support
  disposable stacks. Documents that the queue key / external secret material must be backed up
  separately.
- `scripts/ops/v2 diagnostics --output <p>` / `python -m aethergate.diagnostics` — redacted safe
  bundle (version, migration state, DB-reachable boolean, sanitized feature flags, queue counts,
  observability summary, redacted logs); no credentials/content/DB URLs. `tests/test_diagnostics.py`
  proves canary absence.

### Structured logging + redaction + correlation
`src/aethergate/logging_config.py`: JSON logs in prod/alpha, human-readable dev logs; a central
`RedactionFilter` backstop scrubs credential prefixes (`agk_`/`ags_`/`agb_`), Bearer tokens, session/
CSRF cookies, OIDC fields, DB URLs/passwords, and PEM keys. An HTTP access middleware logs only the
matched route **template**, method, status, duration, and the gateway request id (never query strings,
headers, cookies, bodies, or content). Worker logs `worker.dispatch` and `request.settled` with safe
attempt/endpoint IDs. The gateway request id (response + logs) is distinct from the upstream request
id. `tests/test_logging_safety.py` canary-proves every secret and content class is absent and that the
response request id correlates with its access-log record.

### Populated 0016 -> 0017 upgrade proof
`tests/test_migrations.py::test_populated_0016_to_0017_preserves_rows` inserts representative
identity/provider/account/endpoint/alias/route/request/attempt rows at `0016`, upgrades to head, and
asserts: rows preserved, `upstream_error=false`, `upstream_status_code=null`, `first_token_at=null`
(no fake backfill), and head `0017`.

### Backup/restore + restart acceptance (clean disposable stack, real Ollama)
Ran via `scripts/ci/nomnom_acceptance.sh` on a **fresh disposable** compose project
(`ag-alpha-acceptance`, separate volume; not the accumulated dev DB):
- empty -> `0017`; bootstrap (one-use); deterministic OIDC browser login (httpx);
  catalog/provider/account/endpoint/alias/route to the real internal Ollama; inference credential;
- official OpenAI Python SDK 2.54.0 non-stream + stream `ok`/`ok_stream`, bypass `false`;
- request-priced policy -> completed inference producing `UsageRecord` + signed `LedgerEntry`;
- `scripts/ops/v2`-equivalent backup, database drop/recreate, restore; post-restore verification
  proved identities/roles/catalog/credential and **exact pre-backup counts**
  (`usage_records == ledger_entries`, no duplicate settlement), migration head `0017`, and
  post-restore SDK non-stream + stream success;
- API restart (readiness recovered), worker restart, full-stack restart (persistent volume) — SDK
  inference succeeded afterwards.
- The disposable stack was torn down (containers + volume) after the run; the long-lived dev stack and
  its database are untouched.

### Regression evidence (AGV2-024)
- Backend containerized suite (`scripts/dev/v2 test`): green, exit 0; **561 collected** (baseline 527
  + config_check/diagnostics/fake-ollama/logging-safety tests + populated 0016->0017).
- Frontend unit/component (`npm run test`): **87 passed** (baseline 87), 22 files.
- Full Playwright (`WEB_BASE_URL=http://127.0.0.1:8081`): **32 passed** (baseline 32); the new
  `e2e/alpha-golden.spec.ts` is CI-only and skips without `GOLDEN_INFERENCE_KEY` (3 skipped).
- `npm run build` clean; `npm run lint` clean; `npx tsc -b --noEmit` clean.
- OpenAPI/client drift clean (`scripts/gen_openapi.py` + `npm run generate:client` produced no diff).
- `ruff check src tests` clean; `git diff --check` clean; `scripts/ci/secret_scan.sh` clean.
- Official OpenAI Python SDK 2.54.0, inference-auth bypass `false` throughout.
- Migration head `0017`; `0001`–`0017` untouched.

### Real defect / flake root-caused
The full Playwright suite intermittently failed the first OIDC-dependent test after the API/worker
were recreated (`Sign-in failed` / landing on the login page). Root-caused to the documented
post-recreate cold start: the API caches OIDC discovery for 300s and does not cache failures, and a
recreated/reused `web` nginx or a stale JWKS/discovery momentary failure self-heals. Warming the
IdP discovery/JWKS and re-running the suite produced two consecutive clean full runs (**32 passed**).
No product defect; recorded so future runs restart `web` (nginx DNS) and warm IdP discovery first.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-024`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (gitignored).

## AGV2-023 — Operational observability (queue wait, streaming TTFT, retry, upstream health)

### Why this task exists
The web-console requirement calls for authoritative queue-wait, TTFT, retry-rate, and upstream-health
telemetry. Budget headroom was already Decimal-safe; the other three areas were labeled "Not yet
instrumented". This task adds the durable first-token/provider-outcome metadata plus bounded aggregate
read models, without logging/decrypting prompt or completion content and without introducing a second
scheduling/accounting authority.

### Migration 0017 (`0017_execution_attempt_observability.py`, `down_revision = 0016`)
Extends `execution_attempts` with safe non-content metadata:
- `first_token_at TIMESTAMPTZ NULL` — first non-empty streaming content chunk time;
- `upstream_error BOOLEAN NOT NULL DEFAULT FALSE` — true only for a terminal `ProviderError`;
- `upstream_status_code INTEGER NULL` — sanitized numeric upstream status when reliably known.

Justified indexes only: `ix_execution_attempts_started_at`,
`ix_execution_attempts_endpoint_finished` (`endpoint_id, finished_at`), and
`ix_inference_requests_queued_at`. No content/log columns. Historical rows are not backfilled with
fabricated values (`first_token_at` NULL, `upstream_error` false, `upstream_status_code` NULL). The
migration is reversible (`downgrade` drops the columns and indexes); `tests/test_migrations.py::test_migration_0016_to_0017`
proves `0016 -> 0017`, empty -> latest, and the explicit downgrade.

### First-token streaming transition
`SchedulingService.run_stream` calls `scheduler/repository.py::mark_first_token` on the first non-empty
`chunk.content`. It atomically: checks fence/attempt ownership; moves the request and attempt
`dispatched -> streaming`; sets `first_token_at` exactly once; is idempotent on repeat; refuses a
stale/foreign/terminal attempt; and never fires for a content-less chunk. Only the **presence** of
content is inspected — no prompt/completion content is persisted or logged. Encrypted StreamEvent
behavior is unchanged. A stream with no non-empty content chunk leaves TTFT NULL (never zero).

### Provider failure classification
`run_complete`/`run_stream` on `ProviderError` settle the attempt with `upstream_error=true` and persist
`upstream_status_code` when integer; `UnsupportedProvider`/`SecretResolutionError`/internal config
failures settle `upstream_error=false`. Cancellations are not upstream failures; `outcome_unknown`
stays ambiguous. Only numeric status is stored — never exception text, headers, URLs, or bodies.

### Metric definitions
- **Queue wait** = earliest `ExecutionAttempt.started_at` − `InferenceRequest.queued_at` (admission
  delay; the mutable request `started_at` is deliberately not used). p50/p95/p99 + sample count.
- **Streaming TTFT** = `first_token_at` − attempt `started_at`, streaming attempts only
  (dispatch-to-first-token; no decrypt, no terminal-completion time, no non-stream requests).
- **Retry rate** = additional `ExecutionAttempt`s for the same gateway request id
  (`attempted_requests`, `retried_requests`, `retry_attempts`, `request_retry_rate` nullable at 0
  denominator). Queued polling and new-request-id SDK retries are not counted.
- **Passive upstream health** per endpoint: sample count, succeeded/upstream-failed/ambiguous/429
  counts, nullable success rate, last success/failure, and related quota-group cooldown. No active
  probes, no synthetic percentages.
- Percentiles are computed by PostgreSQL (`percentile_cont`) in bounded, read-only, SQL-scoped
  queries; no unbounded history in Python/React. Window default 900 s (60..86400). No samples =>
  NULL percentiles (never zero).

### Admin API + RBAC
- `GET /admin/v1/observability/summary` — window + queue-wait/TTFT/retry. Uses the existing
  `admin:queue:read` permission (no new RBAC scope): `system_admin` reads the deployment aggregate or
  an explicit project; `project_admin`/`project_viewer` read only their own project; a cross-project
  project-role query is non-enumerating (`404`).
- `GET /admin/v1/observability/upstreams` — `system_admin` deployment scope only; project roles get
  `403` and cannot enumerate endpoint/provider-account health.

### Frontend
`frontend/src/pages/DashboardPage.tsx` renders the three real metric groups (queue wait, streaming
TTFT labeled dispatch-to-first-token, retry rate) plus system-admin upstream-health cards; a window
selector drives the read; `frontend/src/lib/client.ts` gains `observabilitySummary` /
`observabilityUpstreams` on the regenerated OpenAPI types; `frontend/src/lib/format.ts` gains
`formatMillis`/`formatRate` with explicit no-sample handling. Generated files
(`frontend/src/generated/*`) were regenerated, not hand-edited (drift check clean).

### Live nomnom verification (real stack; inference-auth bypass = false)
- **WIP/migration**: marker lifecycle correct; `0016 -> 0017`, empty -> latest, head `0017`.
- **Queue wait (six requests / two slots)** (`e2e/observability.spec.ts`): six concurrent official-SDK
  requests against a dedicated `max_concurrency=2` endpoint all succeed; the summary reports
  `sample_count >= 6` with non-null, ordered `p50 <= p95 <= p99`. Live DB sample snapshot (2 h):
  70 queue-wait samples, p50 ≈ 156.6 ms, p95 ≈ 85.2 s, p99 ≈ 124.6 s.
- **Streaming TTFT**: a real official-SDK stream returns `OK_STREAM`; `first_token_at` becomes
  non-null and a TTFT sample appears (live 2 h: 6 samples, p50 ≈ 110.7 ms, p95 ≈ 169.5 ms, TTFT > 0);
  no StreamEvent is decrypted for the metric and no prompt/completion content appears in the summary.
- **Upstream health**: pointing a disposable route at a nonexistent Ollama model fails the official SDK
  and increments `upstream_failed_attempts` with safe status evidence (persisted `upstream_status_code=404`,
  no raw provider body/URL); restoring `qwen3.8-2b-distill:Q6_K` succeeds and records success evidence.
- **Retry semantics**: the real scheduler pre-dispatch reclaim path (reserved attempt with an expired
  lease -> recovery reclaims -> worker re-claims) yields exactly two `ExecutionAttempt`s for one
  gateway request and `retry_attempts == 1`. Covered deterministically by
  `tests/test_observability.py::test_retry_metrics_real_scheduler_reclaim` (v2 intentionally never
  auto-retries dispatched work).
- **RBAC**: `system_admin` sees deployment summary + upstream health; `project_admin`/`project_viewer`
  see only their own summary; project roles cannot call upstream health (`403`); a cross-project
  explicit `project_id` is non-enumerating.

### Regression evidence (AGV2-023)
- Backend containerized suite (`scripts/dev/v2 test`): **527 passed, 0 failed** (baseline 506 + 20
  `tests/test_observability.py` + 1 `test_migrations.py::test_migration_0016_to_0017`; 527 collected).
- Frontend unit/component (`npm run test`): **87 passed** (baseline 82 + 5 new DashboardPage
  observability cases), 22 files.
- Full browser suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **32
  passed, 0 failed** (baseline 26 + 6 `e2e/observability.spec.ts`).
- `npm run build` clean; `npm run lint` clean; `npx tsc -b --noEmit` clean.
- OpenAPI/client drift clean: `python scripts/gen_openapi.py` + `npm run generate:client` produced no
  diff after regeneration.
- `ruff check src tests` clean; `git diff --check` clean.
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files; no key patterns in
  the changed files).
- Official OpenAI Python SDK regression (`openai 2.54.0`, Python 3.12.3), inference-auth bypass
  `false` (verified via `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` in the running api and an
  unauthenticated `/v1/models` returning `401`): non-stream + stream succeed in the observability spec.
- Migration head `0017`; `0001`–`0016` untouched.

### Real defect found and fixed (latent live-data defect)
Running the new observability six-request proof added project-A traffic, which pushed
`operator-rbac.spec.ts`'s "project_admin cancels own queued work" past the point where its
oldest-first, page-1 `waitForQueuedRequest` scan (limit 200, no state filter) could see the freshly
queued request. Root-caused as the documented live-data accumulation/pagination defect family and
fixed by filtering the queue read to `state=queued` before scanning. Two stale `outcome_unknown`
requests were holding both slots of the shared `ollama-endpoint` from abandoned prior runs; they were
reconciled through the real operator path (`aethergate.reconcile`) to free the slots.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:40953/v1`; web console loopback-only on
  `http://127.0.0.1:8081` (host `8080` occupied by `matrix-comms-element`; the committed
  `compose.web.yaml` default stays `127.0.0.1:8080`, and only the gitignored `deploy/v2/.env` redirect
  URI plus a throwaway compose `!override` port map pointed at `8081`); IdP issuer
  `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-023`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (gitignored).

## AGV2-022D — Remove final browser Decimal coercion from budget headroom

### Why this task exists
AGV2-022C closed the required regression gate, but a final code review found one remaining violation of
the phase-wide Decimal rule: `frontend/src/pages/DashboardPage.tsx` did
`const headroom = Number(budget.headroom)` and compared `headroom <= 0`. `BudgetStatusRead.headroom` is
a signed `Money` string and may be negative after overage; the browser may only make an exact
sign/zero classification, never an IEEE-754 `Number` conversion. This task closes that single dashboard
leak; it starts no new product scope (no observability, Responses, embeddings, accounting expansion, v1
migration, or redesign).

### Exact signed-Decimal comparison helper
`frontend/src/lib/decimal.ts` gains `compareDecimalToZero(value: string): -1 | 0 | 1`, a pure regex
classifier with no `Number()`, `parseFloat()`, bigint, or third-party float path. It accepts an optional
leading `-`, and returns `-1`/`0`/`1` for exact negative/zero/positive. Zero forms `0`,
`0.000000000000`, `-0`, and `-0.000000000000` are all `0`; `0.000000000001` is `1`;
`-0.000000000001` is `-1`; `123456789.123456789012` is `1` and `-123456789.123456789012` is `-1`.
Malformed input (`""`, `1e-3`, `NaN`, `Infinity`, `12,34`, `1.2.3`, `-`, `.5`, `5.`) is classified as
`0` ("not positive") so callers fail closed rather than treating garbage as an available balance.
`isValidDecimalString` / `isPositiveDecimalString` retain their existing non-negative contract.

### Dashboard behavior
`BudgetRow` now computes `blocked = budget.enabled && compareDecimalToZero(budget.headroom) <= 0`.
Exact positive => "ok"; zero or exact negative => "exhausted"; a disabled budget is never marked
exhausted solely because headroom is non-positive. The rendered headroom string is the server-returned
value verbatim (`{budget.headroom} {budget.currency} ...`); no client-side recomputation from
limit/committed/reserved.

### Runtime frontend Decimal-coercion scan
Grep of `frontend/src/` for `Number(`, `parseFloat(`, and `.toFixed(` now matches only the
`decimal.ts` module docstring (a prose mention). The only remaining numeric conversions are `parseInt`
on integer fields: `unit_scale` (PricingPage), `window_seconds` (BudgetsPage, QuotasPage),
`limit_units` (QuotasPage), `max_concurrency` (EndpointsPage), and `default_output_tokens`
(RoutesPage). No monetary Decimal field passes through `Number()`/`parseFloat()`/`.toFixed()` anywhere
in the reviewed runtime accounting surfaces (DashboardPage, `features/accounting/*`,
`features/catalog/QuotasPage`, `lib/decimal.ts`).

### Regression evidence (AGV2-022D)
- Backend containerized suite (`scripts/dev/v2 test`): **506 passed** (`506 tests collected`; clean
  green run, no failures). Baseline unchanged (frontend-only task).
- Frontend unit/component (`npm run test`): **82 passed** (baseline 74 + 8 new: 2 `compareDecimalToZero`
  decimal canaries + 6 DashboardPage budget-headroom cases), 22 files.
- Full browser suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`):
  **26 passed, 0 failed**. The first attempt had one transient `oidc_unavailable` (503) on the first
  accounting login; it was root-caused to a transient `OidcConfigurationError("OIDC discovery failed")`
  (httpx fetch of `/.well-known/openid-configuration` momentarily failed; the API caches discovery for
  300s and does not cache failures, so it self-heals). The IdP was reachable immediately after
  (`/jwks` 200, `.well-known` 200), and the full suite passed clean on the immediately following run.
- `npm run build` clean; `npm run lint` clean; `npx tsc -b --noEmit` clean.
- OpenAPI/client drift clean: `python scripts/gen_openapi.py` + `npm run generate:client` produced no
  diff on `frontend/src/generated/openapi.json` / `schema.d.ts` (no contract/route change).
- `ruff check src tests` clean; `git diff --check` clean.
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files; no key patterns in
  the changed files).
- Official OpenAI Python SDK regression (`openai 2.54.0`), inference-auth bypass `false` (verified via
  the running api `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` and an unauthenticated `/v1/models`
  returning 401): non-stream + stream against the UI-created disposable alias succeed
  (`catalog-inference.spec.ts` test 9), plus the credential lifecycle and accounting live SDK proofs.
- No migration; `0016` remains head; `0001`–`0016` untouched.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022D`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (gitignored).

## AGV2-022C — Close accounting regression gate and budget-policy disable wake-up

### Why this task exists
AGV2-022V closed its two accounting live-proof gaps and fixed two real defects, but the required full
Playwright regression was red (24 passed / 2 failed), and a final review found one adjacent scheduler
correctness hole. AGV2-022C closes exactly those three items; it starts no new product scope
(no observability, Responses, embeddings, accounting expansion, v1 migration, or redesign).

### Budget-policy disable wake semantics (real scheduler hole)
`src/aethergate/accounting/admin.py::update_project_budget_policy` now wakes budget-blocked queued
requests whenever the mutation makes the policy less restrictive:
- `new_limit > existing.limit_amount` (raise — AGV2-022V), **or**
- `existing.enabled is True and new_enabled is False` (disable — new).

Both cases call the existing scoped primitive
`scheduler/repository.py::clear_budget_wait_metadata_for_policy`, which clears wait metadata only for
`state = queued` + `wait_reason = budget_window_exhausted` + `wait_limit_id = this policy`. The worker
re-evaluation remains authoritative: if another policy still blocks, it re-applies correct metadata.
Name-only edits, limit decreases, unchanged policies, and disabled->enabled do **not** clear metadata.

### Deterministic disable-unblock regression
`tests/test_accounting.py::test_budget_window_exhausted_request_unblocks_after_policy_disable` drives
the real admin mutation path (not the repository helper directly): request price P; a budget with
`limit == P` admits the first request and queues the second as `budget_window_exhausted` (not a
terminal rejection); the first commits exactly P; the same policy is **disabled** via
`accounting_admin.update_project_budget_policy` under a deployment `system_admin` context; the cleared
wait metadata is asserted; the **same** `request_id` becomes eligible, dispatches, and settles exactly
once — exactly one new `UsageRecord` and exactly one signed negative `usage_debit`, no duplicate
settlement.

### Full Playwright suite fixes (the gating criterion AGV2-022V missed)
- **Pricing-table pagination** (`accounting.spec.ts` test 6): the pricing table accumulates policies
  across runs and paginates oldest-first at 20, so the run's new policy could fall off page 1. The test
  now scopes the pricing list to this run's route through the existing UI "Route binding" filter before
  asserting the created row, so it is deterministic as disposable policies grow.
- **management-rbac project/provider pagination** (`management-rbac.spec.ts`): after creating a
  uniquely named project/provider through the UI, the test used to assert on page 1 of the
  oldest-first, 20-per-page lists. It now uses the real UI pagination helper `goToLastPage` and asserts
  the new project and provider through the browser UI (no direct-API substitution).
- **credential lifecycle timeout** (`management-rbac.spec.ts` "inference credential lifecycle"): the
  test issues multiple live official-SDK calls against slow Ollama generation inside Playwright's
  default 60s timeout. It now sets a scoped `test.setTimeout(240_000)`; all official OpenAI Python SDK
  calls, `bypass=false`, and the old/new/revoked key assertions are unchanged (no raw `fetch`
  substitution; no global timeout inflation).

### Full browser regression (the gate)
- Full suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **26 passed,
  0 failed**, run twice consecutively with identical results. Per spec: `accounting` 8,
  `catalog-inference` 1, `management-rbac` 6, `operator-rbac` 7, `web-console` 4.
- The two AGV2-022V accounting live proofs remain green **inside the full suite**:
  same-request `budget_window_exhausted` unblock (test 7) and the released pre-dispatch reservation
  browser proof (test 8).

### Verification results (AGV2-022C)
- Backend containerized suite (`scripts/dev/v2 test`): **506 passed** (baseline 505 + 1 deterministic
  disable-unblock test); `506 tests collected`.
- Frontend: `npm run build` clean, `npm run lint` clean, `npm run test` **74 passed** (22 files).
- Browser E2E full suite: **26 passed, 0 failed** (twice).
- Official OpenAI Python SDK (`openai 2.54.0`, `AETHERGATE_ALLOW_INFERENCE_AUTH_BYPASS=false` verified
  in the running api and by an unauthenticated `/v1/models` returning 401): non-stream + stream against
  the UI-created disposable alias succeed (`catalog-inference.spec.ts`), and the credential lifecycle
  proves old/new/revoked key outcomes (`management-rbac.spec.ts`).
- `ruff check src tests` clean; `git diff --check` clean; `npx tsc -b --noEmit` clean; OpenAPI/client
  generation drift clean (`scripts/gen_openapi.py` + `npm run generate:client` produced no diff on
  `openapi.json` / `schema.d.ts`; no contract/route change).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files; no key patterns in
  the changed files).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:37439/v1`; web console loopback-only on
  `http://127.0.0.1:8081` (because `matrix-comms-element` occupied `0.0.0.0:8080`; the committed
  `compose.web.yaml` default stays `127.0.0.1:8080`, and only the gitignored `deploy/v2/.env`
  redirect URI plus a throwaway compose override pointed at `8081` for this run); IdP issuer
  `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022C`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified. It was never staged, committed, or pushed (it is
gitignored).

## AGV2-022V — Close accounting live queue-unblock and released-reservation proofs


### Why this task exists
AGV2-022's final review found two narrow **live acceptance** gaps:
1. The budget live proof used `budget_request_too_large`, a terminal *failed* request, then submitted a
   *new* request after raising the limit. That does not prove the **same queued**
   `budget_window_exhausted` request becomes eligible after a policy edit.
2. There was no live/browser proof of a **real pre-dispatch released BudgetReservation** rendered with
   `price_snapshot_id = null`, zero reserved/committed amounts, no UsageRecord, and no `usage_debit`.

These two are now closed (see below), plus the real scheduler and nginx defects the proofs exposed.

### Semantics distinction (recorded truthfully)
- `budget_request_too_large` = fail-fast for a request that cannot fit even an empty budget window
  (terminal rejection).
- `budget_window_exhausted` = the queued temporary-capacity condition; the same request resumes after a
  policy edit admits it. Only this scenario is called "block/unblock".

### Real scheduler defect fix (found by the live unblock proof)
`src/aethergate/scheduler/repository.py` gains `clear_budget_wait_metadata_for_policy(session,
budget_policy_id)`; `src/aethergate/accounting/admin.py::update_project_budget_policy` calls it when
`new_limit > existing.limit_amount`. Before this, raising the cap left the queued request's
`next_eligible_at` pointing at the window reset, so the worker never reclaimed it. Deterministic
coverage: `tests/test_accounting.py::test_budget_window_exhausted_request_unblocks_after_policy_raise`
(first request commits P; second queues `budget_window_exhausted`; limit raise + metadata clear; the
**same** request_id dispatches and settles exactly once).

### Real nginx proxy defect fix (found by the live SDK proof)
`frontend/nginx.conf` sets `proxy_read_timeout 600s` / `proxy_send_timeout 600s` on the `/v1/` location.
The gateway holds non-stream requests synchronously while a queued request waits for admission and the
upstream model generates (no bytes are flushed until the terminal result). The nginx default 60s cut
those held connections with a 504, causing the official SDK to retry (new request_id each time). The web
image was rebuilt and the `aethergate-v2-web` container recreated to apply it.

### Live proofs (browser UI + official OpenAI Python SDK, inference-auth bypass = false)
- **Budget unblock — same request** (`accounting.spec.ts` test 7): read the route's enabled request
  price P; create a budget with limit == P through the UI; first official SDK request commits P; spawn a
  second official SDK call and keep it pending; poll the queue API for the `budget_window_exhausted`
  request and capture its stable `request_id` (targeted by `waitForBudgetQueuedRequest` via
  `wait_limit_id`, and the row filter now uses the unique `budgetName` — filtering by the price string
  is ambiguous because committed_amount equals P on older leftover rows and the list is ordered
  oldest-first); prove queued/`budget_window_exhausted`/`wait_limit_metric=budget`/no error/no usage;
  raise the **same** policy limit through the UI; await the original pending SDK call and prove it
  succeeds (no replacement request); prove exactly one new UsageRecord and one signed `usage_debit`
  (`-P`), and the server-exact committed/headroom values.
- **Released reservation — real lifecycle + browser** (`accounting.spec.ts` test 8, new):
  `src/aethergate/live_accounting_release.py` is a controlled harness using the real
  scheduler/accounting path (enqueue → `claim_and_reserve` → reserve + snapshot → direct SQL revert to
  the reserved pre-dispatch window → `request_cancellation`), with a `NoopAdapter` that asserts
  `complete`/`stream` are never called (no upstream dispatch). Workers are stopped/restarted around it
  (`runReleaseHarness`/`listWorkerContainers` in `fixtures.ts`, using direct `docker stop`/`start` to
  avoid the podman `service_healthy` wait). The harness prints identifiers only (JSON, no secret/prompt/
  completion) and asserts the released invariants. The browser Reservations page then proves the same
  real reservation renders: state `released`, `reserved_amount = 0`, `committed_amount = 0`, and
  `price_snapshot_id = null` shown as "No snapshot / released before dispatch".

### Verification results (AGV2-022V)
- Backend containerized suite (`scripts/dev/v2 test`): **505 passed** (baseline 504 + 1 deterministic
  budget-unblock test). `505 tests collected`.
- Frontend: `npm run build` clean, `npm run lint` clean, **74 unit/component tests pass** (22 files).
- Browser E2E — `accounting.spec.ts`: **8 passed** (was 7; the new released-reservation proof makes 8).
- Browser E2E — full suite (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`):
  **24 passed, 2 failed** at the time. > **Correction (AGV2-022C):** AGV2-022V was therefore **not**
  fully complete — the required full-browser regression gate was red. Both failures were real,
  deterministic-under-load defects (not "pre-existing and unrelated" flakes as first recorded), and
  both are now fixed in AGV2-022C, which closes the full suite at **26 passed, 0 failed** (twice):
  - `management-rbac.spec.ts` "creates a project and a provider through the UI" failed because
    accumulated disposable projects exceed the oldest-first 20-per-page list; fixed in AGV2-022C with
    the real UI pagination helper `goToLastPage`.
  - `management-rbac.spec.ts` "inference credential lifecycle" intermittently exceeded the default 60s
    timeout across multiple slow live SDK calls; fixed in AGV2-022C with a scoped
    `test.setTimeout(240_000)`.
  Additionally, the accounting pricing-table assertion (test 6) had the same accumulation/pagination
  root cause and was fixed in AGV2-022C by scoping the pricing list to the run's route through the UI
  filter.
- Official OpenAI Python SDK (`openai 2.54.0`): the budget-unblock proof drives a **pending** non-stream
  call that stays held through the browser policy edit and then succeeds; the released-reservation proof
  asserts no upstream dispatch occurred.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean from
  this task (no contract/route change; `frontend/src/generated/openapi.json` and `schema.d.ts` are
  unchanged in this diff). Secret/token/content canary scan clean.
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:43591/v1`; web console loopback-only on
  `http://127.0.0.1:8081`; IdP issuer `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022V`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified.

## AGV2-022 — Accounting management UI

### Implementation commits (origin/v2)
- `feat(web): add accounting management UI` — accounting feature module
  (`frontend/src/features/accounting/`), Decimal-safe helpers (`frontend/src/lib/decimal.ts`), typed
  accounting client methods + `roles.ts` guards, role-aware routing/navigation, accounting
  unit/component tests, and the live browser E2E (`frontend/e2e/accounting.spec.ts`).
- `fix(accounting): serialize money canonically and sign usage ledger debits` — canonical fixed-point
  money JSON serialization and signed `usage_debit` ledger entries, with regression tests.

### Routed pages / features
- Accounting configuration: `/accounting/pricing`, `/accounting/snapshots`, `/accounting/budgets`.
- Accounting history/observability: `/accounting/reservations`, `/accounting/usage`,
  `/accounting/ledger`, `/audit`.
- Pages: `PricingPage`, `PriceSnapshotsPage`, `BudgetsPage`, `BudgetReservationsPage`, `UsagePage`,
  `LedgerPage`, `AuditPage` (all under `frontend/src/features/accounting/`).
- Typed client methods in `frontend/src/lib/client.ts`; role guards in `frontend/src/lib/roles.ts`.

### Decimal-safe browser handling
- Money is fixed-point Decimal and stays a string end-to-end. `frontend/src/lib/decimal.ts` provides
  `isValidDecimalString`, `isPositiveDecimalString`, and `formatMoney` without `Number()`/`parseFloat()`.
- Form state for money/prices is string; validation rejects lossy/float syntax; exact strings are sent
  to the backend; rendered read models are verbatim. Canary tests use `0.000000000123` and
  `123456789.123456789012`.

### Backend accounting corrections
- **Canonical money serialization** (`src/aethergate/domain/value_objects.py`): `Money`,
  `NonNegativeMoney`, and `PositiveMoney` now serialize to JSON as canonical fixed-point strings via
  `PlainSerializer(_serialize_money, return_type=str, when_used="json")` (`format(value.normalize(),
  "f")`). This removes Pydantic's auto `pattern` from money fields in
  `frontend/src/generated/openapi.json`; `schema.d.ts` is unchanged. Regression test:
  `tests/test_contracts.py::test_money_serializes_as_canonical_fixed_point`.
- **Signed usage ledger debits** (`src/aethergate/scheduler/service.py`): the `usage_debit`
  `LedgerEntry` is now written with a negative amount (`amount=-amount`) to match the documented
  "signed" ledger convention (`docs/architecture/accounting.md`). Regression assertion added to
  `tests/test_accounting.py::test_request_priced_budget_reserves_and_queues_exhausted`.

### Live proofs (browser UI + official OpenAI Python SDK, inference-auth bypass = false)
- **Pricing + immutable snapshots** (`accounting.spec.ts`): create a request-priced `PricePolicy`
  through the UI with the exact Decimal `0.000000000123`; official SDK request succeeds; the resulting
  `PriceSnapshot` captures the old price; edit the policy to `0.000000000456`; a second SDK request
  captures the new price; the old snapshot is verified immutable and unchanged.
- **Budget block/unblock** (`accounting.spec.ts`): create a project budget through the UI with a limit
  below the request price; an official SDK request is blocked (`budget_request_too_large`); the budget
  status stays zero-committed; raise the limit through the UI; the same request succeeds; committed
  spend, usage, and the (negative) `usage_debit` ledger entry reflect the exact price.
- **RBAC + audit scope** (`accounting.spec.ts` tests 1–5): system_admin sees the full surface;
  project_admin cannot access deployment pricing/snapshot surfaces and cannot enumerate project B;
  project_viewer is read-only; a project role sees only own-project audit events.

### Verification results (AGV2-022)
- Backend containerized suite (`scripts/dev/v2 test`): **504 passed** (baseline 503 + 1 money
  serialization test).
- Frontend: `npm run build` clean, `npm run lint` clean, **74 unit/component tests pass** (22 files;
  baseline 54 + 20 accounting tests).
- Browser E2E (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **25 passed**
  (baseline 18 + 7 accounting): 4 `web-console`, 7 `operator-rbac`, 7 `management-rbac`, 1
  `catalog-inference`, 7 `accounting`.
- Official OpenAI Python SDK (`openai 2.54.0`): non-stream + stream succeed; the accounting live proofs
  exercise non-stream success plus `budget_request_too_large` fail-closed and recovery after a limit
  raise.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean
  (`openapi.json` only lost the auto `pattern` fields from the money serialization change; `schema.d.ts`
  unchanged).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files, no key patterns in
  the new/modified files).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral `http://127.0.0.1:39569/v1`; web console loopback-only on
  `http://127.0.0.1:8081`; IdP issuer `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-022`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified.

## AGV2-021V — Management lifecycle verification gaps
(unchanged; see the prior handoff section below for the full record)

### Why this task exists
AGV2-021's final review found three narrow acceptance gaps in the **live proof** (not the core UI):
1. the catalog → inference E2E reused the pre-provisioned Ollama provider/account/endpoint instead of
   creating the disposable path through the UI;
2. the credential create/rotate/revoke lifecycle used direct admin API calls instead of the browser UI;
3. `catalog-inference.spec.ts` used raw `fetch`, but the handoff called that an "official OpenAI Python
   SDK" proof.

These three are now closed with truthfully worded evidence (see below).

### Full disposable catalog path through the browser UI
`frontend/e2e/catalog-inference.spec.ts` (system_admin OIDC browser session against the live stack) now
creates the complete disposable path through the actual web pages, using unique names per run
(`e2e-disp-*` + `Date.now()`):
1. **Provider** — `/catalog/providers`, kind `ollama`, capability `text` (reuses the existing Ollama
   adapter; no new backend adapter was invented).
2. **Provider account** — `/catalog/provider-accounts`, no `SecretRef` (local Ollama needs no provider
   secret; raw provider secrets are never placed in browser fields, DB metadata, logs, or Git).
3. **Endpoint** — `/catalog/endpoints`, account = the new account, base destination = the allowlisted
   nomnom Ollama destination `http://192.168.22.50:11434`, `max_concurrency = 1`, active; backend
   egress policy remains authoritative.
4. **Model alias** — `/catalog/models`, client-visible alias `e2e-disp-alias-<runId>`.
5. **Route binding** — `/catalog/routes`, using only the new alias/account/endpoint; upstream model
   `qwen3.8-2b-distill:Q6_K`.

The test resolves the created IDs via `adminJson` postcondition inspection and asserts the new route
uses only the new account/endpoint (not the pre-provisioned `ollama-account` / `ollama-endpoint`), so
no hidden fallback to the pre-existing route is possible.

There is no DELETE surface for catalog resources, so the test leaves the disposable route and endpoint
**inactive** at the end (Active toggle through the UI) rather than mutating rows directly.

### Official Python SDK against the UI-created alias
`frontend/e2e/sdk_inference.py` is a small official `openai` SDK driver invoked by
`frontend/e2e/fixtures.ts` `runOfficialSdk`. It reads the base URL, key, model, and stream flag from
the environment (never argv), and prints a single redacted status line (`OK`, `OK_STREAM`, or
`FAIL <Class> status=<int> code=<code>`). No raw key is ever printed.

- **Official SDK version**: `openai 2.54.0` (Python 3.12, repo venv).
- `base_url` = the web console origin `/v1` (proxied through nginx), model = the disposable alias.
- **inference-auth bypass = false** throughout.
- Non-stream succeeds (`OK`) and stream succeeds (`OK_STREAM`) against the disposable alias.
- Deactivate the RouteBinding through the UI → the official SDK fails safely with `model_unavailable`
  (no fallback); restore/activate → the official SDK succeeds again.

This is genuine **official OpenAI Python SDK** evidence, distinct from the raw `fetch` proof in
AGV2-021 (see the correction note under AGV2-021 below).

### Credential lifecycle entirely through the browser UI
`frontend/e2e/management-rbac.spec.ts` adds "inference credential lifecycle → create/rotate/revoke
through the UI with official SDK outcomes". A disposable project + principal are provisioned via admin
fixtures (allowed), but every lifecycle action is performed through the browser UI:

- **Create** — `/credentials?project=<disposable>`: New credential, select principal, audience =
  inference, Create; the one-time raw key is captured only from the `RevealSecret` UI in test memory.
  Leak canary: key absent from URL/history, `localStorage`/`sessionStorage`, console messages, and the
  DOM after the reveal is closed.
- **Use** — official SDK with the new key against the live alias `gpt-4` succeeds.
- **Rotate** — Rotate → confirm → new one-time key from `RevealSecret`; old key fails `401` and the new
  key succeeds via the official SDK.
- **Revoke** — Revoke → confirm; the rotated key fails `401` immediately.

No direct `adminJson` create/rotate/revoke substitutes the lifecycle actions being proven.

### Operator-rbac robustness fix
`frontend/e2e/operator-rbac.spec.ts` assumed a single endpoint (it paused
`queue/endpoints` `items[0]` and clicked a unique `Pause` button). Because AGV2-021V's catalog E2E now
legitimately leaves a second (inactive) endpoint, the spec was made robust:
- `endpointId` is resolved through the live `gpt-4` route binding (`resolveRouteEndpointId`), not
  `items[0]`;
- the pause/resume UI test scopes `Pause`/`Resume` to the first available button (`.first()`).

This is test-only; no product code changed.

### Verification results (AGV2-021V)
- Backend containerized suite (`scripts/dev/v2 test`): **503 passed** (exit 0; the `-q -q` summary line
  is swallowed by docker-compose output; the count is confirmed by a clean 100% run).
- Frontend: `npm run build` clean, `npm run lint` clean, **54 unit/component tests pass** (18 files).
- Browser E2E (`npx playwright test`, chromium, `WEB_BASE_URL=http://127.0.0.1:8081`): **18 passed** —
  4 `web-console`, 7 `operator-rbac`, 7 `management-rbac` (6 prior + 1 lifecycle), 1 `catalog-inference`.
- Official OpenAI Python SDK (`openai 2.54.0`): non-stream + stream succeed against the disposable
  alias; deactivate → `model_unavailable`; restore → success; old/rotated/revoked key outcomes proven.
- `ruff check src tests` clean; `git diff --check` clean; OpenAPI/client generation drift clean
  (`python scripts/gen_openapi.py` from repo root + `npm run generate:client` produced no diff on
  `openapi.json` / `schema.d.ts`).
- Secret/token/content canary scan clean (no `.env`/`.pem`/`.key`/credential files, no key patterns in
  the new/modified files).
- No migration; `0016` remains head.

### Dynamic ports (this run)
- API base URL ephemeral (`http://127.0.0.1:46291/v1`); web console loopback-only on
  `http://127.0.0.1:8081` (because `matrix-comms-element` occupied `0.0.0.0:8080`; the committed
  `compose.web.yaml` default stays `127.0.0.1:8080`, and only a gitignored `deploy/v2/.env` redirect
  URI plus a throwaway compose override pointed at `8081` for this run); IdP issuer
  `http://192.168.22.50:8090`.

### WIP marker lifecycle
`.aethergate-wip` (`task=AGV2-021V`, `branch=v2`, UTC start timestamp) was created first, kept for the
whole task, and removed only after all criteria were green, the handoff committed, every commit pushed
to `origin/v2`, and the remote branch verified.

## AGV2-021 — Management UI I

### Implementation commit(s) (origin/v2)
- `feat(web): add identity and catalog management UI` — feature modules
  (`frontend/src/features/identity/`, `frontend/src/features/catalog/`), shared UI primitives
  (`frontend/src/components/ui/`), typed management client methods + `roles.ts` guards, role-aware
  navigation/routing, and the management + catalog unit/component tests.
- `test(web): add management RBAC and catalog-inference browser E2E` — the initial browser E2E.
- `test(web): fix operator-rbac queue pagination fragility` — the pre-existing
  `operator-rbac.spec.ts` "project_admin cancels own queued work" test filtered by queue state before
  and after cancel, so it no longer depends on the freshly submitted request landing on page 1 of an
  oldest-first, 20-per-page queue (it broke as historical traffic accumulated).
- `docs(web): document identity and catalog management UI` — the handoff plus `docs/web-console.md`,
  `docs/development/README.md`, and `frontend/README.md` updates.

> **Correction (recorded in AGV2-021V):** the AGV2-021 handoff called the catalog → inference proof an
> "official OpenAI Python SDK" proof, but AGV2-021's `catalog-inference.spec.ts` used raw
> `fetch`/`submitInference` (raw OpenAI-compatible HTTP) and reused the pre-provisioned
> provider/account/endpoint. That wording overstated the evidence. AGV2-021V replaces it with the
> genuine official-`openai`-SDK proof against a fully UI-created disposable path.

### Routed pages / features
- Identity: `/projects`, `/projects/:projectId`, `/principals`, `/principals/:principalId`,
  `/credentials`, `/roles`.
- Catalog (`system_admin` only): `/catalog/providers`, `/catalog/provider-accounts`,
  `/catalog/endpoints`, `/catalog/quotas`, `/catalog/models`, `/catalog/routes`.
- Shared primitives under `frontend/src/components/ui/`: `Modal`, `ConfirmDialog`, `ErrorBanner`,
  `EmptyState`, `StatusBadge`, `RevealSecret`, `Form`, `Pagination`.
- Typed client: `frontend/src/lib/client.ts` (all management methods + `apiErrorMessage`); role
  guards: `frontend/src/lib/roles.ts` (`isSystemAdmin`, `isProjectAdmin`, `canManageIdentity`,
  `canManageCatalog`).

### RBAC behavior
- `system_admin` sees and may mutate identity + catalog management. `project_admin` sees only
  project-scoped identity surfaces and is denied catalog/deployment mutations; `project_viewer` is
  read-only. Nav hiding is cosmetic; the backend `403`/non-enumeration (`404`) remains the boundary.
- Proven live in `management-rbac.spec.ts`: system_admin create project/provider/credential + raw-key
  reveal + revoke + grant/revoke role; project_admin manage own principals but cannot enumerate B and
  is denied catalog APIs (`403`); project_viewer read-only and denied direct mutations (`403`).

### One-time credential reveal
- Create/rotate return the raw key once, shown only in a `RevealSecret` modal with Copy and a
  "cannot be retrieved again" note; destroyed on close. Never in URL/history/web storage/logs; no
  "show existing key" control. The leak canary proves it does not survive reload/navigation/storage
  inspection and is absent from non-secret list/detail responses.

### SecretRef boundary
- Provider-account configuration selects existing `SecretRef` metadata (stable opaque ID) only; no
  plaintext provider-secret input, no raw provider-secret display, no internal secret backend
  material exposed. Raw secret storage remains deferred (truthful boundary).

### Catalog form / invariant handling
- Endpoint list distinguishes catalog `is_active` from queue runtime `operational_state`.
  Pause/drain/resume remain queue/operator actions. Route-binding/account/quota mismatches and the
  one-active-route rule render stable structured errors (`409 active_route_conflict`,
  `400 parent_mismatch`, `400 destination_denied`) with no client-side fallback chain. Edit forms
  send only changed fields (PATCH semantics); server-side pagination everywhere.

## Issues / Risks
- **Web Docker build**: `vite.config.ts` must alias `cookie` and `set-cookie-parser` to their
  concrete CommonJS entries, or `react-router` fails to resolve them under Rollup
  (`Rollup failed to resolve import "cookie"`). The aliases are committed; rebuild with
  `docker build --no-cache -t aethergate-v2-web:local frontend/`.
- **Live SDK inference speed**: the nomnom Ollama `qwen3.8-2b-distill:Q6_K` non-stream generation is
  slow enough (tens of seconds per call under load) that the live-proof specs need a generous
  `test.setTimeout(240_000)` (`accounting.spec.ts` tests 6/7 and `catalog-inference.spec.ts`). If Ollama
  is reloading the model (cold start) the first SDK call is noticeably slower.
- **Docker healthcheck under this environment**: `docker compose up` waits on service healthchecks
  that the host's systemd-less Docker cannot run (`dial unix /run/user/1000/systemd/private: connect:
  connection refused`), so containers may show `(starting)` while fully functional. Bring the stack up
  with manual `docker start`/compose `up -d --no-deps` rather than `scripts/dev/v2 up`.
- **API recreation and OIDC web callback**: recreating the API with only `compose.yaml` drops
  `AETHERGATE_OIDC_WEB_CALLBACK_PATH=/auth/callback` (it lives in `compose.web.yaml`), which breaks
  browser OIDC login. Recreate the API with both `-f compose.yaml -f compose.web.yaml`, then restart
  the `web` container so nginx re-resolves the `api` DNS name.
- **Operator-rbac single-endpoint assumption** (fixed in AGV2-021V): the spec paused
  `queue/endpoints` `items[0]` and clicked a unique `Pause` button, which broke once the catalog E2E
  left a second endpoint. It now resolves the `gpt-4` route's endpoint and scopes buttons with
  `.first()`. Future endpoint-adding tests should keep this in mind.
- **Operator-rbac queue pagination** (fixed in AGV2-021): the queue page sorts oldest-first and
  paginates at 20, so the pre-existing "project_admin cancels" test depended on the new request
  landing on page 1. The test now filters by queue state before/after cancel.
- Live E2E runs accumulate disposable test data (providers/accounts/endpoints/aliases/credentials/
  queue requests/price policies/budgets). The specs are idempotent, use unique names, scope
  assertions to the current run's route, and disable leftover enabled budgets; the disposable
  route/endpoint are deactivated at the end of the catalog E2E. The pricing table can still accumulate
  leftover price policies across aborted runs (assertions must stay route-scoped).
- **Project list pagination (fixed in AGV2-022C)** — `management-rbac.spec.ts` "creates a project and a
  provider through the UI" used to assert `getByText(projectName)` on page 1 after creating a project,
  but the projects list sorts oldest-first at 20 per page and disposable projects accumulate. It now
  uses `goToLastPage` (real UI pagination) before asserting the new project and provider through the
  browser. Same root-cause family as the AGV2-021 operator-rbac queue pagination fix.
- **management-rbac SDK lifecycle test timeout (fixed in AGV2-022C)** — "inference credential lifecycle"
  issues multiple live `gpt-4` SDK calls (each routed to slow `qwen3.8-2b-distill:Q6_K`) inside the
  default 60s test timeout. It now sets a scoped `test.setTimeout(240_000)`; the SDK calls,
  `bypass=false`, and key assertions are unchanged (no global timeout inflation).
- **Accounting pricing-table pagination (fixed in AGV2-022C)** — `accounting.spec.ts` test 6 asserted the
  run's new PricePolicy on page 1 of the oldest-first, 20-per-page pricing table, which accumulates
  policies across runs. It now scopes the table to the run's route through the existing UI "Route
  binding" filter before asserting, so it is deterministic as leftover policies grow.
- **nginx proxy_read_timeout (fixed in AGV2-022V)** — the web nginx must not use the 60s default for
  `/v1/`; the gateway holds non-stream requests while queued/upstream-generating. `proxy_read_timeout
  600s` / `proxy_send_timeout 600s` are now set; recreate the web image+container to apply.
- **Budget list oldest-first pagination (fixed in AGV2-022V)** — the budget list is ordered oldest-first;
  the budget-unblock proof now targets its row by the unique per-run `budgetName`, not the price string
  (committed_amount equals P on older leftover rows).
- Existing AGV2-020V risks still apply: dev IdP key regeneration vs API JWKS cache; the Chromium
  `--disable-software-rasterizer` rAF fix; do not scale workers via `scripts/dev/v2 workers` (use
  `docker compose up -d --scale worker=N --no-deps worker`); host port `8080` may be occupied.
- `ruff format` remains out of scope (pre-existing reformat); `ruff check` is the gate.

## Key files
- Observability (new): `src/aethergate/observability/service.py`,
  `src/aethergate/observability/repository.py`, `src/aethergate/api/observability_admin.py`,
  `src/aethergate/migrations/versions/0017_execution_attempt_observability.py`,
  `tests/test_observability.py`, `frontend/e2e/observability.spec.ts`,
  `docs/architecture/observability.md`.
- Scheduler observability metadata: `src/aethergate/scheduler/repository.py`
  (`mark_first_token`, `settle` with `upstream_error`/`upstream_status_code`),
  `src/aethergate/scheduler/service.py` (`run_stream` first-token transition; provider-failure
  classification), `src/aethergate/persistence/models.py` (`ExecutionAttempt` columns/indexes).
- Frontend: `frontend/src/pages/DashboardPage.tsx` (+ `.test.tsx`), `frontend/src/lib/client.ts`,
  `frontend/src/lib/format.ts`, `frontend/src/generated/openapi.json` / `schema.d.ts`.
- E2E: `frontend/e2e/observability.spec.ts` (new), `frontend/e2e/operator-rbac.spec.ts`
  (`waitForQueuedRequest` state-scoped), `frontend/e2e/web-console.spec.ts` (placeholders replaced).
- Frontend accounting: `frontend/src/features/accounting/*` (7 pages + 3 test files),
  `frontend/src/lib/decimal.ts` (now incl. `compareDecimalToZero`), `frontend/src/lib/client.ts`,
  `frontend/src/lib/roles.ts`, `frontend/src/App.tsx`, `frontend/src/components/Sidebar.tsx`.
- Backend: `src/aethergate/domain/value_objects.py` (canonical money serialization),
  `src/aethergate/scheduler/service.py` (signed usage debits),
  `src/aethergate/accounting/admin.py` (clears wait metadata on limit raise **and policy disable**),
  `src/aethergate/live_accounting_release.py` (live released-reservation harness),
  `tests/test_contracts.py`, `tests/test_accounting.py`.
- Docs: `docs/architecture/observability.md`, `docs/architecture/scheduler.md`,
  `docs/architecture/admin-api.md`, `docs/web-console.md`, `docs/development/README.md`,
  `frontend/README.md`.

## Recommended Next Step
Alpha 2 feature work: the next milestone is the **Responses API** (or embeddings), built on the now
Alpha-1-hardened foundation (deterministic offline CI + golden path, config preflight, schema-head
readiness, structured logging/redaction, backup/restore, and the operator runbook). Reuse the
now-verified scheduler read model, typed `/admin/v1` + generated-client patterns, the fake-Ollama
offline fixture, and the official-SDK + backup/restore acceptance harnesses, starting from the green
regression baseline: backend **561**, frontend **87**, full Playwright **32**, migration head
**0017**. Do not start Responses/embeddings/v1-migration in AGV2-024 (explicitly deferred).
