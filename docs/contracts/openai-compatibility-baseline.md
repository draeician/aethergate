# OpenAI Compatibility Baseline

Living document. Records the pinned upstream OpenAI compatibility reference and the
principles that govern AetherGate's OpenAI-compatible surface.

## Upstream source

- **Repository:** `https://github.com/openai/openai-openapi`
- **Spec version:** OpenAPI `3.1.0`, `info.version` `2.3.0`
- **Commit SHA:** `f7bc81b7f30e834bbdb6bcc3370903e49fe7e5b1`
- **Commit date:** 2026-10-03 19:56:31 +0000 (retrieved 2026-10-03)
- **Server base URL:** `https://api.openai.com/v1`

The full upstream `openapi.yaml`/`openapi.json` is intentionally **not** vendored
into this repository; it is multi-megabyte and belongs to upstream.

## Target AetherGate v2 endpoints

- `GET /v1/models`
- `GET /v1/models/{model}` (model retrieval, present in the current official contract)
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/embeddings`

`/v1/responses` is the preferred modern API surface; `/v1/chat/completions` remains a
supported compatibility surface.

### Implementation status (first inference milestone)

- `GET /v1/models` and `GET /v1/models/{model}` — implemented; publish active public model
  aliases only (never provider-facing model names or routing internals).
- `POST /v1/chat/completions` — implemented for non-streaming and SSE streaming, direct dispatch
  only (no queueing). Transport/provider-control fields are rejected; unknown aliases return an
  OpenAI-compatible structured error.
- `POST /v1/responses`, `POST /v1/embeddings` — **not yet implemented** (still part of the v2
  target, after this milestone).

## Compatibility principles

- Compatibility behavior is **tested against official SDK clients, not assumed**.
- Fields returned by the gateway must match the pinned reference types; JSON is
  serialized as objects/strings, never JSON-strings inside JSON strings.
- Unknown/unsupported request fields are handled according to a documented tolerance
  policy (see below); unsupported *features* are rejected explicitly, never silently dropped.
- Error responses use structured error objects with documented status codes and safe
  messages, distinguishing gateway vs upstream request IDs.

## Additive-field tolerance

- AetherGate accepts and ignores unknown additive fields from clients where the upstream
  contract permits it, and does not fail requests solely because a client sends a newer,
  purely additive field.
- AetherGate does **not** silently strip fields it is expected to honor. A field that
  affects semantics but is unsupported must produce an explicit rejection.

## Streaming / event compatibility

- SSE streams follow the upstream event format (`data:` frames, terminal `[DONE]` for the
  surfaces that use it).
- Streaming responses emit `usage` and finish-reason semantics consistent with the pinned
  contract where applicable; this is subject to official-SDK tests, not assumption.
- HTTP status is fixed once SSE headers are sent; the gateway aligns client, queue, and
  upstream deadlines accordingly.

## Request-ID / error compatibility

- Gateway responses carry a gateway request ID; upstream request IDs are preserved/forwarded
  where available and never conflated with the gateway ID.
- Structured errors expose `type`, `message`, and an optional `code` and `request_id`.

## Not yet implemented (do not claim compatibility)

The following OpenAI surfaces are **not** part of the v2 compatibility target and are
explicitly unsupported until their contracts and adapters are built and tested:

- audio (`/v1/audio/*`), images (`/v1/images/*`), and moderation
- assistants / threads / runs, batches, files, fine-tuning, uploads, vector stores
- realtime / live sessions, chatkit, agents, skills, vaults, videos
- organization- and project-level administration endpoints

## Verification

Endpoint presence was verified by shallow-cloning the upstream repo and confirming the
`/models`, `/models/{model}`, `/chat/completions`, `/responses`, and `/embeddings` path
entries in `openapi.yaml`.
