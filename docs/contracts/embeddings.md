# Embeddings

AetherGate supports `POST /v1/embeddings` as an OpenAI-compatible, **non-streaming**
inference operation that uses the same durable authority as Chat Completions and
Responses. It does **not** implement image generation, audio, or vector storage;
it only produces embedding vectors through the existing scheduler/accounting path.

## Supported surface

- `POST /v1/embeddings` (non-streaming only; there is no SSE path).

Request fields:

| Field | Status |
|---|---|
| `model` | supported — a public AetherGate model alias (never an upstream model name) |
| `input` | supported subset — one non-empty string, or a non-empty ordered list of at most **2048** non-empty strings |
| `encoding_format` | supported — `float` and `base64` (default `float` when omitted); explicit `null` rejected |
| `dimensions` | rejected for any value, **including `null`** — `400 unsupported_feature` |
| `user` | rejected for any value, **including `null`** — `400 unsupported_feature` |
| token-ID `input` (`number[]` / `number[][]`) | rejected — `400 unsupported_feature` |
| transport/provider-control fields (`api_base`, `base_url`, `headers`, `extra_headers`, `api_key`, `api_token`, `organization`, `project`, `provider`, `endpoint`, `upstream_model`, `deployment`, `custom_api_base`) | rejected |

Unknown purely additive fields follow the existing tolerance policy; every known
semantic or transport-control field is implemented or explicitly rejected, never
silently dropped. Empty input (empty string, empty list, or a list containing an
empty string) is rejected with `400 unsupported_feature`. Token-ID inputs are
never coerced to strings.

### Bounded input cardinality

The pinned upstream OpenAI OpenAPI reference
(`docs/contracts/openai-compatibility-baseline.md`, commit
`f7bc81b7f30e834bbdb6bcc3370903e49fe7e5b1`), schema `CreateEmbeddingRequest.input`,
declares `minItems: 1` / `maxItems: 2048` on the array input forms. AetherGate
enforces that provider-neutral, OpenAI-compatible request-shape bound as
`MAX_EMBEDDING_INPUTS = 2048` before durable enqueue (a single string is one
input; a list of exactly 2048 succeeds; 2049 returns `400 invalid_request`).
This is **not** an Ollama-specific limit and is enforced independently of the
separate queue payload byte bound.

### Optional vs explicit null

The pinned SDK request types mark `encoding_format`, `dimensions`, and `user` as
*optional but not nullable*: Python `openai==2.54.0` `EmbeddingCreateParams` is
`total=False` (`dimensions: int`, `encoding_format: Literal["float","base64"]`,
`user: str`), and JS `openai@7.32.0` uses `?` without `| null`. Key **absence**
is therefore an accepted omission, while an explicit JSON `null` is a contract
violation that is never silently treated as omission:

- `encoding_format` omitted — accepted (server default `float`);
- `encoding_format` `"float"` / `"base64"` — accepted;
- `encoding_format: null` — rejected `400 invalid_request`;
- `dimensions` omitted — accepted as omission;
- `dimensions` present with **any** value, including `null` — rejected
  `400 unsupported_feature`;
- `user` omitted — accepted as omission;
- `user` present with **any** value, including `null` — rejected
  `400 unsupported_feature`.

Rejected requests are rejected before durable enqueue and never dispatch upstream.

## Response contract

The response is the pinned OpenAI shape:

```json
{
  "object": "list",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}
  ],
  "model": "<public model alias>",
  "usage": {"prompt_tokens": 5, "total_tokens": 5}
}
```

- `data` preserves input order; `index` is the original input position.
- `embedding` is a numeric float array for `encoding_format=float`.
- `embedding` is a base64 string for `encoding_format=base64`, encoded as the
  pinned SDKs expect: IEEE-754 little-endian `float32`, `base64.b64encode(struct.pack("<%df", *vector))`
  (Python) — exactly what `openai@7.32.0` decodes with a native `Float32Array`
  and `openai==2.54.0` decodes with `array.array("f")`.
- `model` is the public AetherGate model alias, never the upstream model name.
- `usage` reports `prompt_tokens` and `total_tokens` only; there is no
  completion-token field.

Provider results are validated at the adapter boundary before serialization:

- the number of provider entries must equal the number of inputs;
- provider entries may arrive in any order, but each entry's `index` must be a
  genuine integer and the sorted index sequence must equal exactly
  `0, 1, …, len(inputs)-1`;
- duplicates, gaps, negative indexes, out-of-range indexes, and non-integer
  indexes are rejected (`ProviderError`) rather than silently renumbered, so a
  malformed provider response can never corrupt input → vector correspondence;
- every vector element is numeric and finite; no NaN/Infinity or
  malformed/non-finite vector is forwarded.

Provider error text never contains input text or vector contents.

## Canonical representation & encryption

Embedding requests and results are operation-aware variants of the existing
encrypted scheduler envelope. The operation discriminator lives **inside**
`payload_encrypted` (never a plaintext DB column), so embedding input text and
returned vectors stay encrypted at rest and never appear in logs, request
metadata, or plaintext columns. Legacy chat payloads/results with no operation
discriminator deserialize as chat exactly as before.

## Capability enforcement

An embedding request requires, at admission **and again at dispatch time**:

- the public **ModelAlias** advertises `embedding` capability;
- the **Provider** advertises `embedding` capability;
- the selected adapter actually supports embeddings.

There is no fallback to a text/image-only route and no inference of embedding
capability from provider kind or endpoint URL. Revoking capability while a
request is queued fails it before it reaches upstream.

## Provider mapping

The pinned LiteLLM `1.104.0` Ollama embedding transport POSTs
`{"model": <upstream model>, "input": [<str>, ...]}` to `{base_url}/api/embed`
and reads `{"embeddings": [[...], ...], "prompt_eval_count": <int>}`. The
adapter never logs input text or vector contents; hidden retries/fallback stay
disabled, and provider errors (404/429/5xx) are sanitized while 429 continues to
feed the shared cooldown authority.

## Quota / budget / accounting semantics

- One embeddings request consumes exactly one **request** unit regardless of the
  number of input strings/vectors.
- Token quota reserves summed **input** tokens; output reservation is zero.
- The embedding input token estimator returns no trustworthy estimate, so an
  enforced token quota or token-priced budget **fails closed**
  (`quota_token_estimator_unavailable` / `budget_token_estimator_unavailable`).
- Request-priced settlement: `request_units=1`, `input_units=0`, `output_units=0`.
- One successful request produces exactly one `UsageRecord` and one signed
  negative `usage_debit` ledger entry; exactly-once settlement under
  fencing/idempotency is unchanged.

## Explicitly unsupported / deferred

- token-ID input;
- `dimensions`;
- `user`;
- batch/async embeddings;
- vector database / RAG / vector storage;
- image generation, audio (STT/TTS), files.

Do not treat this as full OpenAI embeddings compatibility beyond the tested
subset.

## Verification

- `tests/test_embeddings_contract.py` — request/response DTOs, base64 encoding,
  unsupported-field rejection.
- `tests/test_embeddings_api.py` — endpoint + official Python SDK.
- `tests/test_embeddings_scheduler.py` — durable queueing, concurrency,
  encrypted-at-rest, capability enforcement + dispatch-time revalidation, token
  fail-closed, accounting semantics.
- `tests/test_adapter.py` / `tests/test_fake_ollama.py` — real pinned LiteLLM
  transport (`/api/embed`) and deterministic fake upstream.
- `scripts/ci/golden_path.py` + `sdk/js/smoke.mjs` — offline golden path and
  official Python/JS SDK embeddings (default base64 decode included).
