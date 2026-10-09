"""Official OpenAI Python SDK driver for the web-console live E2E.

Invoked by the Playwright specs (``fixtures.runOfficialSdk``). It reads the
gateway base URL, the inference API key, the model alias, and the stream flag
from the environment (never argv), so the raw key is never exposed in a process
listing. It prints a single redacted status line and exits non-zero on failure.

Output contract (one line on stdout):
  ``OK``            non-stream chat completion returned non-empty content
  ``OK_STREAM``     streaming chat completion produced at least one content delta
  ``FAIL <Class> status=<int|none> code=<code|none>``   request failed
  ``MISSING_ENV``   a required environment variable was not provided
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI


def _error_code(exc: BaseException) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        # The OpenAI SDK unwraps ``{"error": {...}}``, so ``code`` sits at the
        # top level of ``body``. Fall back to the wrapped shape defensively.
        code = body.get("code")
        if isinstance(code, str):
            return code
        err = body.get("error")
        if isinstance(err, dict) and isinstance(err.get("code"), str):
            return err["code"]
    return None


def main() -> int:
    base_url = os.environ.get("AETHERGATE_BASE_URL")
    api_key = os.environ.get("AETHERGATE_API_KEY")
    model = os.environ.get("AETHERGATE_MODEL")
    stream = os.environ.get("AETHERGATE_STREAM") == "1"
    if not base_url or not api_key or not model:
        print("MISSING_ENV")
        return 2

    # Default 60s is fine for an immediately-dispatching request; a request that
    # stays queued while the caller drives browser actions (budget-unblock proof)
    # needs a larger bound, injected via env (never argv).
    timeout = float(os.environ.get("AETHERGATE_TIMEOUT", "60"))
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
    messages = [{"role": "user", "content": "sdk inference proof"}]

    try:
        if stream:
            chunks = client.chat.completions.create(
                model=model, messages=messages, stream=True
            )
            collected = [
                chunk.choices[0].delta.content
                for chunk in chunks
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content
            ]
            if not collected:
                print("FAIL empty stream")
                return 1
            print("OK_STREAM")
            return 0

        completion = client.chat.completions.create(model=model, messages=messages)
        content = completion.choices[0].message.content
        if not content:
            print("FAIL empty content")
            return 1
        print("OK")
        return 0
    except Exception as exc:  # noqa: BLE001 - only the class/status/code are reported
        status = getattr(exc, "status_code", None)
        print(f"FAIL {exc.__class__.__name__} status={status} code={_error_code(exc)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
