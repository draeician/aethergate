"""Per-route thinking (reasoning) policy resolution.

AetherGate exposes an administrative, per-route policy controlling whether and
how an upstream model is asked to produce reasoning content. The policy is route
configuration, never something an inference client can set or override.

The pinned LiteLLM Ollama transport maps the OpenAI ``reasoning_effort``
parameter onto Ollama's ``think`` field:

- for ``gpt-oss`` models, ``reasoning_effort`` is forwarded verbatim, so distinct
  ``low``/``medium``/``high`` levels are genuinely representable;
- for every other Ollama model, Ollama's ``think`` is a boolean, so any of
  ``low``/``medium``/``high`` collapses to ``think: true``. A distinct level is
  therefore *not* representable and is rejected at configuration time.

Reasoning is enabled by sending one of ``low``/``medium``/``high`` and disabled
by sending any other value (for example ``none``). This module resolves the
administrative policy plus an optional client request into the single
``reasoning_effort`` value that is actually sent upstream.
"""

from __future__ import annotations

from aethergate.domain.enums import ThinkingPolicy
from aethergate.errors import AdminValidationError

# Policies that require an upstream able to represent a distinct reasoning level.
_LEVEL_POLICIES = frozenset(
    {ThinkingPolicy.LOW, ThinkingPolicy.MEDIUM, ThinkingPolicy.HIGH}
)

# ``reasoning_effort`` value that disables upstream reasoning.
_REASONING_OFF = "none"
# ``reasoning_effort`` value used to enable upstream reasoning at a default level.
# It maps to ``think: true`` for boolean-only models and to the ``medium`` level
# for level-capable models.
_REASONING_ON = "medium"

# Client-supplied ``reasoning_effort`` values accepted on Chat Completions.
CLIENT_REASONING_EFFORT_VALUES = frozenset(
    {"none", "minimal", "low", "medium", "high"}
)


def thinking_levels_supported(provider_kind: str, upstream_model: str) -> bool:
    """Whether the upstream can represent a distinct reasoning level.

    Mirrors the pinned transport's own parameter mapping, so a level is only
    permitted when it would actually reach the provider as a level rather than
    collapsing to a boolean.
    """
    if provider_kind == "ollama":
        return upstream_model.startswith("gpt-oss")
    return False


def validate_thinking_policy(
    policy: ThinkingPolicy, provider_kind: str, upstream_model: str
) -> None:
    """Reject a level policy the route's upstream cannot genuinely represent.

    Raises:
        AdminValidationError: the policy is a level and the upstream is
            boolean-only. The message names only the supported policies.
    """
    if policy in _LEVEL_POLICIES and not thinking_levels_supported(
        provider_kind, upstream_model
    ):
        raise AdminValidationError(
            "thinking_policy level is not supported by this route's upstream; "
            "use 'inherit', 'off', or 'on'"
        )


def effective_reasoning_effort(
    policy: ThinkingPolicy, client_value: str | None
) -> str | None:
    """Resolve the ``reasoning_effort`` to send upstream.

    An explicit administrative policy always wins: a client-supplied
    ``reasoning_effort`` is honored only under ``inherit``. ``None`` means no
    explicit directive is sent (the upstream default).
    """
    if policy is ThinkingPolicy.INHERIT:
        return client_value
    if policy is ThinkingPolicy.OFF:
        return _REASONING_OFF
    if policy is ThinkingPolicy.ON:
        return _REASONING_ON
    return policy.value
