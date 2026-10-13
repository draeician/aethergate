"""Secret/egress ordering and dispatch-time boundary proofs."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from aethergate.adapters.base import Message
from aethergate.domain.entities import SecretRef
from aethergate.domain.ids import SecretRefId
from aethergate.egress import DestinationPolicy
from aethergate.errors import DestinationDenied
from aethergate.inference.service import InferenceService


class _RecordingResolver:
    def __init__(self) -> None:
        self.calls: list[SecretRef] = []

    def resolve(self, ref: SecretRef) -> str:
        self.calls.append(ref)
        return "material"


def _ref() -> SecretRef:
    return SecretRef(
        id=SecretRefId("sr-1"), name="provider-key", created_at=datetime(2026, 10, 3, tzinfo=UTC)
    )


def _resolved(base_destination: str) -> SimpleNamespace:
    return SimpleNamespace(
        alias=SimpleNamespace(name="golden-model"),
        endpoint=SimpleNamespace(base_destination=base_destination),
        provider_account=SimpleNamespace(secret_ref_id=SecretRefId("sr-1")),
        provider=SimpleNamespace(kind="ollama"),
        upstream_model="test-model",
    )


async def test_denied_destination_never_resolves_secret():
    resolver = _RecordingResolver()
    service = InferenceService(resolver, DestinationPolicy({"allowed.example.com"}))
    resolved = _resolved("http://evil.example.com")
    with pytest.raises(DestinationDenied):
        await service.prepare_from_resolved(
            None, resolved, [Message(role="user", content="hi")]
        )
    assert resolver.calls == []


async def test_denied_destination_never_resolves_secret_for_embedding():
    resolver = _RecordingResolver()
    service = InferenceService(resolver, DestinationPolicy({"allowed.example.com"}))
    resolved = _resolved("http://evil.example.com")
    with pytest.raises(DestinationDenied):
        await service.prepare_embedding_from_resolved(None, resolved, ("hi",))
    assert resolver.calls == []


async def test_allowed_destination_resolves_secret_after_validation(monkeypatch):
    resolver = _RecordingResolver()
    service = InferenceService(resolver, DestinationPolicy({"allowed.example.com"}))

    async def _get_secret_ref(session, ref_id):
        return _ref()

    monkeypatch.setattr(
        "aethergate.persistence.repository.get_secret_ref", _get_secret_ref
    )
    resolved = _resolved("http://allowed.example.com")
    prepared = await service.prepare_from_resolved(
        None, resolved, [Message(role="user", content="hi")]
    )
    assert prepared.secret == "material"
    assert [str(r.id) for r in resolver.calls] == ["sr-1"]


def test_prod_literal_policy_denies_hostname_route_at_dispatch_time():
    # A route persisted under dev/test rules (hostname destination) is still
    # denied when the dispatch-time policy requires a literal IP.
    service = InferenceService(
        _RecordingResolver(),
        DestinationPolicy({"ollama.internal"}, require_literal_ip=True),
    )
    with pytest.raises(DestinationDenied):
        service.validate_destination(_resolved("http://ollama.internal:11434"))
