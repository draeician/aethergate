"""OpenAI-compatible Chat Completions endpoint (non-streaming + SSE streaming)."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.adapters.base import GenerationParams, Message
from aethergate.api.deps import (
    build_inference_service,
    get_gateway_request_id,
    require_inference_access,
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
from aethergate.errors import ProviderError
from aethergate.inference.service import PreparedDispatch
from aethergate.persistence.db import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

AuthDep = Annotated[None, Depends(require_inference_access)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _usage(prompt: int, completion: int, total: int) -> ChatCompletionUsage:
    return ChatCompletionUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


def _completion_response(
    gateway_id: str, alias: str, content: str, finish_reason: str | None, usage: object
) -> ChatCompletion:
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    total = getattr(usage, "total_tokens", 0) or 0
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
        usage=_usage(prompt, completion, total),
    )


def _chunk_json(
    gateway_id: str,
    created: int,
    alias: str,
    *,
    role: str | None = None,
    content: str | None = None,
    finish_reason: str | None = None,
    usage: object | None = None,
) -> str:
    delta = ChatCompletionChunkDelta(role=role, content=content)
    chunk_usage = None
    if usage is not None:
        chunk_usage = _usage(
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
            getattr(usage, "total_tokens", 0) or 0,
        )
    chunk = ChatCompletionChunk(
        id=gateway_id,
        created=created,
        model=alias,
        choices=[ChatCompletionChunkChoice(index=0, delta=delta, finish_reason=finish_reason)],
        usage=chunk_usage,
    )
    return chunk.model_dump_json()


async def _stream_events(
    prepared: PreparedDispatch, gateway_id: str
) -> AsyncIterator[str]:
    created = int(time.time())
    role_sent = False
    finish_seen = False
    try:
        async for chunk in prepared.adapter.stream(prepared.request, prepared.secret):
            is_first = not role_sent
            if is_first:
                role_sent = True
            content = chunk.content
            if is_first and content is None:
                content = ""
            finish = chunk.finish_reason
            if finish is not None:
                finish_seen = True
            yield (
                "data: "
                + _chunk_json(
                    gateway_id,
                    created,
                    prepared.public_alias,
                    role="assistant" if is_first else None,
                    content=content,
                    finish_reason=finish,
                    usage=chunk.usage,
                )
                + "\n\n"
            )
    except ProviderError as exc:
        logger.error("upstream stream failed mid-flight: %s", exc.message)
        return

    if not finish_seen:
        yield (
            "data: "
            + _chunk_json(
                gateway_id,
                created,
                prepared.public_alias,
                finish_reason="stop",
            )
            + "\n\n"
        )
    yield "data: [DONE]\n\n"


@router.post("/v1/chat/completions", response_model=ChatCompletion)
async def chat_completions(
    body: ChatCompletionRequest,
    request: Request,
    response: Response,
    _: AuthDep,
    session: SessionDep,
) -> ChatCompletion | StreamingResponse:
    gateway_id = get_gateway_request_id(request)
    service = build_inference_service()

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

    prepared = await service.prepare(session, body.model, messages, params)

    if body.stream:
        return StreamingResponse(
            _stream_events(prepared, gateway_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    result = await prepared.adapter.complete(prepared.request, prepared.secret)
    if result.upstream_request_id:
        response.headers["X-Upstream-Request-Id"] = result.upstream_request_id
    return _completion_response(
        gateway_id,
        prepared.public_alias,
        result.content,
        result.finish_reason,
        result.usage,
    )
