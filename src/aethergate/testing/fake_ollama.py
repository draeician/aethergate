"""Deterministic, test-only Ollama-compatible upstream client.

This helper starts a real local HTTP server that speaks the minimum Ollama
``/api/chat`` surface consumed by the pinned LiteLLM ollama adapter, so the
**real** AetherGate scheduler + adapter transport can be exercised fully offline
(no Internet, no model download, no provider credentials).

It is strictly test/CI tooling:

- never referenced by production compose (the image only imports it when a test
  or CI job explicitly starts it);
- never persists or logs request prompt/content beyond what is needed to produce
  a deterministic canned response;
- introduces no new production provider kind (it emulates the existing ``ollama``
  HTTP surface, so LiteLLM still uses provider kind ``ollama``).

The fixture supports:
- deterministic non-stream completion;
- deterministic streaming with several chunks;
- deterministic upstream request ID;
- usage metadata (``prompt_eval_count``/``eval_count``);
- configurable delay (for queue/concurrency tests);
- configurable status failure (e.g. 404, 429, and other provider errors).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_CONTENT = "aethergate-fixture-response"
DEFAULT_STREAM_CHUNKS = ("aether", "gate-", "fixture-", "response")
DEFAULT_UPSTREAM_ID = "fixture-upstream-1"


@dataclass
class FakeOllamaConfig:
    """Mutable, thread-safe-enough configuration for the fixture server."""

    content: str = DEFAULT_CONTENT
    stream_chunks: tuple[str, ...] = DEFAULT_STREAM_CHUNKS
    upstream_id: str = DEFAULT_UPSTREAM_ID
    prompt_tokens: int = 5
    completion_tokens: int = 4
    delay_seconds: float = 0.0
    # When set, every request returns this status with a safe error body.
    fail_status: int | None = None
    fail_message: str = "fixture upstream failure"
    # Model that returns 404 when the client requests it (model-not-found path).
    missing_models: tuple[str, ...] = field(default_factory=tuple)

    # Introspection (safe: counts only, no prompt/content).
    request_count: int = 0
    last_model: str | None = None
    last_path: str | None = None
    last_stream: bool | None = None
    last_mode: str | None = None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    config: FakeOllamaConfig  # set by the server

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Silence the default stderr access log; content/requests are not logged.
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _maybe_delay(self) -> None:
        if self.config.delay_seconds > 0:
            time.sleep(self.config.delay_seconds)

    def _fail(self) -> bool:
        config = self.config
        if config.fail_status is not None:
            self._send_json(
                config.fail_status,
                {"error": config.fail_message},
            )
            return True
        return False

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        config = self.config
        config.request_count += 1
        config.last_path = self.path
        body = self._read_json()
        model = body.get("model")
        config.last_model = model if isinstance(model, str) else None
        stream = bool(body.get("stream", False))
        config.last_stream = stream

        path = self.path.rstrip("/")
        if path == "/api/chat":
            mode = "chat"
        elif path == "/api/generate":
            mode = "generate"
        else:
            self._send_json(404, {"error": "not found"})
            return
        config.last_mode = mode
        if isinstance(model, str) and model in config.missing_models:
            self._send_json(
                404, {"error": f"model {model!r} not found"}
            )
            return
        if self._fail():
            return

        self._maybe_delay()
        if stream:
            self._stream_response(mode)
        else:
            self._non_stream_response(mode)

    def _base_fields(self) -> dict[str, Any]:
        return {
            "model": self.config.last_model or "fixture",
            "created_at": "2026-01-01T00:00:00Z",
            "done": False,
        }

    def _text_field(self, mode: str, text: str) -> dict[str, Any]:
        if mode == "chat":
            return {"message": {"role": "assistant", "content": text}}
        return {"response": text}

    def _non_stream_response(self, mode: str) -> None:
        config = self.config
        payload = {
            **self._base_fields(),
            **self._text_field(mode, config.content),
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": config.prompt_tokens,
            "eval_count": config.completion_tokens,
        }
        self._send_json(200, payload)

    def _stream_response(self, mode: str) -> None:
        config = self.config
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def write_line(obj: dict[str, Any]) -> None:
            data = (json.dumps(obj) + "\n").encode("utf-8")
            chunk = f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n"
            self.wfile.write(chunk)
            self.wfile.flush()

        for text in config.stream_chunks:
            write_line({**self._base_fields(), **self._text_field(mode, text)})
        write_line(
            {
                **self._base_fields(),
                **self._text_field(mode, ""),
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": config.prompt_tokens,
                "eval_count": config.completion_tokens,
            }
        )
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    # Ollama clients sometimes probe GET /api/tags; answer deterministically.
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.rstrip("/") == "/api/tags":
            self._send_json(200, {"models": [{"name": "fixture"}]})
            return
        self._send_json(404, {"error": "not found"})


class FakeOllamaServer:
    """A threaded, deterministic fake Ollama upstream bound to loopback."""

    def __init__(self, config: FakeOllamaConfig | None = None) -> None:
        self.config = config or FakeOllamaConfig()
        handler = type("_BoundHandler", (_Handler,), {"config": self.config})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeOllamaServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakeOllamaServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


__all__ = [
    "FakeOllamaConfig",
    "FakeOllamaServer",
    "DEFAULT_CONTENT",
    "DEFAULT_STREAM_CHUNKS",
    "DEFAULT_UPSTREAM_ID",
]
