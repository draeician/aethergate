"""OpenAI-compatible Embeddings endpoint (scheduler-admitted, non-streaming).

The API does not dispatch to the provider adapter directly. It validates and
enqueues a durable, encrypted embedding request, then waits for a terminal
result produced by the worker process. Embeddings are never streamed: there is
no SSE path and no fabricated first-token/TTFT sample.

Vectors stay inside the encrypted result envelope until the API response is
built; they are never logged or written to plaintext.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from aethergate.adapters.base import Usage
from aethergate.api.deps import (
    RequestContext,
    get_gateway_request_id,
    request_context,
    scheduler_service,
)
from aethergate.contracts.embeddings import (
    CreateEmbeddingResponse,
    EmbeddingCreateRequest,
    EmbeddingData,
    EmbeddingUsage,
    encode_base64_float32,
)
from aethergate.errors import ProviderError, QueueTimeout
from aethergate.scheduler.service import SchedulingService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["embeddings"])

ContextDep = Annotated[RequestContext, Depends(request_context)]
ServiceDep = Annotated[SchedulingService, Depends(scheduler_service)]


def _schedule_durable_cancel(service: SchedulingService, request_id: str) -> None:
    """Schedule a durable cancellation that survives ASGI response cancellation."""

    async def _cancel() -> None:
        try:
            await service.request_cancellation(request_id)
        except Exception:  # noqa: BLE001 - cancellation is best-effort on teardown
            logger.exception("durable cancellation failed for request=%s", request_id)

    asyncio.get_running_loop().create_task(_cancel())


def _embedding_response(
    gateway_id: str,
    alias: str,
    vectors: list[list[float]],
    usage: Usage,
    *,
    encoding: str,
) -> CreateEmbeddingResponse:
    data = []
    for index, vector in enumerate(vectors):
        embedding: list[float] | str
        if encoding == "base64":
            embedding = encode_base64_float32(vector)
        else:
            embedding = vector
        data.append(EmbeddingData(index=index, embedding=embedding))
    return CreateEmbeddingResponse(
        data=data,
        model=alias,
        usage=EmbeddingUsage(
            prompt_tokens=usage.prompt_tokens, total_tokens=usage.total_tokens
        ),
    )


@router.post("/v1/embeddings", response_model=CreateEmbeddingResponse)
async def embeddings(
    body: EmbeddingCreateRequest,
    request: Request,
    response: Response,
    context: ContextDep,
    service: ServiceDep,
) -> CreateEmbeddingResponse:
    inputs = body.to_canonical_inputs()
    encoding = body.encoding_format or "float"

    enqueued = await service.admit_embedding_and_enqueue(
        alias_name=body.model,
        inputs=inputs,
        context=context,
    )

    try:
        result = await service.wait_for_terminal_embedding(
            enqueued.request_id, enqueued.expires_at
        )
    except asyncio.CancelledError:
        # The response cycle was cancelled (client disconnect or server shutdown)
        # before a terminal result. A detached task survives the cancellation and
        # durably cancels the admitted request so it can never dispatch after the
        # client has given up. A transient server-side error (for example
        # PostgreSQL raising ``OperationalError``) is NOT a client disconnect and
        # must not destroy durably admitted work, so it is not caught here.
        _schedule_durable_cancel(service, enqueued.request_id)
        raise

    if (
        result.state == "succeeded"
        and result.vectors is not None
        and result.usage is not None
    ):
        if result.upstream_request_id:
            response.headers["X-Upstream-Request-Id"] = result.upstream_request_id
        return _embedding_response(
            get_gateway_request_id(request),
            body.model,
            result.vectors,
            result.usage,
            encoding=encoding,
        )

    if result.state == "expired":
        raise QueueTimeout()
    if result.state == "failed":
        raise ProviderError(result.error_code or "upstream_error")
    if result.state in ("cancelled", "outcome_unknown"):
        raise ProviderError(result.error_code or "upstream_error")
    raise QueueTimeout()
