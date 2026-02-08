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
from app.models import APIKey, LLMModel
from app.services.billing import process_transaction
from app.services.limiter import InMemoryRateLimiter

router = APIRouter()

# Global rate limiter instance — persists across requests within this worker
limiter = InMemoryRateLimiter()

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


class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "gpt-oss-32k:latest" # Default
    messages: List[Message]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None

@router.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    background_tasks: BackgroundTasks,
    api_key: APIKey = Depends(get_current_key),
    session: AsyncSession = Depends(get_session),
):
    """
    Authenticated & Metered Inference Gateway
    """
    # 1. Rate Limit Check (FIRST — before any work)
    if not limiter.check_limit(api_key.key_hash, api_key.rate_limit_model):
        remaining, reset_in = limiter.get_remaining(api_key.key_hash, api_key.rate_limit_model)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {reset_in:.0f}s.",
            headers={"Retry-After": str(int(reset_in))},
        )

    # 2. Config & Routing — resolve per-model endpoint from DB
    model_data = (await session.exec(
        select(LLMModel).where(LLMModel.id == request.model)
    )).first()

    if model_data and model_data.api_base:
        # Per-model override (e.g. OpenAI, Anthropic, a second Ollama host)
        target_api_base = model_data.api_base
        target_model = model_data.litellm_name
    else:
        # Default: fall back to the global Ollama backend
        target_api_base = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")
        if model_data:
            target_model = model_data.litellm_name
        elif not request.model.startswith("ollama/"):
            target_model = f"ollama/{request.model}"
        else:
            target_model = request.model

    # Per-model provider API key (e.g. OpenAI sk-... key)
    target_api_key = model_data.api_key if model_data and model_data.api_key else None

    # Default headers — bypass generic WAF / User-Agent blocks
    extra_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/121.0.0.0 Safari/537.36",
    }

    # 3. Check Balance (Soft Limit)
    # We allow them to go negative on the current request, but block the next one.
    if api_key.user.balance <= 0:
        raise HTTPException(status_code=403, detail="Insufficient balance. Please top up.")

    print(f"--> [AUTH] User: {api_key.user.username} | Model: {request.model} -> {target_model} @ {target_api_base}")

    # 3. Calculate Input Tokens (Pre-flight)
    # We count them now to fail fast if the prompt is too huge (future feature)
    input_text = " ".join([m.content for m in request.messages])
    try:
        input_tokens = token_counter(model=target_model, messages=[m.dict() for m in request.messages])
    except:
        # Fallback if model not found in tokenizer, estimate 4 chars = 1 token
        input_tokens = len(input_text) / 4

    try:
        # --- STREAMING PATH ---
        if request.stream:
            async def billing_stream_wrapper():
                accumulated_content = []
                try:
                    completion_kwargs = dict(
                        model=target_model,
                        messages=[m.dict() for m in request.messages],
                        api_base=target_api_base,
                        stream=True,
                        extra_headers=extra_headers,
                    )
                    if target_api_key:
                        completion_kwargs["api_key"] = target_api_key
                    response = await acompletion(**completion_kwargs)
                    async for chunk in response:
                        # 1. Extract content to accumulate
                        if hasattr(chunk, "choices") and len(chunk.choices) > 0:
                            delta = chunk.choices[0].delta
                            if delta and delta.content:
                                accumulated_content.append(delta.content)
                        
                        # 2. Yield to user immediately
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
                    # --- BILLING TRIGGER (Post-Stream) ---
                    full_response_text = "".join(accumulated_content)
                    
                    # Calculate Output Tokens
                    try:
                        output_tokens = token_counter(model=target_model, text=full_response_text)
                    except:
                        output_tokens = len(full_response_text) / 4

                    # Queue DB Write (Fire and Forget)
                    print(f"--> [STREAM END] Tokens: {input_tokens} in / {output_tokens} out")
                    # Note: We can't use FastAPI 'BackgroundTasks' inside a generator easily.
                    # We must call the service directly or use an async task.
                    # For stability, we await it here (it's fast) or spawn a task.
                    import asyncio
                    asyncio.create_task(process_transaction(
                        user_id=api_key.user_id,
                        api_key_id=api_key.id,
                        model_id=request.model, # Use the ID they asked for (e.g. "qwen2.5:3b")
                        input_tokens=int(input_tokens),
                        output_tokens=int(output_tokens),
                        prompt_text=input_text if api_key.log_content else None,
                        completion_text=full_response_text if api_key.log_content else None
                    ))

            return StreamingResponse(billing_stream_wrapper(), media_type="text/event-stream")

        # --- NON-STREAMING PATH ---
        else:
            completion_kwargs = dict(
                model=target_model,
                messages=[m.dict() for m in request.messages],
                api_base=target_api_base,
                stream=False,
                extra_headers=extra_headers,
            )
            if target_api_key:
                completion_kwargs["api_key"] = target_api_key
            response = await acompletion(**completion_kwargs)
            
            # Calculate Output Tokens
            content = response.choices[0].message.content
            try:
                output_tokens = token_counter(model=target_model, text=content)
            except:
                output_tokens = len(content) / 4

            # Queue DB Write
            background_tasks.add_task(
                process_transaction,
                user_id=api_key.user_id,
                api_key_id=api_key.id,
                model_id=request.model,
                input_tokens=int(input_tokens),
                output_tokens=int(output_tokens),
                prompt_text=input_text if api_key.log_content else None,
                completion_text=content if api_key.log_content else None
            )
            
            return response.json()

    except Exception as e:
        print(f"!!! Request Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
