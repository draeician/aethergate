"""Standalone deterministic fake Ollama upstream entrypoint (test/CI only).

Runs the in-repo :class:`~aethergate.testing.fake_ollama.FakeOllamaServer` as a
long-lived HTTP server so offline CI can serve a real LiteLLM ollama transport
path without Internet, model downloads, or provider credentials.

Configuration (environment):
- ``FAKE_OLLAMA_PORT`` — listen port (default ``11434``).
- ``FAKE_OLLAMA_DELAY`` — per-request delay seconds (default ``0``).
- ``FAKE_OLLAMA_FAIL_STATUS`` — when set, every request returns this status.

It is never started by production compose; only ``deploy/v2/compose.ci.yaml``
(this repo's CI-only override) references it.
"""

from __future__ import annotations

import os
import sys

from aethergate.testing.fake_ollama import (
    FakeOllamaConfig,
    FakeOllamaServer,
    _Handler,
)


class _FixedPortServer(FakeOllamaServer):
    def __init__(self, config: FakeOllamaConfig, port: int) -> None:
        import threading
        from http.server import ThreadingHTTPServer

        self.config = config
        handler = type("_BoundHandler", (_Handler,), {"config": config})
        self._server = ThreadingHTTPServer(("0.0.0.0", port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)


def main() -> int:
    port = int(os.environ.get("FAKE_OLLAMA_PORT", "11434"))
    delay = float(os.environ.get("FAKE_OLLAMA_DELAY", "0") or "0")
    fail_status = os.environ.get("FAKE_OLLAMA_FAIL_STATUS")
    config = FakeOllamaConfig(
        delay_seconds=delay,
        fail_status=int(fail_status) if fail_status else None,
    )
    server = _FixedPortServer(config, port).start()
    print(f"fake-ollama listening on 0.0.0.0:{port}", file=sys.stderr, flush=True)
    try:
        server._thread.join()  # noqa: SLF001 - run until terminated
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
