"""Development-only credential tooling for identity phase 1.

The admin HTTP API is not implemented yet, so this tool exercises the identity
service directly to create/rotate/revoke/list inference credentials for local
development and nomnom verification. It is NOT a production administration path:
the raw key is printed to stdout exactly once at the create/rotate boundary and
must be treated as a secret.

Usage:
    python -m aethergate.dev_credential create --project dev --name my-key
    python -m aethergate.dev_credential list --project dev
    python -m aethergate.dev_credential revoke --id <credential-id>
    python -m aethergate.dev_credential rotate --id <credential-id>
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from aethergate.dev_identity import ensure_dev_identity
from aethergate.domain import entities as domain
from aethergate.domain.enums import CredentialAudience, CredentialScope, PrincipalKind
from aethergate.domain.ids import ApiCredentialId, PrincipalId
from aethergate.identity import service as identity_service
from aethergate.persistence import repository
from aethergate.persistence.db import get_session_factory


def _new_id() -> str:
    return uuid.uuid4().hex


async def _ensure_service_principal(session, project_id, name: str) -> PrincipalId:
    principal = await repository.get_principal_by_name(session, project_id, name)
    if principal is None:
        principal = await repository.create_principal(
            session,
            domain.Principal(
                id=PrincipalId(_new_id()),
                project_id=project_id,
                kind=PrincipalKind.SERVICE_ACCOUNT,
                name=name,
            ),
        )
    return principal.id


def _print_credential(credential: domain.ApiCredential, raw_key: str | None = None) -> None:
    print(f"  id: {credential.id}")
    print(f"  name: {credential.name}")
    print(f"  project_id: {credential.project_id}")
    print(f"  principal_id: {credential.principal_id}")
    print(f"  prefix: {credential.key_prefix}")
    print(f"  audience: {credential.audience.value}")
    print(f"  scopes: {[s.value for s in credential.scopes]}")
    print(f"  expires_at: {credential.expires_at}")
    print(f"  revoked_at: {credential.revoked_at}")
    print(f"  is_active: {credential.is_active}")
    if raw_key is not None:
        print(f"  raw_key: {raw_key}")


async def _create(args) -> None:
    async with get_session_factory()() as session:
        async with session.begin():
            await ensure_dev_identity(session)
            project = await repository.get_project_by_name(session, args.project)
            if project is None:
                raise SystemExit(f"project {args.project!r} not found")
            principal_id = await _ensure_service_principal(session, project.id, args.principal)
            expires_at = None
            if args.expires_in is not None:
                expires_at = datetime.now(UTC) + timedelta(seconds=args.expires_in)
            scopes = (
                tuple(CredentialScope(s) for s in args.scopes) if args.scopes else None
            )
            credential, raw_key = await identity_service.create_credential(
                session,
                project_id=project.id,
                principal_id=principal_id,
                name=args.name,
                audience=CredentialAudience(args.audience),
                scopes=scopes,
                expires_at=expires_at,
            )
    print("created credential:")
    _print_credential(credential, raw_key=raw_key)


async def _list(args) -> None:
    async with get_session_factory()() as session:
        async with session.begin():
            project = await repository.get_project_by_name(session, args.project)
            if project is None:
                raise SystemExit(f"project {args.project!r} not found")
            credentials = await identity_service.list_credentials(session, project.id)
    for credential in credentials:
        _print_credential(credential)


async def _revoke(args) -> None:
    async with get_session_factory()() as session:
        async with session.begin():
            credential = await identity_service.revoke_credential(
                session, ApiCredentialId(args.id)
            )
    if credential is None:
        raise SystemExit(f"credential {args.id!r} not found")
    print("revoked credential:")
    _print_credential(credential)


async def _rotate(args) -> None:
    async with get_session_factory()() as session:
        async with session.begin():
            credential, raw_key = await identity_service.rotate_credential(
                session, ApiCredentialId(args.id)
            )
    print("rotated credential (new):")
    _print_credential(credential, raw_key=raw_key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="create an inference credential")
    create.add_argument("--project", default="dev")
    create.add_argument("--principal", default="svc-test")
    create.add_argument("--name", required=True)
    create.add_argument("--audience", default="inference", choices=["inference", "admin"])
    create.add_argument("--scopes", nargs="+", default=None)
    create.add_argument("--expires-in", type=int, default=None, help="seconds until expiry")
    create.set_defaults(func=_create)

    lst = sub.add_parser("list", help="list a project's credentials (metadata only)")
    lst.add_argument("--project", default="dev")
    lst.set_defaults(func=_list)

    revoke = sub.add_parser("revoke", help="revoke a credential")
    revoke.add_argument("--id", required=True)
    revoke.set_defaults(func=_revoke)

    rotate = sub.add_parser("rotate", help="rotate a credential (revoke old, create new)")
    rotate.add_argument("--id", required=True)
    rotate.set_defaults(func=_rotate)

    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
