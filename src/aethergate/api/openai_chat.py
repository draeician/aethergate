"""OpenAI-compatible Chat Completions endpoint (scheduler-admitted).

The API no longer dispatches to the provider adapter directly. It validates and
enqueues a durable, encrypted request, then waits for a terminal result
(non-streaming) or streams the encrypted event sequence (SSE) produced by the
worker process.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from aethergate.adapters.base import GenerationParams, Message, Usage
from aethergate.api.deps import (
    RequestContext,
    dev_request_context,
    get_gateway_request_id,
    require_inference_access,
    scheduler_service,
)
from aethergate.contracts.openai import (
    ChatCompletion,
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    ChatCompletionRequest,
    ChatCompletionUsage,
    ChatMessageResponse,
)
from aethergate.errors import ProviderError, QueueTimeout
from aethergate.scheduler.service import SchedulingService

router = APIRouter(tags=["chat"])

AuthDep = Annotated[None, Depends(require_inference_access)]
ContextDep = Annotated[RequestContext, Depends(dev_request_context)]
ServiceDep = Annotated[SchedulingService, Depends(scheduler_service)]


def _usage(prompt: int, completion: int, total: int) -> ChatCompletionUsage:
    return ChatCompletionUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


def _completion_response(
    gateway_id: str, alias: str, content: str, finish_reason: str | None, usage: Usage
) -> ChatCompletion:
    return ChatCompletion(
        id=gateway_id,
        model=alias,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessageResponse(role="assistant", content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=_usage(usage.prompt_tokens, usage.completion_tokens, usage.total_tokens),
    )


def _chunk_json(
    gateway_id: str,
    created: int,
    alias: str,
    *,
    role: str | None = None,
    content: str | None = None,
    finish_reason: str | None = None,
    usage: Usage | None = None,
) -> str:
    delta = ChatCompletionChunkDelta(role=role, content=content)
    chunk_usage = None
    if usage is not None:
        chunk_usage = _usage(usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
    chunk = ChatCompletionChunk(
        id=gateway_id,
        created=created,
        model=alias,
        choices=[ChatCompletionChunkChoice(index=0, delta=delta, finish_reason=finish_reason)],
        usage=chunk_usage,
    )
    return chunk.model_dump_json()


async def _stream_worker_events(
    service: SchedulingService,
    request_id: str,
    gateway_id: str,
    alias: str,
) -> AsyncIterator[str]:
    """Stream scheduler-produced encrypted events as standard OpenAI SSE."""
    created = int(time.time())
    role_sent = False
    finish_seen = False
    seq = 0
    poll = service.poll_interval

    while True:
        events = await service.stream_events_after(request_id, seq)
        for event in events:
            seq = event["seq"]
            content = event.get("content")
            finish = event.get("finish_reason")
            usage = event.get("usage")
            if usage is not None:
                usage = Usage(**usage)
            is_first = not role_sent
            if is_first:
                role_sent = True
                if content is None:
                    content = ""
            if finish is not None:
                finish_seen = True
            yield (
                "data: "
                + _chunk_json(
                    gateway_id,
                    created,
                    alias,
                    role="assistant" if is_first else None,
                    content=content,
                    finish_reason=finish,
                    usage=usage,
                )
                + "\n\n"
            )

        state = await service.get_state(request_id)
        if state in ("succeeded", "failed", "cancelled", "expired", "outcome_unknown"):
            break
        await asyncio.sleep(poll)

    if not finish_seen:
        yield (
            "data: "
            + _chunk_json(gateway_id, created, alias, finish_reason="stop")
            + "\n\n"
        )
    yield "data: [DONE]\n\n"


@router.post("/v1/chat/completions", response_model=ChatCompletion)
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    response: Response,
    _: AuthDep,
    context: ContextDep,
    service: ServiceDep,
) -> ChatCompletion | StreamingResponse:
    gateway_id = get_gateway_request_id(request)

    messages = [Message(role=m.role, content=m.content) for m in body.messages]
    params = GenerationParams(
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.generation_max_tokens(),
        stop=body.stop,
        frequency_penalty=body.frequency_penalty,
        presence_penalty=body.presence_penalty,
        seed=body.seed,
    )

    enqueued = await service.admit_and_enqueue(
        alias_name=body.model,
        messages=messages,
        params=params,
        stream=body.stream,
        context=context,
    )

    if body.stream:
        return StreamingResponse(
            _stream_worker_events(service, enqueued.request_id, gateway_id, body.model),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = await service.wait_for_terminal(enqueued.request_id, service.terminal_deadline())

    if result.state == "succeeded" and result.content is not None and result.usage is not None:
        if result.upstream_request_id:
            response.headers["X-Upstream-Request-Id"] = result.upstream_request_id
        return _completion_response(
            gateway_id,
            body.model,
            result.content,
            result.finish_reason,
            result.usage,
        )

    if result.state == "expired":
        raise QueueTimeout()
    if result.state == "failed":
        raise ProviderError(result.error_code or "upstream_error")
    if result.state in ("cancelled", "outcome_unknown"):
        raise ProviderError(result.error_code or "upstream_error")
    raise QueueTimeout()
