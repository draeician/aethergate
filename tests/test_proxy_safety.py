"""Real-transport env-proxy regression for the pinned LiteLLM adapter.

The production literal-IP policy asserts "validated IP == connected IP", so the
transport must connect **directly** to the allowlisted address. An implicit
``HTTP_PROXY``/``HTTPS_PROXY``/``ALL_PROXY`` would route the connection through a
proxy and defeat that guarantee. These tests point the env proxy vars at a local
canary proxy and prove the adapter's completion transport connects directly to
the origin — for both the default aiohttp transport and the httpx fallback
transport — so the canary is never contacted.

No network access beyond loopback is used, and no provider credentials are
required.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aethergate.adapters.base import ChatRequest, GenerationParams, Message
from aethergate.adapters.litellm import LiteLLMChatAdapter


class _Hits:
    def __init__(self) -> None:
        self.requests: list[str] = []


def _start_proxy_fixture() -> tuple[str, str, _Hits, _Hits, list[ThreadingHTTPServer]]:
    origin = _Hits()
    canary = _Hits()

    class OriginHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            origin.requests.append(self.path)
            if self.path.endswith("/api/generate"):
                body = b'{"response": "ok", "done": true}'
            else:
                body = b'{"message": {"role": "assistant", "content": "ok"}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # noqa: D102
            pass

    class CanaryHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            canary.requests.append(self.path)
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            canary.requests.append(self.path)
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:  # noqa: D102
            pass

    origin_server = ThreadingHTTPServer(("127.0.0.1", 0), OriginHandler)
    canary_server = ThreadingHTTPServer(("127.0.0.1", 0), CanaryHandler)
    servers = [origin_server, canary_server]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    origin_url = f"http://127.0.0.1:{origin_server.server_address[1]}"
    canary_url = f"http://127.0.0.1:{canary_server.server_address[1]}"
    return origin_url, canary_url, origin, canary, servers


@pytest.fixture
def proxy_fixture():
    origin_url, canary_url, origin, canary, servers = _start_proxy_fixture()
    try:
        yield origin_url, canary_url, origin, canary
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def _chat_request(base_destination: str) -> ChatRequest:
    return ChatRequest(
        provider_kind="ollama",
        base_destination=base_destination,
        upstream_model="proxy-test-model",
        messages=(Message(role="user", content="hi"),),
        params=GenerationParams(),
        timeout_seconds=10.0,
    )


def _set_env_proxy(monkeypatch: pytest.MonkeyPatch, canary_url: str) -> None:
    monkeypatch.setenv("HTTP_PROXY", canary_url)
    monkeypatch.setenv("HTTPS_PROXY", canary_url)
    monkeypatch.setenv("ALL_PROXY", canary_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)


async def test_default_transport_ignores_env_proxy(proxy_fixture, monkeypatch):
    origin_url, canary_url, origin, canary = proxy_fixture
    _set_env_proxy(monkeypatch, canary_url)
    adapter = LiteLLMChatAdapter()
    result = await adapter.complete(_chat_request(origin_url), None)
    assert result.content == "ok"
    assert origin.requests, "origin was never contacted (request did not go direct)"
    assert canary.requests == [], "implicit env proxy was contacted"


async def test_httpx_fallback_transport_ignores_env_proxy(proxy_fixture, monkeypatch):
    import litellm

    origin_url, canary_url, origin, canary = proxy_fixture
    _set_env_proxy(monkeypatch, canary_url)
    original = litellm.disable_aiohttp_transport
    litellm.disable_aiohttp_transport = True
    try:
        adapter = LiteLLMChatAdapter()
        result = await adapter.complete(_chat_request(origin_url), None)
    finally:
        litellm.disable_aiohttp_transport = original
    assert result.content == "ok"
    assert origin.requests, "origin was never contacted (request did not go direct)"
    assert canary.requests == [], "implicit env proxy was contacted"


def test_proxy_guard_installed_on_litellm():
    import litellm

    assert litellm.aiohttp_trust_env is False
    assert litellm.disable_aiohttp_trust_env is True
    handler = getattr(litellm, "module_level_aclient", None)
    assert handler is not None
    assert handler.client._trust_env is False  # type: ignore[attr-defined]
    assert handler.client._mounts == {}  # type: ignore[attr-defined]
