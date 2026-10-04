"""Development-only, idempotent inference configuration seed.

Seeds the minimum provider/adapter configuration through the service and
repository layer, because the admin API is not implemented yet. It never hard-codes
nomnom specifics: all values come from environment or arguments.

Usage:
    python -m aethergate.devseed \\
        --kind ollama \\
        --upstream-model 'qwen3.8-2b-distill:Q6_K' \\
        --alias gpt-4 \\
        --base-destination http://192.168.22.50:11434

This is development/bootstrap tooling, not a production administration path.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid

from aethergate.config import get_settings
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.enums import Capability
from aethergate.domain.ids import (
    EndpointId,
    ModelAliasId,
    ProviderAccountId,
    ProviderId,
    RouteBindingId,
    SecretRefId,
)
from aethergate.egress import DestinationPolicy
from aethergate.persistence import repository
from aethergate.persistence.db import get_session_factory


def _new_id() -> str:
    return uuid.uuid4().hex


def _env(name: str) -> str | None:
    return os.environ.get(name)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", default=_env("AETHERGATE_SEED_PROVIDER_KIND"))
    parser.add_argument("--upstream-model", default=_env("AETHERGATE_SEED_UPSTREAM_MODEL"))
    parser.add_argument("--alias", default=_env("AETHERGATE_SEED_PUBLIC_ALIAS"))
    parser.add_argument("--base-destination", default=_env("AETHERGATE_SEED_BASE_DESTINATION"))
    parser.add_argument("--secret-ref-name", default=_env("AETHERGATE_SEED_SECRET_REF_NAME"))
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=int(_env("AETHERGATE_SEED_MAX_CONCURRENCY") or "1"),
        help="endpoint physical concurrency limit",
    )
    return parser.parse_args()


async def _seed(args: argparse.Namespace) -> dict[str, str]:
    for required, value in (
        ("--kind", args.kind),
        ("--upstream-model", args.upstream_model),
        ("--alias", args.alias),
        ("--base-destination", args.base_destination),
    ):
        if not value:
            raise SystemExit(f"missing required value: {required}")

    kind: str = args.kind
    base_destination: str = args.base_destination
    settings = get_settings()
    DestinationPolicy(settings.upstream_allowlist_hosts).validate(base_destination)

    async with get_session_factory()() as session:
        async with session.begin():
            provider = await repository.get_provider_by_name(session, kind)
            if provider is None:
                provider = await repository.create_provider(
                    session,
                    domain.Provider(
                        id=ProviderId(_new_id()),
                        kind=kind,
                        name=kind,
                        capabilities=(Capability.TEXT,),
                    ),
                )

            account_name = f"{kind}-account"
            account = await repository.get_provider_account_by_name(session, account_name)
            if account is None:
                secret_ref_id = None
                if args.secret_ref_name:
                    secret_ref = await repository.create_secret_ref(
                        session,
                        domain.SecretRef(
                            id=SecretRefId(_new_id()),
                            name=args.secret_ref_name,
                        ),
                    )
                    secret_ref_id = secret_ref.id
                account = await repository.create_provider_account(
                    session,
                    domain.ProviderAccount(
                        id=ProviderAccountId(_new_id()),
                        provider_id=provider.id,
                        name=account_name,
                        secret_ref_id=secret_ref_id,
                    ),
                )

            endpoint_name = f"{kind}-endpoint"
            endpoint = await repository.get_endpoint_by_name(session, endpoint_name)
            if endpoint is None:
                endpoint = await repository.create_endpoint(
                    session,
                    domain.Endpoint(
                        id=EndpointId(_new_id()),
                        provider_account_id=account.id,
                        name=endpoint_name,
                        base_destination=base_destination,
                        max_concurrency=args.max_concurrency,
                    ),
                )
            else:
                if endpoint.base_destination != base_destination:
                    raise SystemExit(
                        f"endpoint {endpoint_name!r} already exists with a different destination"
                    )
                if endpoint.max_concurrency != args.max_concurrency:
                    endpoint = await repository.update_endpoint_max_concurrency(
                        session, endpoint.id, args.max_concurrency
                    )

            alias = await repository.get_model_alias_by_name(session, args.alias)
            if alias is None:
                alias = await repository.create_model_alias(
                    session,
                    domain.ModelAlias(
                        id=ModelAliasId(_new_id()),
                        name=args.alias,
                        capabilities=(Capability.TEXT,),
                    ),
                )

            existing = await repository.list_route_bindings(session, alias.id)
            bound = any(
                b.endpoint_id == endpoint.id
                and b.provider_account_id == account.id
                and b.upstream_model == args.upstream_model
                for b in existing
            )
            if not bound:
                await repository.create_route_binding(
                    session,
                    domain.RouteBinding(
                        id=RouteBindingId(_new_id()),
                        model_alias_id=alias.id,
                        endpoint_id=endpoint.id,
                        provider_account_id=account.id,
                        upstream_model=args.upstream_model,
                    ),
                )

            await ensure_dev_identity(session)

    return {
        "provider_kind": kind,
        "provider_id": str(provider.id),
        "account_id": str(account.id),
        "endpoint_id": str(endpoint.id),
        "endpoint_max_concurrency": endpoint.max_concurrency,
        "alias": alias.name,
        "alias_id": str(alias.id),
        "upstream_model": args.upstream_model,
        "base_destination": base_destination,
    }


def main() -> None:
    args = _parse_args()
    summary = asyncio.run(_seed(args))
    print("seeded inference configuration:")
    for key, value in summary.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
