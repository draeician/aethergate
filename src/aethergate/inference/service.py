"""Direct-dispatch inference service.

Resolves a public alias to a fully authorized upstream route, enforces the
egress destination policy, resolves provider credential material through the
trusted ``SecretResolver``, and dispatches through the provider adapter.

This milestone intentionally does not queue, reserve quota, retry, or fallback:
one admitted execution maps to one upstream attempt.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from aethergate.adapters.base import (
    ChatRequest,
    CompletionResult,
    GenerationParams,
    Message,
    StreamChunk,
)
from aethergate.adapters.registry import adapter_for
from aethergate.catalog.service import ResolvedRoute, resolve_model_alias
from aethergate.egress import DestinationPolicy
from aethergate.errors import SecretResolutionError
from aethergate.persistence import repository
from aethergate.secrets import SecretResolver

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aethergate.adapters.base import ChatAdapter

AdapterFactory = Callable[[str], "ChatAdapter"]


@dataclass(frozen=True)
class PreparedDispatch:
    """A fully resolved, guarded dispatch ready for a single upstream attempt."""

    adapter: ChatAdapter
    request: ChatRequest
    secret: str | None
    public_alias: str


class InferenceService:
    """Resolve, guard, and dispatch a chat completion."""

    def __init__(
        self,
        secret_resolver: SecretResolver,
        destination_policy: DestinationPolicy,
        *,
        adapter_factory: AdapterFactory = adapter_for,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._resolver = secret_resolver
        self._policy = destination_policy
        self._adapter_factory = adapter_factory
        self._timeout = timeout_seconds

    async def resolve(self, session: AsyncSession, alias: str) -> ResolvedRoute:
        """Resolve ``alias`` and enforce the destination policy."""
        resolved = await resolve_model_alias(session, alias)
        self._policy.validate(resolved.endpoint.base_destination)
        return resolved

    async def _secret(self, session: AsyncSession, resolved: ResolvedRoute) -> str | None:
        ref_id = resolved.provider_account.secret_ref_id
        if ref_id is None:
            return None
        ref = await repository.get_secret_ref(session, ref_id)
        if ref is None:
            raise SecretResolutionError(f"secret reference {ref_id!s} not found")
        return self._resolver.resolve(ref)

    async def prepare(
        self,
        session: AsyncSession,
        alias: str,
        messages: list[Message],
        params: GenerationParams | None = None,
    ) -> PreparedDispatch:
        """Resolve, guard, and prepare a dispatch without contacting upstream."""
        resolved = await self.resolve(session, alias)
        secret = await self._secret(session, resolved)
        adapter = self._adapter_factory(resolved.provider.kind)
        request = ChatRequest(
            provider_kind=resolved.provider.kind,
            base_destination=resolved.endpoint.base_destination,
            upstream_model=resolved.upstream_model,
            messages=tuple(messages),
            params=params or GenerationParams(),
            timeout_seconds=self._timeout,
        )
        return PreparedDispatch(
            adapter=adapter,
            request=request,
            secret=secret,
            public_alias=resolved.alias.name,
        )

    async def complete(
        self,
        session: AsyncSession,
        alias: str,
        messages: list[Message],
        params: GenerationParams | None = None,
    ) -> CompletionResult:
        prepared = await self.prepare(session, alias, messages, params)
        return await prepared.adapter.complete(prepared.request, prepared.secret)

    async def stream(
        self,
        session: AsyncSession,
        alias: str,
        messages: list[Message],
        params: GenerationParams | None = None,
    ) -> AsyncIterator[StreamChunk]:
        prepared = await self.prepare(session, alias, messages, params)
        async for chunk in prepared.adapter.stream(prepared.request, prepared.secret):
            yield chunk
