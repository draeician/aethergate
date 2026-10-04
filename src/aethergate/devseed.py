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
from decimal import Decimal

from aethergate.config import get_settings
from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.enums import BillingUnit, Capability, QuotaMetric
from aethergate.domain.ids import (
    BudgetPolicyId,
    EndpointId,
    ModelAliasId,
    PricePolicyId,
    ProjectId,
    ProviderAccountId,
    ProviderId,
    QuotaGroupId,
    QuotaLimitId,
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


def _parse_limit(value: str, kind: str) -> tuple[int, int]:
    """Parse ``limit_units/window_seconds`` into ``(limit_units, window_seconds)``."""
    parts = value.split("/")
    if len(parts) != 2:
        raise SystemExit(f"invalid --{kind} {value!r}: expected limit_units/window_seconds")
    try:
        limit_units = int(parts[0])
        window_seconds = int(parts[1])
    except ValueError:
        raise SystemExit(f"invalid --{kind} {value!r}: units/window must be integers") from None
    if limit_units < 1 or window_seconds < 1:
        raise SystemExit(f"invalid --{kind} {value!r}: values must be >= 1")
    return limit_units, window_seconds


def _parse_token_price(value: str) -> tuple[Decimal, Decimal, int]:
    """Parse ``input_price/output_price/unit_scale`` (e.g. ``0.5/1.5/1000000``)."""
    parts = value.split("/")
    if len(parts) != 3:
        raise SystemExit(
            f"invalid --token-price {value!r}: expected input_price/output_price/unit_scale"
        )
    try:
        input_price = Decimal(parts[0])
        output_price = Decimal(parts[1])
        unit_scale = int(parts[2])
    except (ValueError, ArithmeticError):
        raise SystemExit(
            f"invalid --token-price {value!r}: prices/scale must be numeric"
        ) from None
    if input_price < 0 or output_price < 0 or unit_scale < 1:
        raise SystemExit(
            f"invalid --token-price {value!r}: prices must be >= 0 and scale >= 1"
        )
    return input_price, output_price, unit_scale


def _parse_budget(value: str) -> tuple[Decimal, int]:
    """Parse ``limit_amount/window_seconds`` into ``(limit_amount, window_seconds)``."""
    parts = value.split("/")
    if len(parts) != 2:
        raise SystemExit(f"invalid --budget-limit {value!r}: expected limit_amount/window_seconds")
    try:
        limit_amount = Decimal(parts[0])
        window_seconds = int(parts[1])
    except (ValueError, ArithmeticError):
        raise SystemExit(
            f"invalid --budget-limit {value!r}: limit/window must be numeric"
        ) from None
    if limit_amount <= 0 or window_seconds < 1:
        raise SystemExit(
            f"invalid --budget-limit {value!r}: limit must be > 0 and window >= 1"
        )
    return limit_amount, window_seconds


async def _ensure_quota_limits(
    session,
    *,
    group_id: QuotaGroupId,
    request_limits: list[tuple[int, int]],
    token_limits: list[tuple[int, int]],
) -> None:
    """Idempotently ensure the requested quota limits exist on the group."""
    existing = await repository.list_quota_limits_for_group(session, group_id)

    def _present(metric: QuotaMetric, limit_units: int, window_seconds: int) -> bool:
        return any(
            item.metric == metric
            and item.limit_units == limit_units
            and item.window_seconds == window_seconds
            for item in existing
        )

    for metric, limits in (
        (QuotaMetric.REQUESTS, request_limits),
        (QuotaMetric.TOKENS, token_limits),
    ):
        for limit_units, window_seconds in limits:
            if not _present(metric, limit_units, window_seconds):
                await repository.create_quota_limit(
                    session,
                    domain.QuotaLimit(
                        id=QuotaLimitId(_new_id()),
                        quota_group_id=group_id,
                        metric=metric,
                        limit_units=limit_units,
                        window_seconds=window_seconds,
                    ),
                )


async def _seed_price_and_budget(
    session,
    *,
    args: argparse.Namespace,
    route_binding_id: RouteBindingId,
    project_id: ProjectId,
) -> None:
    """Idempotently seed optional route pricing and a project budget policy."""
    currency = args.price_currency.strip().upper()
    if args.request_price is not None and args.token_price is not None:
        raise SystemExit("--request-price and --token-price are mutually exclusive")

    if args.request_price is not None:
        request_price = Decimal(args.request_price)
        if request_price < 0:
            raise SystemExit("--request-price must be >= 0")
        existing = await repository.get_price_policy_for_route_binding(
            session, route_binding_id
        )
        if existing is None:
            await repository.create_price_policy(
                session,
                domain.PricePolicy(
                    id=PricePolicyId(_new_id()),
                    route_binding_id=route_binding_id,
                    billing_unit=BillingUnit.REQUEST,
                    currency=currency,
                    unit_scale=1,
                    request_price=request_price,
                ),
            )
        elif (
            existing.billing_unit != BillingUnit.REQUEST
            or existing.currency != currency
            or existing.request_price != request_price
        ):
            raise SystemExit("existing price policy differs from requested request price")

    if args.token_price is not None:
        input_price, output_price, unit_scale = _parse_token_price(args.token_price)
        existing = await repository.get_price_policy_for_route_binding(
            session, route_binding_id
        )
        if existing is None:
            await repository.create_price_policy(
                session,
                domain.PricePolicy(
                    id=PricePolicyId(_new_id()),
                    route_binding_id=route_binding_id,
                    billing_unit=BillingUnit.TOKEN,
                    currency=currency,
                    unit_scale=unit_scale,
                    input_price=input_price,
                    output_price=output_price,
                ),
            )
        elif (
            existing.billing_unit != BillingUnit.TOKEN
            or existing.currency != currency
            or existing.unit_scale != unit_scale
            or existing.input_price != input_price
            or existing.output_price != output_price
        ):
            raise SystemExit("existing price policy differs from requested token price")

    if args.budget_limit is not None:
        limit_amount, window_seconds = _parse_budget(args.budget_limit)
        name = f"{currency}-budget"
        existing = await repository.get_project_budget_policy_by_name(
            session, project_id, name
        )
        if existing is None:
            await repository.create_project_budget_policy(
                session,
                domain.ProjectBudgetPolicy(
                    id=BudgetPolicyId(_new_id()),
                    project_id=project_id,
                    name=name,
                    currency=currency,
                    limit_amount=limit_amount,
                    window_seconds=window_seconds,
                ),
            )
        elif (
            existing.currency != currency
            or existing.limit_amount != limit_amount
            or existing.window_seconds != window_seconds
        ):
            raise SystemExit("existing budget policy differs from requested budget")


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
    parser.add_argument(
        "--quota-group",
        default=_env("AETHERGATE_SEED_QUOTA_GROUP"),
        help="shared quota group name (creates one if set)",
    )
    parser.add_argument(
        "--request-limit",
        action="append",
        default=[],
        metavar="LIMIT/WINDOW_SECONDS",
        help="request quota limit (repeatable)",
    )
    parser.add_argument(
        "--token-limit",
        action="append",
        default=[],
        metavar="LIMIT/WINDOW_SECONDS",
        help="token quota limit (repeatable)",
    )
    parser.add_argument(
        "--default-output-tokens",
        type=int,
        default=None,
        help="default output-token reservation for the route",
    )
    parser.add_argument(
        "--price-currency",
        default=_env("AETHERGATE_SEED_PRICE_CURRENCY") or "USD",
        help="currency for seeded price/budget (default USD)",
    )
    parser.add_argument(
        "--request-price",
        default=_env("AETHERGATE_SEED_REQUEST_PRICE"),
        metavar="AMOUNT",
        help="seed a request-priced policy with this per-request amount",
    )
    parser.add_argument(
        "--token-price",
        default=_env("AETHERGATE_SEED_TOKEN_PRICE"),
        metavar="INPUT/OUTPUT/UNIT_SCALE",
        help="seed a token-priced policy (input_price/output_price/unit_scale)",
    )
    parser.add_argument(
        "--budget-limit",
        default=_env("AETHERGATE_SEED_BUDGET_LIMIT"),
        metavar="LIMIT/WINDOW_SECONDS",
        help="seed a project budget policy (limit_amount/window_seconds)",
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

    if args.max_concurrency < 1:
        raise SystemExit("--max-concurrency must be >= 1")

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

            quota_group_id: QuotaGroupId | None = None
            if args.quota_group:
                group = await repository.get_quota_group_by_name(session, args.quota_group)
                if group is None:
                    group = await repository.create_quota_group(
                        session,
                        domain.QuotaGroup(
                            id=QuotaGroupId(_new_id()),
                            provider_account_id=account.id,
                            name=args.quota_group,
                        ),
                    )
                elif group.provider_account_id != account.id:
                    raise SystemExit(
                        f"quota group {args.quota_group!r} belongs to a different account"
                    )
                quota_group_id = group.id
                await _ensure_quota_limits(
                    session,
                    group_id=group.id,
                    request_limits=[_parse_limit(v, "request-limit") for v in args.request_limit],
                    token_limits=[_parse_limit(v, "token-limit") for v in args.token_limit],
                )

            if args.default_output_tokens is not None and args.default_output_tokens < 1:
                raise SystemExit("--default-output-tokens must be >= 1")

            existing = await repository.list_route_bindings(session, alias.id)
            binding = next(
                (
                    b
                    for b in existing
                    if b.endpoint_id == endpoint.id
                    and b.provider_account_id == account.id
                    and b.upstream_model == args.upstream_model
                ),
                None,
            )
            if binding is None:
                binding = await repository.create_route_binding(
                    session,
                    domain.RouteBinding(
                        id=RouteBindingId(_new_id()),
                        model_alias_id=alias.id,
                        endpoint_id=endpoint.id,
                        provider_account_id=account.id,
                        upstream_model=args.upstream_model,
                        quota_group_id=quota_group_id,
                        default_output_tokens=args.default_output_tokens,
                    ),
                )

            project_id, _, _ = await ensure_dev_identity(session)
            await _seed_price_and_budget(
                session,
                args=args,
                route_binding_id=binding.id,
                project_id=project_id,
            )

    return {
        "provider_kind": kind,
        "provider_id": str(provider.id),
        "account_id": str(account.id),
        "endpoint_id": str(endpoint.id),
        "endpoint_max_concurrency": endpoint.max_concurrency,
        "alias": alias.name,
        "alias_id": str(alias.id),
        "quota_group": args.quota_group,
        "quota_group_id": str(quota_group_id) if quota_group_id else None,
        "default_output_tokens": args.default_output_tokens,
        "upstream_model": args.upstream_model,
        "base_destination": base_destination,
        "price_currency": args.price_currency.strip().upper(),
        "request_price": args.request_price,
        "token_price": args.token_price,
        "budget_limit": args.budget_limit,
    }


def main() -> None:
    args = _parse_args()
    summary = asyncio.run(_seed(args))
    print("seeded inference configuration:")
    for key, value in summary.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
