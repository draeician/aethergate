"""OpenAI-compatible Responses API endpoint (text subset, scheduler-admitted).

The endpoint translates the pinned Responses text-generation request into the
**existing** durable generation authority (canonical ``Message[] +
GenerationParams`` -> ``SchedulingService.admit_and_enqueue``). It does not build
a second scheduler, worker, accounting path, or provider adapter.

- Non-streaming: wait for the durable terminal result and translate it into a
  typed :class:`ResponseObject` (never a Chat Completions object).
- Streaming: translate the worker's encrypted ``StreamEvent`` records into the
  pinned Responses semantic SSE events; ``[DONE]`` is not emitted.

Unsupported semantic features are rejected during request validation with a
structured HTTP 400 ``unsupported_feature`` error. Client disconnect propagates
to the same durable cancellation semantics as Chat.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from aethergate.adapters.base import GenerationParams, Message, Usage
from aethergate.api.deps import (
    RequestContext,
    get_gateway_request_id,
    get_image_limits,
    request_context,
    scheduler_service,
)
from aethergate.contracts.responses import (
    ResponseCompletedEvent,
    ResponseContentPartAddedEvent,
    ResponseContentPartDoneEvent,
    ResponseCreatedEvent,
    ResponseErrorDetail,
    ResponseFailedEvent,
    ResponseInProgressEvent,
    ResponseObject,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessageItem,
    ResponseOutputText,
    ResponsesRequest,
    ResponseTextDeltaEvent,
    ResponseTextDoneEvent,
    ResponseUsage,
)
from aethergate.errors import ProviderError, QueueTimeout
from aethergate.scheduler.service import SchedulingService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["responses"])

ContextDep = Annotated[RequestContext, Depends(request_context)]
ServiceDep = Annotated[SchedulingService, Depends(scheduler_service)]

_TERMINAL_STATES = ("succeeded", "failed", "cancelled", "expired", "outcome_unknown")


def _usage(usage: Usage) -> ResponseUsage:
    return ResponseUsage(
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )


def _output_message(item_id: str, text: str, status: str) -> ResponseOutputMessageItem:
    return ResponseOutputMessageItem(
        id=item_id,
        status=status,  # type: ignore[arg-type]
        content=[ResponseOutputText(text=text)],
    )


def _response_object(
    response_id: str,
    model: str,
    *,
    status: str,
    text: str | None,
    usage: Usage | None,
    error: ResponseErrorDetail | None = None,
) -> ResponseObject:
    output: list[ResponseOutputMessageItem] = []
    if text is not None:
        output.append(_output_message(f"msg_{response_id}", text, "completed"))
    return ResponseObject(
        id=response_id,
        status=status,  # type: ignore[arg-type]
        model=model,
        output=output,
        usage=_usage(usage) if usage is not None else None,
        error=error,
    )


def _sse(event: Any) -> str:
    payload = event.model_dump_json()
    return f"event: {event.type}\ndata: {payload}\n\n"


def _schedule_durable_cancel(service: SchedulingService, request_id: str) -> None:
    """Same detached durable-cancellation as Chat (survives ASGI cancellation)."""

    async def _cancel() -> None:
        try:
            await service.request_cancellation(request_id)
        except Exception:  # noqa: BLE001 - cancellation is best-effort on teardown
            logger.exception("durable cancellation failed for request=%s", request_id)

    asyncio.get_running_loop().create_task(_cancel())


async def _stream_response_events(
    service: SchedulingService,
    request_id: str,
    response_id: str,
    model: str,
    request: Request,
) -> AsyncIterator[str]:
    """Translate durable stream events into pinned Responses SSE events.

    Emits the standard lifecycle/text event sequence with stable response/output/
    content IDs and indices. A failed/cancelled/expired/outcome_unknown stream
    terminates with ``response.failed`` and never emits a synthetic success. No
    ``[DONE]`` marker is appended (the pinned Responses surface does not use one).
    """
    item_id = f"msg_{response_id}"
    seq = 0

    def next_seq() -> int:
        nonlocal seq
        value = seq
        seq += 1
        return value

    final_state: str | None = None
    text_parts: list[str] = []
    usage: Usage | None = None
    poll = service.poll_interval
    cursor = 0
    disconnected = False

    try:
        in_progress = _response_object(
            response_id, model, status="in_progress", text=None, usage=None
        )
        yield _sse(
            ResponseCreatedEvent(sequence_number=next_seq(), response=in_progress)
        )
        yield _sse(
            ResponseInProgressEvent(sequence_number=next_seq(), response=in_progress)
        )
        yield _sse(
            ResponseOutputItemAddedEvent(
                sequence_number=next_seq(),
                output_index=0,
                item=ResponseOutputMessageItem(
                    id=item_id,
                    status="in_progress",
                    content=[],
                ),
            )
        )
        yield _sse(
            ResponseContentPartAddedEvent(
                sequence_number=next_seq(),
                output_index=0,
                item_id=item_id,
                content_index=0,
                part=ResponseOutputText(text=""),
            )
        )

        while True:
            if await request.is_disconnected():
                disconnected = True
                break
            events = await service.stream_events_after(request_id, cursor)
            for event in events:
                cursor = event["seq"]
                if event.get("usage") is not None:
                    usage = Usage(**event["usage"])
                content = event.get("content")
                if content:
                    text_parts.append(content)
                    yield _sse(
                        ResponseTextDeltaEvent(
                            sequence_number=next_seq(),
                            output_index=0,
                            item_id=item_id,
                            content_index=0,
                            delta=content,
                        )
                    )

            state = await service.get_state(request_id)
            if state in _TERMINAL_STATES:
                final_state = state
                break
            await asyncio.sleep(poll)

        full_text = "".join(text_parts)
        if final_state == "succeeded":
            yield _sse(
                ResponseTextDoneEvent(
                    sequence_number=next_seq(),
                    output_index=0,
                    item_id=item_id,
                    content_index=0,
                    text=full_text,
                )
            )
            yield _sse(
                ResponseContentPartDoneEvent(
                    sequence_number=next_seq(),
                    output_index=0,
                    item_id=item_id,
                    content_index=0,
                    part=ResponseOutputText(text=full_text),
                )
            )
            yield _sse(
                ResponseOutputItemDoneEvent(
                    sequence_number=next_seq(),
                    output_index=0,
                    item=_output_message(item_id, full_text, "completed"),
                )
            )
            completed = _response_object(
                response_id, model, status="completed", text=full_text, usage=usage
            )
            yield _sse(
                ResponseCompletedEvent(sequence_number=next_seq(), response=completed)
            )
        else:
            failed = _response_object(
                response_id,
                model,
                status="failed",
                text=full_text or None,
                usage=usage,
                error=ResponseErrorDetail(
                    code="response_failed",
                    message="The response could not be completed.",
                ),
            )
            yield _sse(
                ResponseFailedEvent(sequence_number=next_seq(), response=failed)
            )
    except (asyncio.CancelledError, GeneratorExit):
        # The response cycle was cancelled (client disconnect or server shutdown)
        # or the stream was closed early. Ensure the durable request cannot later
        # dispatch. A transient server-side error is not a client disconnect and
        # must not cancel durably admitted work, so it is not caught here.
        _schedule_durable_cancel(service, request_id)
        raise
    finally:
        if final_state is None and disconnected:
            # The stream ended because the client disconnected (detected directly)
            # without reaching a terminal state. Ensure the durable request cannot
            # later dispatch.
            _schedule_durable_cancel(service, request_id)


@router.post("/v1/responses", response_model=ResponseObject)
async def create_response(
    body: ResponsesRequest,
    request: Request,
    response: Response,
    context: ContextDep,
    service: ServiceDep,
) -> ResponseObject | StreamingResponse:
    gateway_id = get_gateway_request_id(request)
    response_id = f"resp_{gateway_id}"

    messages: list[Message] = body.to_canonical_messages(get_image_limits())
    params = GenerationParams(
        temperature=body.temperature,
        top_p=body.top_p,
        max_tokens=body.max_output_tokens,
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
            _stream_response_events(
                service, enqueued.request_id, response_id, body.model, request
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        result = await service.wait_for_terminal(enqueued.request_id, enqueued.expires_at)
    except asyncio.CancelledError:
        # The response cycle was cancelled (client disconnect or server shutdown)
        # before a terminal result. A detached task survives the cancellation and
        # durably cancels the admitted request so it can never dispatch after the
        # client has given up. A transient server-side error (for example
        # PostgreSQL raising ``OperationalError``) is NOT a client disconnect and
        # must not destroy durably admitted work, so it is not caught here.
        _schedule_durable_cancel(service, enqueued.request_id)
        raise

    if result.state == "succeeded":
        if result.upstream_request_id:
            response.headers["X-Upstream-Request-Id"] = result.upstream_request_id
        return _response_object(
            response_id,
            body.model,
            status="completed",
            text=result.content or "",
            usage=result.usage,
        )

    if result.state == "expired":
        raise QueueTimeout()
    if result.state == "failed":
        raise ProviderError(result.error_code or "upstream_error")
    if result.state in ("cancelled", "outcome_unknown"):
        raise ProviderError(result.error_code or "upstream_error")
    raise QueueTimeout()
