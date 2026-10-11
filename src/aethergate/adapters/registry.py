"""Provider-kind to adapter registry.

Adding a new provider kind does not require changing the API routers: register
the kind here and the routers keep dispatching through :func:`adapter_for`.
"""

from __future__ import annotations

from aethergate.adapters.base import ChatAdapter
from aethergate.adapters.litellm import LiteLLMChatAdapter
from aethergate.errors import UnsupportedProvider

_ADAPTERS: dict[str, ChatAdapter] = {
    "ollama": LiteLLMChatAdapter(),
}


def adapter_for(provider_kind: str) -> ChatAdapter:
    """Return the adapter for ``provider_kind``.

    Raises:
        UnsupportedProvider: no adapter is registered for the kind.
    """
    try:
        return _ADAPTERS[provider_kind]
    except KeyError:
        raise UnsupportedProvider(provider_kind) from None
