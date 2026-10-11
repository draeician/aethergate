"""Official OpenAI Python SDK driver for the web-console live E2E.

Invoked by the Playwright specs (``fixtures.runOfficialSdk``). It reads the
gateway base URL, the inference API key, the model alias, and the stream flag
from the environment (never argv), so the raw key is never exposed in a process
listing. It prints a single redacted status line and exits non-zero on failure.

Additional modes (environment, never argv):
  ``AETHERGATE_VISION=1``    send an inline synthetic image with the prompt
  ``AETHERGATE_DETAIL=<v>``  set the Chat image detail selector (e.g. auto/low)
  ``AETHERGATE_EXPECT_400=1`` assert the request fails with an SDK-visible 400

Output contract (one line on stdout):
  ``OK``            non-stream chat completion returned non-empty content
  ``OK_STREAM``     streaming chat completion produced at least one content delta
  ``OK_400``        the request was rejected with an SDK-visible 400 (expected)
  ``FAIL <Class> status=<int|none> code=<code|none>``   request failed
  ``MISSING_ENV``   a required environment variable was not provided
"""

from __future__ import annotations

import base64
import os
import sys

from openai import OpenAI

# Tiny deterministic 16x16 solid-red PNG (matches the golden-path fixture). A
# real decodable image so it works against a live vision model too; never a real
# photo. No image bytes are printed.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000001000000010080200000090916836"
    "000000164944415478da63f8a0a040126218d530aa61f86a00006e5e30100ce312"
    "630000000049454e44ae426082"
)
_PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(_TINY_PNG).decode("ascii")
_REMOTE_IMAGE_URL = "https://example.invalid/x.png"


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
    vision = os.environ.get("AETHERGATE_VISION") == "1"
    expect_400 = os.environ.get("AETHERGATE_EXPECT_400") == "1"
    detail = os.environ.get("AETHERGATE_DETAIL")
    if not base_url or not api_key or not model:
        print("MISSING_ENV")
        return 2

    # Default 60s is fine for an immediately-dispatching request; a request that
    # stays queued while the caller drives browser actions (budget-unblock proof)
    # needs a larger bound, injected via env (never argv).
    timeout = float(os.environ.get("AETHERGATE_TIMEOUT", "60"))
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
    if vision:
        image_url = _REMOTE_IMAGE_URL if expect_400 else _PNG_DATA_URL
        image_part: dict = {"url": image_url}
        if detail is not None:
            image_part["detail"] = detail
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "sdk vision proof"},
                    {"type": "image_url", "image_url": image_part},
                ],
            }
        ]
    else:
        messages = [{"role": "user", "content": "sdk inference proof"}]

    try:
        if expect_400:
            client.chat.completions.create(model=model, messages=messages)
            print("FAIL expected 400 but request succeeded")
            return 1
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
        if expect_400 and status == 400:
            print("OK_400")
            return 0
        print(f"FAIL {exc.__class__.__name__} status={status} code={_error_code(exc)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
