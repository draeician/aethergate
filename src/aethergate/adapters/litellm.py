"""LiteLLM-backed chat adapter.

LiteLLM provides the SDK-level translation to concrete providers. AetherGate
uses it only for transport, with retries and hidden SDK fallback disabled so
admission controls are never bypassed for throughput.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import litellm
from litellm.utils import huggingface_tokenizer_kind

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    StreamChunk,
    Usage,
)
from aethergate.errors import ProviderError, UnsupportedProvider

_PROVIDER_PREFIX = {
    "ollama": "ollama",
}


def _safe_error_message(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status:
        return f"upstream provider returned status {status}"
    return "upstream provider request failed"


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Extract safe Retry-After feedback from a provider exception, if present.

    Never exposes raw headers or URLs; returns seconds only.
    """
    raw = getattr(exc, "retry_after", None)
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return None


class LiteLLMChatAdapter:
    """Dispatch chat completions through LiteLLM."""

    def _litellm_model(self, request: ChatRequest) -> str:
        prefix = _PROVIDER_PREFIX.get(request.provider_kind)
        if prefix is None:
            raise UnsupportedProvider(request.provider_kind)
        return f"{prefix}/{request.upstream_model}"

    def _messages(self, request: ChatRequest) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in request.messages]

    def _messages_text(self, request: ChatRequest) -> str:
        return "\n".join(m.content for m in request.messages)

    def estimate_input_tokens(self, request: ChatRequest) -> int | None:
        """Return a conservative input-token estimate, or ``None``.

        Only produces an estimate when LiteLLM registers a provider/model-specific
        tokenizer for the resolved model. For models without one (for example the
        nomnom Ollama ``qwen3.8-2b-distill``), LiteLLM would otherwise silently
        fall back to a generic tiktoken encoding, which is not a defensible
        conservative bound for arbitrary tokenizers. In that case this method
        returns ``None`` so the scheduler can fail a token-quota request closed
        instead of masquerading a generic heuristic as TPM protection.
        """
        model = self._litellm_model(request)
        if not self._has_model_specific_tokenizer(model):
            return None
        text = self._messages_text(request)
        try:
            count = litellm.token_counter(model=model, text=text)
        except Exception:  # noqa: BLE001 - estimation is best-effort
            return None
        if isinstance(count, int) and count > 0:
            return count + max(1, count // 4)  # +25% conservative margin
        return None

    def _has_model_specific_tokenizer(self, model: str) -> bool:
        """Whether LiteLLM has a provider/model-specific tokenizer for ``model``."""
        try:
            return huggingface_tokenizer_kind(model) is not None
        except Exception:  # noqa: BLE001 - any failure means "unavailable"
            return False

    def _kwargs(
        self, request: ChatRequest, secret: str | None
    ) -> dict[str, object]:
        params = request.params
        kwargs: dict[str, object] = {
            "api_base": request.base_destination,
            "num_retries": 0,
            "max_retries": 0,
            "drop_params": False,
            "timeout": request.timeout_seconds,
        }
        if secret is not None:
            kwargs["api_key"] = secret
        if params.temperature is not None:
            kwargs["temperature"] = params.temperature
        if params.top_p is not None:
            kwargs["top_p"] = params.top_p
        if params.max_tokens is not None:
            kwargs["max_tokens"] = params.max_tokens
        if params.stop is not None:
            kwargs["stop"] = params.stop
        if params.frequency_penalty is not None:
            kwargs["frequency_penalty"] = params.frequency_penalty
        if params.presence_penalty is not None:
            kwargs["presence_penalty"] = params.presence_penalty
        if params.seed is not None:
            kwargs["seed"] = params.seed
        return kwargs

    def _usage(self, usage: object) -> Usage:
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        completion = getattr(usage, "completion_tokens", 0) or 0
        total = getattr(usage, "total_tokens", 0) or 0
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        )

    async def complete(
        self, request: ChatRequest, secret: str | None
    ) -> CompletionResult:
        model = self._litellm_model(request)
        kwargs = self._kwargs(request, secret)
        try:
            response = await litellm.acompletion(
                model=model,
                messages=self._messages(request),
                stream=False,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - boundary sanitizes all upstream failures
            raise ProviderError(
                _safe_error_message(exc),
                status_code=getattr(exc, "status_code", None),
                retry_after_seconds=_retry_after_seconds(exc),
            ) from exc

        choice = response.choices[0]
        content = getattr(choice.message, "content", None) or ""
        return CompletionResult(
            content=content,
            finish_reason=getattr(choice, "finish_reason", None),
            usage=self._usage(getattr(response, "usage", None)),
            upstream_request_id=getattr(response, "id", None) or None,
        )

    async def stream(
        self, request: ChatRequest, secret: str | None
    ) -> AsyncIterator[StreamChunk]:
        model = self._litellm_model(request)
        kwargs = self._kwargs(request, secret)
        try:
            response = await litellm.acompletion(
                model=model,
                messages=self._messages(request),
                stream=True,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                _safe_error_message(exc),
                status_code=getattr(exc, "status_code", None),
                retry_after_seconds=_retry_after_seconds(exc),
            ) from exc

        try:
            async for chunk in response:
                choices = chunk.choices or []
                delta = choices[0].delta if choices else None
                content = getattr(delta, "content", None) if delta else None
                finish_reason = choices[0].finish_reason if choices else None
                usage = None
                if getattr(chunk, "usage", None) is not None:
                    usage = self._usage(chunk.usage)
                yield StreamChunk(
                    content=content,
                    finish_reason=finish_reason,
                    usage=usage,
                )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                _safe_error_message(exc),
                status_code=getattr(exc, "status_code", None),
                retry_after_seconds=_retry_after_seconds(exc),
            ) from exc
