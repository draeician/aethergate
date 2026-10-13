"""Real-transport redirect-escape regression for the pinned LiteLLM adapter.

Empirically, pinned LiteLLM 1.104.0 creates its httpx clients with
``follow_redirects=True``, so a provider ``3xx`` would let an allowed origin
steer the client to a different (potentially internal) destination. These tests
use a real local HTTP origin that redirects to a canary target and prove the
adapter's completion *and* embedding transports never contact the canary for any
redirect class.

No network access beyond loopback is used, and no provider credentials are
required.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from aethergate.adapters.base import ChatRequest, EmbeddingRequest, GenerationParams, Message
from aethergate.adapters.litellm import LiteLLMChatAdapter
from aethergate.errors import ProviderError

REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class _Canary:
    def __init__(self) -> None:
        self.hits: list[str] = []


def _start_servers(redirect_status: int) -> tuple[str, _Canary, list[ThreadingHTTPServer]]:
    canary = _Canary()

    class CanaryHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            canary.hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            canary.hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:  # noqa: D102
            pass

    canary_server = ThreadingHTTPServer(("127.0.0.1", 0), CanaryHandler)
    canary_port = canary_server.server_address[1]

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_response(redirect_status)
            self.send_header("Location", f"http://127.0.0.1:{canary_port}/canary")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:  # noqa: D102
            pass

    redirect_server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    origin_port = redirect_server.server_address[1]

    servers = [canary_server, redirect_server]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{origin_port}", canary, servers


@pytest.fixture(params=REDIRECT_STATUSES)
def redirect_fixture(request):
    origin, canary, servers = _start_servers(request.param)
    try:
        yield origin, canary
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def _chat_request(base_destination: str) -> ChatRequest:
    return ChatRequest(
        provider_kind="ollama",
        base_destination=base_destination,
        upstream_model="test-model",
        messages=(Message(role="user", content="hi"),),
        params=GenerationParams(),
        timeout_seconds=10.0,
    )


def _embedding_request(base_destination: str) -> EmbeddingRequest:
    return EmbeddingRequest(
        provider_kind="ollama",
        base_destination=base_destination,
        upstream_model="test-model",
        inputs=("hello",),
        timeout_seconds=10.0,
    )


def test_redirect_fixture_would_be_followed_by_a_default_client(redirect_fixture):
    """Positive control: the fixture redirect is real and a default client follows it."""
    origin, canary = redirect_fixture

    async def _run() -> None:
        async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
            await client.post(origin)

    import asyncio

    asyncio.run(_run())
    assert canary.hits == ["/canary"]


async def test_completion_transport_never_follows_redirect(redirect_fixture):
    origin, canary = redirect_fixture
    adapter = LiteLLMChatAdapter()
    with pytest.raises(ProviderError):
        await adapter.complete(_chat_request(origin), None)
    assert canary.hits == []


async def test_streaming_transport_never_follows_redirect(redirect_fixture):
    origin, canary = redirect_fixture
    adapter = LiteLLMChatAdapter()
    with pytest.raises(ProviderError):
        async for _chunk in adapter.stream(_chat_request(origin), None):
            pass
    assert canary.hits == []


async def test_embedding_transport_never_follows_redirect(redirect_fixture):
    origin, canary = redirect_fixture
    adapter = LiteLLMChatAdapter()
    with pytest.raises(ProviderError):
        await adapter.embed(_embedding_request(origin), None)
    assert canary.hits == []


def test_redirect_guard_is_installed_on_litellm_clients():
    import litellm

    handler = getattr(litellm, "module_level_aclient", None)
    assert handler is not None
    assert handler.follow_redirects is False
    assert handler.client.follow_redirects is False
