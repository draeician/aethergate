"""Provider-adapter boundary.

Adapters translate a fully resolved route into an upstream completion call.
AetherGate resolves the endpoint, account, provider, and upstream model before
entering the adapter; adapters never receive a client-controlled destination or
credential.
"""

from __future__ import annotations

from aethergate.adapters.base import (
    ChatAdapter,
    ChatRequest,
    CompletionResult,
    GenerationParams,
    Message,
    StreamChunk,
    Usage,
)
from aethergate.adapters.registry import adapter_for

__all__ = [
    "ChatAdapter",
    "ChatRequest",
    "CompletionResult",
    "GenerationParams",
    "Message",
    "StreamChunk",
    "Usage",
    "adapter_for",
]
