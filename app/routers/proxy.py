from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from litellm import completion
import os
import json
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()

# 1. Load Config
OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")

# 2. Define Request Schema
class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "gpt-oss-32k:latest" # Default to your model
    messages: List[Message]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None

@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """
    Inference Gateway:
    - User asks for 'gpt-4' (or anything).
    - We FORCE it to use your local 'gpt-oss-32k' on Ollama.
    """
    
    # Force the model to be your local Ollama model
    # LiteLLM format: "ollama/<model_name_in_ollama>"
    target_model = f"ollama/{request.model}" 
    
    # If the user sends "gpt-4", we can optionally alias it here:
    if request.model == "gpt-4" or request.model == "gpt-3.5-turbo":
         target_model = "ollama/gpt-oss-32k:latest"

    try:
        if request.stream:
            def iter_response():
                response = completion(
                    model=target_model,
                    messages=[m.dict() for m in request.messages],
                    api_base=OLLAMA_API_BASE, # Points to 'nomnom'
                    stream=True,
                )
                for chunk in response:
                    yield f"data: {json.dumps(chunk.json())}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(iter_response(), media_type="text/event-stream")

        else:
            response = completion(
                model=target_model,
                messages=[m.dict() for m in request.messages],
                api_base=OLLAMA_API_BASE,
                stream=False
            )
            return response.json()

    except Exception as e:
        print(f"Inference Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
