# Vision / multimodal image input

AetherGate supports **image input -> text output** through the existing durable
generation authority. It does **not** implement image generation. Vision is an
additive data-plane feature: image-bearing requests reuse the same scheduler,
worker, quota/budget accounting, auth, cancellation, and observability path as
text inference.

## Supported surfaces

Both surfaces accept inline images on an image-capable public alias:

- `POST /v1/chat/completions` (non-stream + stream)
- `POST /v1/responses` (non-stream + stream)

### Chat Completions request form (pinned)

`messages[].content` is either a plain string (text-only) or the official
content-parts union:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "describe this image"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,<...>"}}
  ]
}
```

### Responses request form (pinned)

Input items use `input_text` / `input_image` content parts:

```json
{
  "role": "user",
  "content": [
    {"type": "input_text", "text": "describe this image"},
    {"type": "input_image", "image_url": "data:image/png;base64,<...>", "detail": "auto"}
  ]
}
```

`instructions` and the text-only input forms continue to work unchanged.

## Image `detail` semantics (explicit)

The pinned OpenAI contracts expose an image-detail selector, inspected from the
locked official SDKs (not from memory):

| Surface | Field | Allowed values | Default |
|---|---|---|---|
| Chat Completions | `image_url.detail` | `auto` \| `low` \| `high` (`openai==2.54.0` Python `ImageURL`); `openai@7.32.0` JS `ChatCompletionContentPartImage.ImageURL` also `original` | not stated |
| Responses | `input_image.detail` | `low` \| `high` \| `auto` \| `original` (`ResponseInputImageParam`); JS `ResponseInputImageContent.detail?: ImageDetail \| null` | `auto` |

AetherGate policy — **omission is distinct from an explicit `null`**. The pinned
contracts are optional in both surfaces, but only the Responses content-part
type is additionally nullable (`detail?: ImageDetail | null`). AetherGate uses
key-presence detection (a sentinel) to honor that distinction per surface:

- **omitted** detail is accepted on both surfaces (the pinned Responses contract
  defaults to `auto`; Chat's `auto` is the neutral provider default);
- **`auto`** is accepted on both surfaces: it is the neutral/default mode and
  imposes no additional fidelity semantics on the transport;
- **explicit `detail: null`**:
  - **Chat** — rejected (`400 unsupported_feature`). The pinned Chat
    `image_url.detail` is optional-but-non-nullable (Python `ImageURL` is a
    `total=False` TypedDict with `detail: Literal['auto','low','high']`; JS
    `detail?: 'auto'|'low'|'high'|'original'`), so a client-supplied `null` is a
    contract violation and is never silently accepted;
  - **Responses** — tolerated. The pinned Responses content-part type is
    `detail?: ImageDetail | null`, so `null` is a valid value and is treated like
    the omitted/`auto` default. This cross-SDK tolerance is intentional and is
    documented here;
- **`low`**, **`high`**, and **`original`** are recognized non-default semantic
  modes. The pinned LiteLLM/Ollama path has no faithful per-image detail control,
  so they are **explicitly rejected** on both surfaces with structured HTTP `400
  unsupported_feature` (`param` = the precise `image_url.detail` /
  `input_image.detail` path). They are never silently ignored and the request
  never reaches the adapter/upstream.
- any other (unrecognized) value or non-string value is likewise rejected as a
  contract violation on both surfaces.

This preserves the settled compatibility rule: a semantic field is either
implemented faithfully or explicitly rejected, never silently dropped.

## Image source policy — inline data only

Only the official inline `data:` URL representation is accepted, and only for
proven media types:

- `image/png`
- `image/jpeg`
- `image/webp`

Explicitly rejected (structured HTTP `400`):

- remote `http://` / `https://` image URLs — **never fetched** (no SSRF/egress
  through client image input);
- `file_id` / uploaded-file references;
- provider-local file paths and arbitrary URI schemes;
- SVG and any other media type not proven safe/supported;
- malformed data URLs, bad/non-canonical base64, empty payloads, and
  MIME/signature mismatches.

Image bytes are validated by strict base64 decoding plus a per-format
magic-byte signature check. No image parser/decompression library (e.g. Pillow)
is invoked on untrusted input. Signatures are exact:

- PNG: `89 50 4E 47 0D 0A 1A 0A`;
- JPEG: `FF D8 FF`;
- WebP: a RIFF container whose form-type fourcc at bytes 8:12 is `WEBP`
  (`bytes 0:4 == b"RIFF"` **and** `bytes 8:12 == b"WEBP"` with a sufficient
  header length). RIFF alone is **not** accepted (RIFF/WAVE, RIFF/AVI, and
  arbitrary RIFF payloads are rejected).

## Explicit resource limits

Configurable (`AETHERGATE_*`) conservative internal-Alpha defaults, enforced
before durable enqueue:

| Limit | Env var | Default |
|---|---|---|
| Max images per request | `AETHERGATE_MAX_IMAGES_PER_REQUEST` | 4 |
| Max decoded bytes per image | `AETHERGATE_MAX_IMAGE_BYTES` | 8 MiB |
| Max total decoded image bytes per request | `AETHERGATE_MAX_TOTAL_IMAGE_BYTES` | 16 MiB |
| Max serialized scheduler payload bytes | `AETHERGATE_MAX_QUEUE_PAYLOAD_BYTES` | 24 MiB |

The per-request payload-byte bound applies to **both** text-only and
image-bearing payloads and accounts for base64 expansion. A request that cannot
fit is rejected with `400 payload_too_large` (or `400 image_limit_exceeded`)
before any provider dispatch. Limits are enforced in the application, not only
at the reverse proxy.

## Canonical representation & encryption

Chat and Responses both map into the **same** canonical provider-neutral
message representation: a message carries either legacy text `content` or an
ordered tuple of `TextContentPart` / `ImageContentPart`. Provider adapters
receive canonical content parts, never Chat/Responses DTOs; part order is
preserved. Image bytes remain inside the encrypted scheduler payload
(`payload_encrypted`) while queued — there is no plaintext image column,
metadata field, or log line.

Legacy text-only encrypted payloads (pre-AGV2-028) remain readable and
deserialize exactly as before.

## Capability enforcement

A request containing at least one image requires:

- the public **ModelAlias** to advertise `image` capability, and
- the **Provider** to advertise `image` capability.

Otherwise the request is rejected before upstream dispatch with no fallback to a
text-only route/model and no silent image removal. Image capability is
re-checked at dispatch time, so revoking it while a request is queued fails the
request before it reaches upstream. Text-only requests are unaffected by the
image-capability gate.

Configure vision routes with **both** `text` and `image`.

## Provider mapping

The pinned LiteLLM adapter maps canonical image parts into the official OpenAI
chat content-parts union. The pinned LiteLLM `1.104.0` ollama transformer
extracts the inline image into the pure-base64 `images` field of the Ollama
`/api/generate` (and `/api/chat`) request. The adapter never logs image
bytes/base64. No new provider kind is introduced; the admin-configured endpoint
destination and upstream model remain authoritative, and hidden retries/fallback
stay disabled.

## Token quota / budget behavior

Request quotas are unchanged: one gateway request is one request unit. Input
token estimation for image-bearing requests has **no** provider/model-specific,
defensible estimator, so an enforced **token** quota or token-priced budget
**fails closed** (`quota_token_estimator_unavailable` /
`budget_token_estimator_unavailable`) rather than treating bytes, base64 length,
or dimensions as fabricated token counts. Request-priced accounting works
normally, and settlement trusts measured upstream usage only where the
provider/LiteLLM reports it.

## Explicitly unsupported / deferred

- remote image URL fetching;
- files / `file_id` / object storage;
- image generation / image output;
- audio (STT/TTS);
- tools / function calling / MCP / computer use;
- video;
- unsupported image media types (including SVG).

Do not treat this as full multimodal/OpenAI compatibility.

## Verification

- `tests/test_vision_images.py` — inline decode/validation/limits, canonical
  mapping, and legacy payload back-compat.
- `tests/test_vision_scheduler.py` — durable queueing, six/two concurrency,
  encrypted-at-rest, capability enforcement + dispatch-time revalidation,
  token fail-closed.
- `tests/test_vision_api.py` — Chat + Responses non-stream/stream and official
  Python SDK vision.
- `tests/test_fake_ollama.py` / `tests/test_adapter.py` — real pinned LiteLLM
  transport reaches the image path; adapter wire mapping.
- `scripts/ci/golden_path.py` + `frontend/e2e/alpha-golden.spec.ts` +
  `sdk/js/smoke.mjs` — offline golden path and official Python/JS SDK vision.
