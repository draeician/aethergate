from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from litellm import acompletion, token_counter
import os
import json
import time
from pydantic import BaseModel
from typing import List, Optional
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from app.auth import get_current_key
from app.database import get_session
from app.models import APIKey, LLMModel, LLMEndpoint
from app.services.billing import process_transaction
from app.services.limiter import InMemoryRateLimiter

router = APIRouter()

# Rate limiter instances — keyed separately for user-key, model, and endpoint
limiter = InMemoryRateLimiter()

# Default headers — bypass generic WAF / User-Agent blocks
_EXTRA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/121.0.0.0 Safari/537.36",
}


# ---------------------------------------------------------------------------
# GET /v1/models — OpenAI-compatible model listing
# ---------------------------------------------------------------------------

@router.get("/v1/models")
async def list_models(
    api_key: APIKey = Depends(get_current_key),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """OpenAI-compatible model listing endpoint."""
    models = (await session.exec(
        select(LLMModel).where(LLMModel.is_active == True)  # noqa: E712
    )).all()

    return {
        "object": "list",
        "data": [
            {
                "id": m.id,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "aethergate",
            }
            for m in models
        ],
    }


# ---------------------------------------------------------------------------
# POST /v1/chat/completions — Inference Gateway
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "gpt-oss-32k:latest"
    messages: List[Message]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None


def _check_rate_limit(limiter: InMemoryRateLimiter, bucket_key: str, rpm: int) -> None:
    """Check a single RPM bucket. Raises 429 if exceeded."""
    spec = f"{rpm}/m"
    if not limiter.check_limit(bucket_key, spec):
        _, reset_in = limiter.get_remaining(bucket_key, spec)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({rpm} RPM). Retry in {reset_in:.0f}s.",
            headers={"Retry-After": str(int(reset_in))},
        )


def _check_daily_limit(limiter: InMemoryRateLimiter, bucket_key: str, day_max: int) -> None:
    """Check a daily bucket. Raises 429 if exceeded."""
    spec = f"{day_max}/d"
    if not limiter.check_limit(bucket_key, spec):
        _, reset_in = limiter.get_remaining(bucket_key, spec)
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit exceeded ({day_max}/day). Retry in {reset_in:.0f}s.",
            headers={"Retry-After": str(int(reset_in))},
        )


@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    background_tasks: BackgroundTasks,
    api_key: APIKey = Depends(get_current_key),
    session: AsyncSession = Depends(get_session),
):
    """Authenticated & Metered Inference Gateway"""

    # =====================================================================
    # 1. USER-KEY RATE LIMIT (per API key, as before)
    # =====================================================================
    if not limiter.check_limit(api_key.key_hash, api_key.rate_limit_model):
        _, reset_in = limiter.get_remaining(api_key.key_hash, api_key.rate_limit_model)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {reset_in:.0f}s.",
            headers={"Retry-After": str(int(reset_in))},
        )

    # =====================================================================
    # 2. RESOLVE MODEL + ENDPOINT (explicit queries — no lazy loading)
    # =====================================================================
    model_data: LLMModel | None = (await session.exec(
        select(LLMModel).where(LLMModel.id == request.model)
    )).first()

    # Explicit endpoint lookup — selectinload is unreliable in async SQLModel
    endpoint: LLMEndpoint | None = None
    if model_data and model_data.endpoint_id is not None:
        endpoint = (await session.exec(
            select(LLMEndpoint).where(LLMEndpoint.id == model_data.endpoint_id)
        )).first()

    # Determine target model name for LiteLLM
    if model_data:
        target_model = model_data.litellm_name
    elif not request.model.startswith("ollama/"):
        target_model = f"ollama/{request.model}"
    else:
        target_model = request.model

    # Determine target base URL and API key from the endpoint hierarchy
    if endpoint is not None:
        target_api_base = endpoint.base_url
        target_api_key = endpoint.api_key
        print(f"--> [ROUTE] Endpoint #{endpoint.id} '{endpoint.name}' -> {target_api_base}")
    else:
        target_api_base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
        target_api_key = None
        print(f"--> [ROUTE] No endpoint attached, falling back to {target_api_base}")

    # =====================================================================
    # 3. TIERED RATE LIMITING (The Gauntlet)
    # =====================================================================

    # Level 1 — Model-specific override (RPM)
    if model_data and model_data.rpm_limit:
        _check_rate_limit(limiter, f"model:{model_data.id}", model_data.rpm_limit)

    # Level 1 — Model-specific override (Daily)
    if model_data and model_data.day_limit:
        _check_daily_limit(limiter, f"model_day:{model_data.id}", model_data.day_limit)

    # Level 2 — Endpoint global RPM (shared across all models on this endpoint)
    if endpoint and endpoint.rpm_limit:
        _check_rate_limit(limiter, f"ep:{endpoint.id}", endpoint.rpm_limit)

    # Level 3 — Endpoint global daily limit
    if endpoint and endpoint.day_limit:
        _check_daily_limit(limiter, f"ep_day:{endpoint.id}", endpoint.day_limit)

    # =====================================================================
    # 4. BALANCE CHECK
    # =====================================================================
    if api_key.user.balance <= 0:
        raise HTTPException(status_code=403, detail="Insufficient balance. Please top up.")

    ep_name = endpoint.name if endpoint else "default"
    print(f"--> [AUTH] User: {api_key.user.username} | {request.model} -> {target_model} @ {ep_name}")

    # =====================================================================
    # 5. TOKEN COUNTING (pre-flight)
    # =====================================================================
    input_text = " ".join([m.content for m in request.messages])
    try:
        input_tokens = token_counter(model=target_model, messages=[m.dict() for m in request.messages])
    except Exception:
        input_tokens = len(input_text) / 4

    # =====================================================================
    # 6. LiteLLM CALL
    # =====================================================================

    try:
        # --- STREAMING PATH ---
        if request.stream:
            async def billing_stream_wrapper():
                accumulated_content = []
                try:
                    kwargs = dict(
                        model=target_model,
                        messages=[m.dict() for m in request.messages],
                        api_base=target_api_base,
                        stream=True,
                        extra_headers=_EXTRA_HEADERS,
                    )
                    if target_api_key:
                        kwargs["api_key"] = target_api_key
                    response = await acompletion(**kwargs)

                    async for chunk in response:
                        if hasattr(chunk, "choices") and len(chunk.choices) > 0:
                            delta = chunk.choices[0].delta
                            if delta and delta.content:
                                accumulated_content.append(delta.content)

                        if hasattr(chunk, "json"):
                            content = chunk.json()
                        else:
                            content = json.dumps(chunk)
                        yield f"data: {content}\n\n"

                    yield "data: [DONE]\n\n"

                except Exception as stream_e:
                    print(f"!!! Stream Error: {stream_e}")
                    yield f"data: {json.dumps({'error': str(stream_e)})}\n\n"

                finally:
                    full_response_text = "".join(accumulated_content)
                    try:
                        output_tokens = token_counter(model=target_model, text=full_response_text)
                    except Exception:
                        output_tokens = len(full_response_text) / 4

                    print(f"--> [STREAM END] Tokens: {input_tokens} in / {output_tokens} out")
                    import asyncio
                    asyncio.create_task(process_transaction(
                        user_id=api_key.user_id,
                        api_key_id=api_key.id,
                        model_id=request.model,
                        input_tokens=int(input_tokens),
                        output_tokens=int(output_tokens),
                        prompt_text=input_text if api_key.log_content else None,
                        completion_text=full_response_text if api_key.log_content else None,
                    ))

            return StreamingResponse(billing_stream_wrapper(), media_type="text/event-stream")

        # --- NON-STREAMING PATH ---
        else:
            kwargs = dict(
                model=target_model,
                messages=[m.dict() for m in request.messages],
                api_base=target_api_base,
                stream=False,
                extra_headers=_EXTRA_HEADERS,
            )
            if target_api_key:
                kwargs["api_key"] = target_api_key
            response = await acompletion(**kwargs)

            content = response.choices[0].message.content
            try:
                output_tokens = token_counter(model=target_model, text=content)
            except Exception:
                output_tokens = len(content) / 4

            background_tasks.add_task(
                process_transaction,
                user_id=api_key.user_id,
                api_key_id=api_key.id,
                model_id=request.model,
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                prompt_text=input_text if api_key.log_content else None,
                completion_text=content if api_key.log_content else None,
            )

            return response.json()

    except HTTPException:
        raise
    except Exception as e:
        print(f"!!! Request Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
