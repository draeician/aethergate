# Responses API Compatibility (AGV2-025)

Living document. Records exactly what AetherGate implements of the OpenAI
Responses API (`POST /v1/responses`), the canonical mapping into the existing
durable generation authority, and every semantic feature that is explicitly
rejected. It does **not** claim full Responses compatibility.

## Pinned source of truth

- Upstream OpenAPI baseline (see `openai-compatibility-baseline.md`).
- Locked official Python SDK: **`openai==2.54.0`**
  (`openai.types.responses.*`, `openai.resources.responses`).
- Request/response/event shapes are derived from those pinned types, not memory.

## Scope

This milestone implements the **text + inline-image input subset** (text
output) only — string/text input plus inline `input_image` (see
`docs/contracts/vision.md`). It reuses the existing provider-neutral path end to
end:

```
ResponsesRequest (protocol DTO)
  -> canonical Message[] + GenerationParams
  -> SchedulingService.admit_and_enqueue        (existing scheduler)
  -> worker claim_and_reserve / run_complete / run_stream (existing worker)
  -> existing provider adapter (LiteLLM -> Ollama)
  -> existing quotas / budgets / PriceSnapshot / UsageRecord / usage_debit ledger
  -> Responses-specific response / SSE translation (API side only)
```

There is **no** second scheduler, worker, accounting path, or provider adapter,
and **no** hidden fallback.

## Supported request fields (text + inline-image subset)

| Field | Behaviour |
|---|---|
| `model` | Public `ModelAlias.name` only. Never a provider/upstream/endpoint name. |
| `input` (string) | Maps to a single `user` message. |
| `input` (array of text message items) | `{role, content}` where `content` is a string or a list of `input_text`/`output_text` parts. `user`/`assistant`/`system`/`developer` roles are accepted; `developer` maps to canonical `system`. |
| `instructions` | Leading highest-precedence `system` message (before all input items). Never dropped. |
| `max_output_tokens` | Feeds the canonical `GenerationParams.max_tokens` output bound (same reservation logic as Chat). |
| `temperature` | 0..2, canonical `GenerationParams.temperature`. |
| `top_p` | 0..1, canonical `GenerationParams.top_p`. |
| `stream` | Non-stream (typed `ResponseObject`) or Responses SSE event stream. |
| `input_text` | Text content part; maps to a canonical text part. |
| `input_image` (inline) | Image content part with an inline `data:` `image_url` (PNG/JPEG/WebP); maps to a canonical image part. Remote URLs, `file_id`, and other sources are rejected. `detail` omitted/`auto` is accepted; `low`/`high`/`original` are rejected `400 unsupported_feature`. See `docs/contracts/vision.md`. |

Unknown purely-additive fields follow the repository's additive-field tolerance
policy and are ignored.

## Canonical mapping semantics

- `instructions` -> a single leading `system` message. Ordering/precedence is
  preserved: instructions precede all input items in the canonical list.
- `developer` role -> canonical `system`. The current Ollama adapter path cannot
  faithfully distinguish a Responses `developer` instruction from `system`
  semantics, so both share the highest-precedence instruction slot rather than
  dropping the instruction. This mapping is proven by
  `tests/test_responses_contracts.py::test_developer_role_maps_to_system_and_is_not_dropped`
  and the live/golden-path `instructions` proof.

## Explicitly unsupported (rejected, never silently dropped)

Every known semantic field below returns HTTP **400** with a structured error
envelope: `type=invalid_request_error`, `code=unsupported_feature`, and `param`
set to the offending field.

- `tools`, `tool_choice`, `max_tool_calls`, `parallel_tool_calls` (function /
  custom / MCP / computer-use / built-in tools);
- audio/file input content parts (`input_audio`, `input_file`) and output audio;
- image input *sources* other than an inline `data:` URL: remote `http(s)://`
  image URLs (never fetched), `file_id`/file references, and unsupported media
  types (including SVG). Inline `input_image` (`data:` PNG/JPEG/WebP) **is**
  supported on image-capable aliases; see `docs/contracts/vision.md`;
- structured output / JSON-schema `text.format`;
- `reasoning` controls and reasoning items;
- `previous_response_id` and `conversation` state;
- `background`;
- stored-response semantics (`store=true`);
- `include`, `top_logprobs`;
- `truncation` other than the default `disabled`;
- `metadata`, `moderation`, `service_tier`, `prompt`, `prompt_cache_*`,
  `safety_identifier`, `user`, `context_management`, `stream_options`;
- provider/endpoint/base-url/header/secret transport overrides
  (`api_base`, `base_url`, `headers`, `extra_headers`, `api_key`, `api_token`,
  `organization`, `project`, `provider`, `endpoint`, `upstream_model`,
  `deployment`, `custom_api_base`).

Nothing in this list is a client-selectable provider, account, endpoint,
upstream model, upstream URL, or transport secret. `model` always means the
public `ModelAlias.name`.

## Non-streaming response object

A completed response is a typed `ResponseObject` (never a Chat Completions
object). It matches the pinned schema sufficiently for `openai==2.54.0` to parse
naturally, including: `id` (`resp_<gateway id>`), `object=response`,
`created_at`, `status=completed`, `model` = public alias, an `output` message
item (`type=message`, `role=assistant`, `content=[{type: output_text, text}]`),
`usage` (input/output/total tokens with details objects), the required
`parallel_tool_calls`/`tool_choice`/`tools` fields, and `error`/
`incomplete_details` = null. The SDK's `response.output_text` convenience
property returns the generated text.

Gateway and upstream request IDs stay distinct: the gateway ID is the response
`id`; `X-Upstream-Request-Id` is emitted only where already safe/established.

## Streaming event sequence

Ordered Responses SSE events over the durable encrypted `StreamEvent` records.
Each event is `event: <type>\ndata: <json>\n\n` with a monotonic
`sequence_number`. No proprietary queue events and **no `[DONE]` marker** (the
pinned Responses surface does not use one).

Successful text response:

1. `response.created`
2. `response.in_progress`
3. `response.output_item.added` (output_index 0)
4. `response.content_part.added` (content_index 0)
5. `response.output_text.delta` (one per content chunk, ordered)
6. `response.output_text.done`
7. `response.content_part.done`
8. `response.output_item.done`
9. `response.completed` (carries the final `ResponseObject` and `usage`)

Response/output/content IDs and indices are stable across the stream. A
failed/cancelled/expired/outcome_unknown stream terminates with
`response.failed` and never emits a synthetic success.

## Cancellation

Client disconnect uses the same durable cancellation semantics as Chat: a
queued/reserved request is terminally cancelled and never dispatches; a
dispatched/streaming request is flagged so the owning worker settles it safely.
No fallback execution occurs.

## Authentication

Identical to Chat: Bearer `agk_...` inference-audience credential, scope
`inference:invoke`, project/principal attribution persisted, credential
revalidated immediately before dispatch (a revoked queued credential never
reaches upstream). Inference auth bypass is `false` in all acceptance proofs.

## Accounting

Responses participates in the existing endpoint slots, request/token quotas,
cooldown, project budget, immutable `PriceSnapshot`, `UsageRecord`, signed
`usage_debit` ledger entry, and exactly-once settlement. `max_output_tokens`
feeds the same canonical output-token reservation bound as Chat. Fail-closed
token/budget behaviour is preserved where no defensible tokenizer estimate
exists.
